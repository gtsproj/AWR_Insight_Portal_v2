# modules/sar/sar_memory_parser.py  (v2)
# ============================================================
# FIXES vs v1:
#   - Signature: parse_sar_memory(lines, hostname, sar_date)
#   - Converts kB → MB to match sar_memory_stats schema
#     (schema has mem_free_mb / mem_used_mb, not kB)
#   - Added buffers_mb, cached_mb, commit_mb, commit_pct columns
#   - Handles both sar -r and sar -r ALL output variants
#   - 12h / 24h time format support
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

logger = get_logger("sar_memory_parser")

_TIME_FMTS = [
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
]

_KB_TO_MB = 1 / 1024.0


def _parse_time(sar_date: str, time_str: str):
    combined = f"{sar_date} {time_str}"
    for fmt in _TIME_FMTS:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue
    return None


def _safe_float(v) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _kb_to_mb(v) -> float | None:
    f = _safe_float(v)
    return round(f * _KB_TO_MB, 3) if f is not None else None


def parse_sar_memory(lines: list, hostname: str, sar_date: str) -> list:
    """
    Parse memory statistics from SAR output (sar -r).

    Schema columns:
      hostname, snap_time,
      mem_free_mb, mem_used_mb, mem_used_pct,
      buffers_mb, cached_mb, commit_mb, commit_pct
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

        # Memory section header: contains kbmemfree or kbavail
        if ("kbmemfree" in line or "kbavail" in line) and "kbmemused" in line:
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

        rec_map = dict(zip(headers, parts))
        snap_time = _parse_time(sar_date, parts[0])
        if snap_time is None:
            continue

        rec = {
            "hostname":     hostname,
            "snap_time":    snap_time,
            # kbmemfree or kbavail (newer SAR versions)
            "mem_free_mb":  _kb_to_mb(rec_map.get("kbmemfree",  rec_map.get("kbavail", 0))),
            "mem_used_mb":  _kb_to_mb(rec_map.get("kbmemused",  0)),
            "mem_used_pct": _safe_float(rec_map.get("%memused",  0)),
            "buffers_mb":   _kb_to_mb(rec_map.get("kbbuffers",  0)),
            "cached_mb":    _kb_to_mb(rec_map.get("kbcached",   rec_map.get("kbdirty", 0))),
            "commit_mb":    _kb_to_mb(rec_map.get("kbcommit",   0)),
            "commit_pct":   _safe_float(rec_map.get("%commit",  0)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} memory rows for {hostname}")
    return records


def insert_sar_memory(records: list) -> None:
    if not records:
        logger.warning("No memory records to insert")
        return

    records = [sanitize_record(r) for r in records]

    sql = """
        INSERT INTO sar_memory_stats
            (hostname, snap_time,
             mem_free_mb, mem_used_mb, mem_used_pct,
             buffers_mb, cached_mb, commit_mb, commit_pct, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s,
             %(mem_free_mb)s, %(mem_used_mb)s, %(mem_used_pct)s,
             %(buffers_mb)s, %(cached_mb)s, %(commit_mb)s, %(commit_pct)s,
             %(row_hash)s)
        ON CONFLICT (hostname, snap_time, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} memory rows")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Memory insert failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
