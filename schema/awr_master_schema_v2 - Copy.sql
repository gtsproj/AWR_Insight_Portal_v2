
-- =============================================================================
-- PRE-INSTALL SETUP (PostgreSQL 12)
-- =============================================================================
-- Run the following commands manually before executing this script.

-- Connect to postgres database:
--psql -U postgres -d postgres

-- =============================================================================
-- AWR INSIGHT PORTAL v2 — MASTER DATABASE SCHEMA
-- =============================================================================
-- Single script for complete fresh installation.
-- Creates all tables, SAR tables, materialized views, indexes, and seed data.
--
-- HOW TO RUN:
--   Windows:
--     psql -U postgres -d postgres ^
--          -v waitevent_path="C:/AWR_Insight_Portal_v2/waitevent.txt" ^
--          -f "schema/awr_master_schema_v2.sql"
--
--   Linux:
--     psql -U postgres -d postgres \
--          -v waitevent_path="/opt/AWR_Insight_Portal_v2/waitevent.txt" \
--          -f schema/awr_master_schema_v2.sql
--
-- PREREQUISITES (run these manually BEFORE this script):
--
--   1. Create tablespace folders on disk:
--        Windows: mkdir C:\PostgreSQL\tablespaces\awrparser
--                 mkdir C:\PostgreSQL\tablespaces\awrparser_idx
--        Linux:   mkdir -p /var/lib/postgresql/tablespaces/awrparser
--                 mkdir -p /var/lib/postgresql/tablespaces/awrparser_idx
--
--   2. Create tablespaces in PostgreSQL:
--        CREATE TABLESPACE awrparser     LOCATION 'C:/PostgreSQL/tablespaces/awrparser';
--        CREATE TABLESPACE awrparser_idx LOCATION 'C:/PostgreSQL/tablespaces/awrparser_idx';
--
--      (Linux paths accordingly. Or use pg_default if you don't want custom tablespaces.)
--
--   3. Ensure you are connected to the correct database:
--        \connect postgres    -- or your target database name
--
-- WHAT THIS SCRIPT CREATES:
--   Section 0 : Extensions (pg_trgm)
--   Section 1 : Drop all existing objects (clean slate)
--   Section 2 : AWR tables (all original tables + v2 CDB/PDB/RAC columns)
--   Section 3 : SAR tables (original 4 + 4 new v2 tables)
--   Section 4 : New v2 tables (recommendations, comparison, plans, anomalies)
--   Section 5 : Load wait event master data
--   Section 6 : Materialized views + indexes
--   Section 7 : Recommendation rules table + 40+ seed rules
--   Section 8 : Helper MVs, normalized SQL text, per-snap score views
-- =============================================================================


-- =============================================================================
-- SECTION 0: DROP ALL EXISTING OBJECTS (clean slate)
-- =============================================================================
SET search_path TO awrparser;
DROP EXTENSION IF EXISTS pg_trgm CASCADE;
--DROP VIEW IF EXISTS awr_sql_score_by_snap CASCADE;
--DROP VIEW IF EXISTS awr_seg_score_by_snap CASCADE;
DROP MATERIALIZED VIEW IF EXISTS awr_sql_summary_mv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS awr_segment_summary_mv CASCADE;
--DROP MATERIALIZED VIEW IF EXISTS awr_seg_summary_mv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS awr_fg_wait_summary_mv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS awr_fg_wait_event_summary_mv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS awr_bg_wait_summary_mv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS awr_bg_wait_event_summary_mv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS awr_wait_summary_mv CASCADE;
--DROP MATERIALIZED VIEW IF EXISTS awr_sql_object_map_mv CASCADE;
--DROP MATERIALIZED VIEW IF EXISTS awr_sql_text_norm CASCADE;
DROP TABLE IF EXISTS awr_anomalies CASCADE;
DROP TABLE IF EXISTS awr_execution_plans CASCADE;
DROP TABLE IF EXISTS awr_recommendations CASCADE;
DROP TABLE IF EXISTS awr_comparison_tags CASCADE;
DROP TABLE IF EXISTS awr_change_log CASCADE;
DROP TABLE IF EXISTS awr_repo_scan_log CASCADE;
DROP TABLE IF EXISTS awr_recommendation_rules CASCADE;
DROP TABLE IF EXISTS awr_wait_event_master CASCADE;
DROP TABLE IF EXISTS awr_seg_cur_blk_rec CASCADE;
DROP TABLE IF EXISTS awr_seg_cr_blk_rec CASCADE;
DROP TABLE IF EXISTS awr_seg_gbl_cache_buff_busy CASCADE;
DROP TABLE IF EXISTS awr_seg_buff_busy_waits CASCADE;
DROP TABLE IF EXISTS awr_seg_itl_waits CASCADE;
DROP TABLE IF EXISTS awr_seg_row_lck_waits CASCADE;
DROP TABLE IF EXISTS awr_seg_db_blk_chg CASCADE;
DROP TABLE IF EXISTS awr_seg_table_scan CASCADE;
DROP TABLE IF EXISTS awr_seg_table_scans CASCADE;
DROP TABLE IF EXISTS awr_seg_direct_phy_writes CASCADE;
DROP TABLE IF EXISTS awr_seg_phy_write_req CASCADE;
DROP TABLE IF EXISTS awr_seg_phy_writes CASCADE;
DROP TABLE IF EXISTS awr_seg_direct_phy_reads CASCADE;
DROP TABLE IF EXISTS awr_seg_opt_reads CASCADE;
DROP TABLE IF EXISTS awr_seg_unopt_reads CASCADE;
DROP TABLE IF EXISTS awr_seg_phy_read_req CASCADE;
DROP TABLE IF EXISTS awr_seg_phy_reads CASCADE;
DROP TABLE IF EXISTS awr_seg_logical_reads CASCADE;
DROP TABLE IF EXISTS awr_undo_statistics CASCADE;
DROP TABLE IF EXISTS awr_enqueue_statistics CASCADE;
DROP TABLE IF EXISTS awr_buffer_wait_statistics CASCADE;
DROP TABLE IF EXISTS awr_advisory_pga CASCADE;
DROP TABLE IF EXISTS awr_advisory_sga CASCADE;
DROP TABLE IF EXISTS awr_file_io_stats CASCADE;
DROP TABLE IF EXISTS awr_tablespace_io_stats CASCADE;
DROP TABLE IF EXISTS awr_iostat_func_filetype CASCADE;
DROP TABLE IF EXISTS awr_iostat_filetype CASCADE;
DROP TABLE IF EXISTS awr_iostat_function CASCADE;
DROP TABLE IF EXISTS awr_instance_activity_stats CASCADE;
DROP TABLE IF EXISTS awr_sql_text CASCADE;
DROP TABLE IF EXISTS awr_sql_cluster_wait_time CASCADE;
DROP TABLE IF EXISTS awr_sql_parsed_calls CASCADE;
DROP TABLE IF EXISTS awr_sql_executions CASCADE;
DROP TABLE IF EXISTS awr_sql_phy_reads_unopt CASCADE;
DROP TABLE IF EXISTS awr_sql_reads CASCADE;
DROP TABLE IF EXISTS awr_sql_gets CASCADE;
DROP TABLE IF EXISTS awr_sql_user_io_time CASCADE;
DROP TABLE IF EXISTS awr_sql_cpu_time CASCADE;
DROP TABLE IF EXISTS awr_sql_elapsed_time CASCADE;
DROP TABLE IF EXISTS awr_background_wait_events CASCADE;
DROP TABLE IF EXISTS awr_foreground_wait_events CASCADE;
DROP TABLE IF EXISTS awr_foreground_wait_class CASCADE;
DROP TABLE IF EXISTS awr_os_statistics CASCADE;
DROP TABLE IF EXISTS awr_time_model_stats CASCADE;
DROP TABLE IF EXISTS awr_io_profile CASCADE;
DROP TABLE IF EXISTS awr_instance_efficiency CASCADE;
DROP TABLE IF EXISTS awr_load_profile CASCADE;
DROP TABLE IF EXISTS awr_db_info CASCADE;
DROP TABLE IF EXISTS sar_loadavg_stats CASCADE;
DROP TABLE IF EXISTS sar_ctxswitch_stats CASCADE;
DROP TABLE IF EXISTS sar_paging_stats CASCADE;
DROP TABLE IF EXISTS sar_network_stats CASCADE;
DROP TABLE IF EXISTS sar_disk_stats CASCADE;
DROP TABLE IF EXISTS sar_swap_stats CASCADE;
DROP TABLE IF EXISTS sar_memory_stats CASCADE;
DROP TABLE IF EXISTS sar_cpu_stats CASCADE;
DROP SCHEMA IF EXISTS awrparser CASCADE;


-- =============================================================================
-- SECTION 1: DROP  AND CREATE TABLESPACES 
-- =============================================================================

-- Drop and recreate schema:

-- Drop tablespaces (must not be in use) and remove data directories manually:
DROP TABLESPACE IF EXISTS awrparser_idx;
DROP TABLESPACE IF EXISTS awrparser;

-- Windows CMD: create tablespace root folder if it does not exist
--\!IF NOT EXIST "C:\PostgreSQL\tablespaces" mkdir "C:\PostgreSQL\tablespaces"

-- Remove old tablespace folders if present:
--\!rmdir /S /Q "C:\PostgreSQL\tablespaces\awrparser"
--\!rmdir /S /Q "C:\PostgreSQL\tablespaces\awrparser_idx"

-- Recreate folders:
--\!mkdir "C:\PostgreSQL\tablespaces\awrparser"
--\!mkdir "C:\PostgreSQL\tablespaces\awrparser_idx"

-- Recreate tablespaces:
CREATE TABLESPACE awrparser
     LOCATION 'C:/PostgreSQL/tablespaces/awrparser';

 CREATE TABLESPACE awrparser_idx
     LOCATION 'C:/PostgreSQL/tablespaces/awrparser_idx';


-- =============================================================================
-- SECTION 2: REQUIRED EXTENSIONS
-- =============================================================================
CREATE SCHEMA awrparser;

SET search_path TO awrparser;


CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =========================================================
--  AWR Parser Database Schema (Phase 1)
--  Includes: DB Info, Load Profile, Efficiency, IO, Waits,
--  SQL, Instance Activity, Undo, Segment, Advisory, OS, BG Waits
-- =========================================================

-- =============================================================================
-- SECTION 3: CREATE AWR SCHEMA, TABLES, INDEXES, MATERIALIZED VIEWS
-- =============================================================================


-- ======================
-- DB Info
-- ======================
CREATE TABLE awr_db_info (
    id SERIAL PRIMARY KEY,
    db_name TEXT NOT NULL,
    edition TEXT,
	release TEXT,
    instance TEXT NOT NULL,
    rac TEXT,
    host_name TEXT,
    platform TEXT,
    cpu INT,
    cores INT,
    sockets INT,
    memory NUMERIC,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_db_info UNIQUE (db_name, instance, row_hash)
) tablespace awrparser;

-- ======================
-- Load Profile
-- ======================
CREATE TABLE awr_load_profile (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    metric TEXT,
    per_sec NUMERIC,
    per_transaction NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_load_profile UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Instance Efficiency
-- ======================
CREATE TABLE awr_instance_efficiency (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    metric TEXT,
    value NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_instance_eff UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- IO Profile
-- ======================
CREATE TABLE awr_io_profile (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    metric TEXT,
    read_per_sec NUMERIC,
    write_per_sec NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_io_profile UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Time Model Stats
-- ======================
CREATE TABLE awr_time_model_stats (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    statistic_name TEXT,
    time_s NUMERIC,
    pct_of_db_time NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_time_model UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- OS Statistics
-- ======================
CREATE TABLE awr_os_statistics (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    statistic TEXT,
    value NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_os_stats UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Foreground Wait Class
-- ======================
CREATE TABLE awr_foreground_wait_class (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    wait_class TEXT,
    waits BIGINT,
    total_wait_time_s NUMERIC,
    avg_wait_ms NUMERIC,
    pct_db_time NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_fg_wait_class UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Foreground Wait Events
-- ======================
CREATE TABLE awr_foreground_wait_events (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    event TEXT,
    waits NUMERIC,
    total_wait_time_s NUMERIC,
    avg_wait_ms NUMERIC,
    waits_per_txn NUMERIC,
    pct_db_time NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_fg_wait_events UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Background Wait Events
-- ======================
CREATE TABLE awr_background_wait_events (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    event TEXT,
    waits NUMERIC,
    total_wait_time_s NUMERIC,
    avg_wait_ms NUMERIC,
    waits_per_txn NUMERIC,
    pct_bg_time NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_bg_wait_events UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered By Elapsed Time
-- ======================
CREATE TABLE awr_sql_elapsed_time (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    pct_total NUMERIC,
    pct_cpu NUMERIC,
    pct_io NUMERIC,
    elapsed_time_s NUMERIC,
    elapsed_time_per_exec_s NUMERIC,
	begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sql_elapsed_time UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered By CPU Time
-- ======================
CREATE TABLE awr_sql_cpu_time (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    pct_total NUMERIC,
    pct_cpu NUMERIC,
    pct_io NUMERIC,
    elapsed_time_s NUMERIC,
    cpu_time_s NUMERIC,
    cpu_per_exec_s NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sql_cpu_time UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered By User IO Wait Time
-- ======================
CREATE TABLE awr_sql_user_io_time (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    pct_total NUMERIC,
    pct_cpu NUMERIC,
    pct_io NUMERIC,
    elapsed_time_s NUMERIC,
    user_io_time_s NUMERIC,
    uio_per_exec_s NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sql_user_io_time UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered by Gets
-- ======================
CREATE TABLE awr_sql_gets (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    pct_total NUMERIC,
    pct_cpu NUMERIC,
    pct_io NUMERIC,
    elapsed_time_s NUMERIC,
    buffer_gets NUMERIC,
    gets_per_exec NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sql_gets UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered bY Reads
-- ======================
CREATE TABLE awr_sql_reads (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    pct_total NUMERIC,
    pct_cpu NUMERIC,
    pct_io NUMERIC,
    elapsed_time_s NUMERIC,
    physical_reads NUMERIC,
    reads_per_exec NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sql_reads UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered By Physical Reads (UnOptimized)
-- ======================
CREATE TABLE awr_sql_phy_reads_unopt (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    pct_total NUMERIC,
    unoptimized_read_reqs NUMERIC,
    physical_read_reqs NUMERIC,
    unoptimized_reqs_per_exec NUMERIC,
    pctopt NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_phy_reads_unopt UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered By Exexutions
-- ======================
CREATE TABLE awr_sql_executions (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    pct_cpu NUMERIC,
    pct_io NUMERIC,
    elapsed_time_s NUMERIC,
    rows_processed NUMERIC,
    rows_per_exec NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_executions UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered By Parsed Calls
-- ======================
CREATE TABLE awr_sql_parsed_calls (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    parse_calls NUMERIC,
    pct_total_parses NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_parsed_calls UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Stats Ordered By Cluster Wait Time
-- ======================
CREATE TABLE awr_sql_cluster_wait_time (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_module TEXT,
    executions NUMERIC,
    pct_total NUMERIC,
    pct_cpu NUMERIC,
    pct_io NUMERIC,
    elapsed_time_s NUMERIC,
	pct_clu NUMERIC,
    cluster_wait_time_s NUMERIC,	
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_cluster_wait_time UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- SQL Complete Text
-- ======================
CREATE TABLE awr_sql_text (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sql_id TEXT,
    sql_text TEXT,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sql_text UNIQUE (dbname, sql_id, row_hash)
) tablespace awrparser;

-- ======================
-- Instance Activity Stats
-- ======================
CREATE TABLE awr_instance_activity_stats (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    name TEXT,
    statistic TEXT,
    total BIGINT,
    per_second NUMERIC,
    per_trans NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_inst_activity UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- IOStat by Function
-- ======================
CREATE TABLE awr_iostat_function (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    function_name TEXT,
    reads_mb BIGINT,
    reads_reqs_per_sec NUMERIC,
    reads_data_per_sec NUMERIC,
    writes_mb BIGINT,
    writes_reqs_per_sec NUMERIC,
    writes_data_per_sec_mb NUMERIC,
    waits BIGINT,
    avg_time_ms NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_iostat_function UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- IOStat by Filetype
-- ======================
CREATE TABLE awr_iostat_filetype (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    filetype_name TEXT,
    reads_mb BIGINT,
    reads_reqs_per_sec NUMERIC,
    reads_data_per_sec_mb NUMERIC,
    writes_mb BIGINT,
    writes_reqs_per_sec NUMERIC,
    writes_data_per_sec_mb NUMERIC,
    small_read_ms NUMERIC,
    large_read_ms NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_iostat_filetype UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- IOStat by Function/Filetype
-- ======================
CREATE TABLE awr_iostat_func_filetype (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    function_file_name TEXT,
    reads_mb BIGINT,
    reads_reqs_per_sec NUMERIC,
    reads_data_per_sec_mb NUMERIC,
    writes_mb BIGINT,
    writes_reqs_per_sec NUMERIC,
    writes_data_per_sec_mb NUMERIC,
    waits BIGINT,
    avg_time_ms NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_iostat_func_filetype UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Tablespace IO Stats
-- ======================
CREATE TABLE awr_tablespace_io_stats (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    tablespace TEXT,
    reads NUMERIC,
    avg_read_sec NUMERIC,
    avg_read_ms NUMERIC,
    writes NUMERIC,
    write_avg_sec NUMERIC,
    write_avg_ms NUMERIC,
    buffer_waits NUMERIC,
    avg_buffer_wait_ms NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_tblspc_io UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- File IO Stats
-- ======================
CREATE TABLE awr_file_io_stats (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    tablespace TEXT,
    filename TEXT,
    reads BIGINT,
    avg_read_sec NUMERIC,
    avg_read_ms NUMERIC,
    writes BIGINT,
    write_avg_sec NUMERIC,
    buffer_waits BIGINT,
    avg_buffer_wait_ms NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_file_io UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Advisory SGA
-- ======================
CREATE TABLE awr_advisory_sga (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    sga_target_size_m NUMERIC,
    sga_size_factor NUMERIC,
    est_db_time_s NUMERIC,
    est_physical_reads BIGINT,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_adv_sga UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Advisory PGA
-- ======================
CREATE TABLE awr_advisory_pga (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    pga_target_est_mb NUMERIC,
    size_factor NUMERIC,
    w_a_mb_processed NUMERIC,
    estd_extra_written_to_disk NUMERIC,
    estd_pga_cache_hit_pct NUMERIC,
    estd_pga_overalloc_count BIGINT,
    estd_time NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_adv_pga UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Buffer Wait Statistics
-- ======================
CREATE TABLE awr_buffer_wait_statistics (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    class TEXT,
    waits BIGINT,
    total_wait_time_s NUMERIC,
    avg_time_ms NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_buf_waits UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Enqueue Statistics
-- ======================
CREATE TABLE awr_enqueue_statistics (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    enqueue_type_request_reason TEXT,
    requests BIGINT,
    succ_gets BIGINT,
    failed_gets BIGINT,
    waits BIGINT,
    wt_time_s NUMERIC,
    av_wt_time_ms NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_enqueue_stats UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Undo Statistics
-- ======================
CREATE TABLE awr_undo_statistics (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    num_undo_blocks BIGINT,
    number_of_transactions BIGINT,
    max_qry_len_s NUMERIC,
    max_tx_concy NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_undo_stats UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Logical Read
-- ======================
CREATE TABLE awr_seg_logical_reads (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    logical_reads NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_seg_logical_reads UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Physical Read
-- ======================

CREATE TABLE awr_seg_phy_reads (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    physical_reads NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_phy_reads UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Physical Read Request
-- ======================
CREATE TABLE awr_seg_phy_read_req (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    phys_read_requests NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_Phy_read_req UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - UnOptimized Reads
-- ======================

CREATE TABLE awr_seg_unopt_reads (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    unoptimized_reads NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_unopt_reads UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Optimized Reads
-- ======================

CREATE TABLE awr_seg_opt_reads (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    optimized_reads NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_opt_reads UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Direct Physical Reads
-- ======================

CREATE TABLE awr_seg_direct_phy_reads (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    direct_reads NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_direct_phy_reads UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Physical Writes
-- ======================

CREATE TABLE awr_seg_phy_writes (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    physical_writes NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_phy_writes UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Physical Writes Request
-- ======================

CREATE TABLE awr_seg_phy_write_req (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    phys_write_requests NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_phy_write_req UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Direct Physical Writes 
-- ======================

CREATE TABLE awr_seg_direct_phy_writes (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    direct_writes NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_direct_phy_writes UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Table Scans 
-- ======================

CREATE TABLE awr_seg_table_scan (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    table_scans NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_table_scan UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - DB Block Changes 
-- ======================

CREATE TABLE awr_seg_db_blk_chg (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    db_block_changes NUMERIC,
    pct_of_capture NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_db_blk_chg UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Row Lock Waits 
-- ======================

CREATE TABLE awr_seg_row_lck_waits (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    row_lock_waits NUMERIC,
    pct_of_capture NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_row_lck_waits UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - ITL Waits 
-- ======================
CREATE TABLE awr_seg_itl_waits (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    itl_waits NUMERIC,
    pct_of_capture NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_itl_waits UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Buffer Busy Waits 
-- ======================

CREATE TABLE awr_seg_buff_busy_waits (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    buffer_busy_waits NUMERIC,
    pct_of_capture NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_awr_seg_buff_busy_waits UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Global Cache Buffer Busy Waits 
-- ======================
CREATE TABLE awr_seg_gbl_cache_buff_busy (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    gc_buffer_busy NUMERIC,
    pct_of_capture NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_gbl_cache_buff_busy UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - CR Blocks Received 
-- ======================

CREATE TABLE awr_seg_cr_blk_rec (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    cr_blocks_received NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_cr_blk_rec UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-- ======================
-- Segment Statistics - Current Blocks Received 
-- ======================

CREATE TABLE awr_seg_cur_blk_rec (
    id SERIAL PRIMARY KEY,
    dbname TEXT NOT NULL,
    instance TEXT NOT NULL,
    instnum INT,
    snap_time TIMESTAMP,
    owner TEXT,
    tablespace_name TEXT,
    object_name TEXT,
    subobject_name TEXT,
    obj_type TEXT,
    current_blocks_received NUMERIC,
    pcttotal NUMERIC,
    begin_snap INT,
    row_hash CHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_seg_cur_blk_rec UNIQUE (dbname, instance, begin_snap, row_hash)
) tablespace awrparser;

-----------------------------------------------------
--Wait Event Master table
-----------------------------------------------------
CREATE TABLE awr_wait_event_master (
    event TEXT PRIMARY KEY,
    wait_class TEXT
) TABLESPACE awrparser;

--------------------------------------------------------
--Bulk load from text filename
----------------------------------------------------------
COPY awr_wait_event_master (event, wait_class)
FROM :'waitevent_path'
WITH (FORMAT csv, HEADER true);

---------------------------------------------------------------
--Materialized view for AWR SQL Summary
---------------------------------------------------------------
--DROP MATERIALIZED VIEW IF EXISTS awr_sql_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_sql_summary_mv
TABLESPACE awrparser
AS
 WITH elapsed AS (
         SELECT awr_sql_elapsed_time.dbname,
            awr_sql_elapsed_time.instance,
            awr_sql_elapsed_time.begin_snap,
            min(awr_sql_elapsed_time.snap_time) AS snap_time,
            awr_sql_elapsed_time.sql_id,
            sum(awr_sql_elapsed_time.executions) AS executions,
            avg(awr_sql_elapsed_time.elapsed_time_per_exec_s) AS elapsed_time_per_exec_s,
            avg(awr_sql_elapsed_time.pct_cpu) AS pct_cpu,
            avg(awr_sql_elapsed_time.pct_io) AS pct_io
           FROM awr_sql_elapsed_time
          GROUP BY awr_sql_elapsed_time.dbname, awr_sql_elapsed_time.instance, awr_sql_elapsed_time.begin_snap, awr_sql_elapsed_time.sql_id
        ), cpu AS (
         SELECT awr_sql_cpu_time.dbname,
            awr_sql_cpu_time.instance,
            awr_sql_cpu_time.begin_snap,
            awr_sql_cpu_time.sql_id,
            avg(awr_sql_cpu_time.cpu_per_exec_s) AS cpu_per_exec_s
           FROM awr_sql_cpu_time
          GROUP BY awr_sql_cpu_time.dbname, awr_sql_cpu_time.instance, awr_sql_cpu_time.begin_snap, awr_sql_cpu_time.sql_id
        ), uio AS (
         SELECT awr_sql_user_io_time.dbname,
            awr_sql_user_io_time.instance,
            awr_sql_user_io_time.begin_snap,
            awr_sql_user_io_time.sql_id,
            avg(awr_sql_user_io_time.uio_per_exec_s) AS user_io_wait_per_exec_s
           FROM awr_sql_user_io_time
          GROUP BY awr_sql_user_io_time.dbname, awr_sql_user_io_time.instance, awr_sql_user_io_time.begin_snap, awr_sql_user_io_time.sql_id
        ), gets AS (
         SELECT awr_sql_gets.dbname,
            awr_sql_gets.instance,
            awr_sql_gets.begin_snap,
            awr_sql_gets.sql_id,
            avg(awr_sql_gets.gets_per_exec) AS buffer_gets_per_exec
           FROM awr_sql_gets
          GROUP BY awr_sql_gets.dbname, awr_sql_gets.instance, awr_sql_gets.begin_snap, awr_sql_gets.sql_id
        ), reads AS (
         SELECT awr_sql_reads.dbname,
            awr_sql_reads.instance,
            awr_sql_reads.begin_snap,
            awr_sql_reads.sql_id,
            avg(awr_sql_reads.reads_per_exec) AS disk_reads_per_exec
           FROM awr_sql_reads
          GROUP BY awr_sql_reads.dbname, awr_sql_reads.instance, awr_sql_reads.begin_snap, awr_sql_reads.sql_id
        ), unopt AS (
         SELECT awr_sql_phy_reads_unopt.dbname,
            awr_sql_phy_reads_unopt.instance,
            awr_sql_phy_reads_unopt.begin_snap,
            awr_sql_phy_reads_unopt.sql_id,
            avg(awr_sql_phy_reads_unopt.unoptimized_reqs_per_exec) AS unoptimized_reqs_per_exec,
            avg(awr_sql_phy_reads_unopt.pctopt) AS pct_opt
           FROM awr_sql_phy_reads_unopt
          GROUP BY awr_sql_phy_reads_unopt.dbname, awr_sql_phy_reads_unopt.instance, awr_sql_phy_reads_unopt.begin_snap, awr_sql_phy_reads_unopt.sql_id
        ), execs AS (
         SELECT awr_sql_executions.dbname,
            awr_sql_executions.instance,
            awr_sql_executions.begin_snap,
            awr_sql_executions.sql_id,
            sum(awr_sql_executions.executions) AS total_executions
           FROM awr_sql_executions
          GROUP BY awr_sql_executions.dbname, awr_sql_executions.instance, awr_sql_executions.begin_snap, awr_sql_executions.sql_id
        ), parses AS (
         SELECT awr_sql_parsed_calls.dbname,
            awr_sql_parsed_calls.instance,
            awr_sql_parsed_calls.begin_snap,
            awr_sql_parsed_calls.sql_id,
            sum(awr_sql_parsed_calls.parse_calls) AS parse_calls,
            avg(awr_sql_parsed_calls.pct_total_parses) AS pct_total_parses
           FROM awr_sql_parsed_calls
          GROUP BY awr_sql_parsed_calls.dbname, awr_sql_parsed_calls.instance, awr_sql_parsed_calls.begin_snap, awr_sql_parsed_calls.sql_id
        ), clu AS (
         SELECT awr_sql_cluster_wait_time.dbname,
            awr_sql_cluster_wait_time.instance,
            awr_sql_cluster_wait_time.begin_snap,
            awr_sql_cluster_wait_time.sql_id,
            avg(awr_sql_cluster_wait_time.cluster_wait_time_s) AS cluster_wait_time_per_exec_s,
            avg(awr_sql_cluster_wait_time.pct_clu) AS pct_clu
           FROM awr_sql_cluster_wait_time
          GROUP BY awr_sql_cluster_wait_time.dbname, awr_sql_cluster_wait_time.instance, awr_sql_cluster_wait_time.begin_snap, awr_sql_cluster_wait_time.sql_id
        )
 SELECT e.dbname,
    e.instance,
    e.begin_snap,
    e.snap_time,
    e.sql_id,
    e.executions,
    e.elapsed_time_per_exec_s,
    c.cpu_per_exec_s,
    u.user_io_wait_per_exec_s,
    g.buffer_gets_per_exec,
    r.disk_reads_per_exec,
    un.unoptimized_reqs_per_exec,
    un.pct_opt,
    ex.total_executions,
    p.parse_calls,
    p.pct_total_parses,
    cl.cluster_wait_time_per_exec_s,
    cl.pct_clu,
    (COALESCE(e.elapsed_time_per_exec_s, 0::numeric) * 0.25 + COALESCE(c.cpu_per_exec_s, 0::numeric) * 0.25 + COALESCE(g.buffer_gets_per_exec, 0::numeric) / 10000.0 * 0.15 + COALESCE(r.disk_reads_per_exec, 0::numeric) / 1000.0 * 0.10 + COALESCE(u.user_io_wait_per_exec_s, 0::numeric) * 0.10 + COALESCE(un.unoptimized_reqs_per_exec, 0::numeric) / 1000.0 * 0.05 + COALESCE(p.parse_calls, 0::numeric) / 1000.0 * 0.05 + COALESCE(cl.cluster_wait_time_per_exec_s, 0::numeric) * 0.05)::numeric(18,4) AS severity_score,row_number() over () AS mv_id
   FROM elapsed e
     LEFT JOIN cpu c ON c.dbname = e.dbname AND c.instance = e.instance AND c.begin_snap = e.begin_snap AND c.sql_id = e.sql_id
     LEFT JOIN uio u ON u.dbname = e.dbname AND u.instance = e.instance AND u.begin_snap = e.begin_snap AND u.sql_id = e.sql_id
     LEFT JOIN gets g ON g.dbname = e.dbname AND g.instance = e.instance AND g.begin_snap = e.begin_snap AND g.sql_id = e.sql_id
     LEFT JOIN reads r ON r.dbname = e.dbname AND r.instance = e.instance AND r.begin_snap = e.begin_snap AND r.sql_id = e.sql_id
     LEFT JOIN unopt un ON un.dbname = e.dbname AND un.instance = e.instance AND un.begin_snap = e.begin_snap AND un.sql_id = e.sql_id
     LEFT JOIN execs ex ON ex.dbname = e.dbname AND ex.instance = e.instance AND ex.begin_snap = e.begin_snap AND ex.sql_id = e.sql_id
     LEFT JOIN parses p ON p.dbname = e.dbname AND p.instance = e.instance AND p.begin_snap = e.begin_snap AND p.sql_id = e.sql_id
     LEFT JOIN clu cl ON cl.dbname = e.dbname AND cl.instance = e.instance AND cl.begin_snap = e.begin_snap AND cl.sql_id = e.sql_id
WITH DATA;

CREATE UNIQUE INDEX idx_awr_sql_summary_mv_id ON awr_sql_summary_mv (mv_id) TABLESPACE awrparser_idx;

CREATE INDEX idx_elapsed ON awr_sql_elapsed_time (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
CREATE INDEX idx_cpu     ON awr_sql_cpu_time (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
CREATE INDEX idx_uio     ON awr_sql_user_io_time (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
CREATE INDEX idx_gets    ON awr_sql_gets (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
CREATE INDEX idx_reads   ON awr_sql_reads (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
CREATE INDEX idx_phy     ON awr_sql_phy_reads_unopt (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
CREATE INDEX idx_parse   ON awr_sql_parsed_calls (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
CREATE INDEX idx_cluster ON awr_sql_cluster_wait_time (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;

---------------------------------------------------------------
--Materialized view for AWR Segment Summary
---------------------------------------------------------------

--DROP MATERIALIZED VIEW IF EXISTS awr_segment_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_segment_summary_mv
TABLESPACE awrparser
AS
 WITH logical_reads AS (
         SELECT awr_seg_logical_reads.dbname,
            awr_seg_logical_reads.instance,
            awr_seg_logical_reads.begin_snap,
            min(awr_seg_logical_reads.snap_time) AS snap_time,
            awr_seg_logical_reads.tablespace_name,
            awr_seg_logical_reads.obj_type,
            awr_seg_logical_reads.owner,
            awr_seg_logical_reads.object_name,
            awr_seg_logical_reads.subobject_name,
            avg(awr_seg_logical_reads.logical_reads) AS logical_reads
           FROM awr_seg_logical_reads
          GROUP BY awr_seg_logical_reads.dbname, awr_seg_logical_reads.instance, awr_seg_logical_reads.begin_snap, awr_seg_logical_reads.tablespace_name, awr_seg_logical_reads.obj_type, awr_seg_logical_reads.owner, awr_seg_logical_reads.object_name, awr_seg_logical_reads.subobject_name
        ), physical_reads AS (
         SELECT awr_seg_phy_reads.dbname,
            awr_seg_phy_reads.instance,
            awr_seg_phy_reads.begin_snap,
            avg(awr_seg_phy_reads.physical_reads) AS physical_reads
           FROM awr_seg_phy_reads
          GROUP BY awr_seg_phy_reads.dbname, awr_seg_phy_reads.instance, awr_seg_phy_reads.begin_snap
        ), physical_writes AS (
         SELECT awr_seg_phy_writes.dbname,
            awr_seg_phy_writes.instance,
            awr_seg_phy_writes.begin_snap,
            avg(awr_seg_phy_writes.physical_writes) AS physical_writes
           FROM awr_seg_phy_writes
          GROUP BY awr_seg_phy_writes.dbname, awr_seg_phy_writes.instance, awr_seg_phy_writes.begin_snap
        ), direct_reads AS (
         SELECT awr_seg_direct_phy_reads.dbname,
            awr_seg_direct_phy_reads.instance,
            awr_seg_direct_phy_reads.begin_snap,
            avg(awr_seg_direct_phy_reads.direct_reads) AS direct_reads
           FROM awr_seg_direct_phy_reads
          GROUP BY awr_seg_direct_phy_reads.dbname, awr_seg_direct_phy_reads.instance, awr_seg_direct_phy_reads.begin_snap
        ), direct_writes AS (
         SELECT awr_seg_direct_phy_writes.dbname,
            awr_seg_direct_phy_writes.instance,
            awr_seg_direct_phy_writes.begin_snap,
            avg(awr_seg_direct_phy_writes.direct_writes) AS direct_writes
           FROM awr_seg_direct_phy_writes
          GROUP BY awr_seg_direct_phy_writes.dbname, awr_seg_direct_phy_writes.instance, awr_seg_direct_phy_writes.begin_snap
        ), read_req AS (
         SELECT awr_seg_phy_read_req.dbname,
            awr_seg_phy_read_req.instance,
            awr_seg_phy_read_req.begin_snap,
            avg(awr_seg_phy_read_req.phys_read_requests) AS phys_read_requests
           FROM awr_seg_phy_read_req
          GROUP BY awr_seg_phy_read_req.dbname, awr_seg_phy_read_req.instance, awr_seg_phy_read_req.begin_snap
        ), write_req AS (
         SELECT awr_seg_phy_write_req.dbname,
            awr_seg_phy_write_req.instance,
            awr_seg_phy_write_req.begin_snap,
            avg(awr_seg_phy_write_req.phys_write_requests) AS phys_write_requests
           FROM awr_seg_phy_write_req
          GROUP BY awr_seg_phy_write_req.dbname, awr_seg_phy_write_req.instance, awr_seg_phy_write_req.begin_snap
        ), cr_blocks AS (
         SELECT awr_seg_cr_blk_rec.dbname,
            awr_seg_cr_blk_rec.instance,
            awr_seg_cr_blk_rec.begin_snap,
            avg(awr_seg_cr_blk_rec.cr_blocks_received) AS cr_blocks_received
           FROM awr_seg_cr_blk_rec
          GROUP BY awr_seg_cr_blk_rec.dbname, awr_seg_cr_blk_rec.instance, awr_seg_cr_blk_rec.begin_snap
        ), cur_blocks AS (
         SELECT awr_seg_cur_blk_rec.dbname,
            awr_seg_cur_blk_rec.instance,
            awr_seg_cur_blk_rec.begin_snap,
            avg(awr_seg_cur_blk_rec.current_blocks_received) AS current_blocks_received
           FROM awr_seg_cur_blk_rec
          GROUP BY awr_seg_cur_blk_rec.dbname, awr_seg_cur_blk_rec.instance, awr_seg_cur_blk_rec.begin_snap
        ), unopt_reads AS (
         SELECT awr_seg_unopt_reads.dbname,
            awr_seg_unopt_reads.instance,
            awr_seg_unopt_reads.begin_snap,
            avg(awr_seg_unopt_reads.unoptimized_reads) AS unoptimized_reads
           FROM awr_seg_unopt_reads
          GROUP BY awr_seg_unopt_reads.dbname, awr_seg_unopt_reads.instance, awr_seg_unopt_reads.begin_snap
        ), opt_reads AS (
         SELECT awr_seg_opt_reads.dbname,
            awr_seg_opt_reads.instance,
            awr_seg_opt_reads.begin_snap,
            avg(awr_seg_opt_reads.optimized_reads) AS optimized_reads
           FROM awr_seg_opt_reads
          GROUP BY awr_seg_opt_reads.dbname, awr_seg_opt_reads.instance, awr_seg_opt_reads.begin_snap
        ), table_scans AS (
         SELECT awr_seg_table_scan.dbname,
            awr_seg_table_scan.instance,
            awr_seg_table_scan.begin_snap,
            avg(awr_seg_table_scan.table_scans) AS table_scans
           FROM awr_seg_table_scan
          GROUP BY awr_seg_table_scan.dbname, awr_seg_table_scan.instance, awr_seg_table_scan.begin_snap
        ), db_block_changes AS (
         SELECT awr_seg_db_blk_chg.dbname,
            awr_seg_db_blk_chg.instance,
            awr_seg_db_blk_chg.begin_snap,
            avg(awr_seg_db_blk_chg.db_block_changes) AS db_block_changes
           FROM awr_seg_db_blk_chg
          GROUP BY awr_seg_db_blk_chg.dbname, awr_seg_db_blk_chg.instance, awr_seg_db_blk_chg.begin_snap
        ), row_lock AS (
         SELECT awr_seg_row_lck_waits.dbname,
            awr_seg_row_lck_waits.instance,
            awr_seg_row_lck_waits.begin_snap,
            avg(awr_seg_row_lck_waits.row_lock_waits) AS row_lock_waits
           FROM awr_seg_row_lck_waits
          GROUP BY awr_seg_row_lck_waits.dbname, awr_seg_row_lck_waits.instance, awr_seg_row_lck_waits.begin_snap
        ), itl AS (
         SELECT awr_seg_itl_waits.dbname,
            awr_seg_itl_waits.instance,
            awr_seg_itl_waits.begin_snap,
            avg(awr_seg_itl_waits.itl_waits) AS itl_waits
           FROM awr_seg_itl_waits
          GROUP BY awr_seg_itl_waits.dbname, awr_seg_itl_waits.instance, awr_seg_itl_waits.begin_snap
        ), buff_busy AS (
         SELECT awr_seg_buff_busy_waits.dbname,
            awr_seg_buff_busy_waits.instance,
            awr_seg_buff_busy_waits.begin_snap,
            avg(awr_seg_buff_busy_waits.buffer_busy_waits) AS buffer_busy_waits
           FROM awr_seg_buff_busy_waits
          GROUP BY awr_seg_buff_busy_waits.dbname, awr_seg_buff_busy_waits.instance, awr_seg_buff_busy_waits.begin_snap
        ), gc_busy AS (
         SELECT awr_seg_gbl_cache_buff_busy.dbname,
            awr_seg_gbl_cache_buff_busy.instance,
            awr_seg_gbl_cache_buff_busy.begin_snap,
            avg(awr_seg_gbl_cache_buff_busy.gc_buffer_busy) AS gc_buffer_busy
           FROM awr_seg_gbl_cache_buff_busy
          GROUP BY awr_seg_gbl_cache_buff_busy.dbname, awr_seg_gbl_cache_buff_busy.instance, awr_seg_gbl_cache_buff_busy.begin_snap
        )
 SELECT l.dbname,
    l.instance,
    l.begin_snap,
    l.snap_time,
    l.tablespace_name,
    l.owner,
    l.obj_type,
    l.object_name,
    l.subobject_name,
    l.logical_reads,
    pr.physical_reads,
    pw.physical_writes,
    dr.direct_reads,
    dw.direct_writes,
    rr.phys_read_requests,
    wr.phys_write_requests,
    cb.cr_blocks_received,
    cur.current_blocks_received,
    un.unoptimized_reads,
    opt.optimized_reads,
    ts.table_scans,
    dbc.db_block_changes,
    rl.row_lock_waits,
    it.itl_waits,
    bb.buffer_busy_waits,
    gc.gc_buffer_busy,
    (COALESCE(pr.physical_reads, 0::numeric) * 0.15 + (COALESCE(dr.direct_reads, 0::numeric) + COALESCE(dw.direct_writes, 0::numeric)) * 0.10 + COALESCE(l.logical_reads, 0::numeric) * 0.10 + (COALESCE(cb.cr_blocks_received, 0::numeric) + COALESCE(cur.current_blocks_received, 0::numeric)) * 0.10 + COALESCE(ts.table_scans, 0::numeric) * 0.05 + COALESCE(un.unoptimized_reads, 0::numeric) * 0.05 + (COALESCE(rr.phys_read_requests, 0::numeric) + COALESCE(wr.phys_write_requests, 0::numeric)) * 0.03 + COALESCE(opt.optimized_reads, 0::numeric) * 0.02 + COALESCE(dbc.db_block_changes, 0::numeric) * 0.10 + COALESCE(rl.row_lock_waits, 0::numeric) * 0.10 + COALESCE(it.itl_waits, 0::numeric) * 0.05 + COALESCE(bb.buffer_busy_waits, 0::numeric) * 0.10 + COALESCE(gc.gc_buffer_busy, 0::numeric) * 0.05)::numeric(18,4) AS severity_score, 
row_number() over () AS mv_id 
   FROM logical_reads l
     LEFT JOIN physical_reads pr ON pr.dbname = l.dbname AND pr.instance = l.instance AND pr.begin_snap = l.begin_snap
     LEFT JOIN physical_writes pw ON pw.dbname = l.dbname AND pw.instance = l.instance AND pw.begin_snap = l.begin_snap
     LEFT JOIN direct_reads dr ON dr.dbname = l.dbname AND dr.instance = l.instance AND dr.begin_snap = l.begin_snap
     LEFT JOIN direct_writes dw ON dw.dbname = l.dbname AND dw.instance = l.instance AND dw.begin_snap = l.begin_snap
     LEFT JOIN read_req rr ON rr.dbname = l.dbname AND rr.instance = l.instance AND rr.begin_snap = l.begin_snap
     LEFT JOIN write_req wr ON wr.dbname = l.dbname AND wr.instance = l.instance AND wr.begin_snap = l.begin_snap
     LEFT JOIN cr_blocks cb ON cb.dbname = l.dbname AND cb.instance = l.instance AND cb.begin_snap = l.begin_snap
     LEFT JOIN cur_blocks cur ON cur.dbname = l.dbname AND cur.instance = l.instance AND cur.begin_snap = l.begin_snap
     LEFT JOIN unopt_reads un ON un.dbname = l.dbname AND un.instance = l.instance AND un.begin_snap = l.begin_snap
     LEFT JOIN opt_reads opt ON opt.dbname = l.dbname AND opt.instance = l.instance AND opt.begin_snap = l.begin_snap
     LEFT JOIN table_scans ts ON ts.dbname = l.dbname AND ts.instance = l.instance AND ts.begin_snap = l.begin_snap
     LEFT JOIN db_block_changes dbc ON dbc.dbname = l.dbname AND dbc.instance = l.instance AND dbc.begin_snap = l.begin_snap
     LEFT JOIN row_lock rl ON rl.dbname = l.dbname AND rl.instance = l.instance AND rl.begin_snap = l.begin_snap
     LEFT JOIN itl it ON it.dbname = l.dbname AND it.instance = l.instance AND it.begin_snap = l.begin_snap
     LEFT JOIN buff_busy bb ON bb.dbname = l.dbname AND bb.instance = l.instance AND bb.begin_snap = l.begin_snap
     LEFT JOIN gc_busy gc ON gc.dbname = l.dbname AND gc.instance = l.instance AND gc.begin_snap = l.begin_snap
WITH DATA;

-- Indexes for performance
--CREATE UNIQUE INDEX idx_awr_seg_summary_mv_id ON awr_segment_summary_mv (mv_id) TABLESPACE awrparser_idx;

--CREATE INDEX idx_seg_summary_dbinstsnap ON awr_seg_summary_mv (dbname, instance, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_seg_summary_score ON awr_seg_summary_mv (severity_score DESC) tablespace awrparser_idx;

------------------------------------------------------------
--Foreground Wait Event Summary
------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_fg_wait_summary_mv
TABLESPACE awrparser
AS
 WITH fg_waits AS (
         SELECT awr_foreground_wait_events.dbname,
            awr_foreground_wait_events.instance,
            awr_foreground_wait_events.begin_snap,
            min(awr_foreground_wait_events.snap_time) AS snap_time,
            awr_foreground_wait_events.event AS wait_event,
            sum(awr_foreground_wait_events.waits) AS total_waits,
            sum(awr_foreground_wait_events.total_wait_time_s) AS total_wait_time_s,
            round(avg(awr_foreground_wait_events.avg_wait_ms), 3) AS avg_wait_ms,
            max(awr_foreground_wait_events.avg_wait_ms) AS max_wait_ms,
            min(awr_foreground_wait_events.avg_wait_ms) AS min_wait_ms,
            round(avg(awr_foreground_wait_events.pct_db_time), 2) AS avg_pct_db_time
           FROM awr_foreground_wait_events
          WHERE awr_foreground_wait_events.pct_db_time > 5::numeric
          GROUP BY awr_foreground_wait_events.dbname, awr_foreground_wait_events.instance, awr_foreground_wait_events.begin_snap, awr_foreground_wait_events.event
        )
 SELECT f.dbname,
    f.instance,
    f.begin_snap,
    f.snap_time,
    f.wait_event,
    f.total_waits,
    f.total_wait_time_s,
    f.avg_wait_ms,
    f.max_wait_ms,
    f.min_wait_ms,
    f.avg_pct_db_time,
    round(COALESCE(f.avg_pct_db_time, 0::numeric) * 0.5 + COALESCE(f.total_wait_time_s, 0::numeric) * 0.3 + COALESCE(f.avg_wait_ms, 0::numeric) * 0.2, 2) AS severity_score
   FROM fg_waits f
  WHERE f.total_wait_time_s > 0::numeric
  ORDER BY (round(COALESCE(f.avg_pct_db_time, 0::numeric) * 0.5 + COALESCE(f.total_wait_time_s, 0::numeric) * 0.3 + COALESCE(f.avg_wait_ms, 0::numeric) * 0.2, 2)) DESC
WITH DATA;

CREATE INDEX idx_fg_wait_summary_dbinstsnap
    ON awr_fg_wait_summary_mv USING btree
    (dbname COLLATE pg_catalog."default", instance COLLATE pg_catalog."default", begin_snap)
    TABLESPACE awrparser_idx;

---------------------------------------------------------------
--Foreground wait event summary with wait class
-----------------------------------------------------------------

--DROP MATERIALIZED VIEW IF EXISTS awr_fg_wait_event_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_fg_wait_event_summary_mv
TABLESPACE pg_default
AS
 SELECT e.dbname,
    e.instance,
    e.begin_snap,
    min(e.snap_time) AS snap_time,
    COALESCE(m.wait_class, 'Other'::text) AS wait_class,
    e.event,
    sum(e.waits) AS total_waits,
    sum(e.total_wait_time_s) AS total_wait_time_s,
    round(avg(e.avg_wait_ms), 2) AS avg_wait_ms,
    round(avg(e.waits_per_txn), 2) AS waits_per_txn,
    round(avg(e.pct_db_time), 2) AS pct_db_time,
        CASE
            WHEN avg(e.pct_db_time) >= 20::numeric THEN 5
            WHEN avg(e.pct_db_time) >= 10::numeric THEN 4
            WHEN avg(e.pct_db_time) >= 5::numeric THEN 3
            WHEN avg(e.pct_db_time) >= 2::numeric THEN 2
            ELSE 1
        END AS severity_score
   FROM awr_foreground_wait_events e
     LEFT JOIN awr_wait_event_master m ON btrim(lower(e.event)) = btrim(lower(m.event))
  WHERE e.pct_db_time > 5::numeric
  GROUP BY e.dbname, e.instance, e.begin_snap, m.wait_class, e.event
WITH DATA;


------------------------------------------------------------
--Background Wait Event Summary
------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_bg_wait_summary_mv
TABLESPACE awrparser
AS
 WITH bg_waits AS (
         SELECT awr_background_wait_events.dbname,
            awr_background_wait_events.instance,
            awr_background_wait_events.begin_snap,
            min(awr_background_wait_events.snap_time) AS snap_time,
            awr_background_wait_events.event AS wait_event,
            sum(awr_background_wait_events.waits) AS total_waits,
            sum(awr_background_wait_events.total_wait_time_s) AS total_wait_time_s,
            round(avg(awr_background_wait_events.avg_wait_ms), 3) AS avg_wait_ms,
            max(awr_background_wait_events.avg_wait_ms) AS max_wait_ms,
            min(awr_background_wait_events.avg_wait_ms) AS min_wait_ms,
            round(avg(awr_background_wait_events.pct_bg_time), 2) AS avg_pct_bg_time
           FROM awr_background_wait_events
          WHERE awr_background_wait_events.pct_bg_time > 5::numeric
          GROUP BY awr_background_wait_events.dbname, awr_background_wait_events.instance, awr_background_wait_events.begin_snap, awr_background_wait_events.event
        )
 SELECT b.dbname,
    b.instance,
    b.begin_snap,
    b.snap_time,
    b.wait_event,
    b.total_waits,
    b.total_wait_time_s,
    b.avg_wait_ms,
    b.max_wait_ms,
    b.min_wait_ms,
    b.avg_pct_bg_time,
    round(COALESCE(b.avg_pct_bg_time, 0::numeric) * 0.5 + COALESCE(b.total_wait_time_s, 0::numeric) * 0.3 + COALESCE(b.avg_wait_ms, 0::numeric) * 0.2, 2) AS severity_score
   FROM bg_waits b
  WHERE b.total_wait_time_s > 0::numeric
  ORDER BY (round(COALESCE(b.avg_pct_bg_time, 0::numeric) * 0.5 + COALESCE(b.total_wait_time_s, 0::numeric) * 0.3 + COALESCE(b.avg_wait_ms, 0::numeric) * 0.2, 2)) DESC
WITH DATA;


CREATE INDEX idx_bg_wait_summary_dbinstsnap
    ON awr_bg_wait_summary_mv USING btree
    (dbname COLLATE pg_catalog."default", instance COLLATE pg_catalog."default", begin_snap)
    TABLESPACE awrparser_idx;

----------------------------------------
--Background wait event summary with wait class
--------------------------------------------------
--DROP MATERIALIZED VIEW IF EXISTS awr_bg_wait_event_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_bg_wait_event_summary_mv
TABLESPACE pg_default
AS
 SELECT e.dbname,
    e.instance,
    e.begin_snap,
    min(e.snap_time) AS snap_time,
    COALESCE(m.wait_class, 'Other'::text) AS wait_class,
    e.event,
    sum(e.waits) AS total_waits,
    sum(e.total_wait_time_s) AS total_wait_time_s,
    round(avg(e.avg_wait_ms), 2) AS avg_wait_ms,
    round(avg(e.waits_per_txn), 2) AS waits_per_txn,
    round(avg(e.pct_db_time), 2) AS pct_db_time,
        CASE
            WHEN avg(e.pct_db_time) >= 20::numeric THEN 5
            WHEN avg(e.pct_db_time) >= 10::numeric THEN 4
            WHEN avg(e.pct_db_time) >= 5::numeric THEN 3
            WHEN avg(e.pct_db_time) >= 2::numeric THEN 2
            ELSE 1
        END AS severity_score
   FROM awr_foreground_wait_events e
     LEFT JOIN awr_wait_event_master m ON btrim(lower(e.event)) = btrim(lower(m.event))
  WHERE e.pct_db_time > 5::numeric
  GROUP BY e.dbname, e.instance, e.begin_snap, m.wait_class, e.event
WITH DATA;
--------------------------------------------

-- ==========================================
-- Materialized View: AWR Wait Events Summary
-- Combines Foreground and Background Wait Events
-- ==========================================


--DROP MATERIALIZED VIEW IF EXISTS awr_wait_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_wait_summary_mv
TABLESPACE awrparser
AS
 WITH fg AS (
         SELECT e.dbname,
            e.instance,
            e.begin_snap,
            min(e.snap_time) AS snap_time,
            COALESCE(m.wait_class, 'Other'::text) AS wait_class,
            e.event,
            sum(e.waits) AS waits,
            sum(e.total_wait_time_s) AS total_wait_time_s,
            round(avg(e.avg_wait_ms), 2) AS avg_wait_ms,
            round(avg(e.waits_per_txn), 2) AS waits_per_txn,
            round(avg(e.pct_db_time), 2) AS pct_time,
            'Foreground'::text AS wait_scope
           FROM awr_foreground_wait_events e
             LEFT JOIN awr_wait_event_master m ON btrim(lower(e.event)) = btrim(lower(m.event))
          WHERE e.pct_db_time > 5::numeric
          GROUP BY e.dbname, e.instance, e.begin_snap, m.wait_class, e.event
        ), bg AS (
         SELECT e.dbname,
            e.instance,
            e.begin_snap,
            min(e.snap_time) AS snap_time,
            COALESCE(m.wait_class, 'Other'::text) AS wait_class,
            e.event,
            sum(e.waits) AS waits,
            sum(e.total_wait_time_s) AS total_wait_time_s,
            round(avg(e.avg_wait_ms), 2) AS avg_wait_ms,
            round(avg(e.waits_per_txn), 2) AS waits_per_txn,
            round(avg(e.pct_bg_time), 2) AS pct_time,
            'Background'::text AS wait_scope
           FROM awr_background_wait_events e
             LEFT JOIN awr_wait_event_master m ON btrim(lower(e.event)) = btrim(lower(m.event))
          WHERE e.pct_bg_time > 5::numeric
          GROUP BY e.dbname, e.instance, e.begin_snap, m.wait_class, e.event
        )
 SELECT combined.dbname,
    combined.instance,
    combined.begin_snap,
    combined.snap_time,
    combined.wait_scope,
    combined.wait_class,
    combined.event,
    combined.waits,
    combined.total_wait_time_s,
    combined.avg_wait_ms,
    combined.waits_per_txn,
    combined.pct_time,
    round(COALESCE(combined.pct_time, 0::numeric) * 0.7 + COALESCE(combined.total_wait_time_s, 0::numeric) / 100.0 * 0.2 + COALESCE(combined.avg_wait_ms, 0::numeric) / 10.0 * 0.1, 2) AS severity_score
   FROM ( SELECT fg.dbname,
            fg.instance,
            fg.begin_snap,
            fg.snap_time,
            fg.wait_class,
            fg.event,
            fg.waits,
            fg.total_wait_time_s,
            fg.avg_wait_ms,
            fg.waits_per_txn,
            fg.pct_time,
            fg.wait_scope
           FROM fg
        UNION ALL
         SELECT bg.dbname,
            bg.instance,
            bg.begin_snap,
            bg.snap_time,
            bg.wait_class,
            bg.event,
            bg.waits,
            bg.total_wait_time_s,
            bg.avg_wait_ms,
            bg.waits_per_txn,
            bg.pct_time,
            bg.wait_scope
           FROM bg) combined
  ORDER BY (round(COALESCE(combined.pct_time, 0::numeric) * 0.7 + COALESCE(combined.total_wait_time_s, 0::numeric) / 100.0 * 0.2 + COALESCE(combined.avg_wait_ms, 0::numeric) / 10.0 * 0.1, 2)) DESC
WITH DATA;

CREATE INDEX idx_wait_summary_dbinstsnap
    ON awr_wait_summary_mv USING btree
    (dbname COLLATE pg_catalog."default", instance COLLATE pg_catalog."default", begin_snap, wait_scope COLLATE pg_catalog."default", wait_class COLLATE pg_catalog."default")
    TABLESPACE awrparser_idx;


------------------------------------------------------------
--Recommendation rule table
------------------------------------------------------------
CREATE TABLE IF NOT EXISTS awr_recommendation_rules (
  category   TEXT NOT NULL,     -- 'wait' | 'sql' | 'segment'
  rule_key   TEXT NOT NULL,     -- exact value (e.g. 'db file sequential read') or '*' (wildcard)
  rule_text  TEXT NOT NULL,
  PRIMARY KEY (category, rule_key)
) tablespace awrparser;

------------------------------------------------------------------
--Recommendation Rules
------------------------------------------------------------------
BEGIN;

-- =========================
-- WAIT EVENTS (exact names)
-- =========================
INSERT INTO awr_recommendation_rules (category, rule_key, rule_text) VALUES
('wait','db file sequential read',
 'High single-block I/O latency. Check index access patterns, hot blocks, and storage latency. Consider adding/rebuilding indexes, fixing random I/O hotspots, or reducing row-by-row lookups.'),

('wait','db file scattered read',
 'Multi-block read latency (older events; modern systems use direct path read). Investigate full scans, proper parallelism, and verify multiblock read count/storage throughput.'),

('wait','direct path read',
 'Large scans via direct path. Validate partition pruning, correct degree of parallelism, and storage bandwidth. Confirm scans are expected and not due to missing indexes.'),

('wait','direct path write',
 'Direct path writes during loads/ETL/temp spilling. Validate temp/IO layout, parallel degree, and undo/redo overhead. Consider batching/APPEND operations and checkpoint pressure.'),

('wait','read by other session',
 'Buffer contention from hot blocks. Look for hot index leaf blocks / clustering issues. Consider reverse key indexes, better clustering, or spreading hotspot workload.'),

('wait','buffer busy waits',
 'Buffer contention on data blocks. Check hot blocks (segments/blocks), hash partitioning, or spreading workload. Consider increasing cache where appropriate.'),

('wait','free buffer waits',
 'Buffer cache cannot free buffers fast enough. Investigate dirty buffer write rate, DBWR throughput, redo/log write speed, and check for full-table scans polluting cache.'),

('wait','write complete waits',
 'Process waiting for DBWR to write dirty buffers. Validate DBWR throughput, I/O latency, and redo/log configuration. Check for heavy full scans spilling dirty buffers.'),

('wait','log file sync',
 'Commit latency high. Investigate redo log device latency/size, commit batching, app commit frequency, and synchronous standby (Data Guard) settings.'),

('wait','log file parallel write',
 'Redo writer latency. Review I/O bandwidth, RAID cache policy, write cache, and ASM/volume layout. Ensure redo logs on fast, dedicated storage.'),

('wait','log buffer space',
 'Log buffer is full. Increase LOG_BUFFER or investigate frequent commits and slow log writes (I/O).'),

('wait','log file switch (archiving needed)',
 'Archiver cannot keep up. Increase redo log size/number, speed up archiving I/O, and verify archival destination health.'),

('wait','log file switch (checkpoint incomplete)',
 'Checkpoint cannot complete before log reuse. Increase redo log size/number and tune checkpointing (FAST_START_MTTR_TARGET).'),

('wait','enq: TX - row lock contention',
 'Row lock contention. Investigate conflicting DML, long transactions, missing indexes, and hot rows. Consider shorter commits, proper indexing, and app-side concurrency controls.'),

('wait','enq: TM - contention',
 'Metadata (TM) enqueue contention, often due to unindexed foreign keys. Ensure FK columns are indexed; review concurrent DDL/partition maintenance.'),

('wait','latch free',
 'Latch contention (legacy). Identify the latch (AWR/ASH). Often caused by shared pool pressure, parse storms, or hot block/latch usage; tune memory/parse or reduce concurrency.'),

('wait','cursor: pin S wait on X',
 'High hard/soft parse or shared pool pressure. Reduce parse storms (bind variables), increase shared pool if appropriate, fix SQL churn.'),

('wait','library cache: mutex X',
 'Library cache mutex contention. Reduce parse/invalidations, avoid frequent object recompiles, ensure bind peeking/hard parse churn is minimized.'),

('wait','library cache lock',
 'Library cache lock contention. Look for invalidations, DDL on hot objects, or frequent cursor recompilations. Limit concurrent DDL on shared objects.'),

('wait','library cache load lock',
 'Session waiting for another to load an object. Reduce parse storms and invalidations; consider pinning critical packages; review shared pool sizing.'),

('wait','gc buffer busy acquire',
 'RAC interconnect block contention. Investigate hot blocks, object partitioning, and service/instance affinity. Ensure low-latency interconnect and correct block placement.'),

('wait','gc cr request',
 'RAC consistent-read block shipping latency. Validate interconnect bandwidth/latency, reduce global hot blocks via partitioning or service affinity.'),

('wait','gc current request',
 'RAC current block shipping latency. Look for hot DML blocks. Consider partitioning, instance affinity, or data placement to reduce global contention.'),

('wait','DFS lock handle',
 'Global lock manager wait (RAC). Investigate cross-instance contention on objects; review object partitioning/service affinity.'),

('wait','SQL*Net more data from client',
 'Network round-trips; often benign (fetching results). If excessive, reduce result set size, paginate, or increase arraysize/fetch size.'),

('wait','SQL*Net more data to client',
 'Sending lots of data to client. Optimize payload size/columns; compress or paginate if needed.'),

('wait','SQL*Net message from client',
 'Idle wait for client — usually benign. High time may indicate slow app tier or think time.'),

('wait','SQL*Net message to client',
 'Idle wait; generally negligible unless showing unusual spikes indicating network stack issues.'),

('wait','db file parallel write',
 'Background write latency. Check I/O subsystem, ASM/volume layout, and write queue saturation.'),

('wait','control file sequential read',
 'Control file read latency. Usually low. If high, check control file I/O location and concurrent operations (RMAN).'),

('wait','control file parallel write',
 'Control file writes latency. Ensure control files are mirrored across fast devices; review RMAN/archival pressure.'),

('wait','ASM file metadata operation',
 'ASM metadata operation latency. Validate ASM diskgroup health, rebalance events, and I/O load.'),

('wait','resmgr: cpu quantum',
 'Resource Manager throttling. Check consumer group limits, runaway sessions, and plan directives.'),

('wait','temporary file read',
 'Temp read I/O — sorts/hash joins spilling. Increase PGA/WORKAREA sizes appropriately, add tempfiles or faster storage, reduce large sorts/skews.'),

('wait','temporary file write',
 'Temp write I/O — sorts/hash joins spilling. See “temporary file read” guidance; reduce spills via better joins, stats, and WORKAREA settings.'),

('wait','PX Deq Credit: send blkd',
 'Parallel execution flow control — sending server is blocked. Review DOP, PX message pool, and skew; balance producers/consumers.'),

('wait','PX Deq Credit: execute reply',
 'Parallel execution messaging delay. Investigate PX skew, oversized granules, or insufficient PX servers.'),

('wait','PX qref latch',
 'PX messaging/latch contention. Review parallel degree, skew, and interconnect pressure.'),

('wait','reliable message',
 'Background message wait; often benign. Investigate only if dominating snapshot alongside specific background processes.'),

('wait','rdbms ipc reply',
 'Background process coordination. If high, correlate to specific background (SMON/DBWR/ARCn) and their bottlenecks.'),

('wait','latch: shared pool',
 'Shared pool pressure. Reduce parse, enable binds, size shared pool appropriately, avoid frequent invalidations.'),

('wait','latch: cache buffers chains',
 'Hot CBC latch — hot block contention. Consider reverse-key indexes, hash partitioning, or spreading hotspots; review access patterns.'),

('wait','latch: redo allocation',
 'Redo allocation latch contention. Increase log buffer, reduce commit bursts, and ensure fast redo I/O.'),

('wait','latch: redo copy',
 'Redo copy latch contention. Check log buffer and commit patterns; mitigate bursts and ensure redo devices are fast.'),

('wait','latch: row cache objects',
 'Row cache (data dictionary) contention. Reduce frequent DDL, tune shared pool, and avoid metadata storms.'),

-- Fallback rule for any wait not explicitly listed
('wait','*',
 'General wait hotspot. Correlate top SQLs and segments in the chosen snaps. Verify storage/interconnect health, workload pattern (OLTP vs batch), and consider indexing/partitioning to reduce the hotspot.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

-- =========================
-- SQL (generic keys)
-- =========================
INSERT INTO awr_recommendation_rules (category, rule_key, rule_text) VALUES
-- You can add exact sql_id rows later: ('sql','7pr32saksf831','<specific recommendation>')
('sql','high_cpu',
 'High CPU SQL. Validate cardinality estimates, join methods, and missing indexes. Consider SQL Profiles/Baselines, gather fresh stats with histograms, and parameterize literals.'),
('sql','sql_long_elapsed',
 'Long elapsed time. Check for bad joins, data skew, or excessive remote/RAC block shipping. Validate plan stability and parallel settings.'),
('sql','temp_spill',
 'Sort/hash joins spilling to temp. Increase workarea (PGA/automatic), improve join order, and reduce row width; add/resize tempfiles on fast storage.'),
('sql','plan_change',
 'Frequent plan flips. Pin with SQL Plan Baseline/Profile, fix stats quality (histograms), and reduce bind peek instability.'),
('sql','hard_parse',
 'High hard-parse. Use bind variables, reduce SQL churn, and avoid concatenated literals. Size shared pool appropriately.'),
('sql','full_scan',
 'Full scans detected. Validate selectivity; add indexes or partition pruning. For analytics, ensure scans are intentional and use proper parallelism.'),
('sql','*',
 'Problematic SQL. Check execution plan, stats, join order, and access paths. Add/repair indexes, fix bad cardinality, and stabilize with Profiles/Baselines where appropriate.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

-- =========================
-- SEGMENTS (generic keys)
-- =========================
INSERT INTO awr_recommendation_rules (category, rule_key, rule_text) VALUES
-- You can add exact object_name rows later: ('segment','HR.EMPLOYEES','<specific>')
('segment','undo_high_cpu',
 'Undo/ITL pressure. Validate UNDO sizing/retention, reduce long transactions, and review concurrent hot-row updates.'),
('segment','hot_index_leaf',
 'Hot index leaf blocks. Consider reverse-key index, better clustering, or hash partitioning the index.'),
('segment','*',
 'Hot segment detected. Validate access patterns, clustering/partitioning strategy, and index/selectivity. Consider targeted indexing or data reorg to reduce hotspots.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

COMMIT;


-------------------------------------------------------------------------------------------------
--Recommendation rules updated from recommendation_rulesv1.1.json
---------------------------------------------------------------------------------------------------
INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','db file sequential read','High single-block IO latency. Check index access patterns, storage latency, and verify that hot blocks fit in buffer cache. Consider adding/adjusting indexes or reducing row-by-row lookups.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','direct path read','Direct path reads indicate large scans. Consider partition pruning, proper parallelism, and verifying that large full scans are expected for the workload.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','enq: TX - row lock contention','Row lock contention detected. Investigate conflicting DML transactions, commit frequency, and application logic. Consider shorter transactions and proper indexing to reduce hot spots.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','buffer busy waits','Buffer contention on hot blocks. Consider spreading workload (hash partitioning), increasing INITRANS on hot segments, or addressing hot block access patterns.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','log file sync','Commit latency is high. Check redo log device latency and size, verify low commit batching, and consider tuning filesystem or storage for sequential writes.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','*','General wait event. Review top SQLs and segments contributing around the chosen snaps. Correlate with IO, concurrency, or commit hotspots.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('sql','*','Problematic SQL. Validate plan stability, predicates, and join methods. Confirm proper indexing, up-to-date stats, and consider SQL profile/outline only after root cause analysis.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('segment','*','Hot segment. Validate access patterns, clustering, and partitioning. Consider targeted indexing, segment reorg, or ILM to reduce skew and hotspots.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;


-- =============================================================================
-- SECTION 3: SAR TABLES
-- =============================================================================

-- ── Original SAR tables (v1) ─────────────────────────────────────────────────

CREATE TABLE sar_cpu_stats (
    id SERIAL PRIMARY KEY, hostname TEXT NOT NULL, snap_time TIMESTAMP NOT NULL,
    cpu TEXT NOT NULL, usr_pct NUMERIC, nice_pct NUMERIC, system_pct NUMERIC,
    iowait_pct NUMERIC, steal_pct NUMERIC, idle_pct NUMERIC,
    row_hash CHAR(32) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_cpu UNIQUE (hostname, snap_time, cpu, row_hash)
) TABLESPACE awrparser;
CREATE INDEX idx_sar_cpu_host_time ON sar_cpu_stats (hostname, snap_time);
CREATE INDEX idx_sar_cpu_iowait    ON sar_cpu_stats (hostname, iowait_pct DESC);

CREATE TABLE sar_memory_stats (
    id SERIAL PRIMARY KEY, hostname TEXT NOT NULL, snap_time TIMESTAMP NOT NULL,
    mem_free_mb NUMERIC, mem_used_mb NUMERIC, mem_used_pct NUMERIC,
    buffers_mb NUMERIC, cached_mb NUMERIC, commit_mb NUMERIC, commit_pct NUMERIC,
    row_hash CHAR(32) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_mem UNIQUE (hostname, snap_time, row_hash)
) TABLESPACE awrparser;
CREATE INDEX idx_sar_mem_host_time ON sar_memory_stats (hostname, snap_time);

CREATE TABLE sar_swap_stats (
    id SERIAL PRIMARY KEY, hostname TEXT NOT NULL, snap_time TIMESTAMP NOT NULL,
    swap_free_mb NUMERIC, swap_used_mb NUMERIC, swap_used_pct NUMERIC,
    row_hash CHAR(32) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_swap UNIQUE (hostname, snap_time, row_hash)
) TABLESPACE awrparser;
CREATE INDEX idx_sar_swap_host_time ON sar_swap_stats (hostname, snap_time);

CREATE TABLE sar_disk_stats (
    id SERIAL PRIMARY KEY, hostname TEXT NOT NULL, snap_time TIMESTAMP NOT NULL,
    device TEXT NOT NULL, tps NUMERIC, read_mb_per_sec NUMERIC,
    write_mb_per_sec NUMERIC, await_ms NUMERIC, svctm_ms NUMERIC, util_pct NUMERIC,
    row_hash CHAR(32) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_disk UNIQUE (hostname, snap_time, device, row_hash)
) TABLESPACE awrparser;
CREATE INDEX idx_sar_disk_host_time ON sar_disk_stats (hostname, snap_time);
CREATE INDEX idx_sar_disk_util      ON sar_disk_stats (hostname, util_pct DESC);

-- ── New SAR tables v2 (network, paging, context switch, load average) ─────────

-- ============================================================
-- schema/sar_new_tables.sql
-- New SAR tables for: Network, Paging, Context Switch, Load Average
-- Run ONCE against the awrparser tablespace
-- ============================================================

-- ── Network Interface Stats (sar -n DEV) ──────────────────────────────

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

CREATE INDEX idx_sar_network_host_time ON sar_network_stats (hostname, snap_time);
CREATE INDEX idx_sar_network_iface     ON sar_network_stats (iface);

-- ── Paging Stats (sar -B) ─────────────────────────────────────────────

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

CREATE INDEX idx_sar_paging_host_time  ON sar_paging_stats (hostname, snap_time);
CREATE INDEX idx_sar_paging_majflt     ON sar_paging_stats (hostname, majflt_per_sec DESC);

-- ── Context Switch Stats (sar -w) ─────────────────────────────────────

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

CREATE INDEX idx_sar_ctxswitch_host_time ON sar_ctxswitch_stats (hostname, snap_time);
CREATE INDEX idx_sar_ctxswitch_cswch     ON sar_ctxswitch_stats (hostname, cswch_per_sec DESC);

-- ── Load Average Stats (sar -q) ───────────────────────────────────────

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

CREATE INDEX idx_sar_loadavg_host_time ON sar_loadavg_stats (hostname, snap_time);
CREATE INDEX idx_sar_loadavg_runq      ON sar_loadavg_stats (hostname, runq_sz DESC);
CREATE INDEX idx_sar_loadavg_blocked   ON sar_loadavg_stats (hostname, blocked DESC);


-- ── CDB/PDB/RAC column additions ─────────────────────────────────────────────

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
ALTER TABLE awr_sql_elapsed_time            ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_cpu_time                ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_gets               ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_reads              ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_executions         ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_parse_calls  ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_sharable_mem       ADD COLUMN IF NOT EXISTS pdb_name TEXT;
ALTER TABLE awr_sql_version_count      ADD COLUMN IF NOT EXISTS pdb_name TEXT;
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
--ALTER TABLE awr_seg_table_scans        ADD COLUMN IF NOT EXISTS pdb_name TEXT;
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


-- =============================================================================
-- SECTION 4: NEW v2 TABLES
-- =============================================================================

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
--CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_sql_text_trgm ON awr_sql_text
    USING gin(sql_text gin_trgm_ops);


-- ── Anomaly detection results ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS awr_anomalies (
    id              SERIAL PRIMARY KEY,
    dbname          TEXT        NOT NULL,
    instance        TEXT,
    begin_snap      INT         NOT NULL,
    snap_time       TIMESTAMP,
    metric_source   TEXT        NOT NULL,
    metric_name     TEXT        NOT NULL,
    metric_value    NUMERIC,
    baseline_mean   NUMERIC,
    baseline_stddev NUMERIC,
    z_score         NUMERIC,
    severity        TEXT,
    object_name     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_anomaly UNIQUE (dbname, begin_snap, metric_source, metric_name, object_name)
) TABLESPACE awrparser;
CREATE INDEX IF NOT EXISTS idx_anomaly_db_snap ON awr_anomalies (dbname, begin_snap, severity);
CREATE INDEX IF NOT EXISTS idx_anomaly_time    ON awr_anomalies (snap_time DESC);


-- =============================================================================
-- SECTION 5: LOAD WAIT EVENT MASTER DATA
-- =============================================================================
-- Pass full path to waitevent.txt via psql -v waitevent_path="..."
-- Example:  psql ... -v waitevent_path="C:/AWR_Insight_Portal_v2/waitevent.txt"

COPY awr_wait_event_master (event, wait_class)
FROM :'waitevent_path'
WITH (FORMAT csv, HEADER true);


-- =============================================================================
-- SECTION 6: MATERIALIZED VIEWS AND INDEXES
-- =============================================================================

---------------------------------------------------------------
Materialized view for AWR SQL Summary
---------------------------------------------------------------
--DROP MATERIALIZED VIEW IF EXISTS awr_sql_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_sql_summary_mv
TABLESPACE awrparser
AS
 WITH elapsed AS (
         SELECT awr_sql_elapsed_time.dbname,
            awr_sql_elapsed_time.instance,
            awr_sql_elapsed_time.begin_snap,
            min(awr_sql_elapsed_time.snap_time) AS snap_time,
            awr_sql_elapsed_time.sql_id,
            sum(awr_sql_elapsed_time.executions) AS executions,
            avg(awr_sql_elapsed_time.elapsed_time_per_exec_s) AS elapsed_time_per_exec_s,
            avg(awr_sql_elapsed_time.pct_cpu) AS pct_cpu,
            avg(awr_sql_elapsed_time.pct_io) AS pct_io
           FROM awr_sql_elapsed_time
          GROUP BY awr_sql_elapsed_time.dbname, awr_sql_elapsed_time.instance, awr_sql_elapsed_time.begin_snap, awr_sql_elapsed_time.sql_id
        ), cpu AS (
         SELECT awr_sql_cpu_time.dbname,
            awr_sql_cpu_time.instance,
            awr_sql_cpu_time.begin_snap,
            awr_sql_cpu_time.sql_id,
            avg(awr_sql_cpu_time.cpu_per_exec_s) AS cpu_per_exec_s
           FROM awr_sql_cpu_time
          GROUP BY awr_sql_cpu_time.dbname, awr_sql_cpu_time.instance, awr_sql_cpu_time.begin_snap, awr_sql_cpu_time.sql_id
        ), uio AS (
         SELECT awr_sql_user_io_time.dbname,
            awr_sql_user_io_time.instance,
            awr_sql_user_io_time.begin_snap,
            awr_sql_user_io_time.sql_id,
            avg(awr_sql_user_io_time.uio_per_exec_s) AS user_io_wait_per_exec_s
           FROM awr_sql_user_io_time
          GROUP BY awr_sql_user_io_time.dbname, awr_sql_user_io_time.instance, awr_sql_user_io_time.begin_snap, awr_sql_user_io_time.sql_id
        ), gets AS (
         SELECT awr_sql_gets.dbname,
            awr_sql_gets.instance,
            awr_sql_gets.begin_snap,
            awr_sql_gets.sql_id,
            avg(awr_sql_gets.gets_per_exec) AS buffer_gets_per_exec
           FROM awr_sql_gets
          GROUP BY awr_sql_gets.dbname, awr_sql_gets.instance, awr_sql_gets.begin_snap, awr_sql_gets.sql_id
        ), reads AS (
         SELECT awr_sql_reads.dbname,
            awr_sql_reads.instance,
            awr_sql_reads.begin_snap,
            awr_sql_reads.sql_id,
            avg(awr_sql_reads.reads_per_exec) AS disk_reads_per_exec
           FROM awr_sql_reads
          GROUP BY awr_sql_reads.dbname, awr_sql_reads.instance, awr_sql_reads.begin_snap, awr_sql_reads.sql_id
        ), unopt AS (
         SELECT awr_sql_phy_reads_unopt.dbname,
            awr_sql_phy_reads_unopt.instance,
            awr_sql_phy_reads_unopt.begin_snap,
            awr_sql_phy_reads_unopt.sql_id,
            avg(awr_sql_phy_reads_unopt.unoptimized_reqs_per_exec) AS unoptimized_reqs_per_exec,
            avg(awr_sql_phy_reads_unopt.pctopt) AS pct_opt
           FROM awr_sql_phy_reads_unopt
          GROUP BY awr_sql_phy_reads_unopt.dbname, awr_sql_phy_reads_unopt.instance, awr_sql_phy_reads_unopt.begin_snap, awr_sql_phy_reads_unopt.sql_id
        ), execs AS (
         SELECT awr_sql_executions.dbname,
            awr_sql_executions.instance,
            awr_sql_executions.begin_snap,
            awr_sql_executions.sql_id,
            sum(awr_sql_executions.executions) AS total_executions
           FROM awr_sql_executions
          GROUP BY awr_sql_executions.dbname, awr_sql_executions.instance, awr_sql_executions.begin_snap, awr_sql_executions.sql_id
        ), parses AS (
         SELECT awr_sql_parsed_calls.dbname,
            awr_sql_parsed_calls.instance,
            awr_sql_parsed_calls.begin_snap,
            awr_sql_parsed_calls.sql_id,
            sum(awr_sql_parsed_calls.parse_calls) AS parse_calls,
            avg(awr_sql_parsed_calls.pct_total_parses) AS pct_total_parses
           FROM awr_sql_parsed_calls
          GROUP BY awr_sql_parsed_calls.dbname, awr_sql_parsed_calls.instance, awr_sql_parsed_calls.begin_snap, awr_sql_parsed_calls.sql_id
        ), clu AS (
         SELECT awr_sql_cluster_wait_time.dbname,
            awr_sql_cluster_wait_time.instance,
            awr_sql_cluster_wait_time.begin_snap,
            awr_sql_cluster_wait_time.sql_id,
            avg(awr_sql_cluster_wait_time.cluster_wait_time_s) AS cluster_wait_time_per_exec_s,
            avg(awr_sql_cluster_wait_time.pct_clu) AS pct_clu
           FROM awr_sql_cluster_wait_time
          GROUP BY awr_sql_cluster_wait_time.dbname, awr_sql_cluster_wait_time.instance, awr_sql_cluster_wait_time.begin_snap, awr_sql_cluster_wait_time.sql_id
        )
 SELECT e.dbname,
    e.instance,
    e.begin_snap,
    e.snap_time,
    e.sql_id,
    e.executions,
    e.elapsed_time_per_exec_s,
    c.cpu_per_exec_s,
    u.user_io_wait_per_exec_s,
    g.buffer_gets_per_exec,
    r.disk_reads_per_exec,
    un.unoptimized_reqs_per_exec,
    un.pct_opt,
    ex.total_executions,
    p.parse_calls,
    p.pct_total_parses,
    cl.cluster_wait_time_per_exec_s,
    cl.pct_clu,
    (COALESCE(e.elapsed_time_per_exec_s, 0::numeric) * 0.25 + COALESCE(c.cpu_per_exec_s, 0::numeric) * 0.25 + COALESCE(g.buffer_gets_per_exec, 0::numeric) / 10000.0 * 0.15 + COALESCE(r.disk_reads_per_exec, 0::numeric) / 1000.0 * 0.10 + COALESCE(u.user_io_wait_per_exec_s, 0::numeric) * 0.10 + COALESCE(un.unoptimized_reqs_per_exec, 0::numeric) / 1000.0 * 0.05 + COALESCE(p.parse_calls, 0::numeric) / 1000.0 * 0.05 + COALESCE(cl.cluster_wait_time_per_exec_s, 0::numeric) * 0.05)::numeric(18,4) AS severity_score,row_number() over () AS mv_id
   FROM elapsed e
     LEFT JOIN cpu c ON c.dbname = e.dbname AND c.instance = e.instance AND c.begin_snap = e.begin_snap AND c.sql_id = e.sql_id
     LEFT JOIN uio u ON u.dbname = e.dbname AND u.instance = e.instance AND u.begin_snap = e.begin_snap AND u.sql_id = e.sql_id
     LEFT JOIN gets g ON g.dbname = e.dbname AND g.instance = e.instance AND g.begin_snap = e.begin_snap AND g.sql_id = e.sql_id
     LEFT JOIN reads r ON r.dbname = e.dbname AND r.instance = e.instance AND r.begin_snap = e.begin_snap AND r.sql_id = e.sql_id
     LEFT JOIN unopt un ON un.dbname = e.dbname AND un.instance = e.instance AND un.begin_snap = e.begin_snap AND un.sql_id = e.sql_id
     LEFT JOIN execs ex ON ex.dbname = e.dbname AND ex.instance = e.instance AND ex.begin_snap = e.begin_snap AND ex.sql_id = e.sql_id
     LEFT JOIN parses p ON p.dbname = e.dbname AND p.instance = e.instance AND p.begin_snap = e.begin_snap AND p.sql_id = e.sql_id
     LEFT JOIN clu cl ON cl.dbname = e.dbname AND cl.instance = e.instance AND cl.begin_snap = e.begin_snap AND cl.sql_id = e.sql_id
WITH DATA;

--CREATE UNIQUE INDEX idx_awr_sql_summary_mv_id ON awr_sql_summary_mv (mv_id) TABLESPACE awrparser_idx;

--CREATE INDEX idx_elapsed ON awr_sql_elapsed_time (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_cpu     ON awr_sql_cpu_time (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_uio     ON awr_sql_user_io_time (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_gets    ON awr_sql_gets (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_reads   ON awr_sql_reads (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_phy     ON awr_sql_phy_reads_unopt (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_parse   ON awr_sql_parsed_calls (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_cluster ON awr_sql_cluster_wait_time (dbname, instance, sql_id, begin_snap) tablespace awrparser_idx;

---------------------------------------------------------------
--Materialized view for AWR Segment Summary
---------------------------------------------------------------

--DROP MATERIALIZED VIEW IF EXISTS awr_segment_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_segment_summary_mv
TABLESPACE awrparser
AS
 WITH logical_reads AS (
         SELECT awr_seg_logical_reads.dbname,
            awr_seg_logical_reads.instance,
            awr_seg_logical_reads.begin_snap,
            min(awr_seg_logical_reads.snap_time) AS snap_time,
            awr_seg_logical_reads.tablespace_name,
            awr_seg_logical_reads.obj_type,
            awr_seg_logical_reads.owner,
            awr_seg_logical_reads.object_name,
            awr_seg_logical_reads.subobject_name,
            avg(awr_seg_logical_reads.logical_reads) AS logical_reads
           FROM awr_seg_logical_reads
          GROUP BY awr_seg_logical_reads.dbname, awr_seg_logical_reads.instance, awr_seg_logical_reads.begin_snap, awr_seg_logical_reads.tablespace_name, awr_seg_logical_reads.obj_type, awr_seg_logical_reads.owner, awr_seg_logical_reads.object_name, awr_seg_logical_reads.subobject_name
        ), physical_reads AS (
         SELECT awr_seg_phy_reads.dbname,
            awr_seg_phy_reads.instance,
            awr_seg_phy_reads.begin_snap,
            avg(awr_seg_phy_reads.physical_reads) AS physical_reads
           FROM awr_seg_phy_reads
          GROUP BY awr_seg_phy_reads.dbname, awr_seg_phy_reads.instance, awr_seg_phy_reads.begin_snap
        ), physical_writes AS (
         SELECT awr_seg_phy_writes.dbname,
            awr_seg_phy_writes.instance,
            awr_seg_phy_writes.begin_snap,
            avg(awr_seg_phy_writes.physical_writes) AS physical_writes
           FROM awr_seg_phy_writes
          GROUP BY awr_seg_phy_writes.dbname, awr_seg_phy_writes.instance, awr_seg_phy_writes.begin_snap
        ), direct_reads AS (
         SELECT awr_seg_direct_phy_reads.dbname,
            awr_seg_direct_phy_reads.instance,
            awr_seg_direct_phy_reads.begin_snap,
            avg(awr_seg_direct_phy_reads.direct_reads) AS direct_reads
           FROM awr_seg_direct_phy_reads
          GROUP BY awr_seg_direct_phy_reads.dbname, awr_seg_direct_phy_reads.instance, awr_seg_direct_phy_reads.begin_snap
        ), direct_writes AS (
         SELECT awr_seg_direct_phy_writes.dbname,
            awr_seg_direct_phy_writes.instance,
            awr_seg_direct_phy_writes.begin_snap,
            avg(awr_seg_direct_phy_writes.direct_writes) AS direct_writes
           FROM awr_seg_direct_phy_writes
          GROUP BY awr_seg_direct_phy_writes.dbname, awr_seg_direct_phy_writes.instance, awr_seg_direct_phy_writes.begin_snap
        ), read_req AS (
         SELECT awr_seg_phy_read_req.dbname,
            awr_seg_phy_read_req.instance,
            awr_seg_phy_read_req.begin_snap,
            avg(awr_seg_phy_read_req.phys_read_requests) AS phys_read_requests
           FROM awr_seg_phy_read_req
          GROUP BY awr_seg_phy_read_req.dbname, awr_seg_phy_read_req.instance, awr_seg_phy_read_req.begin_snap
        ), write_req AS (
         SELECT awr_seg_phy_write_req.dbname,
            awr_seg_phy_write_req.instance,
            awr_seg_phy_write_req.begin_snap,
            avg(awr_seg_phy_write_req.phys_write_requests) AS phys_write_requests
           FROM awr_seg_phy_write_req
          GROUP BY awr_seg_phy_write_req.dbname, awr_seg_phy_write_req.instance, awr_seg_phy_write_req.begin_snap
        ), cr_blocks AS (
         SELECT awr_seg_cr_blk_rec.dbname,
            awr_seg_cr_blk_rec.instance,
            awr_seg_cr_blk_rec.begin_snap,
            avg(awr_seg_cr_blk_rec.cr_blocks_received) AS cr_blocks_received
           FROM awr_seg_cr_blk_rec
          GROUP BY awr_seg_cr_blk_rec.dbname, awr_seg_cr_blk_rec.instance, awr_seg_cr_blk_rec.begin_snap
        ), cur_blocks AS (
         SELECT awr_seg_cur_blk_rec.dbname,
            awr_seg_cur_blk_rec.instance,
            awr_seg_cur_blk_rec.begin_snap,
            avg(awr_seg_cur_blk_rec.current_blocks_received) AS current_blocks_received
           FROM awr_seg_cur_blk_rec
          GROUP BY awr_seg_cur_blk_rec.dbname, awr_seg_cur_blk_rec.instance, awr_seg_cur_blk_rec.begin_snap
        ), unopt_reads AS (
         SELECT awr_seg_unopt_reads.dbname,
            awr_seg_unopt_reads.instance,
            awr_seg_unopt_reads.begin_snap,
            avg(awr_seg_unopt_reads.unoptimized_reads) AS unoptimized_reads
           FROM awr_seg_unopt_reads
          GROUP BY awr_seg_unopt_reads.dbname, awr_seg_unopt_reads.instance, awr_seg_unopt_reads.begin_snap
        ), opt_reads AS (
         SELECT awr_seg_opt_reads.dbname,
            awr_seg_opt_reads.instance,
            awr_seg_opt_reads.begin_snap,
            avg(awr_seg_opt_reads.optimized_reads) AS optimized_reads
           FROM awr_seg_opt_reads
          GROUP BY awr_seg_opt_reads.dbname, awr_seg_opt_reads.instance, awr_seg_opt_reads.begin_snap
        ), table_scans AS (
         SELECT awr_seg_table_scan.dbname,
            awr_seg_table_scan.instance,
            awr_seg_table_scan.begin_snap,
            avg(awr_seg_table_scan.table_scans) AS table_scans
           FROM awr_seg_table_scan
          GROUP BY awr_seg_table_scan.dbname, awr_seg_table_scan.instance, awr_seg_table_scan.begin_snap
        ), db_block_changes AS (
         SELECT awr_seg_db_blk_chg.dbname,
            awr_seg_db_blk_chg.instance,
            awr_seg_db_blk_chg.begin_snap,
            avg(awr_seg_db_blk_chg.db_block_changes) AS db_block_changes
           FROM awr_seg_db_blk_chg
          GROUP BY awr_seg_db_blk_chg.dbname, awr_seg_db_blk_chg.instance, awr_seg_db_blk_chg.begin_snap
        ), row_lock AS (
         SELECT awr_seg_row_lck_waits.dbname,
            awr_seg_row_lck_waits.instance,
            awr_seg_row_lck_waits.begin_snap,
            avg(awr_seg_row_lck_waits.row_lock_waits) AS row_lock_waits
           FROM awr_seg_row_lck_waits
          GROUP BY awr_seg_row_lck_waits.dbname, awr_seg_row_lck_waits.instance, awr_seg_row_lck_waits.begin_snap
        ), itl AS (
         SELECT awr_seg_itl_waits.dbname,
            awr_seg_itl_waits.instance,
            awr_seg_itl_waits.begin_snap,
            avg(awr_seg_itl_waits.itl_waits) AS itl_waits
           FROM awr_seg_itl_waits
          GROUP BY awr_seg_itl_waits.dbname, awr_seg_itl_waits.instance, awr_seg_itl_waits.begin_snap
        ), buff_busy AS (
         SELECT awr_seg_buff_busy_waits.dbname,
            awr_seg_buff_busy_waits.instance,
            awr_seg_buff_busy_waits.begin_snap,
            avg(awr_seg_buff_busy_waits.buffer_busy_waits) AS buffer_busy_waits
           FROM awr_seg_buff_busy_waits
          GROUP BY awr_seg_buff_busy_waits.dbname, awr_seg_buff_busy_waits.instance, awr_seg_buff_busy_waits.begin_snap
        ), gc_busy AS (
         SELECT awr_seg_gbl_cache_buff_busy.dbname,
            awr_seg_gbl_cache_buff_busy.instance,
            awr_seg_gbl_cache_buff_busy.begin_snap,
            avg(awr_seg_gbl_cache_buff_busy.gc_buffer_busy) AS gc_buffer_busy
           FROM awr_seg_gbl_cache_buff_busy
          GROUP BY awr_seg_gbl_cache_buff_busy.dbname, awr_seg_gbl_cache_buff_busy.instance, awr_seg_gbl_cache_buff_busy.begin_snap
        )
 SELECT l.dbname,
    l.instance,
    l.begin_snap,
    l.snap_time,
    l.tablespace_name,
    l.owner,
    l.obj_type,
    l.object_name,
    l.subobject_name,
    l.logical_reads,
    pr.physical_reads,
    pw.physical_writes,
    dr.direct_reads,
    dw.direct_writes,
    rr.phys_read_requests,
    wr.phys_write_requests,
    cb.cr_blocks_received,
    cur.current_blocks_received,
    un.unoptimized_reads,
    opt.optimized_reads,
    ts.table_scans,
    dbc.db_block_changes,
    rl.row_lock_waits,
    it.itl_waits,
    bb.buffer_busy_waits,
    gc.gc_buffer_busy,
    (COALESCE(pr.physical_reads, 0::numeric) * 0.15 + (COALESCE(dr.direct_reads, 0::numeric) + COALESCE(dw.direct_writes, 0::numeric)) * 0.10 + COALESCE(l.logical_reads, 0::numeric) * 0.10 + (COALESCE(cb.cr_blocks_received, 0::numeric) + COALESCE(cur.current_blocks_received, 0::numeric)) * 0.10 + COALESCE(ts.table_scans, 0::numeric) * 0.05 + COALESCE(un.unoptimized_reads, 0::numeric) * 0.05 + (COALESCE(rr.phys_read_requests, 0::numeric) + COALESCE(wr.phys_write_requests, 0::numeric)) * 0.03 + COALESCE(opt.optimized_reads, 0::numeric) * 0.02 + COALESCE(dbc.db_block_changes, 0::numeric) * 0.10 + COALESCE(rl.row_lock_waits, 0::numeric) * 0.10 + COALESCE(it.itl_waits, 0::numeric) * 0.05 + COALESCE(bb.buffer_busy_waits, 0::numeric) * 0.10 + COALESCE(gc.gc_buffer_busy, 0::numeric) * 0.05)::numeric(18,4) AS severity_score, 
row_number() over () AS mv_id 
   FROM logical_reads l
     LEFT JOIN physical_reads pr ON pr.dbname = l.dbname AND pr.instance = l.instance AND pr.begin_snap = l.begin_snap
     LEFT JOIN physical_writes pw ON pw.dbname = l.dbname AND pw.instance = l.instance AND pw.begin_snap = l.begin_snap
     LEFT JOIN direct_reads dr ON dr.dbname = l.dbname AND dr.instance = l.instance AND dr.begin_snap = l.begin_snap
     LEFT JOIN direct_writes dw ON dw.dbname = l.dbname AND dw.instance = l.instance AND dw.begin_snap = l.begin_snap
     LEFT JOIN read_req rr ON rr.dbname = l.dbname AND rr.instance = l.instance AND rr.begin_snap = l.begin_snap
     LEFT JOIN write_req wr ON wr.dbname = l.dbname AND wr.instance = l.instance AND wr.begin_snap = l.begin_snap
     LEFT JOIN cr_blocks cb ON cb.dbname = l.dbname AND cb.instance = l.instance AND cb.begin_snap = l.begin_snap
     LEFT JOIN cur_blocks cur ON cur.dbname = l.dbname AND cur.instance = l.instance AND cur.begin_snap = l.begin_snap
     LEFT JOIN unopt_reads un ON un.dbname = l.dbname AND un.instance = l.instance AND un.begin_snap = l.begin_snap
     LEFT JOIN opt_reads opt ON opt.dbname = l.dbname AND opt.instance = l.instance AND opt.begin_snap = l.begin_snap
     LEFT JOIN table_scans ts ON ts.dbname = l.dbname AND ts.instance = l.instance AND ts.begin_snap = l.begin_snap
     LEFT JOIN db_block_changes dbc ON dbc.dbname = l.dbname AND dbc.instance = l.instance AND dbc.begin_snap = l.begin_snap
     LEFT JOIN row_lock rl ON rl.dbname = l.dbname AND rl.instance = l.instance AND rl.begin_snap = l.begin_snap
     LEFT JOIN itl it ON it.dbname = l.dbname AND it.instance = l.instance AND it.begin_snap = l.begin_snap
     LEFT JOIN buff_busy bb ON bb.dbname = l.dbname AND bb.instance = l.instance AND bb.begin_snap = l.begin_snap
     LEFT JOIN gc_busy gc ON gc.dbname = l.dbname AND gc.instance = l.instance AND gc.begin_snap = l.begin_snap
WITH DATA;

-- Indexes for performance
--CREATE UNIQUE INDEX idx_awr_seg_summary_mv_id ON awr_segment_summary_mv (mv_id) TABLESPACE awrparser_idx;

--CREATE INDEX idx_seg_summary_dbinstsnap ON awr_seg_summary_mv (dbname, instance, begin_snap) tablespace awrparser_idx;
--CREATE INDEX idx_seg_summary_score ON awr_seg_summary_mv (severity_score DESC) tablespace awrparser_idx;

------------------------------------------------------------
--Foreground Wait Event Summary
------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_fg_wait_summary_mv
TABLESPACE awrparser
AS
 WITH fg_waits AS (
         SELECT awr_foreground_wait_events.dbname,
            awr_foreground_wait_events.instance,
            awr_foreground_wait_events.begin_snap,
            min(awr_foreground_wait_events.snap_time) AS snap_time,
            awr_foreground_wait_events.event AS wait_event,
            sum(awr_foreground_wait_events.waits) AS total_waits,
            sum(awr_foreground_wait_events.total_wait_time_s) AS total_wait_time_s,
            round(avg(awr_foreground_wait_events.avg_wait_ms), 3) AS avg_wait_ms,
            max(awr_foreground_wait_events.avg_wait_ms) AS max_wait_ms,
            min(awr_foreground_wait_events.avg_wait_ms) AS min_wait_ms,
            round(avg(awr_foreground_wait_events.pct_db_time), 2) AS avg_pct_db_time
           FROM awr_foreground_wait_events
          WHERE awr_foreground_wait_events.pct_db_time > 5::numeric
          GROUP BY awr_foreground_wait_events.dbname, awr_foreground_wait_events.instance, awr_foreground_wait_events.begin_snap, awr_foreground_wait_events.event
        )
 SELECT f.dbname,
    f.instance,
    f.begin_snap,
    f.snap_time,
    f.wait_event,
    f.total_waits,
    f.total_wait_time_s,
    f.avg_wait_ms,
    f.max_wait_ms,
    f.min_wait_ms,
    f.avg_pct_db_time,
    round(COALESCE(f.avg_pct_db_time, 0::numeric) * 0.5 + COALESCE(f.total_wait_time_s, 0::numeric) * 0.3 + COALESCE(f.avg_wait_ms, 0::numeric) * 0.2, 2) AS severity_score
   FROM fg_waits f
  WHERE f.total_wait_time_s > 0::numeric
  ORDER BY (round(COALESCE(f.avg_pct_db_time, 0::numeric) * 0.5 + COALESCE(f.total_wait_time_s, 0::numeric) * 0.3 + COALESCE(f.avg_wait_ms, 0::numeric) * 0.2, 2)) DESC
WITH DATA;

CREATE INDEX idx_fg_wait_summary_dbinstsnap
    ON awr_fg_wait_summary_mv USING btree
    (dbname COLLATE pg_catalog."default", instance COLLATE pg_catalog."default", begin_snap)
    TABLESPACE awrparser_idx;

---------------------------------------------------------------
--Foreground wait event summary with wait class
-----------------------------------------------------------------

--DROP MATERIALIZED VIEW IF EXISTS awr_fg_wait_event_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_fg_wait_event_summary_mv
TABLESPACE pg_default
AS
 SELECT e.dbname,
    e.instance,
    e.begin_snap,
    min(e.snap_time) AS snap_time,
    COALESCE(m.wait_class, 'Other'::text) AS wait_class,
    e.event,
    sum(e.waits) AS total_waits,
    sum(e.total_wait_time_s) AS total_wait_time_s,
    round(avg(e.avg_wait_ms), 2) AS avg_wait_ms,
    round(avg(e.waits_per_txn), 2) AS waits_per_txn,
    round(avg(e.pct_db_time), 2) AS pct_db_time,
        CASE
            WHEN avg(e.pct_db_time) >= 20::numeric THEN 5
            WHEN avg(e.pct_db_time) >= 10::numeric THEN 4
            WHEN avg(e.pct_db_time) >= 5::numeric THEN 3
            WHEN avg(e.pct_db_time) >= 2::numeric THEN 2
            ELSE 1
        END AS severity_score
   FROM awr_foreground_wait_events e
     LEFT JOIN awr_wait_event_master m ON btrim(lower(e.event)) = btrim(lower(m.event))
  WHERE e.pct_db_time > 5::numeric
  GROUP BY e.dbname, e.instance, e.begin_snap, m.wait_class, e.event
WITH DATA;


------------------------------------------------------------
--Background Wait Event Summary
------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_bg_wait_summary_mv
TABLESPACE awrparser
AS
 WITH bg_waits AS (
         SELECT awr_background_wait_events.dbname,
            awr_background_wait_events.instance,
            awr_background_wait_events.begin_snap,
            min(awr_background_wait_events.snap_time) AS snap_time,
            awr_background_wait_events.event AS wait_event,
            sum(awr_background_wait_events.waits) AS total_waits,
            sum(awr_background_wait_events.total_wait_time_s) AS total_wait_time_s,
            round(avg(awr_background_wait_events.avg_wait_ms), 3) AS avg_wait_ms,
            max(awr_background_wait_events.avg_wait_ms) AS max_wait_ms,
            min(awr_background_wait_events.avg_wait_ms) AS min_wait_ms,
            round(avg(awr_background_wait_events.pct_bg_time), 2) AS avg_pct_bg_time
           FROM awr_background_wait_events
          WHERE awr_background_wait_events.pct_bg_time > 5::numeric
          GROUP BY awr_background_wait_events.dbname, awr_background_wait_events.instance, awr_background_wait_events.begin_snap, awr_background_wait_events.event
        )
 SELECT b.dbname,
    b.instance,
    b.begin_snap,
    b.snap_time,
    b.wait_event,
    b.total_waits,
    b.total_wait_time_s,
    b.avg_wait_ms,
    b.max_wait_ms,
    b.min_wait_ms,
    b.avg_pct_bg_time,
    round(COALESCE(b.avg_pct_bg_time, 0::numeric) * 0.5 + COALESCE(b.total_wait_time_s, 0::numeric) * 0.3 + COALESCE(b.avg_wait_ms, 0::numeric) * 0.2, 2) AS severity_score
   FROM bg_waits b
  WHERE b.total_wait_time_s > 0::numeric
  ORDER BY (round(COALESCE(b.avg_pct_bg_time, 0::numeric) * 0.5 + COALESCE(b.total_wait_time_s, 0::numeric) * 0.3 + COALESCE(b.avg_wait_ms, 0::numeric) * 0.2, 2)) DESC
WITH DATA;


CREATE INDEX idx_bg_wait_summary_dbinstsnap
    ON awr_bg_wait_summary_mv USING btree
    (dbname COLLATE pg_catalog."default", instance COLLATE pg_catalog."default", begin_snap)
    TABLESPACE awrparser_idx;

----------------------------------------
--Background wait event summary with wait class
--------------------------------------------------
--DROP MATERIALIZED VIEW IF EXISTS awr_bg_wait_event_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_bg_wait_event_summary_mv
TABLESPACE pg_default
AS
 SELECT e.dbname,
    e.instance,
    e.begin_snap,
    min(e.snap_time) AS snap_time,
    COALESCE(m.wait_class, 'Other'::text) AS wait_class,
    e.event,
    sum(e.waits) AS total_waits,
    sum(e.total_wait_time_s) AS total_wait_time_s,
    round(avg(e.avg_wait_ms), 2) AS avg_wait_ms,
    round(avg(e.waits_per_txn), 2) AS waits_per_txn,
    round(avg(e.pct_db_time), 2) AS pct_db_time,
        CASE
            WHEN avg(e.pct_db_time) >= 20::numeric THEN 5
            WHEN avg(e.pct_db_time) >= 10::numeric THEN 4
            WHEN avg(e.pct_db_time) >= 5::numeric THEN 3
            WHEN avg(e.pct_db_time) >= 2::numeric THEN 2
            ELSE 1
        END AS severity_score
   FROM awr_foreground_wait_events e
     LEFT JOIN awr_wait_event_master m ON btrim(lower(e.event)) = btrim(lower(m.event))
  WHERE e.pct_db_time > 5::numeric
  GROUP BY e.dbname, e.instance, e.begin_snap, m.wait_class, e.event
WITH DATA;
--------------------------------------------

-- ==========================================
-- Materialized View: AWR Wait Events Summary
-- Combines Foreground and Background Wait Events
-- ==========================================


--DROP MATERIALIZED VIEW IF EXISTS awr_wait_summary_mv;

CREATE MATERIALIZED VIEW IF NOT EXISTS awr_wait_summary_mv
TABLESPACE awrparser
AS
 WITH fg AS (
         SELECT e.dbname,
            e.instance,
            e.begin_snap,
            min(e.snap_time) AS snap_time,
            COALESCE(m.wait_class, 'Other'::text) AS wait_class,
            e.event,
            sum(e.waits) AS waits,
            sum(e.total_wait_time_s) AS total_wait_time_s,
            round(avg(e.avg_wait_ms), 2) AS avg_wait_ms,
            round(avg(e.waits_per_txn), 2) AS waits_per_txn,
            round(avg(e.pct_db_time), 2) AS pct_time,
            'Foreground'::text AS wait_scope
           FROM awr_foreground_wait_events e
             LEFT JOIN awr_wait_event_master m ON btrim(lower(e.event)) = btrim(lower(m.event))
          WHERE e.pct_db_time > 5::numeric
          GROUP BY e.dbname, e.instance, e.begin_snap, m.wait_class, e.event
        ), bg AS (
         SELECT e.dbname,
            e.instance,
            e.begin_snap,
            min(e.snap_time) AS snap_time,
            COALESCE(m.wait_class, 'Other'::text) AS wait_class,
            e.event,
            sum(e.waits) AS waits,
            sum(e.total_wait_time_s) AS total_wait_time_s,
            round(avg(e.avg_wait_ms), 2) AS avg_wait_ms,
            round(avg(e.waits_per_txn), 2) AS waits_per_txn,
            round(avg(e.pct_bg_time), 2) AS pct_time,
            'Background'::text AS wait_scope
           FROM awr_background_wait_events e
             LEFT JOIN awr_wait_event_master m ON btrim(lower(e.event)) = btrim(lower(m.event))
          WHERE e.pct_bg_time > 5::numeric
          GROUP BY e.dbname, e.instance, e.begin_snap, m.wait_class, e.event
        )
 SELECT combined.dbname,
    combined.instance,
    combined.begin_snap,
    combined.snap_time,
    combined.wait_scope,
    combined.wait_class,
    combined.event,
    combined.waits,
    combined.total_wait_time_s,
    combined.avg_wait_ms,
    combined.waits_per_txn,
    combined.pct_time,
    round(COALESCE(combined.pct_time, 0::numeric) * 0.7 + COALESCE(combined.total_wait_time_s, 0::numeric) / 100.0 * 0.2 + COALESCE(combined.avg_wait_ms, 0::numeric) / 10.0 * 0.1, 2) AS severity_score
   FROM ( SELECT fg.dbname,
            fg.instance,
            fg.begin_snap,
            fg.snap_time,
            fg.wait_class,
            fg.event,
            fg.waits,
            fg.total_wait_time_s,
            fg.avg_wait_ms,
            fg.waits_per_txn,
            fg.pct_time,
            fg.wait_scope
           FROM fg
        UNION ALL
         SELECT bg.dbname,
            bg.instance,
            bg.begin_snap,
            bg.snap_time,
            bg.wait_class,
            bg.event,
            bg.waits,
            bg.total_wait_time_s,
            bg.avg_wait_ms,
            bg.waits_per_txn,
            bg.pct_time,
            bg.wait_scope
           FROM bg) combined
  ORDER BY (round(COALESCE(combined.pct_time, 0::numeric) * 0.7 + COALESCE(combined.total_wait_time_s, 0::numeric) / 100.0 * 0.2 + COALESCE(combined.avg_wait_ms, 0::numeric) / 10.0 * 0.1, 2)) DESC
WITH DATA;

CREATE INDEX idx_wait_summary_dbinstsnap
    ON awr_wait_summary_mv USING btree
    (dbname COLLATE pg_catalog."default", instance COLLATE pg_catalog."default", begin_snap, wait_scope COLLATE pg_catalog."default", wait_class COLLATE pg_catalog."default")
    TABLESPACE awrparser_idx;


-- =============================================================================
-- SECTION 7: RECOMMENDATION RULES TABLE AND SEED DATA
-- =============================================================================

------------------------------------------------------------
--Recommendation rule table
------------------------------------------------------------
CREATE TABLE IF NOT EXISTS awr_recommendation_rules (
  category   TEXT NOT NULL,     -- 'wait' | 'sql' | 'segment'
  rule_key   TEXT NOT NULL,     -- exact value (e.g. 'db file sequential read') or '*' (wildcard)
  rule_text  TEXT NOT NULL,
  PRIMARY KEY (category, rule_key)
) tablespace awrparser;

------------------------------------------------------------------
--Recommendation Rules
------------------------------------------------------------------
BEGIN;

-- =========================
-- WAIT EVENTS (exact names)
-- =========================
INSERT INTO awr_recommendation_rules (category, rule_key, rule_text) VALUES
('wait','db file sequential read',
 'High single-block I/O latency. Check index access patterns, hot blocks, and storage latency. Consider adding/rebuilding indexes, fixing random I/O hotspots, or reducing row-by-row lookups.'),

('wait','db file scattered read',
 'Multi-block read latency (older events; modern systems use direct path read). Investigate full scans, proper parallelism, and verify multiblock read count/storage throughput.'),

('wait','direct path read',
 'Large scans via direct path. Validate partition pruning, correct degree of parallelism, and storage bandwidth. Confirm scans are expected and not due to missing indexes.'),

('wait','direct path write',
 'Direct path writes during loads/ETL/temp spilling. Validate temp/IO layout, parallel degree, and undo/redo overhead. Consider batching/APPEND operations and checkpoint pressure.'),

('wait','read by other session',
 'Buffer contention from hot blocks. Look for hot index leaf blocks / clustering issues. Consider reverse key indexes, better clustering, or spreading hotspot workload.'),

('wait','buffer busy waits',
 'Buffer contention on data blocks. Check hot blocks (segments/blocks), hash partitioning, or spreading workload. Consider increasing cache where appropriate.'),

('wait','free buffer waits',
 'Buffer cache cannot free buffers fast enough. Investigate dirty buffer write rate, DBWR throughput, redo/log write speed, and check for full-table scans polluting cache.'),

('wait','write complete waits',
 'Process waiting for DBWR to write dirty buffers. Validate DBWR throughput, I/O latency, and redo/log configuration. Check for heavy full scans spilling dirty buffers.'),

('wait','log file sync',
 'Commit latency high. Investigate redo log device latency/size, commit batching, app commit frequency, and synchronous standby (Data Guard) settings.'),

('wait','log file parallel write',
 'Redo writer latency. Review I/O bandwidth, RAID cache policy, write cache, and ASM/volume layout. Ensure redo logs on fast, dedicated storage.'),

('wait','log buffer space',
 'Log buffer is full. Increase LOG_BUFFER or investigate frequent commits and slow log writes (I/O).'),

('wait','log file switch (archiving needed)',
 'Archiver cannot keep up. Increase redo log size/number, speed up archiving I/O, and verify archival destination health.'),

('wait','log file switch (checkpoint incomplete)',
 'Checkpoint cannot complete before log reuse. Increase redo log size/number and tune checkpointing (FAST_START_MTTR_TARGET).'),

('wait','enq: TX - row lock contention',
 'Row lock contention. Investigate conflicting DML, long transactions, missing indexes, and hot rows. Consider shorter commits, proper indexing, and app-side concurrency controls.'),

('wait','enq: TM - contention',
 'Metadata (TM) enqueue contention, often due to unindexed foreign keys. Ensure FK columns are indexed; review concurrent DDL/partition maintenance.'),

('wait','latch free',
 'Latch contention (legacy). Identify the latch (AWR/ASH). Often caused by shared pool pressure, parse storms, or hot block/latch usage; tune memory/parse or reduce concurrency.'),

('wait','cursor: pin S wait on X',
 'High hard/soft parse or shared pool pressure. Reduce parse storms (bind variables), increase shared pool if appropriate, fix SQL churn.'),

('wait','library cache: mutex X',
 'Library cache mutex contention. Reduce parse/invalidations, avoid frequent object recompiles, ensure bind peeking/hard parse churn is minimized.'),

('wait','library cache lock',
 'Library cache lock contention. Look for invalidations, DDL on hot objects, or frequent cursor recompilations. Limit concurrent DDL on shared objects.'),

('wait','library cache load lock',
 'Session waiting for another to load an object. Reduce parse storms and invalidations; consider pinning critical packages; review shared pool sizing.'),

('wait','gc buffer busy acquire',
 'RAC interconnect block contention. Investigate hot blocks, object partitioning, and service/instance affinity. Ensure low-latency interconnect and correct block placement.'),

('wait','gc cr request',
 'RAC consistent-read block shipping latency. Validate interconnect bandwidth/latency, reduce global hot blocks via partitioning or service affinity.'),

('wait','gc current request',
 'RAC current block shipping latency. Look for hot DML blocks. Consider partitioning, instance affinity, or data placement to reduce global contention.'),

('wait','DFS lock handle',
 'Global lock manager wait (RAC). Investigate cross-instance contention on objects; review object partitioning/service affinity.'),

('wait','SQL*Net more data from client',
 'Network round-trips; often benign (fetching results). If excessive, reduce result set size, paginate, or increase arraysize/fetch size.'),

('wait','SQL*Net more data to client',
 'Sending lots of data to client. Optimize payload size/columns; compress or paginate if needed.'),

('wait','SQL*Net message from client',
 'Idle wait for client — usually benign. High time may indicate slow app tier or think time.'),

('wait','SQL*Net message to client',
 'Idle wait; generally negligible unless showing unusual spikes indicating network stack issues.'),

('wait','db file parallel write',
 'Background write latency. Check I/O subsystem, ASM/volume layout, and write queue saturation.'),

('wait','control file sequential read',
 'Control file read latency. Usually low. If high, check control file I/O location and concurrent operations (RMAN).'),

('wait','control file parallel write',
 'Control file writes latency. Ensure control files are mirrored across fast devices; review RMAN/archival pressure.'),

('wait','ASM file metadata operation',
 'ASM metadata operation latency. Validate ASM diskgroup health, rebalance events, and I/O load.'),

('wait','resmgr: cpu quantum',
 'Resource Manager throttling. Check consumer group limits, runaway sessions, and plan directives.'),

('wait','temporary file read',
 'Temp read I/O — sorts/hash joins spilling. Increase PGA/WORKAREA sizes appropriately, add tempfiles or faster storage, reduce large sorts/skews.'),

('wait','temporary file write',
 'Temp write I/O — sorts/hash joins spilling. See “temporary file read” guidance; reduce spills via better joins, stats, and WORKAREA settings.'),

('wait','PX Deq Credit: send blkd',
 'Parallel execution flow control — sending server is blocked. Review DOP, PX message pool, and skew; balance producers/consumers.'),

('wait','PX Deq Credit: execute reply',
 'Parallel execution messaging delay. Investigate PX skew, oversized granules, or insufficient PX servers.'),

('wait','PX qref latch',
 'PX messaging/latch contention. Review parallel degree, skew, and interconnect pressure.'),

('wait','reliable message',
 'Background message wait; often benign. Investigate only if dominating snapshot alongside specific background processes.'),

('wait','rdbms ipc reply',
 'Background process coordination. If high, correlate to specific background (SMON/DBWR/ARCn) and their bottlenecks.'),

('wait','latch: shared pool',
 'Shared pool pressure. Reduce parse, enable binds, size shared pool appropriately, avoid frequent invalidations.'),

('wait','latch: cache buffers chains',
 'Hot CBC latch — hot block contention. Consider reverse-key indexes, hash partitioning, or spreading hotspots; review access patterns.'),

('wait','latch: redo allocation',
 'Redo allocation latch contention. Increase log buffer, reduce commit bursts, and ensure fast redo I/O.'),

('wait','latch: redo copy',
 'Redo copy latch contention. Check log buffer and commit patterns; mitigate bursts and ensure redo devices are fast.'),

('wait','latch: row cache objects',
 'Row cache (data dictionary) contention. Reduce frequent DDL, tune shared pool, and avoid metadata storms.'),

-- Fallback rule for any wait not explicitly listed
('wait','*',
 'General wait hotspot. Correlate top SQLs and segments in the chosen snaps. Verify storage/interconnect health, workload pattern (OLTP vs batch), and consider indexing/partitioning to reduce the hotspot.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

-- =========================
-- SQL (generic keys)
-- =========================
INSERT INTO awr_recommendation_rules (category, rule_key, rule_text) VALUES
-- You can add exact sql_id rows later: ('sql','7pr32saksf831','<specific recommendation>')
('sql','high_cpu',
 'High CPU SQL. Validate cardinality estimates, join methods, and missing indexes. Consider SQL Profiles/Baselines, gather fresh stats with histograms, and parameterize literals.'),
('sql','sql_long_elapsed',
 'Long elapsed time. Check for bad joins, data skew, or excessive remote/RAC block shipping. Validate plan stability and parallel settings.'),
('sql','temp_spill',
 'Sort/hash joins spilling to temp. Increase workarea (PGA/automatic), improve join order, and reduce row width; add/resize tempfiles on fast storage.'),
('sql','plan_change',
 'Frequent plan flips. Pin with SQL Plan Baseline/Profile, fix stats quality (histograms), and reduce bind peek instability.'),
('sql','hard_parse',
 'High hard-parse. Use bind variables, reduce SQL churn, and avoid concatenated literals. Size shared pool appropriately.'),
('sql','full_scan',
 'Full scans detected. Validate selectivity; add indexes or partition pruning. For analytics, ensure scans are intentional and use proper parallelism.'),
('sql','*',
 'Problematic SQL. Check execution plan, stats, join order, and access paths. Add/repair indexes, fix bad cardinality, and stabilize with Profiles/Baselines where appropriate.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

-- =========================
-- SEGMENTS (generic keys)
-- =========================
INSERT INTO awr_recommendation_rules (category, rule_key, rule_text) VALUES
-- You can add exact object_name rows later: ('segment','HR.EMPLOYEES','<specific>')
('segment','undo_high_cpu',
 'Undo/ITL pressure. Validate UNDO sizing/retention, reduce long transactions, and review concurrent hot-row updates.'),
('segment','hot_index_leaf',
 'Hot index leaf blocks. Consider reverse-key index, better clustering, or hash partitioning the index.'),
('segment','*',
 'Hot segment detected. Validate access patterns, clustering/partitioning strategy, and index/selectivity. Consider targeted indexing or data reorg to reduce hotspots.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

COMMIT;


-------------------------------------------------------------------------------------------------
--Recommendation rules updated from recommendation_rulesv1.1.json
---------------------------------------------------------------------------------------------------
INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','db file sequential read','High single-block IO latency. Check index access patterns, storage latency, and verify that hot blocks fit in buffer cache. Consider adding/adjusting indexes or reducing row-by-row lookups.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','direct path read','Direct path reads indicate large scans. Consider partition pruning, proper parallelism, and verifying that large full scans are expected for the workload.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','enq: TX - row lock contention','Row lock contention detected. Investigate conflicting DML transactions, commit frequency, and application logic. Consider shorter transactions and proper indexing to reduce hot spots.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','buffer busy waits','Buffer contention on hot blocks. Consider spreading workload (hash partitioning), increasing INITRANS on hot segments, or addressing hot block access patterns.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','log file sync','Commit latency is high. Check redo log device latency and size, verify low commit batching, and consider tuning filesystem or storage for sequential writes.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('wait','*','General wait event. Review top SQLs and segments contributing around the chosen snaps. Correlate with IO, concurrency, or commit hotspots.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('sql','*','Problematic SQL. Validate plan stability, predicates, and join methods. Confirm proper indexing, up-to-date stats, and consider SQL profile/outline only after root cause analysis.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;

INSERT INTO awr_recommendation_rules(category, rule_key, rule_text)
VALUES ('segment','*','Hot segment. Validate access patterns, clustering, and partitioning. Consider targeted indexing, segment reorg, or ILM to reduce skew and hotspots.')
ON CONFLICT (category, rule_key) DO UPDATE SET rule_text = EXCLUDED.rule_text;


-- =============================================================================
-- SECTION 8: HELPER MVs, NORMALIZED SQL TEXT, PER-SNAP SCORE VIEWS
-- =============================================================================




-- Additional performance indexes
CREATE INDEX IF NOT EXISTS ix_sql_text_snap    ON awr_sql_text (dbname, instance, begin_snap);
CREATE INDEX IF NOT EXISTS idx_sql_text_trgm   ON awr_sql_text USING gin (sql_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_sql_summary_snap ON awr_sql_summary_mv (dbname, instance, begin_snap);
CREATE INDEX IF NOT EXISTS ix_seg_mv_snap      ON awr_segment_summary_mv (dbname, instance, begin_snap);


-- =============================================================================
-- SCHEMA CREATION COMPLETE
-- Verify with:
--   SELECT tablename   FROM pg_tables   WHERE schemaname='public' ORDER BY tablename;
--   SELECT matviewname FROM pg_matviews  WHERE schemaname='public' ORDER BY matviewname;
--   SELECT viewname    FROM pg_views     WHERE schemaname='public' ORDER BY viewname;
-- =============================================================================

