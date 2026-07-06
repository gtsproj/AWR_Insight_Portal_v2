# common/utils.py

import hashlib
import pandas as pd

def row_hash(record: dict) -> str:
    """Generate MD5 hash for a row dictionary (excluding None values)."""
    concat = "|".join([str(v) for v in record.values() if v is not None])
    return hashlib.md5(concat.encode("utf-8")).hexdigest()


def clean_number(value):
    """Convert numeric strings with commas to float or int. Returns None if invalid."""
    if pd.isna(value):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def extract_workload_repo_metadata(soup):
    """
    Extract db_name, instance, instnum, snap_time, and begin_snap
    from the workload repository section of the AWR HTML report.
    """
    metadata = {
        "dbname": None,
        "instance": None,
        "instnum": None,
        "snap_time": None,
        "begin_snap": None,
    }

    try:
        # --- DB Name ---
        db_table = soup.find("table", summary="This table displays database instance information")
        if db_table:
            df = pd.read_html(str(db_table))[0]
            metadata["db_name"] = str(df.iloc[0, 0]).strip()

        # --- Instance ---
        inst_table = soup.find("table", summary="This table displays database instance information", border="0", class_="tdiff")
        if inst_table:
            df = pd.read_html(str(inst_table))[0]
            metadata["instance"] = str(df.iloc[0, 0]).strip()
            metadata["instnum"] = str(df.iloc[0, 1]).strip()

        # --- Snapshot Info ---
        snap_table = soup.find("table", summary="This table displays snapshot information")
        if snap_table:
            df = pd.read_html(str(snap_table))[0]
            for _, row in df.iterrows():
                label = str(row.iloc[0]).strip().lower()
                if label.startswith("begin snap"):
                    metadata["begin_snap"] = str(row.iloc[1]).strip()
                    metadata["snap_time"] = str(row.iloc[2]).strip()  # Full date+time
                    break
    except Exception as e:
        print(f"⚠️ Failed to extract metadata: {e}")

    return metadata
