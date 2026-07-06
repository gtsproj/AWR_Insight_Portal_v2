# clean_sar_queue.py
# ─────────────────────────────────────────────────────────────────────
# Removes duplicate SAR queue entries caused by the watcher re-queuing
# binary files before they were archived.
# Keeps only the FIRST (oldest) occurrence of each file stem.
# Resets FAILED items to PENDING.
#
# USAGE:
#   py clean_sar_queue.py               # clean all SAR queues
#   py clean_sar_queue.py HOSTNAME      # clean specific hostname

import os, sys, json, glob

_PROJECT_ROOT  = os.path.abspath(os.path.dirname(__file__))
SAR_QUEUES_DIR = os.path.join(_PROJECT_ROOT, "sar_queues")


def clean_queue(hostname: str):
    qfile = os.path.join(SAR_QUEUES_DIR, f"queue_{hostname}.json")
    if not os.path.exists(qfile):
        print(f"  {hostname}: no queue file found")
        return

    with open(qfile, "r", encoding="utf-8") as f:
        items = json.load(f)

    seen_stems = {}   # stem → index of first occurrence
    keep       = []
    removed    = 0

    for item in items:
        filepath = item.get("filepath", "")
        basename = os.path.basename(filepath)
        stem     = os.path.splitext(basename)[0]

        if stem in seen_stems:
            # Duplicate — keep the one with better status
            existing = keep[seen_stems[stem]]
            status_priority = {"DONE": 0, "PROCESSING": 1, "PENDING": 2, "FAILED": 3}
            existing_priority = status_priority.get(existing.get("status","FAILED"), 3)
            this_priority     = status_priority.get(item.get("status","FAILED"), 3)

            if this_priority < existing_priority:
                # This one is better — replace the existing
                keep[seen_stems[stem]] = item
            removed += 1
        else:
            seen_stems[stem] = len(keep)
            keep.append(item)

    # Reset FAILED → PENDING for kept items
    reset = 0
    for item in keep:
        if item.get("status") == "FAILED":
            item["status"]      = "PENDING"
            item["retry_count"] = 0
            item["error"]       = None
            reset += 1

    tmp = qfile + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=2, default=str)
    os.replace(tmp, qfile)

    print(f"  {hostname}: {len(items)} items → {len(keep)} kept "
          f"({removed} duplicates removed, {reset} reset to PENDING)")


def main():
    host_filter = sys.argv[1].upper() if len(sys.argv) > 1 else None

    if not os.path.isdir(SAR_QUEUES_DIR):
        print(f"SAR queues directory not found: {SAR_QUEUES_DIR}")
        return

    queue_files = glob.glob(os.path.join(SAR_QUEUES_DIR, "queue_*.json"))
    if not queue_files:
        print("No SAR queue files found")
        return

    print(f"Cleaning duplicate SAR queue entries...\n")
    for qf in sorted(queue_files):
        hostname = os.path.basename(qf)[len("queue_"):-len(".json")]
        if host_filter and hostname != host_filter:
            continue
        clean_queue(hostname)

    print("\nDone. Queue processor will pick up PENDING items on next cycle.")


if __name__ == "__main__":
    main()
