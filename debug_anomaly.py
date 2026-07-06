# debug_anomaly.py  v2
import os, sys
_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))
from db import get_db_connection

DBNAME   = "NEODBPRD"
INSTANCE = "neodbprd_1"
SNAP     = 116875

conn = get_db_connection()
print(f"\n{'='*60}")

# Step 1: what columns exist and have non-null data?
with conn.cursor() as cur:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'awr_foreground_wait_events'
        ORDER BY ordinal_position
    """)
    cols = [r[0] for r in cur.fetchall()]
    print(f"Columns in awr_foreground_wait_events:\n  {cols}")

# Step 2: show raw rows for this snap
with conn.cursor() as cur:
    cur.execute("""
        SELECT * FROM awr_foreground_wait_events
        WHERE dbname=%s AND begin_snap=%s
        LIMIT 3
    """, (DBNAME, SNAP))
    desc = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print(f"\nRaw rows for snap {SNAP} (first 3):")
    for row in rows:
        for col, val in zip(desc, row):
            print(f"  {col}: {val}")
        print()

# Step 3: check all numeric columns for non-null data
with conn.cursor() as cur:
    cur.execute("""
        SELECT event,
               pct_db_time, avg_wait_ms, total_waits,
               total_wait_time_s, max_wait_ms
        FROM awr_foreground_wait_events
        WHERE dbname=%s AND instance=%s AND begin_snap=%s
        AND pct_db_time IS NOT NULL
        ORDER BY pct_db_time DESC LIMIT 5
    """, (DBNAME, INSTANCE, SNAP))
    rows = cur.fetchall()
    print(f"Rows with non-null pct_db_time for snap {SNAP}: {len(rows)}")
    for r in rows:
        print(f"  {r}")

# Step 4: count nulls vs non-nulls
with conn.cursor() as cur:
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(pct_db_time) AS non_null_pct,
            COUNT(avg_wait_ms) AS non_null_avg_wait,
            COUNT(total_waits) AS non_null_total_waits
        FROM awr_foreground_wait_events
        WHERE dbname=%s AND instance=%s
    """, (DBNAME, INSTANCE))
    r = cur.fetchone()
    print(f"\nNull analysis across ALL snaps for {DBNAME}/{INSTANCE}:")
    print(f"  total rows       : {r[0]}")
    print(f"  non-null pct_db_time  : {r[1]}")
    print(f"  non-null avg_wait_ms  : {r[2]}")
    print(f"  non-null total_waits  : {r[3]}")

# Step 5: check instance values
with conn.cursor() as cur:
    cur.execute("""
        SELECT DISTINCT instance, COUNT(*) FROM awr_foreground_wait_events
        WHERE dbname=%s GROUP BY instance
    """, (DBNAME,))
    rows = cur.fetchall()
    print(f"\nDistinct instance values in awr_foreground_wait_events:")
    for r in rows:
        print(f"  '{r[0]}': {r[1]} rows")

conn.close()
print(f"{'='*60}\n")
