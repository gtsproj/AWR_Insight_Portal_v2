# queue_processor.py
# ============================================================
# AWR Queue Processor — reads per-DB queue files and processes
# each pending AWR report by calling master_parser.py.
#
# Design:
#   - One queue file per DB: queues/queue_<DBNAME>.json
#   - Each queue item has status: PENDING | PROCESSING | DONE | FAILED
#   - Items processed in FIFO order, one at a time per DB
#   - Per-DB .lock file prevents two processor instances from
#     consuming the same queue simultaneously
#   - Multiple DBs can be processed in parallel (--workers N)
#   - Failed items are retried up to MAX_RETRIES times, then
#     marked FAILED permanently with error message stored
#   - Designed to run as a daemon (--daemon) or one-shot (default)
#
# USAGE:
#   python queue_processor.py                  # process all queues once
#   python queue_processor.py --daemon         # run continuously
#   python queue_processor.py --db COLDBPRD    # process one DB only
#   python queue_processor.py --workers 4      # parallel DB processing
#   python queue_processor.py --status         # print queue status report
# ============================================================

import os
import sys
import json
import time
import signal
import argparse
import subprocess
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from config_loader import load_config
from logger_utils import get_logger

logger = get_logger("queue_processor")

# ── config ─────────────────────────────────────────────────────────────
_cfg         = load_config()
_paths       = _cfg.get("paths", {})
_queue_cfg   = _cfg.get("queue", {})

QUEUES_DIR   = os.path.abspath(_paths.get("queues_directory",
                   os.path.join(_PROJECT_ROOT, "queues")))
MASTER_PARSER = os.path.join(_PROJECT_ROOT, "master_parser.py")
MAX_RETRIES   = int(_queue_cfg.get("max_retries", 3))
POLL_INTERVAL = int(_queue_cfg.get("poll_interval_seconds", 15))
ARCHIVE_ON_SUCCESS = _queue_cfg.get("archive_on_success", True)

_shutdown = threading.Event()


# ── signal handling ────────────────────────────────────────────────────
def _handle_signal(signum, frame):
    logger.info(f"Signal {signum} received — shutting down after current jobs complete")
    _shutdown.set()

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── queue I/O helpers ──────────────────────────────────────────────────
def queue_path(db_name: str) -> str:
    return os.path.join(QUEUES_DIR, f"queue_{db_name}.json")


def lock_path(db_name: str) -> str:
    return queue_path(db_name) + ".lock"


def _load_queue(db_name: str) -> list:
    path = queue_path(db_name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load queue for {db_name}: {e}")
        return []


def _save_queue(db_name: str, items: list) -> None:
    path = queue_path(db_name)
    tmp  = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, default=str)
    os.replace(tmp, path)


def _acquire_lock(db_name: str, timeout: float = 30.0) -> bool:
    """Try to acquire the per-DB lock file. Returns True if acquired."""
    lpath  = lock_path(db_name)
    waited = 0.0
    while os.path.exists(lpath):
        # Check for stale lock (process no longer running)
        try:
            with open(lpath) as lf:
                pid = int(lf.read().strip())
            if not _pid_running(pid):
                logger.warning(f"Removing stale lock for {db_name} (PID {pid} not running)")
                os.remove(lpath)
                break
        except (OSError, ValueError):
            break
        time.sleep(0.5)
        waited += 0.5
        if waited >= timeout:
            logger.error(f"Lock timeout for {db_name} after {timeout}s")
            return False
    try:
        with open(lpath, "w") as lf:
            lf.write(str(os.getpid()))
        return True
    except OSError as e:
        logger.error(f"Cannot create lock for {db_name}: {e}")
        return False


def _release_lock(db_name: str) -> None:
    try:
        os.remove(lock_path(db_name))
    except OSError:
        pass


def _pid_running(pid: int) -> bool:
    """Check if a process with given PID is running (cross-platform)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ── discover all DB queues ─────────────────────────────────────────────
def discover_db_queues() -> list:
    """Return list of DB names that have a queue file in QUEUES_DIR."""
    if not os.path.isdir(QUEUES_DIR):
        return []
    dbs = []
    for fname in os.listdir(QUEUES_DIR):
        if fname.startswith("queue_") and fname.endswith(".json"):
            db_name = fname[len("queue_"):-len(".json")]
            dbs.append(db_name)
    return sorted(dbs)


# ── process one pending item from a DB queue ───────────────────────────
def process_next_item(db_name: str) -> bool:
    """
    Pick the next PENDING item from the queue, run master_parser,
    and update its status.  Returns True if an item was processed.
    """
    if not _acquire_lock(db_name):
        return False

    try:
        items = _load_queue(db_name)

        # Find first PENDING item eligible for processing
        target_idx = None
        for i, item in enumerate(items):
            status      = item.get("status", "PENDING")
            retry_count = item.get("retry_count", 0)
            if status == "PENDING" or (status == "FAILED" and retry_count < MAX_RETRIES):
                target_idx = i
                break

        if target_idx is None:
            return False   # Nothing to process for this DB

        item = items[target_idx]
        filepath = item["filepath"]
        fname    = os.path.basename(filepath)

        logger.info(f"[{db_name}] Processing: {fname} "
                    f"(attempt {item.get('retry_count', 0) + 1}/{MAX_RETRIES + 1})")

        # Mark as PROCESSING
        items[target_idx]["status"]       = "PROCESSING"
        items[target_idx]["processing_at"] = datetime.now(timezone.utc).isoformat()
        _save_queue(db_name, items)

    finally:
        _release_lock(db_name)

    # ── Run master_parser outside the lock so other DBs aren't blocked ──
    archive_flag = ["--archive"] if ARCHIVE_ON_SUCCESS else []
    cmd = [sys.executable, MASTER_PARSER, filepath] + archive_flag

    start_time = time.perf_counter()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,   # 1-hour hard timeout per file
        )
        elapsed = time.perf_counter() - start_time
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start_time
        success = False
        result  = type("R", (), {"returncode": -1,
                                  "stderr": "TimeoutExpired after 3600s",
                                  "stdout": ""})()
        logger.error(f"[{db_name}] TIMEOUT after {elapsed:.0f}s: {fname}")
    except Exception as e:
        elapsed = time.perf_counter() - start_time
        success = False
        result  = type("R", (), {"returncode": -2, "stderr": str(e), "stdout": ""})()
        logger.error(f"[{db_name}] Exception running master_parser: {e}", exc_info=True)

    # ── Update queue item status ────────────────────────────────────────
    if not _acquire_lock(db_name):
        logger.error(f"[{db_name}] Cannot re-acquire lock to update status for {fname}")
        return False

    try:
        items = _load_queue(db_name)
        # Re-find the item (index may have shifted if watcher added items)
        for i, item in enumerate(items):
            if item.get("filepath") == filepath:
                target_idx = i
                break

        if success:
            items[target_idx]["status"]       = "DONE"
            items[target_idx]["processed_at"] = datetime.now(timezone.utc).isoformat()
            items[target_idx]["error"]        = None
            logger.info(f"[{db_name}] ✅ DONE in {elapsed:.1f}s: {fname}")
        else:
            items[target_idx]["retry_count"]  = items[target_idx].get("retry_count", 0) + 1
            items[target_idx]["error"]        = (result.stderr or "")[-500:]
            retries_left = MAX_RETRIES - items[target_idx]["retry_count"]

            if retries_left > 0:
                items[target_idx]["status"] = "FAILED"
                logger.warning(f"[{db_name}] ⚠  FAILED ({retries_left} retries left): {fname}")
            else:
                items[target_idx]["status"] = "FAILED"
                logger.error(f"[{db_name}] ❌ PERMANENTLY FAILED: {fname} — {result.stderr[:200]}")

        _save_queue(db_name, items)

    finally:
        _release_lock(db_name)

    return True


# ── process all pending items for one DB ──────────────────────────────
def drain_queue(db_name: str) -> int:
    """Process all pending items for db_name. Returns count processed."""
    count = 0
    while not _shutdown.is_set():
        processed = process_next_item(db_name)
        if not processed:
            break
        count += 1
    return count


# ── queue status report ────────────────────────────────────────────────
def print_status_report() -> None:
    dbs = discover_db_queues()
    if not dbs:
        print("No queue files found.")
        return

    print(f"\n{'DB Name':<20} {'PENDING':>8} {'PROCESSING':>12} {'DONE':>6} {'FAILED':>8} {'TOTAL':>6}")
    print("-" * 65)
    for db_name in dbs:
        items = _load_queue(db_name)
        counts = {"PENDING": 0, "PROCESSING": 0, "DONE": 0, "FAILED": 0}
        for item in items:
            s = item.get("status", "PENDING")
            counts[s] = counts.get(s, 0) + 1
        print(f"{db_name:<20} {counts['PENDING']:>8} {counts['PROCESSING']:>12} "
              f"{counts['DONE']:>6} {counts['FAILED']:>8} {len(items):>6}")
    print()


# ── main ───────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="AWR Queue Processor v2")
    p.add_argument("--daemon",   action="store_true",
                   help="Run continuously, polling for new queue items")
    p.add_argument("--db",       default=None,
                   help="Process only this DB name (e.g. COLDBPRD)")
    p.add_argument("--workers",  type=int, default=1,
                   help="Number of parallel DB workers (default: 1)")
    p.add_argument("--status",   action="store_true",
                   help="Print queue status report and exit")
    args = p.parse_args()

    os.makedirs(QUEUES_DIR, exist_ok=True)

    if args.status:
        print_status_report()
        return

    logger.info("=" * 60)
    logger.info(f"AWR Queue Processor starting | workers={args.workers} | daemon={args.daemon}")
    logger.info(f"Queues dir   : {QUEUES_DIR}")
    logger.info(f"max_retries  : {MAX_RETRIES}")
    logger.info(f"archive      : {ARCHIVE_ON_SUCCESS}")

    def run_once():
        dbs = [args.db] if args.db else discover_db_queues()
        if not dbs:
            logger.info("No queues found — nothing to process")
            return

        if args.workers == 1:
            for db_name in dbs:
                if _shutdown.is_set():
                    break
                n = drain_queue(db_name)
                if n:
                    logger.info(f"[{db_name}] Drained {n} item(s)")
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(drain_queue, db): db for db in dbs}
                for future in as_completed(futures):
                    db  = futures[future]
                    try:
                        n = future.result()
                        if n:
                            logger.info(f"[{db}] Drained {n} item(s)")
                    except Exception as e:
                        logger.error(f"[{db}] Worker error: {e}", exc_info=True)

    if args.daemon:
        logger.info(f"Daemon mode — polling every {POLL_INTERVAL}s")
        while not _shutdown.is_set():
            run_once()
            _shutdown.wait(POLL_INTERVAL)
        logger.info("Queue processor shut down cleanly")
    else:
        run_once()
        logger.info("Queue processor run complete")


if __name__ == "__main__":
    main()
