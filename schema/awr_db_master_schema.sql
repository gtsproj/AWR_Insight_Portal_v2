-- ============================================================
-- AWR Insight Portal v2 — DB Master Table
-- Controls which databases are licensed for AWR parsing
-- Run as: psql -U postgres -d postgres -f awr_db_master_schema.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS awr_db_master (
    id           SERIAL PRIMARY KEY,
    db_name      TEXT NOT NULL,          -- DB name (matches awr_reports/ folder)
    instance_name TEXT,                  -- Oracle instance name (SID)
    inst_no      INTEGER DEFAULT 1,      -- Instance number (RAC: 1,2,3...)
    host_name    TEXT,                   -- Server hostname
    db_type      TEXT DEFAULT 'STANDALONE', -- STANDALONE | RAC | CDB | PDB
    description  TEXT,                  -- Optional notes
    active       BOOLEAN DEFAULT TRUE,  -- FALSE = temporarily disabled
    added_at     TIMESTAMP DEFAULT NOW(),
    added_by     TEXT DEFAULT 'admin',
    CONSTRAINT uq_db_master_db_inst UNIQUE (db_name, inst_no)
);

-- Index for fast lookup by queue processor
CREATE INDEX IF NOT EXISTS idx_db_master_db_name
    ON awr_db_master (db_name, active);

-- Comments
COMMENT ON TABLE awr_db_master IS
    'Licensed database registry. Only DBs in this table will be parsed by the queue processor.';
COMMENT ON COLUMN awr_db_master.db_name IS
    'Database name — must match the folder name under awr_reports/';
COMMENT ON COLUMN awr_db_master.instance_name IS
    'Oracle instance name (SID). For RAC, each node has its own instance name.';
COMMENT ON COLUMN awr_db_master.inst_no IS
    'Instance number. Standalone=1, RAC: 1,2,3... per node.';
COMMENT ON COLUMN awr_db_master.db_type IS
    'STANDALONE | RAC | CDB | PDB';
COMMENT ON COLUMN awr_db_master.active IS
    'FALSE = parsing paused for this DB (does not consume a license slot when inactive? TBD)';
