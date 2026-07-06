#modules/instance_activity_statistics_parser.py

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
log_file = os.path.join(LOG_DIR, "instance_activity_statistics_parser.log")

logger = get_logger("instance_activity_statistics_parser")  # or dynamic module name

def parse_instance_activity_stats(filepath):
    """Parse the Key Instance Activity Stats section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="Key Instance Activity Stats")
    if not section:
        logger.warning("⚠️ Key Instance Activity Stats section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ Key Instance Activity Stats table not found.")
        return []

    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in Key Instance Activity Stats section")

    if is_section_empty(df, "Key Instance Activity Stats", "awr_instance_activity_stats"):
        sys.exit(0)  # Skip this section

    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "name": str(row.get("Statistic", "")).strip(),
            "statistic": str(row.get("Statistic", "")).strip(),
            "total": clean_number(row.get("Total", None)),
            "per_second": clean_number(row.get("per Second", None)),
            "per_trans": clean_number(row.get("per Trans", None)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Key Instance Activity Stats records")
    return records


def insert_instance_activity_stats(records):
    """Insert parsed Key Instance Activity Stats into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_instance_activity_stats")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_instance_activity_stats (
                dbname, instance, instnum, snap_time,
                name, statistic, total, per_second, per_trans,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(name)s, %(statistic)s, %(total)s, %(per_second)s, %(per_trans)s,
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_instance_activity_stats")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate Key Instance Activity Stats record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting Key Instance Activity Stats: {e}", exc_info=True)
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
        records = parse_instance_activity_stats(_target)
        insert_instance_activity_stats(records)
    else:
        logger.error("No filepath provided")
