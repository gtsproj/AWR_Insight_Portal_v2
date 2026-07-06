# modules\load_profile_parser.py

# modules/load_profile_parser.py
import os
import sys
import psycopg2
import pandas as pd
import io
from bs4 import BeautifulSoup



# ensure common utilities are importable
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "common"))
from db import get_db_connection
from config_loader import load_config
from utils import row_hash, extract_workload_repo_metadata, clean_number
from logger_utils import get_logger

# logs path
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "load_profile_parser.log")

logger = get_logger("load_profile_parser")  # or dynamic module name



def parse_load_profile(filepath):
    logger.info(f"Parsing file: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata or not metadata.get("dbname"):
        logger.warning("⚠️ Could not extract workload repository metadata.")
        return []

    # find load profile table by summary text (robust fallback to header detection)
    table = soup.find("table", summary="This table displays load profile")
    if not table:
        # fallback: look for a table with header cell like "Per Second"
        found = None
        for t in soup.find_all("table"):
            ths = [th.get_text(strip=True).lower() for th in t.find_all("th")]
            if any("per second" in x for x in ths):
                found = t
                break
        table = found

    if not table:
        logger.warning("⚠️ No Load Profile section found.")
        return []

    try:
        df = pd.read_html(io.StringIO(str(table)))[0]
    except Exception as e:
        logger.error("Failed to read Load Profile table", exc_info=True)
        return []

    records = []
    for _, row in df.iterrows():
        try:
            # use .iloc to avoid pandas Series position deprecation warnings
            metric_name = str(row.iloc[0]).strip().rstrip(":")
            record = {
                "dbname": metadata.get("dbname"),
                "instance": metadata.get("instance"),
                "instnum": metadata.get("instnum"),
                "snap_time": metadata.get("snap_time"),
                "begin_snap": metadata.get("begin_snap"),
                "metric": metric_name,
                "per_sec": clean_number(row.iloc[1]) if len(row) > 1 else None,
                "per_transaction": clean_number(row.iloc[2]) if len(row) > 2 else None
            }
            record["row_hash"] = row_hash(record)
            records.append(record)
        except Exception:
            logger.exception("Failed to parse a Load Profile row")

    logger.info(f"Parsed {len(records)} load profile rows.")
    return records


def insert_load_profile(records):
    if not records:
        logger.warning("⚠️ No data to insert for Load Profile.")
        return

    insert_query = """
        INSERT INTO awr_load_profile
          (dbname, instance, instnum, snap_time, metric,
           per_sec, per_transaction, begin_snap, row_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
    """

    conn = get_db_connection()
    cur = conn.cursor()
    inserted = 0
    for record in records:
        try:
            cur.execute(insert_query, (
                record["dbname"], record["instance"], record["instnum"],
                record["snap_time"], record["metric"], record["per_sec"],
                record["per_transaction"], record["begin_snap"], record["row_hash"]
            ))
            inserted += cur.rowcount
        except Exception:
            logger.exception("Failed inserting load profile record, rolling back")
            conn.rollback()

        if cur.rowcount > 0:
            logger.info(f"✅ Inserted Load Profile for {record['dbname']} / {record['instance']}")
        else:
            logger.warning(f"⚠️ Skipped duplicate Load Profile for {record['dbname']} / {record['instance']}")
            
    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"✅ Inserted {inserted} rows into awr_load_profile")

if __name__ == "__main__":
    # When called via exec fallback from master_parser, 
    # `filepath` is injected into globals
    _target = globals().get("filepath") or locals().get("filepath")
    if not _target:
        import sys
        _target = sys.argv[1] if len(sys.argv) > 1 else None
    if _target:
        records = parse_load_profile(_target)
        insert_load_profile(records)
    else:
        logger.error("No filepath provided")
