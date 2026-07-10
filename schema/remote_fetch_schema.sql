-- remote_fetch_log — tracks files fetched from remote sources
-- Run: psql -U postgres -d postgres -f remote_fetch_schema.sql

CREATE TABLE IF NOT EXISTS remote_fetch_log (
    id          SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,   -- awr_network | awr_direct_db | sar_ssh
    source_id   TEXT NOT NULL,   -- UNC path or hostname
    filename    TEXT NOT NULL,
    status      TEXT NOT NULL,   -- ok | error | skipped
    error_msg   TEXT,
    fetched_at  TIMESTAMP DEFAULT NOW()
) TABLESPACE awrparser;

CREATE INDEX IF NOT EXISTS idx_fetch_log_src
  ON remote_fetch_log(source_type, source_id, fetched_at DESC);

-- Add remote source config keys to portal_config
INSERT INTO portal_config (key, value, description, section) VALUES
  ('awr_source_type',     'local',        'AWR source: local | network | direct_db', 'awr_source'),
  ('awr_local_path',      'awr_reports',  'Local AWR drop folder path',              'awr_source'),
  ('awr_network_path',    '',             'UNC/network path for AWR files',           'awr_source'),
  ('awr_db_host',         '',             'Oracle DB host for direct AWR fetch',      'awr_source'),
  ('awr_db_port',         '1521',         'Oracle DB port',                           'awr_source'),
  ('awr_db_service',      '',             'Oracle service name',                      'awr_source'),
  ('awr_db_user',         '',             'Oracle username for AWR fetch',            'awr_source'),
  ('awr_db_password',     '',             'Oracle password for AWR fetch',            'awr_source'),
  ('sar_source_type',     'local',        'SAR source: local | ssh',                  'sar_source'),
  ('sar_local_path',      'sar_drop',     'Local SAR drop folder path',              'sar_source'),
  ('sar_ssh_host',        '',             'SSH host for SAR pull',                    'sar_source'),
  ('sar_ssh_port',        '22',           'SSH port',                                 'sar_source'),
  ('sar_ssh_user',        '',             'SSH username',                             'sar_source'),
  ('sar_ssh_key_path',    '',             'Path to SSH private key file',             'sar_source'),
  ('sar_ssh_password',    '',             'SSH password (if no key)',                 'sar_source'),
  ('sar_ssh_remote_path', '/var/log/sa',  'Remote SAR files path on Linux server',   'sar_source'),
  ('winscp_path',         'C:\\Program Files (x86)\\WinSCP\\WinSCP.com',
                                          'WinSCP CLI path (fallback for SSH)',        'sar_source')
ON CONFLICT (key) DO NOTHING;
