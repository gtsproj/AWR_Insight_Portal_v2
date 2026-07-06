# common/utils.py

import hashlib

import hashlib
import pandas as pd
import warnings
import math
 
# suppress pandas FutureWarnings globally
warnings.simplefilter(action="ignore", category=FutureWarning)

# --- Helper to compute row hash for uniqueness ---
def row_hash(record: dict) -> str:
    """
    Compute an MD5 hash from the concatenated values of a record dict.
    """
    concat = "|".join([str(record.get(k, "")) for k in sorted(record.keys())])
    return hashlib.md5(concat.encode("utf-8")).hexdigest()


# --- Clean numeric strings (handles commas, spaces, etc.) ---
def clean_number(value):
    """
    Convert AWR numeric strings like '1,230,065.1' into float/int.
    Returns None if not numeric.
    """
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None

#---- convert to milisecs ----
def convert_to_ms(value: str) -> float:
    """Convert Avg Wait values into milliseconds (ms)."""
    if not value or value.strip() == "" or value.strip() == "&#160;":
        return None
    val = value.strip().lower().replace(",", "")
    try:
        if val.endswith("us"):
            return float(val.replace("us", "")) / 1000
        elif val.endswith("ms"):
            return float(val.replace("ms", ""))
        elif val.endswith("s"):
            return float(val.replace("s", "")) * 1000
        else:
            return float(val)
    except ValueError:
        return None

# --- This function will clean any NaN, empty string, or malformed values in a dictionary before DB insert ---

def sanitize_record(record):
    """Replace NaN or empty values with None for safe DB insertion."""
    for k, v in record.items():
        if pd.isna(v) or v in ("", "nan", "NaN", "None"):
            record[k] = None
    return record

# --- This function will gracefully skip any section that doesnt have any records -----
def is_section_empty(table: pd.DataFrame, section_name: str, module_name: str) -> bool:
    """
    Check if the table is empty (no records or only headers). If empty, log a warning and return True.
    """
    if table.empty or table.dropna(how="all").empty:
        logger.warning(f"⚠️ {section_name} section not found.")
        logger.warning(f"⚠️ No records to insert into {module_name}")
        return True
    return False

# --- Extract DB metadata from workload repository header ---
def extract_workload_repo_metadata(soup):
    """
    Extract dbname, instance, instnum, begin_snap, snap_time
    from the WORKLOAD REPOSITORY header section.
    """
    metadata = {
        "dbname": None,
        "instance": None,
        "instnum": None,
        "begin_snap": None,
        "snap_time": None,
    }

    try:
        tables = soup.find_all("table", {"class": "tdiff"})

        # --- DB Name from first table ---
        if len(tables) > 0:
            df = pd.read_html(str(tables[0]))[0]
            if not df.empty:
                metadata["dbname"] = str(df.iloc[0, 0]).strip()

        # --- Instance & Inst Num from second table ---
        if len(tables) > 1:
            df = pd.read_html(str(tables[1]))[0]
            if not df.empty:
                metadata["instance"] = str(df.iloc[0, 0]).strip()
                try:
                    metadata["instnum"] = int(str(df.iloc[0, 1]).strip())
                except Exception:
                    metadata["instnum"] = None

        # --- Begin Snap & Snap Time from snapshot table (4th table) ---
        if len(tables) > 3:
            df = pd.read_html(str(tables[3]))[0]
            for _, row in df.iterrows():
                label = str(row[0]).strip().lower()

                if label.startswith("begin snap"):
                    snap_val = clean_number(row[1]) if len(row) > 1 else None
                    try:
                        metadata["begin_snap"] = int(snap_val) if snap_val is not None else None
                    except Exception:
                        metadata["begin_snap"] = None

                    snap_time_raw = str(row[2]).strip() if len(row) > 2 else None
                    if snap_time_raw:
                        metadata["snap_time"] = pd.to_datetime(
                            snap_time_raw, errors="coerce", dayfirst=True
                        )
                    else:
                        metadata["snap_time"] = None

    except Exception as e:
        print(f"[utils.extract_workload_repo_metadata] Failed to extract metadata: {e}")

    return metadata
