#modules/background_wait_events_parser.py

import os
import sys
import warnings
import psycopg2
import pandas as pd
from bs4 import BeautifulSoup

# --- Suppress pandas FutureWarnings ---
warnings.simplefilter(action="ignore", category=FutureWarning)

# --- Add common folder to path ---
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "common"))
from db import get_db_connection
from config_loader import load_config
from utils import row_hash, extract_workload_repo_metadata, clean_number, convert_to_ms, is_section_empty, sanitize_record
from logger_utils import get_logger

# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "background_wait_events_parser.log")

logger = get_logger("background_wait_events_parser")  # or dynamic module name

def parse_background_wait_events(filepath):
    """Parse Background Wait Events section from an AWR HTML report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="Background Wait Events")
    if not section:
        logger.warning("⚠️ Background Wait Events section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ Background Wait Events table not found.")
        return []

    df = pd.read_html(str(table))[0]

    if is_section_empty(df, "Background Wait Events", "awr_background_wait_events"):
        sys.exit(0)  # Skip this section

    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "event": str(row[0]).strip(),
            "waits": clean_number(row[1]),
            "total_wait_time_s": clean_number(row[3]),
            "avg_wait_ms": convert_to_ms(str(row[4])),
            "waits_per_txn": clean_number(row[5]),
            "pct_bg_time": clean_number(row[6]),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Background Wait Event records.")
    return records


def insert_background_wait_events(records):
    """Insert Background Wait Events records into PostgreSQL."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_background_wait_events")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_background_wait_events (
                dbname, instance, instnum, snap_time, event,
                waits, total_wait_time_s, avg_wait_ms, waits_per_txn, pct_bg_time,
                begin_snap, row_hash
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        for rec in records:
            cur.execute(insert_query, (
                rec["dbname"], rec["instance"], rec["instnum"], rec["snap_time"],
                rec["event"], rec["waits"], rec["total_wait_time_s"],
                rec["avg_wait_ms"], rec["waits_per_txn"], rec["pct_bg_time"],
                rec["begin_snap"], rec["row_hash"],
            ))

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_background_wait_events")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate Background Wait Events record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting Background Wait Events: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # When called via exec fallback from master_parser, 
    # `filepath` is injected into globals
    _target = globals().get("filepath") or locals().get("filepath")
    if not _target:
        import sys
        _target = sys.argv[1] if len(sys.argv) > 1 else None
    if _target:
        records = parse_background_wait_events(_target)
        insert_background_wait_events(records)
    else:
        logger.error("No filepath provided")
