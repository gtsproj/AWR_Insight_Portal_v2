#utils/logger_utils.py

import os
import logging
from logging.handlers import RotatingFileHandler

# Base log directory
LOG_DIR = r"C:\awr_parser_project\logs"
os.makedirs(LOG_DIR, exist_ok=True)

def get_logger(name: str, log_file: str = None):
    """
    Create a rotating logger for each parser.
    
    Args:
        name (str): Logger name (e.g., "DBInfoParser")
        log_file (str): Optional custom log filename. Defaults to "<name>.log"
    
    Returns:
        logger (logging.Logger): Configured logger
    """
    logger = logging.getLogger(name)
    if logger.handlers:  # Avoid adding duplicate handlers
        return logger

    logger.setLevel(logging.INFO)

    # Log file path
    if not log_file:
        log_file = f"{name.lower()}.log"
    log_path = os.path.join(LOG_DIR, log_file)

    # Rotating file handler
    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding="utf-8"
    )

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Console handler (optional)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
