#modules/foreground_wait_class_parser.py


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
log_file = os.path.join(LOG_DIR, "foreground_wait_class_parser.log")

logger = get_logger("foreground_wait_class_parser")  # or dynamic module name

def convert_to_ms(value_str):
    """Convert Avg wait values to milliseconds (standardization)"""
    if not value_str or pd.isna(value_str):
        return None

    try:
        value_str = str(value_str).strip().lower().replace(",", "")
        if value_str.endswith("ms"):
            return float(value_str.replace("ms", ""))
        elif value_str.endswith("us"):
            return float(value_str.replace("us", "")) / 1000.0
        elif value_str.endswith("ns"):
            return float(value_str.replace("ns", "")) / 1_000_000.0
        elif value_str.endswith("s"):
            return float(value_str.replace("s", "")) * 1000.0
        else:  # assume milliseconds if unit not present
            return float(value_str)
    except Exception:
        return None


def parse_foreground_wait_class(filepath):
    """Parse Foreground Wait Class section from AWR report"""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    # Extract workload repository metadata
    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.warning("⚠️ Could not extract workload repository metadata.")
        return records

    # --- Locate Foreground Wait Class header ---
    header = soup.find("h3", string=lambda t: t and "Foreground Wait Class" in t)
    if not header:
        logger.warning("⚠️ Foreground Wait Class section not found.")
        return records

    table = header.find_next("table")
    if not table:
        logger.warning("⚠️ Foreground Wait Class table missing.")
        return records

    df = pd.read_html(str(table))[0]

    if is_section_empty(df, "Foreground Wait Class", "awr_foreground_wait_class"):
        sys.exit(0)  # Skip this section

    for _, row in df.iterrows():
        try:
            wait_class = str(row[0]).strip()
            waits = clean_number(row[1]) if len(row) > 1 else None
            total_wait_time_s = clean_number(row[3]) if len(row) > 3 else None
            avg_wait_ms = convert_to_ms(row[4]) if len(row) > 4 else None
            pct_db_time = clean_number(row[5]) if len(row) > 5 else None
        except Exception:
            continue

        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "wait_class": wait_class,
            "waits": waits,
            "total_wait_time_s": total_wait_time_s,
            "avg_wait_ms": avg_wait_ms,
            "pct_db_time": pct_db_time,
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Foreground Wait Class records.")
    return records


def insert_foreground_wait_class(records):
    """Insert Foreground Wait Class records into PostgreSQL"""
    if not records:
        logger.warning("⚠️ No records to insert into awr_foreground_wait_class")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    insert_query = """
        INSERT INTO awr_foreground_wait_class
        (dbname, instance, instnum, snap_time, wait_class, waits,
         total_wait_time_s, avg_wait_ms, pct_db_time, begin_snap, row_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
    """

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for rec in records:
            cur.execute(insert_query, (
                rec["dbname"], rec["instance"], rec["instnum"], rec["snap_time"],
                rec["wait_class"], rec["waits"], rec["total_wait_time_s"],
                rec["avg_wait_ms"], rec["pct_db_time"], rec["begin_snap"], rec["row_hash"]
            ))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_foreground_wait_class")
    
    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate oreground Wait Class record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting oreground Wait Class: {e}", exc_info=True)
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
        records = parse_foreground_wait_class(_target)
        insert_foreground_wait_class(records)
    else:
        logger.error("No filepath provided")
