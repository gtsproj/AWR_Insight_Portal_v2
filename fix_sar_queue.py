# fix_sar_queue.py
# ─────────────────────────────────────────────────────────────────────
# Fixes SAR queue items where the filepath points to a file that no
# longer exists (because it was archived before the path was stored).
# Updates the filepath to the actual archive location and resets to PENDING.
#
# USAGE:
#   py fix_sar_queue.py               # fix all SAR queues
#   py fix_sar_queue.py ABCPRDDB01    # fix specific hostname queue

import os
import sys
import json
import glob

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
SAR_QUEUES_DIR  = os.path.join(_PROJECT_ROOT, "sar_queues")
SAR_ARCHIVE_DIR = os.path.join(_PROJECT_ROOT, "sar_archive")


def fix_queue(hostname: str):
    qfile = os.path.join(SAR_QUEUES_DIR, f"queue_{hostname}.json")
    if not os.path.exists(qfile):
        print(f"  {hostname}: no queue file found")
        return

    with open(qfile, "r", encoding="utf-8") as f:
        items = json.load(f)

    changed = 0
    for item in items:
        filepath = item.get("filepath", "")
        if not filepath:
            continue

        # Skip if file exists at stored path — no fix needed
        if os.path.exists(filepath):
            continue

        basename = os.path.basename(filepath)

        # Search for the file in sar_archive
        found = None
        # First check the expected archive location for this hostname
        candidate = os.path.join(SAR_ARCHIVE_DIR, hostname, basename)
        if os.path.exists(candidate):
            found = candidate
        else:
            # Search all subdirs in sar_archive
            for root, dirs, files in os.walk(SAR_ARCHIVE_DIR):
                if basename in files:
                    found = os.path.join(root, basename)
                    break

        if found:
            print(f"  {hostname}: {basename}")
            print(f"    OLD: {filepath}")
            print(f"    NEW: {found}")
            item["filepath"] = found
            if item.get("status") in ("FAILED", "PROCESSING"):
                item["status"]      = "PENDING"
                item["retry_count"] = 0
                item["error"]       = None
                print(f"    STATUS: reset to PENDING")
            changed += 1
        else:
            print(f"  {hostname}: {basename} — NOT FOUND in archive (may need to re-drop file)")
            # Reset to PENDING so it will be retried (parser will log proper error)
            if item.get("status") == "FAILED":
                item["status"]      = "PENDING"
                item["retry_count"] = 0
                item["error"]       = None

    if changed:
        tmp = qfile + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, default=str)
        os.replace(tmp, qfile)
        print(f"  {hostname}: {changed} item(s) fixed and reset to PENDING")
    else:
        print(f"  {hostname}: no fixes needed")


def main():
    host_filter = sys.argv[1].upper() if len(sys.argv) > 1 else None

    if not os.path.isdir(SAR_QUEUES_DIR):
        print(f"SAR queues directory not found: {SAR_QUEUES_DIR}")
        return

    queue_files = glob.glob(os.path.join(SAR_QUEUES_DIR, "queue_*.json"))
    if not queue_files:
        print("No SAR queue files found")
        return

    print(f"Fixing SAR queue filepath issues...\n")
    for qf in sorted(queue_files):
        hostname = os.path.basename(qf)[len("queue_"):-len(".json")]
        if host_filter and hostname != host_filter:
            continue
        fix_queue(hostname)

    print("\nDone. Queue processor will pick up PENDING items on next cycle.")


if __name__ == "__main__":
    main()
