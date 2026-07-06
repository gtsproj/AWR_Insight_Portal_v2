-- ============================================================
-- AWR Insight Portal — DBA Object Metadata & AI Tables
-- Run: psql -U postgres -d postgres -f awr_object_metadata_schema.sql
-- ============================================================

-- ── DBA Object Metadata ───────────────────────────────────────────────
-- Stores Oracle dictionary data extracted manually or via direct connection

CREATE TABLE IF NOT EXISTS awr_object_metadata (
    id              SERIAL PRIMARY KEY,
    dbname          TEXT NOT NULL,
    owner           TEXT NOT NULL,
    object_name     TEXT NOT NULL,
    object_type     TEXT NOT NULL,   -- TABLE, INDEX, VIEW, PARTITION etc.
    -- Table stats
    num_rows        BIGINT,
    blocks          BIGINT,
    avg_row_len     INTEGER,
    last_analyzed   TIMESTAMP,
    partitioned     TEXT,            -- YES/NO
    row_movement    TEXT,
    compression     TEXT,
    -- Index stats
    index_type      TEXT,            -- NORMAL, BITMAP, FUNCTION-BASED etc.
    uniqueness      TEXT,            -- UNIQUE / NONUNIQUE
    blevel          INTEGER,         -- B-tree depth
    leaf_blocks     BIGINT,
    distinct_keys   BIGINT,
    clustering_factor BIGINT,
    status          TEXT,            -- VALID / UNUSABLE
    -- Index columns (stored as JSON array)
    index_columns   JSONB,           -- [{"col":"MOBILE_NO","pos":1,"desc":"ASC"}]
    -- Partition info
    partition_type  TEXT,            -- RANGE, LIST, HASH, COMPOSITE
    partition_count INTEGER,
    subpartition_type TEXT,
    -- Source tracking
    source          TEXT DEFAULT 'csv',  -- csv | direct_db
    uploaded_at     TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_object UNIQUE (dbname, owner, object_name, object_type)
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_obj_meta_db     ON awr_object_metadata(dbname);
CREATE INDEX IF NOT EXISTS idx_obj_meta_owner  ON awr_object_metadata(owner, object_name);
CREATE INDEX IF NOT EXISTS idx_obj_meta_type   ON awr_object_metadata(object_type);

-- ── AI Recommendation Feedback ────────────────────────────────────────
-- Stores AI recommendations and DBA accept/reject decisions

CREATE TABLE IF NOT EXISTS awr_ai_recommendations (
    id              SERIAL PRIMARY KEY,
    dbname          TEXT NOT NULL,
    instance        TEXT,
    begin_snap      INTEGER,
    end_snap        INTEGER,
    -- What triggered the recommendation
    trigger_type    TEXT NOT NULL,   -- wait | sql | segment | overall
    trigger_value   TEXT NOT NULL,   -- event name / sql_id / object_name
    severity        TEXT,            -- high | medium | low
    -- AI generated content
    ai_provider     TEXT,            -- ollama | claude | openai | gemini
    ai_model        TEXT,            -- llama3.1:8b etc.
    ai_prompt       TEXT,            -- full prompt sent to AI
    ai_response     TEXT NOT NULL,   -- raw AI response
    root_cause      TEXT,            -- extracted root cause
    recommendation  TEXT,            -- extracted recommendation
    -- DBA decision
    status          TEXT DEFAULT 'pending',  -- pending | accepted | rejected | revised
    dba_feedback    TEXT,            -- DBA comments on rejection
    revised_prompt  TEXT,            -- Additional context from DBA
    revised_response TEXT,           -- AI re-response after DBA input
    -- Learning
    accepted_at     TIMESTAMP,
    rejected_at     TIMESTAMP,
    revised_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_ai_rec_dbname  ON awr_ai_recommendations(dbname, begin_snap);
CREATE INDEX IF NOT EXISTS idx_ai_rec_trigger ON awr_ai_recommendations(trigger_type, trigger_value);
CREATE INDEX IF NOT EXISTS idx_ai_rec_status  ON awr_ai_recommendations(status);

-- ── AI Learning Store ─────────────────────────────────────────────────
-- Accepted recommendations become training examples for future analysis

CREATE TABLE IF NOT EXISTS awr_ai_learnings (
    id              SERIAL PRIMARY KEY,
    trigger_pattern TEXT NOT NULL,   -- normalized pattern e.g. "wait:db file sequential read"
    context_summary TEXT,            -- brief context when this was observed
    accepted_recommendation TEXT NOT NULL,  -- what the DBA accepted
    times_accepted  INTEGER DEFAULT 1,
    last_seen       TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_learning UNIQUE (trigger_pattern)
) TABLESPACE awrparser;

-- ── Add AI mode flag to portal_config (if not exists) ────────────────
INSERT INTO portal_config (key, value, description, section)
VALUES
    ('ai_mode',           'rules',        'Recommendation mode: rules | local_ai | cloud_ai', 'ai'),
    ('ai_local_url',      'http://localhost:11434', 'Ollama base URL', 'ai'),
    ('ai_local_model',    'llama3.1:8b',  'Ollama model name', 'ai'),
    ('ai_cloud_provider', 'claude',        'Cloud AI provider', 'ai'),
    ('ai_cloud_api_key',  '',             'Cloud AI API key', 'ai'),
    ('ai_cloud_model',    '',             'Cloud AI model', 'ai')
ON CONFLICT (key) DO NOTHING;
