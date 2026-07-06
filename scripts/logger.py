#common/logger.py

import logging
from logging.handlers import RotatingFileHandler
import os

def get_logger(name, log_dir="logs", level="INFO", max_bytes=10*1024*1024, backup_count=5):
    """
    Return a logger with rotating file handler.
    Args:
        name (str): logger name (usually module name)
        log_dir (str): directory where log files are stored
        level (str): logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        max_bytes (int): max log file size before rotation
        backup_count (int): number of rotated files to keep
    """
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, f"{name}.log")

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:  # Avoid duplicate handlers if logger is reused
        handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
        formatter = logging.Formatter(
            "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
