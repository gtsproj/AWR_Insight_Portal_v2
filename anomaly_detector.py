# anomaly_detector.py  (v2)
# ============================================================
# AWR + SAR Anomaly Detection
#
# v2 changes:
#   - Two separate tables: awr_anomalies + sar_anomalies
#   - awr_anomalies : keyed on dbname + instance + begin_snap
#   - sar_anomalies : keyed on hostname + snap_time
#   - store_awr_anomalies() / store_sar_anomalies() split
#   - detect_all()  → stores to awr_anomalies
#   - detect_sar()  → stores to sar_anomalies
#   - detect()      → programmatic entry for master_parser
#
# USAGE:
#   python anomaly_detector.py --db COLDBPRD --snap 100 --store
#   python anomaly_detector.py --host TCLFSLPRDDB1 --from "2024-02-21 00:00" --store
# ============================================================

import os
import sys
import json
import argparse
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from config_loader import load_config
from db import get_db_connection
from logger_utils import get_logger

logger = get_logger("anomaly_detector")

_cfg          = load_config()
Z_THRESHOLD   = float(_cfg.get("anomaly", {}).get("z_threshold",  1.5))
BASELINE_DAYS = int(_cfg.get("anomaly",   {}).get("baseline_days", 90))
MIN_SAMPLES   = int(_cfg.get("anomaly",   {}).get("min_samples",   3))


# ══════════════════════════════════════════════════════════════
#  SCHEMA — two clean tables
# ══════════════════════════════════════════════════════════════
_AWR_ANOMALY_SCHEMA = """
CREATE TABLE IF NOT EXISTS awr_anomalies (
    id              SERIAL PRIMARY KEY,
    dbname          TEXT        NOT NULL,
    instance        TEXT        NOT NULL,
    begin_snap      INT         NOT NULL,
    snap_time       TIMESTAMP,
    metric_source   TEXT        NOT NULL,
    metric_name     TEXT        NOT NULL,
    object_name     TEXT,
    metric_value    NUMERIC,
    baseline_mean   NUMERIC,
    baseline_stddev NUMERIC,
    z_score         NUMERIC,
    severity        TEXT,
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_anomaly
        UNIQUE (dbname, instance, begin_snap, metric_source, metric_name, object_name)
) TABLESPACE awrparser;
CREATE INDEX IF NOT EXISTS idx_awr_anomaly_db_snap
    ON awr_anomalies (dbname, instance, begin_snap, severity);
CREATE INDEX IF NOT EXISTS idx_awr_anomaly_time
    ON awr_anomalies (snap_time DESC);
"""

_SAR_ANOMALY_SCHEMA = """
CREATE TABLE IF NOT EXISTS sar_anomalies (
    id              SERIAL PRIMARY KEY,
    hostname        TEXT        NOT NULL,
    snap_time       TIMESTAMP   NOT NULL,
    metric_source   TEXT        NOT NULL,
    metric_name     TEXT        NOT NULL,
    object_name     TEXT,
    metric_value    NUMERIC,
    baseline_mean   NUMERIC,
    baseline_stddev NUMERIC,
    z_score         NUMERIC,
    severity        TEXT,
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_anomaly
        UNIQUE (hostname, snap_time, metric_source, metric_name, object_name)
) TABLESPACE awrparser;
CREATE INDEX IF NOT EXISTS idx_sar_anomaly_host_time
    ON sar_anomalies (hostname, snap_time DESC, severity);
CREATE INDEX IF NOT EXISTS idx_sar_anomaly_severity
    ON sar_anomalies (severity, snap_time DESC);
"""


def ensure_schema(conn):
    """Create both anomaly tables. Drops and recreates awr_anomalies on v2 migration."""
    with conn.cursor() as cur:
        cur.execute(_AWR_ANOMALY_SCHEMA)
        cur.execute(_SAR_ANOMALY_SCHEMA)
    conn.commit()
    logger.info("Anomaly schema v2 ensured (awr_anomalies + sar_anomalies)")


# ══════════════════════════════════════════════════════════════
#  Z-SCORE HELPERS
# ══════════════════════════════════════════════════════════════
def _zscore(value, mean, stddev):
    if stddev is None or stddev == 0:
        return 0.0
    return (value - mean) / stddev


def _severity(z):
    az = abs(z)
    if az >= 4.0: return "critical"
    if az >= 3.0: return "alert"
    if az >= Z_THRESHOLD: return "warning"
    return None


# ══════════════════════════════════════════════════════════════
#  AWR DETECTION — dbname + instance + begin_snap
# ══════════════════════════════════════════════════════════════
def _detect_awr_table(conn, dbname, instance, begin_snap,
                      source, table, value_col, name_col) -> list:
    """
    Z-score detection for one AWR metric table.
    Baseline = 30-day rolling average for this dbname + instance.
    Current  = values at this begin_snap.
    """
    baseline_sql = f"""
        SELECT {name_col} AS metric_name,
               AVG({value_col})    AS mean_val,
               STDDEV({value_col}) AS std_val,
               COUNT(*)            AS sample_count
        FROM {table}
        WHERE dbname   = %(dbname)s
          AND instance = %(instance)s
          AND begin_snap < %(begin_snap)s
          AND snap_time >= (
              SELECT snap_time FROM awr_load_profile
              WHERE dbname = %(dbname)s AND instance = %(instance)s
                AND begin_snap = %(begin_snap)s LIMIT 1
          ) - INTERVAL '{BASELINE_DAYS} days'
        GROUP BY {name_col}
        HAVING COUNT(*) >= %(min_samples)s
    """
    # Fallback: if not enough samples in time window — use all prior snaps
    baseline_fallback_sql = f"""
        SELECT {name_col} AS metric_name,
               AVG({value_col})    AS mean_val,
               STDDEV({value_col}) AS std_val,
               COUNT(*)            AS sample_count
        FROM {table}
        WHERE dbname   = %(dbname)s
          AND instance = %(instance)s
          AND begin_snap < %(begin_snap)s
        GROUP BY {name_col}
        HAVING COUNT(*) >= 2
    """
    current_sql = f"""
        SELECT {name_col} AS metric_name,
               AVG({value_col}) AS val,
               MAX(snap_time)   AS snap_time
        FROM {table}
        WHERE dbname   = %(dbname)s
          AND instance = %(instance)s
          AND begin_snap = %(begin_snap)s
        GROUP BY {name_col}
    """
    params = {"dbname": dbname, "instance": instance,
              "begin_snap": begin_snap, "min_samples": MIN_SAMPLES}
    try:
        with conn.cursor() as cur:
            cur.execute(baseline_sql, params)
            cols     = [d[0] for d in cur.description]
            baseline = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}

            # Fallback: not enough samples in time window — use all prior snaps
            if not baseline:
                cur.execute(baseline_fallback_sql, params)
                cols     = [d[0] for d in cur.description]
                baseline = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}
                if baseline:
                    logger.debug(f"[{source}/{table}] fallback baseline: "
                                 f"{len(baseline)} metric(s)")

            cur.execute(current_sql, params)
            cols    = [d[0] for d in cur.description]
            current = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        conn.rollback()
        logger.warning(f"AWR anomaly check failed [{source}/{table}]: {e}")
        return []

    findings = []
    for row in current:
        name = row["metric_name"]
        val  = float(row.get("val") or 0)
        base = baseline.get(name)
        if not base:
            continue
        mean   = float(base["mean_val"] or 0)
        stddev = float(base["std_val"]  or 0)
        z      = _zscore(val, mean, stddev)
        sev    = _severity(z)
        if sev:
            findings.append({
                "dbname":          dbname,
                "instance":        instance,
                "begin_snap":      begin_snap,
                "snap_time":       row.get("snap_time"),
                "metric_source":   source,
                "metric_name":     value_col,
                "object_name":     str(name),
                "metric_value":    val,
                "baseline_mean":   mean,
                "baseline_stddev": stddev,
                "z_score":         round(z, 3),
                "severity":        sev,
            })
    return findings


def detect_all(dbname: str, begin_snap: int, instance: str = None) -> list:
    """
    Run AWR anomaly detection for one snap.
    Returns list of findings (not yet stored — call store_awr_anomalies() to persist).
    """
    conn = get_db_connection()

    # Resolve instance if not supplied
    if not instance:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT instance FROM awr_load_profile
                    WHERE dbname=%s AND begin_snap=%s LIMIT 1
                """, (dbname, begin_snap))
                row = cur.fetchone()
                instance = row[0] if row else dbname
        except Exception:
            instance = dbname

    awr_checks = [
        # (source_label, table, value_col, name_col)
        ("wait_event",  "awr_foreground_wait_events", "pct_db_time",    "event"),
        ("wait_class",  "awr_foreground_wait_class",  "pct_db_time",    "wait_class"),
        ("sql_elapsed", "awr_sql_elapsed_time",        "elapsed_time_s", "sql_id"),
        ("sql_cpu",     "awr_sql_cpu_time",            "cpu_per_exec_s", "sql_id"),
        ("segment_lr",  "awr_seg_logical_reads",       "logical_reads",  "object_name"),
        ("segment_pr",  "awr_seg_phy_reads",           "physical_reads", "object_name"),
    ]

    all_findings = []
    for source, table, value_col, name_col in awr_checks:
        findings = _detect_awr_table(conn, dbname, instance, begin_snap,
                                     source, table, value_col, name_col)
        all_findings.extend(findings)
        if findings:
            logger.info(f"  [{source}] {len(findings)} anomaly(ies) found")

    logger.info(f"AWR anomaly detection complete — {dbname}/{instance} "
                f"snap {begin_snap}: {len(all_findings)} finding(s)")
    conn.close()
    return all_findings


def store_awr_anomalies(findings: list) -> int:
    """Persist AWR anomaly findings to awr_anomalies table."""
    if not findings:
        return 0
    sql = """
        INSERT INTO awr_anomalies
            (dbname, instance, begin_snap, snap_time, metric_source, metric_name,
             object_name, metric_value, baseline_mean, baseline_stddev,
             z_score, severity)
        VALUES
            (%(dbname)s, %(instance)s, %(begin_snap)s, %(snap_time)s,
             %(metric_source)s, %(metric_name)s, %(object_name)s,
             %(metric_value)s, %(baseline_mean)s, %(baseline_stddev)s,
             %(z_score)s, %(severity)s)
        ON CONFLICT (dbname, instance, begin_snap, metric_source, metric_name, object_name)
        DO UPDATE SET
            z_score=EXCLUDED.z_score,
            severity=EXCLUDED.severity,
            metric_value=EXCLUDED.metric_value
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, findings)
        conn.commit()
        logger.info(f"Stored {len(findings)} AWR anomalies")
        return len(findings)
    except Exception as e:
        conn.rollback()
        logger.error(f"AWR anomaly store failed: {e}", exc_info=True)
        return 0
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
#  SAR DETECTION — hostname + snap_time range
# ══════════════════════════════════════════════════════════════
def _detect_sar_table(conn, hostname, snap_time_from, snap_time_to,
                      source, table, value_col, name_col) -> list:
    """
    Z-score detection for one SAR metric table.
    Baseline = 30-day rolling average for this hostname before snap_time_from.
    Current  = values between snap_time_from and snap_time_to.
    """
    baseline_sql = f"""
        SELECT {name_col}           AS metric_name,
               AVG({value_col})    AS mean_val,
               STDDEV({value_col}) AS std_val,
               COUNT(*)            AS sample_count
        FROM {table}
        WHERE hostname  = %(hostname)s
          AND snap_time >= %(snap_time_from)s - INTERVAL '{BASELINE_DAYS} days'
          AND snap_time <  %(snap_time_from)s
        GROUP BY {name_col}
        HAVING COUNT(*) >= %(min_samples)s
    """
    baseline_fallback_sql = f"""
        SELECT {name_col}           AS metric_name,
               AVG({value_col})    AS mean_val,
               STDDEV({value_col}) AS std_val,
               COUNT(*)            AS sample_count
        FROM {table}
        WHERE hostname  = %(hostname)s
          AND snap_time <  %(snap_time_from)s
        GROUP BY {name_col}
        HAVING COUNT(*) >= 2
    """
    current_sql = f"""
        SELECT {name_col}        AS metric_name,
               AVG({value_col}) AS val,
               MAX(snap_time)   AS snap_time
        FROM {table}
        WHERE hostname  = %(hostname)s
          AND snap_time BETWEEN %(snap_time_from)s AND %(snap_time_to)s
        GROUP BY {name_col}
    """
    params = {
        "hostname":       hostname,
        "snap_time_from": snap_time_from,
        "snap_time_to":   snap_time_to,
        "min_samples":    MIN_SAMPLES,
    }
    try:
        with conn.cursor() as cur:
            cur.execute(baseline_sql, params)
            cols     = [d[0] for d in cur.description]
            baseline = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}

            # Fallback: not enough samples in time window — use all prior data
            if not baseline:
                cur.execute(baseline_fallback_sql, params)
                cols     = [d[0] for d in cur.description]
                baseline = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}
                if baseline:
                    logger.debug(f"[{source}/{table}] fallback baseline: "
                                 f"{len(baseline)} metric(s)")

            cur.execute(current_sql, params)
            cols    = [d[0] for d in cur.description]
            current = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        conn.rollback()
        logger.warning(f"SAR anomaly check failed [{source}/{table}]: {e}")
        return []

    findings = []
    for row in current:
        name = row["metric_name"]
        val  = float(row.get("val") or 0)
        base = baseline.get(name)
        if not base:
            continue
        mean   = float(base["mean_val"] or 0)
        stddev = float(base["std_val"]  or 0)
        z      = _zscore(val, mean, stddev)
        sev    = _severity(z)
        if sev:
            findings.append({
                "hostname":        hostname,
                "snap_time":       row.get("snap_time"),
                "metric_source":   source,
                "metric_name":     value_col,
                "object_name":     str(name),
                "metric_value":    val,
                "baseline_mean":   mean,
                "baseline_stddev": stddev,
                "z_score":         round(z, 3),
                "severity":        sev,
            })
    return findings


def detect_sar(hostname: str, snap_time_from, snap_time_to=None,
               store: bool = False) -> list:
    """
    SAR anomaly detection entry point — called by sar_master_parser.
    Stores findings to sar_anomalies (not awr_anomalies).
    """
    if snap_time_to is None:
        snap_time_to = snap_time_from + timedelta(hours=25)

    conn = get_db_connection()

    sar_checks = [
        ("sar_cpu",    "sar_cpu_stats",     "iowait_pct",     "cpu"),
        ("sar_disk",   "sar_disk_stats",    "util_pct",       "device"),
        ("sar_mem",    "sar_memory_stats",  "mem_used_pct",   "hostname"),
        ("sar_paging", "sar_paging_stats",  "majflt_per_sec", "hostname"),
        ("sar_load",   "sar_loadavg_stats", "runq_sz",        "hostname"),
        ("sar_net",    "sar_network_stats", "ifutil_pct",     "iface"),
    ]

    all_findings = []
    for source, table, value_col, name_col in sar_checks:
        findings = _detect_sar_table(conn, hostname, snap_time_from,
                                     snap_time_to, source, table,
                                     value_col, name_col)
        all_findings.extend(findings)
        if findings:
            logger.info(f"  [{source}] {len(findings)} SAR anomaly(ies) found")

    conn.close()

    if store and all_findings:
        store_sar_anomalies(all_findings)

    logger.info(f"SAR anomaly detection complete for {hostname}: "
                f"{len(all_findings)} finding(s)")
    return all_findings


def store_sar_anomalies(findings: list) -> int:
    """Persist SAR anomaly findings to sar_anomalies table."""
    if not findings:
        return 0
    sql = """
        INSERT INTO sar_anomalies
            (hostname, snap_time, metric_source, metric_name, object_name,
             metric_value, baseline_mean, baseline_stddev, z_score, severity)
        VALUES
            (%(hostname)s, %(snap_time)s, %(metric_source)s, %(metric_name)s,
             %(object_name)s, %(metric_value)s, %(baseline_mean)s,
             %(baseline_stddev)s, %(z_score)s, %(severity)s)
        ON CONFLICT (hostname, snap_time, metric_source, metric_name, object_name)
        DO UPDATE SET
            z_score=EXCLUDED.z_score,
            severity=EXCLUDED.severity,
            metric_value=EXCLUDED.metric_value
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, findings)
        conn.commit()
        logger.info(f"Stored {len(findings)} SAR anomalies")
        return len(findings)
    except Exception as e:
        conn.rollback()
        logger.error(f"SAR anomaly store failed: {e}", exc_info=True)
        return 0
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
#  PROGRAMMATIC ENTRY POINTS (called by master_parser / sar_master_parser)
# ══════════════════════════════════════════════════════════════
def detect(db_name: str, snap: int, store: bool = False,
           instance: str = None) -> list:
    """Called by master_parser._run_anomaly_detection() per snap."""
    findings = detect_all(db_name, snap, instance)
    if store:
        store_awr_anomalies(findings)
    return findings


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(description="AWR/SAR Anomaly Detector v2")
    sub = p.add_subparsers(dest="mode")

    # AWR mode
    awr = sub.add_parser("awr", help="AWR anomaly detection")
    awr.add_argument("--db",    required=True,       help="Database name")
    awr.add_argument("--snap",  required=True, type=int, help="Snap ID")
    awr.add_argument("--inst",  default=None,         help="Instance name")
    awr.add_argument("--store", action="store_true",  help="Save to DB")
    awr.add_argument("--json",  action="store_true",  help="JSON output")

    # SAR mode
    sar = sub.add_parser("sar", help="SAR anomaly detection")
    sar.add_argument("--host",  required=True,        help="Hostname")
    sar.add_argument("--from",  dest="from_time", required=True,
                     help="Start time YYYY-MM-DD HH:MM")
    sar.add_argument("--to",    dest="to_time",   default=None,
                     help="End time YYYY-MM-DD HH:MM (default: from+25h)")
    sar.add_argument("--store", action="store_true",  help="Save to DB")
    sar.add_argument("--json",  action="store_true",  help="JSON output")

    # Legacy flat args (backward compat — old callers without subcommand)
    p.add_argument("--db",    default=None)
    p.add_argument("--snap",  default=None, type=int)
    p.add_argument("--inst",  default=None)
    p.add_argument("--store", action="store_true")
    p.add_argument("--json",  action="store_true")

    args = p.parse_args()

    # Legacy flat invocation: anomaly_detector.py --db X --snap N
    if args.mode is None:
        if args.db and args.snap is not None:
            findings = detect_all(args.db, args.snap, args.inst)
            if args.store:
                store_awr_anomalies(findings)
            _print_findings(findings, args.json)
        else:
            p.print_help()
        return

    if args.mode == "awr":
        findings = detect_all(args.db, args.snap, args.inst)
        if args.store:
            store_awr_anomalies(findings)
        _print_findings(findings, args.json)

    elif args.mode == "sar":
        t_from = datetime.strptime(args.from_time, "%Y-%m-%d %H:%M")
        t_to   = (datetime.strptime(args.to_time, "%Y-%m-%d %H:%M")
                  if args.to_time else None)
        findings = detect_sar(args.host, t_from, t_to, store=False)
        if args.store:
            store_sar_anomalies(findings)
        _print_findings(findings, args.json)


def _print_findings(findings, as_json=False):
    if as_json:
        print(json.dumps(findings, indent=2, default=str))
        return
    if not findings:
        print("No anomalies detected.")
        return
    print(f"\n{'='*70}")
    print(f"  {len(findings)} anomaly(ies) found")
    print(f"{'='*70}")
    for f in sorted(findings, key=lambda x: -abs(x["z_score"])):
        src  = f.get("dbname") or f.get("hostname", "")
        print(f"  [{f['severity'].upper():8}] z={f['z_score']:+.2f}"
              f"  {f['metric_source']:15}  {f['metric_name']:20}"
              f"  obj={f['object_name']}"
              f"  val={f['metric_value']:.2f}"
              f"  (μ={f['baseline_mean']:.2f} ±{f['baseline_stddev']:.2f})"
              f"  [{src}]")


if __name__ == "__main__":
    main()
