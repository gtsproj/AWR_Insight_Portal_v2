# modules/sar/sar_master_parser.py
import os, re, sys, glob
from datetime import date

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'common'))

from logger_utils import get_logger
logger = get_logger('sar_master_parser')

SAR_DIR     = os.path.join(_PROJECT_ROOT, 'sar_reports')
ARCHIVE_DIR = os.path.join(_PROJECT_ROOT, 'sar_archive')

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
    ('CPU',            parse_sar_cpu,       insert_sar_cpu),
    ('Memory',         parse_sar_memory,    insert_sar_memory),
    ('Swap',           parse_sar_swap,      insert_sar_swap),
    ('Disk I/O',       parse_sar_disk,      insert_sar_disk),
    ('Network',        parse_sar_network,   insert_sar_network),
    ('Paging',         parse_sar_paging,    insert_sar_paging),
    ('Context Switch', parse_sar_ctxswitch, insert_sar_ctxswitch),
    ('Load Average',   parse_sar_loadavg,   insert_sar_loadavg),
    ('HugePages',      parse_sar_hugepage,  insert_sar_hugepage),
    ('Sockets',        parse_sar_socket,    insert_sar_socket),
]

_SAR_BINARY_MAGIC = b'\x96\xd5\x71\x21'

_DATE_PATTERNS = [
    re.compile(r'\(([^)]+)\)\s+(\d{2}/\d{2}/\d{4})'),
    re.compile(r'\(([^)]+)\)\s+(\d{4}-\d{2}-\d{2})'),
    re.compile(r'\(([^)]+)\)\s+(\d{2}/\d{2}/\d{2})\b'),
]


def _is_binary_sar(filepath):
    try:
        with open(filepath, 'rb') as f:
            return f.read(4) == _SAR_BINARY_MAGIC
    except Exception:
        return False


def _wsl_path(windows_path):
    p = windows_path.replace('\\', '/').replace('\\', '/')
    import re as _re
    m = _re.match(r'^([A-Za-z]):/(.+)', p)
    if m:
        return '/mnt/{}/{}'.format(m.group(1).lower(), m.group(2))
    return p


def _convert_binary_sar(filepath):
    import shutil, subprocess
    filepath = os.path.abspath(filepath)

    def _run(cmd, label):
        try:
            logger.info('Attempting {}'.format(label))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            output = result.stdout.strip()
            if not output:
                logger.warning('{} produced no output. stderr: {}'.format(label, result.stderr[:200]))
                return None
            lines = result.stdout.splitlines(keepends=True)
            logger.info('{} OK - {} lines'.format(label, len(lines)))
            return lines
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            logger.error('{} timed out'.format(label))
            return None
        except Exception as e:
            logger.error('{} error: {}'.format(label, e))
            return None

    native_sar = shutil.which('sar')
    if native_sar:
        lines = _run([native_sar, '-A', '-f', filepath], 'native sar')
        if lines:
            return lines

    wsl_exe = shutil.which('wsl')
    if wsl_exe:
        wsl_filepath = _wsl_path(filepath)
        check = subprocess.run([wsl_exe, 'which', 'sar'],
                               capture_output=True, text=True, timeout=10)
        if check.returncode == 0 and check.stdout.strip():
            lines = _run([wsl_exe, 'sar', '-A', '-f', wsl_filepath], 'WSL sar direct')
            if lines:
                return lines
            tmp = '/tmp/_sarconv_{}'.format(os.path.basename(filepath))
            cmd = 'TZ=Asia/Kolkata sadf -c {} > {} 2>/dev/null && TZ=Asia/Kolkata sar -A -f {}'.format(
                wsl_filepath, tmp, tmp)
            lines = _run([wsl_exe, 'bash', '-c', cmd], 'WSL sadf+sar UTC')
            if lines:
                return lines

    logger.error(
        'Cannot convert binary SAR file: {}\n'
        'Option A: Install WSL + sysstat (sudo apt-get install sysstat)\n'
        'Option B: On Linux server: sar -A -f <file> > output.txt'.format(
            os.path.basename(filepath)))
    return None


def extract_hostname_and_date(lines):
    hostname, sar_date = 'UNKNOWN_HOST', None
    for line in lines[:10]:
        for pat in _DATE_PATTERNS:
            m = pat.search(line)
            if m:
                hostname = m.group(1).strip()
                raw = m.group(2).strip()
                if re.match(r'\d{4}-\d{2}-\d{2}', raw):
                    p = raw.split('-')
                    sar_date = '{}/{}/{}'.format(p[1], p[2], p[0])
                elif re.match(r'\d{2}/\d{2}/\d{2}$', raw):
                    parts = raw.split('/')
                    sar_date = '{}/{}/20{}'.format(parts[0], parts[1], parts[2])
                else:
                    sar_date = raw
                break
        if sar_date:
            break
    if not sar_date:
        sar_date = date.today().strftime('%m/%d/%Y')
        logger.warning('SAR date not found - defaulting to today: {}'.format(sar_date))
    return hostname, sar_date


def _run_sar_anomaly_detection(hostname: str, lines: list) -> None:
    """
    Auto-trigger SAR anomaly detection after a SAR file is fully parsed.
    Resolves snap_time range from the parsed lines, then calls detect_sar().
    Best-effort — failure does not abort the SAR parse.
    """
    import importlib.util
    det_path = os.path.join(_PROJECT_ROOT, 'anomaly_detector.py')
    if not os.path.exists(det_path):
        logger.warning('anomaly_detector.py not found — skipping SAR anomaly detection')
        return

    # Resolve time range from SAR lines (look for HH:MM:SS timestamps)
    snap_time_from, snap_time_to = _resolve_sar_time_range(hostname, lines)
    if snap_time_from is None:
        logger.warning('Could not resolve SAR time range — skipping anomaly detection')
        return

    try:
        spec   = importlib.util.spec_from_file_location('anomaly_detector', det_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, 'detect_sar'):
            module.detect_sar(hostname=hostname,
                              snap_time_from=snap_time_from,
                              snap_time_to=snap_time_to,
                              store=True)
            logger.info(f'🔍 SAR anomaly detection complete for {hostname}')
        else:
            logger.warning('anomaly_detector has no detect_sar() — skipping')

    except Exception as e:
        logger.error(f'SAR anomaly detection failed for {hostname}: {e}', exc_info=True)


def _resolve_sar_time_range(hostname: str, lines: list):
    """
    Resolve min/max snap_time for the just-parsed SAR file.

    Strategy 1: Parse timestamps directly from SAR text lines (most reliable,
                 works for both current and historical data).
    Strategy 2: Query sar_cpu_stats for most recent insert for this hostname
                 using created_at (works when file just parsed).
    Strategy 3: Query sar_cpu_stats for the latest day's data for this hostname.
    """
    from datetime import datetime, timedelta

    # ── Strategy 1: Parse from SAR text lines ────────────────────────
    time_re = re.compile(r'^(\d{2}:\d{2}:\d{2})\s*(AM|PM)?', re.IGNORECASE)
    _, sar_date = extract_hostname_and_date(lines)
    times = []
    if sar_date:
        for line in lines:
            m = time_re.match(line.strip())
            if m:
                try:
                    t_str = m.group(1)
                    ampm  = (m.group(2) or '').strip()
                    fmt   = '%m/%d/%Y %I:%M:%S %p' if ampm else '%m/%d/%Y %H:%M:%S'
                    dt    = datetime.strptime(
                        f'{sar_date} {t_str} {ampm}'.strip(), fmt
                    )
                    times.append(dt)
                except Exception:
                    continue

    if times:
        t_from = min(times)
        t_to   = max(times)
        logger.debug(f'SAR time range from lines: {t_from} → {t_to}')
        return t_from, t_to

    # ── Strategy 2: DB — most recently inserted rows ──────────────────
    try:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'common'))
        from db import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Use created_at window — works for files parsed right now
            cur.execute("""
                SELECT MIN(snap_time), MAX(snap_time)
                FROM sar_cpu_stats
                WHERE hostname = %s
                  AND created_at >= NOW() - INTERVAL '30 minutes'
            """, (hostname,))
            row = cur.fetchone()
            if row and row[0] is not None:
                conn.close()
                logger.debug(f'SAR time range from DB (recent): {row[0]} → {row[1]}')
                return row[0], row[1]

            # ── Strategy 3: latest day for this hostname ──────────────
            cur.execute("""
                SELECT MIN(snap_time), MAX(snap_time)
                FROM sar_cpu_stats
                WHERE hostname = %s
                  AND snap_time::date = (
                      SELECT MAX(snap_time::date)
                      FROM sar_cpu_stats
                      WHERE hostname = %s
                  )
            """, (hostname, hostname))
            row = cur.fetchone()
            if row and row[0] is not None:
                conn.close()
                logger.debug(f'SAR time range from DB (latest day): {row[0]} → {row[1]}')
                return row[0], row[1]
        conn.close()
    except Exception as e:
        logger.debug(f'DB time range lookup failed: {e}')

    logger.warning(f'Could not resolve SAR time range for {hostname}')
    return None, None


def _shift_sar_lines_to_ist(lines: list) -> list:
    """
    Shift all time values in SAR text output by +5:30 (UTC → IST).
    Handles midnight rollovers — updates the date header when time crosses midnight.
    """
    from datetime import datetime, timedelta
    IST_OFFSET = timedelta(hours=5, minutes=30)

    # Extract the SAR date from header line
    _, sar_date_str = extract_hostname_and_date(lines)
    if not sar_date_str:
        return lines  # Can't shift without a date

    # Parse base date
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            base_date = datetime.strptime(sar_date_str, fmt).date()
            break
        except ValueError:
            continue
    else:
        return lines  # Unrecognised date format

    time_re = re.compile(
        r'^(\d{2}:\d{2}:\d{2})(\s+(AM|PM))?(\s+.*)?$',
        re.IGNORECASE
    )

    shifted = []
    current_date = base_date

    for line in lines:
        m = time_re.match(line.rstrip('\r\n'))
        if not m:
            shifted.append(line)
            continue

        t_str = m.group(1)
        ampm  = (m.group(3) or '').strip()
        rest  = m.group(4) or ''

        try:
            if ampm:
                dt = datetime.strptime(f'{current_date} {t_str} {ampm}', '%Y-%m-%d %I:%M:%S %p')
            else:
                dt = datetime.strptime(f'{current_date} {t_str}', '%Y-%m-%d %H:%M:%S')
        except ValueError:
            shifted.append(line)
            continue

        dt_ist = dt + IST_OFFSET

        # Handle date rollover — if shifted time crosses midnight, date changes
        if dt_ist.date() != current_date:
            current_date = dt_ist.date()

        new_time = dt_ist.strftime('%H:%M:%S')
        new_line = line.replace(t_str, new_time, 1)
        if ampm:
            # Remove AM/PM since we've converted to 24h
            new_line = new_line.replace(f' {ampm}', '', 1)
        shifted.append(new_line)

    return shifted


def process_sar_file(filepath, archive=False, hostname_override=None):
    filepath = os.path.abspath(filepath)
    if not os.path.exists(filepath):
        # Try archive location — watcher archives before queuing
        basename = os.path.basename(filepath)
        hostname = (hostname_override or '').upper() or 'UNKNOWN'
        # Build candidate archive paths
        candidates = []
        # sar_archive/<hostname>/<filename>
        archive_dir = os.path.join(_PROJECT_ROOT, 'sar_archive', hostname, basename)
        candidates.append(archive_dir)
        # Also search all hostname subdirs in sar_archive
        sar_archive_root = os.path.join(_PROJECT_ROOT, 'sar_archive')
        if os.path.isdir(sar_archive_root):
            for hdir in os.listdir(sar_archive_root):
                candidate = os.path.join(sar_archive_root, hdir, basename)
                if candidate not in candidates:
                    candidates.append(candidate)
        found = None
        for c in candidates:
            if os.path.exists(c):
                found = c
                break
        if found:
            logger.info(f'File found in archive: {found}')
            filepath = found
        else:
            logger.error(f'SAR file not found: {filepath} (also checked archive locations)')
            return False

    fname = os.path.basename(filepath)
    logger.info('=' * 60)
    logger.info('Processing SAR: {}'.format(fname))

    if _is_binary_sar(filepath):
        lines = _convert_binary_sar(filepath)
        if lines is None:
            return False
        logger.info('Binary SAR converted to text successfully')
    else:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        # Text SAR files have times in server local timezone (usually UTC on Linux).
        # Shift all timestamps to IST (+5:30) so data aligns with AWR (stored IST).
        lines = _shift_sar_lines_to_ist(lines)
        logger.info('Text SAR: timestamps shifted to IST (+5:30)')

    hostname, sar_date = extract_hostname_and_date(lines)

    if hostname_override and hostname_override.strip():
        logger.info('   Host    : {} (override - header had: {})'.format(
            hostname_override.strip(), hostname))
        hostname = hostname_override.strip()
    else:
        logger.info('   Host    : {}'.format(hostname))
    logger.info('   SAR date: {}'.format(sar_date))

    failures = []
    for label, parse_fn, insert_fn in _PARSERS:
        try:
            records = parse_fn(lines, hostname, sar_date)
            insert_fn(records)
            logger.info('  OK {:<18} - {} rows'.format(label, len(records)))
        except Exception as e:
            logger.error('  FAIL {:<18} - {}'.format(label, e), exc_info=True)
            failures.append(label)

    success = len(failures) == 0
    logger.info('SAR complete - {}'.format('OK' if success else 'Failures: {}'.format(failures)))

    # ── Auto SAR anomaly detection ────────────────────────────────────
    logger.info(f'🔍 Running SAR anomaly detection for {hostname} …')
    _run_sar_anomaly_detection(hostname, lines)

    return True


def scan_and_process(scan_dir=None):
    scan_dir = scan_dir or SAR_DIR
    if not os.path.isdir(scan_dir):
        logger.warning('SAR directory not found: {}'.format(scan_dir))
        return
    txt_files = glob.glob(os.path.join(scan_dir, '**', '*.txt'), recursive=True)
    if not txt_files:
        logger.warning('No SAR text files found in {}'.format(scan_dir))
        return
    for f in txt_files:
        process_sar_file(f, archive=True)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--file',      default=None)
    p.add_argument('--scan',      default=None)
    p.add_argument('--host',      default=None)
    p.add_argument('--tz-offset', default=None,
                   help='Hours to add to snap_time (e.g. 5.5 for IST). '
                        'Use when SAR file has UTC times but server is IST. '
                        'Default: no adjustment (times stored as-is)')
    args = p.parse_args()

    # Set timezone offset for sub-parsers via environment variable
    # Sub-parsers read SAR_TZ_OFFSET_HOURS to shift snap_times
    if args.tz_offset:
        os.environ['SAR_TZ_OFFSET_HOURS'] = str(args.tz_offset)
        logger.info(f'Timezone offset: +{args.tz_offset}h will be applied to snap_times')

    if args.file:
        process_sar_file(args.file, hostname_override=args.host)
    else:
        scan_and_process(args.scan)
