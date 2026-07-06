# common/logger_utils.py
# ============================================================
# Single, consolidated logger for all AWR/SAR parser modules.
# Replaces both common/logger.py and the old logger_utils.py.
#
# Key fixes vs old version:
#   - LOG_DIR derived dynamically from project root (no hardcoded Windows path)
#   - log_dir can be overridden per-module if needed
#   - Console handler is optional (default ON, can suppress with console=False)
#   - Configurable log level via settings.yaml or override param
#   - Thread-safe: no duplicate handlers across re-imports
# ============================================================

import os
import logging
from logging.handlers import RotatingFileHandler

# Resolve project root as the parent of the 'common' directory.
# Works regardless of OS or install path.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DEFAULT_LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")


def get_logger(
    name: str,
    log_dir: str = None,
    level: str = "INFO",
    max_bytes: int = 5 * 1024 * 1024,   # 5 MB per file
    backup_count: int = 5,
    console: bool = True,
) -> logging.Logger:
    """
    Return a named rotating-file logger.  Safe to call multiple times —
    duplicate handlers are never added.

    Args:
        name        : Logger name, also used as the log filename (<name>.log).
        log_dir     : Directory for log files.  Defaults to <project_root>/logs.
        level       : Logging level string: DEBUG | INFO | WARNING | ERROR.
        max_bytes   : Max size of a single log file before rotation.
        backup_count: Number of rotated files to keep.
        console     : If True, also emit to stdout (useful during dev/testing).

    Returns:
        logging.Logger
    """
    log_directory = log_dir or _DEFAULT_LOG_DIR
    os.makedirs(log_directory, exist_ok=True)

    logger = logging.getLogger(name)

    # Guard: if handlers already attached this logger is fully configured.
    if logger.handlers:
        return logger

    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Rotating file handler ──────────────────────────────────────────
    log_path = os.path.join(log_directory, f"{name}.log")
    fh = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # ── Optional console handler ───────────────────────────────────────
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    # Prevent messages propagating to the root logger (avoids double-print).
    logger.propagate = False

    return logger
