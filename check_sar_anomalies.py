# check_sar_anomalies.py
# Run this to get SAR anomaly data structure for dashboard design
import os, sys
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), "common"))
from db import get_db_connection

conn = get_db_connection()

print("\n=== sar_anomalies table structure ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name='sar_anomalies' 
        ORDER BY ordinal_position
    """)
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1]}")

print("\n=== Row counts by metric_source + severity ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT metric_source, severity, COUNT(*) 
        FROM sar_anomalies 
        GROUP BY metric_source, severity 
        ORDER BY metric_source, severity
    """)
    for r in cur.fetchall():
        print(f"  {r[0]:<20} {r[1]:<10} {r[2]}")

print("\n=== Sample rows (top 10 by deviation score) ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT hostname, metric_source, metric_name, object_name,
               severity, ROUND(z_score::numeric,3) AS z,
               ROUND(metric_value::numeric,4) AS val,
               ROUND(baseline_mean::numeric,4) AS mean,
               snap_time
        FROM sar_anomalies
        ORDER BY ABS(z_score) DESC
        LIMIT 10
    """)
    cols = [d[0] for d in cur.description]
    for r in cur.fetchall():
        print("  " + " | ".join(f"{c}={v}" for c,v in zip(cols,r)))

print("\n=== Distinct hostnames ===")
with conn.cursor() as cur:
    cur.execute("SELECT DISTINCT hostname FROM sar_anomalies ORDER BY hostname")
    for r in cur.fetchall():
        print(f"  {r[0]}")

print("\n=== Distinct metric_source values ===")
with conn.cursor() as cur:
    cur.execute("SELECT DISTINCT metric_source FROM sar_anomalies ORDER BY metric_source")
    for r in cur.fetchall():
        print(f"  {r[0]}")

conn.close()
