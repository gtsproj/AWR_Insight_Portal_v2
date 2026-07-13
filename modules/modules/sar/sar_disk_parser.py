# modules/sar/sar_disk_parser.py  (v2)
# ============================================================
# FIXES vs v1:
#   - Correct signature: parse_sar_disk(lines, hostname, sar_date)
#   - Populates all schema columns: tps, read_mb_per_sec,
#     write_mb_per_sec, await_ms, svctm_ms, util_pct
#   - Converts rd_sec/s and wr_sec/s (512-byte sectors) → MB/s
#   - Handles both older (svctm) and newer SAR layouts
#   - Device name extracted correctly (2nd column after time)
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

logger = get_logger("sar_disk_parser")

_TIME_FMTS = [
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
]
# 512-byte sectors → MB: 1 sector = 512 bytes = 1/2048 MB
_SECTORS_TO_MB = 1 / 2048.0


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


def parse_sar_disk(lines: list, hostname: str, sar_date: str) -> list:
    """
    Parse block-device I/O statistics from SAR output (sar -d).

    Schema columns:
      hostname, snap_time, device, tps,
      read_mb_per_sec, write_mb_per_sec,
      await_ms, svctm_ms, util_pct
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

        # Disk section header — contains DEV and await
        if "DEV" in line and ("await" in line or "rd_sec" in line or "rkB" in line):
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

        device = rec_map.get("DEV", "unknown")

        # tps
        tps = _sf(rec_map.get("tps", 0))

        # Read MB/s: try rkB/s first (newer SAR), then rd_sec/s (older)
        if "rkB/s" in rec_map:
            read_mb = round((_sf(rec_map["rkB/s"]) or 0) / 1024.0, 4)
        elif "rd_sec/s" in rec_map:
            read_mb = round((_sf(rec_map["rd_sec/s"]) or 0) * _SECTORS_TO_MB, 4)
        else:
            read_mb = None

        # Write MB/s: try wkB/s first, then wr_sec/s
        if "wkB/s" in rec_map:
            write_mb = round((_sf(rec_map["wkB/s"]) or 0) / 1024.0, 4)
        elif "wr_sec/s" in rec_map:
            write_mb = round((_sf(rec_map["wr_sec/s"]) or 0) * _SECTORS_TO_MB, 4)
        else:
            write_mb = None

        await_ms = _sf(rec_map.get("await", 0))
        svctm_ms = _sf(rec_map.get("svctm", None))   # removed in newer kernels
        util_pct = _sf(rec_map.get("%util", 0))

        rec = {
            "hostname":       hostname,
            "snap_time":      snap_time,
            "device":         device,
            "tps":            tps,
            "read_mb_per_sec":  read_mb,
            "write_mb_per_sec": write_mb,
            "await_ms":       await_ms,
            "svctm_ms":       svctm_ms,
            "util_pct":       util_pct,
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} disk rows for {hostname}")
    return records


def insert_sar_disk(records: list) -> None:
    if not records:
        logger.warning("No disk records to insert")
        return

    records = [sanitize_record(r) for r in records]

    sql = """
        INSERT INTO sar_disk_stats
            (hostname, snap_time, device, tps,
             read_mb_per_sec, write_mb_per_sec,
             await_ms, svctm_ms, util_pct, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s, %(device)s, %(tps)s,
             %(read_mb_per_sec)s, %(write_mb_per_sec)s,
             %(await_ms)s, %(svctm_ms)s, %(util_pct)s, %(row_hash)s)
        ON CONFLICT (hostname, snap_time, device, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} disk rows")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Disk insert failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
