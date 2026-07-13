#modules/awr_seg_db_blk_chg_parser.py

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
log_file = os.path.join(LOG_DIR, "awr_seg_db_blk_chg_parser.log")

logger = get_logger("awr_seg_db_blk_chg_parser")  # or dynamic module name


def parse_awr_seg_db_blk_chg(filepath):
    """Parse the Segments by DB Blocks Changes section from AWR report."""
    records = []

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed extracting workload repo metadata")
        return []

    section = soup.find("h3", string="Segments by DB Blocks Changes")
    if not section:
        logger.warning("⚠️ Segments by DB Blocks Changes section not found.")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning("⚠️ Segments by DB Blocks Changes table not found.")
        return []

    df = pd.read_html(str(table))[0]
    logger.info(f"Found {len(df)} rows in Segments by DB Blocks Changes section")

    if is_section_empty(df, "Segments by DB Blocks Changes", "awr_seg_db_blk_chg"):
        sys.exit(0)  # Skip this section

    for _, row in df.iterrows():
        rec = {
            "dbname": metadata["dbname"],
            "instance": metadata["instance"],
            "instnum": metadata["instnum"],
            "snap_time": metadata["snap_time"],
            "begin_snap": metadata["begin_snap"],
            "owner": str(row.get("Owner", "")).strip(),
            "tablespace_name": str(row.get("Tablespace Name", "")).strip(),
            "object_name": str(row.get("Object Name", "")).strip(),
            "subobject_name": str(row.get("Subobject Name", "")).strip(),
            "obj_type": str(row.get("Obj. Type", "")).strip(),
            "db_block_changes": clean_number(row.get("DB Block Changes", None)),
            "pct_of_capture": clean_number(row.get("% of Capture", None)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ Parsed {len(records)} Segments by DB Blocks Changes records")
    return records


def insert_awr_seg_db_blk_chg(records):
    """Insert parsed Segments by DB Blocks Changes into DB."""
    if not records:
        logger.warning("⚠️ No records to insert into awr_seg_db_blk_chg")
        return

    #sanitize all records before insertion
    records = [sanitize_record(rec) for rec in records]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_seg_db_blk_chg (
                dbname, instance, instnum, snap_time,
                owner, tablespace_name, object_name, subobject_name, obj_type,
                db_block_changes, pct_of_capture,
                begin_snap, row_hash
            )
            VALUES (
                %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
                %(owner)s, %(tablespace_name)s, %(object_name)s, %(subobject_name)s, %(obj_type)s,
                %(db_block_changes)s, %(pct_of_capture)s,
                %(begin_snap)s, %(row_hash)s
            )
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        cur.executemany(insert_query, records)
        conn.commit()
        cur.close()
        logger.info(f"✅ Inserted {len(records)} rows into awr_seg_db_blk_chg")

    except psycopg2.errors.UniqueViolation:
        logger.warning("⚠️ Skipped duplicate Segments by DB Blocks Changes record")
        if conn:
            conn.rollback()

    except Exception as e:
        logger.error(f"❌ Failed inserting Segments by DB Blocks Changes: {e}", exc_info=True)
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
        records = parse_awr_seg_db_blk_chg(_target)
        insert_awr_seg_db_blk_chg(records)
    else:
        logger.error("No filepath provided")
