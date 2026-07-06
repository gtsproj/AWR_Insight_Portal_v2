-- ============================================================
-- schema/recommendations_and_comparison.sql
-- Tables for: Recommendation results, AWR comparison tags
-- ============================================================

-- ── Stored recommendation results ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS awr_recommendations (
    id               SERIAL PRIMARY KEY,
    dbname           TEXT        NOT NULL,
    begin_snap       INT         NOT NULL,
    end_snap         INT         NOT NULL,
    rule_id          TEXT        NOT NULL,
    category         TEXT,                  -- wait|sql|segment|instance_efficiency|sga|pga|undo|redo|sar_correlated
    severity         TEXT,                  -- low|medium|high|critical
    title            TEXT,
    event_or_object  TEXT,                  -- wait event name, segment name, or sql_id
    root_cause       TEXT,
    resolution_json  JSONB,                 -- array of resolution steps
    ai_summary       TEXT,                  -- AI-generated narrative (empty if rules-only mode)
    created_at       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_rec UNIQUE (dbname, begin_snap, end_snap, rule_id)
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_awr_rec_db_snap   ON awr_recommendations (dbname, begin_snap, end_snap);
CREATE INDEX IF NOT EXISTS idx_awr_rec_severity  ON awr_recommendations (severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_awr_rec_category  ON awr_recommendations (category, severity);

-- ── AWR comparison tags for before/after analysis ─────────────────────
CREATE TABLE IF NOT EXISTS awr_comparison_tags (
    id           SERIAL PRIMARY KEY,
    tag_name     TEXT        NOT NULL,      -- e.g. "Before index fix" or "After stats gather"
    dbname       TEXT        NOT NULL,
    instance     TEXT,
    snap_start   INT         NOT NULL,
    snap_end     INT         NOT NULL,
    tag_type     TEXT        NOT NULL,      -- 'baseline' | 'current'
    notes        TEXT,
    created_by   TEXT        DEFAULT 'DBA',
    created_at   TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_tag UNIQUE (dbname, tag_name, tag_type)
) TABLESPACE awrparser;

-- ── DBA change log (events correlated with dashboards) ────────────────
CREATE TABLE IF NOT EXISTS awr_change_log (
    id           SERIAL PRIMARY KEY,
    dbname       TEXT        NOT NULL,
    event_time   TIMESTAMP   NOT NULL,
    change_type  TEXT,                      -- e.g. 'index_added','stats_gathered','patch_applied','config_change'
    description  TEXT        NOT NULL,
    changed_by   TEXT,
    impact       TEXT,                      -- 'positive'|'negative'|'neutral'|'unknown'
    created_at   TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_change_log_db_time ON awr_change_log (dbname, event_time);
