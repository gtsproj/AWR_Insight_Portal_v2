# test_single_snap.py - test detection for snap 116875 directly
import os, sys
_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))
from db import get_db_connection

DBNAME   = "NEODBPRD"
INSTANCE = "neodbprd_1"
SNAP     = 116875
MIN_SAMPLES = 2

conn = get_db_connection()

# Direct z-score test matching exactly what _detect_awr_table does
with conn.cursor() as cur:
    cur.execute("""
        SELECT event AS metric_name,
               AVG(pct_db_time) AS mean_val,
               STDDEV(pct_db_time) AS std_val,
               COUNT(*) AS sample_count
        FROM awr_foreground_wait_events
        WHERE dbname = %s
          AND instance = %s
          AND snap_time >= NOW() - INTERVAL '90 days'
          AND begin_snap < %s
        GROUP BY event
        HAVING COUNT(*) >= %s
        ORDER BY AVG(pct_db_time) DESC
        LIMIT 5
    """, (DBNAME, INSTANCE, SNAP, MIN_SAMPLES))
    rows = cur.fetchall()
    print(f"Primary baseline rows (snap_time >= NOW()-90days, begin_snap < {SNAP}): {len(rows)}")
    for r in rows:
        print(f"  {r[0]}: mean={r[1]}, stddev={r[2]}, n={r[3]}")

# The problem - snap_time is 2024-02-21, NOW() is 2026-06-24
# NOW() - 90 days = 2026-03-25 — ALL data is before this!
print(f"\nNOTE: snap_time in data is 2024-02-21")
print(f"NOW() - 90 days is approximately 2026-03-25")
print(f"So ALL AWR data falls OUTSIDE the 90-day window from today!")

# Fallback query (no snap_time filter)
with conn.cursor() as cur:
    cur.execute("""
        SELECT event AS metric_name,
               AVG(pct_db_time) AS mean_val,
               STDDEV(pct_db_time) AS std_val,
               COUNT(*) AS sample_count
        FROM awr_foreground_wait_events
        WHERE dbname = %s
          AND instance = %s
          AND begin_snap < %s
        GROUP BY event
        HAVING COUNT(*) >= 2
        ORDER BY AVG(pct_db_time) DESC
        LIMIT 5
    """, (DBNAME, INSTANCE, SNAP))
    rows = cur.fetchall()
    print(f"\nFallback baseline rows (no snap_time filter, begin_snap < {SNAP}): {len(rows)}")
    for r in rows:
        print(f"  {r[0]}: mean={r[1]}, stddev={r[2]}, n={r[3]}")

conn.close()
