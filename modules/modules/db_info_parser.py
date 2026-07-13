# modules/db_info_parser.py

import os
import sys
import psycopg2
from bs4 import BeautifulSoup
import pandas as pd
from io import StringIO


# --- Add common folder to path ---
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "common"))
from db import get_db_connection
from config_loader import load_config
from utils import row_hash, extract_workload_repo_metadata, clean_number, convert_to_ms
from logger_utils import get_logger

# --- Logging setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "db_info_parser.log")

logger = get_logger("db_info_parser")  # or dynamic module name


# --- Reports directory ---
REPORTS_DIR = r"C:\awr_parser_project\awr_reports"


def insert_db_info(record):
    """Insert record into awr_db_info, skip duplicates using row_hash."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        record_hash = row_hash(record)

        insert_query = """
        INSERT INTO awr_db_info (
            db_name, edition, release, instance, rac, host_name,
            platform, cpu, cores, sockets, memory, row_hash
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (db_name, instance, row_hash) DO NOTHING
        """

        cur.execute(insert_query, (
            record["db_name"],
            record.get("edition"),
            record.get("release"),
            record["instance"],
            record.get("rac"),
            record.get("host_name"),
            record.get("platform"),
            record.get("cpu"),
            record.get("cores"),
            record.get("sockets"),
            record.get("memory"),
            record_hash
        ))

        if cur.rowcount > 0:
            logger.info(f"✅ Inserted DB Info for {record['db_name']} / {record['instance']}")
        else:
            logger.warning(f"⚠️ Skipped duplicate DB Info for {record['db_name']} / {record['instance']}")

        conn.commit()
        cur.close()

    except Exception as e:
        logger.error(f"❌ Insert failed for {record.get('db_name', 'UNKNOWN')} - {e}", exc_info=True)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def parse_awr_file(filepath):
    """Parse AWR HTML file and extract DB Info record."""
    logger.info(f"Parsing file: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml")

    record = {}

    # --- DB Info table ---
    db_table = soup.find("table", summary="This table displays database instance information")
    if db_table is not None:
        df = pd.read_html(StringIO(str(db_table)))[0]
        record["db_name"] = str(df.iloc[0, 0]).strip()
        record["edition"] = str(df.iloc[0, 4]).strip()
        record["release"] = str(df.iloc[0, 5]).strip()
        record["rac"] = str(df.iloc[0, 6]).strip()
        logger.debug(f"DB Info parsed: {record}")

    # --- Instance Info table ---
    inst_table = db_table.find_next("table") if db_table else None
    if inst_table is not None:
        df = pd.read_html(StringIO(str(inst_table)))[0]
        record["instance"] = str(df.iloc[0, 0]).strip()
        logger.debug(f"Instance Info parsed: {record['instance']}")

    # --- Host Info table ---
    host_table = soup.find("table", summary="This table displays host information")
    if host_table is not None:
        df = pd.read_html(StringIO(str(host_table)))[0]
        record["host_name"] = str(df.iloc[0, 0]).strip()
        record["platform"] = str(df.iloc[0, 1]).strip()
        record["cpu"] = int(str(df.iloc[0, 2]).strip())
        record["cores"] = int(str(df.iloc[0, 3]).strip())
        record["sockets"] = int(str(df.iloc[0, 4]).strip())
        record["memory"] = float(str(df.iloc[0, 5]).strip())
        logger.debug(f"Host Info parsed: {record}")

    logger.info(f"Parsed record: {record}")
    return record


if __name__ == "__main__":
    # When called via exec fallback from master_parser, 
    # `filepath` is injected into globals
    _target = globals().get("filepath") or locals().get("filepath")
    if not _target:
        import sys
        _target = sys.argv[1] if len(sys.argv) > 1 else None
    if _target:
        records = parse_awr_file(_target)
        insert_db_info(records)
    else:
        logger.error("No filepath provided")
