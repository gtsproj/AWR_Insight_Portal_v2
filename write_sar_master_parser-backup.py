# Run from C:\AWR_Insight_Portal_v2\
# py write_sar_master_parser.py

import os

content = """\
# modules/sar/sar_master_parser.py  (v2 - with hugepage + socket parsers)
import os
import re
import sys
import glob
from datetime import date

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from logger_utils import get_logger

logger = get_logger("sar_master_parser")

SAR_DIR     = os.path.join(_PROJECT_ROOT, "sar_reports")
ARCHIVE_DIR = os.path.join(_PROJECT_ROOT, "sar_archive")

sys.path.insert(0, os.path.dirname(__file__))

from sar_cpu_parser       import parse_sar_cpu,       insert_sar_cpu
from sar_memory_parser    import parse_sar_memory,    insert_sar_memory
from sar_swap_parser      import parse_sar_swap,      insert_sar_swap
from sar_disk_parser      import parse_sar_disk,      insert_sar_disk
from sar_network_parser   import parse_sar_network,   insert_sar_network
from sar_paging_parser    import parse_sar_paging,    insert_sar_paging
from sar_ctxswitch_parser import parse_sar_ctxswitch, insert_sar_ctxswitch
from sar_loadavg_parser   import parse_sar_loadavg,   insert_sar_loadavg
from sar_hugepage_parser  import parse_sar_hugepage,  insert_sar_hugepage
from sar_socket_parser    import parse_sar_socket,    insert_sar_socket

_PARSERS = [
    ("CPU",            parse_sar_cpu,       insert_sar_cpu),
    ("Memory",         parse_sar_memory,    insert_sar_memory),
    ("Swap",           parse_sar_swap,      insert_sar_swap),
    ("Disk I/O",       parse_sar_disk,      insert_sar_disk),
    ("Network",        parse_sar_network,   insert_sar_network),
    ("Paging",         parse_sar_paging,    insert_sar_paging),
    ("Context Switch", parse_sar_ctxswitch, insert_sar_ctxswitch),
    ("Load Average",   parse_sar_loadavg,   insert_sar_loadavg),
    ("HugePages",      parse_sar_hugepage,  insert_sar_hugepage),
    ("Sockets",        parse_sar_socket,    insert_sar_socket),
]

# sysstat binary magic bytes
_SAR_BINARY_MAGIC = b"\\x96\\xd5\\x71\\x21"

_DATE_PATTERNS = [
    re.compile(r"\\(([^)]+)\\)\\s+(\\d{2}/\\d{2}/\\d{4})"),
    re.compile(r"\\(([^)]+)\\)\\s+(\\d{4}-\\d{2}-\\d{2})"),
    re.compile(r"\\(([^)]+)\\)\\s+(\\d{2}/\\d{2}/\\d{2})\\\\b"),
]


def _is_binary_sar(filepath):
    try:
        with open(filepath, "rb") as f:
            return f.read(4) == _SAR_BINARY_MAGIC
    except Exception:
        return False


def _wsl_path(windows_path):
    import re as _re
    p = windows_path.replace("\\\\\\\\", "/").replace("\\\\", "/")
    m = _re.match(r"^([A-Za-z]):/(.+)", p)
    if m:
        return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    return p


def _convert_binary_sar(filepath):
    import shutil, subprocess

    filepath = os.path.abspath(filepath)

    def _run(cmd, label):
        try:
            logger.info(f"Attempting {label}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            output = result.stdout.strip()
            if not output:
                logger.warning(f"{label} produced no output. stderr: {result.stderr[:200]}")
                return None
            lines = result.stdout.splitlines(keepends=True)
            logger.info(f"  {label} OK - {len(lines)} lines")
            return lines
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            logger.error(f"{label} timed out")
            return None
        except Exception as e:
            logger.error(f"{label} error: {e}")
            return None

    native_sar = shutil.which("sar")
    if native_sar:
        lines = _run([native_sar, "-A", "-f", filepath], "native sar")
        if lines:
            return lines

    wsl_exe = shutil.which("wsl")
    if wsl_exe:
        wsl_filepath = _wsl_path(filepath)
        check = subprocess.run([wsl_exe, "which", "sar"],
                               capture_output=True, text=True, timeout=10)
        if check.returncode == 0 and check.stdout.strip():
            lines = _run([wsl_exe, "sar", "-A", "-f", wsl_filepath], "WSL sar direct")
            if lines:
                return lines
            tmp = f"/tmp/_sarconv_{os.path.basename(filepath)}"
            cmd = f"sadf -c {wsl_filepath} > {tmp} 2>/dev/null && sar -A -f {tmp}"
            lines = _run([wsl_exe, "bash", "-c", cmd], "WSL sadf -c + sar")
            if lines:
                return lines

    logger.error(
        f"Cannot convert binary SAR file: {os.path.basename(filepath)}\\n"
        f"Option A: Install WSL + sysstat (sudo apt-get install sysstat)\\n"
        f"Option B: On Linux server: sar -A -f <file> > output.txt"
    )
    return None


def extract_hostname_and_date(lines):
    hostname, sar_date = "UNKNOWN_HOST", None
    for line in lines[:10]:
        for pat in _DATE_PATTERNS:
            m = pat.search(line)
            if m:
                hostname = m.group(1).strip()
                raw = m.group(2).strip()
                if re.match(r"\\d{4}-\\d{2}-\\d{2}", raw):
                    p = raw.split("-")
                    sar_date = f"{p[1]}/{p[2]}/{p[0]}"
                elif re.match(r"\\d{2}/\\d{2}/\\d{2}$", raw):
                    parts = raw.split("/")
                    sar_date = f"{parts[0]}/{parts[1]}/20{parts[2]}"
                else:
                    sar_date = raw
                break
        if sar_date:
            break
    if not sar_date:
        sar_date = date.today().strftime("%m/%d/%Y")
        logger.warning(f"SAR date not found in header - defaulting to today: {sar_date}")
    return hostname, sar_date


def process_sar_file(filepath, archive=False, hostname_override=None):
    filepath = os.path.abspath(filepath)
    if not os.path.exists(filepath):
        logger.error(f"SAR file not found: {filepath}")
        return False

    fname = os.path.basename(filepath)
    logger.info("=" * 60)
    logger.info(f"Processing SAR: {fname}")

    if _is_binary_sar(filepath):
        lines = _convert_binary_sar(filepath)
        if lines is None:
            return False
        logger.info("Binary SAR converted to text successfully")
    else:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

    hostname, sar_date = extract_hostname_and_date(lines)

    if hostname_override and hostname_override.strip():
        logger.info(f"   Host    : {hostname_override.strip()} (override - header had: {hostname})")
        hostname = hostname_override.strip()
    else:
        logger.info(f"   Host    : {hostname}")
    logger.info(f"   SAR date: {sar_date}")

    failures = []
    for label, parse_fn, insert_fn in _PARSERS:
        try:
            records = parse_fn(lines, hostname, sar_date)
            insert_fn(records)
            logger.info(f"  OK {label:<18} - {len(records)} rows")
        except Exception as e:
            logger.error(f"  FAIL {label:<18} - {e}", exc_info=True)
            failures.append(label)

    success = len(failures) == 0
    logger.info(f"SAR complete - {'OK' if success else f'Failures: {failures}'}")
    return True


def _archive_sar_file(filepath, hostname):
    dest_dir = os.path.join(ARCHIVE_DIR, hostname)
    os.makedirs(dest_dir, exist_ok=True)
    try:
        import shutil
        base, ext = os.path.splitext(os.path.basename(filepath))
        from datetime import datetime
        dest = os.path.join(dest_dir, f"{base}_{datetime.now():%Y%m%d%H%M%S}{ext}")
        shutil.move(filepath, dest)
        logger.info(f"Archived to {dest}")
    except Exception as e:
        logger.warning(f"Could not archive {filepath}: {e}")


def scan_and_process(scan_dir=None):
    scan_dir = scan_dir or SAR_DIR
    if not os.path.isdir(scan_dir):
        logger.warning(f"SAR directory not found: {scan_dir}")
        return
    txt_files = glob.glob(os.path.join(scan_dir, "**", "*.txt"), recursive=True)
    if not txt_files:
        logger.warning(f"No SAR text files found in {scan_dir}")
        return
    for f in txt_files:
        process_sar_file(f, archive=True)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--file", default=None)
    p.add_argument("--scan", default=None)
    p.add_argument("--host", default=None)
    args = p.parse_args()
    if args.file:
        process_sar_file(args.file, hostname_override=args.host)
    else:
        scan_and_process(args.scan)
"""

dest = os.path.join("modules", "sar", "sar_master_parser.py")
with open(dest, "w", encoding="utf-8") as f:
    f.write(content)

written = open(dest, encoding="utf-8").read()
ok1 = "parse_sar_hugepage" in written
ok2 = "parse_sar_socket" in written
ok3 = "HugePages" in written

print(f"Written: {os.path.abspath(dest)}")
print(f"Size: {len(written)} bytes")
print(f"Has hugepage parser: {ok1}")
print(f"Has socket parser:   {ok2}")
print(f"Has HugePages entry: {ok3}")
print()
if ok1 and ok2 and ok3:
    print("SUCCESS - sar_master_parser.py is correct")
else:
    print("ERROR - something missing")
