    #modules/sql_parse_calls_parser.py

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
log_file = os.path.join(LOG_DIR, "sql_parse_calls_parser.log")

logger = get_logger("sql_parse_calls_parser")  # or dynamic module name

def parse_sql_parse_calls(filepath):
    """Parse the SQL ordered by Parse Calls section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="SQL ordered by Parse Calls")
    if not section:
        logger.warning("⚠️ SQL ordered by Parse Calls section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ SQL ordered by Parse Calls table not found.")
        return []

    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in SQL Parse Calls section")

    if is_section_empty(df, "SQL ordered by Parse Calls", "awr_sql_parsed_calls"):
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
            "parse_calls": clean_number(row.get("Parse Calls")),
            "pct_total_parses": clean_number(row.get("% Total Parses")),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} SQL Parse Calls records")
    return records


def insert_parsed_calls(records):
    """Insert parsed Calls stats into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_sql_parsed_calls")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_sql_parsed_calls (
                dbname, instance, instnum, snap_time,
                sql_id, sql_module, executions, parse_calls, pct_total_parses,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(sql_id)s, %(sql_module)s, %(executions)s, %(parse_calls)s, %(pct_total_parses)s,
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_sql_stats")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate SQL ordered by Parse Calls record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting SQL ordered by Parse Calls: {e}", exc_info=True)
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
        records = parse_sql_parse_calls(_target)
        insert_parsed_calls(records)
    else:
        logger.error("No filepath provided")
