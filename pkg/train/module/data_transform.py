import abc
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import torch
from torch import Tensor

from common.constant import MAX_VAL, MEAN_VAL, MIN_VAL, STD_VAL


class DataTransform(abc.ABC):
    """Abstract base class for data transformations.

    Defines interface for transforming data samples. All data transform classes should inherit from this.
    """

    def __call__(self, *args, **kwargs):
        """Transform a data sample.

        Must be implemented by subclasses to define specific transformation logic.
        """
        raise NotImplementedError("please implement this function __call__(self, *args, **kwargs)")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


class TFRecordToTensor(DataTransform):
    """Converts TFRecord data to PyTorch tensors.

    Transforms TFRecord format data into PyTorch tensors with appropriate data types.

    Args:
        config (Dict): Configuration containing context and feature descriptions
    """

    convert_type = {
        "float": torch.float32,
        "int": torch.int64,
    }

    def __init__(self, config: Dict) -> None:
        self.context_list = config["context_description"]
        self.feature_list = config["feature_description"]

    def __call__(self, sample: Tuple[Dict[str, List], Dict[str, List]]) -> Tuple[Dict[str, Tensor], Dict[str, Tensor]]:
        """Convert TFRecord sample to tensors.

        Args:
            sample: Tuple of (context, feature) dictionaries containing TFRecord data

        Returns:
            Tuple of (context_tensor, feature_tensor) dictionaries containing PyTorch tensors
        """
        context, feature = sample

        context_tensor: Dict[str, Tensor] = dict()
        feature_tensor: Dict[str, Tensor] = dict()

        for name, fea in context.items():
            if name not in self.context_list:
                raise ValueError(f"please check your feature list and add {name}")

            context_tensor[name] = torch.tensor(np.array(fea), dtype=self.convert_type[self.context_list[name]])

        for name, fea in feature.items():
            if name not in self.feature_list:
                raise ValueError(f"please check your feature list and add {name}")

            feature_tensor[name] = torch.tensor(np.array(fea), dtype=self.convert_type[self.feature_list[name]])

        return context_tensor, feature_tensor


class ToTensor(DataTransform):
    """Converts numpy arrays to PyTorch tensors.

    Transforms numpy array data into PyTorch tensors with appropriate data types.

    Args:
        config (Dict): Configuration containing context and feature descriptions
    """

    convert_type = {
        "float": torch.float32,
        "int": torch.int64,
    }

    def __init__(self, config: Dict) -> None:
        self.context_list = config["context_description"]
        self.feature_list = config["feature_description"]

    def __call__(
            self, sample: Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]
    ) -> Tuple[Dict[str, Tensor], Dict[str, Tensor]]:
        """Convert numpy arrays to tensors.

        Args:
            sample: Tuple of (context, feature) dictionaries containing numpy arrays

        Returns:
            Tuple of (context_tensor, feature_tensor) dictionaries containing PyTorch tensors
        """
        context, feature = sample

        context_tensor: Dict[str, Tensor] = dict()
        feature_tensor: Dict[str, Tensor] = dict()

        for name, fea in context.items():
            if name not in self.context_list:
                raise ValueError(f"please check your feature list and add {name}")

            context_tensor[name] = torch.tensor(fea, dtype=self.convert_type[self.context_list[name]])

        for name, fea in feature.items():
            if name not in self.feature_list:
                raise ValueError(f"please check your feature list and add {name}")

            feature_tensor[name] = torch.tensor(fea, dtype=self.convert_type[self.feature_list[name]])

        return context_tensor, feature_tensor


@DeprecationWarning
class TensorToGPU(DataTransform):
    """Moves PyTorch tensors to GPU.

    Deprecated: Use .to(device) instead.

    Args:
        config (Dict): Configuration containing GPU settings
    """

    def __init__(self, config: Dict) -> None:
        self.gpu = config["gpu"]
        self.cuda_core = config["cuda_core"]

    def __call__(
            self, sample: Tuple[Dict[str, Tensor], Dict[str, Tensor]]
    ) -> Tuple[Dict[str, Tensor], Dict[str, Tensor]]:
        """Move tensors to GPU.

        Args:
            sample: Tuple of (context, feature) dictionaries containing tensors

        Returns:
            Tuple of (context, feature) dictionaries with tensors moved to GPU
        """
        if not self.gpu:
            return sample

        context, feature = sample

        for name, fea in context.items():
            context[name] = fea.cuda()

        for name, fea in feature.items():
            feature[name] = fea.cuda()

        return context, feature


class Norm(DataTransform):
    """Base class for data normalization.

    Provides functionality for loading normalization statistics.

    Args:
        config (Dict): Configuration containing normalization parameters
        global_scaling (bool): Whether to use global statistics for normalization
        coarse_dim (bool): Whether to use coarse-grained dimension-wise normalization
        setup_val (bool): Whether to load statistics from values instead of files
    """

    def __init__(
            self, config: Dict, global_scaling: bool = True, coarse_dim: bool = False, setup_val: bool = False
    ) -> None:
        self.normalization_config = config
        if not setup_val:
            self.feature_config: Dict[str, Dict[str, Tensor]] = self.load_stats_file()
        else:
            self.feature_config: Dict[str, Dict[str, Tensor]] = self.load_stats_value()

        self.global_scaling = global_scaling
        self.coarse_dim = coarse_dim

    def load_stats_file(self) -> Dict[str, Dict[str, Tensor]]:
        """Load normalization statistics from files.

        Returns:
            Dictionary containing normalization statistics loaded from files
        """
        feature_config: Dict[str, Dict[str, Tensor]] = {}

        for key, path in self.normalization_config.items():
            loaded_data = np.load(path)
            feature_config.update({key: {name: torch.tensor(loaded_data[name]) for name in loaded_data}})

        return feature_config

    def load_stats_value(self) -> Dict[str, Dict[str, Tensor]]:
        """Load normalization statistics from values.

        Returns:
            Dictionary containing normalization statistics from config values
        """
        feature_config: Dict[str, Dict[str, Tensor]] = {}

        for key, config in self.normalization_config.items():
            feature_config.update({key: {name: torch.tensor(config[name]) for name in config}})

        return feature_config


class MaxMinNorm(Norm):
    """Min-max normalization of tensors.

    Normalizes tensors to [0,1] range using min-max scaling.

    Args:
        config (Dict): Configuration containing normalization parameters
        global_scaling (bool): Whether to use global statistics
        coarse_dim (bool): Whether to use coarse-grained normalization
        setup_val (bool): Whether to load statistics from values
    """

    def __init__(
            self, config: Dict, global_scaling: bool = True, coarse_dim: bool = False, setup_val: bool = False
    ) -> None:
        super().__init__(config, global_scaling, coarse_dim, setup_val)

        self.max_val_name = MAX_VAL
        self.min_val_name = MIN_VAL

    def __call__(
            self, sample: Tuple[Dict[str, Tensor], Dict[str, Tensor]]
    ) -> Tuple[Dict[str, Tensor], Dict[str, Tensor]]:
        """Apply min-max normalization.

        Args:
            sample: Tuple of (context, feature) dictionaries containing tensors

        Returns:
            Tuple of (context, feature) dictionaries with normalized tensors
        """
        context, feature = sample

        for name, fea in context.items():
            if name in self.feature_config:
                if self.global_scaling:
                    max_val, min_val = (
                        self.feature_config[name][self.max_val_name],
                        self.feature_config[name][self.min_val_name],
                    )
                else:
                    max_val, min_val = self._calculate_max_min(fea)
                if self.coarse_dim:
                    max_val, min_val = torch.max(max_val), torch.min(min_val)
                context[name] = self._normal_max_min_transform(fea, max_val, min_val)

        for name, fea in feature.items():
            if name in self.feature_config:
                if self.global_scaling:
                    max_val, min_val = (
                        self.feature_config[name][self.max_val_name],
                        self.feature_config[name][self.min_val_name],
                    )
                else:
                    max_val, min_val = self._calculate_max_min(fea)
                if self.coarse_dim:
                    max_val, min_val = torch.max(max_val), torch.min(min_val)
                feature[name] = self._normal_max_min_transform(fea, max_val, min_val)

        return context, feature

    @staticmethod
    def _calculate_max_min(array: Tensor) -> (Tensor, Tensor):
        """Calculate max and min values along first dimension.

        Args:
            array: Input tensor

        Returns:
            Tuple of (max_values, min_values)
        """
        return torch.max(array, 0).values, torch.min(array, 0).values

    @staticmethod
    def _normal_max_min_transform(array: Tensor, max_norm_val: Tensor, min_norm_val: Tensor) -> Tensor:
        """Apply min-max normalization transform.

        Args:
            array: Input tensor
            max_norm_val: Maximum values for normalization
            min_norm_val: Minimum values for normalization

        Returns:
            Normalized tensor
        """
        return (array - min_norm_val) / (max_norm_val - min_norm_val)


class NormalNorm(Norm):
    """Z-score normalization of tensors.

    Normalizes tensors using mean and standard deviation.
    """

    def __call__(
            self, sample: Tuple[Dict[str, Tensor], Dict[str, Tensor]]
    ) -> Tuple[Dict[str, Tensor], Dict[str, Tensor]]:
        """Apply z-score normalization.

        Args:
            sample: Tuple of (context, feature) dictionaries containing tensors

        Returns:
            Tuple of (context, feature) dictionaries with normalized tensors
        """
        context, feature = sample

        for name, fea in context.items():
            if name in self.feature_config:
                mean_val = self.feature_config[name][MEAN_VAL]
                std_val = self.feature_config[name][STD_VAL]
                context[name] = self._normal_transform(fea, mean_val, std_val)

        for name, fea in feature.items():
            if name in self.feature_config:
                mean_val = self.feature_config[name][MEAN_VAL]
                std_val = self.feature_config[name][STD_VAL]
                feature[name] = self._normal_transform(fea, mean_val, std_val)

        return context, feature

    @staticmethod
    def _normal_transform(array: Tensor, mean_val: Tensor, std_val: Tensor) -> Tensor:
        """Apply z-score normalization transform.

        Args:
            array: Input tensor
            mean_val: Mean values for normalization
            std_val: Standard deviation values for normalization

        Returns:
            Normalized tensor
        """
        return (array - mean_val) / std_val


class CovertToModelInputs(DataTransform):
    """Converts data samples to model input format.

    Separates features and labels from data samples.

    Args:
        config (Dict): Configuration containing label names
        multi_obj (bool): Whether multiple objectives/labels are used
    """

    def __init__(self, config: Dict, multi_obj: bool = False) -> None:
        self.labels_name = config["labels"]
        self.multi_obj = multi_obj

    def __call__(
            self, sample: Tuple[Dict[str, Tensor], Dict[str, Tensor]]
    ) -> Tuple[Dict[str, Tensor], Union[Tensor, Dict[str, Tensor]]]:
        """Convert to model inputs.

        Args:
            sample: Tuple of (context, feature) dictionaries

        Returns:
            Tuple of (inputs, labels) where inputs is a dictionary and labels is either
            a tensor or dictionary depending on multi_obj setting
        """
        context, feature = sample

        inputs: Optional[Dict[str, Tensor]] = dict()
        labels: Optional[Union[Tensor, Dict[str, Tensor]]] = dict() if self.multi_obj else None

        for name, fea in context.items():
            if name not in self.labels_name:
                inputs[name] = fea
            else:
                if self.multi_obj:
                    labels[name] = fea
                else:
                    labels = fea

        for name, fea in feature.items():
            if name not in self.labels_name:
                inputs[name] = fea
            else:
                if self.multi_obj:
                    labels[name] = fea
                else:
                    labels = fea

        return inputs, labels


class ClampTensor(DataTransform):
    """Clamps tensor values to specified range.

    Args:
        config (Dict): Configuration containing min/max values for clamping
    """

    def __init__(self, config: Dict) -> None:
        self.clamp_config = config

    def __call__(
            self, sample: Tuple[Dict[str, Tensor], Dict[str, Tensor]]
    ) -> Tuple[Dict[str, Tensor], Union[Tensor, Dict[str, Tensor]]]:
        """Clamp tensor values.

        Args:
            sample: Tuple of (context, feature) dictionaries

        Returns:
            Tuple of (context, feature) with clamped tensor values
        """
        context, feature = sample

        for name, fea in context.items():
            if name in self.clamp_config:
                max_val = self.clamp_config[name][MAX_VAL]
                min_val = self.clamp_config[name][MIN_VAL]
                context[name] = context[name].clamp(min_val, max_val)

        for name, fea in feature.items():
            if name in self.clamp_config:
                max_val = self.clamp_config[name][MAX_VAL]
                min_val = self.clamp_config[name][MIN_VAL]
                feature[name] = feature[name].clamp(min_val, max_val)

        return context, feature


class SqueezeDataDim(DataTransform):
    """Removes singleton dimensions from tensors.

    Args:
        config (Dict): Configuration containing dimensions to squeeze
    """

    def __init__(self, config: Dict) -> None:
        self.feature_config = config

    def __call__(
            self, sample: Tuple[Dict[str, Tensor], Dict[str, Tensor]]
    ) -> Tuple[Dict[str, Tensor], Dict[str, Tensor]]:
        """Squeeze tensor dimensions.

        Args:
            sample: Tuple of (context, feature) dictionaries

        Returns:
            Tuple of (context, feature) with squeezed tensors
        """
        context, feature = sample

        for name, fea in context.items():
            if name in self.feature_config:
                context[name] = fea.squeeze(dim=self.feature_config[name])

        for name, fea in feature.items():
            if name in self.feature_config:
                feature[name] = fea.squeeze(dim=self.feature_config[name])

        return context, feature


class UnSqueezeDataDim(DataTransform):
    """Adds singleton dimensions to tensors.

    Args:
        config (Dict): Configuration containing dimensions to unsqueeze
    """

    def __init__(self, config: Dict) -> None:
        self.feature_config = config

    def __call__(
            self, sample: Tuple[Dict[str, Tensor], Dict[str, Tensor]]
    ) -> Tuple[Dict[str, Tensor], Dict[str, Tensor]]:
        """Unsqueeze tensor dimensions.

        Args:
            sample: Tuple of (context, feature) dictionaries

        Returns:
            Tuple of (context, feature) with unsqueezed tensors
        """
        context, feature = sample

        for name, fea in context.items():
            if name in self.feature_config:
                context[name] = fea.unsqueeze(dim=self.feature_config[name])

        for name, fea in feature.items():
            if name in self.feature_config:
                feature[name] = fea.unsqueeze(dim=self.feature_config[name])

        return context, feature
