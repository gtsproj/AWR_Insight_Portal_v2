-- schema/execution_plans.sql
-- Tables for execution plan storage and analysis

CREATE TABLE IF NOT EXISTS awr_execution_plans (
    id              SERIAL PRIMARY KEY,
    dbname          TEXT        NOT NULL,
    instance        TEXT,
    sql_id          TEXT        NOT NULL,
    plan_hash_value TEXT,
    snap_time       TIMESTAMP,
    begin_snap      INT,
    step_id         INT,
    parent_id       INT,
    operation       TEXT,
    options         TEXT,
    object_owner    TEXT,
    object_name     TEXT,
    object_type     TEXT,
    cost            NUMERIC,
    cardinality     NUMERIC,    -- estimated rows
    bytes           NUMERIC,
    time_secs       NUMERIC,
    partition_start TEXT,
    partition_stop  TEXT,
    access_predicates  TEXT,
    filter_predicates  TEXT,
    projection         TEXT,
    note               TEXT,
    upload_source   TEXT    DEFAULT 'paste',  -- paste | file | direct_db
    uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_hash        CHAR(32),
    CONSTRAINT uq_exec_plan UNIQUE (dbname, sql_id, plan_hash_value, step_id)
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_exec_plan_sql    ON awr_execution_plans (dbname, sql_id);
CREATE INDEX IF NOT EXISTS idx_exec_plan_snap   ON awr_execution_plans (dbname, begin_snap);
CREATE INDEX IF NOT EXISTS idx_exec_plan_obj    ON awr_execution_plans (object_owner, object_name);
CREATE INDEX IF NOT EXISTS idx_exec_plan_fts    ON awr_execution_plans 
    USING gin(to_tsvector('english', COALESCE(object_name,'') || ' ' || COALESCE(operation,'')));

-- Plan analysis flags (auto-set by plan_parser.py on insert)
ALTER TABLE awr_execution_plans ADD COLUMN IF NOT EXISTS has_full_scan    BOOLEAN DEFAULT FALSE;
ALTER TABLE awr_execution_plans ADD COLUMN IF NOT EXISTS has_cartesian     BOOLEAN DEFAULT FALSE;
ALTER TABLE awr_execution_plans ADD COLUMN IF NOT EXISTS has_sort_spill    BOOLEAN DEFAULT FALSE;
ALTER TABLE awr_execution_plans ADD COLUMN IF NOT EXISTS plan_warning      TEXT;

-- Full-text search on SQL text (pg_trgm for LIKE search)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_sql_text_trgm ON awr_sql_text
    USING gin(sql_text gin_trgm_ops);
