# -----------------------------
# pga_memory_advisory_parser.py
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
log_file = os.path.join(LOG_DIR, "pga_memory_advisory_parser.log")

logger = get_logger("pga_memory_advisory")  # or dynamic module name

def parse_pga_memory_advisory(filepath):
    """Parse PGA Memory Advisory section from an AWR HTML report."""
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="PGA Memory Advisory")
    if not section:
        logger.warning("⚠️ PGA Memory Advisory section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ PGA Memory Advisory table not found.")
        return []

    df = pd.read_html(str(table))[0]

    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "pga_target_est_mb": clean_number(row["PGA Target Est (MB)"]),
            "size_factor": clean_number(row["Size Factr"]),
            "w_a_mb_processed": clean_number(row["W/A MB Processed"]),
            "estd_extra_written_to_disk": clean_number(row["Estd Extra W/A MB Read/ Written to Disk"]),
            "estd_pga_cache_hit_pct": clean_number(row["Estd PGA Cache Hit %"]),
            "estd_pga_overalloc_count": clean_number(row["Estd PGA Overalloc Count"]),
            "estd_time": clean_number(row["Estd Time"]),            
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} PGA Memory Advisory records.")
    return records

def insert_pga_memory_advisory(records):
    """Insert PGA Memory Advisory records into PostgreSQL."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_advisory_pga")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_advisory_pga (
                dbname, instance, instnum, snap_time, pga_target_est_mb,
                size_factor, w_a_mb_processed, estd_extra_written_to_disk,
                estd_pga_cache_hit_pct,estd_pga_overalloc_count,estd_time,
                begin_snap, row_hash
            )
            VALUES (
            %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
            %(pga_target_est_mb)s, %(size_factor)s, %(w_a_mb_processed)s,
            %(estd_extra_written_to_disk)s, %(estd_pga_cache_hit_pct)s,
            %(estd_pga_overalloc_count)s, %(estd_time)s, %(begin_snap)s,%(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_advisory_pga")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate PGA Memory Advisory record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting PGA Memory Advisory: {e}", exc_info=True)
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
        records = parse_pga_memory_advisory(_target)
        insert_pga_memory_advisory(records)
    else:
        logger.error("No filepath provided")
