# awr_watcher.py  (v2)
# ============================================================
# AWR Report Watcher — detects new AWR files and routes each
# one to the correct per-DB processing queue.
#
# FIXES vs v1:
#   - Stores FULL PATH in pending_files (was storing basename only)
#   - Enqueues each file individually — no batch debounce that
#     caused all-but-last files to be skipped
#   - Routes each file to its DB queue (queues/queue_<DBNAME>.json)
#     instead of calling master_parser directly from the watcher
#   - Watches per-DB sub-folders under awr_reports/ OR a flat folder
#   - Handles both .html and .txt AWR reports
#   - Skips files already present in any queue (dedup check)
#   - Writes a .lock file while enqueueing to prevent race conditions
#   - Cross-platform: works on Windows and Linux
#
# ARCHITECTURE:
#   awr_watcher.py  →  queues/queue_<DBNAME>.json  →  queue_processor.py
#   (detects files)      (durable work list)           (runs master_parser)
#
# USAGE:
#   python awr_watcher.py               # uses config/settings.yaml
#   python awr_watcher.py --once        # scan once and exit (cron/task mode)
# ============================================================

import os
import re
import sys
import json
import time
import hashlib
import argparse
import threading
from datetime import datetime, timezone
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

sys.path.append(os.path.join(os.path.dirname(__file__), "common"))
from config_loader import load_config
from logger_utils import get_logger

logger = get_logger("awr_watcher")

# ── load config ────────────────────────────────────────────────────────
_cfg = load_config()
_paths = _cfg.get("paths", {})
_watcher_cfg = _cfg.get("watcher", {})

PROJECT_ROOT   = os.path.abspath(os.path.dirname(__file__))
WATCH_ROOT     = os.path.abspath(_paths.get("watch_directory",
                     os.path.join(PROJECT_ROOT, "awr_reports")))
QUEUES_DIR     = os.path.abspath(_paths.get("queues_directory",
                     os.path.join(PROJECT_ROOT, "queues")))
POLL_INTERVAL  = int(_watcher_cfg.get("poll_interval_seconds", 10))
AWR_EXTENSIONS = {".html", ".htm", ".txt"}

os.makedirs(QUEUES_DIR, exist_ok=True)

# ── filename → DB name ─────────────────────────────────────────────────
# Supported patterns (case-insensitive):
#   awr_<DBNAME>_<inst>_<date>_<from>_<to>.html     (standard)
#   awr_<DBNAME>_<inst>_<instnum>_<date>_...html    (RAC variant)
#   awrrpt_<inst>_<from>_<to>.html                  (plain Oracle default)
# Fallback: parent folder name (when files sit in awr_reports/<DBNAME>/)
_AWR_PATTERN = re.compile(
    r"^awr[_-]([A-Za-z0-9$#_]+?)(?:[_-]\d)", re.IGNORECASE
)

def db_name_from_path(filepath: str) -> str:
    """
    Extract database name from AWR file path.
    Priority:
      1. Filename regex  (awr_<DBNAME>_...)
      2. Parent folder name if it is NOT the generic watch root
      3. 'UNKNOWN' as last resort
    """
    fname = os.path.basename(filepath)
    m = _AWR_PATTERN.match(fname)
    if m:
        return m.group(1).upper()

    parent = os.path.basename(os.path.dirname(filepath))
    watch_root_name = os.path.basename(WATCH_ROOT).upper()
    if parent and parent.upper() not in (watch_root_name, "AWR_REPORTS", ""):
        return parent.upper()

    logger.warning(f"Could not determine DB name from path: {filepath} — using UNKNOWN")
    return "UNKNOWN"


def file_hash(filepath: str) -> str:
    """MD5 of first 64 KB — fast dedup without reading whole file."""
    h = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            h.update(f.read(65536))
    except OSError:
        pass
    return h.hexdigest()


# ── queue helpers ──────────────────────────────────────────────────────
def queue_path(db_name: str) -> str:
    return os.path.join(QUEUES_DIR, f"queue_{db_name}.json")


def _load_queue(db_name: str) -> list:
    path = queue_path(db_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_queue(db_name: str, items: list) -> None:
    path = queue_path(db_name)
    tmp  = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, default=str)
    os.replace(tmp, path)   # atomic on POSIX; near-atomic on Windows


def _lock_path(db_name: str) -> str:
    return queue_path(db_name) + ".lock"


def _all_queue_items() -> list:
    """
    Load every item from every queue_*.json in QUEUES_DIR.
    Used by enqueue_file() to do a cross-queue dedup check — prevents
    a duplicate entry when the upload endpoint and the periodic watcher
    scan derive different db_name keys for the same file (e.g. case
    difference or override vs filename-inferred) and therefore look in
    different queue files.
    """
    all_items = []
    try:
        for fname in os.listdir(QUEUES_DIR):
            if fname.startswith("queue_") and fname.endswith(".json"):
                fpath = os.path.join(QUEUES_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_items.extend(data)
                except (json.JSONDecodeError, OSError):
                    pass
    except OSError:
        pass
    return all_items


def enqueue_file(filepath: str) -> bool:
    """
    Add filepath to the correct DB queue.
    Returns True if newly enqueued, False if already present.
    Thread-safe via a per-DB lock file.
    """
    filepath = os.path.abspath(filepath)
    db_name  = db_name_from_path(filepath)
    lock     = _lock_path(db_name)

    # Simple file-based mutex (works cross-process / cross-thread)
    timeout = 10  # seconds
    waited  = 0
    while os.path.exists(lock):
        time.sleep(0.2)
        waited += 0.2
        if waited >= timeout:
            logger.error(f"Lock timeout for DB {db_name}, skipping {filepath}")
            return False

    try:
        # Acquire lock
        with open(lock, "w") as lf:
            lf.write(str(os.getpid()))

        # ── Cross-queue dedup ─────────────────────────────────────────
        # Check ALL queue files, not just this db_name's queue.
        # Prevents a duplicate entry when the upload endpoint and the
        # periodic watcher scan derive different db_name keys (e.g.
        # a dbname_override vs filename-inferred name) and therefore
        # read/write different queue_<DBNAME>.json files.
        fhash    = file_hash(filepath)
        all_items = _all_queue_items()
        for item in all_items:
            if item.get("filepath") == filepath:
                logger.info(f"⏭  Already queued (path match): {filepath}")
                return False
            if item.get("file_hash") == fhash and item.get("status") != "FAILED":
                logger.info(f"⏭  Already queued (hash match across queues): {filepath}")
                return False

        # ── Enqueue into this db's queue ──────────────────────────────
        items = _load_queue(db_name)
        entry = {
            "filepath":   filepath,
            "db_name":    db_name,
            "file_hash":  fhash,
            "status":     "PENDING",
            "added_at":   datetime.now(timezone.utc).isoformat(),
            "processed_at": None,
            "error":      None,
            "retry_count": 0,
        }
        items.append(entry)
        _save_queue(db_name, items)
        logger.info(f"✅ Enqueued [{db_name}]: {os.path.basename(filepath)}")
        return True

    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


# ── watchdog event handler ─────────────────────────────────────────────
class AwrEventHandler(FileSystemEventHandler):
    """
    Fires on file creation (or move-into) inside the watched folder tree.
    Each qualifying file is immediately enqueued — no debounce timer,
    so no files are dropped in a batch drop scenario.
    """

    def _handle(self, path: str) -> None:
        ext = Path(path).suffix.lower()
        if ext not in AWR_EXTENSIONS:
            return
        # Brief wait to let the file finish writing / copying
        for _ in range(10):
            try:
                size_a = os.path.getsize(path)
                time.sleep(0.5)
                size_b = os.path.getsize(path)
                if size_a == size_b and size_a > 0:
                    break
            except OSError:
                time.sleep(0.5)
        enqueue_file(path)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        # Handles files copied/moved INTO the watch folder
        if not event.is_directory:
            self._handle(event.dest_path)


# ── scan existing files (startup + --once mode) ────────────────────────
def scan_existing_files() -> int:
    """
    Walk WATCH_ROOT and enqueue any AWR file not already in a queue.
    Returns count of newly enqueued files.
    """
    enqueued = 0
    for root, _dirs, files in os.walk(WATCH_ROOT):
        for fname in files:
            if Path(fname).suffix.lower() in AWR_EXTENSIONS:
                fpath = os.path.join(root, fname)
                if enqueue_file(fpath):
                    enqueued += 1
    return enqueued


# ── main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AWR Report Watcher v2")
    parser.add_argument(
        "--once", action="store_true",
        help="Scan existing files once and exit (for cron / scheduled task use)"
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("AWR Watcher v2 starting")
    logger.info(f"Watch root : {WATCH_ROOT}")
    logger.info(f"Queues dir : {QUEUES_DIR}")
    logger.info(f"Extensions : {AWR_EXTENSIONS}")

    # Always scan existing files on startup to pick up anything dropped
    # while the watcher was not running.
    n = scan_existing_files()
    if n:
        logger.info(f"📂 {n} existing file(s) enqueued on startup scan")
    else:
        logger.info("📂 No new files found on startup scan")

    if args.once:
        logger.info("--once mode: exiting after startup scan")
        return

    # Start watchdog observer for real-time detection
    event_handler = AwrEventHandler()
    observer = Observer()
    # recursive=True catches per-DB sub-folders
    observer.schedule(event_handler, WATCH_ROOT, recursive=True)
    observer.start()
    logger.info(f"👀 Watching for new AWR files (poll interval hint: {POLL_INTERVAL}s) ...")

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            # Periodic re-scan catches files copied without triggering fs events
            # (e.g. network drives, some CIFS mounts)
            scan_existing_files()
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user (KeyboardInterrupt)")
    finally:
        observer.stop()
        observer.join()
        logger.info("AWR Watcher v2 shut down cleanly")


if __name__ == "__main__":
    main()
