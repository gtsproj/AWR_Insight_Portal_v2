# queue_processor.py
# ============================================================
# Unified Queue Processor — handles both AWR and SAR queues
#
# Design:
#   - AWR queues : queues\queue_<DBNAME>.json
#   - SAR queues : sar_queues\queue_<HOSTNAME>.json
#   - Each DB/host gets its own worker thread → parallel across servers
#   - Files for the same DB/host processed sequentially (FIFO)
#   - Per-queue .lock file prevents duplicate processing
#   - Failed items retried up to MAX_RETRIES, then PERMANENTLY FAILED
#
# USAGE:
#   python queue_processor.py                   # process all queues once
#   python queue_processor.py --daemon          # run continuously
#   python queue_processor.py --type awr        # AWR queues only
#   python queue_processor.py --type sar        # SAR queues only
#   python queue_processor.py --db COLDBPRD     # one DB only
#   python queue_processor.py --workers 4       # N parallel workers
#   python queue_processor.py --status          # print status report
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
from logger_utils  import get_logger

logger = get_logger("queue_processor")

# ── config ─────────────────────────────────────────────────────────────
_cfg          = load_config()
_paths        = _cfg.get("paths", {})
_queue_cfg    = _cfg.get("queue", {})

AWR_QUEUES_DIR  = os.path.abspath(_paths.get("queues_directory",
                     os.path.join(_PROJECT_ROOT, "queues")))
SAR_QUEUES_DIR  = os.path.abspath(_paths.get("sar_queues_directory",
                     os.path.join(_PROJECT_ROOT, "sar_queues")))
MASTER_PARSER   = os.path.join(_PROJECT_ROOT, "master_parser.py")
SAR_PARSER      = os.path.join(_PROJECT_ROOT, "modules", "sar", "sar_master_parser.py")
MAX_RETRIES     = int(_queue_cfg.get("max_retries", 3))
POLL_INTERVAL   = int(_queue_cfg.get("poll_interval_seconds", 15))
ARCHIVE_ON_SUCCESS = _queue_cfg.get("archive_on_success", True)

_shutdown = threading.Event()


# ── signal handling ────────────────────────────────────────────────────
def _handle_signal(signum, frame):
    logger.info(f"Signal {signum} — shutting down after current jobs complete")
    _shutdown.set()

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── queue I/O ─────────────────────────────────────────────────────────
def _queue_path(queues_dir: str, name: str) -> str:
    return os.path.join(queues_dir, f"queue_{name}.json")


def _lock_path(queues_dir: str, name: str) -> str:
    return _queue_path(queues_dir, name) + ".lock"


def _load_queue(queues_dir: str, name: str) -> list:
    path = _queue_path(queues_dir, name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Failed to load queue {name}: {e}")
        return []


def _save_queue(queues_dir: str, name: str, items: list) -> None:
    os.makedirs(queues_dir, exist_ok=True)
    path = _queue_path(queues_dir, name)
    tmp  = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, default=str)
    os.replace(tmp, path)


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _acquire_lock(queues_dir: str, name: str, timeout: float = 30.0) -> bool:
    lpath  = _lock_path(queues_dir, name)
    waited = 0.0
    while os.path.exists(lpath):
        try:
            with open(lpath) as lf:
                pid = int(lf.read().strip())
            if not _pid_running(pid):
                logger.warning(f"Removing stale lock for {name} (PID {pid})")
                os.remove(lpath)
                break
        except (OSError, ValueError):
            break
        time.sleep(0.5)
        waited += 0.5
        if waited >= timeout:
            logger.error(f"Lock timeout for {name}")
            return False
    try:
        with open(lpath, "w") as lf:
            lf.write(str(os.getpid()))
        return True
    except OSError as e:
        logger.error(f"Cannot create lock for {name}: {e}")
        return False


def _release_lock(queues_dir: str, name: str) -> None:
    try:
        os.remove(_lock_path(queues_dir, name))
    except OSError:
        pass


# ── discover queues ────────────────────────────────────────────────────
def _discover_queues(queues_dir: str) -> list:
    if not os.path.isdir(queues_dir):
        return []
    names = []
    for fname in sorted(os.listdir(queues_dir)):
        if fname.startswith("queue_") and fname.endswith(".json"):
            names.append(fname[len("queue_"):-len(".json")])
    return names


# ── process one item ───────────────────────────────────────────────────
def _process_next(queues_dir: str, name: str, parser_script: str,
                  queue_type: str) -> bool:
    """
    Pick next PENDING item in FIFO order (queued_at timestamp),
    run parser, update status. Returns True if an item was processed.
    """
    if not _acquire_lock(queues_dir, name):
        return False

    try:
        items = _load_queue(queues_dir, name)

        # Collect eligible items with their original index
        eligible = [
            (i, item) for i, item in enumerate(items)
            if item.get("status") == "PENDING"
            or (item.get("status") == "FAILED"
                and item.get("retry_count", 0) < MAX_RETRIES)
        ]

        if not eligible:
            return False

        # Sort eligible items by queued_at (FIFO) — None sorts last
        eligible.sort(key=lambda x: (
            x[1].get("queued_at") or "9999",
        ))

        target_idx, item = eligible[0]
        filepath = item["filepath"]
        fname    = os.path.basename(filepath)

        logger.info(f"[{queue_type}/{name}] Processing: {fname} "
                    f"(attempt {item.get('retry_count',0)+1}/{MAX_RETRIES+1})")

        items[target_idx]["status"]        = "PROCESSING"
        items[target_idx]["processing_at"] = datetime.now().isoformat()
        _save_queue(queues_dir, name, items)

    finally:
        _release_lock(queues_dir, name)

    # ── Build parser command ──────────────────────────────────────────
    if queue_type == "SAR":
        hostname = item.get("hostname", name)
        cmd = [sys.executable, parser_script,
               "--file", filepath,
               "--host", hostname]
    else:
        archive_flag = ["--archive"] if ARCHIVE_ON_SUCCESS else []
        cmd = [sys.executable, parser_script, filepath] + archive_flag

    start = time.perf_counter()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        elapsed = time.perf_counter() - start
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start
        success = False
        result  = type("R", (), {"returncode": -1,
                                  "stderr": "TimeoutExpired after 3600s",
                                  "stdout": ""})()
        logger.error(f"[{queue_type}/{name}] TIMEOUT after {elapsed:.0f}s: {fname}")
    except Exception as e:
        elapsed = time.perf_counter() - start
        success = False
        result  = type("R", (), {"returncode": -2,
                                  "stderr": str(e), "stdout": ""})()
        logger.error(f"[{queue_type}/{name}] Exception: {e}", exc_info=True)

    # ── Update queue status ───────────────────────────────────────────
    if not _acquire_lock(queues_dir, name):
        logger.error(f"Cannot re-acquire lock to update {fname}")
        return False

    try:
        items = _load_queue(queues_dir, name)
        for i, item in enumerate(items):
            if item.get("filepath") == filepath:
                target_idx = i
                break

        if success:
            items[target_idx]["status"]       = "DONE"
            items[target_idx]["processed_at"] = datetime.now().isoformat()
            items[target_idx]["error"]        = None
            logger.info(f"[{queue_type}/{name}] ✅ DONE in {elapsed:.1f}s: {fname}")
        else:
            items[target_idx]["retry_count"] = items[target_idx].get("retry_count", 0) + 1
            items[target_idx]["error"]       = (result.stderr or "")[-500:]
            retries_left = MAX_RETRIES - items[target_idx]["retry_count"]
            items[target_idx]["status"] = "FAILED"
            err_preview = (result.stderr or result.stdout or "no output")[:300]
            if retries_left > 0:
                logger.warning(
                    f"[{queue_type}/{name}] ⚠ FAILED ({retries_left} retries left): {fname}\n"
                    f"  Error: {err_preview}"
                )
            else:
                logger.error(
                    f"[{queue_type}/{name}] ❌ PERMANENTLY FAILED: {fname}\n"
                    f"  Error: {err_preview}"
                )

        _save_queue(queues_dir, name, items)

    finally:
        _release_lock(queues_dir, name)

    return True


# ── drain one queue ────────────────────────────────────────────────────
def _drain(queues_dir: str, name: str, parser_script: str,
           queue_type: str) -> int:
    """
    Process all PENDING items for one DB/host then exit.
    The daemon will restart this worker on the next poll cycle if needed.
    This ensures the daemon can always discover and start workers for new DBs.
    """
    count      = 0
    empty_runs = 0

    while not _shutdown.is_set():
        processed = _process_next(queues_dir, name, parser_script, queue_type)
        if processed:
            count      += 1
            empty_runs  = 0
        else:
            empty_runs += 1
            # Exit after 3 consecutive empty checks (15s total) so daemon
            # can restart worker and also pick up any new DB queues
            if empty_runs >= 3:
                logger.debug(f"[{queue_type}/{name}] Queue empty — exiting worker (daemon will restart)")
                break
            _shutdown.wait(5)

    return count


# ── status report ──────────────────────────────────────────────────────
def print_status_report() -> None:
    header = f"\n{'Type':<6} {'Name':<22} {'PENDING':>8} {'PROCESSING':>12} {'DONE':>6} {'FAILED':>8} {'TOTAL':>6}"
    print(header)
    print("-" * 70)

    for qtype, qdir in [("AWR", AWR_QUEUES_DIR), ("SAR", SAR_QUEUES_DIR)]:
        names = _discover_queues(qdir)
        for name in names:
            items  = _load_queue(qdir, name)
            counts = {"PENDING": 0, "PROCESSING": 0, "DONE": 0, "FAILED": 0}
            for item in items:
                s = item.get("status", "PENDING")
                counts[s] = counts.get(s, 0) + 1
            print(f"{qtype:<6} {name:<22} {counts['PENDING']:>8} "
                  f"{counts['PROCESSING']:>12} {counts['DONE']:>6} "
                  f"{counts['FAILED']:>8} {len(items):>6}")
    print()


# ── main ───────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="AWR + SAR Queue Processor")
    p.add_argument("--daemon",      action="store_true",
                   help="Run continuously, polling for new items")
    p.add_argument("--type",        choices=["awr", "sar", "all"], default="all",
                   help="Which queue type to process (default: all)")
    p.add_argument("--db",          default=None,
                   help="Process only this DB/hostname")
    p.add_argument("--workers",     type=int, default=4,
                   help="Max parallel AWR workers (default: 4)")
    p.add_argument("--sar-workers", type=int, default=2,
                   help="Max parallel SAR workers (default: 2)")
    p.add_argument("--status",      action="store_true",
                   help="Print queue status and exit")
    args = p.parse_args()

    os.makedirs(AWR_QUEUES_DIR, exist_ok=True)
    os.makedirs(SAR_QUEUES_DIR, exist_ok=True)

    if args.status:
        print_status_report()
        return

    awr_workers = args.workers
    sar_workers = args.sar_workers

    logger.info("=" * 60)
    logger.info(f"Queue Processor starting | type={args.type} | "
                f"AWR workers={awr_workers} | SAR workers={sar_workers} | daemon={args.daemon}")
    logger.info(f"AWR queues : {AWR_QUEUES_DIR}")
    logger.info(f"SAR queues : {SAR_QUEUES_DIR}")

    def build_awr_work():
        if args.db:
            return [(AWR_QUEUES_DIR, args.db.upper(), MASTER_PARSER, "AWR")]
        return [(AWR_QUEUES_DIR, name, MASTER_PARSER, "AWR")
                for name in _discover_queues(AWR_QUEUES_DIR)]

    def build_sar_work():
        if args.db:
            return [(SAR_QUEUES_DIR, args.db.upper(), SAR_PARSER, "SAR")]
        return [(SAR_QUEUES_DIR, name, SAR_PARSER, "SAR")
                for name in _discover_queues(SAR_QUEUES_DIR)]

    def run_once():
        work = []
        if args.type in ("awr", "all"):
            work.extend(build_awr_work())
        if args.type in ("sar", "all"):
            work.extend(build_sar_work())
        if not work:
            logger.debug("No queues found — nothing to process")
            return
        total_workers = min(awr_workers + sar_workers, len(work))
        logger.info(f"Processing {len(work)} queue(s) with up to {total_workers} parallel worker(s)")
        with ThreadPoolExecutor(max_workers=total_workers) as pool:
            futures = {
                pool.submit(_drain, qdir, name, parser, qtype): f"{qtype}/{name}"
                for qdir, name, parser, qtype in work
            }
            for future in as_completed(futures):
                label = futures[future]
                try:
                    n = future.result()
                    if n:
                        logger.info(f"[{label}] Drained {n} item(s)")
                except Exception as e:
                    logger.error(f"[{label}] Worker error: {e}", exc_info=True)

    def run_daemon():
        """
        Daemon mode with SEPARATE AWR and SAR thread pools.
        AWR DBs get up to --workers parallel threads.
        SAR hosts get up to --sar-workers parallel threads.
        Both pools poll independently every POLL_INTERVAL seconds.
        Workers exit when queue is empty and are restarted on next poll.
        """
        awr_active: dict = {}   # label -> Future
        sar_active: dict = {}   # label -> Future

        logger.info(f"Daemon starting — AWR pool={awr_workers} | SAR pool={sar_workers}")

        with ThreadPoolExecutor(max_workers=awr_workers) as awr_pool, \
             ThreadPoolExecutor(max_workers=sar_workers) as sar_pool:

            while not _shutdown.is_set():
                try:
                    # ── AWR workers ───────────────────────────────────
                    if args.type in ("awr", "all"):
                        for qdir, name, parser, qtype in build_awr_work():
                            label = f"AWR/{name}"
                            future = awr_active.get(label)
                            if future is None or future.done():
                                if future is not None and future.done():
                                    try:
                                        n = future.result()
                                        if n:
                                            logger.info(f"[{label}] Processed {n} item(s)")
                                    except Exception as e:
                                        logger.error(f"[{label}] Error: {e}", exc_info=True)
                                    del awr_active[label]
                                awr_active[label] = awr_pool.submit(
                                    _drain, qdir, name, parser, qtype)
                                logger.info(
                                    f"[{label}] Worker started — "
                                    f"AWR active: {sum(1 for f in awr_active.values() if not f.done())}"
                                )

                    # ── SAR workers ───────────────────────────────────
                    if args.type in ("sar", "all"):
                        for qdir, name, parser, qtype in build_sar_work():
                            label = f"SAR/{name}"
                            future = sar_active.get(label)
                            if future is None or future.done():
                                if future is not None and future.done():
                                    try:
                                        n = future.result()
                                        if n:
                                            logger.info(f"[{label}] Processed {n} item(s)")
                                    except Exception as e:
                                        logger.error(f"[{label}] Error: {e}", exc_info=True)
                                    del sar_active[label]
                                sar_active[label] = sar_pool.submit(
                                    _drain, qdir, name, parser, qtype)
                                logger.info(
                                    f"[{label}] Worker started — "
                                    f"SAR active: {sum(1 for f in sar_active.values() if not f.done())}"
                                )

                    awr_running = [l for l, f in awr_active.items() if not f.done()]
                    sar_running = [l for l, f in sar_active.items() if not f.done()]
                    if awr_running or sar_running:
                        logger.debug(f"Active — AWR: {awr_running} | SAR: {sar_running}")

                except Exception as e:
                    logger.error(f"Daemon poll error: {e}", exc_info=True)

                _shutdown.wait(POLL_INTERVAL)

        logger.info("Queue processor daemon shut down cleanly")

    if args.daemon:
        logger.info(f"Daemon mode — AWR workers={awr_workers} SAR workers={sar_workers} "
                    f"poll={POLL_INTERVAL}s")
        run_daemon()
    else:
        run_once()
        logger.info("Queue processor run complete")


if __name__ == "__main__":
    main()

