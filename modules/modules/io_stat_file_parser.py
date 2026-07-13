#modules/file_io_stats_parser.py

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
log_file = os.path.join(LOG_DIR, "file_io_stats_parser.log")

logger = get_logger("file_io_stats_parser")  # or dynamic module name

def parse_file_io_stats(filepath):
    """Parse the File IO Stats section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="File IO Stats")
    if not section:
        logger.warning("⚠️ File IO Stats section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ File IO Stats table not found.")
        return []

    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in File IO Stats section")

    if is_section_empty(df, "File IO Stats", "awr_file_io_stats"):
        sys.exit(0)  # Skip this section
    
    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "tablespace": str(row.get("Tablespace", "")).strip(),
            "filename": str(row.get("Filename", "")).strip(),
            "reads": clean_number(row.get("Reads", None)),
            "avg_read_sec": clean_number(row.get("Av Rds/s", None)),
            "avg_read_ms": clean_number(row.get("Av Rd(ms)", None)),
            "writes": clean_number(row.get("Writes", None)),
            "write_avg_sec": clean_number(row.get("Writes avg/s", None)),
            "buffer_waits": clean_number(row.get("Buffer Waits", None)),
            "avg_buffer_wait_ms": clean_number(row.get("Av Buf Wt(ms)", None)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} File IO Stats records")
    return records



def insert_file_io_stats(records):
    """Insert parsed File IO Stats into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_file_io_stats")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_file_io_stats (
                dbname, instance, instnum, snap_time,
                tablespace, filename, reads, avg_read_sec, avg_read_ms,
                writes, write_avg_sec,
                buffer_waits, avg_buffer_wait_ms,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(tablespace)s, %(filename)s, %(reads)s, %(avg_read_sec)s, %(avg_read_ms)s,
                %(writes)s, %(write_avg_sec)s,
                %(buffer_waits)s, %(avg_buffer_wait_ms)s,
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_file_io_stats")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate File IO Stats record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting File IO Stats: {e}", exc_info=True)
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
        records = parse_file_io_stats(_target)
        insert_file_io_stats(records)
    else:
        logger.error("No filepath provided")
