# common/utils.py

import hashlib
import pandas as pd
import io
import pandas as pd
from bs4 import BeautifulSoup

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
    Given a BeautifulSoup object for an AWR HTML file, return dict:
      {
        "dbname": <str>,
        "edition": <str or None>,
        "release": <str or None>,
        "rac": <str or None>,
        "instance": <str>,
        "instnum": <int or None>,
        "snap_time": <pd.Timestamp or None>,   # full date+time
        "begin_snap": <int or None>
      }
    The function is defensive and returns None values when it cannot find items.
    """
    metadata = {
        "dbname": None,
        "edition": None,
        "release": None,
        "rac": None,
        "instance": None,
        "instnum": None,
        "snap_time": None,
        "begin_snap": None
    }

    # collect all tables
    tables = soup.find_all("table")

    # --- 1) Find DB info table (has header "DB Name") ---
    db_table = None
    for t in tables:
        th_texts = [th.get_text(strip=True).lower() for th in t.find_all("th")]
        if any("db name" == txt for txt in th_texts):
            db_table = t
            break

    if db_table:
        try:
            df = pd.read_html(io.StringIO(str(db_table)))[0]
            # Normalize column labels and row into a mapping
            cols = [str(c).strip().lower() for c in df.columns]
            if df.shape[0] >= 1:
                row = df.iloc[0]
                row_map = dict(zip(cols, [str(x).strip() if not pd.isna(x) else None for x in row.tolist()]))
                metadata["dbname"] = row_map.get("db name") or row_map.get("db_name")
                metadata["edition"] = row_map.get("edition")
                metadata["release"] = row_map.get("release")
                metadata["rac"] = row_map.get("rac")
        except Exception:
            pass

    # --- 2) Find Instance table (has header "Instance") ---
    inst_table = None
    for t in tables:
        th_texts = [th.get_text(strip=True).lower() for th in t.find_all("th")]
        if any("instance" == txt for txt in th_texts) and any("inst num" in txt for txt in th_texts):
            inst_table = t
            break
    # fallback: second table frequently contains instance info
    if not inst_table:
        # look for a table that has an "Instance" header at all
        for t in tables:
            th_texts = [th.get_text(strip=True).lower() for th in t.find_all("th")]
            if any("instance" == txt for txt in th_texts):
                inst_table = t
                break

    if inst_table:
        try:
            df2 = pd.read_html(io.StringIO(str(inst_table)))[0]
            if df2.shape[0] >= 1:
                row = df2.iloc[0]
                cols = [str(c).strip().lower() for c in df2.columns]
                row_map = dict(zip(cols, [x for x in row.tolist()]))
                # Instance name
                inst = row_map.get("instance")
                metadata["instance"] = str(inst).strip() if inst is not None and not pd.isna(inst) else None
                # instnum comes from the column labeled "Inst Num"
                instnum_val = row_map.get("inst num") or row_map.get("inst_num") or row_map.get("instnum")
                try:
                    metadata["instnum"] = int(str(instnum_val).strip()) if instnum_val is not None and str(instnum_val).strip() != "" else None
                except Exception:
                    metadata["instnum"] = None
        except Exception:
            pass

    # --- 3) Find Snap Info table (has header "Snap Id" or "Snap Time") ---
    #snap_table = None
    #for t in tables:
        #th_texts = [th.get_text(strip=True).lower() for th in t.find_all("th")]
        #if any("snap id" in txt for txt in th_texts) or any("snap time" in txt for txt in th_texts):
            #snap_table = t
            #break

    #if snap_table:
        #try:
            #df3 = pd.read_html(io.StringIO(str(snap_table)))[0]
            # iterate rows to find the Begin Snap row
            #for _, r in df3.iterrows():
                #first_cell = str(r.iloc[0]).strip().lower() if len(r) > 0 else ""
                #if first_cell.startswith("begin snap"):
                    # r.iloc[1] should be Snap Id, r.iloc[2] is Snap Time (full date+time)
                    #try:
                        #begin_snap = r.iloc[1]
                        #metadata["begin_snap"] = int(str(begin_snap).replace(",", "").strip()) if begin_snap is not None and str(begin_snap).strip() != "" else None
                    #except Exception:
                        #metadata["begin_snap"] = None
                    #try:
                        #snap_time_raw = r.iloc[2]
                        #if pd.isna(snap_time_raw):
                            #metadata["snap_time"] = None
                        #else:
                            # convert to pandas Timestamp (robust)
                            #metadata["snap_time"] = pd.to_datetime(str(snap_time_raw).strip(), dayfirst=False, errors="coerce")
                    #except Exception:
                        #metadata["snap_time"] = None
                    #break
        #except Exception:
            #pass

    # --- 3) Find Snap Info table (has header "Snap Id" or "Snap Time") ---
    #snap_table = None
    #for t in tables:
        #th_texts = [th.get_text(strip=True).lower() for th in t.find_all("th")]
        #if any("snap id" in txt for txt in th_texts) or any("snap time" in txt for txt in th_texts):
            #snap_table = t
            #break

    #if snap_table:
        #try:
            #df3 = pd.read_html(io.StringIO(str(snap_table)), header=0)[0]
            #for _, r in df3.iterrows():
                #label = str(r.iloc[0]).strip().lower()
                #if label.startswith("begin snap"):
                    # column 1 = Snap Id, column 2 = Snap Time
                    #snap_id_raw = r.iloc[1] if len(r) > 1 else None
                    #snap_time_raw = r.iloc[2] if len(r) > 2 else None

                    #if snap_id_raw:
                        #try:
                            #metadata["begin_snap"] = int(str(snap_id_raw).replace(",", "").strip())
                        #except Exception:
                            #metadata["begin_snap"] = None
                    #if snap_time_raw:
                        #metadata["snap_time"] = pd.to_datetime(
                            #str(snap_time_raw).strip(),
                            #dayfirst=False, errors="coerce"
                        #)
                    #break
        #except Exception:
            #pass

    if len(tables) > 3:
        df = pd.read_html(str(tables[3]))[0]
        for _, row in df.iterrows():
            label = str(row[0]).strip().lower().rstrip(":")  # normalize, remove colon
            if label.startswith("begin snap"):
                try:
                    metadata["begin_snap"] = int(str(row[1]).strip())
                except Exception:
                    metadata["begin_snap"] = None
                metadata["snap_time"] = str(row[2]).strip()  # keep full datetime
    
    return metadata
