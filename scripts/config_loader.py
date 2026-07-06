#common/config_loader.py

import os
import yaml

def load_config(config_file=None):
    """
    Load the YAML configuration file.
    If config_file is not provided, it will resolve to:
    <project_root>/config/settings.yaml
    """
    if config_file is None:
        # Go up one directory from /common to project root
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_file = os.path.join(base_dir, "config", "settings.yaml")

    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "r") as f:
        return yaml.safe_load(f)
