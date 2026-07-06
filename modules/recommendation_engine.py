# recommendation_engine.py
# ============================================================
# AWR Insight Portal — Recommendation Engine v2
#
# Evaluates 60+ rules against parsed AWR/SAR metrics and
# returns ranked, severity-weighted recommendations.
#
# Three operating modes (set via settings.yaml ai.mode):
#   rules  — deterministic rule engine only (default, ~75% accuracy)
#   local  — Ollama local LLM supplements rule output
#   cloud  — Anthropic API supplements rule output
#
# The rule engine always runs first. AI modes add narrative
# analysis on top of rule findings, they don't replace rules.
#
# USAGE:
#   from recommendation_engine import RecommendationEngine
#   engine = RecommendationEngine()
#   results = engine.evaluate(dbname="COLDBPRD", begin_snap=100, end_snap=110)
#
# API endpoint (called by FastAPI portal):
#   python recommendation_engine.py --db COLDBPRD --start 100 --end 110
# ============================================================

import os
import sys
import json
import re
import argparse
from typing import Optional

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from config_loader import load_config
from db import get_db_connection
from logger_utils import get_logger

logger = get_logger("recommendation_engine")

# ── config ──────────────────────────────────────────────────────────
_cfg      = load_config()
_ai_cfg   = _cfg.get("ai", {})
AI_MODE   = _ai_cfg.get("mode", "rules")
RULES_FILE = os.path.join(
    _PROJECT_ROOT,
    _ai_cfg.get("rules", {}).get("rules_file", "rules/recommendation_rules_v2.json")
)
MIN_SEVERITY = _ai_cfg.get("rules", {}).get("min_severity_to_show", "medium")

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
MIN_RANK      = SEVERITY_RANK.get(MIN_SEVERITY.lower(), 2)


# ── load rules ────────────────────────────────────────────────────────
def _load_rules() -> list:
    """Load and parse the rules JSON file. Strips JS-style // comments."""
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
        # Strip single-line // comments (not valid JSON but used for readability)
        cleaned = re.sub(r"//[^\n]*", "", raw)
        data = json.loads(cleaned)
        rules = data.get("rules", [])
        logger.info(f"Loaded {len(rules)} recommendation rules from {RULES_FILE}")
        return rules
    except Exception as e:
        logger.error(f"Failed to load rules from {RULES_FILE}: {e}")
        return []


# ── metric fetchers ───────────────────────────────────────────────────
def _fetch_wait_metrics(conn, dbname: str, begin_snap: int, end_snap: int) -> list:
    """Fetch foreground wait event metrics for the snap range."""
    sql = """
        SELECT event AS event_name, avg(pct_db_time) AS pct_db_time,
               avg(avg_wait_ms) AS avg_wait_ms,
               sum(waits) AS total_waits
        FROM awr_foreground_wait_events
        WHERE dbname = %s
          AND begin_snap BETWEEN %s AND %s
        GROUP BY event
        ORDER BY pct_db_time DESC NULLS LAST
        LIMIT 30
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (dbname, begin_snap, end_snap))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.warning(f"Wait metrics fetch failed: {e}")
        return []


def _fetch_sql_metrics(conn, dbname: str, begin_snap: int, end_snap: int) -> dict:
    """
    Fetch SQL performance metrics aggregated by sql_id.
    Elapsed + parse metrics from awr_sql_elapsed_time.
    CPU metrics from awr_sql_cpu_time (separate table, LEFT JOINed).
    """
    # ── Step 1: elapsed time + executions from awr_sql_elapsed_time ──
    sql_elapsed = """
        SELECT sql_id,
               avg(elapsed_time_per_exec_s) AS elapsed_time_s,
               avg(executions)              AS executions
        FROM awr_sql_elapsed_time
        WHERE dbname = %s AND begin_snap BETWEEN %s AND %s
        GROUP BY sql_id
        ORDER BY elapsed_time_s DESC NULLS LAST
        LIMIT 20
    """
    # ── Step 2: CPU time from awr_sql_cpu_time ─────────────────────
    sql_cpu = """
        SELECT sql_id,
               avg(cpu_per_exec_s) AS cpu_time_s
        FROM awr_sql_cpu_time
        WHERE dbname = %s AND begin_snap BETWEEN %s AND %s
        GROUP BY sql_id
    """
    # ── Step 3: parse calls from awr_sql_parsed_calls ──────────────
    sql_parse = """
        SELECT sql_id,
               sum(parse_calls) AS parse_calls
        FROM awr_sql_parsed_calls
        WHERE dbname = %s AND begin_snap BETWEEN %s AND %s
        GROUP BY sql_id
    """
    results = {"top_elapsed": [], "high_parse": [], "high_cpu": []}
    try:
        with conn.cursor() as cur:
            cur.execute(sql_elapsed, (dbname, begin_snap, end_snap))
            cols = [d[0] for d in cur.description]
            rows = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}  # keyed by sql_id

        try:
            with conn.cursor() as cur:
                cur.execute(sql_cpu, (dbname, begin_snap, end_snap))
                for r in cur.fetchall():
                    if r[0] in rows:
                        rows[r[0]]["cpu_time_s"] = float(r[1] or 0)
        except Exception as e:
            conn.rollback()
            logger.warning(f"SQL CPU metrics fetch failed: {e}")

        try:
            with conn.cursor() as cur:
                cur.execute(sql_parse, (dbname, begin_snap, end_snap))
                for r in cur.fetchall():
                    if r[0] in rows:
                        rows[r[0]]["parse_calls"] = float(r[1] or 0)
        except Exception as e:
            conn.rollback()
            logger.warning(f"SQL parse metrics fetch failed: {e}")

        for r in rows.values():
            elapsed = r.get("elapsed_time_s") or 0
            cpu     = r.get("cpu_time_s") or 0
            execs   = r.get("executions") or 1
            parses  = r.get("parse_calls") or 0

            if elapsed > 60:
                results["top_elapsed"].append(r)
            if cpu > 30:
                results["high_cpu"].append(r)
            if execs > 0 and (parses / execs) > 0.8 and parses > 1000:
                results["high_parse"].append(r)

    except Exception as e:
        conn.rollback()
        logger.warning(f"SQL metrics fetch failed: {e}")
    return results


def _fetch_instance_efficiency(conn, dbname: str, begin_snap: int, end_snap: int) -> dict:
    """Fetch instance efficiency ratios."""
    sql = """
        SELECT metric, avg(value) AS value
        FROM awr_instance_efficiency
        WHERE dbname = %s AND begin_snap BETWEEN %s AND %s
        GROUP BY metric
    """
    result = {}
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (dbname, begin_snap, end_snap))
            for row in cur.fetchall():
                result[row[0]] = row[1]
    except Exception as e:
        logger.warning(f"Instance efficiency fetch failed: {e}")
    return result


def _fetch_segment_metrics(conn, dbname: str, begin_snap: int, end_snap: int) -> dict:
    """Fetch top segment metrics across all segment tables."""
    metrics = {}
    tables = {
        "logical_reads":    ("awr_seg_logical_reads",     "logical_reads",      "pcttotal"),
        "physical_reads":   ("awr_seg_phy_reads",         "physical_reads",     "pcttotal"),
        "buffer_busy":      ("awr_seg_buff_busy_waits",   "buffer_busy_waits",  "pct_of_capture"),
        "row_lock_waits":   ("awr_seg_row_lck_waits",     "row_lock_waits",     "pct_of_capture"),
        "itl_waits":        ("awr_seg_itl_waits",         "itl_waits",          "pct_of_capture"),
        "table_scans":      ("awr_seg_table_scan",         "table_scans",        "pcttotal"),
        "gc_buffer_busy":   ("awr_seg_gbl_cache_buff_busy", "gc_buffer_busy",   "pct_of_capture"),
    }
    for key, (table, metric_col, pct_col) in tables.items():
        try:
            sql = f"""
                SELECT owner, object_name, obj_type,
                       SUM({metric_col}) AS metric_value,
                       AVG({pct_col}) AS pct_value
                FROM {table}
                WHERE dbname = %s AND begin_snap BETWEEN %s AND %s
                GROUP BY owner, object_name, obj_type
                ORDER BY metric_value DESC NULLS LAST
                LIMIT 5
            """
            with conn.cursor() as cur:
                cur.execute(sql, (dbname, begin_snap, end_snap))
                cols = [d[0] for d in cur.description]
                metrics[key] = [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.debug(f"Segment fetch for {table} failed: {e}")
            metrics[key] = []
    return metrics


# ── object metadata helpers ───────────────────────────────────────────

def _fetch_object_metadata(conn, dbname: str, seg_metrics: dict) -> dict:
    """
    Fetch object metadata for segments that appear in AWR metrics.
    Returns dict keyed by OWNER.OBJECT_NAME (upper case).
    """
    # Collect object names from all segment metric categories
    names = set()
    for segs in seg_metrics.values():
        for s in segs:
            obj = s.get("object_name", "")
            if obj:
                names.add(obj.upper())

    if not names:
        return {}

    try:
        placeholders = ",".join(["%s"] * len(names))
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT owner, object_name, object_type,
                       num_rows, last_analyzed, blevel,
                       distinct_keys, clustering_factor,
                       index_columns, partition_type, partition_count,
                       uniqueness, compression, blocks, avg_row_len
                FROM awr_object_metadata
                WHERE dbname = %s
                  AND UPPER(object_name) IN ({placeholders})
            """, [dbname] + list(names))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        # Key by OWNER.OBJECT_NAME for easy lookup
        result = {}
        for r in rows:
            key = f"{r['owner'].upper()}.{r['object_name'].upper()}"
            result[key] = r
            # Also index by object_name alone for cases where owner is unknown
            result[r['object_name'].upper()] = r
        return result
    except Exception as e:
        logger.debug(f"Object metadata fetch failed: {e}")
        return {}


def _evaluate_metadata_rules(obj_metadata: dict, seg_metrics: dict) -> list:
    """
    Fire metadata-driven rules against hot segments.
    Returns list of findings in same format as RuleEngine findings.
    """
    from datetime import datetime, timedelta
    if not obj_metadata:
        return []

    findings = []
    now = datetime.now()

    # Collect top segments across all categories
    hot_segments = {}
    for category, segs in seg_metrics.items():
        for s in segs:
            key = f"{s.get('owner','').upper()}.{s.get('object_name','').upper()}"
            if key not in hot_segments:
                hot_segments[key] = s

    for seg_key, seg in hot_segments.items():
        obj_name = seg.get("object_name", "")
        owner    = seg.get("owner", "")

        # Look up metadata — try owner.object first, then object alone
        meta = obj_metadata.get(seg_key) or obj_metadata.get(obj_name.upper())
        if not meta:
            continue

        obj_type      = meta.get("object_type", "")
        num_rows      = meta.get("num_rows")
        last_analyzed = meta.get("last_analyzed")
        blevel        = meta.get("blevel")
        distinct_keys = meta.get("distinct_keys")
        clust_factor  = meta.get("clustering_factor")
        partition_type = meta.get("partition_type")
        compression   = meta.get("compression", "")

        # ── Rule: Stale statistics ─────────────────────────────────
        if last_analyzed is None:
            findings.append({
                "rule_id":   f"meta_no_stats_{obj_name}",
                "category":  "statistics",
                "severity":  "high",
                "title":     f"Missing Statistics — {obj_name}",
                "event_or_object": f"{owner}.{obj_name}",
                "root_cause": (
                    f"{obj_type} {owner}.{obj_name} has never been analyzed. "
                    f"The optimizer is using default statistics which leads to "
                    f"poor cardinality estimates and suboptimal execution plans."
                ),
                "resolution_steps": [
                    f"Gather statistics: EXEC DBMS_STATS.GATHER_TABLE_STATS('{owner}','{obj_name}',CASCADE=>TRUE);",
                    "Schedule nightly statistics gathering via DBMS_STATS.GATHER_DATABASE_STATS.",
                    "Check for statistics locks: SELECT * FROM dba_tab_stat_prefs WHERE table_name='" + obj_name + "';",
                ],
                "metadata_context": f"Object: {obj_type}, rows: {num_rows or 'unknown'}, last_analyzed: never",
            })
        elif (now - last_analyzed).days > 30:
            days_old = (now - last_analyzed).days
            severity = "high" if days_old > 90 else "medium"
            findings.append({
                "rule_id":   f"meta_stale_stats_{obj_name}",
                "category":  "statistics",
                "severity":  severity,
                "title":     f"Stale Statistics ({days_old}d old) — {obj_name}",
                "event_or_object": f"{owner}.{obj_name}",
                "root_cause": (
                    f"Statistics for {owner}.{obj_name} are {days_old} days old "
                    f"(last analyzed: {last_analyzed.strftime('%Y-%m-%d')}). "
                    f"Stale statistics cause suboptimal execution plans especially "
                    f"when data volume has grown significantly."
                ),
                "resolution_steps": [
                    f"Regather statistics: EXEC DBMS_STATS.GATHER_TABLE_STATS('{owner}','{obj_name}',CASCADE=>TRUE,ESTIMATE_PERCENT=>DBMS_STATS.AUTO_SAMPLE_SIZE);",
                    "Consider incremental statistics for partitioned tables.",
                    "Review statistics history: SELECT * FROM dba_tab_stats_history WHERE table_name='" + obj_name + "' ORDER BY stats_update_time DESC;",
                ],
                "metadata_context": f"Object: {obj_type}, rows: {num_rows or 'unknown'}, last_analyzed: {last_analyzed.strftime('%Y-%m-%d')} ({days_old} days ago)",
            })

        # ── Rule: High clustering factor (index) ───────────────────
        if obj_type == 'INDEX' and clust_factor and num_rows:
            try:
                ratio = float(clust_factor) / float(num_rows)
                if ratio > 0.8:
                    findings.append({
                        "rule_id":   f"meta_high_cf_{obj_name}",
                        "category":  "index",
                        "severity":  "high" if ratio > 1.5 else "medium",
                        "title":     f"High Clustering Factor — {obj_name}",
                        "event_or_object": f"{owner}.{obj_name}",
                        "root_cause": (
                            f"Index {owner}.{obj_name} has clustering_factor={clust_factor:,} "
                            f"vs num_rows={num_rows:,} (ratio={ratio:.1f}x). "
                            f"A high clustering factor means rows are randomly distributed "
                            f"relative to the index order, causing excessive single-block I/O "
                            f"on range scans. This index may be nearly as expensive as a full table scan."
                        ),
                        "resolution_steps": [
                            "Consider rebuilding the table sorted by this index key column to improve data clustering.",
                            "Evaluate if a full table scan with parallel query is cheaper for large range queries.",
                            "If Oracle 12c+, consider using index clustering factor with optimizer_index_caching.",
                            "For heavily accessed columns, consider hash partitioning or IOT (Index Organized Table).",
                        ],
                        "metadata_context": f"clustering_factor={clust_factor:,}, num_rows={num_rows:,}, ratio={ratio:.1f}x",
                    })
            except (TypeError, ZeroDivisionError):
                pass

        # ── Rule: Deep B-tree index ────────────────────────────────
        if obj_type == 'INDEX' and blevel and blevel >= 4:
            severity = "high" if blevel >= 5 else "medium"
            findings.append({
                "rule_id":   f"meta_deep_btree_{obj_name}",
                "category":  "index",
                "severity":  severity,
                "title":     f"Deep B-tree Index (blevel={blevel}) — {obj_name}",
                "event_or_object": f"{owner}.{obj_name}",
                "root_cause": (
                    f"Index {owner}.{obj_name} has blevel={blevel} "
                    f"(healthy blevel ≤ 3). Each extra level adds one I/O per index lookup. "
                    f"A blevel of {blevel} means at least {blevel+1} I/Os per row fetch via this index. "
                    f"Typically caused by excessive deletes leaving sparse leaf blocks."
                ),
                "resolution_steps": [
                    f"Rebuild the index: ALTER INDEX {owner}.{obj_name} REBUILD ONLINE;",
                    "Verify blevel after rebuild: SELECT index_name, blevel FROM dba_indexes WHERE index_name='" + obj_name + "';",
                    "Schedule periodic index coalesce: ALTER INDEX " + obj_name + " COALESCE;",
                    "Investigate delete patterns — consider partitioned indexes for tables with range-based purges.",
                ],
                "metadata_context": f"blevel={blevel}, distinct_keys={distinct_keys}, clustering_factor={clust_factor}",
            })

        # ── Rule: Large unpartitioned table ───────────────────────
        if obj_type == 'TABLE' and num_rows and not partition_type:
            try:
                if int(num_rows) > 10_000_000:
                    severity = "high" if int(num_rows) > 100_000_000 else "medium"
                    findings.append({
                        "rule_id":   f"meta_large_unpartitioned_{obj_name}",
                        "category":  "partitioning",
                        "severity":  severity,
                        "title":     f"Large Unpartitioned Table ({int(num_rows):,} rows) — {obj_name}",
                        "event_or_object": f"{owner}.{obj_name}",
                        "root_cause": (
                            f"Table {owner}.{obj_name} has {int(num_rows):,} rows and is not partitioned. "
                            f"Large unpartitioned tables prevent partition pruning, making "
                            f"range queries and purge operations unnecessarily expensive. "
                            f"Full table scans will read all {int(num_rows):,} rows."
                        ),
                        "resolution_steps": [
                            "Evaluate a date/time-based RANGE partition strategy for this table.",
                            "Use DBMS_REDEFINITION to partition online without downtime.",
                            "Identify most common WHERE clause predicates to choose partition key.",
                            "After partitioning, rebuild global indexes as local indexes for partition pruning.",
                        ],
                        "metadata_context": f"num_rows={int(num_rows):,}, unpartitioned, compression={compression or 'NONE'}",
                    })
            except (TypeError, ValueError):
                pass

        # ── Rule: Table compression opportunity ───────────────────
        if obj_type == 'TABLE' and num_rows and (not compression or compression == 'DISABLED'):
            try:
                if int(num_rows) > 50_000_000:
                    findings.append({
                        "rule_id":   f"meta_no_compression_{obj_name}",
                        "category":  "storage",
                        "severity":  "medium",
                        "title":     f"Compression Opportunity — {obj_name}",
                        "event_or_object": f"{owner}.{obj_name}",
                        "root_cause": (
                            f"Table {owner}.{obj_name} has {int(num_rows):,} rows and no compression. "
                            f"For large read-mostly tables, Advanced Compression (OLTP) or "
                            f"Hybrid Columnar Compression (HCC on Exadata) can reduce "
                            f"physical I/O significantly by reducing blocks read."
                        ),
                        "resolution_steps": [
                            "Evaluate compression ratio: exec dbms_compression.get_compression_ratio(ownname=>'" + owner + "',tabname=>'" + obj_name + "',comptype=>dbms_compression.comp_advanced,blkcnt_cmp=>:a,blkcnt_uncmp=>:b,row_cmp=>:c,row_uncmp=>:d,cmp_ratio=>:e,comptype_str=>:f);",
                            "Apply compression online (12c+): ALTER TABLE " + owner + "." + obj_name + " MOVE ONLINE COMPRESS FOR OLTP;",
                            "Validate access patterns — compression best for insert-only or low-DML tables.",
                        ],
                        "metadata_context": f"num_rows={int(num_rows):,}, compression=NONE",
                    })
            except (TypeError, ValueError):
                pass

    return findings


# ── rule evaluator ────────────────────────────────────────────────────
class RuleEngine:
    """
    Evaluates rules against a dict of current metric values.
    Conditions are expressed as simple Python-evaluatable expressions.
    """

    SAFE_GLOBALS = {"__builtins__": {}}

    def __init__(self, rules: list):
        self.rules = rules

    def evaluate_condition(self, condition: str, context: dict) -> bool:
        """
        Safely evaluate a condition string like 'pct_db_time > 15 OR avg_wait_ms > 10'
        against a context dict of current metric values.
        Returns True if condition is met.
        """
        if not condition:
            return False
        try:
            # Replace boolean operators to Python equivalents
            expr = condition.replace(" AND ", " and ").replace(" OR ", " or ")
            return bool(eval(expr, self.SAFE_GLOBALS, context))
        except Exception:
            return False

    def match_wait_event(self, rule: dict, wait_event: dict) -> bool:
        """Check if a wait event matches a rule's event_pattern."""
        pattern = rule.get("event_pattern", "*")
        event   = (wait_event.get("event_name") or "").lower()

        if pattern == "*":
            return True
        if "*" in pattern:
            # Simple wildcard match
            prefix = pattern.split("*")[0].lower()
            return event.startswith(prefix)
        return event == pattern.lower()

    def evaluate_wait_rules(self, wait_metrics: list) -> list:
        findings = []
        wait_rules = [r for r in self.rules if r.get("category") == "wait"]

        for event in wait_metrics:
            context = {
                "pct_db_time":  float(event.get("pct_db_time") or 0),
                "avg_wait_ms":  float(event.get("avg_wait_ms") or 0),
                "total_waits":  float(event.get("total_waits") or 0),
            }

            matched_rules = [r for r in wait_rules if self.match_wait_event(r, event)]
            for rule in matched_rules:
                if self.evaluate_condition(rule.get("condition", ""), context):
                    findings.append({
                        "rule_id":      rule["rule_id"],
                        "category":     "wait",
                        "severity":     rule.get("severity", "medium"),
                        "title":        rule.get("title", ""),
                        "event":        event.get("event_name"),
                        "pct_db_time":  context["pct_db_time"],
                        "avg_wait_ms":  context["avg_wait_ms"],
                        "root_cause":   rule.get("root_cause", ""),
                        "resolution":   rule.get("resolution_steps", []),
                        "diagnostic_sql": rule.get("diagnostic_sql", []),
                        "related_rules":  rule.get("related_rules", []),
                    })
                    break   # Don't double-fire specific + wildcard for same event

        return findings

    def evaluate_sql_rules(self, sql_metrics: dict) -> list:
        findings = []
        sql_rules = [r for r in self.rules if r.get("category") == "sql"]

        mappings = [
            ("high_elapsed_time", "top_elapsed",  {"elapsed_time_s": "elapsed_time_s", "executions": "executions"}),
            ("high_cpu",          "high_cpu",     {"cpu_time_s": "cpu_time_s", "executions": "executions"}),
            ("high_parse_calls",  "high_parse",   {"parse_calls": "parse_calls", "executions": "executions"}),
        ]

        for pattern_key, metric_key, field_map in mappings:
            items = sql_metrics.get(metric_key, [])
            if not items:
                continue
            rule = next((r for r in sql_rules
                         if r.get("event_pattern") == pattern_key), None)
            if not rule:
                continue

            for item in items[:5]:   # Top 5 per category
                context = {k: float(item.get(v) or 0) for k, v in field_map.items()}
                context["parse_calls_pct"] = (
                    context.get("parse_calls", 0) / max(context.get("executions", 1), 1) * 100
                )
                if self.evaluate_condition(rule.get("condition", ""), context):
                    findings.append({
                        "rule_id":       rule["rule_id"],
                        "category":      "sql",
                        "severity":      rule.get("severity", "medium"),
                        "title":         rule.get("title", ""),
                        "sql_id":        item.get("sql_id"),
                        "metrics":       context,
                        "root_cause":    rule.get("root_cause", ""),
                        "resolution":    rule.get("resolution_steps", []),
                        "diagnostic_sql": rule.get("diagnostic_sql", []),
                        "related_rules": rule.get("related_rules", []),
                    })

        return findings

    def evaluate_efficiency_rules(self, efficiency: dict) -> list:
        findings = []
        eff_rules = [r for r in self.rules if r.get("category") == "instance_efficiency"]

        pattern_to_metric = {
            "buffer_cache_hit_ratio": ["Buffer Cache Hit Ratio", "Buffer Nowait %"],
            "soft_parse_ratio":       ["Soft Parse %"],
            "library_cache_hit_ratio":["Library Cache Hit Ratio", "Library Hit %"],
            "execute_to_parse_ratio": ["Execute to Parse %"],
        }

        for rule in eff_rules:
            pattern  = rule.get("event_pattern", "")
            metrics  = pattern_to_metric.get(pattern, [pattern])
            value    = None
            for m in metrics:
                if m in efficiency:
                    value = float(efficiency[m] or 0)
                    break
            if value is None:
                continue
            context = {"value": value}
            if self.evaluate_condition(rule.get("condition", ""), context):
                findings.append({
                    "rule_id":      rule["rule_id"],
                    "category":     "instance_efficiency",
                    "severity":     rule.get("severity", "medium"),
                    "title":        rule.get("title", ""),
                    "metric":       pattern,
                    "value":        value,
                    "root_cause":   rule.get("root_cause", ""),
                    "resolution":   rule.get("resolution_steps", []),
                    "diagnostic_sql": rule.get("diagnostic_sql", []),
                    "related_rules":  rule.get("related_rules", []),
                })

        return findings

    def evaluate_segment_rules(self, segment_metrics: dict) -> list:
        findings = []
        seg_rules = [r for r in self.rules if r.get("category") == "segment"]

        pattern_to_key = {
            "high_logical_reads":    "logical_reads",
            "high_physical_reads":   "physical_reads",
            "high_table_scans":      "table_scans",
            "high_row_lock_waits":   "row_lock_waits",
            "high_gc_buffer_busy":   "gc_buffer_busy",
        }

        for rule in seg_rules:
            pattern  = rule.get("event_pattern", "")
            metric_key = pattern_to_key.get(pattern)
            if not metric_key:
                continue
            items = segment_metrics.get(metric_key, [])
            for item in items[:3]:
                context = {
                    "logical_reads":   float(item.get("metric_value") or 0),
                    "physical_reads":  float(item.get("metric_value") or 0),
                    "table_scans":     float(item.get("metric_value") or 0),
                    "row_lock_waits":  float(item.get("metric_value") or 0),
                    "gc_buffer_busy":  float(item.get("metric_value") or 0),
                    "pcttotal":        float(item.get("pct_value") or 0),
                }
                if self.evaluate_condition(rule.get("condition", ""), context):
                    findings.append({
                        "rule_id":     rule["rule_id"],
                        "category":    "segment",
                        "severity":    rule.get("severity", "medium"),
                        "title":       rule.get("title", ""),
                        "object":      f"{item.get('owner','')}.{item.get('object_name','')}",
                        "obj_type":    item.get("obj_type"),
                        "metric_value": context.get(metric_key, 0),
                        "root_cause":  rule.get("root_cause", ""),
                        "resolution":  rule.get("resolution_steps", []),
                        "diagnostic_sql": rule.get("diagnostic_sql", []),
                        "related_rules":  rule.get("related_rules", []),
                    })

        return findings


# ── AI supplement ──────────────────────────────────────────────────────
def _ai_supplement(findings: list, dbname: str, snap_range: str) -> str:
    """
    Generate an AI narrative summary of the top findings.
    Reads ai_mode from portal_config DB table (not JSON file).
    Falls back gracefully if AI is unavailable.
    """
    # Read live config from DB
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT key,value FROM portal_config WHERE section='ai'")
            db_cfg = {r[0]: r[1] for r in cur.fetchall()}
        conn.close()
    except Exception:
        db_cfg = {}

    ai_mode = db_cfg.get("ai_mode", AI_MODE)

    if ai_mode == "rules":
        return ""

    top_findings = findings[:5]
    if not top_findings:
        return ""

    prompt = (
        f"You are an Oracle DBA expert. Analyse these performance findings for "
        f"database {dbname} (snap range {snap_range}) and provide a concise "
        f"3-5 paragraph executive summary with prioritised action items.\n\n"
        f"Findings:\n{json.dumps(top_findings, indent=2, default=str)}\n\n"
        f"Respond with: 1) Summary of the overall performance state. "
        f"2) Top 3 most urgent actions in priority order. "
        f"3) Any cross-finding patterns worth noting. Keep it factual and concise."
    )

    try:
        if ai_mode == "local_ai":
            url   = db_cfg.get("ai_local_url", "http://localhost:11434")
            model = db_cfg.get("ai_local_model", "llama3.1:8b")
            import urllib.request as _ur
            payload = json.dumps({
                "model": model, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.3, "num_predict": 500}
            }).encode()
            req = _ur.Request(f"{url}/api/generate", data=payload,
                              headers={"Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read()).get("response", "").strip()

        elif ai_mode == "cloud_ai":
            provider = db_cfg.get("ai_cloud_provider", "claude")
            api_key  = db_cfg.get("ai_cloud_api_key", "")
            model    = db_cfg.get("ai_cloud_model", "")
            if not api_key:
                logger.warning("Cloud AI: no API key set")
                return ""
            import urllib.request as _ur
            if provider == "claude":
                payload = json.dumps({
                    "model": model or "claude-haiku-4-5-20251001",
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode()
                req = _ur.Request(
                    "https://api.anthropic.com/v1/messages", data=payload,
                    headers={"Content-Type": "application/json",
                             "x-api-key": api_key,
                             "anthropic-version": "2023-06-01"})
                with _ur.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())["content"][0]["text"].strip()
            elif provider == "openai":
                payload = json.dumps({
                    "model": model or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600, "temperature": 0.3,
                }).encode()
                req = _ur.Request(
                    "https://api.openai.com/v1/chat/completions", data=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {api_key}"})
                with _ur.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"AI supplement failed ({ai_mode}): {e}")
    return ""


def _call_ollama(prompt: str) -> str:
    try:
        import urllib.request
        ollama_cfg = _ai_cfg.get("ollama", {})
        url   = ollama_cfg.get("base_url", "http://localhost:11434") + "/api/generate"
        model = ollama_cfg.get("model", "llama3")
        payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data.get("response", "")
    except Exception as e:
        logger.warning(f"Ollama call failed: {e}")
        return ""


def _call_anthropic(prompt: str) -> str:
    try:
        import urllib.request
        ant_cfg  = _ai_cfg.get("anthropic", {})
        api_key  = ant_cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("Anthropic API key not set — skipping AI supplement")
            return ""
        model    = ant_cfg.get("model", "claude-sonnet-4-6")
        max_tok  = int(ant_cfg.get("max_tokens", 1024))
        payload  = json.dumps({
            "model": model, "max_tokens": max_tok,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"]
    except Exception as e:
        logger.warning(f"Anthropic API call failed: {e}")
        return ""


# ── main evaluator ────────────────────────────────────────────────────
class RecommendationEngine:

    def __init__(self):
        self.rules  = _load_rules()
        self.engine = RuleEngine(self.rules)

    def evaluate(self, dbname: str, begin_snap: int, end_snap: int,
                 instance: Optional[str] = None) -> dict:
        """
        Run all rule categories against the given snap range.

        Returns:
            {
                "dbname": ...,
                "snap_range": "begin-end",
                "total_findings": N,
                "findings": [ { rule_id, category, severity, title, ... } ],
                "ai_summary": "..." (empty if AI mode is rules)
            }
        """
        logger.info(f"Evaluating recommendations: {dbname} snaps {begin_snap}-{end_snap}")

        conn = get_db_connection()
        try:
            wait_metrics  = _fetch_wait_metrics(conn, dbname, begin_snap, end_snap)
            sql_metrics   = _fetch_sql_metrics(conn, dbname, begin_snap, end_snap)
            efficiency    = _fetch_instance_efficiency(conn, dbname, begin_snap, end_snap)
            seg_metrics   = _fetch_segment_metrics(conn, dbname, begin_snap, end_snap)
            obj_metadata  = _fetch_object_metadata(conn, dbname, seg_metrics)
        finally:
            conn.close()

        # Run all rule categories
        findings = []
        findings += self.engine.evaluate_wait_rules(wait_metrics)
        findings += self.engine.evaluate_sql_rules(sql_metrics)
        findings += self.engine.evaluate_efficiency_rules(efficiency)
        findings += self.engine.evaluate_segment_rules(seg_metrics)

        # ── Metadata-driven rules ────────────────────────────────────
        findings += _evaluate_metadata_rules(obj_metadata, seg_metrics)

        # Filter by minimum severity
        findings = [f for f in findings
                    if SEVERITY_RANK.get(f.get("severity", "low"), 0) >= MIN_RANK]

        # Sort: critical first, then high, then by category
        findings.sort(
            key=lambda f: (-SEVERITY_RANK.get(f.get("severity", "low"), 0),
                           f.get("category", ""),
                           f.get("rule_id", ""))
        )

        # Deduplicate by rule_id (same rule can fire for multiple events/segments)
        seen     = set()
        deduped  = []
        for f in findings:
            key = f.get("rule_id")
            if key not in seen:
                seen.add(key)
                deduped.append(f)

        snap_range  = f"{begin_snap}-{end_snap}"
        ai_summary  = _ai_supplement(deduped, dbname, snap_range)

        result = {
            "dbname":         dbname,
            "begin_snap":     begin_snap,
            "end_snap":       end_snap,
            "snap_range":     snap_range,
            "ai_mode":        AI_MODE,
            "total_findings": len(deduped),
            "critical":       sum(1 for f in deduped if f.get("severity") == "critical"),
            "high":           sum(1 for f in deduped if f.get("severity") == "high"),
            "medium":         sum(1 for f in deduped if f.get("severity") == "medium"),
            "low":            sum(1 for f in deduped if f.get("severity") == "low"),
            "findings":       deduped,
            "ai_summary":     ai_summary,
        }

        logger.info(f"Recommendations complete: {len(deduped)} findings "
                    f"(critical={result['critical']}, high={result['high']})")
        return result

    def store_recommendations(self, result: dict) -> None:
        """Persist recommendation results to awr_recommendations table."""
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for f in result["findings"]:
                    cur.execute("""
                        INSERT INTO awr_recommendations
                            (dbname, begin_snap, end_snap, rule_id, category,
                             severity, title, event_or_object,
                             root_cause, resolution_json, ai_summary, created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT (dbname, begin_snap, end_snap, rule_id) DO UPDATE
                            SET severity = EXCLUDED.severity,
                                created_at = NOW()
                    """, (
                        result["dbname"], result["begin_snap"], result["end_snap"],
                        f.get("rule_id"), f.get("category"),
                        f.get("severity"), f.get("title"),
                        f.get("event") or f.get("object") or f.get("sql_id") or "",
                        f.get("root_cause", ""),
                        json.dumps(f.get("resolution", []), default=str),
                        result.get("ai_summary", ""),
                    ))
            conn.commit()
            logger.info(f"Stored {len(result['findings'])} recommendations to DB")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to store recommendations: {e}", exc_info=True)
        finally:
            conn.close()


# ── callable interface for master_parser auto-trigger ─────────────────
def run(db_name: str, start_snap: int, end_snap: int, store: bool = False,
        instance: Optional[str] = None) -> dict:
    """
    Module-level entry point called by master_parser._run_recommendations().
    Avoids sys.argv patching — preferred over main() for programmatic use.
    """
    engine = RecommendationEngine()
    result = engine.evaluate(db_name, start_snap, end_snap, instance)
    if store:
        engine.store_recommendations(result)
    return result


# ── CLI entry point ───────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="AWR Recommendation Engine v2")
    p.add_argument("--db",    required=True,       help="Database name (e.g. COLDBPRD)")
    p.add_argument("--start", required=True, type=int, help="Begin snap ID")
    p.add_argument("--end",   required=True, type=int, help="End snap ID")
    p.add_argument("--store", action="store_true",  help="Persist findings to DB")
    p.add_argument("--json",  action="store_true",  help="Output JSON instead of plain text")
    args = p.parse_args()

    engine = RecommendationEngine()
    result = engine.evaluate(args.db, args.start, args.end)

    if args.store:
        engine.store_recommendations(result)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    # Human-readable output
    print(f"\n{'='*60}")
    print(f"Recommendations for {result['dbname']} (snaps {result['snap_range']})")
    print(f"Total: {result['total_findings']}  |  "
          f"Critical: {result['critical']}  High: {result['high']}  Medium: {result['medium']}")
    print(f"AI mode: {result['ai_mode']}")
    print(f"{'='*60}\n")

    for i, f in enumerate(result["findings"], 1):
        sev = f.get("severity", "").upper()
        print(f"[{i}] [{sev}] {f.get('rule_id')} — {f.get('title')}")
        if f.get("event"):
            print(f"     Event  : {f['event']} ({f.get('pct_db_time',0):.1f}% DB time, avg {f.get('avg_wait_ms',0):.1f}ms)")
        if f.get("object"):
            print(f"     Segment: {f['object']} ({f.get('obj_type','')})")
        if f.get("sql_id"):
            print(f"     SQL ID : {f['sql_id']}")
        print(f"     Cause  : {f.get('root_cause','')[:120]}...")
        print(f"     Fix 1  : {f.get('resolution',[''])[0] if f.get('resolution') else ''}")
        print()

    if result.get("ai_summary"):
        print(f"\n{'─'*60}")
        print("AI ANALYSIS SUMMARY:")
        print(result["ai_summary"])


if __name__ == "__main__":
    main()
