# common/utils.py

import re
from datetime import datetime
import io
import pandas as pd
from bs4 import BeautifulSoup

def clean_number(val):
    val = str(val).replace(",", "").replace("K", "e3").replace("M", "e6")
    try:
        return float(eval(val))
    except:
        return None

def parse_filename_metadata(filename):
    base = filename.replace("awr_", "").replace(".html", "")
    parts = base.split("_")
    db_name = parts[0]
    instance = int(parts[2])
    snap_date = parts[3]
    snap_start = parts[4]
    snap_dt = datetime.strptime(snap_date + snap_start, "%y%m%d%H%M")
    return db_name, instance, snap_dt

def clean_number(value):
    """Cleans numeric strings by removing commas and converting K/M units."""
    if isinstance(value, (int, float)):
        return value
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

def parse_table_by_title(soup, title_keyword):
    """Finds the first <table> near an <h3>/<h2> containing the given title."""
    header = soup.find(['h2', 'h3'], string=re.compile(title_keyword, re.I))
    if header:
        table = header.find_next("table")
        if table:
            return pd.read_html(io.StringIO(str(table)))[0]
    return None