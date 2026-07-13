#!/usr/bin/env python3
"""
bulk_export.py
==============
Bulk export all Grafana dashboard JSON files from Grafana.

Usage:
  py bulk_export.py
  py bulk_export.py --dir grafana_export
  py bulk_export.py --url http://localhost:3000 --user admin --password admin
  py bulk_export.py --folder "AWR Portal" --dir my_export

Defaults:
  --dir      : grafana_export (created in script folder)
  --url      : http://localhost:3000
  --user     : admin
  --password : admin
  --folder   : (all folders)
"""

import json
import os
import sys
import argparse
import urllib.request
import urllib.error
import base64
import time
import re


def get_auth_header(user: str, password: str) -> str:
    cred = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {cred}"


def grafana_request(url: str, auth: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": auth,
            "Accept":        "application/json",
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise Exception(f"HTTP {e.code}: {body[:200]}")


def safe_filename(title: str, uid: str) -> str:
    """Convert dashboard title + uid to a safe filename."""
    safe = re.sub(r'[^\w\s-]', '', title).strip()
    safe = re.sub(r'[\s]+', '_', safe)
    safe = safe[:50]
    return f"{uid}__{safe}.json"


def main():
    parser = argparse.ArgumentParser(
        description="Bulk export all Grafana dashboards to JSON files"
    )
    parser.add_argument(
        "--dir",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "grafana_export"
        ),
        help="Output directory for exported JSON files (default: grafana_export)"
    )
    parser.add_argument("--url",      default="http://localhost:3000",
                        help="Grafana URL (default: http://localhost:3000)")
    parser.add_argument("--user",     default="admin",
                        help="Grafana admin username (default: admin)")
    parser.add_argument("--password", default="admin",
                        help="Grafana admin password (default: admin)")
    parser.add_argument("--folder",   default="",
                        help="Export only dashboards in this folder (default: all)")
    parser.add_argument("--filter",   default="",
                        help="Export only dashboards matching title filter")
    parser.add_argument("--uid",      default="",
                        help="Export a single dashboard by UID")
    args = parser.parse_args()

    grafana_url = args.url.rstrip("/")
    auth        = get_auth_header(args.user, args.password)
    export_dir  = os.path.abspath(args.dir)

    # Verify Grafana is reachable
    print(f"Connecting to Grafana at {grafana_url}...")
    try:
        info = grafana_request(f"{grafana_url}/api/health", auth)
        print(f"  Grafana version: {info.get('version', 'unknown')}")
        print(f"  Database:        {info.get('database', 'unknown')}")
    except Exception as e:
        print(f"  ERROR: Cannot connect to Grafana: {e}")
        print("  Check URL and that Grafana service is running.")
        sys.exit(1)

    # Create output directory
    os.makedirs(export_dir, exist_ok=True)
    print(f"  Export directory: {export_dir}")

    # Single UID export
    if args.uid:
        print(f"\nExporting single dashboard: {args.uid}")
        try:
            data  = grafana_request(f"{grafana_url}/api/dashboards/uid/{args.uid}", auth)
            dash  = data.get("dashboard", {})
            title = dash.get("title", args.uid)
            fname = safe_filename(title, args.uid)
            fpath = os.path.join(export_dir, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(dash, f, indent=2, ensure_ascii=False)
            print(f"  ✓ Exported: {fname}")
        except Exception as e:
            print(f"  ✗ Failed: {e}")
        return 0

    # Search all dashboards
    print("\nSearching for dashboards...")
    search_url = f"{grafana_url}/api/search?type=dash-db&limit=5000"
    if args.folder:
        # Get folder ID first
        try:
            folders = grafana_request(f"{grafana_url}/api/folders", auth)
            folder_id = None
            for fd in folders:
                if fd.get("title", "").lower() == args.folder.lower():
                    folder_id = fd["id"]
                    break
            if folder_id is None:
                print(f"  Warning: Folder '{args.folder}' not found — exporting all")
            else:
                search_url += f"&folderIds={folder_id}"
                print(f"  Filtering to folder: {args.folder} (id={folder_id})")
        except Exception as e:
            print(f"  Warning: Could not filter by folder: {e}")

    try:
        dashboards = grafana_request(search_url, auth)
    except Exception as e:
        print(f"ERROR: Cannot search dashboards: {e}")
        sys.exit(1)

    # Apply title filter
    if args.filter:
        dashboards = [d for d in dashboards
                      if args.filter.lower() in d.get("title", "").lower()]
        print(f"  Filtered to {len(dashboards)} dashboards matching '{args.filter}'")

    if not dashboards:
        print("  No dashboards found.")
        sys.exit(0)

    print(f"  Found {len(dashboards)} dashboards to export")
    print("-" * 60)

    ok     = 0
    failed = 0
    errors = []

    for db in dashboards:
        uid   = db.get("uid", "")
        title = db.get("title", "unknown")

        if not uid:
            print(f"  –  {title[:50]:<50} (no UID, skipping)")
            continue

        try:
            # Fetch full dashboard JSON
            data = grafana_request(
                f"{grafana_url}/api/dashboards/uid/{uid}", auth
            )
            dash = data.get("dashboard", {})
            meta = data.get("meta", {})

            if not dash:
                print(f"  –  {title[:50]:<50} (empty response)")
                continue

            # Save to file
            fname = safe_filename(title, uid)
            fpath = os.path.join(export_dir, fname)

            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(dash, f, indent=2, ensure_ascii=False)

            folder_label = meta.get("folderTitle", "General")
            print(f"  ✓  {title[:50]:<50} [{folder_label}]")
            ok += 1

        except Exception as e:
            print(f"  ✗  {title[:50]:<50} ERROR: {str(e)[:60]}")
            failed += 1
            errors.append((title, uid, str(e)))

        # Small delay to avoid overwhelming Grafana
        time.sleep(0.05)

    print("-" * 60)
    print(f"\nResults: {ok} exported, {failed} failed")
    print(f"Files saved to: {export_dir}")

    if errors:
        print("\nFailed dashboards:")
        for title, uid, err in errors:
            print(f"  [{uid}] {title}: {err[:80]}")

    if ok > 0:
        print(f"\n✅ Done. {ok} dashboard(s) exported to:")
        print(f"   {export_dir}")
        print()
        print("To re-import later:")
        print(f"   py bulk_import.py --dir {os.path.basename(export_dir)}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
