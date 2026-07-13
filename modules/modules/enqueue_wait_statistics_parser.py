#modules/enqueue_wait_statistics_parser.py

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
from utils import row_hash, extract_workload_repo_metadata, clean_number, is_section_empty, sanitize_record
from logger_utils import get_logger

# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "enqueue_wait_statistics_parser.log")

logger = get_logger("enqueue_wait_statistics_parser")  # or dynamic module name

def parse_enqueue_statistics(filepath):
    """Parse the Enqueue Activity section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="Enqueue Activity")
    if not section:
        logger.warning("⚠️ Enqueue Activity section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ Enqueue Activity table not found.")
        return []

    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in Enqueue Activity section")

    if is_section_empty(df, "Enqueue Activity", "awr_enqueue_statistics"):
        sys.exit(0)  # Skip this section

    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "enqueue_type_request_reason": str(row.get("Enqueue Type (Request Reason)", "")).strip(),
            "requests": clean_number(row.get("Requests", None)),
            "succ_gets": clean_number(row.get("Succ Gets", None)),
            "failed_gets": clean_number(row.get("Failed Gets", None)),
            "waits": clean_number(row.get("Waits", None)),
            "wt_time_s": clean_number(row.get("Wt Time (s)", None)),
            "av_wt_time_ms": clean_number(row.get("Av Wt Time(ms)", None)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Enqueue Activity records")
    return records


def insert_enqueue_statistics(records):
    """Insert parsed Enqueue Activity statistics into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_enqueue_statistics")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_enqueue_statistics (
                dbname, instance, instnum, snap_time,
                enqueue_type_request_reason, requests, succ_gets, failed_gets,
                waits, wt_time_s, av_wt_time_ms,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(enqueue_type_request_reason)s, %(requests)s, %(succ_gets)s, %(failed_gets)s,
                %(waits)s, %(wt_time_s)s, %(av_wt_time_ms)s,
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_enqueue_statistics")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate Enqueue Activity record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting Enqueue Activity: {e}", exc_info=True)
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
        records = parse_enqueue_statistics(_target)
        insert_enqueue_statistics(records)
    else:
        logger.error("No filepath provided")
