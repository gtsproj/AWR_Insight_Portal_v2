# central_repo_scanner.py
# ============================================================
# Central Repository Scanner — Deliverable #6
#
# Walks a shared network path and enqueues AWR reports for
# all databases, handling:
#   - Standalone non-CDB databases
#   - RAC (multiple instances per DB, each in inst<N>/ sub-folder)
#   - CDB with Pluggable Databases (PDB sub-folders)
#   - Oracle 19c and 21c AWR HTML formats
#   - Mixed environments (CDB + non-CDB in same repo)
#
# FOLDER LAYOUTS SUPPORTED:
#
#   Standard:
#     <repo_root>/<DBNAME>/awr_<DBNAME>_<inst>_*.html
#
#   RAC:
#     <repo_root>/<DBNAME>/inst1/awr_<DBNAME>_1_*.html
#     <repo_root>/<DBNAME>/inst2/awr_<DBNAME>_2_*.html
#
#   CDB + PDB:
#     <repo_root>/<CDB_NAME>/<PDB_NAME>/awr_<CDB>_*.html
#
#   Mixed (auto-detected via AWR HTML header):
#     <repo_root>/<DBNAME>/   -- parser reads CDB=YES/NO from header
#
# USAGE:
#   python central_repo_scanner.py                # one-shot scan
#   python central_repo_scanner.py --daemon       # continuous polling
#   python central_repo_scanner.py --db COLDBPRD  # specific DB only
# ============================================================

import os
import re
import sys
import time
import hashlib
import argparse
import signal
from pathlib import Path

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from config_loader import load_config
from logger_utils  import get_logger

logger = get_logger("central_repo_scanner")

# ── config ──────────────────────────────────────────────────────────
_cfg      = load_config()
_src_cfg  = _cfg.get("source", {})

REPO_PATH     = _src_cfg.get("central_repo_path", "/mnt/shared/oracle_awrs")
SCAN_INTERVAL = int(_src_cfg.get("repo_scan_interval_seconds", 300))
RECURSIVE     = bool(_src_cfg.get("repo_recursive", True))
DB_FILTER     = [d.upper() for d in _src_cfg.get("repo_db_filter", [])]
AWR_EXTS      = {".html", ".htm", ".txt"}

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    logger.info(f"Signal {sig} — shutting down")
    _shutdown = True

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── file hash ────────────────────────────────────────────────────────
def _file_hash(filepath: str) -> str:
    h = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            h.update(f.read(65536))
    except OSError:
        pass
    return h.hexdigest()


# ── AWR header parsing ───────────────────────────────────────────────
# Reads only the first 4 KB of the HTML to extract metadata fast.
_TITLE_RE  = re.compile(
    r"DB:\s*([A-Za-z0-9$#_]+).*?Inst:\s*([A-Za-z0-9$#_]+)", re.IGNORECASE
)
_CDB_RE    = re.compile(r"<td[^>]*>\s*(YES|NO)\s*</td>", re.IGNORECASE)
_PDB_RE    = re.compile(r"CDB Root|PDB Name|Con Name", re.IGNORECASE)
_INSTNUM_RE = re.compile(r"Inst\s+Num[^<]*<[^>]+>\s*(\d+)", re.IGNORECASE)


def read_awr_header(filepath: str) -> dict:
    """
    Extract key metadata from an AWR HTML header without full parsing.

    Returns dict with keys:
      db_name, instance, instnum, is_cdb, is_rac, oracle_version, pdb_name
    """
    meta = {
        "db_name":        None,
        "instance":       None,
        "instnum":        1,
        "is_cdb":         False,
        "is_rac":         False,
        "oracle_version": None,
        "pdb_name":       None,
    }

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)   # first 8 KB is enough for header tables
    except OSError as e:
        logger.warning(f"Cannot read {filepath}: {e}")
        return meta

    # DB name and instance from <title>
    m = re.search(r"DB:\s*([A-Za-z0-9$#_]+).*?Inst:\s*([A-Za-z0-9$#_]+)",
                  head, re.IGNORECASE)
    if m:
        meta["db_name"] = m.group(1).upper()
        meta["instance"] = m.group(2).upper()

    # RAC: check CDB column in db_info table (second column = RAC flag)
    # Also detect from RAC=YES in the header table
    if re.search(r"\bYES\b", head[:2000]):
        # Check context — is it CDB=YES or RAC=YES?
        rac_m = re.search(r"RAC.*?(YES|NO)", head, re.IGNORECASE | re.DOTALL)
        cdb_m = re.search(r"CDB.*?(YES|NO)", head, re.IGNORECASE | re.DOTALL)
        meta["is_rac"] = bool(rac_m and rac_m.group(1).upper() == "YES")
        meta["is_cdb"] = bool(cdb_m and cdb_m.group(1).upper() == "YES")

    # Oracle version from Release column
    ver_m = re.search(r"(\d+\.\d+\.\d+\.\d+\.\d+)", head)
    if ver_m:
        meta["oracle_version"] = ver_m.group(1)

    # Instance number
    inum_m = re.search(r"Inst\s+Num[^<]*<[^>]*>\s*(\d+)", head, re.IGNORECASE)
    if inum_m:
        meta["instnum"] = int(inum_m.group(1))

    # PDB name (19c+ CDB AWR reports include Con Name)
    pdb_m = re.search(r"Con\s+Name[^<]*<[^>]*>\s*([A-Za-z0-9$#_]+)", head, re.IGNORECASE)
    if pdb_m and pdb_m.group(1).upper() not in ("CDB$ROOT", ""):
        meta["pdb_name"] = pdb_m.group(1).upper()

    return meta


# ── folder classification ────────────────────────────────────────────
def _classify_db_folder(db_folder: str) -> str:
    """
    Inspect a DB folder and return its layout type:
      'flat'   — AWR files directly in db_folder/
      'rac'    — AWR files in db_folder/inst<N>/ sub-folders
      'cdb'    — AWR files in db_folder/<PDBNAME>/ sub-folders
      'mixed'  — combination (detect from file headers)
    """
    if not os.path.isdir(db_folder):
        return "flat"

    entries   = os.listdir(db_folder)
    sub_dirs  = [e for e in entries if os.path.isdir(os.path.join(db_folder, e))]
    awr_files = [e for e in entries
                 if Path(e).suffix.lower() in AWR_EXTS]

    if awr_files:
        return "flat"

    if sub_dirs:
        # Check if sub-dirs look like RAC instances (inst1, inst2, 1, 2 etc.)
        rac_pattern = re.compile(r"^inst\d+$|\^\d+$", re.IGNORECASE)
        if all(rac_pattern.match(d) for d in sub_dirs):
            return "rac"
        return "cdb"   # assume PDB layout

    return "flat"


# ── enqueue wrapper ───────────────────────────────────────────────────
def _enqueue(filepath: str, db_name: str, meta: dict) -> bool:
    """
    Import enqueue_file from awr_watcher and enqueue a file.
    Isolated here so import errors don't crash the whole scanner.
    """
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        from awr_watcher import enqueue_file
        return enqueue_file(filepath)
    except Exception as e:
        logger.error(f"Enqueue failed for {filepath}: {e}")
        return False


# ── scan a single DB folder ───────────────────────────────────────────
def scan_db_folder(db_folder: str, db_name: str) -> int:
    """
    Scan one DB folder (all layout types) and enqueue new AWR files.
    Returns count of newly enqueued files.
    """
    if DB_FILTER and db_name.upper() not in DB_FILTER:
        return 0

    layout = _classify_db_folder(db_folder)
    logger.debug(f"[{db_name}] Folder layout: {layout}")

    enqueued = 0

    if layout == "flat":
        # Files directly in db_folder
        for fname in os.listdir(db_folder):
            if Path(fname).suffix.lower() not in AWR_EXTS:
                continue
            fpath = os.path.join(db_folder, fname)
            meta  = read_awr_header(fpath)
            if _enqueue(fpath, db_name, meta):
                enqueued += 1
                logger.info(f"[{db_name}] Enqueued: {fname}")

    elif layout == "rac":
        # Sub-folders are RAC instance folders (inst1, inst2, ...)
        for inst_dir in sorted(os.listdir(db_folder)):
            inst_path = os.path.join(db_folder, inst_dir)
            if not os.path.isdir(inst_path):
                continue
            for fname in os.listdir(inst_path):
                if Path(fname).suffix.lower() not in AWR_EXTS:
                    continue
                fpath = os.path.join(inst_path, fname)
                meta  = read_awr_header(fpath)
                meta["is_rac"] = True
                if _enqueue(fpath, db_name, meta):
                    enqueued += 1
                    logger.info(f"[{db_name}/{inst_dir}] Enqueued RAC: {fname}")

    elif layout == "cdb":
        # Sub-folders are PDB names
        for pdb_dir in sorted(os.listdir(db_folder)):
            pdb_path = os.path.join(db_folder, pdb_dir)
            if not os.path.isdir(pdb_path):
                continue
            for fname in os.listdir(pdb_path):
                if Path(fname).suffix.lower() not in AWR_EXTS:
                    continue
                fpath = os.path.join(pdb_path, fname)
                meta  = read_awr_header(fpath)
                meta["is_cdb"]   = True
                meta["pdb_name"] = meta.get("pdb_name") or pdb_dir.upper()
                if _enqueue(fpath, db_name, meta):
                    enqueued += 1
                    logger.info(f"[{db_name}/{pdb_dir}] Enqueued CDB/PDB: {fname}")

    return enqueued


# ── full repo scan ────────────────────────────────────────────────────
def scan_repository(repo_path: str = None) -> int:
    """
    Walk the central repository root and enqueue all new AWR files.
    Returns total count of newly enqueued files.
    """
    root = repo_path or REPO_PATH

    if not os.path.isdir(root):
        logger.error(f"Central repo path not found or not accessible: {root}")
        return 0

    logger.info(f"Scanning central repository: {root}")
    total_enqueued = 0

    # Top-level entries are DB name folders
    for entry in sorted(os.listdir(root)):
        if _shutdown:
            break
        db_folder = os.path.join(root, entry)
        if not os.path.isdir(db_folder):
            continue
        db_name = entry.upper()
        n = scan_db_folder(db_folder, db_name)
        if n:
            logger.info(f"[{db_name}] {n} file(s) enqueued from central repo")
        total_enqueued += n

    logger.info(f"Repository scan complete — {total_enqueued} file(s) enqueued")
    return total_enqueued


# ── main ─────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Central Repository Scanner")
    p.add_argument("--daemon", action="store_true",
                   help="Run continuously, re-scanning every repo_scan_interval_seconds")
    p.add_argument("--repo",   default=None,
                   help="Override central_repo_path from settings.yaml")
    p.add_argument("--db",     default=None,
                   help="Scan only this DB name")
    args = p.parse_args()

    if args.db:
        DB_FILTER.clear()
        DB_FILTER.append(args.db.upper())

    repo = args.repo or REPO_PATH

    logger.info("=" * 60)
    logger.info(f"Central Repo Scanner starting")
    logger.info(f"Repo path     : {repo}")
    logger.info(f"Scan interval : {SCAN_INTERVAL}s")
    logger.info(f"DB filter     : {DB_FILTER or 'all'}")

    if args.daemon:
        logger.info("Daemon mode — press Ctrl+C to stop")
        while not _shutdown:
            scan_repository(repo)
            for _ in range(SCAN_INTERVAL):
                if _shutdown:
                    break
                time.sleep(1)
        logger.info("Central repo scanner shut down")
    else:
        scan_repository(repo)


if __name__ == "__main__":
    main()
