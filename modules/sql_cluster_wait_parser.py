    #modules/sql_cluster_wait_parser.py

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
log_file = os.path.join(LOG_DIR, "sql_cluster_wait_parser.log")

logger = get_logger("sql_cluster_wait_parser")  # or dynamic module name

def parse_sql_cluster_wait(filepath):
    """Parse the SQL ordered by Cluster Wait Time section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="SQL ordered by Cluster Wait Time")
    if not section:
        logger.warning("⚠️ SQL ordered by Cluster Wait Time section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ SQL ordered by Cluster Wait Time table not found.")
        return []

    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in SQL ordered by Cluster Wait Time section")

    if is_section_empty(df, "SQL ordered by Cluster Wait Time", "awr_sql_cluster_wait_time"):
        sys.exit(0)  # Skip this section
        
    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "sql_id": str(row.get("SQL Id", "")).strip(),
            "sql_module": str(row.get("SQL Module", "")).strip(),
            "executions": clean_number(row.get("Executions")),
            "pct_total": clean_number(row.get("%Total")),
            "pct_cpu": clean_number(row.get("%CPU")),
            "pct_io": clean_number(row.get("%IO")),
            "elapsed_time_s": clean_number(row.get("Elapsed Time(s)")),
            "pct_clu": clean_number(row.get("%Clu")), 
            "cluster_wait_time_s": clean_number(row.get("Cluster Wait Time (s)")),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} SQL Cluster Wait Time records")
    return records


def insert_sql_cluster_wait(records):
    """Insert SQL Cluster Wait time stats into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_sql_cluster_wait_time")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_sql_cluster_wait_time (
                dbname, instance, instnum, snap_time,
                sql_id, sql_module, executions, pct_total, pct_cpu, pct_io,
                elapsed_time_s, 
                pct_clu,cluster_wait_time_s,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(sql_id)s, %(sql_module)s, %(executions)s, %(pct_total)s, %(pct_cpu)s, %(pct_io)s,
                %(elapsed_time_s)s, 
                %(pct_clu)s, %(cluster_wait_time_s)s,
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_sql_cluster_wait_time")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate SQL Cluster Wait Time record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting SQL Cluster Wait Time: {e}", exc_info=True)
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
        records = parse_sql_cluster_wait(_target)
        insert_sql_cluster_wait(records)
    else:
        logger.error("No filepath provided")
