from typing import Dict
import os
import yaml


def get_cur_abs_dir(path) -> str:
    abs_dir = os.path.dirname(path)
    return abs_dir


# get the currently repo path, by default assume we have .git file.
def get_repo_path(path: str) -> str:
    # Get the directory of the current script
    script_directory = os.path.dirname(path)

    # Traverse upwards until a directory containing the .git folder is found, indicating the root of the repo
    while script_directory and not os.path.exists(os.path.join(script_directory, ".git")):
        script_directory = os.path.dirname(script_directory)

    return script_directory


def load_yaml(path: str) -> Dict:
    with open(path, "r") as f:
        config = yaml.safe_load(f)

    return config