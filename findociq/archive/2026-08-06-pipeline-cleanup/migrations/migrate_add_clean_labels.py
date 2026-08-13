"""migrate_add_clean_labels.py — add the typography-clean label columns +
hierarchy_source to a live schema_v7 DB (mirrors the geometry/pass2 stage).

Idempotent (guard: skip a step if already applied). Adds:
  * row_dim.row_leaf_label_clean   TEXT — row_leaf_label with footnote
      superscripts stripped (typography-detected, NOT regex). NULL when the
      geometry stage did not match that row.
  * col_dim.col_leaf_label_clean   TEXT — same, for the column leaf label.
  * table_t.table_title_clean      TEXT — same, for the table title.
  * table_t.hierarchy_source       TEXT — 'geometry' when the row hierarchy
      for this table came from the PDF geometry side-car, 'model' when it
      fell back to the model's `level` field. Backfilled 'model' on existing
      rows — every document loaded before this migration predates the
      geometry stage, so its row hierarchy unambiguously came from the model.

    python3 findociq/pipeline/migrate_add_clean_labels.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def migrate(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = OFF")   # ALTER TABLE sequence below is transactional
    summary = {"columns_added": [], "rows_backfilled": {}}
    try:
        # 1) nullable *_clean columns — no backfill, NULL means "geometry stage
        # did not match/run for this row/col/table".
        if not _has_column(con, "row_dim", "row_leaf_label_clean"):
            con.execute("ALTER TABLE row_dim ADD COLUMN row_leaf_label_clean TEXT")
            summary["columns_added"].append("row_dim.row_leaf_label_clean")

        if not _has_column(con, "col_dim", "col_leaf_label_clean"):
            con.execute("ALTER TABLE col_dim ADD COLUMN col_leaf_label_clean TEXT")
            summary["columns_added"].append("col_dim.col_leaf_label_clean")

        if not _has_column(con, "table_t", "table_title_clean"):
            con.execute("ALTER TABLE table_t ADD COLUMN table_title_clean TEXT")
            summary["columns_added"].append("table_t.table_title_clean")

        # 2) table_t.hierarchy_source — backfilled 'model' on existing rows:
        # every document loaded before this migration predates the geometry
        # stage, so its row hierarchy unambiguously came from the model's
        # `level` field, not the PDF geometry side-car.
        if not _has_column(con, "table_t", "hierarchy_source"):
            con.execute("ALTER TABLE table_t ADD COLUMN hierarchy_source TEXT")
            summary["columns_added"].append("table_t.hierarchy_source")
            cur = con.execute(
                "UPDATE table_t SET hierarchy_source = 'model' "
                "WHERE hierarchy_source IS NULL")
            summary["rows_backfilled"]["table_t"] = cur.rowcount

        con.commit()
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
