# reset_failed_queue.py
import os, json, glob

queues_dir = r"C:\AWR_Insight_Portal_v2\queues"

for qfile in glob.glob(os.path.join(queues_dir, "queue_*.json")):
    with open(qfile, encoding="utf-8") as f:
        items = json.load(f)

    changed = 0
    for item in items:
        if item.get("status") in ("FAILED", "PERMANENTLY_FAILED"):
            item["status"] = "PENDING"
            item["retry_count"] = 0
            item["error"] = None
            changed += 1

    tmp = qfile + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, default=str)
    os.replace(tmp, qfile)
    print(f"✅ {os.path.basename(qfile)}: reset {changed} failed items → PENDING")

print("\nDone. queue_processor.py will pick them up on next poll.")