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
    Falls back gracefully if AI is unavailable.
    """
    if AI_MODE == "rules":
        return ""

    top_findings = findings[:5]
    if not top_findings:
        return ""

    prompt = f"""You are an Oracle DBA expert. Analyse these performance findings for database {dbname} (snap range {snap_range}) and provide a concise 3-5 paragraph executive summary with prioritised action items.

Findings:
{json.dumps(top_findings, indent=2, default=str)}

Respond with: 1) Summary of the overall performance state. 2) Top 3 most urgent actions in priority order. 3) Any cross-finding patterns worth noting (e.g. chain of related issues). Keep it factual and concise."""

    if AI_MODE == "local":
        return _call_ollama(prompt)
    elif AI_MODE == "cloud":
        return _call_anthropic(prompt)
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
        finally:
            conn.close()

        # Run all rule categories
        findings = []
        findings += self.engine.evaluate_wait_rules(wait_metrics)
        findings += self.engine.evaluate_sql_rules(sql_metrics)
        findings += self.engine.evaluate_efficiency_rules(efficiency)
        findings += self.engine.evaluate_segment_rules(seg_metrics)

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
