# modules/sar/sar_paging_parser.py
# ============================================================
# Parses SAR paging statistics (sar -B).
# Critical for detecting memory pressure causing major page faults.
#
# SAR header format:
#   HH:MM:SS  pgpgin/s  pgpgout/s  fault/s  majflt/s
#             pgfree/s  pgscank/s  pgscand/s  pgsteal/s  %vmeff
#
# Schema: sar_paging_stats
# ============================================================

import os, sys, warnings
from datetime import datetime

warnings.simplefilter("ignore")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from utils import row_hash, sanitize_record
from logger_utils import get_logger

logger = get_logger("sar_paging_parser")

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


def parse_sar_paging(lines: list, hostname: str, sar_date: str) -> list:
    """
    Parse paging statistics from SAR output (sar -B).

    majflt/s is the most critical metric — major page faults indicate
    the OS is swapping pages from disk, severely impacting Oracle SGA/PGA.

    Schema columns:
      hostname, snap_time,
      pgpgin_per_sec, pgpgout_per_sec,
      fault_per_sec, majflt_per_sec,
      pgfree_per_sec, pgscank_per_sec,
      pgscand_per_sec, pgsteal_per_sec,
      vmeff_pct
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

        # Paging section header
        if "pgpgin/s" in line and "fault/s" in line:
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
            "hostname":         hostname,
            "snap_time":        snap_time,
            "pgpgin_per_sec":   _sf(rec_map.get("pgpgin/s",  0)),
            "pgpgout_per_sec":  _sf(rec_map.get("pgpgout/s", 0)),
            "fault_per_sec":    _sf(rec_map.get("fault/s",   0)),
            "majflt_per_sec":   _sf(rec_map.get("majflt/s",  0)),
            "pgfree_per_sec":   _sf(rec_map.get("pgfree/s",  0)),
            "pgscank_per_sec":  _sf(rec_map.get("pgscank/s", 0)),
            "pgscand_per_sec":  _sf(rec_map.get("pgscand/s", 0)),
            "pgsteal_per_sec":  _sf(rec_map.get("pgsteal/s", 0)),
            "vmeff_pct":        _sf(rec_map.get("%vmeff",    0)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} paging rows for {hostname}")
    return records


def insert_sar_paging(records: list) -> None:
    if not records:
        logger.warning("No paging records to insert")
        return

    records = [sanitize_record(r) for r in records]

    sql = """
        INSERT INTO sar_paging_stats
            (hostname, snap_time,
             pgpgin_per_sec, pgpgout_per_sec,
             fault_per_sec, majflt_per_sec,
             pgfree_per_sec, pgscank_per_sec,
             pgscand_per_sec, pgsteal_per_sec,
             vmeff_pct, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s,
             %(pgpgin_per_sec)s, %(pgpgout_per_sec)s,
             %(fault_per_sec)s, %(majflt_per_sec)s,
             %(pgfree_per_sec)s, %(pgscank_per_sec)s,
             %(pgscand_per_sec)s, %(pgsteal_per_sec)s,
             %(vmeff_pct)s, %(row_hash)s)
        ON CONFLICT (hostname, snap_time, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} paging rows")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Paging insert failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
