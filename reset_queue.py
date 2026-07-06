import os, json, glob

for qfile in glob.glob("queues/queue_*.json"):
    with open(qfile, encoding="utf-8") as f:
        items = json.load(f)
    changed = 0
    for item in items:
        if item.get("status") == "PROCESSING":
            item["status"]      = "PENDING"
            item["retry_count"] = 0
            item["error"]       = None
            changed += 1
    with open(qfile, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, default=str)
    print(f"{os.path.basename(qfile)}: reset {changed} PROCESSING → PENDING")

print("Done — safe to restart Windows")