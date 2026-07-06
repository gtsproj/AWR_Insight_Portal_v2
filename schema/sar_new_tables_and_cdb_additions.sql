-- ============================================================
-- schema/sar_new_tables.sql
-- New SAR tables for: Network, Paging, Context Switch, Load Average
-- Run ONCE against the awrparser tablespace
-- ============================================================

-- ── Network Interface Stats (sar -n DEV) ──────────────────────────────
DROP TABLE IF EXISTS sar_network_stats;

CREATE TABLE sar_network_stats (
    id              SERIAL PRIMARY KEY,
    hostname        TEXT        NOT NULL,
    snap_time       TIMESTAMP   NOT NULL,
    iface           TEXT        NOT NULL,
    rxpck_per_sec   NUMERIC,
    txpck_per_sec   NUMERIC,
    rxkb_per_sec    NUMERIC,
    txkb_per_sec    NUMERIC,
    rxcmp_per_sec   NUMERIC,
    txcmp_per_sec   NUMERIC,
    rxmcst_per_sec  NUMERIC,
    ifutil_pct      NUMERIC,
    row_hash        CHAR(32)    NOT NULL,
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_network UNIQUE (hostname, snap_time, iface, row_hash)
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_sar_network_host_time ON sar_network_stats (hostname, snap_time);
CREATE INDEX IF NOT EXISTS idx_sar_network_iface     ON sar_network_stats (iface);

-- ── Paging Stats (sar -B) ─────────────────────────────────────────────
DROP TABLE IF EXISTS sar_paging_stats;

CREATE TABLE sar_paging_stats (
    id               SERIAL PRIMARY KEY,
    hostname         TEXT        NOT NULL,
    snap_time        TIMESTAMP   NOT NULL,
    pgpgin_per_sec   NUMERIC,          -- Pages read in from disk
    pgpgout_per_sec  NUMERIC,          -- Pages written to disk
    fault_per_sec    NUMERIC,          -- Total page faults (minor + major)
    majflt_per_sec   NUMERIC,          -- Major page faults (disk reads) — KEY METRIC
    pgfree_per_sec   NUMERIC,          -- Pages freed by page daemon
    pgscank_per_sec  NUMERIC,          -- Pages scanned by kswapd
    pgscand_per_sec  NUMERIC,          -- Pages scanned directly
    pgsteal_per_sec  NUMERIC,          -- Pages stolen from cache
    vmeff_pct        NUMERIC,          -- VM efficiency: pgsteal/pgscan ratio
    row_hash         CHAR(32)    NOT NULL,
    created_at       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_paging UNIQUE (hostname, snap_time, row_hash)
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_sar_paging_host_time  ON sar_paging_stats (hostname, snap_time);
CREATE INDEX IF NOT EXISTS idx_sar_paging_majflt     ON sar_paging_stats (hostname, majflt_per_sec DESC);

-- ── Context Switch Stats (sar -w) ─────────────────────────────────────
DROP TABLE IF EXISTS sar_ctxswitch_stats;

CREATE TABLE sar_ctxswitch_stats (
    id              SERIAL PRIMARY KEY,
    hostname        TEXT        NOT NULL,
    snap_time       TIMESTAMP   NOT NULL,
    proc_per_sec    NUMERIC,           -- Process creation rate
    cswch_per_sec   NUMERIC,           -- Context switches/sec — KEY METRIC
    row_hash        CHAR(32)    NOT NULL,
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_ctxswitch UNIQUE (hostname, snap_time, row_hash)
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_sar_ctxswitch_host_time ON sar_ctxswitch_stats (hostname, snap_time);
CREATE INDEX IF NOT EXISTS idx_sar_ctxswitch_cswch     ON sar_ctxswitch_stats (hostname, cswch_per_sec DESC);

-- ── Load Average Stats (sar -q) ───────────────────────────────────────
DROP TABLE IF EXISTS sar_loadavg_stats;

CREATE TABLE sar_loadavg_stats (
    id          SERIAL PRIMARY KEY,
    hostname    TEXT        NOT NULL,
    snap_time   TIMESTAMP   NOT NULL,
    runq_sz     NUMERIC,               -- Run queue depth — KEY METRIC
    plist_sz    NUMERIC,               -- Process/thread list size
    ldavg_1     NUMERIC,               -- 1-minute load average
    ldavg_5     NUMERIC,               -- 5-minute load average
    ldavg_15    NUMERIC,               -- 15-minute load average
    blocked     NUMERIC,               -- Processes blocked waiting for I/O — KEY METRIC
    row_hash    CHAR(32)    NOT NULL,
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_loadavg UNIQUE (hostname, snap_time, row_hash)
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_sar_loadavg_host_time ON sar_loadavg_stats (hostname, snap_time);
CREATE INDEX IF NOT EXISTS idx_sar_loadavg_runq      ON sar_loadavg_stats (hostname, runq_sz DESC);
CREATE INDEX IF NOT EXISTS idx_sar_loadavg_blocked   ON sar_loadavg_stats (hostname, blocked DESC);


-- ============================================================
-- schema/awr_cdb_rac_additions.sql
-- Add CDB/PDB/RAC columns to AWR tables for Deliverable #6
-- Run against existing awr_parser schema (ALTER TABLE)
-- ============================================================

-- ── awr_db_info: add CDB and PDB columns ──────────────────────────────
-- CDB column already exists as TEXT — ensure it is present
ALTER TABLE awr_db_info ADD COLUMN IF NOT EXISTS cdb         TEXT;
ALTER TABLE awr_db_info ADD COLUMN IF NOT EXISTS pdb_name    TEXT;
ALTER TABLE awr_db_info ADD COLUMN IF NOT EXISTS rac_nodes   INT;
-- source_type: 'local_file' | 'central_repo' | 'direct_db'
ALTER TABLE awr_db_info ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'local_file';
ALTER TABLE awr_db_info ADD COLUMN IF NOT EXISTS repo_path   TEXT;

-- ── Add pdb_name to all AWR metric tables ─────────────────────────────
-- Only needed when CDB=YES; NULL for non-CDB databases
ALTER TABLE awr_load_profile           ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_instance_efficiency    ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_io_profile             ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_time_model_stats       ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_os_statistics          ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_foreground_wait_class  ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_foreground_wait_events ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_background_wait_events ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_elapsed_time       ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_cpu_time           ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_gets               ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_reads              ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_executions         ADD COLUMN IF NOT EXISTS pdb_name TEXT;
-- ALTER TABLE awr_sql_parse_calls     ADD COLUMN IF NOT EXISTS pdb_name TEXT;  -- table not in schema
-- ALTER TABLE awr_sql_sharable_mem    ADD COLUMN IF NOT EXISTS pdb_name TEXT;  -- table not in schema
-- ALTER TABLE awr_sql_version_count   ADD COLUMN IF NOT EXISTS pdb_name TEXT;  -- table not in schema
ALTER TABLE awr_seg_logical_reads      ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_phy_reads          ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_phy_read_req       ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_phy_writes         ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_phy_write_req      ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_direct_phy_reads   ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_direct_phy_writes  ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_buff_busy_waits    ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_row_lck_waits      ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_itl_waits          ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_cr_blk_rec         ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_cur_blk_rec        ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_gbl_cache_buff_busy ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_db_blk_chg         ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_table_scan         ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_seg_opt_reads          ADD COLUMN IF NOT EXISTS pdb_name TEXT;

-- ── Indexes for pdb_name filtering (only on high-query tables) ────────
CREATE INDEX IF NOT EXISTS idx_awrfw_pdb  ON awr_foreground_wait_events (dbname, pdb_name, snap_time);
CREATE INDEX IF NOT EXISTS idx_awrsql_pdb ON awr_sql_elapsed_time (dbname, pdb_name, snap_time);
CREATE INDEX IF NOT EXISTS idx_awrseg_pdb ON awr_seg_logical_reads (dbname, pdb_name, snap_time);

-- ── Central repo tracking table ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS awr_repo_scan_log (
    id           SERIAL PRIMARY KEY,
    repo_path    TEXT        NOT NULL,
    db_name      TEXT        NOT NULL,
    instance     TEXT,
    pdb_name     TEXT,
    filename     TEXT        NOT NULL,
    file_hash    CHAR(32),
    scanned_at   TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    status       TEXT        DEFAULT 'PENDING',   -- PENDING|DONE|FAILED
    error        TEXT,
    CONSTRAINT uq_repo_scan UNIQUE (repo_path, db_name, filename, file_hash)
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_repo_scan_db     ON awr_repo_scan_log (db_name, status);
CREATE INDEX IF NOT EXISTS idx_repo_scan_status ON awr_repo_scan_log (status, scanned_at);
