# modules/awr/generic_segment_parser.py
# ============================================================
# Replaces 17 near-identical awr_seg_*_parser.py files.
#
# Each AWR segment section shares the same structure:
#   - h3 heading with the section name
#   - table with: Owner, Tablespace Name, Object Name,
#                 Subobject Name, Obj. Type, <metric>, %Total/%Capture
#
# Adding a new segment section = add ONE entry to SEGMENT_PARSERS.
# No new Python file required.
#
# USAGE (standalone):
#   python generic_segment_parser.py /path/to/awr.html
#   python generic_segment_parser.py /path/to/awr.html --section "Segments by Logical Reads"
#
# CALLED BY master_parser via main(filepath):
#   from generic_segment_parser import main
#   main("/path/to/awr.html")
# ============================================================

import os
import sys
import warnings
import argparse
import pandas as pd
from bs4 import BeautifulSoup
from io import StringIO

warnings.simplefilter(action="ignore", category=FutureWarning)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from db import get_db_connection
from utils import row_hash, extract_workload_repo_metadata, clean_number, is_section_empty, sanitize_record
from logger_utils import get_logger

logger = get_logger("generic_segment_parser")


# ══════════════════════════════════════════════════════════════
#  SEGMENT PARSER CONFIGURATION
#  Each entry defines one AWR segment section.
#
#  Fields:
#    section_heading : exact text of the <h3> tag in the AWR HTML
#    table_name      : PostgreSQL target table
#    metric_col      : DB column name for the primary metric
#    metric_html_col : Column header in the AWR HTML table
#    pct_col         : DB column name for the % column
#    pct_html_col    : Column header for the % column in AWR HTML
# ══════════════════════════════════════════════════════════════
SEGMENT_PARSERS = [
    {
        "section_heading": "Segments by Logical Reads",
        "table_name":      "awr_seg_logical_reads",
        "metric_col":      "logical_reads",
        "metric_html_col": "Logical Reads",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Physical Reads",
        "table_name":      "awr_seg_phy_reads",
        "metric_col":      "physical_reads",
        "metric_html_col": "Physical Reads",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Physical Read Requests",
        "table_name":      "awr_seg_phy_read_req",
        "metric_col":      "phys_read_requests",
        "metric_html_col": "Phys Read Requests",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Physical Writes",
        "table_name":      "awr_seg_phy_writes",
        "metric_col":      "physical_writes",
        "metric_html_col": "Physical Writes",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Physical Write Requests",
        "table_name":      "awr_seg_phy_write_req",
        "metric_col":      "phys_write_requests",
        "metric_html_col": "Phys Write Requests",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Direct Physical Reads",
        "table_name":      "awr_seg_direct_phy_reads",
        "metric_col":      "direct_reads",
        "metric_html_col": "Direct Reads",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Direct Physical Writes",
        "table_name":      "awr_seg_direct_phy_writes",
        "metric_col":      "direct_writes",
        "metric_html_col": "Direct Writes",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Buffer Busy Waits",
        "table_name":      "awr_seg_buff_busy_waits",
        "metric_col":      "buffer_busy_waits",
        "metric_html_col": "Buffer Busy Waits",
        "pct_col":         "pct_of_capture",
        "pct_html_col":    "% of Capture",
    },
    {
        "section_heading": "Segments by Row Lock Waits",
        "table_name":      "awr_seg_row_lck_waits",
        "metric_col":      "row_lock_waits",
        "metric_html_col": "Row Lock Waits",
        "pct_col":         "pct_of_capture",
        "pct_html_col":    "% of Capture",
    },
    {
        "section_heading": "Segments by ITL Waits",
        "table_name":      "awr_seg_itl_waits",
        "metric_col":      "itl_waits",
        "metric_html_col": "ITL Waits",
        "pct_col":         "pct_of_capture",
        "pct_html_col":    "% of Capture",
    },
    {
        "section_heading": "Segments by CR Blocks Received",
        "table_name":      "awr_seg_cr_blk_rec",
        "metric_col":      "cr_blocks_received",
        "metric_html_col": "CR Blocks Received",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Current Blocks Received",
        "table_name":      "awr_seg_cur_blk_rec",
        "metric_col":      "current_blocks_received",
        "metric_html_col": "Current Blocks Received",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Global Cache Buffer Busy",
        "table_name":      "awr_seg_gbl_cache_buff_busy",
        "metric_col":      "gc_buffer_busy",
        "metric_html_col": "GC Buffer Busy",
        "pct_col":         "pct_of_capture",
        "pct_html_col":    "% of Capture",
    },
    {
        "section_heading": "Segments by DB Blocks Changes",
        "table_name":      "awr_seg_db_blk_chg",
        "metric_col":      "db_block_changes",
        "metric_html_col": "DB Block Changes",
        "pct_col":         "pct_of_capture",
        "pct_html_col":    "% of Capture",
    },
    {
        "section_heading": "Segments by Table Scans",
        "table_name":      "awr_seg_table_scan",
        "metric_col":      "table_scans",
        "metric_html_col": "Table Scans",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    {
        "section_heading": "Segments by Optimized Reads",
        "table_name":      "awr_seg_opt_reads",
        "metric_col":      "optimized_reads",
        "metric_html_col": "Optimized Reads",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
    # ── RAC-specific segment section ────────────────────────────
    {
        "section_heading": "Segments by Global Cache CR Blocks Received",
        "table_name":      "awr_seg_gc_cr_blk_rec",
        "metric_col":      "gc_cr_blocks_received",
        "metric_html_col": "GC CR Blocks Received",
        "pct_col":         "pcttotal",
        "pct_html_col":    "%Total",
    },
]

# Build a quick-lookup dict: section_heading → config
_PARSER_MAP = {p["section_heading"]: p for p in SEGMENT_PARSERS}


# ── common base columns present in every segment table ────────────────
_BASE_COLS = [
    "dbname", "instance", "instnum", "snap_time",
    "owner", "tablespace_name", "object_name", "subobject_name",
    "obj_type", "begin_snap", "row_hash",
]


def parse_segment_section(soup: BeautifulSoup, metadata: dict, cfg: dict) -> list:
    """
    Parse one segment section from an already-loaded BeautifulSoup object.

    Args:
        soup     : Parsed AWR HTML
        metadata : dict from extract_workload_repo_metadata()
        cfg      : One entry from SEGMENT_PARSERS

    Returns:
        List of record dicts ready for insert.
    """
    section_heading = cfg["section_heading"]
    records = []

    section = soup.find("h3", string=section_heading)
    if not section:
        # Try case-insensitive / partial match for version tolerance
        section = soup.find(
            "h3",
            string=lambda t: t and section_heading.lower() in t.lower()
        )
    if not section:
        logger.warning(f"Section not found: {section_heading!r}")
        return []

    table = section.find_next("table")
    if not table:
        logger.warning(f"Table not found after section: {section_heading!r}")
        return []

    try:
        df = pd.read_html(StringIO(str(table)))[0]
    except Exception as e:
        logger.error(f"pandas read_html failed for {section_heading!r}: {e}")
        return []

    if is_section_empty(df, section_heading, cfg["table_name"]):
        return []

    metric_col      = cfg["metric_col"]
    metric_html_col = cfg["metric_html_col"]
    pct_col         = cfg["pct_col"]
    pct_html_col    = cfg["pct_html_col"]

    for _, row in df.iterrows():
        rec = {
            "dbname":           metadata["dbname"],
            "instance":         metadata["instance"],
            "instnum":          metadata["instnum"],
            "snap_time":        metadata["snap_time"],
            "begin_snap":       metadata["begin_snap"],
            "owner":            str(row.get("Owner", "")).strip(),
            "tablespace_name":  str(row.get("Tablespace Name", "")).strip(),
            "object_name":      str(row.get("Object Name", "")).strip(),
            "subobject_name":   str(row.get("Subobject Name", "")).strip(),
            "obj_type":         str(row.get("Obj. Type", "")).strip(),
            metric_col:         clean_number(row.get(metric_html_col)),
            pct_col:            clean_number(row.get(pct_html_col)),
        }
        rec["row_hash"] = row_hash(rec)
        records.append(rec)

    logger.info(f"✅ {section_heading}: {len(records)} rows")
    return records


def insert_segment_records(records: list, cfg: dict) -> None:
    """
    Insert records for one segment section into its target table.
    Uses executemany with ON CONFLICT DO NOTHING for deduplication.
    """
    if not records:
        return

    records = [sanitize_record(r) for r in records]

    table_name      = cfg["table_name"]
    metric_col      = cfg["metric_col"]
    pct_col         = cfg["pct_col"]

    # Build INSERT dynamically based on the metric + pct column names
    sql = f"""
        INSERT INTO {table_name} (
            dbname, instance, instnum, snap_time,
            owner, tablespace_name, object_name, subobject_name, obj_type,
            {metric_col}, {pct_col},
            begin_snap, row_hash
        )
        VALUES (
            %(dbname)s, %(instance)s, %(instnum)s, %(snap_time)s,
            %(owner)s, %(tablespace_name)s, %(object_name)s,
            %(subobject_name)s, %(obj_type)s,
            %({metric_col})s, %({pct_col})s,
            %(begin_snap)s, %(row_hash)s
        )
        ON CONFLICT (dbname, instance, begin_snap, row_hash) DO NOTHING
    """

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
        logger.info(f"Inserted {len(records)} rows → {table_name}")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Insert failed for {table_name}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()


def parse_all_segments(filepath: str) -> dict:
    """
    Parse all configured segment sections from a single AWR file.

    Returns:
        dict mapping section_heading → list of records
    """
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        soup = BeautifulSoup(f, "lxml")

    metadata = extract_workload_repo_metadata(soup)
    if not metadata or not metadata.get("dbname"):
        logger.error(f"Failed to extract metadata from {filepath}")
        return {}

    results = {}
    for cfg in SEGMENT_PARSERS:
        records = parse_segment_section(soup, metadata, cfg)
        if records:
            results[cfg["section_heading"]] = records

    return results


def insert_all_segments(results: dict) -> None:
    """Insert all parsed segment records into the database."""
    for section_heading, records in results.items():
        cfg = _PARSER_MAP.get(section_heading)
        if cfg:
            insert_segment_records(records, cfg)


# ── master_parser interface ────────────────────────────────────────────
def main(filepath: str) -> None:
    """
    Entry point called by master_parser.py.
    Parses all segment sections and inserts results.
    """
    logger.info(f"Generic segment parser: {os.path.basename(filepath)}")
    results = parse_all_segments(filepath)
    insert_all_segments(results)
    total = sum(len(r) for r in results.values())
    logger.info(f"Segment parsing complete — {len(results)} section(s), {total} total rows")


# ── standalone execution ───────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Generic AWR Segment Parser")
    p.add_argument("filepath", help="Path to AWR HTML report")
    p.add_argument(
        "--section", default=None,
        help=f"Run only this section. Available: {[c['section_heading'] for c in SEGMENT_PARSERS]}"
    )
    args = p.parse_args()

    if args.section:
        cfg = _PARSER_MAP.get(args.section)
        if not cfg:
            print(f"Unknown section: {args.section!r}")
            print("Available sections:")
            for c in SEGMENT_PARSERS:
                print(f"  {c['section_heading']}")
            sys.exit(1)
        with open(args.filepath, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f, "lxml")
        meta = extract_workload_repo_metadata(soup)
        recs = parse_segment_section(soup, meta, cfg)
        insert_segment_records(recs, cfg)
        print(f"Done: {len(recs)} rows inserted into {cfg['table_name']}")
    else:
        main(args.filepath)
