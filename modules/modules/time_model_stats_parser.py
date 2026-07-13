#module/time_model_stats_parser.py


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
from utils import row_hash, extract_workload_repo_metadata, clean_number, sanitize_record
from logger_utils import get_logger

# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "time_model_stats_parser.log")

logger = get_logger("time_model_stats_parser")  # or dynamic module name

def parse_time_model_stats(filepath):
    """Parse Time Model Statistics section from AWR report"""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    # Extract workload repository metadata
    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.warning("⚠️ Could not extract workload repository metadata.")
        return records

    # --- Locate Time Model Statistics header ---
    header = soup.find("h3", string=lambda t: t and "Time Model Statistics" in t)
    if not header:
        logger.warning("⚠️ Time Model Statistics section not found.")
        return records

    # --- Locate table after header ---
    table = header.find_next("table")
    if not table:
        logger.warning("⚠️ Time Model Statistics table missing.")
        return records

    df = pd.read_html(str(table))[0]

    # Expecting at least 3 columns: Statistic Name, Time (s), % of DB Time
    for _, row in df.iterrows():
        statistic_name = str(row[0]).strip()
        time_s = clean_number(row[1]) if len(row) > 1 else None
        pct_of_db_time = clean_number(row[2]) if len(row) > 2 else None

        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "statistic_name": statistic_name,
            "time_s": time_s,
            "pct_of_db_time": pct_of_db_time,
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Time Model Statistics records.")
    return records


def insert_time_model_stats(records):
    """Insert Time Model Statistics records into PostgreSQL"""
    if not records:
        logger.warning("⚠️ No records to insert into awr_time_model_stats")
        return

    insert_query = """
        INSERT INTO awr_time_model_stats
        (dbname, instance, instnum, snap_time, statistic_name,
         time_s, pct_of_db_time, begin_snap, row_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
    """

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for rec in records:
            cur.execute(insert_query, (
                rec["dbname"], rec["instance"], rec["instnum"],
                rec["snap_time"], rec["statistic_name"],
                rec["time_s"], rec["pct_of_db_time"],
                rec["begin_snap"], rec["row_hash"]
            ))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_time_model_stats")
    except Exception as e:
        logger.error(f"❌ Failed inserting Time Model Stats: {e}", exc_info=True)


if __name__ == "__main__":
    # When called via exec fallback from master_parser, 
    # `filepath` is injected into globals
    _target = globals().get("filepath") or locals().get("filepath")
    if not _target:
        import sys
        _target = sys.argv[1] if len(sys.argv) > 1 else None
    if _target:
        records = parse_time_model_stats(_target)
        insert_time_model_stats(records)
    else:
        logger.error("No filepath provided")
