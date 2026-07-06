import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from psycopg2 import sql
import re
import traceback

# === DB connection settings (keep yours) ===
DB_SETTINGS = {
    "dbname": "postgres",
    "user": "postgres",
    "password": "Admin123",
    "host": "localhost",
    "port": "5432"
}

# === Section → Table mapping (explicit names you provided + common AWR variants) ===
SECTION_TABLE_MAP = {
    # Core DB info
    "DB Info": "awr_db_info",

    # Efficiency / Load / IO / Memory
    "Instance Efficiency": "awr_instance_efficiency",
    "Instance Efficiency Percentages": "awr_instance_efficiency",
    "Load Profile": "awr_load_profile",
    "IO Profile": "awr_io_profile",
    "Memory Statistics": "awr_memory_statistics",
    "Time Model Statistics": "awr_time_model_stats",

    # Waits
    "Foreground Wait Class": "awr_foreground_wait_class",
    "Foreground Wait Events": "awr_foreground_wait_events",
    "Background Wait Events": "awr_background_wait_events",
    "Buffer Wait Statistics": "awr_wait_statistics",   # you listed this
    "Wait Statistics": "awr_wait_statistics",          # fallback alias

    # SQL Ordered (handle via resolver too)
    "SQL Ordered": "awr_sql_stats",
    "SQL ordered by CPU Time": "awr_sql_stats",
    "SQL ordered by Elapsed Time": "awr_sql_stats",
    "SQL ordered by Executions": "awr_sql_stats",
    "SQL ordered by Gets": "awr_sql_stats",
    "SQL ordered by Parse Calls": "awr_sql_stats",
    "SQL ordered by Reads": "awr_sql_stats",
    "SQL ordered by User I/O Wait Time": "awr_sql_stats",
    "SQL ordered by Physical Reads (UnOptimized)": "awr_sql_stats",

    # Undo
    "Undo Statistics": "awr_undo_statistics",
    "Undo Segment Stats": "awr_undo_statistics",
    "Undo Segment Summary": "awr_undo_statistics",

    # Segment Statistics (many sections — resolver also handles prefix)
    "Segment Statistics": "awr_segment_statistics",
    "Segments by Buffer Busy Waits": "awr_segment_statistics",
    "Segments by CR Blocks Received": "awr_segment_statistics",
    "Segments by Current Blocks Received": "awr_segment_statistics",
    "Segments by DB Blocks Changes": "awr_segment_statistics",
    "Segments by Direct Physical Reads": "awr_segment_statistics",
    "Segments by Direct Physical Writes": "awr_segment_statistics",
    "Segments by Global Cache Buffer Busy": "awr_segment_statistics",
    "Segments by ITL Waits": "awr_segment_statistics",
    "Segments by Logical Reads": "awr_segment_statistics",
    "Segments by Optimized Reads": "awr_segment_statistics",
    "Segments by Physical Read Requests": "awr_segment_statistics",
    "Segments by Physical Reads": "awr_segment_statistics",
    "Segments by Physical Write Requests": "awr_segment_statistics",
    "Segments by Physical Writes": "awr_segment_statistics",
    "Segments by Row Lock Waits": "awr_segment_statistics",
    "Segments by Table Scans": "awr_segment_statistics",
    "Segments by UnOptimized Reads": "awr_segment_statistics",

    # IO Stats
    "IOStat by Function summary": "awr_iostat_function",
    "IOStat by Filetype summary": "awr_iostat_filetype",
    "IOStat by Function/Filetype summary": "awr_iostat_func_filetype",
    "Tablespace IO Stats": "awr_tablespace_io_stats",
    "File IO Stats": "awr_file_io_stats",

    # Advisory
    "SGA Target Advisory": "awr_advisory_sga",
    "Advisory SGA": "awr_advisory_sga",
    "PGA Memory Advisory": "awr_advisory_pga",
    "Advisory PGA": "awr_advisory_pga",

    # OS
    "Operating System Statistics": "awr_os_statistics",

    # RAC (mapped into awr_rac_statistics by design)
    "Global Cache Times (Immediate)": "awr_rac_statistics",
    "Global Cache Transfer (Immediate)": "awr_rac_statistics",

    # ASH style rollups
    "ASH Summary": "awr_ash_summary",
    "Top Foreground Events by Total Wait Time": "awr_ash_summary",
    "Top SQL with Top Events": "awr_ash_summary",
    "Top SQL with Top Row Sources": "awr_ash_summary",
    "Top Event P1/P2/P3 Values": "awr_ash_summary",
    "Top PL/SQL Procedures": "awr_ash_summary",
    "Top DB Objects": "awr_ash_summary",
}

# Optional flexible resolver for unseen variants
def resolve_table(section: str):
    """Return target table or None."""
    if section in SECTION_TABLE_MAP:
        return SECTION_TABLE_MAP[section]
    s = section.lower().strip()

    if s.startswith("sql ordered"):
        return "awr_sql_stats"
    if s.startswith("segments by"):
        return "awr_segment_statistics"
    if s.startswith("iostat by function/filetype"):
        return "awr_iostat_func_filetype"
    if s.startswith("iostat by function"):
        return "awr_iostat_function"
    if s.startswith("iostat by filetype"):
        return "awr_iostat_filetype"
    if s in ("advisory sga", "sga target advisory"):
        return "awr_advisory_sga"
    if s in ("advisory pga", "pga memory advisory"):
        return "awr_advisory_pga"
    return None

# === Column normalization ===
def normalize_column(col: str) -> str:
    col = str(col).strip().lower()
    col = col.replace("p1, p2, p3 values", "p1_p2_p3_values")
    # remove non-alphanum -> underscore, compress, trim
    col = re.sub(r"[^0-9a-z_]+", "_", col)
    col = re.sub(r"_+", "_", col).strip("_")
    # common aliases
    aliases = {
        "timestamp": "snap_time",
        "avg_wait_ms": "avg_wait_ms",
        "avg_wait": "avg_wait",  # will be dropped if not in target table
        "total_wait_time_s": "total_wait_time_s",
        "total_wait_time_sec": "total_wait_time_s",
        "obj_": "obj_num",
        "dataobj_": "dataobj_num",
        # common misspellings or alt names
        "sga_target_size_m": "sga_target_mb",
        "pga_target_est_mb": "pga_target_mb",
        "size_factr": "size_factor",
        "name_": "name",  # trailing underscore fix
    }
    return aliases.get(col, col)

def get_table_columns(cur, table_name: str):
    cur.execute("""
        SELECT LOWER(column_name)
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (table_name,))
    return [r[0] for r in cur.fetchall()]

def upload_to_postgres(csv_file):
    df = pd.read_csv(csv_file)

    if "section" not in df.columns:
        print("❌ No 'section' column found in CSV. Check parser output.")
        return

    conn = psycopg2.connect(**DB_SETTINGS)
    cur = conn.cursor()

    # iterate only sections actually present in CSV
    for section_name in sorted(df["section"].dropna().unique()):
        table = resolve_table(section_name)
        if not table:
            # If not mapped, skip gracefully
            print(f"⚠️ No target table mapping for section: {section_name}")
            continue

        section_df = df[df["section"] == section_name].copy()
        if section_df.empty:
            print(f"⚠️ No data for section {section_name}")
            continue

        # Drop 'section'
        if "section" in section_df.columns:
            section_df = section_df.drop(columns=["section"])

        # Normalize column names
        section_df.columns = [normalize_column(c) for c in section_df.columns]

        # Replace NaN with None to avoid psycopg2 NaN issues
        section_df = section_df.where(pd.notnull(section_df), None)

        # Intersect with actual table columns
        pg_cols = get_table_columns(cur, table)

        # If CSV has 'timestamp' we mapped it to 'snap_time' already.
        # Keep only columns that exist in the target table
        keep_cols = [c for c in section_df.columns if c in pg_cols]
        drop_cols = [c for c in section_df.columns if c not in pg_cols]

        if not keep_cols:
            print(f"❌ After normalization, no matching columns for table {table} from section '{section_name}'.")
            print(f"   CSV cols: {list(section_df.columns)}")
            print(f"   Table {table} cols: {pg_cols}")
            continue

        if drop_cols:
            print(f"ℹ️ Dropping extra CSV columns for {table}: {drop_cols}")

        section_df = section_df[keep_cols]

        # Build safe SQL
        cols_ident = [sql.Identifier(c) for c in keep_cols]
        insert_query = sql.SQL("INSERT INTO {table} ({fields}) VALUES %s").format(
            table=sql.Identifier(table),
            fields=sql.SQL(", ").join(cols_ident)
        )

        # Data rows
        values = [tuple(x) for x in section_df.to_numpy()]

        try:
            execute_values(cur, insert_query.as_string(cur), values)
            conn.commit()
            print(f"✅ Inserted {len(values)} rows into {table} (section: {section_name})")
        except Exception as e:
            print(f"❌ Failed inserting into {table}: {e}")
            conn.rollback()
            try:
                # Extra diagnostics: show column diffs clearly
                extra = [c for c in section_df.columns if c not in pg_cols]
                missing = [c for c in pg_cols if c not in section_df.columns]
                print(f"   ➡️ Expected (Postgres): {pg_cols}")
                print(f"   ➡️ Got (CSV normalized): {list(section_df.columns)}")
                if extra: print(f"   ⚠️ Extra CSV columns not in Postgres: {extra}")
                if missing: print(f"   ⚠️ Missing CSV columns expected by Postgres: {missing}")
            except Exception:
                pass
            traceback.print_exc()

    cur.close()
    conn.close()

if __name__ == "__main__":
    # Point to your parsed CSV output
    CSV_FILE = r"C:\awr_parser_project\parsed_output\parsed_awr_metrics.csv"
    upload_to_postgres(CSV_FILE)
