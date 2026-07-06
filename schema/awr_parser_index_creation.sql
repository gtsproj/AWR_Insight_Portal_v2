-- DB Info
CREATE INDEX idx_db_info ON awr_db_info (dbname);

-- Load Profile
CREATE INDEX idx_load_profile ON awr_load_profile (dbname, instance, snap_time, begin_snap);
CREATE INDEX idx_load_profile1 ON awr_load_profile (metric);

-- Instance Efficiency Stats
CREATE INDEX idx_inst_eff ON awr_instance_efficiency (dbname, instance, snap_time, begin_snap);
CREATE INDEX idx_inst_eff1 ON awr_instance_efficiency (metric);

-- Foreground Wait Events
CREATE INDEX idx_fwe_db_snap ON awr_foreground_wait_events (dbname, instance, snap_time, begin_snap);

-- Foreground Wait Class
CREATE INDEX idx_fwc_db_snap ON awr_foreground_wait_class (dbname, instance, snap_time, begin_snap);

-- SQL Ordered by Elapsed Time
CREATE INDEX idx_sql_elapsed ON awr_sql_elapsed_time (dbname, instance, snap_time, begin_snap, sql_id);

-- SQL Ordered by CPU Time
CREATE INDEX idx_sql_cpu ON awr_sql_cpu_time (dbname, instance, snap_time, begin_snap, sql_id);

-- SQL Ordered by User I/O Wait Time
CREATE INDEX idx_sql_io_wait ON awr_sql_io_wait_time (dbname, instance, snap_time, begin_snap, sql_id);

-- Tablespace IO Stats
CREATE INDEX idx_ts_io ON awr_ts_io_stats (dbname, instance, snap_time, begin_snap, tablespace_name);

-- File IO Stats
CREATE INDEX idx_file_io ON awr_file_io_stats (dbname, instance, snap_time, begin_snap, file_name);

-- Segment Logical Reads
CREATE INDEX idx_seg_log_reads ON awr_seg_logical_reads (dbname, instance, snap_time, begin_snap, object_name);

-- Segment Physical Reads
CREATE INDEX idx_seg_phy_reads ON awr_seg_physical_reads (dbname, instance, snap_time, begin_snap, object_name);

-- Segment ITL Waits
CREATE INDEX idx_seg_itl_waits ON awr_seg_itl_waits (dbname, instance, snap_time, begin_snap, object_name);




-- Undo Segment Summary
CREATE INDEX idx_undo_summary ON awr_undo_segment_summary (dbname, instance, snap_time, begin_snap);

-- File IO Stats (duplicate - removed later)
CREATE INDEX idx_file_io_stats ON awr_file_io_stats (dbname, instance, snap_time, begin_snap, file_name);

-- Advisory Stats (SGA)
CREATE INDEX idx_advisory_sga ON awr_sga_advice (dbname, instance, snap_time, begin_snap);

-- Advisory Stats (PGA)
CREATE INDEX idx_advisory_pga ON awr_pga_advice (dbname, instance, snap_time, begin_snap);


-- RAC Statistics: Global Cache Load Profile
CREATE INDEX idx_gc_load ON awr_gc_load_profile (dbname, instance, snap_time, begin_snap);

-- ASH Summary: Top SQL with Top Events
CREATE INDEX idx_ash_sql_events ON awr_ash_sql_top_events (dbname, instance, snap_time, begin_snap, sql_id);
