import re
import io
import pandas as pd
from bs4 import BeautifulSoup

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
