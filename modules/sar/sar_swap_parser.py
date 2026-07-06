# modules/sar/sar_swap_parser.py  (v2)
# ============================================================
# FIXES vs v1:
#   - Correct signature: parse_sar_swap(lines, hostname, sar_date)
#   - Converts kB → MB to match sar_swap_stats schema
#   - swap_used_pct calculated if not present in SAR output
#   - 12h/24h time format support
# ============================================================

import os
import sys
import warnings
from datetime import datetime

warnings.simplefilter("ignore")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from utils import row_hash, sanitize_record
from logger_utils import get_logger

logger = get_logger("sar_swap_parser")

_TIME_FMTS = [
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
]
_KB_TO_MB = 1 / 1024.0


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


def _kb_to_mb(v):
    f = _sf(v)
    return round(f / 1024.0, 3) if f is not None else None


def parse_sar_swap(lines: list, hostname: str, sar_date: str) -> list:
    records = []
    parsing = False
    headers = []

    for raw_line in lines:
        line = raw_line.strip()

        if line.lower().startswith("average:"):
            parsing = False
            headers = []
            continue

        if "kbswpfree" in line and "kbswpused" in line:
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

        free_mb = _kb_to_mb(rec_map.get("kbswpfree", 0))
        used_mb = _kb_to_mb(rec_map.get("kbswpused", 0))

        # %swpused may be present directly; calculate if absent
        pct = _sf(rec_map.get("%swpused", None))
        if pct is None and free_mb is not None and used_mb is not None:
            total = free_mb + used_mb
            pct   = round((used_mb / total) * 100, 2) if total > 0 else 0.0

        rec = {
            "hostname":      hostname,
            "snap_time":     snap_time,
            "swap_free_mb":  free_mb,
            "swap_used_mb":  used_mb,
            "swap_used_pct": pct,
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} swap rows for {hostname}")
    return records


def insert_sar_swap(records: list) -> None:
    if not records:
        logger.warning("No swap records to insert")
        return

    records = [sanitize_record(r) for r in records]

    sql = """
        INSERT INTO sar_swap_stats
            (hostname, snap_time, swap_free_mb, swap_used_mb, swap_used_pct, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s, %(swap_free_mb)s,
             %(swap_used_mb)s, %(swap_used_pct)s, %(row_hash)s)
        ON CONFLICT (hostname, snap_time, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} swap rows")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Swap insert failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
