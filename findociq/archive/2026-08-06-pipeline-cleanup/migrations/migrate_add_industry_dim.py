"""migrate_add_industry_dim.py — add the INDUSTRY dimension axis to a live
schema_v7 DB (mirrors the existing geo/segment axes).

Idempotent (guard: skip a step if already applied). Adds:
  * industry_dim / industry_map tables (seeded, mirror of segment_dim/segment_map)
  * industry_key column on row_dim, col_dim, cell_fact, fact_metric (nullable;
    backfilled 'IND_TOTAL' on existing rows — the sentinel/default member,
    correct because no document loaded before this migration had an industry
    axis stamped)
  * v_cell / v_cell_flat views recreated to expose industry_key

    python3 findociq/pipeline/migrate_add_industry_dim.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCHEMA = _REPO / "findociq" / "schema" / "schema_v7.sql"
sys.path.insert(0, str(Path(__file__).resolve().parent))  # pipeline/ on path
from mapping.migrate_serving_views import migrate as rebuild_serving_views  # noqa: E402


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _extract_seed_statements() -> tuple[str, str]:
    """Pull the industry_dim + industry_map INSERT statements verbatim out of
    schema_v7.sql so the migration and the schema NEVER drift apart (one
    auditable source, same rule as every other reference dimension). SQL '--'
    line comments are stripped FIRST — several of the industry_map comments are
    prose containing a literal ';', which would otherwise fool a naive
    non-greedy '.*?;' statement match into stopping mid-comment."""
    lines = _SCHEMA.read_text().splitlines()
    code_only = "\n".join(re.sub(r"\s+--.*$", "", ln) for ln in lines)
    dim_sql = re.search(r"INSERT INTO industry_dim.*?;", code_only, re.S).group(0)
    map_sql = re.search(r"INSERT INTO industry_map.*?;", code_only, re.S).group(0)
    return dim_sql, map_sql


def migrate(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = OFF")   # ALTER TABLE sequence below is transactional
    summary = {"industry_dim_created": False, "industry_map_created": False,
               "columns_added": [], "rows_backfilled": {}}
    try:
        # 1) industry_dim / industry_map — table creation and row-seeding are
        # guarded INDEPENDENTLY (by row count, not just table existence): SQLite
        # DDL (CREATE TABLE) autocommits outside of Python's transaction handling,
        # so a table can legitimately exist with zero rows after an earlier
        # interrupted run — re-running must heal that, not skip it.
        dim_sql, map_sql = _extract_seed_statements()
        if not _table_exists(con, "industry_dim"):
            con.execute(
                "CREATE TABLE industry_dim (\n"
                "  industry_key   TEXT PRIMARY KEY,\n"
                "  industry_name  TEXT NOT NULL,\n"
                "  level          TEXT NOT NULL CHECK (level IN ('sector','total')),\n"
                "  parent         TEXT REFERENCES industry_dim(industry_key)\n"
                ")")
            summary["industry_dim_created"] = True
        if con.execute("SELECT COUNT(*) FROM industry_dim").fetchone()[0] == 0:
            con.executescript(dim_sql)

        if not _table_exists(con, "industry_map"):
            con.execute(
                "CREATE TABLE industry_map (\n"
                "  label_norm    TEXT PRIMARY KEY,\n"
                "  industry_key  TEXT NOT NULL REFERENCES industry_dim(industry_key)\n"
                ")")
            summary["industry_map_created"] = True
        if con.execute("SELECT COUNT(*) FROM industry_map").fetchone()[0] == 0:
            con.executescript(map_sql)

        # 2) nullable industry_key column on row_dim/col_dim/cell_fact
        for table in ("row_dim", "col_dim", "cell_fact"):
            if not _has_column(con, table, "industry_key"):
                con.execute(f"ALTER TABLE {table} ADD COLUMN industry_key TEXT "
                            f"REFERENCES industry_dim(industry_key)")
                summary["columns_added"].append(table)
                # backfill: every existing row predates the industry axis, so
                # it is unambiguously the default/whole-book member.
                cur = con.execute(
                    f"UPDATE {table} SET industry_key = 'IND_TOTAL' "
                    f"WHERE industry_key IS NULL")
                summary["rows_backfilled"][table] = cur.rowcount

        # 3) fact_metric.industry_key (if the table already exists in this DB;
        #    build_fact_metric.py rebuilds it from scratch, but guard anyway).
        if _table_exists(con, "fact_metric") and not _has_column(con, "fact_metric", "industry_key"):
            con.execute("ALTER TABLE fact_metric ADD COLUMN industry_key TEXT")
            con.execute("UPDATE fact_metric SET industry_key = 'IND_TOTAL' "
                        "WHERE industry_key IS NULL")
            summary["columns_added"].append("fact_metric")

        # 4) rebuild v_cell/v_cell_leaf/v_cell_sumsafe/v_cell_flat to expose
        #    industry_key. This used to carry its own copy of the view DDL
        #    (pre-human-anchor, pre-period-label) and rebuilt it
        #    UNCONDITIONALLY -- the same clobbering bug found in
        #    concept.load_dictionary.ensure_schema() during the 2026-08-03
        #    pre-flight pass (see docs/DECISIONS.md). There is now exactly one
        #    definition of these views, in mapping.migrate_serving_views;
        #    calling it here means this script can never again silently
        #    revert concept_key_human/identity_source/period_source/
        #    period_end/period_label if it's ever re-run.
        con.commit()
        rebuild_serving_views(con)
    except Exception:
        con.rollback()
        raise
    finally:
        con.execute("PRAGMA foreign_keys = ON")
        con.close()
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    default_db = _REPO / "findociq" / "db" / "compiled_fs.db"
    ap.add_argument("--db", default=str(default_db))
    args = ap.parse_args(argv)
    summary = migrate(args.db)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
