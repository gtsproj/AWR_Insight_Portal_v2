# backfill_awr_anomalies.py
# ============================================================
# One-time script to run AWR anomaly detection across all
# existing snaps that were parsed before the auto-trigger
# was working correctly.
#
# Skips the first 3 snaps (insufficient baseline).
# Safe to re-run — ON CONFLICT DO UPDATE in store_awr_anomalies.
#
# USAGE:
#   python backfill_awr_anomalies.py
#   python backfill_awr_anomalies.py --db NEODBPRD
#   python backfill_awr_anomalies.py --db NEODBPRD --min-prior 3
# ============================================================

import os
import sys
import argparse

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from logger_utils import get_logger

logger = get_logger("backfill_awr_anomalies")


def get_all_dbs_and_snaps(min_prior: int = 3) -> dict:
    """
    Return dict of {(dbname, instance): [snap_ids_with_enough_baseline]}.
    Only includes snaps that have at least min_prior snaps before them.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT dbname, instance, begin_snap
                FROM awr_load_profile
                ORDER BY dbname, instance, begin_snap
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    # Group by db+instance and filter snaps with enough prior history
    from collections import defaultdict
    groups = defaultdict(list)
    for dbname, instance, snap in rows:
        groups[(dbname, instance)].append(snap)

    eligible = {}
    for (dbname, instance), snaps in groups.items():
        snaps_sorted = sorted(snaps)
        # Only snaps with at least min_prior snaps before them
        eligible_snaps = snaps_sorted[min_prior:]
        if eligible_snaps:
            eligible[(dbname, instance)] = eligible_snaps
            logger.info(f"  {dbname}/{instance}: {len(snaps_sorted)} total snaps, "
                        f"{len(eligible_snaps)} eligible for detection "
                        f"(starting snap {eligible_snaps[0]})")

    return eligible


def backfill(dbname_filter: str = None, min_prior: int = 3) -> None:
    # Import here so script can sit in project root
    import importlib.util
    det_path = os.path.join(_PROJECT_ROOT, "anomaly_detector.py")
    spec   = importlib.util.spec_from_file_location("anomaly_detector", det_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    logger.info("=" * 60)
    logger.info("AWR Anomaly Backfill Starting")
    logger.info(f"  Min prior snaps required: {min_prior}")
    if dbname_filter:
        logger.info(f"  DB filter: {dbname_filter}")
    logger.info("=" * 60)

    eligible = get_all_dbs_and_snaps(min_prior)

    if not eligible:
        logger.warning("No eligible snaps found.")
        return

    total_findings = 0
    total_snaps    = 0

    for (dbname, instance), snaps in eligible.items():
        if dbname_filter and dbname.upper() != dbname_filter.upper():
            continue

        logger.info(f"\n{'─'*50}")
        logger.info(f"Processing: {dbname} / {instance}  ({len(snaps)} snaps)")
        logger.info(f"{'─'*50}")

        db_findings = 0
        for snap in snaps:
            findings = module.detect_all(dbname, snap, instance)
            if findings:
                stored = module.store_awr_anomalies(findings)
                db_findings    += stored
                total_findings += stored
                logger.info(f"  Snap {snap}: {stored} anomaly(ies) stored")
            else:
                logger.debug(f"  Snap {snap}: 0 findings")
            total_snaps += 1

        logger.info(f"  {dbname}/{instance} complete: "
                    f"{db_findings} anomalies across {len(snaps)} snaps")

    logger.info("\n" + "=" * 60)
    logger.info(f"Backfill complete: {total_findings} total anomalies "
                f"stored across {total_snaps} snaps")
    logger.info("=" * 60)


def main():
    p = argparse.ArgumentParser(description="AWR Anomaly Detection Backfill")
    p.add_argument("--db",        default=None,
                   help="Filter to one DB name (default: all DBs)")
    p.add_argument("--min-prior", type=int, default=3,
                   help="Minimum prior snaps needed for baseline (default: 3)")
    args = p.parse_args()
    backfill(dbname_filter=args.db, min_prior=args.min_prior)


if __name__ == "__main__":
    main()
