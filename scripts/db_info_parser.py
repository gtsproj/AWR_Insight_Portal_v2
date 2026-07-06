#modules\db_info_parser.py

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from bs4 import BeautifulSoup

from common.db import Database
from common.logger import get_logger
from common.utils import row_hash
from common.config_loader import load_config

# Load config
config = load_config()

# Initialize DB and Logger
db = Database(dsn=config["database"]["uri"])
logger = get_logger(
    "db_info_parser",
    log_dir=config["logging"]["dir"],
    level=config["logging"].get("level", "INFO")
)

def parse_db_info(file_path: str):
    """
    Debug version: print discovered tables and their summaries
    before trying to parse DB Info, Instance Info, and Host Info.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "lxml")

        # Debug: list all tables
        tables = soup.find_all("table")
        print(f"DEBUG Found {len(tables)} total tables")
        for i, t in enumerate(tables[:10]):  # print only first 10
            print(f"Table {i} summary:", t.get("summary"))

        if len(tables) < 3:
            logger.warning(f"Not enough tables found in {file_path}")
            return 0

        # Attempt to parse first table as DB Info
        try:
            db_info_df = pd.read_html(str(tables[0]))[0]
            print("DEBUG First table dataframe:\n", db_info_df.head())
        except Exception as e:
            print("DEBUG Could not parse first table:", e)

        return 0  # stop after debug

    except Exception as e:
        logger.error(f"Error parsing {file_path}: {e}")
        return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python db_info_parser.py <awr_report.html>")
        sys.exit(1)

    report_file = sys.argv[1]
    parse_db_info(report_file)
