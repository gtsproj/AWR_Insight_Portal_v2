#!/usr/bin/env python3
"""
bulk_import.py
==============
Bulk import all Grafana dashboard JSON files into Grafana.

Usage:
  py bulk_import.py
  py bulk_import.py --dir grafana-v12.0.2/public/dashboard
  py bulk_import.py --dir portal/static --url http://localhost:3000 --user admin --password admin

Defaults:
  --dir      : grafana-v12.0.2/public/dashboard (relative to script location)
  --url      : http://localhost:3000
  --user     : admin
  --password : admin
  --folder   : General (folder ID 0)
"""

import json
import os
import sys
import glob
import argparse
import urllib.request
import urllib.error
import base64
import time


def get_auth_header(user: str, password: str) -> str:
    cred = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {cred}"


def grafana_request(url: str, auth: str, method: str = "GET",
                    data: bytes = None) -> dict:
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type":  "application/json",
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


def get_folder_id(grafana_url: str, auth: str, folder_name: str) -> int:
    """Get folder ID by name, create if not exists. Returns 0 for General."""
    if not folder_name or folder_name.lower() == "general":
        return 0
    try:
        folders = grafana_request(f"{grafana_url}/api/folders", auth)
        for f in folders:
            if f.get("title", "").lower() == folder_name.lower():
                return f["id"]
        # Create folder
        result = grafana_request(
            f"{grafana_url}/api/folders", auth,
            method="POST",
            data=json.dumps({"title": folder_name}).encode()
        )
        print(f"  Created folder: {folder_name} (id={result['id']})")
        return result["id"]
    except Exception as e:
        print(f"  Warning: Could not get/create folder '{folder_name}': {e}")
        return 0


def import_dashboard(grafana_url: str, auth: str, dash: dict,
                     folder_id: int, overwrite: bool = True) -> dict:
    """Import a single dashboard via Grafana API."""
    # Remove id to allow overwrite by UID
    dash_copy = {k: v for k, v in dash.items() if k != "id"}

    payload = json.dumps({
        "dashboard": dash_copy,
        "overwrite": overwrite,
        "folderId":  folder_id,
    }).encode()

    return grafana_request(
        f"{grafana_url}/api/dashboards/import",
        auth,
        method="POST",
        data=payload
    )


def main():
    parser = argparse.ArgumentParser(
        description="Bulk import Grafana dashboard JSON files"
    )
    parser.add_argument(
        "--dir",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "grafana-v12.0.2", "public", "dashboard"
        ),
        help="Directory containing dashboard JSON files"
    )
    parser.add_argument("--url",      default="http://localhost:3000",
                        help="Grafana URL (default: http://localhost:3000)")
    parser.add_argument("--user",     default="admin",
                        help="Grafana admin username (default: admin)")
    parser.add_argument("--password", default="admin",
                        help="Grafana admin password (default: admin)")
    parser.add_argument("--folder",   default="",
                        help="Grafana folder name (default: General)")
    parser.add_argument("--no-overwrite", action="store_true",
                        help="Skip dashboards that already exist")
    parser.add_argument("--filter",   default="",
                        help="Only import files matching this pattern (e.g. fdv31)")
    args = parser.parse_args()

    grafana_url = args.url.rstrip("/")
    auth        = get_auth_header(args.user, args.password)
    overwrite   = not args.no_overwrite

    # Verify Grafana is reachable
    print(f"Connecting to Grafana at {grafana_url}...")
    try:
        info = grafana_request(f"{grafana_url}/api/health", auth)
        print(f"  Grafana version: {info.get('version', 'unknown')}")
    except Exception as e:
        print(f"  ERROR: Cannot connect to Grafana: {e}")
        print(f"  Check URL and that Grafana service is running.")
        sys.exit(1)

    # Get folder ID
    folder_id = get_folder_id(grafana_url, auth, args.folder)
    folder_label = args.folder or "General"
    print(f"  Target folder: {folder_label} (id={folder_id})")

    # Find JSON files
    dashboard_dir = os.path.abspath(args.dir)
    if not os.path.isdir(dashboard_dir):
        print(f"\nERROR: Directory not found: {dashboard_dir}")
        sys.exit(1)

    json_files = sorted(glob.glob(os.path.join(dashboard_dir, "*.json")))

    if args.filter:
        json_files = [f for f in json_files if args.filter.lower()
                      in os.path.basename(f).lower()]

    if not json_files:
        print(f"\nNo JSON files found in: {dashboard_dir}")
        sys.exit(1)

    print(f"\nFound {len(json_files)} dashboard files in:")
    print(f"  {dashboard_dir}")
    print(f"\nImporting{'(overwrite)' if overwrite else '(skip existing)'}...")
    print("-" * 60)

    ok      = 0
    skipped = 0
    failed  = 0
    errors  = []

    for fpath in json_files:
        fname = os.path.basename(fpath)
        try:
            with open(fpath, encoding="utf-8") as f:
                dash = json.load(f)

            uid   = dash.get("uid", "?")
            title = dash.get("title", fname)[:50]

            result = import_dashboard(grafana_url, auth, dash,
                                      folder_id, overwrite)
            status = result.get("status", "")
            slug   = result.get("slug", "") or result.get("uid", "")

            # Grafana returns various success statuses:
            # "success", "plugin-dashboard", "imported", or empty with a slug
            success = (status in ("success", "plugin-dashboard", "imported")
                       or bool(slug) or result.get("id"))

            if success:
                print(f"  ✓  {fname[:45]:<45} → {title}")
                ok += 1
            else:
                print(f"  ?  {fname[:45]:<45} status={status} result={str(result)[:80]}")
                ok += 1

        except Exception as e:
            err_msg = str(e)
            if "already exists" in err_msg.lower() and not overwrite:
                print(f"  –  {fname[:45]:<45} (skipped — exists)")
                skipped += 1
            else:
                print(f"  ✗  {fname[:45]:<45} ERROR: {err_msg[:60]}")
                failed += 1
                errors.append((fname, err_msg))

        # Small delay to avoid overwhelming Grafana
        time.sleep(0.1)

    print("-" * 60)
    print(f"\nResults: {ok} imported, {skipped} skipped, {failed} failed")

    if errors:
        print("\nFailed files:")
        for fname, err in errors:
            print(f"  {fname}: {err[:100]}")

    if ok > 0:
        print(f"\n✅ Done. Open Grafana at {grafana_url}")
        print(f"   Check Dashboards → Browse to verify imported dashboards.")

    if failed > 0:
        print(f"\n⚠️  {failed} dashboard(s) failed. Check errors above.")
        print("   Common causes:")
        print("   - Invalid JSON (run py patch_grafana_urls.py first)")
        print("   - Grafana version incompatibility")
        print("   - Duplicate UID conflict (use --no-overwrite to skip)")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
