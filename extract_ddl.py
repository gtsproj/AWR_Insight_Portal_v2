#!/usr/bin/env python3
"""
extract_ddl.py
==============
Extract DDL scripts for all AWR Insight Portal database objects.
Generates a consolidated SQL script with all tables, indexes, views,
materialized views, sequences, types, and functions.

Usage:
  py extract_ddl.py
  py extract_ddl.py --output awr_portal_full_schema.sql
  py extract_ddl.py --schema public --output awr_portal_full_schema.sql

Output file can be used to recreate the entire database schema from scratch.
"""

import sys
import os
import argparse
from datetime import datetime

# Add common folder to path
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

try:
    from config_loader import load_config
    import psycopg2

    def get_db_connection():
        config = load_config()
        db_config = config.get('database', {})
        return psycopg2.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 5432),
            user=db_config.get('user'),
            password=db_config.get('password'),
            database=db_config.get('dbname', 'postgres')
        )
except ImportError:
    import psycopg2
    def get_db_connection():
        return psycopg2.connect(
            host='localhost', port=5432,
            user='postgres', password='postgres',
            database='postgres'
        )


HEADER = """-- ============================================================
-- AWR Insight Portal v2 — Complete Database Schema
-- Generated: {timestamp}
-- Tool: extract_ddl.py
-- ============================================================
-- Run as: psql -U postgres -d postgres -f awr_portal_full_schema.sql
-- ============================================================

SET client_min_messages = WARNING;
SET search_path = public;

"""

SECTION = """
-- ============================================================
-- {title}
-- ============================================================
"""


def get_types(cur, schema):
    cur.execute("""
        SELECT
            t.typname,
            t.typtype
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = %s
          AND t.typtype IN ('e', 'c')
          AND t.typname NOT LIKE '\\_%%'
        ORDER BY t.typname
    """, (schema,))
    type_rows = cur.fetchall()

    result = []
    for tname, ttype in type_rows:
        if ttype == 'e':
            # Fetch enum labels separately
            cur.execute("""
                SELECT e.enumlabel
                FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = %s AND t.typname = %s
                ORDER BY e.enumsortorder
            """, (schema, tname))
            labels = [r[0] for r in cur.fetchall()]
            result.append((tname, ttype, labels))
        else:
            result.append((tname, ttype, []))
    return result


def get_sequences(cur, schema):
    cur.execute("""
        SELECT sequence_name
        FROM information_schema.sequences
        WHERE sequence_schema = %s
        ORDER BY sequence_name
    """, (schema,))
    return [r[0] for r in cur.fetchall()]


def get_tables(cur, schema):
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """, (schema,))
    return [r[0] for r in cur.fetchall()]


def get_table_ddl(cur, schema, table_name):
    """Generate CREATE TABLE statement."""
    # Get columns
    cur.execute("""
        SELECT
            c.column_name,
            c.data_type,
            c.character_maximum_length,
            c.numeric_precision,
            c.numeric_scale,
            c.is_nullable,
            c.column_default,
            c.udt_name
        FROM information_schema.columns c
        WHERE c.table_schema = %s
          AND c.table_name = %s
        ORDER BY c.ordinal_position
    """, (schema, table_name))
    columns = cur.fetchall()

    # Get constraints
    cur.execute("""
        SELECT
            tc.constraint_name,
            tc.constraint_type,
            kcu.column_name,
            cc.check_clause,
            ccu.table_name AS ref_table,
            ccu.column_name AS ref_column,
            rc.update_rule,
            rc.delete_rule
        FROM information_schema.table_constraints tc
        LEFT JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_name = tc.constraint_name
            AND kcu.table_schema = tc.table_schema
        LEFT JOIN information_schema.check_constraints cc
            ON cc.constraint_name = tc.constraint_name
        LEFT JOIN information_schema.referential_constraints rc
            ON rc.constraint_name = tc.constraint_name
        LEFT JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_schema = %s
          AND tc.table_name = %s
        ORDER BY tc.constraint_type, tc.constraint_name, kcu.ordinal_position
    """, (schema, table_name))
    constraints_raw = cur.fetchall()

    # Build column definitions
    col_defs = []
    for col in columns:
        col_name, data_type, char_len, num_prec, num_scale, nullable, default, udt = col

        # Build type string
        if data_type == 'character varying':
            type_str = f"VARCHAR({char_len})" if char_len else "TEXT"
        elif data_type == 'character':
            type_str = f"CHAR({char_len})" if char_len else "CHAR"
        elif data_type == 'numeric':
            if num_prec and num_scale:
                type_str = f"NUMERIC({num_prec},{num_scale})"
            elif num_prec:
                type_str = f"NUMERIC({num_prec})"
            else:
                type_str = "NUMERIC"
        elif data_type == 'USER-DEFINED':
            type_str = udt
        elif data_type == 'ARRAY':
            type_str = udt.lstrip('_') + '[]'
        else:
            type_str = data_type.upper()

        col_def = f"    {col_name:<30} {type_str}"
        if default and 'nextval' in str(default):
            # SERIAL shorthand
            if 'integer' in data_type or 'int4' in data_type:
                col_def = f"    {col_name:<30} SERIAL"
            elif 'bigint' in data_type or 'int8' in data_type:
                col_def = f"    {col_name:<30} BIGSERIAL"
            else:
                col_def = f"    {col_name:<30} {type_str} DEFAULT {default}"
        elif default:
            col_def += f" DEFAULT {default}"

        if nullable == 'NO' and 'nextval' not in str(default or ''):
            col_def += " NOT NULL"

        col_defs.append(col_def)

    # Build constraint definitions
    pk_cols = {}
    uq_cols = {}
    fk_defs = []
    check_defs = []

    for row in constraints_raw:
        cname, ctype, col_name, check_clause, ref_table, ref_col, upd_rule, del_rule = row
        if ctype == 'PRIMARY KEY':
            pk_cols.setdefault(cname, []).append(col_name)
        elif ctype == 'UNIQUE':
            uq_cols.setdefault(cname, []).append(col_name)
        elif ctype == 'FOREIGN KEY':
            fk_defs.append((cname, col_name, ref_table, ref_col, upd_rule, del_rule))
        elif ctype == 'CHECK' and 'not_null' not in cname.lower():
            if check_clause and check_clause not in [c[1] for c in check_defs]:
                check_defs.append((cname, check_clause))

    constraint_defs = []
    for cname, cols in pk_cols.items():
        constraint_defs.append(
            f"    CONSTRAINT {cname} PRIMARY KEY ({', '.join(cols)})"
        )
    for cname, cols in uq_cols.items():
        constraint_defs.append(
            f"    CONSTRAINT {cname} UNIQUE ({', '.join(cols)})"
        )
    for cname, col, ref_tab, ref_col, upd, del_ in fk_defs:
        fk = f"    CONSTRAINT {cname} FOREIGN KEY ({col})\n"
        fk += f"        REFERENCES {ref_tab} ({ref_col})"
        if del_ and del_ != 'NO ACTION':
            fk += f" ON DELETE {del_}"
        constraint_defs.append(fk)
    for cname, clause in check_defs:
        constraint_defs.append(
            f"    CONSTRAINT {cname} CHECK {clause}"
        )

    all_defs = col_defs + constraint_defs
    ddl = f"CREATE TABLE IF NOT EXISTS {table_name} (\n"
    ddl += ',\n'.join(all_defs)
    ddl += "\n);\n"

    # Add comment
    cur.execute("""
        SELECT obj_description(
            (quote_ident(%s) || '.' || quote_ident(%s))::regclass, 'pg_class'
        )
    """, (schema, table_name))
    comment = cur.fetchone()[0]
    if comment:
        ddl += f"\nCOMMENT ON TABLE {table_name} IS '{comment}';\n"

    return ddl


def get_indexes(cur, schema, table_name):
    """Get CREATE INDEX statements for a table."""
    cur.execute("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s
          AND tablename = %s
          AND indexname NOT IN (
              SELECT constraint_name
              FROM information_schema.table_constraints
              WHERE table_schema = %s AND table_name = %s
          )
        ORDER BY indexname
    """, (schema, table_name, schema, table_name))
    rows = cur.fetchall()
    ddls = []
    for iname, idef in rows:
        # Add IF NOT EXISTS
        idef = idef.replace('CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS ')
        idef = idef.replace('CREATE UNIQUE INDEX ', 'CREATE UNIQUE INDEX IF NOT EXISTS ')
        ddls.append(idef + ';')
    return ddls


def get_views(cur, schema):
    cur.execute("""
        SELECT table_name, view_definition
        FROM information_schema.views
        WHERE table_schema = %s
        ORDER BY table_name
    """, (schema,))
    return cur.fetchall()


def get_materialized_views(cur, schema):
    cur.execute("""
        SELECT matviewname, definition, ispopulated
        FROM pg_matviews
        WHERE schemaname = %s
        ORDER BY matviewname
    """, (schema,))
    return cur.fetchall()


def get_mv_indexes(cur, schema, mvname):
    cur.execute("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s AND tablename = %s
        ORDER BY indexname
    """, (schema, mvname))
    rows = cur.fetchall()
    ddls = []
    for iname, idef in rows:
        idef = idef.replace('CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS ')
        idef = idef.replace('CREATE UNIQUE INDEX ', 'CREATE UNIQUE INDEX IF NOT EXISTS ')
        ddls.append(idef + ';')
    return ddls


def get_functions(cur, schema):
    cur.execute("""
        SELECT
            p.proname,
            pg_get_functiondef(p.oid) AS definition
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = %s
          AND p.prokind IN ('f', 'p')
        ORDER BY p.proname
    """, (schema,))
    return cur.fetchall()


def get_tablespaces(cur):
    cur.execute("""
        SELECT spcname
        FROM pg_tablespace
        WHERE spcname NOT IN ('pg_default', 'pg_global')
        ORDER BY spcname
    """)
    return [r[0] for r in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(
        description="Extract DDL for all AWR Portal database objects"
    )
    parser.add_argument(
        "--output", "-o",
        default="awr_portal_full_schema.sql",
        help="Output SQL file (default: awr_portal_full_schema.sql)"
    )
    parser.add_argument(
        "--schema", "-s",
        default="public",
        help="PostgreSQL schema (default: public)"
    )
    parser.add_argument(
        "--no-functions",
        action="store_true",
        help="Skip function/procedure DDL"
    )
    args = parser.parse_args()

    print(f"Connecting to database...")
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cur = conn.cursor()
        print(f"Connected ✓")
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    schema = args.schema
    output = os.path.join(_PROJECT_ROOT, args.output)
    lines = []

    # Header
    lines.append(HEADER.format(
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ))

    # ── Tablespaces ─────────────────────────────────────────────
    tablespaces = get_tablespaces(cur)
    if tablespaces:
        lines.append(SECTION.format(title="TABLESPACES"))
        for ts in tablespaces:
            lines.append(f"-- NOTE: Update location path for your environment")
            lines.append(
                f"CREATE TABLESPACE IF NOT EXISTS {ts} "
                f"LOCATION '/var/lib/postgresql/tablespaces/{ts}';\n"
            )

    # ── Types / Enums ────────────────────────────────────────────
    types = get_types(cur, schema)
    if types:
        lines.append(SECTION.format(title="CUSTOM TYPES / ENUMS"))
        for tname, ttype, labels in types:
            if ttype == 'e' and labels:
                label_list = ", ".join(f"'{l}'" for l in labels)
                lines.append(
                    f"CREATE TYPE {tname} AS ENUM ({label_list});\n"
                )

    # ── Tables ───────────────────────────────────────────────────
    tables = get_tables(cur, schema)
    lines.append(SECTION.format(title=f"TABLES ({len(tables)} total)"))
    print(f"Extracting {len(tables)} tables...")

    all_indexes = []
    for tname in tables:
        try:
            lines.append(f"-- Table: {tname}")
            ddl = get_table_ddl(cur, schema, tname)
            lines.append(ddl)
            # Collect indexes
            idx = get_indexes(cur, schema, tname)
            if idx:
                all_indexes.extend(idx)
                all_indexes.append("")
        except Exception as e:
            lines.append(f"-- ERROR extracting {tname}: {e}\n")
            print(f"  WARNING: {tname}: {e}")

    # ── Indexes ──────────────────────────────────────────────────
    if all_indexes:
        lines.append(SECTION.format(title="INDEXES"))
        lines.extend(all_indexes)

    # ── Views ────────────────────────────────────────────────────
    views = get_views(cur, schema)
    if views:
        lines.append(SECTION.format(title=f"VIEWS ({len(views)} total)"))
        print(f"Extracting {len(views)} views...")
        for vname, vdef in views:
            if vdef:
                lines.append(f"-- View: {vname}")
                lines.append(
                    f"CREATE OR REPLACE VIEW {vname} AS\n{vdef.rstrip()};\n"
                )

    # ── Materialized Views ───────────────────────────────────────
    mvs = get_materialized_views(cur, schema)
    if mvs:
        lines.append(SECTION.format(
            title=f"MATERIALIZED VIEWS ({len(mvs)} total)"
        ))
        print(f"Extracting {len(mvs)} materialized views...")
        mv_indexes = []
        for mvname, mvdef, populated in mvs:
            if mvdef:
                lines.append(f"-- Materialized View: {mvname}")
                lines.append(
                    f"CREATE MATERIALIZED VIEW IF NOT EXISTS {mvname} AS\n"
                    f"{mvdef.rstrip()}\nWITH {'DATA' if populated else 'NO DATA'};\n"
                )
                idx = get_mv_indexes(cur, schema, mvname)
                if idx:
                    mv_indexes.extend(idx)
                    mv_indexes.append("")

        if mv_indexes:
            lines.append(SECTION.format(
                title="MATERIALIZED VIEW INDEXES"
            ))
            lines.extend(mv_indexes)

        # Refresh commands
        lines.append(SECTION.format(title="REFRESH MATERIALIZED VIEWS"))
        lines.append("-- Run after initial data load:")
        for mvname, _, _ in mvs:
            lines.append(f"-- REFRESH MATERIALIZED VIEW {mvname};")
        lines.append("")

    # ── Functions / Procedures ───────────────────────────────────
    funcs = []
    if not args.no_functions:
        funcs = get_functions(cur, schema)
        if funcs:
            lines.append(SECTION.format(
                title=f"FUNCTIONS & PROCEDURES ({len(funcs)} total)"
            ))
            print(f"Extracting {len(funcs)} functions...")
            for fname, fdef in funcs:
                if fdef:
                    lines.append(f"-- Function: {fname}")
                    lines.append(f"CREATE OR REPLACE {fdef.strip()};\n")

    # ── Permissions ──────────────────────────────────────────────
    lines.append(SECTION.format(title="PERMISSIONS"))
    lines.append("""-- Adjust these grants for your environment
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO awr_portal_reader;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO awr_portal_dba;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO awr_portal_dba;
""")

    # ── Footer ───────────────────────────────────────────────────
    func_count = len(funcs) if not args.no_functions else 'skipped'
    lines.append(f"""
-- ============================================================
-- END OF SCHEMA SCRIPT
-- Tables:             {len(tables)}
-- Views:              {len(views)}
-- Materialized Views: {len(mvs)}
-- Functions:          {func_count}
-- Generated:          {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
-- ============================================================
""")

    # Write output
    content = '\n'.join(lines)
    with open(output, 'w', encoding='utf-8') as f:
        f.write(content)

    cur.close()
    conn.close()

    print(f"\n✅ DDL extracted successfully:")
    print(f"   Tables:              {len(tables)}")
    print(f"   Views:               {len(views)}")
    print(f"   Materialized Views:  {len(mvs)}")
    print(f"   Indexes:             {len(all_indexes)}")
    if not args.no_functions:
        print(f"   Functions:           {len(funcs)}")
    print(f"\n   Output: {output}")
    print(f"\nTo recreate schema on a new server:")
    print(f"   psql -U postgres -d postgres -f {args.output}")


if __name__ == "__main__":
    main()
