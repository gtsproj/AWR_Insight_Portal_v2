# Run from C:\AWR_Insight_Portal_v2\
# py clear_cache.py
import os, shutil

project_root = os.getcwd()
deleted = []

for root, dirs, files in os.walk(project_root):
    for d in dirs:
        if d == "__pycache__":
            full_path = os.path.join(root, d)
            try:
                shutil.rmtree(full_path)
                deleted.append(full_path)
                print(f"Deleted: {full_path}")
            except Exception as e:
                print(f"Failed: {full_path} — {e}")

print(f"\nTotal deleted: {len(deleted)} __pycache__ folders")
print("Now restart the portal.")
