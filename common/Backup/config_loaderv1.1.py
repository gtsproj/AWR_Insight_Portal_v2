#common/config_loader.py

import os
import yaml

def load_config(config_file=None):
    if config_file is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # go up from /common
        config_file = os.path.join(base_dir, "config", "settings.yaml")

    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "r") as f:
        return yaml.safe_load(f)
