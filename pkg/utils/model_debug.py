import torch.nn as nn
from torch.utils.tensorboard import SummaryWriter
import functools

writer = SummaryWriter('runs/model_debug')


def debug_model(model):
    def build_module_tree(module):
        def _in(module, id_parent, depth):
            for key, c in module.named_children():
                # ModuleList and Sequential do not count as valid layers
                if isinstance(c, (nn.ModuleList, nn.Sequential)):
                    _in(c, id_parent, depth)
                else:
                    _module_name = str(key).split(".")[-1].split("'")[0]
                    _parent_layers = (
                        f'{module_summary[id_parent].get("parent_layers")}'
                        f'{"/" if module_summary[id_parent].get("parent_layers") != "" else ""}'
                        f'{module_summary[id_parent]["module_name"]}'
                    )

                    module_summary[id(c)] = {
                        "module_name": _module_name,
                        "parent_layers": _parent_layers,
                        "id_parent": id_parent,
                        "depth": depth,
                        "n_children": 0,
                    }

                    module_summary[id_parent]["n_children"] += 1

                    _in(c, id(c), depth + 1)

        # Defining summary for the main module
        module_summary[id(module)] = {
            "module_name": str(module.__class__).split(".")[-1].split("'")[0],
            "parent_layers": "",
            "id_parent": None,
            "depth": 0,
            "n_children": 0,
        }

        _in(module, id_parent=id(module), depth=1)

    def print_hist_hook(module, input, output):
        writer.add_histogram(module_summary.get(id(module)).get("module_name"), input[0])

    # create properties
    module_summary = dict()

    # register id of parent modules
    build_module_tree(model)

    for m in model.modules():
        print(m)
        if isinstance(m, nn.Linear) or isinstance(m, nn.LayerNorm) or isinstance(m, nn.Tanh):
            m.register_forward_hook(functools.partial(print_hist_hook))

    return model
