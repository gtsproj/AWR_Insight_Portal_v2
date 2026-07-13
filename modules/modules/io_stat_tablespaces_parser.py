#modules/io_stat_tablespaces_parser.py

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
log_file = os.path.join(LOG_DIR, "io_stat_tablespaces_parser.log")

logger = get_logger("io_stat_tablespaces_parser")  # or dynamic module name

def parse_tablespace_io_stats(filepath):
    """Parse the Tablespace IO Stats section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="Tablespace IO Stats")
    if not section:
        logger.warning("⚠️ Tablespace IO Stats section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ Tablespace IO Stats table not found.")
        return []
        
    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in Tablespace IO Stats section")

    if is_section_empty(df, "Tablespace IO Stats", "awr_tablespace_io_stats"):
        sys.exit(0)  # Skip this section
        
    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "tablespace": str(row.get("Tablespace", "")).strip(),
            "reads": clean_number(row.get("Reads", None)),
            "avg_read_sec": clean_number(row.get("Av Rds/s", None)),
            "avg_read_ms": clean_number(row.get("Av Rd(ms)", None)),
            "writes": clean_number(row.get("Writes", None)),
            "write_avg_sec": clean_number(row.get("Writes avg/s", None)),
            "write_avg_ms": clean_number(row.get("Av Writes(ms)", None)),
            "buffer_waits": clean_number(row.get("Buffer Waits", None)),
            "avg_buffer_wait_ms": clean_number(row.get("Av Buf Wt(ms)", None)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Tablespace IO Stats records")
    return records


def insert_tablespace_io_stats(records):
    """Insert parsed Tablespace IO Stats into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_tablespace_io_stats")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_tablespace_io_stats (
                dbname, instance, instnum, snap_time,
                tablespace, reads, avg_read_sec, avg_read_ms,
                writes, write_avg_sec, write_avg_ms,
                buffer_waits, avg_buffer_wait_ms,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(tablespace)s, %(reads)s, %(avg_read_sec)s, %(avg_read_ms)s,
                %(writes)s, %(write_avg_sec)s, %(write_avg_ms)s,
                %(buffer_waits)s, %(avg_buffer_wait_ms)s,
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_tablespace_io_stats")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate Tablespace IO Stats record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting Tablespace IO Stats: {e}", exc_info=True)
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
        records = parse_tablespace_io_stats(_target)
        insert_tablespace_io_stats(records)
    else:
        logger.error("No filepath provided")
