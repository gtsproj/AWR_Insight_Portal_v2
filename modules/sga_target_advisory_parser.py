# -----------------------------
# sga_target_advisory_parser.py
# -----------------------------
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
from utils import row_hash, extract_workload_repo_metadata, clean_number, convert_to_ms, sanitize_record
from logger_utils import get_logger



# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "sga_target_advisory_parser.log")

logger = get_logger("sga_target_advisory")  # or dynamic module name

def parse_sga_target_advisory(filepath):
    """Parse SGA Target Advisory section from an AWR HTML report."""
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="SGA Target Advisory")
    if not section:
        logger.warning("⚠️ SGA Target Advisory section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ SGA Target Advisory table not found.")
        return []

    df = pd.read_html(str(table))[0]

    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "sga_target_size_m": clean_number(row["SGA Target Size (M)"]),
            "sga_size_factor": clean_number(row["SGA Size Factor"]),
            "est_db_time_s": clean_number(row["Est DB Time (s)"]),
            "est_physical_reads": clean_number(row["Est Physical Reads"]),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} SGA Target Advisory records.")
    return records

def insert_sga_target_advisory(records):
    """Insert SGA Target Advisory records into PostgreSQL."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_advisory_sga")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_advisory_sga (
                dbname, instance, instnum, snap_time, sga_target_size_m,
                sga_size_factor, est_db_time_s, est_physical_reads,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(sga_target_size_m)s, %(sga_size_factor)s, %(est_db_time_s)s, 
                %(est_physical_reads)s, 
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """
        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_advisory_sga")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate SGA Target Memory record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting SGA Target Memory: {e}", exc_info=True)
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
        records = parse_sga_target_advisory(_target)
        insert_sga_target_advisory(records)
    else:
        logger.error("No filepath provided")
