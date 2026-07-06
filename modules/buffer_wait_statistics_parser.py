#modules/buffer_wait_statistics_parser.py

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
log_file = os.path.join(LOG_DIR, "buffer_wait_statistics_parser.log")

logger = get_logger("buffer_wait_statistics_parser")  # or dynamic module name

def parse_buffer_wait_statistics(filepath):
    """Parse the Buffer Wait Statistics section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="Buffer Wait Statistics")
    if not section:
        logger.warning("⚠️ Buffer Wait Statistics section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ Buffer Wait Statistics table not found.")
        return []

    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in Buffer Wait Statistics section")

    if is_section_empty(df, "Buffer Wait Statistics", "awr_buffer_wait_statistics"):
        sys.exit(0)  # Skip this section

    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "class": str(row.get("Class", "")).strip(),
            "waits": clean_number(row.get("Waits", None)),
            "total_wait_time_s": clean_number(row.get("Total Wait Time (s)", None)),
            "avg_time_ms": clean_number(row.get("Avg Time (ms)", None)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Buffer Wait Statistics records")
    return records


def insert_buffer_wait_statistics(records):
    """Insert parsed Buffer Wait Statistics into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_buffer_wait_statistics")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_buffer_wait_statistics (
                dbname, instance, instnum, snap_time,
                class, waits, total_wait_time_s, avg_time_ms,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(class)s, %(waits)s, %(total_wait_time_s)s, %(avg_time_ms)s,
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_buffer_wait_statistics")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate Buffer Wait Statistics record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting Buffer Wait Statistics: {e}", exc_info=True)
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
        records = parse_buffer_wait_statistics(_target)
        insert_buffer_wait_statistics(records)
    else:
        logger.error("No filepath provided")
