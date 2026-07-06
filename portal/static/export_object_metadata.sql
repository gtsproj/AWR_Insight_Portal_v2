-- ============================================================
-- export_object_metadata.sql
-- Run this on the Oracle database as a user with DBA/SELECT_CATALOG_ROLE
-- 
-- Usage (SQL*Plus):
--   sqlplus user/password@DBNAME @export_object_metadata.sql DBNAME
--
-- Usage (spool to CSV):
--   sqlplus -s user/password@DBNAME << EOF
--   @export_object_metadata.sql COLDBPRD
--   EOF
--
-- Output: object_metadata_DBNAME.csv in current directory
-- Then upload via AWR Portal → Settings → AWR Source → Upload Metadata
-- ============================================================

SET PAGESIZE 0
SET FEEDBACK OFF
SET HEADING OFF
SET LINESIZE 32767
SET TRIMSPOOL ON
SET COLSEP '|'

DEFINE dbname = '&1'

SPOOL object_metadata_&dbname..csv

-- Header
SELECT 'dbname|owner|object_name|object_type|num_rows|blocks|avg_row_len|last_analyzed|partitioned|compression|index_type|uniqueness|blevel|leaf_blocks|distinct_keys|clustering_factor|status|index_columns|partition_type|partition_count' FROM dual;

-- Tables
SELECT
    '&dbname' AS dbname,
    t.owner,
    t.table_name AS object_name,
    'TABLE' AS object_type,
    NVL(TO_CHAR(t.num_rows), '') AS num_rows,
    NVL(TO_CHAR(t.blocks), '') AS blocks,
    NVL(TO_CHAR(t.avg_row_len), '') AS avg_row_len,
    NVL(TO_CHAR(t.last_analyzed, 'YYYY-MM-DD HH24:MI:SS'), '') AS last_analyzed,
    NVL(t.partitioned, 'NO') AS partitioned,
    NVL(t.compression, '') AS compression,
    '' AS index_type,
    '' AS uniqueness,
    '' AS blevel,
    '' AS leaf_blocks,
    '' AS distinct_keys,
    '' AS clustering_factor,
    '' AS status,
    '' AS index_columns,
    '' AS partition_type,
    '' AS partition_count
FROM dba_tables t
WHERE t.owner NOT IN (
    'SYS','SYSTEM','DBSNMP','SYSMAN','OUTLN','MDSYS','ORDSYS','EXFSYS',
    'DMSYS','WMSYS','CTXSYS','ANONYMOUS','XDB','ORDPLUGINS','OLAPSYS',
    'PUBLIC','APEX_030200','APEX_040000','FLOWS_FILES','APEX_PUBLIC_USER'
)
ORDER BY t.owner, t.table_name;

-- Indexes with column details
SELECT
    '&dbname' AS dbname,
    i.owner,
    i.index_name AS object_name,
    'INDEX' AS object_type,
    NVL(TO_CHAR(i.num_rows), '') AS num_rows,
    NVL(TO_CHAR(i.leaf_blocks), '') AS blocks,
    '' AS avg_row_len,
    NVL(TO_CHAR(i.last_analyzed, 'YYYY-MM-DD HH24:MI:SS'), '') AS last_analyzed,
    '' AS partitioned,
    '' AS compression,
    NVL(i.index_type, '') AS index_type,
    NVL(i.uniqueness, '') AS uniqueness,
    NVL(TO_CHAR(i.blevel), '') AS blevel,
    NVL(TO_CHAR(i.leaf_blocks), '') AS leaf_blocks,
    NVL(TO_CHAR(i.distinct_keys), '') AS distinct_keys,
    NVL(TO_CHAR(i.clustering_factor), '') AS clustering_factor,
    NVL(i.status, '') AS status,
    -- Build JSON array of index columns
    '[' ||
    LISTAGG(
        '{"col":"' || c.column_name || '"' ||
        ',"pos":' || c.column_position ||
        ',"desc":"' || NVL(c.descend,'ASC') || '"' ||
        ',"table":"' || i.table_name || '"' ||
        '}',
        ','
    ) WITHIN GROUP (ORDER BY c.column_position)
    || ']' AS index_columns,
    '' AS partition_type,
    '' AS partition_count
FROM dba_indexes i
JOIN dba_ind_columns c
    ON i.owner = c.index_owner
   AND i.index_name = c.index_name
WHERE i.owner NOT IN (
    'SYS','SYSTEM','DBSNMP','SYSMAN','OUTLN','MDSYS','ORDSYS','EXFSYS',
    'DMSYS','WMSYS','CTXSYS','ANONYMOUS','XDB','ORDPLUGINS','OLAPSYS',
    'PUBLIC','APEX_030200','APEX_040000','FLOWS_FILES','APEX_PUBLIC_USER'
)
GROUP BY
    i.owner, i.index_name, i.table_name, i.index_type, i.uniqueness,
    i.blevel, i.leaf_blocks, i.distinct_keys, i.clustering_factor,
    i.status, i.num_rows, i.last_analyzed
ORDER BY i.owner, i.index_name;

-- Table Partitions (summary per table)
SELECT
    '&dbname' AS dbname,
    p.table_owner AS owner,
    p.table_name AS object_name,
    'PARTITION' AS object_type,
    '' AS num_rows,
    '' AS blocks,
    '' AS avg_row_len,
    '' AS last_analyzed,
    '' AS partitioned,
    '' AS compression,
    '' AS index_type,
    '' AS uniqueness,
    '' AS blevel,
    '' AS leaf_blocks,
    '' AS distinct_keys,
    '' AS clustering_factor,
    '' AS status,
    '' AS index_columns,
    pt.partitioning_type AS partition_type,
    TO_CHAR(COUNT(DISTINCT p.partition_name)) AS partition_count
FROM dba_tab_partitions p
JOIN dba_part_tables pt
    ON p.table_owner = pt.owner
   AND p.table_name  = pt.table_name
WHERE p.table_owner NOT IN (
    'SYS','SYSTEM','DBSNMP','SYSMAN','OUTLN','MDSYS','ORDSYS','EXFSYS',
    'DMSYS','WMSYS','CTXSYS','ANONYMOUS','XDB'
)
GROUP BY p.table_owner, p.table_name, pt.partitioning_type
ORDER BY p.table_owner, p.table_name;

SPOOL OFF

PROMPT
PROMPT ============================================================
PROMPT Export complete: object_metadata_&dbname..csv
PROMPT Upload this file via:
PROMPT   AWR Portal -> Settings -> Object Metadata -> Upload CSV
PROMPT ============================================================
