-- ============================================================
-- New SAR tables: HugePages + Socket Stats
-- Run against existing schema
-- ============================================================

-- ── HugePages Stats (sar -H) ──────────────────────────────────────────
-- Critical for Oracle: SGA is allocated from HugePages on Linux.
-- If hugused_pct = 100% AND kbhugfree = 0, Oracle cannot grow SGA.
DROP TABLE IF EXISTS sar_hugepage_stats;
CREATE TABLE sar_hugepage_stats (
    id            SERIAL PRIMARY KEY,
    hostname      TEXT        NOT NULL,
    snap_time     TIMESTAMP   NOT NULL,
    kbhugfree     BIGINT,                  -- Free HugePages (KB)
    kbhugused     BIGINT,                  -- Used HugePages (KB)
    hugused_pct   NUMERIC,                 -- % of HugePages used — KEY METRIC
    kbhugrsvd     BIGINT,                  -- Reserved HugePages (KB)
    kbhugsurp     BIGINT,                  -- Surplus HugePages (KB)
    row_hash      CHAR(32)    NOT NULL,
    created_at    TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_hugepage UNIQUE (hostname, snap_time, row_hash)
) TABLESPACE awrparser;

CREATE INDEX idx_sar_hugepage_host_time ON sar_hugepage_stats (hostname, snap_time);
CREATE INDEX idx_sar_hugepage_pct       ON sar_hugepage_stats (hostname, hugused_pct DESC);

-- ── Socket Stats (sar -n SOCK) ────────────────────────────────────────
-- tcp-tw (TIME_WAIT) spikes = connection storms from app servers
-- totsck approaching system limit = socket descriptor exhaustion
DROP TABLE IF EXISTS sar_socket_stats;
CREATE TABLE sar_socket_stats (
    id          SERIAL PRIMARY KEY,
    hostname    TEXT        NOT NULL,
    snap_time   TIMESTAMP   NOT NULL,
    totsck      INT,                        -- Total sockets in use
    tcpsck      INT,                        -- TCP sockets
    udpsck      INT,                        -- UDP sockets
    rawsck      INT,                        -- Raw sockets
    ip_frag     INT,                        -- IP fragments
    tcp_tw      INT,                        -- TCP TIME_WAIT — KEY METRIC
    row_hash    CHAR(32)    NOT NULL,
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sar_socket UNIQUE (hostname, snap_time, row_hash)
) TABLESPACE awrparser;

CREATE INDEX idx_sar_socket_host_time ON sar_socket_stats (hostname, snap_time);
CREATE INDEX idx_sar_socket_tcptw     ON sar_socket_stats (hostname, tcp_tw DESC);
