# modules/sar/sar_ctxswitch_parser.py
# ============================================================
# Parses SAR context switch / process creation stats (sar -w).
#
# High cswch/s correlated with Oracle latch/mutex contention.
# High proc/s during peak can indicate runaway process spawning.
#
# SAR header format:
#   HH:MM:SS  proc/s  cswch/s
#
# Schema: sar_ctxswitch_stats
# ============================================================

import os, sys, warnings
from datetime import datetime

warnings.simplefilter("ignore")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from utils import row_hash, sanitize_record
from logger_utils import get_logger

logger = get_logger("sar_ctxswitch_parser")

_TIME_FMTS = [
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
]


def _parse_time(sar_date, time_str):
    combined = f"{sar_date} {time_str}"
    for fmt in _TIME_FMTS:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue
    return None


def _sf(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_sar_ctxswitch(lines: list, hostname: str, sar_date: str) -> list:
    """
    Parse context switch statistics from SAR output (sar -w).

    Schema columns:
      hostname, snap_time, proc_per_sec, cswch_per_sec
    """
    records = []
    parsing = False
    headers = []

    for raw_line in lines:
        line = raw_line.strip()

        if line.lower().startswith("average:"):
            parsing = False
            headers = []
            continue

        # Context switch section header
        if "cswch/s" in line:
            headers = line.split()
            parsing = True
            continue

        if parsing and not line:
            parsing = False
            headers = []
            continue

        if not parsing or not headers:
            continue

        parts = line.split()
        if len(parts) < len(headers):
            continue

        rec_map   = dict(zip(headers, parts))
        snap_time = _parse_time(sar_date, parts[0])
        if snap_time is None:
            continue

        rec = {
            "hostname":      hostname,
            "snap_time":     snap_time,
            "proc_per_sec":  _sf(rec_map.get("proc/s",  0)),
            "cswch_per_sec": _sf(rec_map.get("cswch/s", 0)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} context switch rows for {hostname}")
    return records


def insert_sar_ctxswitch(records: list) -> None:
    if not records:
        logger.warning("No context switch records to insert")
        return

    records = [sanitize_record(r) for r in records]

    sql = """
        INSERT INTO sar_ctxswitch_stats
            (hostname, snap_time, proc_per_sec, cswch_per_sec, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s,
             %(proc_per_sec)s, %(cswch_per_sec)s, %(row_hash)s)
        ON CONFLICT (hostname, snap_time, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} context switch rows")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Context switch insert failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
