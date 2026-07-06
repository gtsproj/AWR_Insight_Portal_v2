# reset_failed_queues.py
# Resets all PERMANENTLY FAILED items back to PENDING
# Run after fixing missing packages so files get reprocessed

import os, sys, json
_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))

def reset_dir(queues_dir: str, label: str):
    if not os.path.isdir(queues_dir):
        print(f"{label}: directory not found — {queues_dir}")
        return
    total_reset = 0
    for fname in sorted(os.listdir(queues_dir)):
        if not (fname.startswith("queue_") and fname.endswith(".json")):
            continue
        path = os.path.join(queues_dir, fname)
        name = fname[len("queue_"):-len(".json")]
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
        reset_count = 0
        for item in items:
            if isinstance(item, dict) and item.get("status") == "FAILED":
                item["status"]      = "PENDING"
                item["retry_count"] = 0
                item["error"]       = None
                reset_count += 1
        if reset_count:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2, default=str)
            os.replace(tmp, path)
            print(f"  {label}/{name}: reset {reset_count} FAILED → PENDING")
            total_reset += reset_count
        else:
            print(f"  {label}/{name}: no failed items")
    print(f"  Total reset: {total_reset}")

print("\nResetting AWR queues...")
reset_dir(os.path.join(_PROJECT_ROOT, "queues"), "AWR")

print("\nResetting SAR queues...")
reset_dir(os.path.join(_PROJECT_ROOT, "sar_queues"), "SAR")

print("\nDone. Queue processor will pick up PENDING items on next cycle.")
