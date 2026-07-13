# modules/sar/sar_socket_parser.py
# ============================================================
# Parses Socket statistics from SAR text output (sar -n SOCK)
#
# SAR header format:
#   HH:MM:SS [AM/PM]  totsck  tcpsck  udpsck  rawsck  ip-frag  tcp-tw
#
# Oracle relevance:
#   tcp-tw (TIME_WAIT) spikes = connection storm from app servers
#     correlate with Oracle "SQL*Net message from client" wait spikes
#   totsck surge = sudden burst of new sessions (batch job / app restart)
#   udpsck surge = possible RAC interconnect issue (RAC uses UDP)
# ============================================================

import os, sys, re, warnings
from datetime import datetime

warnings.simplefilter("ignore")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from utils import row_hash, sanitize_record
from logger_utils import get_logger

logger = get_logger("sar_socket_parser")

_TIME_RE   = re.compile(r"^(\d{1,2}:\d{2}:\d{2})\s*(AM|PM)?\s+", re.IGNORECASE)
_TIME_FMTS = ["%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S"]


def _parse_time(sar_date, time_str):
    combined = f"{sar_date} {time_str.strip()}"
    for fmt in _TIME_FMTS:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue
    return None


def _strip_time(line):
    stripped = line.strip()
    if not stripped or stripped.lower().startswith("average:"):
        return None, None
    m = _TIME_RE.match(stripped)
    if not m:
        return None, None
    hms      = m.group(1)
    ampm     = (m.group(2) or "").upper()
    rest     = stripped[m.end():].split()
    time_str = f"{hms} {ampm}".strip() if ampm else hms
    return time_str, rest


def _si(v):
    try:    return int(float(str(v).replace(",", "").strip()))
    except: return None


def parse_sar_socket(lines, hostname, sar_date):
    records   = []
    col_names = None

    for raw_line in lines:
        line = raw_line.rstrip()

        if not line.strip():
            col_names = None
            continue

        time_str, parts = _strip_time(line)
        if time_str is None:
            col_names = None
            continue

        # Header: contains totsck
        if parts and "totsck" in parts:
            col_names = parts
            continue

        if col_names is None or not parts or len(parts) < len(col_names):
            continue

        snap_time = _parse_time(sar_date, time_str)
        if snap_time is None:
            continue

        rec_map = dict(zip(col_names, parts))
        rec = {
            "hostname":  hostname,
            "snap_time": snap_time,
            "totsck":    _si(rec_map.get("totsck",   0)),
            "tcpsck":    _si(rec_map.get("tcpsck",   0)),
            "udpsck":    _si(rec_map.get("udpsck",   0)),
            "rawsck":    _si(rec_map.get("rawsck",   0)),
            "ip_frag":   _si(rec_map.get("ip-frag",  0)),
            "tcp_tw":    _si(rec_map.get("tcp-tw",   0)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} Socket rows for {hostname}")
    return records


def insert_sar_socket(records):
    if not records:
        logger.warning("No Socket records to insert")
        return

    records = [sanitize_record(r) for r in records]
    sql = """
        INSERT INTO sar_socket_stats
            (hostname, snap_time, totsck, tcpsck, udpsck,
             rawsck, ip_frag, tcp_tw, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s, %(totsck)s, %(tcpsck)s, %(udpsck)s,
             %(rawsck)s, %(ip_frag)s, %(tcp_tw)s, %(row_hash)s)
        ON CONFLICT (hostname, snap_time, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} Socket rows")
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"Socket insert failed: {e}", exc_info=True)
    finally:
        if conn: conn.close()
