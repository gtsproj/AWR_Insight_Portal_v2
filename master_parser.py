# master_parser.py  (v3)
# ============================================================
# Master AWR Parser — orchestrates all AWR section modules
# for a single AWR report file.
#
# v3 changes vs v2:
#   - Auto-triggers recommendation_engine after MV refresh
#   - Auto-triggers anomaly_detector after MV refresh
#   - Both are best-effort (failure does not abort the parse)
# ============================================================

import os
import sys
import glob
import time
import importlib.util
from datetime import datetime

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from logger_utils import get_logger
from db import get_db_connection

logger = get_logger("master_parser")

# ── paths ──────────────────────────────────────────────────────────────
MODULES_DIR = os.path.join(_PROJECT_ROOT, "modules")
AWR_DIR     = os.path.join(_PROJECT_ROOT, "awr_reports")
ARCHIVE_DIR = os.path.join(_PROJECT_ROOT, "archive")

# ── SAR modules excluded from AWR run ─────────────────────────────────
_SAR_MODULES = {
    "sar_cpu_parser", "sar_memory_parser", "sar_swap_parser",
    "sar_disk_parser", "sar_master_parser",
    "sar_network_parser", "sar_paging_parser",
    "sar_ctxswitch_parser", "sar_loadavg_parser",
    "sar_hugepage_parser","sar_socket_parser",
    "plan_parser",
}

# ── materialized views to refresh after each file ─────────────────────
_MV_NAMES = [
    "public.awr_sql_summary_mv",
    "public.awr_segment_summary_mv",
    "public.awr_fg_wait_summary_mv",
    "public.awr_fg_wait_event_summary_mv",
    "public.awr_bg_wait_summary_mv",
    "public.awr_bg_wait_event_summary_mv",
    "public.awr_wait_summary_mv",
]


# ── module loader ──────────────────────────────────────────────────────
def _load_module(script_path: str):
    module_name = os.path.basename(script_path).replace(".py", "")
    spec   = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_module(module, module_name: str, filepath: str) -> bool:
    if hasattr(module, "main"):
        module.main(filepath)
        return True

    if hasattr(module, "parse") and hasattr(module, "insert"):
        records = module.parse(filepath)
        module.insert(records)
        return True

    logger.debug(f"Module {module_name} has no main() or parse()/insert() — using exec fallback")
    try:
        spec = importlib.util.find_spec(module_name)
        origin = spec.origin if spec else None
    except Exception:
        origin = None

    file_origin = getattr(module, "__file__", None) or origin
    if file_origin and os.path.isfile(file_origin):
        with open(file_origin, encoding="utf-8") as f:
            code = compile(f.read(), file_origin, "exec")
        exec(code, {"__file__": file_origin, "__name__": "__main__", "filepath": filepath})
        return True

    logger.error(f"Cannot invoke module {module_name} — no supported interface found")
    return False


# ── MV refresh ─────────────────────────────────────────────────────────
def _refresh_materialized_views():
    logger.info("🔁 Refreshing materialized views …")
    try:
        conn = get_db_connection()
        conn.autocommit = True
        with conn.cursor() as cur:
            for mv in _MV_NAMES:
                mv = mv.strip()
                if not mv:
                    continue
                try:
                    cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv};")
                    logger.info(f"  ✅ {mv} (CONCURRENT)")
                except Exception as e1:
                    logger.warning(f"  ⚠ {mv} CONCURRENT failed ({e1}) — retrying non-concurrent")
                    try:
                        cur.execute(f"REFRESH MATERIALIZED VIEW {mv};")
                        logger.info(f"  ✅ {mv} (non-concurrent)")
                    except Exception as e2:
                        logger.error(f"  ❌ Failed to refresh {mv}: {e2}")
        conn.close()
        logger.info("🎉 MV refresh complete")
    except Exception as e:
        logger.error(f"❌ MV refresh aborted: {e}", exc_info=True)


# ── snap range lookup ───────────────────────────────────────────────────
def _get_snap_range(db_name: str, filepath: str):
    """
    Return (begin_snap, end_snap) for the just-parsed file.
    Queries awr_load_profile for the most recent two snaps for this DB
    whose snap_time was recorded in the last 10 minutes (i.e. just inserted).
    Falls back to the global min/max for this DB if nothing recent found.
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Most recently inserted snap pair for this DB
            cur.execute("""
                SELECT MIN(begin_snap), MAX(begin_snap)
                FROM awr_load_profile
                WHERE dbname = %s
                  AND snap_time >= NOW() - INTERVAL '10 minutes'
            """, (db_name,))
            row = cur.fetchone()
            if row and row[0] is not None:
                return int(row[0]), int(row[1])

            # Fallback: last two distinct snaps for this DB
            cur.execute("""
                SELECT begin_snap FROM awr_load_profile
                WHERE dbname = %s
                ORDER BY begin_snap DESC LIMIT 2
            """, (db_name,))
            rows = cur.fetchall()
            if rows:
                snaps = sorted([r[0] for r in rows])
                return int(snaps[0]), int(snaps[-1])
        conn.close()
    except Exception as e:
        logger.warning(f"Could not resolve snap range for {db_name}: {e}")
    return None, None


# ── auto recommendation engine ──────────────────────────────────────────
def _run_recommendations(db_name: str, begin_snap: int, end_snap: int):
    """
    Auto-trigger recommendation_engine for the just-parsed snap range.
    Best-effort — failure is logged but does not abort the parse.
    """
    logger.info(f"🤖 Generating recommendations: {db_name} snaps {begin_snap}–{end_snap}")
    try:
        rec_path = os.path.join(_PROJECT_ROOT, "recommendation_engine.py")
        if not os.path.exists(rec_path):
            logger.warning("recommendation_engine.py not found — skipping")
            return

        spec   = importlib.util.spec_from_file_location("recommendation_engine", rec_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Prefer a callable run() / generate() / main() interface
        if hasattr(module, "run"):
            module.run(db_name=db_name, start_snap=begin_snap,
                       end_snap=end_snap, store=True)
        elif hasattr(module, "generate_recommendations"):
            module.generate_recommendations(db_name=db_name,
                                             start_snap=begin_snap,
                                             end_snap=end_snap, store=True)
        elif hasattr(module, "main"):
            # Patch sys.argv so argparse inside main() picks up the right args
            _orig_argv = sys.argv[:]
            sys.argv = [
                "recommendation_engine.py",
                "--db",    db_name,
                "--start", str(begin_snap),
                "--end",   str(end_snap),
                "--store",
            ]
            try:
                module.main()
            finally:
                sys.argv = _orig_argv
        else:
            logger.warning("recommendation_engine has no recognised entry point (run/generate_recommendations/main)")
            return

        logger.info(f"  ✅ Recommendations stored for {db_name} snaps {begin_snap}–{end_snap}")

    except SystemExit:
        # argparse / sys.exit(0) inside the engine — not a real error
        logger.info(f"  ✅ Recommendations complete for {db_name} snaps {begin_snap}–{end_snap}")
    except Exception as e:
        logger.error(f"  ❌ Recommendation engine failed for {db_name}: {e}", exc_info=True)


# ── auto anomaly detection ──────────────────────────────────────────────
def _run_anomaly_detection(db_name: str, begin_snap: int, end_snap: int):
    """
    Auto-trigger anomaly_detector for every snap in the parsed range.
    Best-effort — failure is logged but does not abort the parse.
    """
    logger.info(f"🔍 Running anomaly detection: {db_name} snaps {begin_snap}–{end_snap}")
    try:
        det_path = os.path.join(_PROJECT_ROOT, "anomaly_detector.py")
        if not os.path.exists(det_path):
            logger.warning("anomaly_detector.py not found — skipping")
            return

        spec   = importlib.util.spec_from_file_location("anomaly_detector", det_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        snaps_to_check = list(range(begin_snap, end_snap + 1))

        for snap in snaps_to_check:
            try:
                if hasattr(module, "detect"):
                    module.detect(db_name=db_name, snap=snap, store=True)
                elif hasattr(module, "run"):
                    module.run(db_name=db_name, snap=snap, store=True)
                elif hasattr(module, "main"):
                    _orig_argv = sys.argv[:]
                    sys.argv = [
                        "anomaly_detector.py",
                        "--db",    db_name,
                        "--snap",  str(snap),
                        "--store",
                    ]
                    try:
                        module.main()
                    finally:
                        sys.argv = _orig_argv
                else:
                    logger.warning("anomaly_detector has no recognised entry point (detect/run/main)")
                    break
                logger.debug(f"  ✅ Anomaly detection done for snap {snap}")
            except SystemExit:
                logger.debug(f"  ✅ Anomaly detection done for snap {snap}")
            except Exception as e:
                logger.warning(f"  ⚠ Anomaly detection failed for snap {snap}: {e}")

        logger.info(f"  ✅ Anomaly detection complete for {db_name} snaps {begin_snap}–{end_snap}")

    except Exception as e:
        logger.error(f"  ❌ Anomaly detector failed for {db_name}: {e}", exc_info=True)


# ── archive helper ─────────────────────────────────────────────────────
def _archive_file(filepath: str, db_name: str) -> None:
    import shutil
    dest_dir = os.path.join(ARCHIVE_DIR, db_name)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(filepath))
    if os.path.exists(dest):
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        base, ext = os.path.splitext(os.path.basename(filepath))
        dest = os.path.join(dest_dir, f"{base}_{ts}{ext}")
    try:
        shutil.move(filepath, dest)
        logger.info(f"📦 Archived to {dest}")
    except Exception as e:
        logger.warning(f"Could not archive {filepath}: {e}")


# ── DB name from HTML title ─────────────────────────────────────────────
def _db_name_from_html(filepath: str) -> str:
    import re
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(2048)
        m = re.search(r"<title>[^<]*DB:\s*([A-Za-z0-9$#_]+)", head, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    except OSError:
        pass

    fname = os.path.basename(filepath)
    m2 = re.match(r"^awr[_-]([A-Za-z0-9$#_]+?)[_-]\d", fname, re.IGNORECASE)
    if m2:
        return m2.group(1).upper()

    parent = os.path.basename(os.path.dirname(filepath))
    return parent.upper() if parent else "UNKNOWN"


# ── core: process one file ─────────────────────────────────────────────
def process_file(filepath: str, archive: bool = False) -> bool:
    filepath = os.path.abspath(filepath)

    if not os.path.exists(filepath):
        parent_dir  = os.path.dirname(filepath)
        fname_only  = os.path.basename(filepath)
        try:
            grandparent   = os.path.dirname(parent_dir)
            target_folder = os.path.basename(parent_dir).lower()
            for entry in os.listdir(grandparent):
                if entry.lower() == target_folder:
                    candidate = os.path.join(grandparent, entry, fname_only)
                    if os.path.exists(candidate):
                        logger.info(f"📁 Case-insensitive folder match: {candidate}")
                        filepath = candidate
                        break
        except Exception:
            pass

    if not os.path.exists(filepath):
        logger.error(f"❌ File not found: {filepath}")
        return False

    db_name = _db_name_from_html(filepath)
    fname   = os.path.basename(filepath)

    logger.info("=" * 60)
    logger.info(f"📂 Processing: {fname}")
    logger.info(f"   DB Name  : {db_name}")
    logger.info(f"   Full path: {filepath}")

    module_files = sorted([
        p for p in glob.glob(os.path.join(MODULES_DIR, "**", "*.py"), recursive=True)
        if not p.endswith("__init__.py")
        and os.path.basename(p).replace(".py", "") not in _SAR_MODULES
    ])

    if not module_files:
        logger.error(f"No parser modules found in {MODULES_DIR}")
        return False

    logger.info(f"Running {len(module_files)} parser modules …")

    t_file_start = time.perf_counter()
    failures = []

    for module_path in module_files:
        module_name = os.path.basename(module_path).replace(".py", "")
        t_start = time.perf_counter()
        try:
            module  = _load_module(module_path)
            ok      = _run_module(module, module_name, filepath)
            elapsed = time.perf_counter() - t_start
            if ok:
                logger.info(f"  ✅ {module_name:<45} {elapsed:.2f}s")
            else:
                logger.warning(f"  ⚠  {module_name:<45} {elapsed:.2f}s (no-op)")
        except Exception as e:
            elapsed = time.perf_counter() - t_start
            logger.error(f"  ❌ {module_name:<45} {elapsed:.2f}s — {e}", exc_info=True)
            failures.append(module_name)

    total_elapsed = time.perf_counter() - t_file_start
    logger.info(f"Modules complete in {total_elapsed:.1f}s — {len(failures)} failure(s)")

    # ── MV refresh ───────────────────────────────────────────────────────
    _refresh_materialized_views()

    # ── Auto recommendations + anomaly detection ─────────────────────────
    begin_snap, end_snap = _get_snap_range(db_name, filepath)
    if begin_snap is not None and end_snap is not None:
        _run_recommendations(db_name, begin_snap, end_snap)
        _run_anomaly_detection(db_name, begin_snap, end_snap)
    else:
        logger.warning(f"⚠ Could not resolve snap range for {db_name} — skipping recommendations/anomalies")

    # ── Archive ──────────────────────────────────────────────────────────
    if archive:
        _archive_file(filepath, db_name)

    has_critical_failure = False
    if failures:
        logger.warning(f"⚠  File processed with {len(failures)} module error(s): {fname}")
        logger.warning(f"   Failed modules: {failures}")
    else:
        logger.info(f"🎉 File processed successfully: {fname}")

    return not has_critical_failure


# ── batch mode ─────────────────────────────────────────────────────────
def process_all_in_dir(awr_dir: str = None, archive: bool = False) -> None:
    scan_dir = awr_dir or AWR_DIR
    patterns = ["**/*.html", "**/*.htm", "**/*.txt"]
    awr_files = []
    for pat in patterns:
        awr_files.extend(glob.glob(os.path.join(scan_dir, pat), recursive=True))

    awr_files = sorted(set(awr_files))

    if not awr_files:
        logger.warning(f"⚠  No AWR reports found under {scan_dir}")
        return

    logger.info(f"Batch mode: {len(awr_files)} file(s) found under {scan_dir}")
    for fpath in awr_files:
        process_file(fpath, archive=archive)


# ── entry point ────────────────────────────────────────────────────────
def main():
    import argparse
    p = argparse.ArgumentParser(description="AWR Master Parser v3")
    p.add_argument("filepath", nargs="?", default=None,
                   help="Path to a single AWR HTML/TXT report. Omit for batch scan.")
    p.add_argument("--archive", action="store_true",
                   help="Move processed file to archive/<db_name>/ after parsing")
    p.add_argument("--dir", default=None,
                   help="Override AWR directory for batch scan mode")
    args = p.parse_args()

    if args.filepath:
        success = process_file(args.filepath, archive=args.archive)
        sys.exit(0 if success else 1)
    else:
        logger.info("No filepath provided — running batch scan mode")
        process_all_in_dir(awr_dir=args.dir, archive=args.archive)


if __name__ == "__main__":
    main()
