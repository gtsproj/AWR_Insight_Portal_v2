#common/db.py

import os
import psycopg2
from psycopg2 import pool, extras
import logging

logger = logging.getLogger(__name__)

class Database:
    """Centralized PostgreSQL connection pool + helpers"""

    def __init__(self, dsn=None, minconn=1, maxconn=5):
        self.dsn = dsn or os.getenv("DB_URI")
        if not self.dsn:
            raise ValueError("Database URI must be set in DB_URI env variable or passed explicitly.")
        self.pool = psycopg2.pool.SimpleConnectionPool(minconn, maxconn, dsn=self.dsn)

    def get_conn(self):
        return self.pool.getconn()

    def put_conn(self, conn):
        self.pool.putconn(conn)

    def insert_rows(self, table, columns, rows, conflict_cols=None):
        """
        Insert multiple rows into a table.
        rows = list of tuples matching `columns`.
        conflict_cols = list of columns to use for ON CONFLICT DO NOTHING
        """
        if not rows:
            return 0

        conn = self.get_conn()
        inserted = 0
        try:
            with conn.cursor() as cur:
                sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES %s"
                if conflict_cols:
                    conflict_clause = f" ON CONFLICT ({','.join(conflict_cols)}) DO NOTHING"
                    sql += conflict_clause
                extras.execute_values(cur, sql, rows, page_size=500)
            conn.commit()
            inserted = len(rows)
        except Exception as e:
            logger.error(f"DB insert failed for {table}: {e}")
            conn.rollback()
        finally:
            self.put_conn(conn)

        return inserted
