# modules/remote_fetch.py
# ============================================================
# AWR Insight Portal v2 — Remote File Fetch Framework
#
# Supports:
#   AWR: local path | Network/UNC path | Direct Oracle DB (future)
#   SAR: local path | SSH pull from Linux server (via paramiko/WinSCP)
#
# Called by awr_watcher.py and sar_watcher.py when source
# type is not 'local'.
#
# Usage:
#   from modules.remote_fetch import AWRFetcher, SARFetcher
#   fetcher = AWRFetcher(config)
#   new_files = fetcher.fetch_new(since=last_parsed_time)
# ============================================================

import os
import sys
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("remote_fetch")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))


# ── Config loader ──────────────────────────────────────────────────
def _get_source_config() -> dict:
    """Load AWR/SAR source config from portal_config table."""
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT key, value FROM portal_config
                WHERE key LIKE 'awr_%' OR key LIKE 'sar_%'
            """)
            cfg = {r[0]: r[1] for r in cur.fetchall()}
        conn.close()
        return cfg
    except Exception as e:
        logger.warning(f"Could not load source config: {e}")
        return {}


def _get_last_fetch_time(source_type: str, source_id: str) -> Optional[datetime]:
    """
    Returns the timestamp of the last successfully fetched file
    for a given source, so we only fetch newer files.
    """
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(fetched_at) FROM remote_fetch_log
                WHERE source_type=%s AND source_id=%s AND status='ok'
            """, (source_type, source_id))
            row = cur.fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _log_fetch(source_type: str, source_id: str,
               filename: str, status: str, error: str = "") -> None:
    """Log a fetch attempt to remote_fetch_log table."""
    try:
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO remote_fetch_log
                  (source_type, source_id, filename, status, error_msg, fetched_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
            """, (source_type, source_id, filename, status, error[:500]))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Could not log fetch: {e}")


# ══════════════════════════════════════════════════════════════════
# AWR FETCHER
# ══════════════════════════════════════════════════════════════════

class AWRFetcher:
    """
    Fetches AWR HTML reports from configured source.

    Source types:
      local   — watch local folder (handled by awr_watcher directly)
      network — copy from UNC/network path to local drop folder
      direct_db — generate AWR via DBMS_WORKLOAD_REPOSITORY (future)
    """

    def __init__(self, config: dict = None):
        self.cfg = config or _get_source_config()
        self.source_type = self.cfg.get("awr_source_type", "local")
        self.local_drop  = self.cfg.get("awr_local_path", "awr_reports")
        # Resolve relative path to absolute
        if not os.path.isabs(self.local_drop):
            self.local_drop = os.path.join(_PROJECT_ROOT, self.local_drop)
        os.makedirs(self.local_drop, exist_ok=True)

    def fetch_new(self, since: datetime = None) -> list:
        """
        Fetch new AWR files from configured source.
        Returns list of local file paths copied to drop folder.
        If source_type is 'local', returns [] (watcher handles it).
        """
        if self.source_type == "local":
            return []  # local watcher handles this
        elif self.source_type == "network":
            return self._fetch_network(since)
        elif self.source_type == "direct_db":
            return self._fetch_direct_db(since)
        else:
            logger.warning(f"Unknown AWR source type: {self.source_type}")
            return []

    # ── Network / UNC path ────────────────────────────────────────
    def _fetch_network(self, since: datetime = None) -> list:
        """
        Copy new AWR HTML files from a UNC/network path.
        Only copies files modified after 'since' timestamp.

        UNC path example: \\\\server\\share\\awr_reports
        The folder is expected to contain subfolders per DB:
          \\\\server\\share\\awr_reports\\COLDBPRD\\*.html
          \\\\server\\share\\awr_reports\\NEODBPRD\\*.html
        Or flat HTML files in the root:
          \\\\server\\share\\awr_reports\\*.html
        """
        network_path = self.cfg.get("awr_network_path", "")
        if not network_path:
            logger.error("AWR network path not configured")
            return []

        fetched = []
        try:
            src_root = Path(network_path)
            if not src_root.exists():
                logger.error(f"AWR network path not accessible: {network_path}")
                return []

            # Collect all HTML files recursively
            for html_file in src_root.rglob("*.html"):
                try:
                    file_mtime = datetime.fromtimestamp(html_file.stat().st_mtime)
                    # Skip if older than last fetch
                    if since and file_mtime <= since:
                        continue

                    # Preserve subfolder structure (DB name) in local drop
                    rel = html_file.relative_to(src_root)
                    dest = Path(self.local_drop) / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)

                    if not dest.exists():
                        shutil.copy2(str(html_file), str(dest))
                        fetched.append(str(dest))
                        _log_fetch("awr_network", network_path,
                                   html_file.name, "ok")
                        logger.info(f"Fetched AWR: {html_file.name}")

                except Exception as e:
                    _log_fetch("awr_network", network_path,
                               html_file.name, "error", str(e))
                    logger.warning(f"Failed to copy {html_file}: {e}")

        except Exception as e:
            logger.error(f"AWR network fetch failed: {e}")

        logger.info(f"AWR network fetch: {len(fetched)} new files from {network_path}")
        return fetched

    # ── Direct Oracle DB (future) ─────────────────────────────────
    def _fetch_direct_db(self, since: datetime = None) -> list:
        """
        Future implementation — generate AWR reports directly from
        Oracle using DBMS_WORKLOAD_REPOSITORY.

        Prerequisites:
          - Oracle Instant Client installed on portal server
          - pip install oracledb
          - Network access to Oracle DB on port 1521
          - User with DBA or EXECUTE on DBMS_WORKLOAD_REPOSITORY

        Planned steps:
          1. Connect to Oracle via oracledb
          2. Query DBA_HIST_SNAPSHOT for snaps since last fetch
          3. Call DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_HTML for each snap pair
          4. Save HTML output to local drop folder
          5. AWR watcher picks up and queues for parsing
        """
        logger.warning(
            "Direct Oracle DB AWR fetch is not yet implemented. "
            "Prerequisites: Oracle Instant Client + oracledb Python package + "
            "network access to Oracle on port 1521 + DBA/EXECUTE privilege. "
            "See Installation Guide Section 9 for setup steps."
        )
        return []


# ══════════════════════════════════════════════════════════════════
# SAR FETCHER
# ══════════════════════════════════════════════════════════════════

class SARFetcher:
    """
    Fetches SAR files from configured source.

    Source types:
      local — watch local folder (handled by sar_watcher directly)
      ssh   — pull SA files from Linux server via SCP/paramiko
    """

    def __init__(self, config: dict = None):
        self.cfg = config or _get_source_config()
        self.source_type  = self.cfg.get("sar_source_type", "local")
        self.local_drop   = self.cfg.get("sar_local_path", "sar_drop")
        if not os.path.isabs(self.local_drop):
            self.local_drop = os.path.join(_PROJECT_ROOT, self.local_drop)
        os.makedirs(self.local_drop, exist_ok=True)

    def fetch_new(self, hostname: str = None, since: datetime = None) -> list:
        """
        Fetch new SAR files from configured source.
        Returns list of local file paths copied to drop folder.
        """
        if self.source_type == "local":
            return []
        elif self.source_type == "ssh":
            return self._fetch_ssh(hostname, since)
        else:
            logger.warning(f"Unknown SAR source type: {self.source_type}")
            return []

    # ── SSH pull from Linux ───────────────────────────────────────
    def _fetch_ssh(self, hostname: str = None, since: datetime = None) -> list:
        """
        Pull SAR binary files from Linux server via SSH/SCP.

        Uses paramiko (pure Python SSH) — no WinSCP required.
        Falls back to WinSCP CLI if paramiko is not installed.

        SAR files on Linux are at: /var/log/sa/sa01 .. sa31
        Files are named by day-of-month (sa01 = 1st, sa31 = 31st).

        Only fetches files modified after 'since' timestamp.
        Saves to: sar_drop\\HOSTNAME\\sa01 (binary)
        """
        host      = hostname or self.cfg.get("sar_ssh_host", "")
        port      = int(self.cfg.get("sar_ssh_port", 22))
        user      = self.cfg.get("sar_ssh_user", "")
        key_path  = self.cfg.get("sar_ssh_key_path", "")
        password  = self.cfg.get("sar_ssh_password", "")
        remote    = self.cfg.get("sar_ssh_remote_path", "/var/log/sa")

        if not host:
            logger.error("SAR SSH host not configured")
            return []

        fetched = []

        # Try paramiko first (preferred)
        try:
            import paramiko
            return self._fetch_ssh_paramiko(
                host, port, user, key_path, password, remote, since, fetched
            )
        except ImportError:
            logger.info("paramiko not installed — trying WinSCP CLI fallback")

        # WinSCP CLI fallback
        try:
            return self._fetch_winscp(
                host, port, user, key_path, password, remote, since
            )
        except Exception as e:
            logger.error(f"SAR SSH fetch failed (both methods): {e}")
            return []

    def _fetch_ssh_paramiko(self, host, port, user, key_path,
                             password, remote, since, fetched) -> list:
        """SSH fetch using paramiko + SFTP."""
        import paramiko

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {"hostname": host, "port": port, "username": user,
                          "timeout": 30}
        if key_path and os.path.exists(key_path):
            connect_kwargs["key_filename"] = key_path
        elif password:
            connect_kwargs["password"] = password
        else:
            logger.error("SAR SSH: no key or password configured")
            return []

        try:
            ssh.connect(**connect_kwargs)
            sftp = ssh.open_sftp()

            # List SA files in remote path
            try:
                remote_files = sftp.listdir_attr(remote)
            except Exception as e:
                logger.error(f"Cannot list {remote} on {host}: {e}")
                return []

            # Local dest folder: sar_drop/HOSTNAME/
            dest_dir = os.path.join(self.local_drop, host)
            os.makedirs(dest_dir, exist_ok=True)

            for attr in remote_files:
                fname = attr.filename
                # Only SA files: sa01..sa31
                if not (fname.startswith("sa") and
                        fname[2:].isdigit() and len(fname) == 4):
                    continue

                file_mtime = datetime.fromtimestamp(attr.st_mtime)
                if since and file_mtime <= since:
                    continue

                remote_path = f"{remote.rstrip('/')}/{fname}"
                local_path  = os.path.join(dest_dir, fname)

                try:
                    sftp.get(remote_path, local_path)
                    fetched.append(local_path)
                    _log_fetch("sar_ssh", f"{host}:{remote}", fname, "ok")
                    logger.info(f"Fetched SAR: {host}/{fname} ({file_mtime})")
                except Exception as e:
                    _log_fetch("sar_ssh", f"{host}:{remote}", fname, "error", str(e))
                    logger.warning(f"Failed to fetch {remote_path}: {e}")

            sftp.close()
        finally:
            ssh.close()

        logger.info(f"SAR SSH fetch from {host}: {len(fetched)} new files")
        return fetched

    def _fetch_winscp(self, host, port, user, key_path,
                      password, remote, since) -> list:
        """
        WinSCP CLI fallback for SSH fetch.
        Requires WinSCP to be installed on the portal server.
        WinSCP CLI path: C:\\Program Files (x86)\\WinSCP\\WinSCP.com
        """
        import subprocess
        import tempfile

        winscp_exe = self.cfg.get(
            "winscp_path",
            r"C:\Program Files (x86)\WinSCP\WinSCP.com"
        )
        if not os.path.exists(winscp_exe):
            winscp_exe = r"C:\Program Files\WinSCP\WinSCP.com"
        if not os.path.exists(winscp_exe):
            raise FileNotFoundError(
                "WinSCP not found. Install from https://winscp.net or "
                "use paramiko: pip install paramiko"
            )

        dest_dir = os.path.join(self.local_drop, host)
        os.makedirs(dest_dir, exist_ok=True)

        # Build WinSCP script
        auth = f"publickey;{key_path}" if key_path else f"{password}"
        script = (
            f'open sftp://{user}:{auth}@{host}:{port}/ -hostkey=*\n'
            f'lcd "{dest_dir}"\n'
            f'cd {remote}\n'
            f'get sa?? \n'
            f'exit\n'
        )

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                         delete=False) as f:
            f.write(script)
            script_path = f.name

        try:
            result = subprocess.run(
                [winscp_exe, "/script=" + script_path],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(f"WinSCP error: {result.stderr[:300]}")

            # Return all sa files in dest_dir
            fetched = [
                os.path.join(dest_dir, f)
                for f in os.listdir(dest_dir)
                if f.startswith("sa") and f[2:].isdigit()
            ]
            logger.info(f"WinSCP SAR fetch from {host}: {len(fetched)} files")
            return fetched

        finally:
            os.unlink(script_path)


# ══════════════════════════════════════════════════════════════════
# DB SCHEMA for remote_fetch_log
# ══════════════════════════════════════════════════════════════════
SCHEMA_SQL = """
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
"""


if __name__ == "__main__":
    # Test connectivity
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["awr_network","sar_ssh"], required=True)
    parser.add_argument("--since", help="ISO datetime e.g. 2026-07-01T00:00:00")
    args = parser.parse_args()

    since_dt = datetime.fromisoformat(args.since) if args.since else None

    if args.type == "awr_network":
        f = AWRFetcher()
        files = f.fetch_new(since=since_dt)
        print(f"Fetched {len(files)} AWR files: {files[:5]}")

    elif args.type == "sar_ssh":
        f = SARFetcher()
        files = f.fetch_new(since=since_dt)
        print(f"Fetched {len(files)} SAR files: {files[:5]}")
