# watcher.py
# ============================================================
# AWR File Watcher — monitors awr_reports\<DBNAME>\ for new
# AWR HTML files and adds them to the queue for processing
# by queue_processor.py (daemon).
#
# Queue format (compatible with queue_processor.py v2):
#   queues\queue_<DBNAME>.json — list of dicts with status field
#
# This watcher ONLY enqueues files. All parsing is handled
# by queue_processor.py running as a separate service.
# ============================================================

import os
import sys
import time
import json
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from logger_utils import get_logger

logger = get_logger("watcher")

WATCH_DIR     = os.path.join(_PROJECT_ROOT, "awr_reports")
QUEUE_DIR     = os.path.join(_PROJECT_ROOT, "queues")
POLL_INTERVAL = 10   # seconds


def extract_snap_time(filename: str):
    """
    Extract snap BEGIN time from AWR filename for sequential ordering.
    Format: awr_DBNAME_INST_YYMMDD_HHMM_HHMM.html
    e.g.    awr_COLDBPRD_1_221212_0730_0830.html → 2022-12-12 07:30
    Falls back to datetime.max if parsing fails.
    """
    try:
        parts = filename.split("_")
        # parts: ['awr', 'COLDBPRD', '1', '221212', '0730', '0830.html']
        # snap date = parts[-3], snap begin time = parts[-2]
        snap_date  = parts[-3]   # YYMMDD
        snap_begin = parts[-2]   # HHMM
        return datetime.strptime(snap_date + snap_begin, "%y%m%d%H%M")
    except Exception:
        try:
            # Fallback: try original pattern (older filename formats)
            parts = filename.split("_")
            snap_date = parts[-2]
            snap_time = parts[-1].split(".")[0]
            return datetime.strptime(snap_date + snap_time, "%y%m%d%H%M")
        except Exception:
            return datetime.max


def _queue_path(dbname: str) -> str:
    return os.path.join(QUEUE_DIR, f"queue_{dbname}.json")


def _load_queue(dbname: str) -> list:
    path = _queue_path(dbname)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_queue(dbname: str, items: list) -> None:
    os.makedirs(QUEUE_DIR, exist_ok=True)
    path = _queue_path(dbname)
    tmp  = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, default=str)
    os.replace(tmp, path)


def _already_queued(dbname: str, filepath: str) -> bool:
    items = _load_queue(dbname)
    return any(
        (isinstance(i, dict) and i.get("filepath") == filepath) or
        (isinstance(i, str) and i == filepath)
        for i in items
    )


def _enqueue(dbname: str, filepath: str) -> None:
    items = _load_queue(dbname)
    # Migrate legacy string entries to dict format
    migrated = []
    for item in items:
        if isinstance(item, str):
            migrated.append({
                "filepath": item, "status": "DONE",
                "queued_at": None, "retry_count": 0, "error": None,
            })
        else:
            migrated.append(item)

    # Use snap time as queued_at so processor picks up in snap order
    snap_time = extract_snap_time(os.path.basename(filepath))
    snap_str  = snap_time.isoformat() if snap_time != datetime.max else datetime.now().isoformat()

    migrated.append({
        "filepath":    filepath,
        "status":      "PENDING",
        "queued_at":   snap_str,
        "retry_count": 0,
        "error":       None,
    })
    _save_queue(dbname, migrated)
    logger.info(f"Queued: {os.path.basename(filepath)} -> {dbname}")


def _scan():
    if not os.path.isdir(WATCH_DIR):
        return
    for entry in sorted(os.listdir(WATCH_DIR)):
        db_folder = os.path.join(WATCH_DIR, entry)
        if not os.path.isdir(db_folder):
            continue
        dbname = entry.upper()
        awr_files = sorted(
            [f for f in os.listdir(db_folder)
             if f.lower().endswith((".html", ".htm", ".txt"))
             and not f.startswith(".")],
            key=lambda f: extract_snap_time(f)
        )
        for filename in awr_files:
            filepath = os.path.join(db_folder, filename)
            if not _already_queued(dbname, filepath):
                _enqueue(dbname, filepath)


def watch():
    os.makedirs(WATCH_DIR, exist_ok=True)
    os.makedirs(QUEUE_DIR, exist_ok=True)
    logger.info("Watcher started — monitoring AWR reports")
    logger.info(f"  Watch dir : {WATCH_DIR}")
    logger.info(f"  Queue dir : {QUEUE_DIR}")
    logger.info(f"  Layout    : {WATCH_DIR}\\<DBNAME>\\awr_*.html")
    while True:
        try:
            _scan()
        except Exception as e:
            logger.error(f"Watcher error: {e}", exc_info=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    watch()
