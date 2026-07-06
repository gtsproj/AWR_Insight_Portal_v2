# AWR Insight Portal v2

Oracle AWR & SAR performance analysis — Python + PostgreSQL + Grafana.

## Folder Structure

```
AWR_Insight_Portal_v2/
│
├── _backup/                          ← Original files before v2 changes
│   ├── root_scripts/                 ← awr_watcher.py, master_parser.py, watcher.py
│   ├── common/                       ← logger.py, logger_utils original
│   ├── config/                       ← settings_original.yaml
│   └── modules/
│       ├── sar_original/             ← 5 original SAR parsers (flat, with bugs)
│       └── segment_parsers_original/ ← 17 original segment parsers (replaced by generic)
│
├── awr_watcher.py          ← v2: multi-file, per-DB queue routing (UPDATED)
├── master_parser.py        ← v2: accepts filepath arg, no glob (UPDATED)
├── queue_processor.py      ← NEW: FIFO queue consumer with retry/lock
├── central_repo_scanner.py ← NEW: RAC/CDB/network share folder walker
├── recommendation_engine.py← NEW: 62-rule engine + optional AI
├── anomaly_detector.py     ← NEW: statistical z-score anomaly detection
├── upload_to_postgres.py   ← original (retained)
├── requirements.txt        ← NEW
├── README.md               ← NEW
│
├── common/
│   ├── config_loader.py    ← original (retained)
│   ├── db.py               ← original (retained)
│   ├── logger_utils.py     ← v2: consolidated, dynamic paths (UPDATED)
│   │                          (logger.py removed — backed up in _backup/)
│   └── utils.py            ← original (retained)
│
├── config/
│   └── settings.yaml       ← v2: extended with source modes, AI, queues (UPDATED)
│
├── modules/
│   ├── awr/                ← NEW sub-folder
│   │   └── generic_segment_parser.py ← replaces 17 awr_seg_* parsers
│   ├── sar/                ← NEW sub-folder (replaces flat SAR parsers)
│   │   ├── sar_master_parser.py      ← v3: all 8 sections, bugs fixed
│   │   ├── sar_cpu_parser.py         ← v2: correct signature, all columns
│   │   ├── sar_memory_parser.py      ← v2: kB→MB, all columns
│   │   ├── sar_swap_parser.py        ← v2: kB→MB, auto pct calc
│   │   ├── sar_disk_parser.py        ← v2: all columns, newer SAR format
│   │   ├── sar_network_parser.py     ← NEW (sar -n DEV)
│   │   ├── sar_paging_parser.py      ← NEW (sar -B)
│   │   ├── sar_ctxswitch_parser.py   ← NEW (sar -w)
│   │   └── sar_loadavg_parser.py     ← NEW (sar -q)
│   ├── plan/               ← NEW sub-folder
│   │   └── plan_parser.py            ← execution plan upload & analysis
│   └── [all 48 original AWR parser modules retained as-is]
│
├── portal/                 ← NEW: FastAPI web UI
│   ├── app.py              ← 6-page web application
│   ├── filters.py
│   └── templates/          ← 8 Jinja2 HTML pages
│       ├── base.html, home.html, awr_upload.html
│       ├── sar_upload.html, plan_upload.html
│       ├── queue_monitor.html, comparison.html, sql_search.html
│
├── rules/                  ← NEW folder
│   ├── recommendation_rules_v2.json  ← 62 rules (severity/conditions/SQL)
│   └── recommendation_rulesv1.1.json ← original 14 rules (retained)
│
├── schema/
│   ├── awr_parser schema scripts.txt ← original (retained)
│   ├── sar_parser schema scripts.txt ← original (retained)
│   ├── MATERIALIZED VIEW.txt         ← original (retained)
│   ├── awr_parser_index_creation.sql ← original (retained)
│   ├── sar_new_tables_and_cdb_additions.sql ← NEW
│   ├── recommendations_and_comparison.sql   ← NEW
│   └── execution_plans.sql                  ← NEW
│
├── queues/                 ← per-DB queue JSON files (auto-created by watcher)
├── awr_reports/            ← AWR input: awr_reports/<DBNAME>/
├── sar_reports/            ← SAR input: sar_reports/<HOSTNAME>/
├── archive/                ← processed files moved here automatically
│   ├── awr/
│   └── sar/
├── logs/                   ← rotating log files (auto-created)
│
└── grafana-v12.0.2/public/dashboard/
    ├── [59 original dashboards — all retained]
    ├── awr_problem_areas.json      ← NEW
    ├── awr_comparison.json         ← NEW
    ├── sar_overview.json           ← NEW
    └── sar_awr_correlation.json    ← NEW
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure DB connection — edit config/settings.yaml

# 3. Create schema (run in order)
psql -U postgres -d postgres -f "schema/awr_parser schema scripts.txt"
psql -U postgres -d postgres -f schema/sar_new_tables_and_cdb_additions.sql
psql -U postgres -d postgres -f schema/recommendations_and_comparison.sql
psql -U postgres -d postgres -f schema/execution_plans.sql

# 4. Start watcher (detects new AWR files and enqueues them)
python awr_watcher.py

# 5. Start queue processor (processes queued files one at a time per DB)
python queue_processor.py --daemon

# 6. Start web portal
py -m uvicorn portal.app:app --host 0.0.0.0 --port 8000

# 7. Import new Grafana dashboards from grafana-v12.0.2/public/dashboard/
#    - awr_problem_areas.json
#    - awr_comparison.json
#    - sar_overview.json
#    - sar_awr_correlation.json
```

## Run Recommendation Engine Manually

```bash
python recommendation_engine.py --db COLDBPRD --start 43649 --end 43660 --store
```

## Run Anomaly Detection

```bash
python anomaly_detector.py --db COLDBPRD --snap 43650 --store
```
reset queue before restarting windows

py reset_queue.py


reset failed queues or delete the queue files from queues folder
py reset_failed_queue.py

SWL user id
avkadmin
Admin@123