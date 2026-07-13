#modules/io_stat_by_func_filetype_summary_parser.py

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
from utils import row_hash, extract_workload_repo_metadata, clean_number, convert_to_ms, convert_to_thousand, convert_to_mb, is_section_empty, sanitize_record
from logger_utils import get_logger

# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "io_stat_by_func_filetype_summary_parser.log")

logger = get_logger("io_stat_by_func_filetype_summary_parser")  # or dynamic module name

def parse_io_stat_by_func_filetype_summary(filepath):
    """Parse IOStat by Function/Filetype summary section from an AWR HTML report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="IOStat by Function/Filetype summary")
    if not section:
        logger.warning("⚠️ IOStat by Function/Filetype summary section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ IOStat by Function/Filetype summary table not found.")
        return []

    df = pd.read_html(str(table))[0]

    if is_section_empty(df, "IOStat by Function/Filetype summary", "awr_iostat_func_filetype"):
        sys.exit(0)  # Skip this section

    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "function_file_name": str(row[0]).strip(),
            "reads_mb": convert_to_mb(str(row[1])),
            "reads_reqs_per_sec": clean_number(row[2]),
            "reads_data_per_sec_mb": convert_to_mb(str(row[3])),
            "writes_mb": convert_to_mb(str(row[4])),
            "writes_reqs_per_sec": clean_number(row[5]),
            "writes_data_per_sec_mb": convert_to_mb(str(row[6])),
            "waits": convert_to_thousand(str(row[7])),
            "avg_time_ms": convert_to_ms(str(row[8]))
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} IOStat by Function/Filetype summary records.")
    return records


def insert_io_stat_by_func_filetype_summary(records):
    """Insert IOStat by Function/Filetype summary records into PostgreSQL."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_iostat_func_filetype")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_iostat_func_filetype (
                dbname, instance, instnum, snap_time, function_file_name,
                reads_mb, reads_reqs_per_sec, reads_data_per_sec_mb, writes_mb,
                writes_reqs_per_sec, writes_data_per_sec_mb, waits, avg_time_ms, 
                begin_snap, row_hash
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        for rec in records:
            cur.execute(insert_query, (
                rec["dbname"], rec["instance"], rec["instnum"], rec["snap_time"],
                rec["function_file_name"], rec["reads_mb"], rec["reads_reqs_per_sec"],
                rec["reads_data_per_sec_mb"], rec["writes_mb"], rec["writes_reqs_per_sec"],
                rec["writes_data_per_sec_mb"], rec["waits"], rec["avg_time_ms"],                
                rec["begin_snap"], rec["row_hash"],
            ))

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_iostat_func_filetype")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate IOStat by Function/Filetype summary record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting IOStat by Function/Filetype summary: {e}", exc_info=True)
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
        records = parse_io_stat_by_func_filetype_summary(_target)
        insert_io_stat_by_func_filetype_summary(records)
    else:
        logger.error("No filepath provided")
