#modules/operating_system_statistics_parser.py


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
log_file = os.path.join(LOG_DIR, "operating_system_statistics_parser.log")

logger = get_logger("operating_system_statistics_parser")  # or dynamic module name

def parse_os_statistics(filepath):
    """Parse Operating System Statistics section from AWR report"""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    # Extract workload repository metadata
    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.warning("⚠️ Could not extract workload repository metadata.")
        return records

    # --- Locate Operating System Statistics header ---
    header = soup.find("h3", string=lambda t: t and "Operating System Statistics" in t)
    if not header:
        logger.warning("⚠️ Operating System Statistics section not found.")
        return records

    # --- Locate table after header ---
    table = header.find_next("table")
    if not table:
        logger.warning("⚠️ Operating System Statistics table missing.")
        return records

    df = pd.read_html(str(table))[0]

    if is_section_empty(df, "Operating System Statistics", "awr_os_statistics"):
        sys.exit(0)  # Skip this section

    # Expecting at least 2 columns: Statistic, Value
    for _, row in df.iterrows():
        try:
            statistic = str(row[0]).strip()
            value = clean_number(row[1]) if len(row) > 1 else None
        except Exception:
            continue

        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "statistic": statistic,
            "value": value,
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Operating System Statistics records.")
    return records


def insert_os_statistics(records):
    """Insert OS Statistics records into PostgreSQL"""
    if not records:
        logger.warning("⚠️ No records to insert into awr_os_statistics")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    insert_query = """
        INSERT INTO awr_os_statistics
        (dbname, instance, instnum, snap_time, statistic, value,
         begin_snap, row_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
    """

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for rec in records:
            cur.execute(insert_query, (
                rec["dbname"], rec["instance"], rec["instnum"],
                rec["snap_time"], rec["statistic"], rec["value"],
                rec["begin_snap"], rec["row_hash"]
            ))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_os_statistics")
    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate Operating System Statistics record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting Operating System Statistics: {e}", exc_info=True)
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
        records = parse_os_statistics(_target)
        insert_os_statistics(records)
    else:
        logger.error("No filepath provided")
