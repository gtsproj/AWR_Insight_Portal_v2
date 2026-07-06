# modules/ai/ai_engine.py
# ============================================================
# AWR Insight Portal — AI Recommendation Engine
# Supports: Ollama (local), Anthropic Claude, OpenAI, Google Gemini
#
# Called by recommendation_engine.py when ai_mode != 'rules'
# ============================================================

import os
import sys
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

logger = logging.getLogger("ai_engine")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))


# ── Config loader ─────────────────────────────────────────────────────
def _get_ai_config() -> dict:
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT key, value FROM portal_config
                WHERE section = 'ai'
            """)
            return {r[0]: (r[1] or "") for r in cur.fetchall()}
        conn.close()
    except Exception as e:
        logger.warning(f"Could not load AI config from DB: {e}")
        return {}


# ── Oracle object metadata fetcher ───────────────────────────────────
def _get_object_context(dbname: str, object_names: list) -> str:
    """
    Fetch relevant object metadata from awr_object_metadata table
    to enrich the AI prompt with table/index details.
    """
    if not object_names:
        return ""
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(object_names))
            cur.execute(f"""
                SELECT object_name, object_type, num_rows, last_analyzed,
                       blevel, distinct_keys, clustering_factor,
                       index_columns, partition_type, partition_count,
                       uniqueness, compression
                FROM awr_object_metadata
                WHERE dbname = %s
                  AND UPPER(object_name) = ANY(
                      ARRAY[{placeholders}]::text[]
                  )
                ORDER BY object_type, object_name
                LIMIT 20
            """, [dbname] + [o.upper() for o in object_names])
            rows = cur.fetchall()
        conn.close()

        if not rows:
            return ""

        lines = ["Object Metadata from Oracle Dictionary:"]
        for r in rows:
            obj_name, obj_type, num_rows, last_analyzed, blevel, \
            distinct_keys, clust_factor, idx_cols, part_type, \
            part_count, uniqueness, compression = r

            if obj_type == 'TABLE':
                analyzed = last_analyzed.strftime('%Y-%m-%d') if last_analyzed else 'never'
                lines.append(
                    f"  TABLE {obj_name}: rows={num_rows:,}" if num_rows else
                    f"  TABLE {obj_name}: rows=unknown"
                )
                lines[-1] += f", last_analyzed={analyzed}"
                if compression and compression != 'DISABLED':
                    lines[-1] += f", compression={compression}"
                if part_type:
                    lines[-1] += f", partitioned={part_type}({part_count} parts)"

            elif obj_type == 'INDEX':
                analyzed = last_analyzed.strftime('%Y-%m-%d') if last_analyzed else 'never'
                col_info = ""
                if idx_cols:
                    try:
                        cols = json.loads(idx_cols)
                        col_info = " columns=(" + ",".join(
                            c.get("col","") + ("↓" if c.get("desc")=="DESC" else "")
                            for c in cols
                        ) + ")"
                    except Exception:
                        pass
                lines.append(
                    f"  INDEX {obj_name} [{uniqueness}]: "
                    f"blevel={blevel}, distinct_keys={distinct_keys}, "
                    f"clustering_factor={clust_factor}"
                    f"{col_info}, last_analyzed={analyzed}"
                )

        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Object context fetch failed: {e}")
        return ""


# ── AI Learning context ───────────────────────────────────────────────
def _get_learning_context(trigger_pattern: str) -> str:
    """Fetch previously accepted recommendations for similar patterns."""
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT accepted_recommendation, times_accepted, last_seen
                FROM awr_ai_learnings
                WHERE trigger_pattern = %s
                   OR trigger_pattern LIKE %s
                ORDER BY times_accepted DESC
                LIMIT 3
            """, (trigger_pattern, trigger_pattern.split(":")[0] + ":%"))
            rows = cur.fetchall()
        conn.close()
        if not rows:
            return ""
        lines = ["Previously accepted recommendations for similar issues:"]
        for rec, times, last in rows:
            lines.append(f"  [accepted {times}x, last {last.strftime('%Y-%m-%d')}]: {rec[:200]}")
        return "\n".join(lines)
    except Exception:
        return ""


# ── Prompt builder ────────────────────────────────────────────────────
def build_prompt(
    dbname: str,
    trigger_type: str,
    trigger_value: str,
    metrics: dict,
    dba_feedback: str = "",
) -> str:
    """
    Build a structured Oracle DBA analysis prompt from AWR metrics.

    metrics dict expected keys:
      top_waits: list of {event, pct_db_time}
      top_sql:   list of {sql_id, elapsed_s, executions, cpu_s}
      top_segments: list of {owner, object_name, obj_type, logical_reads, physical_reads}
      db_stats:  dict {buffer_cache_hit_pct, soft_parse_pct, sga_mb, pga_mb}
      snap_info: dict {begin_snap, end_snap, begin_time, end_time, db_time_s}
    """
    # Extract object names for metadata lookup
    seg_names = [s.get("object_name","") for s in metrics.get("top_segments", [])]
    obj_context = _get_object_context(dbname, seg_names)
    learn_context = _get_learning_context(f"{trigger_type}:{trigger_value}")

    snap = metrics.get("snap_info", {})
    db_stats = metrics.get("db_stats", {})

    # Format top waits
    waits_txt = "\n".join(
        f"  {i+1}. {w.get('event','?')}: {w.get('pct_db_time',0):.1f}% DB time"
        for i, w in enumerate(metrics.get("top_waits", [])[:8])
    ) or "  No wait data available"

    # Format top SQL
    sql_txt = "\n".join(
        f"  {i+1}. SQL_ID={s.get('sql_id','?')}: "
        f"elapsed={s.get('elapsed_s',0):.1f}s, "
        f"execs={s.get('executions',0)}, "
        f"cpu={s.get('cpu_s',0):.1f}s"
        for i, s in enumerate(metrics.get("top_sql", [])[:5])
    ) or "  No SQL data available"

    # Format top segments
    seg_txt = "\n".join(
        f"  {i+1}. {s.get('owner','?')}.{s.get('object_name','?')} "
        f"[{s.get('obj_type','?')}]: "
        f"logical_reads={s.get('logical_reads',0):,}, "
        f"physical_reads={s.get('physical_reads',0):,}"
        for i, s in enumerate(metrics.get("top_segments", [])[:5])
    ) or "  No segment data available"

    # Focus area
    focus_txt = {
        "wait":    f"Focus: Wait event '{trigger_value}' is the primary concern.",
        "sql":     f"Focus: SQL_ID '{trigger_value}' is the primary concern.",
        "segment": f"Focus: Segment '{trigger_value}' is the primary concern.",
        "overall": "Focus: Overall database performance analysis.",
    }.get(trigger_type, f"Focus: {trigger_value}")

    prompt = f"""You are an expert Oracle DBA performance analyst. Analyse the AWR snapshot data below and provide a concise, actionable diagnosis.

Database: {dbname}
Snapshot: {snap.get('begin_time','')} → {snap.get('end_time','')} (snaps {snap.get('begin_snap','')}–{snap.get('end_snap','')})
Total DB Time: {snap.get('db_time_s',0):.0f} seconds

{focus_txt}

=== TOP WAIT EVENTS ===
{waits_txt}

=== TOP SQL BY ELAPSED TIME ===
{sql_txt}

=== TOP SEGMENTS BY I/O ===
{seg_txt}

=== INSTANCE EFFICIENCY ===
  Buffer Cache Hit%: {db_stats.get('buffer_cache_hit_pct','?')}%
  Soft Parse%: {db_stats.get('soft_parse_pct','?')}%
  SGA: {db_stats.get('sga_mb','?')} MB  |  PGA: {db_stats.get('pga_mb','?')} MB
"""

    if obj_context:
        prompt += f"\n=== ORACLE OBJECT METADATA ===\n{obj_context}\n"

    if learn_context:
        prompt += f"\n=== HISTORICAL CONTEXT ===\n{learn_context}\n"

    if dba_feedback:
        prompt += f"\n=== DBA ADDITIONAL CONTEXT ===\n{dba_feedback}\n"

    prompt += """
=== YOUR TASK ===
Provide a structured analysis with:

1. ROOT CAUSE (2-3 sentences): What is causing the primary performance issue?
2. EVIDENCE: Which specific metrics support your diagnosis?
3. RECOMMENDATION (numbered list, max 5 steps): Concrete DBA actions to resolve the issue.
4. VALIDATION: How to verify the fix worked.
5. RISK: Any risks or prerequisites before implementing.

Be specific — mention exact table names, index columns, or Oracle parameters where relevant.
Keep total response under 400 words.
"""
    return prompt


# ── AI Callers ────────────────────────────────────────────────────────

def _call_ollama(prompt: str, url: str, model: str) -> str:
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 600},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data.get("response", "").strip()


def _call_claude(prompt: str, api_key: str, model: str) -> str:
    model = model or "claude-haiku-4-5-20251001"
    payload = json.dumps({
        "model": model,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["content"][0]["text"].strip()


def _call_openai(prompt: str, api_key: str, model: str) -> str:
    model = model or "gpt-4o-mini"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 700,
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


def _call_gemini(prompt: str, api_key: str, model: str) -> str:
    model = model or "gemini-1.5-flash"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 700, "temperature": 0.3},
    }).encode("utf-8")
    url = (f"https://generativelanguage.googleapis.com/v1/models/"
           f"{model}:generateContent?key={api_key}")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ── Main entry point ──────────────────────────────────────────────────
def get_ai_recommendation(
    dbname: str,
    trigger_type: str,
    trigger_value: str,
    metrics: dict,
    dba_feedback: str = "",
    config: dict = None,
) -> dict:
    """
    Main entry point. Returns dict with:
      {
        ok: bool,
        response: str,          # full AI narrative
        root_cause: str,        # extracted section
        recommendation: str,    # extracted section
        provider: str,
        model: str,
        prompt: str,
        error: str,             # if ok=False
      }
    """
    cfg = config or _get_ai_config()
    ai_mode     = cfg.get("ai_mode", "rules")
    provider    = cfg.get("ai_cloud_provider", "claude")
    api_key     = cfg.get("ai_cloud_api_key", "")
    cloud_model = cfg.get("ai_cloud_model", "")
    local_url   = cfg.get("ai_local_url", "http://localhost:11434")
    local_model = cfg.get("ai_local_model", "llama3.1:8b")

    prompt = build_prompt(dbname, trigger_type, trigger_value, metrics, dba_feedback)

    try:
        if ai_mode == "local_ai":
            response = _call_ollama(prompt, local_url, local_model)
            used_provider = "ollama"
            used_model    = local_model
        elif ai_mode == "cloud_ai":
            if provider == "claude":
                response = _call_claude(prompt, api_key, cloud_model)
            elif provider == "openai":
                response = _call_openai(prompt, api_key, cloud_model)
            elif provider == "gemini":
                response = _call_gemini(prompt, api_key, cloud_model)
            else:
                raise ValueError(f"Unknown cloud provider: {provider}")
            used_provider = provider
            used_model    = cloud_model
        else:
            return {"ok": False, "error": "AI mode not enabled — using rules engine"}

        # Extract sections from response
        def extract_section(text, heading):
            import re
            pattern = rf"{heading}[:\s]*\n?(.*?)(?=\n\d+\.|ROOT CAUSE|EVIDENCE|RECOMMENDATION|VALIDATION|RISK|$)"
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else ""

        root_cause     = extract_section(response, "ROOT CAUSE")
        recommendation = extract_section(response, "RECOMMENDATION")

        return {
            "ok":             True,
            "response":       response,
            "root_cause":     root_cause or response[:300],
            "recommendation": recommendation or response,
            "provider":       used_provider,
            "model":          used_model,
            "prompt":         prompt,
            "error":          "",
        }

    except Exception as e:
        logger.error(f"AI recommendation failed: {e}", exc_info=True)
        return {
            "ok":             False,
            "response":       "",
            "root_cause":     "",
            "recommendation": "",
            "provider":       ai_mode,
            "model":          "",
            "prompt":         prompt,
            "error":          str(e),
        }


# ── Feedback storage ──────────────────────────────────────────────────
def store_ai_recommendation(
    dbname: str, instance: str,
    begin_snap: int, end_snap: int,
    trigger_type: str, trigger_value: str,
    severity: str,
    result: dict,
) -> int:
    """Store AI recommendation in DB. Returns record ID."""
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO awr_ai_recommendations
                  (dbname, instance, begin_snap, end_snap,
                   trigger_type, trigger_value, severity,
                   ai_provider, ai_model, ai_prompt,
                   ai_response, root_cause, recommendation, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
                RETURNING id
            """, (
                dbname, instance, begin_snap, end_snap,
                trigger_type, trigger_value, severity,
                result.get("provider"), result.get("model"),
                result.get("prompt"), result.get("response"),
                result.get("root_cause"), result.get("recommendation"),
            ))
            rec_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return rec_id
    except Exception as e:
        logger.error(f"Failed to store AI recommendation: {e}")
        return -1


def accept_recommendation(rec_id: int) -> bool:
    """Mark a recommendation as accepted and add to learnings."""
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE awr_ai_recommendations
                SET status='accepted', accepted_at=NOW()
                WHERE id=%s
                RETURNING trigger_type, trigger_value, recommendation
            """, (rec_id,))
            row = cur.fetchone()
            if row:
                trigger_pattern = f"{row[0]}:{row[1]}"
                rec_text        = row[2] or ""
                # Upsert into learnings
                cur.execute("""
                    INSERT INTO awr_ai_learnings
                      (trigger_pattern, accepted_recommendation, times_accepted, last_seen)
                    VALUES (%s, %s, 1, NOW())
                    ON CONFLICT (trigger_pattern) DO UPDATE
                      SET times_accepted = awr_ai_learnings.times_accepted + 1,
                          accepted_recommendation = EXCLUDED.accepted_recommendation,
                          last_seen = NOW()
                """, (trigger_pattern, rec_text[:2000]))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to accept recommendation: {e}")
        return False


def reject_recommendation(rec_id: int, feedback: str) -> bool:
    """Mark as rejected with DBA feedback."""
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE awr_ai_recommendations
                SET status='rejected', rejected_at=NOW(), dba_feedback=%s
                WHERE id=%s
            """, (feedback, rec_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to reject recommendation: {e}")
        return False


def revise_recommendation(rec_id: int, additional_context: str, config: dict = None) -> dict:
    """Re-run AI with additional DBA context."""
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT dbname, trigger_type, trigger_value,
                       ai_prompt, begin_snap, end_snap
                FROM awr_ai_recommendations WHERE id=%s
            """, (rec_id,))
            row = cur.fetchone()
        conn.close()
        if not row:
            return {"ok": False, "error": "Recommendation not found"}

        dbname, ttype, tval, orig_prompt, b_snap, e_snap = row

        # Re-run with additional context
        # Pass empty metrics — the prompt already has the original context
        result = get_ai_recommendation(
            dbname, ttype, tval,
            metrics={},   # original metrics baked into prompt already
            dba_feedback=additional_context,
            config=config,
        )

        if result["ok"]:
            from db import get_db_connection
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE awr_ai_recommendations
                    SET status='revised', revised_at=NOW(),
                        revised_prompt=%s, revised_response=%s,
                        dba_feedback=%s
                    WHERE id=%s
                """, (result["prompt"], result["response"],
                      additional_context, rec_id))
            conn.commit()
            conn.close()

        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}
