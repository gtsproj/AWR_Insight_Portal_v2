#!/usr/bin/env python3
"""
patch_grafana_urls.py
======================
Patches all AWR Insight Portal Grafana dashboard JSON files to replace
hardcoded localhost URLs with the actual server IP address.

Usage:
  py patch_grafana_urls.py --dir C:\\AWR_Insight_Portal_v2\\portal\\static
  py patch_grafana_urls.py --dir portal\\static --portal-port 8000 --grafana-port 3000

The server IP is auto-detected from the network interface.
No need to hardcode IP addresses — the script detects them automatically.
Re-run whenever the server IP changes.
"""

import json
import os
import sys
import argparse
import glob
import socket
from copy import deepcopy


def get_server_ip() -> str:
    """
    Auto-detect the server's primary LAN IP address.
    Uses a UDP socket trick — no actual connection made.
    Works reliably on Windows servers with multiple NICs.
    """
    # Try multiple targets in case one is blocked
    for target in [("8.8.8.8", 80), ("1.1.1.1", 80), ("192.168.1.1", 80)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(target)
            ip = s.getsockname()[0]
            s.close()
            if ip and ip != "0.0.0.0":
                return ip
        except Exception:
            continue

    # Fallback: get all IPs and pick first non-loopback
    try:
        hostname = socket.gethostname()
        ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in ips:
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "localhost"

# ── URL variable templates ──────────────────────────────────────────
def _make_url_variable(name: str, label: str, default: str) -> dict:
    """Create a Grafana text custom variable for URL."""
    return {
        "current": {"selected": False, "text": default, "value": default},
        "description": f"Base URL for {label} — change to server IP for intranet access",
        "hide": 2,            # 2 = hidden from UI (technical variable)
        "label": label,
        "name": name,
        "options": [{"selected": True, "text": default, "value": default}],
        "query": default,
        "skipUrlSync": False,
        "type": "textbox",
    }


def patch_dashboard(data: dict, portal_url: str, grafana_url: str) -> dict:
    """
    Patch a single dashboard:
    1. Add portal_url and grafana_url as hidden textbox variables
       with the LITERAL server IP as default value
    2. Replace all localhost:8000 / localhost:3000 in content with the IP
    3. Replace any ${portal_url}/${grafana_url} self-references in variable
       definitions with the actual IP
    4. Re-apply literal IP to variable definitions to ensure correctness
    """
    data = deepcopy(data)

    # ── 1. Add URL variables with literal IP defaults ─────────────
    if "templating" not in data:
        data["templating"] = {"list": []}
    if "list" not in data["templating"]:
        data["templating"]["list"] = []

    vlist = [v for v in data["templating"]["list"]
             if v.get("name") not in ("portal_url", "grafana_url")]
    vlist.insert(0, _make_url_variable("grafana_url", "Grafana URL", grafana_url))
    vlist.insert(0, _make_url_variable("portal_url",  "Portal URL",  portal_url))
    data["templating"]["list"] = vlist

    # ── 2. Replace in full JSON ───────────────────────────────────
    text = json.dumps(data)
    # Replace localhost references
    text = text.replace("http://localhost:8000", portal_url)
    text = text.replace("http://localhost:3000", grafana_url)
    data = json.loads(text)

    # ── 3. Force variable definitions to literal IP ───────────────
    # This is the critical step — must run AFTER json replace
    # because step 2 may have corrupted the variable values
    for v in data["templating"]["list"]:
        if v.get("name") == "portal_url":
            v["query"]            = portal_url
            v["current"]["text"]  = portal_url
            v["current"]["value"] = portal_url
            if v.get("options"):
                v["options"][0]["text"]  = portal_url
                v["options"][0]["value"] = portal_url
        elif v.get("name") == "grafana_url":
            v["query"]            = grafana_url
            v["current"]["text"]  = grafana_url
            v["current"]["value"] = grafana_url
            if v.get("options"):
                v["options"][0]["text"]  = grafana_url
                v["options"][0]["value"] = grafana_url

    return data


def main():
    parser = argparse.ArgumentParser(
        description="Patch Grafana dashboard JSON files for intranet access"
    )
    parser.add_argument("--dir",          default=".",
                        help="Directory containing dashboard JSON files (default: current dir)")
    parser.add_argument("--portal-port",  default="8000",
                        help="Portal port number (default: 8000)")
    parser.add_argument("--grafana-port", default="3000",
                        help="Grafana port number (default: 3000)")
    parser.add_argument("--ip",           default=None,
                        help="Override server IP (default: auto-detected)")
    parser.add_argument("--backup",       action="store_true",
                        help="Create .bak backup before patching")
    args = parser.parse_args()

    # Auto-detect server IP
    server_ip = args.ip or get_server_ip()
    portal_url  = f"http://{server_ip}:{args.portal_port}"
    grafana_url = f"http://{server_ip}:{args.grafana_port}"

    print(f"Detected server IP: {server_ip}")
    print(f"Portal URL:  {portal_url}")
    print(f"Grafana URL: {grafana_url}")
    print()

    dashboard_dir = os.path.abspath(args.dir)
    if not os.path.isdir(dashboard_dir):
        print(f"Error: Directory not found: {dashboard_dir}")
        sys.exit(1)

    json_files = glob.glob(os.path.join(dashboard_dir, "*.json"))
    dashboard_files = [f for f in json_files
                       if any(k in os.path.basename(f).lower() for k in
                              ["awr", "sar", "intelligence", "memory",
                               "anomal", "correlation", "nav"])]

    if not dashboard_files:
        print(f"No dashboard JSON files found in: {dashboard_dir}")
        print("Expected files like: awr_intelligence_ai.json, sar_overview.json etc.")
        sys.exit(1)

    print(f"Found {len(dashboard_files)} dashboard files in: {dashboard_dir}")
    print()

    patched = 0
    errors  = 0

    for filepath in sorted(dashboard_files):
        fname = os.path.basename(filepath)
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)

            if args.backup:
                import shutil
                shutil.copy2(filepath, filepath + ".bak")

            # Count existing localhost references
            orig_text    = json.dumps(data)
            n_portal  = orig_text.count("localhost:8000") + orig_text.count("localhost:" + args.portal_port)
            n_grafana = orig_text.count("localhost:3000") + orig_text.count("localhost:" + args.grafana_port)

            patched_data = patch_dashboard(data, portal_url, grafana_url)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(patched_data, f, indent=2, ensure_ascii=False)

            print(f"  ✓ {fname}")
            if n_portal > 0 or n_grafana > 0:
                print(f"    Replaced: {n_portal} portal URL(s), {n_grafana} Grafana URL(s)")
            else:
                print(f"    Variables added (no hardcoded URLs found)")
            patched += 1

        except Exception as e:
            print(f"  ✗ {fname}: {e}")
            errors += 1

    print()
    print(f"Done — {patched} patched, {errors} errors")
    print()
    print("Next steps:")
    print("  1. Re-import each updated JSON file in Grafana:")
    print("     Dashboards → New → Import → Upload JSON file → overwrite existing")
    print()
    print("  Note: Re-run this script whenever the server IP changes.")
    print(f"        Current IP: {server_ip}")
    print()
    print("  To override IP manually:")
    print(f"    py patch_grafana_urls.py --dir portal\\static --ip YOUR_IP")


if __name__ == "__main__":
    main()
