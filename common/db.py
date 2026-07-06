#common/db.py

import os
import psycopg2
import sys
import warnings
import pandas as pd
from bs4 import BeautifulSoup

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "common"))
from config_loader import load_config

def get_db_connection():
    config = load_config()
    db_config = config.get('database', {})
    
    return psycopg2.connect(
        host=db_config.get('host'),
        port=db_config.get('port'),
        user=db_config.get('user'),
        password=db_config.get('password'),
        dbname=db_config.get('dbname')
    )
    
    

