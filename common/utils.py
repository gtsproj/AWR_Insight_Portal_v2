# common/utils.py

import hashlib

import hashlib
import pandas as pd
import warnings
import math
 
# suppress pandas FutureWarnings globally
warnings.simplefilter(action="ignore", category=FutureWarning)

# --- Helper to compute row hash for uniqueness ---
def get_col(row, column_name, logger=None, context=""):
    """
    Look up a pandas Series/row value by column name, tolerating the AWR
    HTML quirks that cause exact-match KeyErrors:
      - non-breaking spaces (\\xa0) instead of regular spaces
      - leading/trailing whitespace
      - repeated internal whitespace collapsed differently

    Falls back to a normalized-whitespace match across the row's index if
    the exact key isn't found. Returns None (and logs a warning, if a
    logger is given) rather than raising, so one unexpected/renamed
    column degrades that single field instead of failing the whole file.
    """
    if column_name in row.index:
        return row[column_name]

    def _norm(s):
        return " ".join(str(s).replace("\xa0", " ").split()).strip().lower()

    target = _norm(column_name)
    for col in row.index:
        if _norm(col) == target:
            return row[col]

    if logger:
        logger.warning(f"⚠️ Column '{column_name}' not found{f' ({context})' if context else ''} — got columns: {list(row.index)}")
    return None



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

#---- convert to Megabytes ----
def convert_to_mb(value: str) -> float:
    """Convert Read/Write/Data per sec values into MB."""
    if not value:
        return None

    val = str(value).strip().replace(",", "").upper()

    try:
        if val.endswith("G"):
            return float(val.replace("G", "")) * 1024
        elif val.endswith("M"):
            return float(val.replace("M", ""))
        elif val.endswith("K"):
            return float(val.replace("K", "")) / 1024
        elif val == "0" or val == "0M":
            return 0
        else:
            return float(val)
    except Exception:
        return None

#---- convert to multiple of Thousand ----
def convert_to_thousand(value: str) -> float:
    """Convert Waits: Count values into number."""
    if not value:
        return None

    val = str(value).strip().replace(",", "").upper()

    try:
        if val.endswith("K"):
            return float(val.replace("K", "")) * 1000
        elif val.endswith("M"):
            return float(val.replace("M", "")) * 1000 * 1000
        elif val == "0" or val == "0K":            return 0
        else:
            return float(val)
    except Exception:
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
    Extract dbname, instance, instnum, begin_snap, snap_time from AWR HTML.

    Handles two Oracle AWR formats:
      Oracle 11g/12c (single instance table):
        → DB Name | DB Id | Instance | Inst num | Startup Time | Release | RAC
      Oracle 19c+ (two instance tables):
        → Table 1: DB Name | DB Id | Unique Name | Role | Edition | Release | RAC | CDB
        → Table 2: Instance | Inst Num | Startup Time | ...
    """
    from io import StringIO

    metadata = {
        "dbname":     None,
        "instance":   None,
        "instnum":    None,
        "begin_snap": None,
        "snap_time":  None,
    }

    try:
        # ── All tables with instance info summary ───────────────────────
        inst_tables = soup.find_all(
            "table",
            summary="This table displays database instance information"
        )

        if len(inst_tables) == 1:
            # Oracle 11g/12c: single table has DB Name + Instance + Inst Num
            df = pd.read_html(StringIO(str(inst_tables[0])))[0]
            if not df.empty:
                row = df.iloc[0]
                cols = [str(c).lower() for c in df.columns]
                metadata["dbname"] = str(row.iloc[0]).strip()
                # Instance at col 2, Inst num at col 3
                if len(row) > 2:
                    metadata["instance"] = str(row.iloc[2]).strip()
                if len(row) > 3:
                    try:
                        metadata["instnum"] = int(float(str(row.iloc[3]).strip()))
                    except Exception:
                        metadata["instnum"] = None

        elif len(inst_tables) >= 2:
            # Oracle 19c+: first table = DB-level, second = instance-level
            df1 = pd.read_html(StringIO(str(inst_tables[0])))[0]
            if not df1.empty:
                metadata["dbname"] = str(df1.iloc[0, 0]).strip()

            df2 = pd.read_html(StringIO(str(inst_tables[1])))[0]
            if not df2.empty:
                row2 = df2.iloc[0]
                cols2 = [str(c).lower() for c in df2.columns]
                # Find Instance and Inst Num columns by name
                for i, col in enumerate(cols2):
                    if col in ("instance",):
                        metadata["instance"] = str(row2.iloc[i]).strip()
                    if "inst num" in col or col == "inst_num":
                        try:
                            metadata["instnum"] = int(float(str(row2.iloc[i]).strip()))
                        except Exception:
                            metadata["instnum"] = None
                # Fallback: positional (Instance col 0, Inst Num col 1)
                if metadata["instance"] is None and len(row2) > 0:
                    metadata["instance"] = str(row2.iloc[0]).strip()
                if metadata["instnum"] is None and len(row2) > 1:
                    try:
                        metadata["instnum"] = int(float(str(row2.iloc[1]).strip()))
                    except Exception:
                        metadata["instnum"] = None

        # ── Begin Snap ID and Snap Time ─────────────────────────────────
        snap_table = soup.find(
            "table",
            summary="This table displays snapshot information"
        )
        if snap_table is not None:
            df = pd.read_html(StringIO(str(snap_table)))[0]
            for _, row in df.iterrows():
                label = str(row.iloc[0]).strip().lower()
                if "begin snap" in label:
                    snap_val = clean_number(row.iloc[1]) if len(row) > 1 else None
                    try:
                        metadata["begin_snap"] = int(snap_val) if snap_val is not None else None
                    except Exception:
                        metadata["begin_snap"] = None
                    snap_time_raw = str(row.iloc[2]).strip() if len(row) > 2 else None
                    if snap_time_raw and snap_time_raw.lower() not in ("nan", "none", ""):
                        metadata["snap_time"] = pd.to_datetime(
                            snap_time_raw, errors="coerce", dayfirst=True
                        )
                    break

    except Exception as e:
        print(f"[utils.extract_workload_repo_metadata] Failed: {e}")

    return metadata
