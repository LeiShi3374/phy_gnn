import abc
import argparse
import os
import time
from typing import Dict, List, Optional, Union

import torch
import torchmetrics
from torch import Tensor, nn
from torch.utils.data import DataLoader

from common.constant import MODEL_TRAIN, TRAIN_NAME, VALIDATION_NAME
from pkg.train.callbacks.base_callback import CallbackList
from pkg.train.callbacks.log_callback import LogCallback
from pkg.train.callbacks.model_checkpoint_callback import ModelCheckpointCallback
from pkg.train.callbacks.scheduling_callback import SchedulingCallback
from pkg.train.callbacks.tensorboard_callback import TensorBoardCallback
from pkg.train.config.base_config import BaseConfig
from pkg.train.datasets.base_datasets_train import AbstractTrainDataset, BaseDataset
from pkg.train.module.learning_rate_scheduler import DefaultLRScheduler
from pkg.train.module.loss import EuclideanDistanceMSE
from pkg.utils.io import load_yaml
from pkg.utils.logs import init_logger
# from pkg.utils.model_debug import debug_model
from pkg.utils.model_summary import summary_model

logger = init_logger("BASE_TRAINER")


class TrainerConfig(BaseConfig):
    """TrainerConfig class is inherent from BaseConfig class defining the structure for Trainer configuration."""

    def __init__(self) -> None:
        """Constructor to initialize a TrainerConfig object."""
        # parse args
        args = self.parse_args()
        repo_path: str = args.repo_path
        task_name: str = args.task_name
        task_type: str = args.task_type
        model_name: str = args.model_name
        config_name: str = args.config_name

        # task base info
        # === task path setup
        task_path = f"{repo_path}/task/{task_name}"
        task_path = task_path if model_name is None else f"{task_path}/{model_name}"

        # === load config
        config_path = f"{task_path}/config/{config_name}.yaml"
        config: Dict = load_yaml(config_path)

        if task_name != config["task_base"]["task_name"]:
            raise ValueError("task_name in task_base should match with the input model_name")

        if model_name != config["task_base"]["model_name"]:
            raise ValueError("model_name in task_base should match with the input model_name")

        # === fill in task base info
        self.task_base = config["task_base"]
        self.task_name = self.task_base["task_name"]
        self.model_name = self.task_base["exp_name"]
        self.exp_name = self.task_base["exp_name"]

        self.task_base["repo_path"] = repo_path
        self.task_base["task_path"] = task_path
        self.task_base["config_path"] = config_path

        self._create_logs_path(task_type)

        # === setup gpu
        self.task_base["gpu"] = self.task_base["gpu"] and torch.cuda.is_available()
        self.task_base["gpu_num"] = self.task_base.get("gpu_num", 1)
        self.task_base["cuda_core"] = self.task_base.get("cuda_core", 0)

        if self.task_base["gpu"]:
            torch.cuda.set_device(self.task_base["cuda_core"])

        # task dataset info
        self.task_data = config.get("task_data", {})
        self.task_data["repo_path"] = repo_path
        self.task_data["task_path"] = task_path
        self.task_data["task_data_path"] = self.task_data.get("task_data_path", f"{repo_path}/pkg/data/{task_name}")

        self.task_data["task_name"] = task_name
        self.task_data["model_name"] = self.task_base["model_name"]
        self.task_data["exp_name"] = self.task_base["exp_name"]

        self.task_data["gpu"] = self.task_base["gpu"]

        # task trainer
        self.task_trainer = config["task_trainer"]
        self.task_trainer["gpu"] = self.task_base["gpu"]
        self.task_trainer["gpu_num"] = self.task_base["gpu_num"]

        # task train
        self.task_train = config["task_train"]
        self.task_train["gpu"] = self.task_base["gpu"]
        self.task_train["gpu_num"] = self.task_base["gpu_num"]

    @staticmethod
    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(description="Train Model")

        parser.add_argument("--repo_path", type=str, help="current repo path")
        parser.add_argument("--task_name", type=str, help="task job name")
        parser.add_argument("--task_type", default="model_evaluation", type=str, help="define task type")
        parser.add_argument("--model_name", type=str, default="", help="model job name")
        parser.add_argument("--config_name", default="train_config", type=str, help="config file name")

        args = parser.parse_args()

        return args

    def _create_logs_path(self, task_type: str) -> None:
        # only model train task job will create/remove logs path
        if task_type != MODEL_TRAIN:
            return

        log_path = self.task_base.get("log_dir", "")
        if log_path == "":
            log_path = f"{self.task_base['repo_path']}/log"
        else:
            log_path = f"{log_path}/log"

        if not os.path.exists(log_path):
            os.makedirs(log_path)

        task_logs_path = f"{log_path}/{self.task_name}"
        if not os.path.exists(task_logs_path):
            os.makedirs(task_logs_path)

        exp_logs_path = f"{task_logs_path}/{self.exp_name}"
        if not os.path.exists(exp_logs_path):
            os.makedirs(exp_logs_path)
        elif not self.task_base.get("overwrite_exp_folder", True):
            raise ValueError(
                f"the current {exp_logs_path} directory can't be overwrite, please choice a new exp folder name"
            )

        self.task_base["logs_base_path"] = exp_logs_path

        # logger.info(f"log base path {exp_logs_path} setup done")

    def get_config(self) -> Dict:
        return {
            "task_base": self.task_base,
            "task_data": self.task_data,
            "task_trainer": self.task_trainer,
            "task_train": self.task_train,
        }


class BaseTrainer(abc.ABC):
    dataset_class: AbstractTrainDataset = AbstractTrainDataset

    def __init__(self):
        logger.info("=== Init BaseTrainer start ===")

        config = TrainerConfig()

        logger.info(f"input BaseTrainer: {config.get_config()}")

        # === init config
        self.task_base: Dict = config.task_base
        self.task_data: Dict = config.task_data
        self.task_trainer: Dict = config.task_trainer
        self.task_train: Dict = config.task_train

        # === init tuning param
        self.model: Optional[nn.Module] = None
        self.loss: Optional[nn.Module] = None
        self.optimizer: Optional[torch.optim.Optimizer] = None
        self.scheduler: Optional[torch.optim.lr_scheduler.LRScheduler] = None
        self.metrics: Dict[str, callable] = {}

        # === init callback
        self.callback: Optional[CallbackList] = None

        # === init labels
        self.labels: Optional[List[str]] = self.task_train.get("labels", None)

        # === init others
        self.gpu: bool = self.task_trainer["gpu"]
        self.gpu_num: int = self.task_trainer["gpu_num"]
        self.static_graph: bool = self.task_trainer.get("static_graph", False)

        # === init debug
        self.debug: bool = self.task_trainer.get("debug", False)

        logger.info("=== Init BaseTrainer done ===")

    # === dataset ===
    def create_dataset(self) -> (AbstractTrainDataset, AbstractTrainDataset):
        # deprecated: in the future, the dataset should only come from the task_trainer["dataset_param"]
        task_data = {**self.task_data, **self.task_trainer["dataset_param"]}

        train_dataset = self.dataset_class(task_data, TRAIN_NAME)
        logger.info(f"Number of train data points: {len(train_dataset)}")

        validation_dataset = self.dataset_class(task_data, VALIDATION_NAME)
        logger.info(f"Number of validation_data data points: {len(validation_dataset)}")

        return train_dataset, validation_dataset

    # === model ===
    def create_model(self) -> None:
        raise NotImplementedError("please implement create_model func")

    def model_to_cuda(self) -> None:
        if not self.gpu:
            return

        if self.gpu_num > 1:
            self.model = nn.DataParallel(
                self.model, device_ids=[i + self.task_base["cuda_core"] for i in range(self.gpu_num)]
            )

        self.model = self.model.cuda()
        logger.info(f"cuda version: {torch.version.cuda}")
        logger.info(f"default cuda device check: {self.task_base['cuda_core']}")
        logger.info(f"model device check: {next(self.model.parameters()).device}")
        logger.info(f"model device count: {torch.cuda.device_count()}")

    def print_model(self, model: nn.Module, inputs: Dict):
        default_summary_info = {
            "show_input": False,
            "show_hierarchical": False,
            "print_summary": True,
            "max_depth": 999,
            "show_parent_layers": True,
        }

        model_summary = self.task_trainer.get("model_summary", default_summary_info)

        logger.info("=== Print Model Structure ===")
        logger.info(model)

        if self.gpu:
            if self.gpu_num >= 2:
                logger.info("when gpu num is over 2, we do not support model summary function")
                return

            inputs = {key: inputs[key].cuda() for key in inputs}

        str_summary = summary_model(
            model,
            inputs,
            show_input=model_summary["show_input"],
            show_hierarchical=model_summary["show_hierarchical"],
            # print_summary=model_summary["print_summary"],
            max_depth=model_summary["max_depth"],
            show_parent_layers=model_summary["show_parent_layers"],
        )

        logger.info(str_summary)

    def create_optimize(self) -> None:
        optimizer_param = self.task_trainer["optimizer_param"]

        if optimizer_param["name"] == "adam":
            self.optimizer = torch.optim.Adam(self.model.parameters(), lr=optimizer_param["learning_rate"])
        else:
            raise ValueError(f"optimizer name do not set properly, please check: {optimizer_param['optimizer']}")

    def create_lr(self) -> None:
        optimizer_param = self.task_trainer["optimizer_param"]

        scheduler_method = optimizer_param.get("scheduler", "default")

        if scheduler_method == "multi_step":
            batch_per_epoch = optimizer_param.get("batch_per_epoch", 1)
            milestones = optimizer_param["milestones"]
            decay_per_step = optimizer_param.get("decay_per_step", 1)

            self.scheduler = torch.optim.lr_scheduler.MultiStepLR(
                self.optimizer,
                milestones=[i * batch_per_epoch for i in milestones],
                gamma=decay_per_step,
            )
        else:
            decay_per_step = optimizer_param.get("decay_per_step", 1)

            self.scheduler = DefaultLRScheduler(self.optimizer, gamma=decay_per_step)

    def create_loss(self) -> None:
        loss_param = self.task_trainer["loss_param"]

        if loss_param["name"] == "mse":
            self.loss = nn.MSELoss()
        if loss_param["name"] == "euclidean_distance_mse":
            self.loss = EuclideanDistanceMSE()
        else:
            raise ValueError(f"loss name do not set properly, please check: {loss_param['name']}")

    def create_metrics(self):
        metrics_param = self.task_trainer.get("metrics_param", {})

        for p in metrics_param:
            if p == "mean_absolute_error":
                self.metrics["mean_absolute_error"] = torchmetrics.functional.mean_absolute_error
            elif p == "explained_variance":
                self.metrics["explained_variance"] = torchmetrics.functional.explained_variance
            else:
                raise ValueError(f"metrics name do not set properly, please check: {p}")

    def create_callback(self) -> None:
        callback_param = self.task_trainer["callback_param"]
        callback_list = []

        tensorboard_param = callback_param.get("tensorboard", {})
        tensorboard = TensorBoardCallback(self.task_base, tensorboard_param)
        if len(tensorboard_param) > 0:
            callback_list.append(tensorboard)

        checkpoint_param = callback_param.get("model_checkpoint", {})
        model_checkpoint = ModelCheckpointCallback(self.task_base, checkpoint_param)
        if len(checkpoint_param) > 0:
            callback_list.append(model_checkpoint)

        logs_param = callback_param.get("logs", {})
        log_record = LogCallback(self.task_base, logs_param)
        if len(logs_param) > 0:
            callback_list.append(log_record)

        scheduling_param = callback_param.get("scheduling", {"avoid_work_hour": False})
        scheduling = SchedulingCallback(self.task_base, scheduling_param)
        if scheduling_param["avoid_work_hour"]:
            callback_list.append(scheduling)

        self.callback = CallbackList(
            callbacks=callback_list, model=self.model, optimizer=self.optimizer, use_gpu=self.gpu
        )

    def create_evaluation_callback(self) -> None:
        callback_param = self.task_trainer["callback_param"]
        callback_list = []

        logs_param = callback_param.get("logs", {})
        log_record = LogCallback(self.task_base, logs_param)
        if len(logs_param) > 0:
            callback_list.append(log_record)

        scheduling_param = callback_param.get("scheduling", {})
        scheduling = SchedulingCallback(self.task_base, scheduling_param)
        if len(scheduling_param) > 0:
            callback_list.append(scheduling)

        self.callback = CallbackList(
            callbacks=callback_list, model=self.model, optimizer=self.optimizer, use_gpu=self.gpu
        )

    def init_model_weights(self) -> int:
        init_model_weights = self.task_trainer.get("init_model_weights", False)

        if not init_model_weights:
            return 0

        ckpt_path = f"{self.task_base['logs_base_path']}/checkpoint/ckpt.pth"

        if not os.path.exists(ckpt_path):
            return 0

        checkpoint = torch.load(ckpt_path)

        self.model.load_state_dict(checkpoint["model_state_dict"])

        self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])

        epoch = checkpoint["epoch"]

        logger.info(f"model init previous checkpoint at epoch={epoch} done")

        return epoch

    # === train ===
    def train(self):
        task_trainer = self.task_trainer

        # ====== Params ======
        epoch = task_trainer["epochs"]
        dataset_param = task_trainer["dataset_param"]

        # corner case test:
        # if infinite = True and per_epoch_steps = None, the model will stuck in train loops
        if not task_trainer.get("per_epoch_steps", None) and task_trainer.get("infinite", False):
            raise ValueError("when infinite = True, please setup the per_epoch_steps")

        # ====== Generate dataset ======
        train_dataset, validation_dataset = self.create_dataset()

        # ====== Generate data loader ======
        if isinstance(train_dataset, BaseDataset):
            train_data_loader = DataLoader(
                dataset=train_dataset,
                batch_size=dataset_param.get("batch_size", 1),
                shuffle=dataset_param.get("train_shuffle", True),
                # num_workers=dataset_param.get("num_workers", 0),
                # prefetch_factor=dataset_param.get("prefetch_factor", None)
            )
            validation_data_loader = DataLoader(
                dataset=validation_dataset,
                batch_size=dataset_param.get("val_batch_size", len(validation_dataset)),
                shuffle=dataset_param.get("test_shuffle", False),
                # num_workers=dataset_param.get("num_workers", 0),
                # prefetch_factor=dataset_param.get("prefetch_factor", None)
            )
        else:
            train_data_loader = DataLoader(
                dataset=train_dataset,
                batch_size=dataset_param.get("batch_size", 1),
                num_workers=dataset_param.get("val_num_workers", dataset_param.get("num_workers", 0)),
                prefetch_factor=dataset_param.get("prefetch_factor", None),
                pin_memory=dataset_param.get("pin_memory", False),
                persistent_workers=dataset_param.get("persistent_workers", False),
            )

            validation_data_loader = DataLoader(
                dataset=validation_dataset,
                batch_size=dataset_param.get("val_batch_size", 1),
                num_workers=dataset_param.get("val_num_workers", dataset_param.get("num_workers", 0)),
                prefetch_factor=dataset_param.get("val_prefetch_factor", None),
                pin_memory=dataset_param.get("pin_memory", False),
                persistent_workers=dataset_param.get("persistent_workers", False),
            )

        # ====== Create model ======
        self.create_model()

        self.model_to_cuda()

        self.print_model(self.model, train_dataset.get_head_inputs(dataset_param.get("batch_size", 1)))

        if self.static_graph:
            self.model = torch.jit.trace(self.model, train_dataset.get_head_inputs(1))

        # ====== Init optimizer ======
        self.create_optimize()

        # ====== Init learning rate ======
        self.create_lr()

        # ====== Init loss ======
        self.create_loss()

        # ====== Init metrics ======
        self.create_metrics()

        # ====== Init Model Weight ======
        init_epoch = self.init_model_weights()

        # self.model = debug_model(self.model, self.task_base["logs_base_path"])

        # ====== Init callback ======
        self.create_callback()

        self.callback.on_train_begin()

        for t in range(init_epoch + 1, epoch + 1):
            self.callback.on_epoch_begin(t)

            train_metrics = self.train_step(self.model, train_data_loader)

            val_metrics = self.validation_step(self.model, validation_data_loader, t, t == epoch)

            self.callback.on_epoch_end(t, train_metrics=train_metrics, val_metrics=val_metrics)

        self.callback.on_train_end(epoch=epoch)

    def evaluation(self):
        task_trainer = self.task_trainer

        # ====== Params ======
        dataset_param = task_trainer["dataset_param"]

        # ====== Generate dataset ======
        test_dataset = self.dataset_class(dataset_param, VALIDATION_NAME)
        logger.info(f"Number of test data points: {len(test_dataset)}")

        # ====== Generate data loader ======
        test_data_loader = DataLoader(
            dataset=test_dataset,
            batch_size=dataset_param.get("batch_size", 1),
            num_workers=dataset_param.get("num_workers", 0),
            prefetch_factor=dataset_param.get("prefetch_factor", None),
        )

        # ====== Create model ======
        self.create_model()

        if self.gpu:
            model = self.model.cuda()
            logger.info(f"cuda version: {torch.version.cuda}")
            logger.info(f"model device check: {next(model.parameters()).device}")

        self.print_model(self.model, test_dataset.get_head_inputs(dataset_param.get("batch_size", 1)))

        if self.static_graph:
            self.model = torch.jit.trace(self.model, test_dataset.get_head_inputs(1))

        # ====== Init optimizer ======
        self.create_optimize()

        # ====== Init loss & metrics ======
        self.create_loss()
        self.create_metrics()

        # ====== Init Model Weight ======
        epoch = self.init_model_weights()

        # ====== Init callback ======
        self.create_evaluation_callback()

        self.callback.on_evaluation_begin()

        val_metrics = self.validation_step(self.model, test_data_loader, epoch, True)

        self.callback.on_evaluation_end(epoch=epoch, val_metrics=val_metrics)

    def compute_loss(self, predictions: Dict[str, Tensor], labels: Dict[str, Tensor]) -> Dict[str, Tensor]:
        losses = dict()

        for label_name in self.labels:
            prediction = predictions[label_name]
            label = labels[label_name]

            losses[label_name] = self.loss(prediction, label)

        return losses

    def compute_metrics(
        self, metrics_func: callable, predictions: Dict[str, Tensor], labels: Dict[str, Tensor]
    ) -> Union[Dict[str, Tensor], Tensor]:
        metrics = dict()

        for label_name in self.labels:
            prediction = predictions[label_name]
            label = labels[label_name]

            metrics[label_name] = metrics_func(prediction, label)

        return metrics

    def to_device(self, data: Dict[str, torch.Tensor]) -> Union[Dict[str, torch.Tensor], torch.Tensor]:
        if self.gpu:
            if isinstance(data, torch.Tensor):
                return data.cuda(non_blocking=True)
            return {key: value.cuda(non_blocking=True) for key, value in data.items()}
        else:
            return data

    def post_transform_data(
        self, data: (Union[Dict[str, Tensor], Tensor], Union[Dict[str, Tensor], Tensor])
    ) -> (Union[Dict[str, Tensor], Tensor], Union[Dict[str, Tensor], Tensor]):
        inputs, labels = data

        return self.to_device(inputs), self.to_device(labels)

    def train_step(self, model: nn.Module, data_loader: DataLoader) -> Dict:
        task_trainer = self.task_trainer

        per_epoch_steps: Optional[int] = task_trainer.get("per_epoch_steps", None)

        batch = 0

        metrics = {}

        model.train()

        for _, data in enumerate(data_loader):
            if batch == per_epoch_steps:
                break

            step_time_start = time.time()

            # Forward pass: compute predicted y by passing x to the model.
            # note: by default, we assume batch size = 1
            train_inputs, train_labels = self.post_transform_data(data)

            time_2_device = time.time()

            # Compute and print loss.
            outputs = model(train_inputs)  # noqa

            loss: Union[Tensor, Dict[str, Tensor]] = self.compute_loss(outputs, train_labels)
            total_loss: Optional[Union[Dict[Tensor], Tensor]] = None

            if isinstance(loss, Tensor):
                total_loss = loss
                metrics["train_loss"] = metrics["train_loss"] + loss.item() if "train_loss" in metrics else loss.item()
            elif isinstance(loss, Dict):
                total_loss = sum(loss.values())  # noqa
                for name, loss in loss.items():
                    metrics[f"{name}_train_loss"] = (
                        metrics[f"{name}_train_loss"] + loss.item() if f"{name}_train_loss" in metrics else loss.item()
                    )

            time_2_fw = time.time()

            # Before the backward pass, use the optimizer object to zero all the
            # gradients for the variables it will update (which are the learnable
            # weights of the model). This is because by default, gradients are
            # accumulated in buffers( i.e, not overwritten) whenever .backward()
            # is called. Checkout docs of torch.autograd.backward for more details.
            self.optimizer.zero_grad()

            # Backward pass: compute gradient of the loss with respect to model parameters
            total_loss.backward()

            def check_grad(name, param):
                if param.grad is not None:
                    grad_norm = param.grad.norm()
                    if torch.isnan(grad_norm) or torch.isinf(grad_norm):
                        print(f"Gradient problem in {name}: {grad_norm}")
                        return True
                return False

            for name, param in model.named_parameters():
                if check_grad(name, param):
                    for name, param in model.named_parameters():
                        print(f"==> {name} {param.tolist()} {param.grad}")
                    raise Exception("value error, has grad problem")

            # Calling the step function on an Optimizer makes an update to its parameters
            self.optimizer.step()

            self.scheduler.step()

            time_2_bw = time.time()

            metrics.update(
                **{
                    "time_2_device": time_2_device - step_time_start,
                    "time_2_fw": time_2_fw - time_2_device,
                    "time_2_bw": time_2_bw - time_2_fw,
                    "time_per_step": time_2_bw - step_time_start,
                    "lr": self.scheduler.get_last_lr()[0],
                }
            )

            self.callback.on_train_batch_end(batch, metrics=metrics)

            batch += 1

        for p in metrics:
            if "loss" in p or "error" in p:
                metrics[p] = metrics[p] / batch

        return metrics

    def compute_validation_loss(self, predictions: Dict[str, Tensor], labels: Dict[str, Tensor]) -> Dict[str, Tensor]:
        return self.compute_loss(predictions, labels)

    def validation_step_check(self, epoch: int, is_last_epoch: bool) -> bool:
        return True

    def post_transform_val_data(
        self, data: (Union[Dict[str, Tensor], Tensor], Union[Dict[str, Tensor], Tensor])
    ) -> (Union[Dict[str, Tensor], Tensor], Union[Dict[str, Tensor], Tensor]):
        inputs, labels = data

        return self.to_device(inputs), self.to_device(labels)

    def validation_step(self, model: nn.Module, data_loader: DataLoader, epoch: int, is_last_epoch: bool) -> Dict:
        if not self.validation_step_check(epoch, is_last_epoch):
            return dict()

        # Set the model to evaluation mode, disabling dropout and using population
        # statistics for batch normalization.
        total_batch = 0
        metrics = {}

        model.eval()

        with torch.no_grad():
            for _, val_data in enumerate(data_loader):
                total_batch += 1

                val_inputs, val_labels = self.post_transform_val_data(val_data)  # noqa

                outputs = model(val_inputs)

                loss = self.compute_validation_loss(outputs, val_labels)

                if isinstance(loss, torch.Tensor):  # v1, deprecated
                    metrics["val_loss"] = metrics["val_loss"] + loss.item() if "val_loss" in metrics else loss.item()

                    # Compute metrics
                    for p in self.metrics:
                        results: Tensor = self.compute_metrics(self.metrics[p], outputs, val_labels)
                        metrics[f"val_{p}"] = (
                            metrics[f"val_{p}"] + results.item() if f"val_{p}" in metrics else results.item()
                        )

                elif isinstance(loss, Dict):
                    for name, loss in loss.items():
                        metrics[f"{name}_val_loss"] = (
                            metrics[f"{name}_val_loss"] + loss.item() if f"{name}_val_loss" in metrics else loss.item()
                        )

                    # Compute metrics
                    for p in self.metrics:
                        results: Dict[str, Tensor] = self.compute_metrics(self.metrics[p], outputs, val_labels)
                        for name, r in results.items():
                            metrics[f"val_{name}_{p}"] = (
                                metrics[f"val_{name}_{p}"] + r.item() if f"val_{name}_{p}" in metrics else r.item()
                            )

                # print(batch_cnt, loss, metrics)

            for p in metrics:
                metrics[p] = metrics[p] / total_batch

        return metrics
