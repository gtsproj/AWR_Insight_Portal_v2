# common/utils.py

import hashlib
import pandas as pd
import io


def row_hash(values):
    m = hashlib.md5()
    concat = "|".join([str(v) if v is not None else "" for v in values])
    m.update(concat.encode("utf-8"))
    return m.hexdigest()


def extract_common_metadata(soup):
    """
    Extract dbname, instance, instnum, snap_time, begin_snap
    from the DB Info, Instance Info, and Snapshot Info tables.
    """
    metadata = {
        "dbname": None,
        "instance": None,
        "instnum": None,
        "snap_time": None,
        "begin_snap": None,
    }

    # --- All tables with the same summary text ---
    db_instance_tables = soup.find_all(
        "table", summary="This table displays database instance information"
    )

    if db_instance_tables:
        # First table → DB info
        db_table = db_instance_tables[0]
        df_db = pd.read_html(io.StringIO(str(db_table)))[0]
        if "DB Name" in df_db.columns:
            metadata["dbname"] = str(df_db["DB Name"].iloc[0]).strip()

        # Second table → Instance info
        if len(db_instance_tables) > 1:
            inst_table = db_instance_tables[1]
            df_inst = pd.read_html(io.StringIO(str(inst_table)))[0]
            if "Instance" in df_inst.columns and "Inst Num" in df_inst.columns:
                metadata["instance"] = str(df_inst["Instance"].iloc[0]).strip()
                metadata["instnum"] = int(df_inst["Inst Num"].iloc[0])

    # --- Snapshot Info ---
    snap_table = soup.find("table", summary="This table displays snapshot information")
    if snap_table:
        df_snap = pd.read_html(io.StringIO(str(snap_table)))[0]
        for _, row in df_snap.iterrows():
            label = str(row[0]).strip().lower()
            if label.startswith("begin snap"):
                metadata["begin_snap"] = str(row[1]).strip()  # Snap Id
                metadata["snap_time"] = str(row[2]).strip()   # Full date + time

    return metadata


def clean_number(value):
    if pd.isna(value):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None
