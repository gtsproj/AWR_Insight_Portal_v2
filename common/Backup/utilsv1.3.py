# common/utils.py

import re
import io
import hashlib
from datetime import datetime
import pandas as pd
from bs4 import BeautifulSoup

def clean_number(value):
    """
    Cleans numeric strings by removing commas, spaces, and converting K/M units.
    Returns float or None.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        value = value.replace(",", "").replace("\xa0", "").strip()
        if value.endswith("K"):
            return float(value[:-1]) * 1_000
        elif value.endswith("M"):
            return float(value[:-1]) * 1_000_000
        return float(value)
    except:
        return None

def parse_filename_metadata(filename):
    """
    Extract db_name, instance, and snapshot datetime from AWR filename.
    Expected format: awr_<db_name>_<...>_<instance>_<date>_<time>.html
    Example: awr_COLDBPRD_dbid_1_250618_1551.html
    """
    try:
        base = filename.replace("awr_", "").replace(".html", "")
        parts = base.split("_")
        db_name = parts[0]
        instance = int(parts[2])
        snap_date = parts[3]
        snap_start = parts[4]
        snap_dt = datetime.strptime(snap_date + snap_start, "%y%m%d%H%M")
        return db_name, instance, snap_dt
    except Exception as e:
        raise ValueError(f"Filename format not recognized: {filename} ({e})")

def parse_table_by_title(soup, title_keyword, return_all=False):
    """
    Finds <table>(s) near an <h2>/<h3> containing the given title.
    - return_all=False → return the first matching table as DataFrame
    - return_all=True  → return a list of DataFrames
    """
    header = soup.find(['h2', 'h3'], string=re.compile(title_keyword, re.I))
    if not header:
        return [] if return_all else None

    tables = []
    while header:
        table = header.find_next("table")
        if not table:
            break
        df = pd.read_html(io.StringIO(str(table)))[0]
        tables.append(df)
        if not return_all:
            return df
        header = header.find_next(['h2', 'h3'], string=re.compile(title_keyword, re.I))

    return tables if return_all else None

def row_hash(row):
    """
    Generate a stable hash for a row (tuple/list).
    Useful for idempotency when inserting into DB.
    """
    row_str = "|".join([str(x) if x is not None else "" for x in row])
    return hashlib.md5(row_str.encode("utf-8")).hexdigest()
