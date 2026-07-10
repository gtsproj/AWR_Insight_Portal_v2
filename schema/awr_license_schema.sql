-- ============================================================
-- AWR Insight Portal v2 — License Schema
-- Run: psql -U postgres -d postgres -f awr_license_schema.sql
-- ============================================================

-- ── Licensed databases (explicit registration) ───────────────────
CREATE TABLE IF NOT EXISTS awr_licensed_dbs (
    id          SERIAL PRIMARY KEY,
    dbname      TEXT NOT NULL UNIQUE,
    db_type     TEXT DEFAULT 'STANDALONE',  -- STANDALONE | CDB | PDB | RAC_NODE
    cdb_name    TEXT,                        -- parent CDB name if PDB
    is_pdb      BOOLEAN DEFAULT FALSE,
    rac_cluster TEXT,                        -- RAC cluster name if applicable
    host        TEXT,                        -- hostname
    registered_at TIMESTAMP DEFAULT NOW(),
    registered_by TEXT,
    active      BOOLEAN DEFAULT TRUE,
    notes       TEXT
) TABLESPACE awrparser;

-- ── License audit log ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS awr_license_audit (
    id          SERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,  -- ok | expired | grace | db_exceeded | sar_exceeded | no_key | mac_mismatch
    message     TEXT,
    db_count    INTEGER,
    sar_count   INTEGER,
    event_time  TIMESTAMP DEFAULT NOW()
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_lic_audit_time
    ON awr_license_audit(event_time DESC);

-- ── Add license config keys to portal_config ─────────────────────
INSERT INTO portal_config (key, value, description, section) VALUES
    ('license_key',          '',    'License key (AVK-TIER-PAYLOAD)',         'license'),
    ('license_customer',     '',    'Customer name',                          'license'),
    ('license_db_count',     '2',   'Licensed Oracle DB instance count',      'license'),
    ('license_sar_count',    '2',   'Licensed SAR server count',              'license'),
    ('license_expiry',       '',    'License expiry date (YYYY-MM-DD)',       'license'),
    ('license_tier',         '',    'License tier (set automatically)',       'license'),
    ('ai_monthly_limit',     '200', 'Monthly cloud AI recommendation cap',    'license')
ON CONFLICT (key) DO NOTHING;

-- ── Add is_pdb and cdb_name columns to awr_load_profile if missing ─
ALTER TABLE awr_load_profile
    ADD COLUMN IF NOT EXISTS is_pdb    BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS cdb_name  TEXT,
    ADD COLUMN IF NOT EXISTS db_role   TEXT,
    ADD COLUMN IF NOT EXISTS db_type   TEXT DEFAULT 'STANDALONE';

-- MAC override for license validation
INSERT INTO portal_config (key, value, description, section)
VALUES ('license_mac_override', '', 'Pinned MAC address for license validation', 'license')
ON CONFLICT (key) DO NOTHING;
