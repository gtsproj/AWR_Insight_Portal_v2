#common/config_loader.py

import os
import yaml

def load_config(config_file="config/settings.yaml"):
    """Load YAML config file, with support for environment variable overrides."""
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # Allow DB URI override via environment variable
    db_uri_env = os.getenv("DB_URI")
    if db_uri_env:
        cfg["database"]["uri"] = db_uri_env

    return cfg
