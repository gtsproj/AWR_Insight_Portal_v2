# backfill_sar_anomalies.py
# ============================================================
# One-time script to run SAR anomaly detection across all
# existing parsed SAR data in sar_cpu_stats.
#
# For each hostname + date found in sar_cpu_stats, runs
# anomaly detection over that day's data window and stores
# results in sar_anomalies table.
#
# Safe to re-run — ON CONFLICT DO UPDATE in store_sar_anomalies.
#
# USAGE:
#   py backfill_sar_anomalies.py
#   py backfill_sar_anomalies.py --host TCLFSLPRDDB1
# ============================================================

import os
import sys
import argparse
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from logger_utils import get_logger

logger = get_logger("backfill_sar_anomalies")


def get_host_dates(host_filter=None) -> list:
    """Return list of (hostname, date) tuples from sar_cpu_stats."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if host_filter:
                cur.execute("""
                    SELECT DISTINCT hostname, snap_time::date AS snap_date
                    FROM sar_cpu_stats
                    WHERE hostname = %s
                    ORDER BY hostname, snap_date
                """, (host_filter,))
            else:
                cur.execute("""
                    SELECT DISTINCT hostname, snap_time::date AS snap_date
                    FROM sar_cpu_stats
                    ORDER BY hostname, snap_date
                """)
            return cur.fetchall()
    finally:
        conn.close()


def backfill(host_filter=None):
    import importlib.util
    det_path = os.path.join(_PROJECT_ROOT, "anomaly_detector.py")
    spec     = importlib.util.spec_from_file_location("anomaly_detector", det_path)
    module   = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    host_dates = get_host_dates(host_filter)
    if not host_dates:
        logger.warning("No SAR data found in sar_cpu_stats")
        return

    # Group by hostname
    from collections import defaultdict
    by_host = defaultdict(list)
    for hostname, snap_date in host_dates:
        by_host[hostname].append(snap_date)

    logger.info("=" * 60)
    logger.info("SAR Anomaly Backfill Starting")
    logger.info(f"  Hosts found: {list(by_host.keys())}")
    logger.info(f"  Total host-days: {len(host_dates)}")
    logger.info("=" * 60)

    total_findings = 0

    for hostname, dates in by_host.items():
        logger.info(f"\n{'─'*50}")
        logger.info(f"Host: {hostname}  ({len(dates)} days)")
        logger.info(f"{'─'*50}")
        host_findings = 0

        for snap_date in sorted(dates):
            # Run detection for each day's window
            snap_from = datetime.combine(snap_date, datetime.min.time())
            snap_to   = snap_from + timedelta(hours=25)

            findings = module.detect_sar(
                hostname, snap_from, snap_to, store=False
            )
            if findings:
                stored = module.store_sar_anomalies(findings)
                host_findings  += stored
                total_findings += stored
                logger.info(f"  {snap_date}: {stored} anomaly(ies) stored")
            else:
                logger.debug(f"  {snap_date}: 0 findings")

        logger.info(f"  {hostname} complete: {host_findings} total anomalies")

    logger.info("\n" + "=" * 60)
    logger.info(f"Backfill complete: {total_findings} SAR anomalies stored")
    logger.info("=" * 60)


def main():
    p = argparse.ArgumentParser(description="SAR Anomaly Backfill")
    p.add_argument("--host", default=None,
                   help="Filter to one hostname (default: all)")
    args = p.parse_args()
    backfill(host_filter=args.host)


if __name__ == "__main__":
    main()
