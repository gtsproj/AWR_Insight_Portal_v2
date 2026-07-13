    #modules/sql_physical_reads_unopt_parser.py

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
log_file = os.path.join(LOG_DIR, "sql_physical_reads_unopt_parser.log")

logger = get_logger("sql_physical_reads_unopt_parser")  # or dynamic module name

def parse_sql_physical_reads_unopt(filepath):
    """Parse the SQL ordered by Physical Reads (UnOptimized) section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="SQL ordered by Physical Reads (UnOptimized)")
    if not section:
        logger.warning("⚠️ SQL ordered by Physical Reads (UnOptimized) section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ SQL ordered by Physical Reads (UnOptimized) table not found.")
        return []

    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in SQL Physical Reads (UnOptimized)")

    if is_section_empty(df, "SQL ordered by Physical Reads (UnOptimized)", "awr_sql_phy_reads_unopt"):
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
            "unoptimized_read_reqs": clean_number(row.get("UnOptimized Read Reqs")),
            "physical_read_reqs": clean_number(row.get("Physical Read Reqs")),
            "unoptimized_reqs_per_exec": clean_number(row.get("UnOptimized Reqs per Exec")),
            "pctopt": clean_number(row.get("%Opt")),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} SQL Physical Reads (UnOptimized) records")
    return records


def insert_sql_physical_reads_unopt(records):
    """Insert parsed SQL Physical Reads (UnOptimized) stats into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_sql_phy_reads_unopt")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_sql_phy_reads_unopt (
                dbname, instance, instnum, snap_time,
                sql_id, sql_module, executions, pct_total,                 
                unoptimized_read_reqs, physical_read_reqs, unoptimized_reqs_per_exec,
                pctopt,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s, 
                %(sql_id)s, %(sql_module)s, %(executions)s, %(pct_total)s, 
                %(unoptimized_read_reqs)s, %(physical_read_reqs)s, %(unoptimized_reqs_per_exec)s,
                %(pctopt)s, 
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_sql_phy_reads_unopt")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate SQL ordered by Physical Reads (UnOptimized) record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting SQL ordered by Physical Reads (UnOptimized): {e}", exc_info=True)
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
        records = parse_sql_physical_reads_unopt(_target)
        insert_sql_physical_reads_unopt(records)
    else:
        logger.error("No filepath provided")
