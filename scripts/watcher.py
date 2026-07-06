#watcher.py

import sys, os, time
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import glob
from common.logger import get_logger
from common.config_loader import load_config
from master_parser import parse_file  # call master_parser on new reports

# Load config
config = load_config()
logger = get_logger(
    "watcher",
    log_dir=config["logging"]["dir"],
    level=config["logging"].get("level", "INFO")
)

WATCH_DIR = config["paths"]["awr_reports"]

def watcher_loop():
    logger.info(f"Starting watcher on {WATCH_DIR}")
    seen = set()

    while True:
        try:
            files = glob.glob(os.path.join(WATCH_DIR, "*.html"))
            for file in files:
                if file not in seen:
                    logger.info(f"New file detected: {file}")
                    inserted = parse_file(file)
                    logger.info(f"Processed {file}, inserted {inserted} rows")
                    seen.add(file)
        except Exception as e:
            logger.error(f"Watcher error: {e}")
        time.sleep(30)

if __name__ == "__main__":
    watcher_loop()
