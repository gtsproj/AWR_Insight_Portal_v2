-- ============================================================
-- AWR Insight Portal v2 — Database Security Schema
-- Creates DBA role (full access) and read-only role
-- Run as PostgreSQL superuser (postgres)
--
-- Usage:
--   psql -U postgres -d postgres -f awr_db_security_schema.sql
-- ============================================================

-- ── 1. Create roles ──────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'awr_portal_dba') THEN
        CREATE ROLE awr_portal_dba LOGIN PASSWORD 'AWRPortalDBA@2026';
        RAISE NOTICE 'Created role: awr_portal_dba';
    ELSE
        RAISE NOTICE 'Role awr_portal_dba already exists';
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'awr_portal_reader') THEN
        CREATE ROLE awr_portal_reader LOGIN PASSWORD 'AWRPortalReader@2026';
        RAISE NOTICE 'Created role: awr_portal_reader';
    ELSE
        RAISE NOTICE 'Role awr_portal_reader already exists';
    END IF;
END$$;

-- ── 2. Grant database access ─────────────────────────────────────
GRANT CONNECT ON DATABASE postgres TO awr_portal_dba;
GRANT CONNECT ON DATABASE postgres TO awr_portal_reader;

-- ── 3. Grant schema access ────────────────────────────────────────
GRANT USAGE ON SCHEMA public TO awr_portal_dba;
GRANT USAGE ON SCHEMA public TO awr_portal_reader;

-- ── 4. DBA role — full access to all portal tables ───────────────
-- Select, Insert, Update, Delete on all tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
    TO awr_portal_dba;

-- Sequence access (for SERIAL/auto-increment)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
    TO awr_portal_dba;

-- Future tables — auto-grant
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO awr_portal_dba;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO awr_portal_dba;

-- ── 5. Reader role — SELECT only, no sensitive tables ────────────
-- Grant SELECT on AWR analysis tables (safe to expose to read-only users)
GRANT SELECT ON
    awr_advisory_pga,
    awr_advisory_sga,
    awr_anomalies,
    awr_background_wait_events,
    awr_buffer_wait_statistics,
    awr_change_log,
    awr_db_info,
    awr_enqueue_statistics,
    awr_file_io_stats,
    awr_foreground_wait_class,
    awr_foreground_wait_events,
    awr_instance_activity_stats,
    awr_instance_efficiency,
    awr_io_profile,
    awr_iostat_filetype,
    awr_iostat_func_filetype,
    awr_iostat_function,
    awr_load_profile,
    awr_object_metadata,
    awr_os_statistics,
    awr_recommendation_rules,
    awr_recommendations,
    awr_seg_buff_busy_waits,
    awr_seg_cr_blk_rec,
    awr_seg_cur_blk_rec,
    awr_seg_db_blk_chg,
    awr_seg_direct_phy_reads,
    awr_seg_direct_phy_writes,
    awr_seg_gbl_cache_buff_busy,
    awr_seg_itl_waits,
    awr_seg_logical_reads,
    awr_seg_opt_reads,
    awr_seg_phy_read_req,
    awr_seg_phy_reads,
    awr_seg_phy_write_req,
    awr_seg_phy_writes,
    awr_seg_row_lck_waits,
    awr_seg_table_scan,
    awr_seg_unopt_reads,
    awr_sql_cluster_wait_time,
    awr_sql_cpu_time,
    awr_sql_elapsed_time,
    awr_sql_executions,
    awr_sql_gets,
    awr_sql_parsed_calls,
    awr_sql_phy_reads_unopt,
    awr_sql_reads,
    awr_sql_text,
    awr_sql_user_io_time,
    awr_tablespace_io_stats,
    awr_time_model_stats,
    awr_undo_statistics,
    awr_wait_event_master,
    exec_plan_headers,
    awr_execution_plans,
    sar_anomalies,
    sar_cpu_stats,
    sar_ctxswitch_stats,
    sar_disk_stats,
    sar_hugepage_stats,
    sar_loadavg_stats,
    sar_memory_stats,
    sar_network_stats,
    sar_paging_stats,
    sar_socket_stats,
    sar_swap_stats,
    wait_event_trend,
    awr_db_info
TO awr_portal_reader;

-- AI recommendations readable but not ai_learnings (internal)
GRANT SELECT ON awr_ai_recommendations TO awr_portal_reader;

-- Reader can view comparison tags
GRANT SELECT ON awr_comparison_tags TO awr_portal_reader;

-- ── Tables NOT granted to reader (sensitive/admin only) ──────────
-- portal_config    — contains license key, API keys, passwords
-- portal_users     — user credentials
-- awr_ai_learnings — internal AI learning store
-- awr_license_audit — license event log
-- awr_licensed_dbs  — license management
-- remote_fetch_log  — infrastructure log

-- ── 6. Row-level notes in portal_config ─────────────────────────
-- Reader cannot see portal_config at all (contains API keys, license)
-- DBA can see all portal_config rows

-- ── 7. Update portal_config for DB credentials ───────────────────
INSERT INTO portal_config (key, value, description, section) VALUES
    ('db_dba_user',    'awr_portal_dba',    'PostgreSQL DBA user for portal (read-write)', 'database'),
    ('db_reader_user', 'awr_portal_reader', 'PostgreSQL reader user for Grafana (read-only)', 'database'),
    ('db_host',        'localhost',          'PostgreSQL host',   'database'),
    ('db_port',        '5432',              'PostgreSQL port',   'database'),
    ('db_name',        'postgres',          'PostgreSQL database name', 'database')
ON CONFLICT (key) DO NOTHING;

-- ── 8. Grafana should use read-only user ─────────────────────────
-- In Grafana datasource configuration:
--   User: awr_portal_reader
--   Password: AWRPortalReader@2026
--   This ensures Grafana cannot modify any data

-- ── 9. Verify grants ─────────────────────────────────────────────
DO $$
DECLARE
    tbl_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO tbl_count
    FROM information_schema.role_table_grants
    WHERE grantee = 'awr_portal_reader' AND privilege_type = 'SELECT';
    RAISE NOTICE 'awr_portal_reader has SELECT on % tables', tbl_count;

    SELECT COUNT(*) INTO tbl_count
    FROM information_schema.role_table_grants
    WHERE grantee = 'awr_portal_dba' AND privilege_type IN ('SELECT','INSERT','UPDATE','DELETE');
    RAISE NOTICE 'awr_portal_dba has DML privileges on % table-privilege combinations', tbl_count;
END$$;

-- ── 10. Change passwords (run manually after setup) ──────────────
-- ALTER ROLE awr_portal_dba    PASSWORD 'your_secure_password_here';
-- ALTER ROLE awr_portal_reader PASSWORD 'your_secure_password_here';

-- ── Summary ──────────────────────────────────────────────────────
-- awr_portal_dba    — portal app uses this (full read-write)
-- awr_portal_reader — Grafana uses this (read-only, no sensitive tables)
-- postgres          — superuser, for schema changes and maintenance only
--
-- Connection strings:
--   Portal:  postgresql://awr_portal_dba:AWRPortalDBA@2026@localhost:5432/postgres
--   Grafana: postgresql://awr_portal_reader:AWRPortalReader@2026@localhost:5432/postgres
