#modules/instance_efficiency_parser.py

# modules/instance_efficiency_parser.py

import os
import re
import sys
import hashlib
import psycopg2
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime

# --- Import utils ---
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "common"))
from db import get_db_connection
from config_loader import load_config
from utils import row_hash, extract_workload_repo_metadata, clean_number
from logger_utils import get_logger

# ----------------- Logging -----------------
log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "instance_efficiency_parser.log")

logger = get_logger("instance_efficiency_parser")  # or dynamic module name


# ----------------- Helpers -----------------
def row_hash_func(row):
    h = hashlib.md5()
    h.update("|".join([str(x) for x in row]).encode("utf-8"))
    return h.hexdigest()

# ----------------- Parser -----------------
def parse_instance_efficiency(filepath):
    logger.info(f"🔍 Parsing Instance Efficiency section from {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata:
        logger.error("❌ Failed to extract workload repo metadata.")
        return []

    # --- Find Instance Efficiency section (flexible) ---
    section_header = soup.find(
        string=re.compile(r"Instance Efficiency Percentages", re.IGNORECASE)
    )
    if not section_header:
        logger.warning("Instance Efficiency section not found.")
        return []

    table = section_header.find_next("table") if hasattr(section_header, "find_next") else None
    if not table:
        logger.warning("No table found under Instance Efficiency section.")
        return []

    df = pd.read_html(str(table))[0]

    records = []
    for _, row in df.iterrows():
        # Each row may contain two metrics (left/right side)
        for i in [0, 2]:
            if i < len(row) and pd.notna(row[i]):
                metric = str(row[i]).strip().rstrip(":")
                value = clean_number(row[i + 1]) if i + 1 < len(row) else None
                rec = {
                    "dbname": metadata["dbname"],
                    "instance": metadata["instance"],
                    "instnum": metadata["instnum"],
                    "snap_time": metadata["snap_time"],
                    "metric": metric,
                    "value": value,
                    "begin_snap": metadata["begin_snap"],
                }
                rec["row_hash"] = row_hash_func(list(rec.values()))
                records.append(rec)

    logger.info(f"✅ Extracted {len(records)} Instance Efficiency rows")
    return records

# ----------------- DB Insert -----------------
def insert_instance_efficiency(records):
    if not records:
        logger.warning("No records to insert into awr_instance_efficiency")
        return

    DB_SETTINGS = {
        "dbname": "postgres",
        "user": "postgres",
        "password": "Admin123",
        "host": "localhost",
        "port": "5432"
    }

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        insert_query = """
            INSERT INTO awr_instance_efficiency
            (dbname, instance, instnum, snap_time, metric, value, begin_snap, row_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
        """

        inserted = 0
        for rec in records:
            try:
                cur.execute(insert_query, (
                    rec["dbname"], rec["instance"], rec["instnum"], rec["snap_time"],
                    rec["metric"], rec["value"], rec["begin_snap"], rec["row_hash"]
                ))
                inserted += 1
            except Exception as e:
                logger.error(f"❌ Insert failed: {e}")

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Inserted {inserted} rows into awr_instance_efficiency")

    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")

# ----------------- Main -----------------
if __name__ == "__main__":
    # When called via exec fallback from master_parser, 
    # `filepath` is injected into globals
    _target = globals().get("filepath") or locals().get("filepath")
    if not _target:
        import sys
        _target = sys.argv[1] if len(sys.argv) > 1 else None
    if _target:
        records = parse_instance_efficiency(_target)
        insert_instance_efficiency(records)
    else:
        logger.error("No filepath provided")
