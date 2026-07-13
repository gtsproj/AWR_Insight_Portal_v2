# modules/sar/sar_network_parser.py
# ============================================================
# Parses SAR network interface statistics (sar -n DEV).
#
# SAR header format (from live output):
#   HH:MM:SS  IFACE  rxpck/s  txpck/s  rxkB/s  txkB/s
#             rxcmp/s  txcmp/s  rxmcst/s  %ifutil
#
# Schema: sar_network_stats
# ============================================================

import os, sys, warnings
from datetime import datetime

warnings.simplefilter("ignore")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from utils import row_hash, sanitize_record
from logger_utils import get_logger

logger = get_logger("sar_network_parser")

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


def parse_sar_network(lines: list, hostname: str, sar_date: str) -> list:
    """
    Parse network interface stats from SAR text output (sar -n DEV).

    Schema columns:
      hostname, snap_time, iface,
      rxpck_per_sec, txpck_per_sec,
      rxkb_per_sec, txkb_per_sec,
      rxcmp_per_sec, txcmp_per_sec,
      rxmcst_per_sec, ifutil_pct
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

        # Network section header: contains IFACE and rxpck/s
        if "IFACE" in line and ("rxpck/s" in line or "rxkB/s" in line):
            headers = line.split()
            parsing = True
            continue

        if parsing and not line:
            # Blank line — repeated headers follow in sar -n DEV output
            # Don't reset parsing — next line may be another header block
            continue

        # Re-detect header repeat (sar -n DEV prints headers per interval)
        if parsing and "IFACE" in line and "rxpck/s" in line:
            headers = line.split()
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

        iface = rec_map.get("IFACE", "unknown")
        # Skip loopback by default — can be enabled via config
        # (keeping loopback in for completeness; filter in Grafana)

        rec = {
            "hostname":       hostname,
            "snap_time":      snap_time,
            "iface":          iface,
            "rxpck_per_sec":  _sf(rec_map.get("rxpck/s", 0)),
            "txpck_per_sec":  _sf(rec_map.get("txpck/s", 0)),
            "rxkb_per_sec":   _sf(rec_map.get("rxkB/s",  0)),
            "txkb_per_sec":   _sf(rec_map.get("txkB/s",  0)),
            "rxcmp_per_sec":  _sf(rec_map.get("rxcmp/s", 0)),
            "txcmp_per_sec":  _sf(rec_map.get("txcmp/s", 0)),
            "rxmcst_per_sec": _sf(rec_map.get("rxmcst/s", 0)),
            "ifutil_pct":     _sf(rec_map.get("%ifutil",  0)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} network rows for {hostname}")
    return records


def insert_sar_network(records: list) -> None:
    if not records:
        logger.warning("No network records to insert")
        return

    records = [sanitize_record(r) for r in records]

    sql = """
        INSERT INTO sar_network_stats
            (hostname, snap_time, iface,
             rxpck_per_sec, txpck_per_sec,
             rxkb_per_sec, txkb_per_sec,
             rxcmp_per_sec, txcmp_per_sec,
             rxmcst_per_sec, ifutil_pct, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s, %(iface)s,
             %(rxpck_per_sec)s, %(txpck_per_sec)s,
             %(rxkb_per_sec)s, %(txkb_per_sec)s,
             %(rxcmp_per_sec)s, %(txcmp_per_sec)s,
             %(rxmcst_per_sec)s, %(ifutil_pct)s, %(row_hash)s)
        ON CONFLICT (hostname, snap_time, iface, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} network rows")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Network insert failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
