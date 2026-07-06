# modules/sar/sar_loadavg_parser.py
# ============================================================
# Parses SAR run queue and load average stats (sar -q).
#
# runq-sz and blocked are the key DBA metrics:
#   - runq-sz > number_of_CPUs = CPU saturation confirmed
#   - blocked  > 0             = processes stuck waiting for I/O
#
# SAR header format:
#   HH:MM:SS  runq-sz  plist-sz  ldavg-1  ldavg-5  ldavg-15  blocked
#
# Schema: sar_loadavg_stats
# ============================================================

import os, sys, warnings
from datetime import datetime

warnings.simplefilter("ignore")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from utils import row_hash, sanitize_record
from logger_utils import get_logger

logger = get_logger("sar_loadavg_parser")

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


def parse_sar_loadavg(lines: list, hostname: str, sar_date: str) -> list:
    """
    Parse run queue and load average stats from SAR output (sar -q).

    Schema columns:
      hostname, snap_time,
      runq_sz, plist_sz,
      ldavg_1, ldavg_5, ldavg_15,
      blocked
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

        # Load average section header
        if "runq-sz" in line and "ldavg-1" in line:
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
            "hostname":  hostname,
            "snap_time": snap_time,
            "runq_sz":   _sf(rec_map.get("runq-sz",  0)),
            "plist_sz":  _sf(rec_map.get("plist-sz", 0)),
            "ldavg_1":   _sf(rec_map.get("ldavg-1",  0)),
            "ldavg_5":   _sf(rec_map.get("ldavg-5",  0)),
            "ldavg_15":  _sf(rec_map.get("ldavg-15", 0)),
            "blocked":   _sf(rec_map.get("blocked",  0)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} load average rows for {hostname}")
    return records


def insert_sar_loadavg(records: list) -> None:
    if not records:
        logger.warning("No load average records to insert")
        return

    records = [sanitize_record(r) for r in records]

    sql = """
        INSERT INTO sar_loadavg_stats
            (hostname, snap_time,
             runq_sz, plist_sz,
             ldavg_1, ldavg_5, ldavg_15,
             blocked, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s,
             %(runq_sz)s, %(plist_sz)s,
             %(ldavg_1)s, %(ldavg_5)s, %(ldavg_15)s,
             %(blocked)s, %(row_hash)s)
        ON CONFLICT (hostname, snap_time, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} load average rows")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Load average insert failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
