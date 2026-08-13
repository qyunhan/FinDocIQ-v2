"""migrate_add_period_source — feature-stamping pass, Step 2 (G4).

Adds `cell_fact.period_source`, recording which link of the period chain
(col > row > table_title > doc) actually supplied a cell's period: 'col' /
'row' / 'table_title' / 'doc'. The loader (`pass2/load_v7.py`) now writes this
column directly on every fresh load — this migration exists only to add the
column to a DB that predates it and to backfill existing rows with a
best-effort reconstruction, so metric-4 reporting ("cells inheriting
doc_period") has real provenance instead of an inferred proxy.

Backfill is a RECONSTRUCTION, not authoritative: it infers period_source from
what's already stored (col_dim.col_period, row_dim.row_period, table_t.period
vs table_t's own title-vs-doc origin is NOT recoverable after the fact) —
'table_title' and 'doc' are collapsed to a single 'table_or_doc' backfill value
where table_t.period was used, since whether that period came from the title
or the doc-fallback is lost information for rows loaded before this pass. Any
cell reloaded through the fixed loader gets the real, precise value instead.

    python3 findociq/pipeline/pass2/migrate_add_period_source.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def _has_column(con, table, column) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def migrate(con: sqlite3.Connection) -> dict:
    s = {"column_added": False, "backfilled": 0}
    if not _has_column(con, "cell_fact", "period_source"):
        con.execute("ALTER TABLE cell_fact ADD COLUMN period_source TEXT")
        s["column_added"] = True
    con.commit()
    return s


def backfill(con: sqlite3.Connection) -> dict:
    """Best-effort reconstruction for rows with period_source still NULL —
    only touches rows this migration itself left unstamped; never overwrites
    a value the loader already wrote."""
    n_col = con.execute("""
        UPDATE cell_fact SET period_source='col'
        WHERE period_source IS NULL AND col_id IN (
            SELECT c.col_id FROM col_dim c
            WHERE c.doc_id=cell_fact.doc_id AND c.table_id=cell_fact.table_id
              AND c.col_id=cell_fact.col_id AND c.col_period IS NOT NULL)
    """).rowcount
    con.commit()
    n_row = con.execute("""
        UPDATE cell_fact SET period_source='row'
        WHERE period_source IS NULL AND row_id IN (
            SELECT r.row_id FROM row_dim r
            WHERE r.doc_id=cell_fact.doc_id AND r.table_id=cell_fact.table_id
              AND r.row_id=cell_fact.row_id AND r.row_period IS NOT NULL)
    """).rowcount
    con.commit()
    n_table_or_doc = con.execute(
        "UPDATE cell_fact SET period_source='table_or_doc' WHERE period_source IS NULL"
    ).rowcount
    con.commit()
    return {"col": n_col, "row": n_row, "table_or_doc": n_table_or_doc}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    ap.add_argument("--no-backfill", action="store_true",
                     help="add the column only, leave existing rows NULL (they will be "
                          "correctly stamped by the Step 3 reload instead)")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    m = migrate(con)
    print(f"column added: {m['column_added']}")
    if not args.no_backfill:
        b = backfill(con)
        print(f"backfilled: col={b['col']:,} row={b['row']:,} table_or_doc={b['table_or_doc']:,}")
    con.close()


if __name__ == "__main__":
    main()
