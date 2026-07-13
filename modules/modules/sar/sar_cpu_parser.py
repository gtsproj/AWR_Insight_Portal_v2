# modules/sar/sar_cpu_parser.py  (v4 — definitive)
# CPU parser — first 20 lines diagnostic enabled
# Handles sysstat 10.x (AM/PM 12hr) and 12.x (24hr) formats

import os, sys, re, warnings
from datetime import datetime

warnings.simplefilter("ignore")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from utils import row_hash, sanitize_record
from logger_utils import get_logger

logger = get_logger("sar_cpu_parser")

_TIME_RE = re.compile(r"^(\d{1,2}:\d{2}:\d{2})\s*(AM|PM)?\s+", re.IGNORECASE)

_TIME_FMTS = [
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
]


def _parse_time(sar_date, time_str):
    combined = f"{sar_date} {time_str.strip()}"
    for fmt in _TIME_FMTS:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue
    logger.warning(f"Could not parse time: {combined!r}")
    return None


def _strip_time_prefix(line):
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


def parse_sar_cpu(lines, hostname, sar_date):
    records   = []
    col_names = None

    logger.info(f"CPU parser -- first 20 lines of input for {hostname} / {sar_date}:")
    non_empty = [l.rstrip() for l in lines if l.strip()][:20]
    for i, l in enumerate(non_empty):
        logger.info(f"  [{i:02d}] {repr(l[:120])}")

    for raw_line in lines:
        line = raw_line.rstrip()

        if not line.strip():
            col_names = None
            continue

        time_str, parts = _strip_time_prefix(line)

        if time_str is None:
            col_names = None
            continue

        if (parts and parts[0] == "CPU"
                and any(p in ("%usr", "%user") for p in parts)):
            col_names = parts
            logger.info(f"  CPU header detected: {col_names}")
            continue

        if col_names is None or not parts:
            continue

        if len(parts) < len(col_names):
            continue

        rec_map   = dict(zip(col_names, parts))
        snap_time = _parse_time(sar_date, time_str)
        if snap_time is None:
            continue

        rec = {
            "hostname":   hostname,
            "snap_time":  snap_time,
            "cpu":        str(rec_map.get("CPU", "all")),
            "usr_pct":    _sf(rec_map.get("%user",   rec_map.get("%usr",    "0"))),
            "nice_pct":   _sf(rec_map.get("%nice",   "0")),
            "system_pct": _sf(rec_map.get("%system", rec_map.get("%sys",    "0"))),
            "iowait_pct": _sf(rec_map.get("%iowait", "0")),
            "steal_pct":  _sf(rec_map.get("%steal",  "0")),
            "idle_pct":   _sf(rec_map.get("%idle",   "0")),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"Parsed {len(records)} CPU rows for {hostname}")
    return records


def insert_sar_cpu(records):
    if not records:
        logger.warning("No CPU records to insert")
        return

    records = [sanitize_record(r) for r in records]
    sql = """
        INSERT INTO sar_cpu_stats
            (hostname, snap_time, cpu, usr_pct, nice_pct,
             system_pct, iowait_pct, steal_pct, idle_pct, row_hash)
        VALUES
            (%(hostname)s, %(snap_time)s, %(cpu)s, %(usr_pct)s, %(nice_pct)s,
             %(system_pct)s, %(iowait_pct)s, %(steal_pct)s, %(idle_pct)s, %(row_hash)s)
        ON CONFLICT (hostname, snap_time, cpu, row_hash) DO NOTHING
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} CPU rows")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"CPU insert failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()


def _sf(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
