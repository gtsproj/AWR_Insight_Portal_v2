# portal/app.py
# ============================================================
# AWR Insight Portal — Web UI (FastAPI)  — Deliverable #13
#
# Companion web portal for file management, plan upload,
# queue monitoring, comparison setup, and SQL search.
# Runs alongside Grafana (not a replacement).
#
# INSTALL:
#   pip install fastapi uvicorn python-multipart jinja2
#
# RUN:
#   python portal/app.py              # development
#   uvicorn portal.app:app --host 0.0.0.0 --port 8000 --workers 2
# ============================================================

import os
import sys
import json
import hashlib
import shutil
import secrets
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Add project root and common to path
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from config_loader import load_config
from db import get_db_connection
from logger_utils import get_logger

_PORTAL_DIR = os.path.dirname(__file__)

logger = get_logger("portal")
_cfg   = load_config()
_paths = _cfg.get("paths", {})

AWR_WATCH_DIR = os.path.join(_PROJECT_ROOT,
    _paths.get("watch_directory", "awr_reports"))
SAR_INPUT_DIR = os.path.join(_PROJECT_ROOT,
    _cfg.get("sar", {}).get("input_dir", "sar_reports"))
QUEUES_DIR    = os.path.join(_PROJECT_ROOT,
    _paths.get("queues_directory", "queues"))
SAR_QUEUES_DIR = os.path.join(_PROJECT_ROOT,
    _paths.get("sar_queues_directory", "sar_queues"))

app = FastAPI(
    title="AWR Insight Portal",
    description="Upload, monitor, and manage Oracle AWR/SAR analysis",
    version="1.0.0"
)

@app.on_event("startup")
async def on_startup():
    """On portal start — auto-patch Grafana dashboard URLs from portal_config."""
    import asyncio
    # Run in background so startup isn't delayed
    asyncio.create_task(asyncio.to_thread(_auto_patch_grafana_dashboards))

# Static files and templates
_static_dir    = os.path.join(_PORTAL_DIR, "static")
_templates_dir = os.path.join(_PORTAL_DIR, "templates")
os.makedirs(_static_dir, exist_ok=True)
os.makedirs(_templates_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=_static_dir), name="static")
templates = Jinja2Templates(directory=_templates_dir)
try:
    from portal.filters import basename as _basename_filter
    templates.env.filters["basename"] = _basename_filter
except Exception as _fe:
    logger.warning(f"Could not load portal.filters: {_fe}")
    templates.env.filters["basename"] = lambda p: os.path.basename(p) if p else p

def _get_grafana_url() -> str:
    """
    Get Grafana URL from portal_config.
    Always reads from DB — no caching — so changes take effect immediately.
    """
    try:
        cfg = _get_config()
        url = cfg.get("grafana_url", "").strip().rstrip("/")
        return url if url else "http://localhost:3000"
    except Exception:
        return "http://localhost:3000"

def _get_portal_url(request: Request = None) -> str:
    """
    Get portal base URL.
    Derives from the incoming request when available — guaranteed correct
    since the browser is already connected to this address.
    Falls back to portal_config value.
    """
    if request:
        # Build from actual request: scheme://host (includes port if non-standard)
        base = str(request.base_url).rstrip("/")
        return base
    try:
        cfg = _get_config()
        url = cfg.get("portal_url", "").strip().rstrip("/")
        return url if url else "http://localhost:8000"
    except Exception:
        return "http://localhost:8000"

def _url_context(request: Request = None) -> dict:
    """Return URL context for template injection. Always fresh from DB."""
    return {
        "grafana_url": _get_grafana_url(),
        "portal_url":  _get_portal_url(request),
    }

def _refresh_url_globals():
    """No-op — kept for compatibility. URL context now read fresh per request."""
    pass


def _auto_patch_grafana_dashboards():
    """
    Automatically patch Grafana dashboard JSON files on portal startup.
    Reads portal_url and grafana_url from portal_config (source of truth).
    Updates the variable defaults in all dashboard JSON files.
    Runs silently — never blocks startup if it fails.

    Triggered by:
    - Portal service start/restart
    - Settings save (when grafana_url or portal_url changes)
    """
    try:
        cfg         = _get_config()
        portal_url  = cfg.get("portal_url",  "").strip().rstrip("/")
        grafana_url = cfg.get("grafana_url", "").strip().rstrip("/")

        if not portal_url or not grafana_url:
            logger.debug("auto_patch_dashboards: URLs not configured yet — skipping")
            return

        if "localhost" in portal_url and "localhost" in grafana_url:
            logger.debug("auto_patch_dashboards: Both URLs are localhost — skipping patch")
            return

        # Find dashboard directories
        dashboard_dirs = [
            os.path.join(_PROJECT_ROOT, "grafana-v12.0.2", "public", "dashboard"),
            os.path.join(_PROJECT_ROOT, "portal", "static"),
        ]

        patched_total = 0
        for dash_dir in dashboard_dirs:
            if not os.path.isdir(dash_dir):
                continue

            import glob
            json_files = glob.glob(os.path.join(dash_dir, "*.json"))
            patched = 0

            for fpath in json_files:
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)

                    if "templating" not in data:
                        data["templating"] = {"list": []}

                    modified = False
                    for v in data["templating"].get("list", []):
                        if v.get("name") == "portal_url":
                            if v.get("query") != portal_url:
                                v["query"]            = portal_url
                                v["current"]["text"]  = portal_url
                                v["current"]["value"] = portal_url
                                if v.get("options"):
                                    v["options"][0]["text"]  = portal_url
                                    v["options"][0]["value"] = portal_url
                                modified = True
                        elif v.get("name") == "grafana_url":
                            if v.get("query") != grafana_url:
                                v["query"]            = grafana_url
                                v["current"]["text"]  = grafana_url
                                v["current"]["value"] = grafana_url
                                if v.get("options"):
                                    v["options"][0]["text"]  = grafana_url
                                    v["options"][0]["value"] = grafana_url
                                modified = True

                    if modified:
                        with open(fpath, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        patched += 1

                except Exception as e:
                    logger.debug(f"auto_patch_dashboards: skip {os.path.basename(fpath)}: {e}")

            if patched:
                logger.info(f"auto_patch_dashboards: updated {patched} files in {dash_dir}")
            patched_total += patched

        if patched_total:
            logger.info(
                f"auto_patch_dashboards: {patched_total} dashboard(s) updated "
                f"with portal_url={portal_url}, grafana_url={grafana_url}. "
                f"Re-import dashboards in Grafana to apply."
            )
        else:
            logger.debug("auto_patch_dashboards: all dashboards already up to date")

    except Exception as e:
        logger.warning(f"auto_patch_dashboards failed (non-fatal): {e}")

# Public routes — never require login
_PUBLIC_PATHS = {"/login", "/logout", "/forgot-password", "/reset-password"}

@app.middleware("http")
async def enforce_login(request: Request, call_next):
    """Redirect to login page if portal_login_required=true and no valid session."""
    path = request.url.path
    # Always allow static files and public paths
    if path.startswith("/static") or path in _PUBLIC_PATHS:
        return await call_next(request)
    # Check config — cache for 60s to avoid DB hit on every request
    import time
    now = time.time()
    if not hasattr(app.state, '_login_cache') or now - app.state._login_cache_ts > 60:
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM portal_config WHERE key='portal_login_required'")
                row = cur.fetchone()
                app.state._login_cache    = (row[0] if row else 'false')
                app.state._login_cache_ts = now
            conn.close()
        except Exception:
            app.state._login_cache    = 'false'
            app.state._login_cache_ts = now
    if app.state._login_cache != 'true':
        return await call_next(request)
    # Validate session
    token = request.cookies.get('portal_session')
    sess  = _sessions.get(token, {}) if token else {}
    if sess and sess.get('expires') and datetime.now() > sess['expires']:
        _sessions.pop(token, None)
        sess = {}
    if not sess:
        # API calls return 401, page calls redirect to login
        if path.startswith("/api/"):
            from fastapi.responses import JSONResponse as _JR
            return _JR({"detail": "Session expired — please log in"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)
    return await call_next(request)


@app.middleware("http")
async def enforce_license(request: Request, call_next):
    """
    License enforcement middleware.
    Blocks parsing APIs and AI generation when license is expired/invalid.
    Passes through login, settings, static, and read-only pages.
    """
    path = request.url.path

    # Always allow
    _lic_free = {"/login", "/logout", "/forgot-password", "/settings",
                 "/service-control",
                 "/api/license/status", "/api/license/mac-info", "/api/license/mac-debug",
                 "/api/portal-info", "/api/queue-stats", "/api/queue-stats-sar",
                 "/api/settings", "/api/snaps", "/api/db-list",
                 "/api/instance-list", "/api/ai/history",
                 "/api/services/status", "/api/cache/clear"}
    if (path.startswith("/static") or path in _PUBLIC_PATHS or
            path in _lic_free or path.startswith("/api/settings") or
            path.startswith("/api/services/") or path.startswith("/api/logs")):
        return await call_next(request)

    # Cache license status for 5 minutes
    import time as _t
    now = _t.time()
    if (not hasattr(app.state, '_lic_cache') or
            now - getattr(app.state, '_lic_cache_ts', 0) > 300):
        try:
            sys.path.insert(0, os.path.join(_PROJECT_ROOT, "modules"))
            from license_engine import get_license_status
            conn = get_db_connection()
            app.state._lic_cache    = get_license_status(conn)
            app.state._lic_cache_ts = now
            conn.close()
        except Exception as e:
            logger.debug(f"License cache refresh failed: {e}")
            app.state._lic_cache    = {"allow_parse": True, "allow_grafana": True,
                                        "allow_ai_new": True, "status": "ok"}
            app.state._lic_cache_ts = now

    lic = app.state._lic_cache

    # Block AWR/SAR upload/parse APIs when not allowed
    _parse_paths = ["/upload", "/api/queue", "/api/requeue", "/api/reprocess"]
    if not lic.get("allow_parse", True):
        if any(path.startswith(p) for p in _parse_paths):
            if path.startswith("/api/"):
                from fastapi.responses import JSONResponse as _JR
                return _JR({
                    "ok": False,
                    "error": f"License: {lic.get('status_msg', 'License error')}. Parsing is blocked."
                }, status_code=403)

    # Block new AI recommendations when not allowed
    if not lic.get("allow_ai_new", True):
        if path == "/api/ai/recommend":
            from fastapi.responses import JSONResponse as _JR
            return _JR({
                "ok": False,
                "error": f"License: {lic.get('status_msg', 'License error')}. New AI recommendations are blocked."
            }, status_code=403)

    return await call_next(request)


# ── helpers ───────────────────────────────────────────────────────────
def _file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(65536))
    return h.hexdigest()


def _load_queue(db_name: str) -> list:
    path = os.path.join(QUEUES_DIR, f"queue_{db_name}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _all_queues() -> dict:
    result = {}
    if not os.path.isdir(QUEUES_DIR):
        return result
    for fname in os.listdir(QUEUES_DIR):
        if fname.startswith("queue_") and fname.endswith(".json"):
            db = fname[len("queue_"):-len(".json")]
            result[db] = _load_queue(db)
    return result


def _load_sar_queue(hostname: str) -> list:
    path = os.path.join(SAR_QUEUES_DIR, f"queue_{hostname}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _all_sar_queues() -> dict:
    result = {}
    if not os.path.isdir(SAR_QUEUES_DIR):
        return result
    for fname in os.listdir(SAR_QUEUES_DIR):
        if fname.startswith("queue_") and fname.endswith(".json"):
            host = fname[len("queue_"):-len(".json")]
            result[host] = _load_sar_queue(host)
    return result


def _db_list() -> list:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT dbname FROM awr_load_profile ORDER BY dbname")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════

def _queue_stats() -> dict:
    """Return live queue counters for both AWR and SAR queues."""
    awr_queues = _all_queues()          # existing AWR queue reader
    sar_queues = _all_sar_queues()      # new SAR queue reader

    # AWR stats
    awr_pending    = sum(1 for items in awr_queues.values() for i in items if i.get("status") == "PENDING")
    awr_processing = sum(1 for items in awr_queues.values() for i in items if i.get("status") == "PROCESSING")
    awr_done       = sum(1 for items in awr_queues.values() for i in items if i.get("status") == "DONE")
    awr_failed     = sum(1 for items in awr_queues.values() for i in items if i.get("status") == "FAILED")

    # SAR stats
    sar_pending    = sum(1 for items in sar_queues.values() for i in items if i.get("status") == "PENDING")
    sar_processing = sum(1 for items in sar_queues.values() for i in items if i.get("status") == "PROCESSING")
    sar_done       = sum(1 for items in sar_queues.values() for i in items if i.get("status") == "DONE")
    sar_failed     = sum(1 for items in sar_queues.values() for i in items if i.get("status") == "FAILED")

    active_dbs  = [db for db, items in awr_queues.items()
                   if any(i.get("status") == "PROCESSING" for i in items)]
    active_sars = [h for h, items in sar_queues.items()
                   if any(i.get("status") == "PROCESSING" for i in items)]
    db_names    = sorted(awr_queues.keys())
    sar_names   = sorted(sar_queues.keys())

    return {
        # AWR
        "pending":    awr_pending,
        "processing": awr_processing,
        "done":       awr_done,
        "failed":     awr_failed,
        "db_count":   len(awr_queues),
        "db_names":   db_names,
        "active_db":  ", ".join(active_dbs) if active_dbs else None,
        # SAR
        "sar_pending":    sar_pending,
        "sar_processing": sar_processing,
        "sar_done":       sar_done,
        "sar_failed":     sar_failed,
        "sar_host_count": len(sar_queues),
        "sar_hosts":      sar_names,
        "active_sar":     ", ".join(active_sars) if active_sars else None,
    }

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    _refresh_url_globals()
    stats = _queue_stats()
    cfg   = _get_config("ai")
    return templates.TemplateResponse(request, "home.html",
        context={"stats": stats, "page": "home",
                 "is_admin": _is_admin(request),
                 "ai_mode": cfg.get("ai_mode","rules"),
                 **_url_context(request)})


# ── AWR Upload ────────────────────────────────────────────────────────
@app.get("/upload/awr", response_class=HTMLResponse)
async def awr_upload_page(request: Request):
    return templates.TemplateResponse(request, "awr_upload.html",
        context={"page": "upload", "message": None})


@app.post("/upload/awr", response_class=HTMLResponse)
async def awr_upload_post(request: Request,
                           files: list[UploadFile] = File(...),
                           dbname_override: str = Form(default="")):
    results = []
    for upload in files:
        if not upload.filename:
            continue
        ext = Path(upload.filename).suffix.lower()
        if ext not in (".html", ".htm", ".txt"):
            results.append({"file": upload.filename, "status": "❌ Invalid type (need .html/.htm/.txt)"})
            continue

        # Detect target folder from filename or override
        db_name = dbname_override.strip().upper() or _infer_db_name(upload.filename)
        dest_dir = os.path.join(AWR_WATCH_DIR, db_name.lower())
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, upload.filename)

        content = await upload.read()
        with open(dest_path, "wb") as f:
            f.write(content)

        # Enqueue via watcher module
        try:
            sys.path.insert(0, _PROJECT_ROOT)
            from awr_watcher import enqueue_file
            enqueued = enqueue_file(dest_path)
            status   = "✅ Enqueued" if enqueued else "⏭ Already queued"
        except Exception as e:
            status = f"⚠ Saved but enqueue failed: {e}"

        results.append({"file": upload.filename, "db": db_name, "status": status})

    return templates.TemplateResponse(request, "awr_upload.html",
        context={"page": "upload", "results": results, "message": None})


def _infer_db_name(filename: str) -> str:
    import re
    m = re.match(r"^awr[_-]([A-Za-z0-9$#_]+?)[_-]\d", filename, re.IGNORECASE)
    return m.group(1).upper() if m else "UNKNOWN"


# ── SAR Upload ────────────────────────────────────────────────────────
@app.get("/upload/sar", response_class=HTMLResponse)
async def sar_upload_page(request: Request):
    return templates.TemplateResponse(request, "sar_upload.html",
        context={"page": "sar", "message": None})


@app.post("/upload/sar", response_class=HTMLResponse)
async def sar_upload_post(request: Request,
                           files: list[UploadFile] = File(...),
                           hostname: str = Form(default="")):
    results = []
    for upload in files:
        if not upload.filename:
            continue
        host = hostname.strip() or "unknown_host"
        dest_dir  = os.path.join(SAR_INPUT_DIR, host)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, upload.filename)

        content = await upload.read()
        with open(dest_path, "wb") as f:
            f.write(content)

        # Trigger SAR parsing inline for immediate feedback
        try:
            sys.path.insert(0, os.path.join(_PROJECT_ROOT, "modules", "sar"))
            from sar_master_parser import process_sar_file
            ok     = process_sar_file(dest_path)
            status = "✅ Parsed" if ok else "⚠ Parsed with warnings"
        except Exception as e:
            status = f"⚠ Saved but parsing failed: {e}"

        results.append({"file": upload.filename, "host": host, "status": status})

    return templates.TemplateResponse(request, "sar_upload.html",
        context={"page": "sar", "results": results, "message": None})


# ── Execution Plan Upload ─────────────────────────────────────────────
@app.get("/upload/plan", response_class=HTMLResponse)
async def plan_upload_page(request: Request):
    dbs = _db_list()
    return templates.TemplateResponse(request, "plan_upload.html",
        context={"page": "plan", "dbs": dbs,
         "message": None, "analysis": None})


@app.post("/upload/plan", response_class=HTMLResponse)
async def plan_upload_post(request: Request,
                            dbname: str = Form(...),
                            sql_id: str = Form(default=""),
                            begin_snap: str = Form(default=""),
                            plan_text: str = Form(default=""),
                            plan_file: UploadFile = File(default=None)):
    dbs    = _db_list()
    text   = plan_text.strip()
    source = "paste"
    if not text and plan_file and plan_file.filename:
        text   = (await plan_file.read()).decode("utf-8", errors="replace")
        source = "file"
    if not text:
        return templates.TemplateResponse(request, "plan_upload.html",
            context={"page": "plan", "dbs": dbs,
                     "message": "\u26a0 No plan text provided.",
                     "analysis": None, "multi_results": None})
    try:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, "modules", "plan"))
        from plan_parser import (parse_plan_text, parse_multi_plan_file,
                                  insert_plan, insert_multi_plan, analyse_plan)
        snap = int(begin_snap) if begin_snap.strip().isdigit() else None
        import re as _re
        plan_count = len(_re.findall(
            r"SQL_ID\s+[A-Za-z0-9_$#]+",
            text, _re.IGNORECASE))
        if plan_count > 1:
            results = parse_multi_plan_file(text, dbname,
                                             begin_snap=snap,
                                             upload_source=source)
            summary = insert_multi_plan(results)
            total   = sum(summary.values())
            message = (f"\u2705 Multi-plan file: {len(results)} plans parsed, "
                       f"{total} total steps stored")
            return templates.TemplateResponse(request, "plan_upload.html",
                context={"page": "plan", "dbs": dbs, "message": message,
                         "analysis": None, "multi_results": results})
        else:
            records  = parse_plan_text(text, dbname,
                                        sql_id=sql_id.strip() or None,
                                        begin_snap=snap, upload_source=source)
            inserted = insert_plan(records)
            analysis = analyse_plan(records)
            message  = (f"\u2705 {inserted} plan steps stored "
                        f"for SQL_ID={analysis.sql_id}")
            return templates.TemplateResponse(request, "plan_upload.html",
                context={"page": "plan", "dbs": dbs, "message": message,
                         "analysis": analysis, "multi_results": None})
    except Exception as e:
        logger.error(f"Plan upload failed: {e}", exc_info=True)
        return templates.TemplateResponse(request, "plan_upload.html",
            context={"page": "plan", "dbs": dbs,
                     "message": f"\u274c Parse failed: {e}",
                     "analysis": None, "multi_results": None})

# ── Queue Monitor ─────────────────────────────────────────────────────
@app.get("/queues", response_class=HTMLResponse)
async def queue_monitor(request: Request):
    queues     = _all_queues()
    sar_queues = _all_sar_queues()
    return templates.TemplateResponse(request, "queue_monitor.html",
        context={"page": "queues", "queues": queues, "sar_queues": sar_queues})


@app.post("/queues/retry/{db_name}", response_class=RedirectResponse)
async def retry_failed(db_name: str):
    """Re-queue all FAILED items for a DB."""
    path = os.path.join(QUEUES_DIR, f"queue_{db_name}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        for item in items:
            if item.get("status") == "FAILED":
                item["status"]      = "PENDING"
                item["retry_count"] = 0
                item["error"]       = None
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, default=str)
        os.replace(tmp, path)
    return RedirectResponse("/queues", status_code=303)


# ── Comparison Setup ──────────────────────────────────────────────────
@app.get("/comparison", response_class=HTMLResponse)
async def comparison_page(request: Request):
    dbs  = _db_list()
    tags = _get_comparison_tags()
    return templates.TemplateResponse(request, "comparison.html",
        context={"page": "comparison", "dbs": dbs,
         "tags": tags, "message": None, **_url_context(request)})


@app.post("/comparison", response_class=HTMLResponse)
async def comparison_post(request: Request,
                           dbname: str = Form(...),
                           instance: str = Form(...),
                           tag_name: str = Form(...),
                           tag_type: str = Form(...),
                           snap_start: int = Form(...),
                           snap_end: int = Form(...),
                           notes: str = Form(default=""),
                           edit_original_tag: str = Form(default="")):
    dbs = _db_list()
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Ensure instance column exists (safe migration)
            cur.execute("""
                ALTER TABLE awr_comparison_tags
                ADD COLUMN IF NOT EXISTS instance TEXT DEFAULT ''
            """)
            conn.commit()
            if edit_original_tag.strip():
                cur.execute("""
                    UPDATE awr_comparison_tags
                    SET tag_name   = %s,
                        instance   = %s,
                        tag_type   = %s,
                        snap_start = %s,
                        snap_end   = %s,
                        notes      = %s
                    WHERE dbname = %s AND tag_name = %s
                """, (tag_name, instance, tag_type, snap_start, snap_end,
                      notes, dbname, edit_original_tag.strip()))
                message = f"✅ Tag '{tag_name}' updated for {dbname}/{instance}"
            else:
                cur.execute("""
                    INSERT INTO awr_comparison_tags
                        (tag_name, dbname, instance, snap_start, snap_end, tag_type, notes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (dbname, tag_name, tag_type) DO UPDATE
                      SET instance=EXCLUDED.instance,
                          snap_start=EXCLUDED.snap_start,
                          snap_end=EXCLUDED.snap_end,
                          notes=EXCLUDED.notes
                """, (tag_name, dbname, instance, snap_start, snap_end, tag_type, notes))
                message = (f"✅ Tag '{tag_name}' ({tag_type}) saved for "
                           f"{dbname}/{instance} snaps {snap_start}-{snap_end}")
        conn.commit()
        conn.close()
    except Exception as e:
        message = f"❌ Failed: {e}"

    tags = _get_comparison_tags()
    return templates.TemplateResponse(request, "comparison.html",
        context={"page": "comparison", "dbs": dbs,
                 "tags": tags, "message": message})


@app.post("/comparison/delete", response_class=RedirectResponse)
async def comparison_delete(dbname: str = Form(...),
                             tag_name: str = Form(...),
                             tag_type: str = Form(...)):
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM awr_comparison_tags
                WHERE dbname = %s AND tag_name = %s AND tag_type = %s
            """, (dbname, tag_name, tag_type))
        conn.commit()
        conn.close()
        logger.info(f"Deleted comparison tag '{tag_name}' ({tag_type}) for {dbname}")
    except Exception as e:
        logger.error(f"Tag delete failed: {e}")
    return RedirectResponse("/comparison", status_code=303)


def _get_comparison_tags() -> list:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT dbname, COALESCE(instance,'') AS instance,
                       tag_name, tag_type, snap_start, snap_end, notes, created_at
                FROM awr_comparison_tags ORDER BY created_at DESC LIMIT 50
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


# ── SQL Text Search ───────────────────────────────────────────────────
@app.get("/sql-search", response_class=HTMLResponse)
async def sql_search_page(request: Request,
                            dbname: str = "",
                            instance: str = "",
                            search_type: str = "text",
                            q: str = "",
                            sql_id: str = ""):
    dbs     = _db_list()
    results = []
    error   = None
    searched = False

    if dbname and instance:
        if search_type == "id" and sql_id.strip():
            searched = True
            results = _search_sql_by_id(sql_id.strip(), dbname, instance)
        elif search_type == "text" and q.strip():
            searched = True
            results = _search_sql_text(q.strip(), dbname, instance)

    return templates.TemplateResponse(request, "sql_search.html",
        context={"page": "sql_search", "dbs": dbs,
                 "dbname": dbname, "instance": instance,
                 "search_type": search_type,
                 "query": q, "sql_id": sql_id,
                 "results": results, "searched": searched})


def _search_sql_by_id(sql_id: str, dbname: str, instance: str) -> list:
    """Exact SQL ID lookup — returns one distinct full SQL text."""
    sql = """
        SELECT DISTINCT ON (sql_id)
               dbname, instance, sql_id,
               sql_text AS sql_excerpt,
               snap_time
        FROM awr_sql_text
        WHERE sql_id   = %(sql_id)s
          AND dbname   = %(dbname)s
          AND instance = %(instance)s
        ORDER BY sql_id, snap_time DESC
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(sql, {"sql_id": sql_id, "dbname": dbname,
                              "instance": instance})
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"SQL ID search failed: {e}")
        return []
    finally:
        conn.close()


def _search_sql_text(query: str, dbname: str, instance: str) -> list:
    """Full-text SQL search — returns distinct SQL texts matching keyword."""
    sql = """
        SELECT DISTINCT ON (sql_id)
               dbname, instance, sql_id,
               sql_text AS sql_excerpt,
               snap_time
        FROM awr_sql_text
        WHERE sql_text ILIKE %(like_query)s
          AND dbname   = %(dbname)s
          AND instance = %(instance)s
        ORDER BY sql_id, snap_time DESC
        LIMIT 50
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(sql, {"like_query": f"%{query}%",
                              "dbname": dbname, "instance": instance})
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"SQL text search failed: {e}")
        return []
    finally:
        conn.close()


# ── API: queue status JSON (for future JS polling) ────────────────────
@app.get("/api/queue/stats")
async def api_queue_stats():
    """Live queue counter endpoint — polled every 10s by the portal JS."""
    return JSONResponse(_queue_stats())


# ── Service names managed by NSSM ─────────────────────────────────────
_SERVICES = {
    "portal":    "AWRPortal",
    "watcher":   "AWRWatcher",
    "sar":       "SARWatcher",
    "queue":     "AWRQueueProcessor",
    "grafana":   "Grafana",
}

def _service_status(svc_name: str) -> str:
    """Return Running / Stopped / Paused / Unknown via sc query."""
    try:
        result = subprocess.run(
            ["sc", "query", svc_name],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess,'CREATE_NO_WINDOW') else 0
        )
        out = result.stdout.upper()
        if "RUNNING"       in out: return "Running"
        if "STOPPED"       in out: return "Stopped"
        if "PAUSED"        in out: return "Paused"
        if "START_PENDING" in out: return "Starting"
        if "STOP_PENDING"  in out: return "Stopping"
        # Service not found
        if "FAILED" in out or result.returncode == 1060:
            return "Not installed"
        return "Unknown"
    except Exception as e:
        logger.debug(f"_service_status({svc_name}): {e}")
        return "Unknown"


def _run_sc(action: str, svc_name: str) -> tuple:
    """
    Run service control command.
    Returns (success, message).

    Notes:
    - Uses 'net start/stop' for start/stop (more reliable than sc)
    - Pause/resume implemented as stop/start since NSSM doesn't support sc pause
    - Portal restart uses sc to trigger NSSM auto-restart (can't net start itself)
    """
    NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess,'CREATE_NO_WINDOW') else 0

    def run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=30, creationflags=NO_WIN)
        msg = (r.stdout + r.stderr).strip()
        return r.returncode == 0, msg

    try:
        current = _service_status(svc_name)

        if action == "start":
            if current == "Running":
                return True, "Already running"
            return run(["net", "start", svc_name])

        elif action == "stop":
            if current in ("Stopped", "Not installed"):
                return True, "Already stopped"
            return run(["net", "stop", svc_name])

        elif action == "restart":
            if current == "Running":
                ok, msg = run(["net", "stop", svc_name])
                if not ok and "not started" not in msg.lower():
                    return False, f"Stop failed: {msg}"
                import time; time.sleep(3)
            ok, msg = run(["net", "start", svc_name])
            return ok, msg

        elif action in ("pause", "resume"):
            # NSSM services don't support sc pause/continue
            # Return informational message instead of failing silently
            return False, (f"Pause/Resume not supported for NSSM services. "
                          f"Use Stop/Start instead.")

        else:
            return False, f"Unknown action: {action}"

    except subprocess.TimeoutExpired:
        return False, "Command timed out after 30s"
    except Exception as e:
        return False, str(e)[:200]


@app.get("/api/services/status")
async def api_services_status(request: Request):
    """Return status of all portal services via sc query."""
    return JSONResponse({
        key: _service_status(svc)
        for key, svc in _SERVICES.items()
    })


@app.post("/api/services/{action}")
async def api_service_action(action: str, request: Request):
    """
    Control portal services.
    action : start | stop | restart | pause | resume
    Body   : {"service": "all" | "portal" | "watcher" | "sar" | "queue" | "grafana"}

    Notes:
    - Pause/Resume not supported for NSSM services
    - Portal self-restart: stop the service, NSSM auto-restarts after AppRestartDelay
    """
    if not _is_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    valid_actions = {"start", "stop", "restart", "pause", "resume"}
    if action not in valid_actions:
        raise HTTPException(400, f"Invalid action '{action}'")

    body   = await request.json()
    target = body.get("service", "all")
    keys   = list(_SERVICES.keys()) if target == "all" else [target]
    invalid = [k for k in keys if k not in _SERVICES]
    if invalid:
        raise HTTPException(400, f"Unknown service(s): {invalid}")

    # Pause/Resume not supported for NSSM
    if action in ("pause", "resume"):
        return JSONResponse({
            "action":  action,
            "warning": "Pause/Resume is not supported for NSSM services. Use Stop/Start instead.",
            "results": {k: {"ok": False,
                            "msg": "Not supported — use Stop/Start",
                            "status": _service_status(_SERVICES[k])} for k in keys}
        })

    # Portal self-restart — stop service, NSSM auto-restarts it
    if action == "restart" and "portal" in keys:
        import asyncio
        results = {}
        other_keys = [k for k in keys if k != "portal"]
        for key in other_keys:
            ok, msg = _run_sc("restart", _SERVICES[key])
            await asyncio.sleep(0.5)
            results[key] = {"ok": ok, "msg": msg[:200],
                            "status": _service_status(_SERVICES[key])}

        # Portal restart — use schtasks to schedule stop after response is sent
        # NSSM auto-restarts the service after AppRestartDelay
        svc_name = _SERVICES["portal"]
        try:
            task_cmd = f"net stop {svc_name}"
            r = subprocess.run([
                "schtasks", "/Create",
                "/TN",  "AWRPortalRestart",
                "/TR",  task_cmd,
                "/SC",  "ONCE",
                "/ST",  (datetime.now() + __import__('datetime').timedelta(seconds=3)).strftime("%H:%M"),
                "/F", "/RL", "HIGHEST",
            ], capture_output=True, text=True, timeout=10,
               creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess,'CREATE_NO_WINDOW') else 0)

            if r.returncode == 0:
                results["portal"] = {"ok": True,
                                      "msg": "Stopping in ~3s — NSSM auto-restarts",
                                      "status": "Stopping"}
            else:
                # Fallback: PowerShell
                subprocess.Popen(
                    ["powershell", "-NonInteractive", "-WindowStyle", "Hidden",
                     "-Command",
                     f"Start-Job -ScriptBlock {{ Start-Sleep 3; net stop {svc_name} }}"],
                    creationflags=(subprocess.CREATE_NO_WINDOW |
                                   subprocess.CREATE_NEW_PROCESS_GROUP)
                    if hasattr(subprocess,'CREATE_NO_WINDOW') else 0,
                )
                results["portal"] = {"ok": True,
                                      "msg": "Stopping in ~3s via PS — NSSM auto-restarts",
                                      "status": "Stopping"}
        except Exception as e:
            results["portal"] = {"ok": False, "msg": str(e)[:200], "status": "Unknown"}

        return JSONResponse({"action": action, "results": results})

    # Standard start/stop
    import asyncio
    results = {}
    for key in keys:
        ok, msg = _run_sc(action, _SERVICES[key])
        await asyncio.sleep(0.5)
        results[key] = {"ok": ok, "msg": msg[:200],
                        "status": _service_status(_SERVICES[key])}

    return JSONResponse({"action": action, "results": results})


@app.post("/api/cache/clear")
async def api_cache_clear(request: Request):
    """
    Clear Python __pycache__ folders.
    If restart=true, schedules portal restart via Windows Task Scheduler
    (schtasks) — runs 3 seconds after response is sent, fully independent
    of the portal process.
    """
    body = {}
    try:    body = await request.json()
    except: pass
    do_restart = body.get("restart", False)

    # Clear cache
    cleared = []
    errors  = []
    for root, dirs, _ in os.walk(_PROJECT_ROOT):
        for d in dirs:
            if d == "__pycache__":
                path = os.path.join(root, d)
                try:
                    shutil.rmtree(path)
                    cleared.append(path)
                except Exception as e:
                    errors.append(f"{path}: {e}")

    restart_msg = None
    if do_restart:
        svc_name = _SERVICES.get("portal", "AWRPortal")
        try:
            # Create a one-time scheduled task to stop+start the service
            # Task runs 3 seconds from now — well after the HTTP response is sent
            # schtasks /Create is natively available on all Windows versions
            # /F = force overwrite if task exists, /Z = delete after run
            task_cmd = f"net stop {svc_name}"
            result = subprocess.run([
                "schtasks", "/Create",
                "/TN",  "AWRPortalRestart",
                "/TR",  task_cmd,
                "/SC",  "ONCE",
                "/ST",  (datetime.now() + __import__('datetime').timedelta(seconds=3)).strftime("%H:%M"),
                "/F",                    # overwrite if exists
                "/RL", "HIGHEST",        # run with highest privileges
            ], capture_output=True, text=True, timeout=10,
               creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess,'CREATE_NO_WINDOW') else 0)

            if result.returncode == 0:
                restart_msg = "Portal stopping in ~3s — NSSM will auto-restart it"
            else:
                # Fallback: PowerShell Start-Job
                ps_cmd = (
                    f"Start-Job -ScriptBlock {{"
                    f" Start-Sleep 3; "
                    f" net stop {svc_name}"
                    f"}}"
                )
                subprocess.Popen(
                    ["powershell", "-NonInteractive", "-WindowStyle", "Hidden",
                     "-Command", ps_cmd],
                    creationflags=(subprocess.CREATE_NO_WINDOW |
                                   subprocess.CREATE_NEW_PROCESS_GROUP)
                    if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )
                restart_msg = "Portal stopping in ~3s via PowerShell — NSSM will auto-restart"

        except Exception as e:
            errors.append(f"Restart schedule error: {e}")
            restart_msg = f"Cache cleared but restart failed: {e}. Restart portal manually."

    return JSONResponse({
        "cleared":     len(cleared),
        "errors":      errors,
        "restart_msg": restart_msg,
    })


@app.get("/api/queues")
async def api_queues():
    queues = _all_queues()
    summary = {}
    for db, items in queues.items():
        counts = {"PENDING": 0, "PROCESSING": 0, "DONE": 0, "FAILED": 0}
        for i in items:
            counts[i.get("status", "PENDING")] = counts.get(i.get("status", "PENDING"), 0) + 1
        summary[db] = counts
    return JSONResponse(summary)


@app.get("/api/instances")
async def api_instances(dbname: str = ""):
    """Return distinct instances for a DB — used by comparison page."""
    if not dbname:
        return JSONResponse([])
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT instance FROM awr_load_profile
                WHERE dbname = %s ORDER BY instance
            """, (dbname,))
            rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return JSONResponse(rows)
    except Exception as e:
        logger.error(f"api_instances failed: {e}")
        return JSONResponse([])


@app.get("/api/snaps")
async def api_snaps(dbname: str = "", instance: str = ""):
    """
    Return snap list for DB + optional instance.
    Used by comparison page and AI recommendations page.
    """
    if not dbname:
        return JSONResponse([])
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if instance:
                cur.execute("""
                    SELECT DISTINCT begin_snap AS snap_id,
                           to_char(snap_time, 'YYYY-MM-DD HH24:MI') AS snap_time
                    FROM awr_load_profile
                    WHERE dbname=%s AND instance=%s
                    ORDER BY begin_snap DESC
                    LIMIT 200
                """, (dbname, instance))
            else:
                cur.execute("""
                    SELECT DISTINCT begin_snap AS snap_id,
                           to_char(snap_time, 'YYYY-MM-DD HH24:MI') AS snap_time
                    FROM awr_load_profile
                    WHERE dbname=%s
                    ORDER BY begin_snap DESC
                    LIMIT 200
                """, (dbname,))
            rows = [{"snap_id": r[0], "snap_time": r[1],
                     "label": f"{r[0]} — {r[1]}"} for r in cur.fetchall()]
        conn.close()
        return JSONResponse(rows)
    except Exception as e:
        logger.error(f"api_snaps failed: {e}")
        return JSONResponse([])


@app.get("/api/anomalies/{dbname}/{begin_snap}")
async def api_anomalies(dbname: str, begin_snap: int):
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT metric_source, metric_name, object_name,
                       metric_value, baseline_mean, z_score, severity
                FROM awr_anomalies
                WHERE dbname=%s AND begin_snap=%s
                ORDER BY ABS(z_score) DESC
            """, (dbname, begin_snap))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return JSONResponse(rows)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Execution Plan Comparison ─────────────────────────────────────────
import sys as _sys
_plan_module_path = os.path.join(_PROJECT_ROOT, "modules", "plan")
if _plan_module_path not in _sys.path:
    _sys.path.insert(0, _plan_module_path)


def _load_plan_parser():
    """Load plan_parser module from modules/plan/."""
    try:
        from plan_parser import parse_plan, compare_plans
        return parse_plan, compare_plans
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "plan_parser",
            os.path.join(_PROJECT_ROOT, "modules", "plan", "plan_parser.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.parse_plan, mod.compare_plans



@app.get("/api/logs/{log_name}")
async def api_log_viewer(log_name: str, lines: int = 50):
    """Return last N lines of a portal log file."""
    log_files = {
        "portal":      os.path.join(_PROJECT_ROOT, "logs", "portal_stderr.log"),
        "watcher":     os.path.join(_PROJECT_ROOT, "watcher.log"),
        "sar_watcher": os.path.join(_PROJECT_ROOT, "sar_watcher", "sar_watcher.log"),
        "queue":       os.path.join(_PROJECT_ROOT, "queue_processor.log"),
        "anomaly":     os.path.join(_PROJECT_ROOT, "anomaly_detector.log"),
    }
    fpath = log_files.get(log_name)
    if not fpath or not os.path.exists(fpath):
        # Try alternate locations
        for alt in [
            os.path.join(_PROJECT_ROOT, f"{log_name}.log"),
            os.path.join(_PROJECT_ROOT, "logs", f"{log_name}.log"),
        ]:
            if os.path.exists(alt):
                fpath = alt
                break
        else:
            return JSONResponse({"content": f"Log file not found: {log_name}.log"})
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        last_lines = all_lines[-lines:]
        return JSONResponse({"content": "".join(last_lines), "total_lines": len(all_lines)})
    except Exception as e:
        return JSONResponse({"content": f"Error reading log: {e}"})


@app.get("/api/portal-info")
async def api_portal_info(request: Request):
    """Returns AI mode, license limits from key, usage counts and session user for info strip."""
    cfg  = _get_config()
    sess = _get_session(request)

    # Get limits from validated key — not from editable config fields
    lic = _check_license()
    db_limit  = lic.get("db_limit",  5)
    sar_limit = lic.get("sar_limit", 5)
    if db_limit  == -1: db_limit  = 9999  # ENT unlimited — show as large number
    if sar_limit == -1: sar_limit = 9999

    return JSONResponse({
        "ai_mode":           cfg.get("ai_mode", "rules"),
        "license_db_count":  db_limit,
        "license_sar_count": sar_limit,
        "db_used":           lic.get("db_used",  0),
        "sar_used":          lic.get("sar_used", 0),
        "username":          sess.get("username", ""),
    })


@app.get("/api/db-list")
async def api_db_list():
    """Return list of known DB names for dropdowns."""
    return JSONResponse({"dbs": _db_list()})


@app.get("/service-control", response_class=HTMLResponse)
async def service_control_page(request: Request):
    return templates.TemplateResponse(request, "service_control.html", context={
        "page":     "service_control",
        "is_admin": _is_admin(request),
    })


@app.get("/exec-plan", response_class=HTMLResponse)
async def exec_plan_page(request: Request):
    return templates.TemplateResponse(request, "exec_plan.html",
        context={"page": "exec_plan"})


@app.post("/api/plans/compare")
async def api_plans_compare(request: Request):
    """Parse two plans, compare them, save to exec_plan_headers + awr_execution_plans."""
    parse_plan, compare_plans = _load_plan_parser()

    body   = await request.json()
    plan_a = parse_plan(body.get("plan_a", ""))
    plan_b = parse_plan(body.get("plan_b", ""))
    comp   = compare_plans(plan_a, plan_b)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for plan, label, ptype, key in [
                (plan_a, body.get("label_a"), "baseline",  "a"),
                (plan_b, body.get("label_b"), "optimized", "b"),
            ]:
                sql_id  = body.get(f"sql_id_{key}") or plan.sql_id or None
                dbname  = body.get(f"dbname_{key}") or None
                tags    = body.get(f"tags_{key}") or None

                # ── Save header ───────────────────────────────────────
                cur.execute("""
                    INSERT INTO exec_plan_headers
                      (sql_id, dbname, plan_hash_value, plan_label, plan_type,
                       plan_text_raw, sql_text, total_cost, step_count,
                       has_full_scan, has_nested_loop, tags)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    sql_id, dbname,
                    plan.plan_hash or None,
                    label, ptype,
                    body.get(f"plan_{key}"),
                    plan.sql_text or None,
                    plan.total_cost,
                    len(plan.steps),
                    plan.has_full_scan,
                    plan.has_nested_loop,
                    tags,
                ))
                header_id = cur.fetchone()[0]

                # ── Save steps to awr_execution_plans ─────────────────
                for step in plan.steps:
                    cur.execute("""
                        INSERT INTO awr_execution_plans
                          (dbname, sql_id, plan_hash_value, step_id,
                           operation, object_name, cost, cardinality, bytes,
                           filter_predicates, has_full_scan, plan_warning,
                           upload_source, note)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (dbname, sql_id, plan_hash_value, step_id)
                        DO UPDATE SET
                          cost = EXCLUDED.cost,
                          cardinality = EXCLUDED.cardinality,
                          has_full_scan = EXCLUDED.has_full_scan
                    """, (
                        dbname or '',
                        sql_id or '',
                        plan.plan_hash or '',
                        step.step_id,
                        step.operation,
                        step.object_name or None,
                        step.cost,
                        step.rows_est,
                        step.bytes_est,
                        step.predicates or None,
                        step.is_problem and 'FULL' in step.operation.upper(),
                        step.problem_reason or None,
                        'compare',
                        None,
                    ))
        conn.commit()
    finally:
        conn.close()

    def plan_to_dict(p):
        return {
            "sql_id":          p.sql_id,
            "plan_hash":       p.plan_hash,
            "sql_text":        p.sql_text,
            "plan_text":       p.plan_text,
            "total_cost":      p.total_cost,
            "total_rows":      p.total_rows,
            "step_count":      len(p.steps),
            "has_full_scan":   p.has_full_scan,
            "has_nested_loop": p.has_nested_loop,
            "notes":           p.notes,
            "steps": [{
                "step_id":        s.step_id,
                "operation":      s.operation,
                "object_name":    s.object_name,
                "rows_est":       s.rows_est,
                "bytes_est":      s.bytes_est,
                "cost":           s.cost,
                "predicates":     s.predicates,
                "is_problem":     s.is_problem,
                "problem_reason": s.problem_reason,
            } for s in p.steps],
        }

    return JSONResponse({
        "plan_a":     plan_to_dict(plan_a),
        "plan_b":     plan_to_dict(plan_b),
        "comparison": comp,
    })


@app.get("/api/plans/list")
async def api_plans_list(q: str = ""):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if q:
                cur.execute("""
                    SELECT id, plan_label, sql_id, dbname, plan_type,
                           total_cost, step_count, has_full_scan, tags, created_at
                    FROM exec_plan_headers
                    WHERE plan_label ILIKE %s OR sql_id ILIKE %s OR tags ILIKE %s
                    ORDER BY created_at DESC LIMIT 100
                """, (f"%{q}%", f"%{q}%", f"%{q}%"))
            else:
                cur.execute("""
                    SELECT id, plan_label, sql_id, dbname, plan_type,
                           total_cost, step_count, has_full_scan, tags, created_at
                    FROM exec_plan_headers
                    ORDER BY created_at DESC LIMIT 100
                """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
        return JSONResponse({"plans": rows})
    finally:
        conn.close()


@app.get("/api/plans/{plan_id}")
async def api_plan_get(plan_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, plan_label, sql_id, dbname, plan_type,
                       plan_text_raw AS plan_text, sql_text, total_cost,
                       step_count, has_full_scan, has_nested_loop,
                       tags, notes, created_at
                FROM exec_plan_headers WHERE id=%s
            """, (plan_id,))
            cols = [d[0] for d in cur.description]
            row  = cur.fetchone()
            if not row:
                raise HTTPException(404, "Plan not found")
            d = dict(zip(cols, row))
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
        return JSONResponse(d)
    finally:
        conn.close()


@app.delete("/api/plans/{plan_id}")
async def api_plan_delete(plan_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM exec_plan_headers WHERE id=%s", (plan_id,))
        conn.commit()
        return JSONResponse({"deleted": plan_id})
    finally:
        conn.close()


@app.post("/api/plans/compare")
async def api_plans_compare(request: Request):
    """Parse two plans, compare them, save both to DB, return diff."""
    try:
        from plan_parser import parse_plan, compare_plans
    except ImportError:
        # Fallback: load from project root modules path
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "plan_parser",
            os.path.join(_PROJECT_ROOT, "modules", "plan", "plan_parser.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        parse_plan    = mod.parse_plan
        compare_plans = mod.compare_plans

    body = await request.json()

    plan_a = parse_plan(body.get("plan_a", ""))
    plan_b = parse_plan(body.get("plan_b", ""))
    comp   = compare_plans(plan_a, plan_b)

    # Save both plans to DB
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for plan, label, ptype, key in [
                (plan_a, body.get("label_a"), "baseline",  "a"),
                (plan_b, body.get("label_b"), "optimized", "b"),
            ]:
                cur.execute("""
                    INSERT INTO exec_plan_headers
                      (plan_hash, sql_id, dbname, plan_label, plan_type,
                       sql_text, plan_text, total_cost, total_rows, step_count,
                       has_full_scan, has_nested_loop, tags)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    plan.plan_hash or None,
                    body.get(f"sql_id_{key}") or plan.sql_id or None,
                    body.get(f"dbname_{key}") or None,
                    label,
                    ptype,
                    plan.sql_text or None,
                    body.get(f"plan_{key}"),
                    plan.total_cost,
                    plan.total_rows,
                    len(plan.steps),
                    plan.has_full_scan,
                    plan.has_nested_loop,
                    body.get(f"tags_{key}") or None,
                ))
                plan_id = cur.fetchone()[0]

                # Save steps
                for step in plan.steps:
                    cur.execute("""
                        INSERT INTO exec_plan_steps
                          (plan_id, step_id, operation, object_name, rows_est,
                           bytes_est, cost, time_est, predicates, is_problem, problem_reason)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        plan_id, step.step_id, step.operation, step.object_name,
                        step.rows_est, step.bytes_est, step.cost, step.time_est,
                        step.predicates, step.is_problem, step.problem_reason,
                    ))
        conn.commit()
    finally:
        conn.close()

    # Serialise response
    def plan_to_dict(p):
        return {
            "sql_id":       p.sql_id,
            "plan_hash":    p.plan_hash,
            "sql_text":     p.sql_text,
            "plan_text":    p.plan_text,
            "total_cost":   p.total_cost,
            "total_rows":   p.total_rows,
            "step_count":   len(p.steps),
            "has_full_scan": p.has_full_scan,
            "has_nested_loop": p.has_nested_loop,
            "notes":        p.notes,
            "steps": [{
                "step_id":      s.step_id,
                "operation":    s.operation,
                "object_name":  s.object_name,
                "rows_est":     s.rows_est,
                "bytes_est":    s.bytes_est,
                "cost":         s.cost,
                "predicates":   s.predicates,
                "is_problem":   s.is_problem,
                "problem_reason": s.problem_reason,
            } for s in p.steps],
        }

    return JSONResponse({
        "plan_a":     plan_to_dict(plan_a),
        "plan_b":     plan_to_dict(plan_b),
        "comparison": comp,
    })


@app.get("/api/plans/list")
async def api_plans_list(q: str = ""):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if q:
                cur.execute("""
                    SELECT id, plan_label, sql_id, dbname, plan_type,
                           total_cost, step_count, has_full_scan, tags, created_at
                    FROM exec_plan_headers
                    WHERE plan_label ILIKE %s OR sql_id ILIKE %s OR tags ILIKE %s
                    ORDER BY created_at DESC LIMIT 100
                """, (f"%{q}%", f"%{q}%", f"%{q}%"))
            else:
                cur.execute("""
                    SELECT id, plan_label, sql_id, dbname, plan_type,
                           total_cost, step_count, has_full_scan, tags, created_at
                    FROM exec_plan_headers ORDER BY created_at DESC LIMIT 100
                """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
        return JSONResponse({"plans": rows})
    except Exception as e:
        logger.error(f"api_plans_list error: {e}")
        return JSONResponse({"error": str(e), "plans": []}, status_code=500)
    finally:
        conn.close()


@app.get("/api/plans/{plan_id}")
async def api_plan_get(plan_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, plan_label, sql_id, dbname, plan_type, plan_text_raw AS plan_text, sql_text, total_cost, step_count, has_full_scan, has_nested_loop, tags, notes, created_at FROM exec_plan_headers WHERE id=%s", (plan_id,))
            cols = [d[0] for d in cur.description]
            row  = cur.fetchone()
            if not row:
                raise HTTPException(404, "Plan not found")
            d = dict(zip(cols, row))
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
        return JSONResponse(d)
    finally:
        conn.close()


@app.delete("/api/plans/{plan_id}")
async def api_plan_delete(plan_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM exec_plan_headers WHERE id=%s", (plan_id,))
        conn.commit()
        return JSONResponse({"deleted": plan_id})
    finally:
        conn.close()


# ── Settings, Auth & User Management ─────────────────────────────────

def _get_config(section: str = None) -> dict:
    """Load all config keys from portal_config, optionally filtered by section."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if section:
                cur.execute("SELECT key, value FROM portal_config WHERE section=%s", (section,))
            else:
                cur.execute("SELECT key, value FROM portal_config")
            return {r[0]: (r[1] or '') for r in cur.fetchall()}
    finally:
        conn.close()


def _set_config(fields: dict, updated_by: str = 'admin'):
    """Upsert config keys."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for key, value in fields.items():
                cur.execute("""
                    INSERT INTO portal_config (key, value, updated_by, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                      SET value=EXCLUDED.value,
                          updated_by=EXCLUDED.updated_by,
                          updated_at=NOW()
                """, (key, str(value), updated_by))
        conn.commit()
    finally:
        conn.close()


def _hash_password(password: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
    except ImportError:
        # Fallback: sha256 (install bcrypt for production)
        return 'sha256:' + hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, hashed: str) -> bool:
    try:
        import bcrypt
        if hashed.startswith('sha256:'):
            return 'sha256:' + hashlib.sha256(password.encode()).hexdigest() == hashed
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ImportError:
        return 'sha256:' + hashlib.sha256(password.encode()).hexdigest() == hashed


# In-memory session store {token: {username, role, expires}}
_sessions: dict = {}


def _get_session(request: Request) -> dict:
    token = request.cookies.get('portal_session')
    if not token:
        return {}
    sess = _sessions.get(token, {})
    if sess and sess.get('expires') and datetime.now() > sess['expires']:
        _sessions.pop(token, None)
        return {}
    return sess


def _is_admin(request: Request) -> bool:
    cfg = _get_config('access')
    if cfg.get('portal_login_required', 'false') != 'true':
        return True  # login not required — treat as admin for settings
    return _get_session(request).get('role') == 'admin'


def _get_mac() -> str:
    try:
        import uuid
        mac = uuid.getnode()
        return ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
    except Exception:
        return 'N/A'


def _get_license_usage() -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT dbname) FROM awr_load_profile")
            db_used = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(DISTINCT hostname) FROM sar_cpu_stats")
            sar_used = cur.fetchone()[0] or 0
        return {"db_used": db_used, "sar_used": sar_used}
    finally:
        conn.close()


def _check_license() -> dict:
    """Full license status using license_engine."""
    try:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, "modules"))
        from license_engine import get_license_status
        conn = get_db_connection()
        result = get_license_status(conn)
        conn.close()
        return result
    except Exception as e:
        logger.warning(f"License check failed: {e}")
        return {
            "status": "error", "status_msg": f"License check error: {e}",
            "valid": False, "allow_parse": True, "allow_grafana": True,
            "allow_ai_new": True, "allow_ai_past": True,
            "db_used": 0, "sar_used": 0, "db_limit": 5, "sar_limit": 5,
            "days_left": -1, "tier": "", "tier_name": "",
            "mac_address": "", "ai_monthly_used": 0, "ai_monthly_limit": 200,
        }


def _get_users() -> list:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT username, full_name, role, last_login, active
                FROM portal_users ORDER BY role, username
            """)
            cols = ['username','full_name','role','last_login','active']
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


# ── Login / Logout ────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    cfg  = _get_config()
    next_url = request.query_params.get("next", "/")
    return templates.TemplateResponse(request, "login.html",
        context={"error": None, "username": None,
                 "next": next_url,
                 "ai_mode": cfg.get("ai_mode", "rules")})


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request,
                     username: str  = Form(...),
                     password: str  = Form(...),
                     next_url: str  = Form(default="/")):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT password_hash, role, active FROM portal_users
                WHERE username=%s
            """, (username,))
            row = cur.fetchone()
        if not row or not row[2]:
            raise ValueError("Invalid username or password")
        if not _verify_password(password, row[0]):
            raise ValueError("Invalid username or password")
        with conn.cursor() as cur:
            cur.execute("UPDATE portal_users SET last_login=NOW() WHERE username=%s", (username,))
        conn.commit()
    except ValueError as e:
        cfg = _get_config()
        return templates.TemplateResponse(request, "login.html",
            context={"error": str(e), "username": username,
                     "next": next_url,
                     "ai_mode": cfg.get("ai_mode","rules")})
    finally:
        conn.close()

    token   = secrets.token_hex(32)
    cfg     = _get_config('access')
    timeout = int(cfg.get('session_timeout_mins', 30))   # default 30 mins
    expires = datetime.now() + timedelta(minutes=timeout) if timeout > 0 else None
    _sessions[token] = {"username": username, "role": row[1], "expires": expires}

    # Invalidate login cache so middleware re-checks
    app.state._login_cache_ts = 0

    resp = RedirectResponse(next_url or "/", status_code=303)
    resp.set_cookie("portal_session", token, httponly=True,
                    max_age=timeout * 60 if timeout > 0 else None)
    return resp


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html",
        context={"msg": None, "error": None})


@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_post(request: Request,
                                username: str  = Form(...),
                                new_password:str = Form(...),
                                admin_key: str = Form(...)):
    """
    Password reset using admin PIN stored in portal_config.
    Default PIN is AWR@2024 — change it in Settings → Access Control.
    """
    conn = get_db_connection()
    try:
        # Get stored admin reset PIN
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM portal_config WHERE key='admin_reset_pin'")
            row = cur.fetchone()
            stored_pin = row[0] if row else 'AWR@2024'

        if admin_key.strip() != stored_pin:
            return templates.TemplateResponse(request, "forgot_password.html",
                context={"msg": None,
                         "error": "Invalid admin key. Default is AWR@2024 — check Settings → Access Control for the current PIN."})
        if len(new_password) < 8:
            return templates.TemplateResponse(request, "forgot_password.html",
                context={"msg": None, "error": "Password must be at least 8 characters"})
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM portal_users WHERE username=%s", (username,))
            if not cur.fetchone():
                return templates.TemplateResponse(request, "forgot_password.html",
                    context={"msg": None, "error": f"User '{username}' not found"})
            cur.execute("UPDATE portal_users SET password_hash=%s WHERE username=%s",
                        (_hash_password(new_password), username))
        conn.commit()
    finally:
        conn.close()
    return templates.TemplateResponse(request, "forgot_password.html",
        context={"msg": f"Password for '{username}' has been reset. You can now log in.",
                 "error": None})


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get('portal_session')
    _sessions.pop(token, None)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("portal_session")
    return resp


# ── Settings page ─────────────────────────────────────────────────────
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg     = _get_config()
    license = _get_license_usage()
    users   = _get_users()
    return templates.TemplateResponse(request, "settings.html", context={
        "page":       "settings",
        "cfg":        cfg,
        "license":    license,
        "users":      users,
        "mac_address": _get_mac(),
        "is_admin":   _is_admin(request),
    })


@app.post("/api/settings/save")
async def api_settings_save(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required")
    body       = await request.json()
    fields     = body.get("fields", {})
    session    = _get_session(request)
    updated_by = session.get("username", "admin")
    _set_config(fields, updated_by)
    _refresh_url_globals()

    # Auto-patch Grafana dashboards if URL config changed
    if "portal_url" in fields or "grafana_url" in fields:
        import threading
        threading.Thread(
            target=_auto_patch_grafana_dashboards,
            daemon=True
        ).start()
        logger.info("Settings saved — Grafana dashboard URLs will be updated automatically")

    # If a license key was saved, extract and store tier + counts from the key
    if "license_key" in fields and fields["license_key"].strip():
        try:
            from modules.license_engine import validate_license_key
            ki = validate_license_key(fields["license_key"].strip())
            if ki.get("valid") or ki.get("tier"):
                derived = {
                    "license_tier":      ki.get("tier", ""),
                    "license_db_count":  str(ki.get("db_limit", "")),
                    "license_sar_count": str(ki.get("sar_limit", "")),
                    "license_expiry":    ki.get("expiry").isoformat()
                                         if ki.get("expiry") else "",
                }
                # Customer name is entered manually in the UI (not in key payload)
                # Keep existing value if not provided in this save
                if fields.get("license_customer", "").strip():
                    derived["license_customer"] = fields["license_customer"].strip()
                _set_config(derived, updated_by)
        except Exception as e:
            logger.debug(f"License key extraction on save: {e}")

    return JSONResponse({"ok": True})


@app.post("/api/settings/test-ollama")
async def api_test_ollama(request: Request):
    body  = await request.json()
    url   = body.get("url", "http://localhost:11434")
    model = body.get("model", "llama3.1")
    try:
        import urllib.request
        req  = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        models = [m['name'] for m in data.get('models', [])]
        if not any(model in m for m in models):
            return JSONResponse({"ok": False,
                "error": f"Model '{model}' not found. Available: {', '.join(models[:5])}"})
        return JSONResponse({"ok": True, "model": model, "available": models})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/settings/test-cloud-ai")
async def api_test_cloud_ai(request: Request):
    body     = await request.json()
    provider = body.get("provider", "claude")
    api_key  = body.get("api_key", "")
    model    = body.get("model", "")
    if not api_key:
        return JSONResponse({"ok": False, "error": "API key is required"})
    try:
        import urllib.request, urllib.error
        if provider == "claude":
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                method="POST",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                data=json.dumps({"model": model or "claude-haiku-4-5",
                                 "max_tokens": 10,
                                 "messages": [{"role":"user","content":"ping"}]}).encode()
            )
        elif provider == "openai":
            req = urllib.request.Request(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"}
            )
        elif provider == "gemini":
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
            )
        else:
            return JSONResponse({"ok": False, "error": f"Provider '{provider}' test not implemented"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        return JSONResponse({"ok": True})
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
            err_msg  = err_body.get("error", {}).get("message", str(e))
        except Exception:
            err_msg = str(e)
        # Credit balance error = API key is valid, just needs funding
        if "credit balance" in err_msg.lower() or "billing" in err_msg.lower():
            return JSONResponse({"ok": True,
                "warning": "✅ API key is valid but account has no credits. "
                           "Add credits at https://console.anthropic.com/billing"})
        return JSONResponse({"ok": False, "error": f"HTTP {e.code}: {err_msg}"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]})


# ── User management ───────────────────────────────────────────────────
@app.post("/api/settings/users")
async def api_save_user(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required")
    body     = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    role     = body.get("role", "viewer")
    fullname = body.get("full_name", "").strip()
    if not username:
        raise HTTPException(400, "Username is required")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM portal_users WHERE username=%s", (username,))
            existing = cur.fetchone()
            if existing:
                # Update
                if password:
                    cur.execute("""
                        UPDATE portal_users SET role=%s, full_name=%s, password_hash=%s
                        WHERE username=%s
                    """, (role, fullname, _hash_password(password), username))
                else:
                    cur.execute("""
                        UPDATE portal_users SET role=%s, full_name=%s WHERE username=%s
                    """, (role, fullname, username))
            else:
                if not password:
                    raise HTTPException(400, "Password is required for new users")
                cur.execute("""
                    INSERT INTO portal_users (username, password_hash, role, full_name)
                    VALUES (%s, %s, %s, %s)
                """, (username, _hash_password(password), role, fullname))
        conn.commit()
        return JSONResponse({"ok": True})
    finally:
        conn.close()


@app.post("/api/settings/change-password")
async def api_change_password(request: Request):
    body     = await request.json()
    curr_pwd = body.get("current_password", "")
    new_pwd  = body.get("new_password", "")
    sess     = _get_session(request)
    username = sess.get("username")
    cfg      = _get_config('access')
    if cfg.get('portal_login_required','false') != 'true':
        raise HTTPException(400, "Login not enabled — password change not applicable")
    if not username:
        raise HTTPException(401, "Not logged in")
    if len(new_pwd) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM portal_users WHERE username=%s", (username,))
            row = cur.fetchone()
            if not row or not _verify_password(curr_pwd, row[0]):
                raise HTTPException(400, "Current password is incorrect")
            cur.execute("UPDATE portal_users SET password_hash=%s WHERE username=%s",
                        (_hash_password(new_pwd), username))
        conn.commit()
        return JSONResponse({"ok": True})
    finally:
        conn.close()


# ── AI Recommendation & Object Metadata Routes ───────────────────────

@app.get("/api/license/mac-debug")
async def api_mac_debug():
    """Debug endpoint — shows exactly what MAC validation sees."""
    try:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, "modules"))
        from license_engine import get_mac_address, get_all_physical_macs
        import psutil, uuid as _uuid

        primary = get_mac_address()
        all_macs = get_all_physical_macs()

        # Also get raw uuid.getnode() MAC
        node_mac = _uuid.UUID(int=_uuid.getnode()).hex[-12:]
        node_mac_fmt = ":".join(node_mac[i:i+2] for i in range(0,12,2)).upper()

        # Get all psutil MACs raw
        raw_macs = []
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                s = a.address or ""
                if (":" in s or "-" in s) and len(s) in (17,14):
                    raw_macs.append({"iface": name, "mac": s.upper()})

        return JSONResponse({
            "primary_mac":    primary,
            "primary_stripped": primary.replace(":","").upper(),
            "uuid_node_mac":  node_mac_fmt,
            "all_physical":   all_macs,
            "all_raw_psutil": raw_macs,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)})


@app.get("/api/license/mac-info")
async def api_mac_info():
    """Return MAC address info for all physical adapters."""
    try:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, "modules"))
        from license_engine import get_mac_address, get_all_physical_macs, get_machine_fingerprint
        return JSONResponse({
            "primary_mac":   get_mac_address(),
            "fingerprint":   get_machine_fingerprint(),
            "all_adapters":  get_all_physical_macs(),
        })
    except Exception as e:
        return JSONResponse({"primary_mac": "", "fingerprint": "", "all_adapters": [], "error": str(e)})


# ── DB Master CRUD ───────────────────────────────────────────────────

@app.get("/api/db-master")
async def api_db_master_list():
    """Return all entries in awr_db_master with license slot info."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Get db_limit from license key
            cur.execute("SELECT value FROM portal_config WHERE key='license_key'")
            row      = cur.fetchone()
            key_info = {}
            if row and row[0]:
                from modules.license_engine import validate_license_key
                key_info = validate_license_key(row[0])
            db_limit = key_info.get("db_limit", 5)
            if db_limit == -1:
                db_limit = 9999

            cur.execute("""
                SELECT id, db_name, instance_name, inst_no,
                       host_name, db_type, description, active,
                       added_at, added_by
                FROM awr_db_master
                ORDER BY added_at
            """)
            rows = cur.fetchall()
        conn.close()

        dbs = []
        for r in rows:
            dbs.append({
                "id":            r[0],
                "db_name":       r[1],
                "instance_name": r[2] or "",
                "inst_no":       r[3] or 1,
                "host_name":     r[4] or "",
                "db_type":       r[5] or "STANDALONE",
                "description":   r[6] or "",
                "active":        r[7],
                "added_at":      r[8].isoformat() if r[8] else "",
                "added_by":      r[9] or "",
            })

        active_count = sum(1 for d in dbs if d["active"])
        return JSONResponse({
            "dbs":          dbs,
            "db_limit":     db_limit,
            "active_count": active_count,
            "slots_free":   max(0, db_limit - active_count),
        })
    except Exception as e:
        return JSONResponse({"error": str(e), "dbs": [], "db_limit": 0,
                             "active_count": 0, "slots_free": 0})


@app.post("/api/db-master/add")
async def api_db_master_add(request: Request):
    """Add a DB to awr_db_master."""
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required")
    body = await request.json()
    db_name       = (body.get("db_name") or "").strip().upper()
    instance_name = (body.get("instance_name") or "").strip()
    inst_no       = int(body.get("inst_no") or 1)
    host_name     = (body.get("host_name") or "").strip()
    db_type       = (body.get("db_type") or "STANDALONE").strip().upper()
    description   = (body.get("description") or "").strip()
    session       = _get_session(request)
    added_by      = session.get("username", "admin")

    if not db_name:
        raise HTTPException(400, "db_name is required")

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Check license slot availability
            cur.execute("SELECT value FROM portal_config WHERE key='license_key'")
            row = cur.fetchone()
            if row and row[0]:
                from modules.license_engine import validate_license_key
                ki = validate_license_key(row[0])
                db_limit = ki.get("db_limit", 5)
                if db_limit != -1:
                    cur.execute(
                        "SELECT COUNT(*) FROM awr_db_master WHERE active = TRUE"
                    )
                    active_count = cur.fetchone()[0]
                    if active_count >= db_limit:
                        conn.close()
                        return JSONResponse({
                            "ok": False,
                            "error": f"License allows {db_limit} DB(s). "
                                     f"All {db_limit} slot(s) are used. "
                                     f"Upgrade license to add more databases."
                        }, status_code=400)

            cur.execute("""
                INSERT INTO awr_db_master
                    (db_name, instance_name, inst_no, host_name,
                     db_type, description, active, added_by)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
                ON CONFLICT (db_name, inst_no)
                DO UPDATE SET
                    instance_name = EXCLUDED.instance_name,
                    host_name     = EXCLUDED.host_name,
                    db_type       = EXCLUDED.db_type,
                    description   = EXCLUDED.description,
                    active        = TRUE,
                    added_by      = EXCLUDED.added_by
                RETURNING id
            """, (db_name, instance_name, inst_no, host_name,
                  db_type, description, added_by))
            new_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return JSONResponse({"ok": True, "id": new_id})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/db-master/toggle")
async def api_db_master_toggle(request: Request):
    """Toggle active/inactive for a DB entry."""
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required")
    body    = await request.json()
    db_id   = int(body.get("id", 0))
    active  = bool(body.get("active", True))
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE awr_db_master SET active=%s WHERE id=%s",
                (active, db_id)
            )
        conn.commit()
        conn.close()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.delete("/api/db-master/{db_id}")
async def api_db_master_delete(db_id: int, request: Request):
    """Remove a DB from awr_db_master."""
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required")
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM awr_db_master WHERE id=%s", (db_id,))
        conn.commit()
        conn.close()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/license/status")
async def api_license_status():
    """Return full license validation status from license engine."""
    lic = _check_license()
    # Serialise date objects
    if lic.get("expiry") and not isinstance(lic["expiry"], str):
        lic["expiry"] = lic["expiry"].isoformat()
    # customer_name is stored in portal_config (not in binary key payload)
    # Add it to the response so the settings page displays correctly
    if not lic.get("customer_name"):
        try:
            cfg = _get_config()
            lic["customer_name"] = cfg.get("license_customer", "")
        except Exception:
            pass
    return JSONResponse(lic)


@app.get("/api/instance-list")
async def api_instance_list(dbname: str = ""):
    """Return instances for a given database."""
    if not dbname:
        return JSONResponse({"instances": []})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT instance FROM awr_load_profile
                WHERE dbname=%s ORDER BY instance
            """, (dbname,))
            instances = [r[0] for r in cur.fetchall()]
        return JSONResponse({"instances": instances})
    finally:
        conn.close()


@app.get("/api/ai/focus-values")
async def api_focus_values(dbname: str = "", instance: str = "",
                            begin_snap: int = 0, end_snap: int = 0,
                            focus_type: str = "wait"):
    """Return dropdown values for AI focus value based on snap range."""
    if not dbname or not begin_snap:
        return JSONResponse({"values": []})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if focus_type == "wait":
                cur.execute("""
                    SELECT DISTINCT event,
                           ROUND(AVG(pct_time)::numeric,1) AS pct
                    FROM awr_wait_summary_mv
                    WHERE dbname=%s AND begin_snap BETWEEN %s AND %s
                    GROUP BY event ORDER BY pct DESC LIMIT 30
                """, (dbname, begin_snap, end_snap or begin_snap))
                values = [{"value": r[0], "label": f"{r[0]} ({r[1]}% DB time)"}
                          for r in cur.fetchall()]
            elif focus_type == "sql":
                cur.execute("""
                    SELECT DISTINCT sql_id,
                           ROUND(SUM(elapsed_time_s)::numeric,1) AS elapsed
                    FROM awr_sql_elapsed_time
                    WHERE dbname=%s AND begin_snap BETWEEN %s AND %s
                    GROUP BY sql_id ORDER BY elapsed DESC LIMIT 30
                """, (dbname, begin_snap, end_snap or begin_snap))
                values = [{"value": r[0], "label": f"{r[0]} (elapsed {r[1]}s)"}
                          for r in cur.fetchall()]
            elif focus_type == "segment":
                cur.execute("""
                    SELECT DISTINCT object_name, owner,
                           SUM(logical_reads) AS lr
                    FROM awr_seg_logical_reads
                    WHERE dbname=%s AND begin_snap BETWEEN %s AND %s
                    GROUP BY object_name, owner ORDER BY lr DESC LIMIT 30
                """, (dbname, begin_snap, end_snap or begin_snap))
                values = [{"value": r[0],
                           "label": f"{r[1]}.{r[0]} ({int(r[2] or 0):,} LR)"}
                          for r in cur.fetchall()]
            else:
                values = []
        conn.close()
        return JSONResponse({"values": values})
    except Exception as e:
        logger.error(f"focus_values failed: {e}")
        return JSONResponse({"values": []})


@app.get("/ai-recommendations", response_class=HTMLResponse)
async def ai_recommendations_page(request: Request):
    cfg = _get_config("ai")
    return templates.TemplateResponse(request, "ai_recommendations.html",
        context={"page": "ai_rec", "ai_mode": cfg.get("ai_mode", "rules"),
                 "is_admin": _is_admin(request)})


@app.get("/metadata", response_class=HTMLResponse)
async def metadata_page(request: Request):
    return templates.TemplateResponse(request, "metadata.html",
        context={"page": "metadata", "is_admin": _is_admin(request)})


@app.post("/api/metadata/upload")
async def api_metadata_upload(request: Request,
                               dbname: str = Form(...),
                               csv_file: UploadFile = File(...)):
    """Upload object metadata CSV exported from Oracle."""
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required")
    import csv, io

    content_bytes = await csv_file.read()
    text = content_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter="|")

    inserted = 0
    updated  = 0
    errors   = 0

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for row in reader:
                try:
                    cur.execute("""
                        INSERT INTO awr_object_metadata
                          (dbname, owner, object_name, object_type,
                           num_rows, blocks, avg_row_len, last_analyzed,
                           partitioned, compression, index_type, uniqueness,
                           blevel, leaf_blocks, distinct_keys, clustering_factor,
                           status, index_columns, partition_type, partition_count, source)
                        VALUES (%s,%s,%s,%s,
                                %s,%s,%s,%s,
                                %s,%s,%s,%s,
                                %s,%s,%s,%s,
                                %s,%s,%s,%s,'csv')
                        ON CONFLICT (dbname, owner, object_name, object_type)
                        DO UPDATE SET
                          num_rows           = EXCLUDED.num_rows,
                          blocks             = EXCLUDED.blocks,
                          last_analyzed      = EXCLUDED.last_analyzed,
                          blevel             = EXCLUDED.blevel,
                          distinct_keys      = EXCLUDED.distinct_keys,
                          clustering_factor  = EXCLUDED.clustering_factor,
                          index_columns      = EXCLUDED.index_columns,
                          partition_count    = EXCLUDED.partition_count,
                          uploaded_at        = NOW()
                    """, (
                        dbname or row.get("dbname",""),
                        row.get("owner",""),
                        row.get("object_name",""),
                        row.get("object_type",""),
                        row.get("num_rows") or None,
                        row.get("blocks") or None,
                        row.get("avg_row_len") or None,
                        row.get("last_analyzed") or None,
                        row.get("partitioned") or None,
                        row.get("compression") or None,
                        row.get("index_type") or None,
                        row.get("uniqueness") or None,
                        row.get("blevel") or None,
                        row.get("leaf_blocks") or None,
                        row.get("distinct_keys") or None,
                        row.get("clustering_factor") or None,
                        row.get("status") or None,
                        row.get("index_columns") or None,
                        row.get("partition_type") or None,
                        row.get("partition_count") or None,
                    ))
                    inserted += 1
                except Exception as e:
                    errors += 1
                    logger.debug(f"Metadata row error: {e}")
        conn.commit()
    finally:
        conn.close()

    return JSONResponse({
        "ok": True,
        "inserted": inserted,
        "errors": errors,
        "message": f"Processed {inserted} objects ({errors} errors)",
    })


@app.get("/api/metadata/refresh-status")
async def api_metadata_refresh_status():
    """
    Returns metadata refresh status for all DBs.
    Compares last upload date against configured refresh frequency.
    """
    conn = get_db_connection()
    try:
        cfg       = _get_config()
        freq_days = int(cfg.get("metadata_refresh_days", 14))

        with conn.cursor() as cur:
            cur.execute("""
                SELECT dbname, MAX(uploaded_at) AS last_upload,
                       COUNT(*) AS object_count
                FROM awr_object_metadata
                GROUP BY dbname ORDER BY dbname
            """)
            uploaded = {r[0]: {"last_upload": r[1], "object_count": r[2]}
                        for r in cur.fetchall()}
            cur.execute("SELECT DISTINCT dbname FROM awr_load_profile ORDER BY dbname")
            all_dbs = [r[0] for r in cur.fetchall()]

        from datetime import datetime, timedelta
        now      = datetime.now()
        due_date = now - timedelta(days=freq_days)
        alerts   = []

        for db in all_dbs:
            info = uploaded.get(db)
            if not info:
                alerts.append({"dbname": db, "status": "missing",
                    "last_upload": None, "days_old": None,
                    "object_count": 0,
                    "message": f"No metadata uploaded for {db}"})
            else:
                last     = info["last_upload"]
                days_old = (now - last).days if last else None
                if last and last < due_date:
                    alerts.append({"dbname": db, "status": "overdue",
                        "last_upload": last.strftime("%d-%b-%Y"),
                        "days_old": days_old,
                        "object_count": info["object_count"],
                        "message": f"{db}: metadata is {days_old} days old"})
                else:
                    alerts.append({"dbname": db, "status": "ok",
                        "last_upload": last.strftime("%d-%b-%Y") if last else None,
                        "days_old": days_old,
                        "object_count": info["object_count"],
                        "message": f"{db}: metadata is up to date"})

        overdue = [a for a in alerts if a["status"] in ("missing","overdue")]
        return JSONResponse({
            "alerts":        alerts,
            "overdue_count": len(overdue),
            "freq_days":     freq_days,
            "freq_label":    {7:"Weekly",14:"Fortnightly",30:"Monthly"}.get(
                              freq_days, f"Every {freq_days} days"),
        })
    finally:
        conn.close()


@app.get("/api/metadata/summary")
async def api_metadata_summary(dbname: str = ""):
    """Return object metadata counts per database."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if dbname:
                cur.execute("""
                    SELECT object_type, COUNT(*) AS cnt,
                           MAX(uploaded_at) AS last_upload
                    FROM awr_object_metadata
                    WHERE dbname=%s
                    GROUP BY object_type ORDER BY object_type
                """, (dbname,))
            else:
                cur.execute("""
                    SELECT dbname, object_type, COUNT(*) AS cnt,
                           MAX(uploaded_at) AS last_upload
                    FROM awr_object_metadata
                    GROUP BY dbname, object_type
                    ORDER BY dbname, object_type
                """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("last_upload"):
                    r["last_upload"] = r["last_upload"].isoformat()
        return JSONResponse({"summary": rows})
    finally:
        conn.close()


@app.post("/api/ai/recommend")
async def api_ai_recommend(request: Request):
    """Generate AI recommendation for a specific trigger."""
    body          = await request.json()
    dbname        = body.get("dbname","")
    instance      = body.get("instance","")
    begin_snap    = int(body.get("begin_snap", 0))
    end_snap      = int(body.get("end_snap", 0))
    trigger_type  = body.get("trigger_type","overall")
    trigger_value = body.get("trigger_value","")
    severity      = body.get("severity","medium")
    dba_feedback  = body.get("dba_feedback","")

    cfg       = _get_config("ai")
    ai_mode   = cfg.get("ai_mode","rules")

    if ai_mode == "rules":
        return JSONResponse({"ok": False,
            "error": "AI mode is set to Rules. Go to Settings → AI to enable Local AI or Cloud AI."})

    # ── Build AWR metrics context from DB ─────────────────────────────
    metrics = {}
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Top waits
            cur.execute("""
                SELECT event, ROUND(AVG(pct_time)::numeric,2) AS pct
                FROM awr_wait_summary_mv
                WHERE dbname=%s AND begin_snap BETWEEN %s AND %s
                GROUP BY event ORDER BY pct DESC LIMIT 8
            """, (dbname, begin_snap, end_snap))
            metrics["top_waits"] = [{"event":r[0],"pct_db_time":float(r[1])} for r in cur.fetchall()]

            # Top SQL - join elapsed + cpu tables
            cur.execute("""
                SELECT e.sql_id,
                       ROUND(SUM(e.elapsed_time_s)::numeric,2) AS elapsed,
                       SUM(e.executions) AS execs,
                       ROUND(COALESCE(SUM(c.cpu_time_s),0)::numeric,2) AS cpu
                FROM awr_sql_elapsed_time e
                LEFT JOIN awr_sql_cpu_time c
                  ON e.dbname=c.dbname AND e.begin_snap=c.begin_snap AND e.sql_id=c.sql_id
                WHERE e.dbname=%s AND e.begin_snap BETWEEN %s AND %s
                GROUP BY e.sql_id ORDER BY elapsed DESC LIMIT 5
            """, (dbname, begin_snap, end_snap))
            metrics["top_sql"] = [{"sql_id":r[0],"elapsed_s":float(r[1] or 0),
                                   "executions":int(r[2] or 0),"cpu_s":float(r[3] or 0)}
                                  for r in cur.fetchall()]

            # Top segments
            cur.execute("""
                SELECT owner, object_name, obj_type,
                       SUM(logical_reads) AS lr, SUM(physical_reads) AS pr
                FROM awr_seg_logical_reads
                WHERE dbname=%s AND begin_snap BETWEEN %s AND %s
                GROUP BY owner, object_name, obj_type
                ORDER BY lr DESC LIMIT 5
            """, (dbname, begin_snap, end_snap))
            metrics["top_segments"] = [{"owner":r[0],"object_name":r[1],"obj_type":r[2],
                                         "logical_reads":int(r[3] or 0),
                                         "physical_reads":int(r[4] or 0)}
                                        for r in cur.fetchall()]

            # Snap info
            cur.execute("""
                SELECT MIN(snap_time), MAX(snap_time)
                FROM awr_load_profile
                WHERE dbname=%s AND begin_snap BETWEEN %s AND %s
            """, (dbname, begin_snap, end_snap))
            row = cur.fetchone()
            metrics["snap_info"] = {
                "begin_snap": begin_snap, "end_snap": end_snap,
                "begin_time": str(row[0])[:16] if row and row[0] else "",
                "end_time":   str(row[1])[:16] if row and row[1] else "",
                "db_time_s":  0,
            }
        conn.close()
    except Exception as e:
        logger.warning(f"AI metrics fetch failed: {e}")

    # ── Pre-analyse metrics before building prompt ────────────────────
    snap      = metrics.get("snap_info",{})
    top_waits = metrics.get("top_waits",[])
    top_sql   = metrics.get("top_sql",[])
    top_segs  = metrics.get("top_segments",[])

    # Pre-compute derived metrics — tell the AI what numbers mean
    sql_analysis = []
    for s in top_sql:
        elapsed  = float(s.get("elapsed_s",0))
        execs    = int(s.get("executions",0))
        cpu      = float(s.get("cpu_s",0))
        avg_ms   = round(elapsed / max(execs,1) * 1000, 3)
        cpu_pct  = round(cpu / max(elapsed,0.001) * 100, 1)
        # Classify performance
        if execs > 0 and avg_ms < 10:
            perf = "GOOD (< 10ms avg — likely not a problem)"
        elif execs > 0 and avg_ms < 100:
            perf = "ACCEPTABLE (< 100ms avg)"
        elif execs > 0 and avg_ms < 1000:
            perf = "SLOW (100ms-1s avg — investigate)"
        elif execs > 0:
            perf = "VERY SLOW (> 1s avg — high priority)"
        else:
            perf = "NO EXECUTIONS — possibly cached plan or parse-only"
        sql_analysis.append({
            **s,
            "avg_ms":  avg_ms,
            "cpu_pct": cpu_pct,
            "perf":    perf,
        })

    seg_analysis = []
    for s in top_segs:
        lr = int(s.get("logical_reads",0))
        pr = int(s.get("physical_reads",0))
        cache_hit = round((1 - pr/max(lr,1))*100, 1)
        seg_analysis.append({**s, "cache_hit_pct": cache_hit})

    # Build text blocks with pre-computed analysis
    waits_txt = "\n".join(
        f"  {i+1}. {w['event']}: {w['pct_db_time']}% DB time"
        for i,w in enumerate(top_waits)
    ) or "  No wait data available"

    sql_txt = "\n".join(
        f"  {i+1}. SQL_ID={s['sql_id']}: total_elapsed={s['elapsed_s']}s, "
        f"executions={s['executions']}, avg_per_exec={s['avg_ms']}ms, "
        f"cpu={s['cpu_s']}s ({s['cpu_pct']}% of elapsed), "
        f"ASSESSMENT={s['perf']}"
        for i,s in enumerate(sql_analysis)
    ) or "  No SQL data"

    seg_txt = "\n".join(
        f"  {i+1}. {s['owner']}.{s['object_name']} [{s['obj_type']}]: "
        f"logical_reads={s['logical_reads']:,}, physical_reads={s['physical_reads']:,}, "
        f"buffer_cache_hit={s['cache_hit_pct']}%"
        for i,s in enumerate(seg_analysis)
    ) or "  No segment data"

    # ── Focus-specific prompt ─────────────────────────────────────────
    if trigger_type == "sql" and trigger_value:
        # Find this SQL in pre-analysed list
        focused_sql = next((s for s in sql_analysis
                           if s['sql_id'] == trigger_value), None)
        if focused_sql:
            sql_note = (
                f"\nIMPORTANT: SQL_ID '{trigger_value}' has avg_per_exec="
                f"{focused_sql['avg_ms']}ms with {focused_sql['executions']} executions. "
                f"Assessment: {focused_sql['perf']}. "
                f"If avg_per_exec < 50ms this SQL is NOT the problem — say so clearly "
                f"and redirect analysis to the actual bottleneck."
            )
        else:
            sql_note = f"\nSQL_ID '{trigger_value}' not in top SQL list — may be a parse-only or idle query."
        focus_instruction = (
            f"TASK: Analyse SQL_ID '{trigger_value}' specifically.{sql_note}\n"
            f"- If the SQL is performing well (< 50ms avg), explicitly state this and explain "
            f"  what the actual bottleneck is instead.\n"
            f"- If the SQL is slow, identify whether the cause is: I/O waits, CPU, "
            f"  parse overhead, row lock waits, or plan instability.\n"
            f"- Do NOT recommend gathering stats unless you have evidence stats are stale.\n"
            f"- Do NOT recommend LOG_FILES or redo parameters for a SQL tuning question."
        )

    elif trigger_type == "wait" and trigger_value:
        # Oracle wait event knowledge base
        wait_kb = {
            "db file sequential read":   "single-block I/O. Caused by: index range scans on large tables with high clustering factor, row-by-row processing. Fix: index rebuild, IOT tables, or reduce index range scan cardinality.",
            "db file scattered read":    "multi-block I/O (full table/index scan). Caused by: missing indexes, unselective predicates, full scans. Fix: add selective index, partition pruning, parallel query tuning.",
            "log file parallel write":   "LGWR writing redo to disk. Caused by: high commit rate, slow redo log disk, small redo logs, sync commits. Fix: async commits (COMMIT_WRITE=BATCH,NOWAIT for non-critical), move redo to faster disk, increase redo log size (> 500MB), reduce commit frequency.",
            "log file sync":             "foreground waiting for LGWR to flush. Caused by: too many commits per second, slow redo disk. Fix: batch commits, async commits, faster redo disk.",
            "buffer busy waits":         "multiple sessions waiting for same buffer. Caused by: hot block (right-side index leaf, segment header). Fix: reverse-key index, hash partitioning, increase FREELISTS.",
            "enq: TX - row lock contention":"row lock conflict. Caused by: long-running uncommitted DML, missing FK indexes. Fix: add index on FK columns, reduce transaction length, investigate blocking session.",
            "gc buffer busy acquire":    "RAC global cache — cross-instance block contention. Caused by: hot blocks accessed from multiple instances. Fix: partition data by instance affinity, reduce cross-instance DML.",
            "cursor: pin S wait on X":   "parse/compile contention. Caused by: hard parse storm, DDL/recompile on shared cursors. Fix: bind variables, cursor_sharing=FORCE, reduce hard parses.",
            "direct path read":          "large scan bypassing buffer cache. Often normal for parallel query/sort. If unexpected: reduce full scans, adjust db_file_multiblock_read_count.",
            "latch: cache buffers chains":"hot block — CBC latch contention. Fix: identify hot block with X$BH, use reverse-key index, hash partition hot table.",
        }
        wait_guidance = wait_kb.get(trigger_value.lower(),
            f"Wait event '{trigger_value}'. Look up Oracle documentation for this specific wait class.")
        focus_instruction = (
            f"TASK: Deep-dive analysis of wait event '{trigger_value}'.\n"
            f"Oracle knowledge base for this wait: {wait_guidance}\n"
            f"- Identify which SQLs and segments from the data above are most likely contributors.\n"
            f"- Provide tuning steps specific to '{trigger_value}' — NOT generic advice.\n"
            f"- Include specific Oracle commands (ALTER SYSTEM, init.ora parameters, etc.)."
        )

    elif trigger_type == "segment" and trigger_value:
        focused_seg = next((s for s in seg_analysis
                           if s['object_name'] == trigger_value), None)
        seg_note = ""
        if focused_seg:
            if focused_seg['cache_hit_pct'] < 80:
                seg_note = f" Cache hit={focused_seg['cache_hit_pct']}% — LOW, indicating heavy physical I/O. Likely a large table with full scans or very high clustering factor index."
            elif focused_seg['cache_hit_pct'] > 99:
                seg_note = f" Cache hit={focused_seg['cache_hit_pct']}% — GOOD. Data mostly served from buffer cache. Focus on logical read reduction instead."
            else:
                seg_note = f" Cache hit={focused_seg['cache_hit_pct']}%."
        focus_instruction = (
            f"TASK: Analyse hot segment '{trigger_value}'.{seg_note}\n"
            f"- Identify what type of access is causing this segment to be hot "
            f"  (full scan, index lookup, DML contention).\n"
            f"- Cross-reference with top SQLs — which SQL IDs are likely accessing this segment.\n"
            f"- Recommend: index changes, partitioning, statistics, or access pattern changes.\n"
            f"- Do NOT just say 'gather statistics' without evidence they are stale."
        )
    else:
        # Overall analysis — find the real bottleneck
        top_wait = top_waits[0]['event'] if top_waits else "unknown"
        top_wait_pct = top_waits[0]['pct_db_time'] if top_waits else 0
        good_sqls = [s for s in sql_analysis if s['avg_ms'] < 50]
        focus_instruction = (
            f"TASK: Overall database performance analysis.\n"
            f"Top wait '{top_wait}' consumes {top_wait_pct}% DB time — this is the primary bottleneck.\n"
            f"Note: {len(good_sqls)}/{len(sql_analysis)} top SQLs have avg < 50ms (performing well).\n"
            f"- Focus on the wait event as the primary bottleneck, not individual SQL tuning.\n"
            f"- Explain why this wait event is high based on the segment and SQL data provided.\n"
            f"- Give wait-class-specific Oracle recommendations."
        )

    prompt = f"""You are a senior Oracle DBA with 20 years of AWR analysis experience.
You have deep knowledge of Oracle internals, wait events, execution plans, and tuning.

DATABASE: {dbname} | SNAPS: {begin_snap}–{end_snap} | {snap.get('begin_time','')} → {snap.get('end_time','')}

{focus_instruction}

=== AWR DATA ===
TOP WAIT EVENTS:
{waits_txt}

TOP SQL (pre-analysed):
{sql_txt}

TOP SEGMENTS BY LOGICAL READS:
{seg_txt}
"""
    if dba_feedback:
        prompt += f"\nDBA CONTEXT: {dba_feedback}\n"

    prompt += """
RULES YOU MUST FOLLOW:
- Never recommend LOG_FILES parameter (it does not exist in Oracle)
- Never recommend DBMS_MONITOR for performance — use V$SESSION, ASH, AWR
- If a SQL has avg < 50ms, say it is performing well — do NOT tune it
- Use only valid Oracle 12c/19c syntax
- Reference specific SQL IDs and object names from the data above

RESPONSE FORMAT (use these exact headers):

**ROOT CAUSE**
[2-3 sentences. Name the specific Oracle mechanism causing the problem. Reference actual numbers.]

**EVIDENCE**
[3 bullet points with specific numbers from the data above]

**RECOMMENDATIONS**
1. [Most impactful immediate action — include exact Oracle command]
2. [Second action — include exact Oracle command or SQL]
3. [Third action]
4. [Monitoring — exact V$ or AWR query to run]
5. [Preventive action]

**VALIDATION**
[Which metric to check, expected before/after values, exact query to run]"""

    # ── Call AI ───────────────────────────────────────────────────────
    import urllib.request as _ur, urllib.error as _ue
    response_text = ""
    try:
        if ai_mode == "local_ai":
            url   = cfg.get("ai_local_url","http://localhost:11434").rstrip("/")
            model = cfg.get("ai_local_model","llama3.1:8b")

            # Verify Ollama is running and resolve exact model name
            try:
                tags_req = _ur.Request(f"{url}/api/tags")
                with _ur.urlopen(tags_req, timeout=5) as tr:
                    tags = json.loads(tr.read())
                available = [m.get("name","") for m in tags.get("models",[])]
                if not available:
                    return JSONResponse({"ok":False,
                        "error": f"No models found in Ollama. Run: ollama pull llama3.1:8b"})
                # Resolve: find exact match or prefix match
                resolved = None
                for m in available:
                    if m == model:
                        resolved = m
                        break
                if not resolved:
                    # Try prefix match e.g. "llama3.1" matches "llama3.1:latest"
                    base = model.split(":")[0]
                    for m in available:
                        if m.startswith(base):
                            resolved = m
                            break
                if not resolved:
                    return JSONResponse({"ok":False,
                        "error": f"Model '{model}' not found in Ollama. "
                                 f"Available: {', '.join(available[:5])}. "
                                 f"Run: ollama pull {model}"})
                model = resolved  # use exact name Ollama knows
            except _ue.URLError:
                return JSONResponse({"ok":False,
                    "error": f"Ollama not reachable at {url}. "
                             f"Is Ollama installed and running? "
                             f"Download from https://ollama.com/download/windows"})

            payload = json.dumps({"model":model,"prompt":prompt,"stream":False,
                                  "options":{"temperature":0.3,"num_predict":1200}}).encode()
            req = _ur.Request(f"{url}/api/generate", data=payload,
                              headers={"Content-Type":"application/json"})
            with _ur.urlopen(req, timeout=None) as resp:
                response_text = json.loads(resp.read()).get("response","").strip()

        elif ai_mode == "cloud_ai":
            provider = cfg.get("ai_cloud_provider","claude")
            api_key  = cfg.get("ai_cloud_api_key","")
            model    = cfg.get("ai_cloud_model","")
            if not api_key:
                return JSONResponse({"ok":False,"error":"Cloud AI API key not set in Settings → AI"})
            if provider == "claude":
                payload = json.dumps({"model":model or "claude-haiku-4-5",
                                      "max_tokens":700,
                                      "messages":[{"role":"user","content":prompt}]}).encode()
                req = _ur.Request("https://api.anthropic.com/v1/messages", data=payload,
                                  headers={"Content-Type":"application/json",
                                           "x-api-key":api_key,
                                           "anthropic-version":"2023-06-01"})
                with _ur.urlopen(req, timeout=None) as resp:
                    response_text = json.loads(resp.read())["content"][0]["text"].strip()
            elif provider == "openai":
                payload = json.dumps({"model":model or "gpt-4o-mini","max_tokens":700,
                                      "temperature":0.3,
                                      "messages":[{"role":"user","content":prompt}]}).encode()
                req = _ur.Request("https://api.openai.com/v1/chat/completions", data=payload,
                                  headers={"Content-Type":"application/json",
                                           "Authorization":f"Bearer {api_key}"})
                with _ur.urlopen(req, timeout=None) as resp:
                    response_text = json.loads(resp.read())["choices"][0]["message"]["content"].strip()

    except _ue.HTTPError as e:
        err = f"AI API error {e.code}: {e.reason}"
        try: err += " — " + json.loads(e.read()).get("error",{}).get("message","")
        except Exception: pass
        return JSONResponse({"ok":False,"error":err})
    except Exception as e:
        return JSONResponse({"ok":False,"error":str(e)})

    if not response_text:
        return JSONResponse({"ok":False,"error":"AI returned empty response — check Ollama is running and model is loaded"})

    # ── Store in DB ───────────────────────────────────────────────────
    rec_id = -1
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO awr_ai_recommendations
                  (dbname,instance,begin_snap,end_snap,
                   trigger_type,trigger_value,severity,
                   ai_provider,ai_model,ai_prompt,
                   ai_response,root_cause,recommendation,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
                RETURNING id
            """, (dbname, instance, begin_snap, end_snap,
                  trigger_type, trigger_value, severity,
                  ai_mode, cfg.get("ai_local_model" if ai_mode=="local_ai" else "ai_cloud_model",""),
                  prompt, response_text, response_text[:500], response_text))
            rec_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Store AI rec failed: {e}")

    return JSONResponse({"ok":True,"response":response_text,"rec_id":rec_id,"error":""})


@app.post("/api/ai/feedback")
async def api_ai_feedback(request: Request):
    """Accept, reject or revise an AI recommendation."""
    body     = await request.json()
    rec_id   = body.get("rec_id")
    action   = body.get("action","")
    feedback = body.get("feedback","")

    conn = get_db_connection()
    try:
        if action == "accept":
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE awr_ai_recommendations
                    SET status='accepted', accepted_at=NOW()
                    WHERE id=%s
                    RETURNING trigger_type, trigger_value, recommendation
                """, (rec_id,))
                row = cur.fetchone()
                if row:
                    pattern  = f"{row[0]}:{row[1]}"
                    rec_text = (row[2] or "")[:2000]
                    cur.execute("""
                        INSERT INTO awr_ai_learnings
                          (trigger_pattern, accepted_recommendation, times_accepted, last_seen)
                        VALUES (%s,%s,1,NOW())
                        ON CONFLICT (trigger_pattern) DO UPDATE
                          SET times_accepted = awr_ai_learnings.times_accepted + 1,
                              accepted_recommendation = EXCLUDED.accepted_recommendation,
                              last_seen = NOW()
                    """, (pattern, rec_text))
            conn.commit()
            return JSONResponse({"ok": True})

        elif action == "reject":
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE awr_ai_recommendations
                    SET status='rejected', rejected_at=NOW(), dba_feedback=%s
                    WHERE id=%s
                """, (feedback, rec_id))
            conn.commit()
            return JSONResponse({"ok": True})

        elif action == "revise":
            # Fetch original and re-call AI with additional context
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT dbname, instance, begin_snap, end_snap,
                           trigger_type, trigger_value, severity, ai_prompt
                    FROM awr_ai_recommendations WHERE id=%s
                """, (rec_id,))
                row = cur.fetchone()
            if not row:
                return JSONResponse({"ok":False,"error":"Recommendation not found"})

            dbname, instance, b_snap, e_snap, ttype, tval, sev, orig_prompt = row
            # Re-call with feedback appended to original prompt
            revised_prompt = (orig_prompt or "") + f"\n\nDBA ADDITIONAL CONTEXT:\n{feedback}"
            cfg     = _get_config("ai")
            ai_mode = cfg.get("ai_mode","rules")

            import urllib.request as _ur
            response_text = ""
            try:
                if ai_mode == "local_ai":
                    url   = cfg.get("ai_local_url","http://localhost:11434")
                    model = cfg.get("ai_local_model","llama3.1:8b")
                    payload = json.dumps({"model":model,"prompt":revised_prompt,
                                          "stream":False,"options":{"temperature":0.3,"num_predict":1200}}).encode()
                    req = _ur.Request(f"{url.rstrip('/')}/api/generate", data=payload,
                                      headers={"Content-Type":"application/json"})
                    with _ur.urlopen(req, timeout=None) as resp:
                        response_text = json.loads(resp.read()).get("response","").strip()
                elif ai_mode == "cloud_ai":
                    api_key  = cfg.get("ai_cloud_api_key","")
                    provider = cfg.get("ai_cloud_provider","claude")
                    model    = cfg.get("ai_cloud_model","")
                    if provider == "claude":
                        payload = json.dumps({"model":model or "claude-haiku-4-5",
                                              "max_tokens":700,
                                              "messages":[{"role":"user","content":revised_prompt}]}).encode()
                        req = _ur.Request("https://api.anthropic.com/v1/messages", data=payload,
                                          headers={"Content-Type":"application/json",
                                                   "x-api-key":api_key,
                                                   "anthropic-version":"2023-06-01"})
                        with _ur.urlopen(req, timeout=None) as resp:
                            response_text = json.loads(resp.read())["content"][0]["text"].strip()
            except Exception as e:
                return JSONResponse({"ok":False,"error":str(e)})

            if response_text:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE awr_ai_recommendations
                        SET status='revised', revised_at=NOW(),
                            revised_response=%s, dba_feedback=%s
                        WHERE id=%s
                    """, (response_text, feedback, rec_id))
                conn.commit()
                return JSONResponse({"ok":True,"response":response_text})
            return JSONResponse({"ok":False,"error":"AI returned empty response"})

        raise HTTPException(400, f"Unknown action: {action}")
    finally:
        conn.close()


@app.get("/api/ai/history")
async def api_ai_history(dbname: str = "", limit: int = 50):
    """Return recent AI recommendations with status."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, dbname, trigger_type, trigger_value, severity,
                       ai_provider, ai_model, status, root_cause,
                       recommendation, dba_feedback, created_at
                FROM awr_ai_recommendations
                WHERE (%s='' OR dbname=%s)
                ORDER BY created_at DESC
                LIMIT %s
            """, (dbname, dbname, limit))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
        return JSONResponse({"history": rows})
    finally:
        conn.close()


# ── run ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True,
                app_dir=_PORTAL_DIR)
