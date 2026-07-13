# modules/io_profile_parser.py

import os
import sys
import psycopg2
import pandas as pd
from bs4 import BeautifulSoup

# --- Add common folder to path ---
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "common"))
from db import get_db_connection
from config_loader import load_config
from utils import row_hash, extract_workload_repo_metadata, clean_number
from logger_utils import get_logger

# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "io_profile_parser.log")

logger = get_logger("io_profile_parser")  # or dynamic module name

def parse_io_profile(filepath):
    """Parse IO Profile section from AWR report"""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    # Extract workload repository metadata
    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.warning("⚠️ Could not extract workload repository metadata.")
        return records

    # --- Locate IO Profile header text ---
    io_profile_header = soup.find(string=lambda t: t and "IO Profile" in t)
    if not io_profile_header:
        logger.warning("⚠️ IO Profile section not found.")
        return records

    # --- Locate table after header ---
    table = io_profile_header.find_next("table")
    if not table:
        logger.warning("⚠️ IO Profile table missing.")
        return records

    df = pd.read_html(str(table))[0]

    # Parse rows
    for _, row in df.iterrows():
        metric = str(row[0]).strip().rstrip(":")
        read_per_sec = clean_number(row[2]) if len(row) > 2 else None
        write_per_sec = clean_number(row[3]) if len(row) > 3 else None

        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "metric": metric,
            "read_per_sec": read_per_sec,
            "write_per_sec": write_per_sec,
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} IO Profile records.")
    return records


def insert_io_profile(records):
    """Insert IO Profile records into PostgreSQL"""
    if not records:
        logger.warning("⚠️ No records to insert into awr_io_profile")
        return

    insert_query = """
        INSERT INTO awr_io_profile
        (dbname, instance, instnum, snap_time, metric,
         read_per_sec, write_per_sec, begin_snap, row_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
    """

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for rec in records:
            cur.execute(insert_query, (
                rec["dbname"], rec["instance"], rec["instnum"],
                rec["snap_time"], rec["metric"],
                rec["read_per_sec"], rec["write_per_sec"],
                rec["begin_snap"], rec["row_hash"]
            ))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_io_profile")
    except Exception as e:
        logger.error(f"❌ Failed inserting IO Profile: {e}", exc_info=True)


if __name__ == "__main__":
    # When called via exec fallback from master_parser, 
    # `filepath` is injected into globals
    _target = globals().get("filepath") or locals().get("filepath")
    if not _target:
        import sys
        _target = sys.argv[1] if len(sys.argv) > 1 else None
    if _target:
        records = parse_io_profile(_target)
        insert_io_profile(records)
    else:
        logger.error("No filepath provided")
