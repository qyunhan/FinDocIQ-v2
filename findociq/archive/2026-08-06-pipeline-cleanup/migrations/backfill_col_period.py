"""backfill_col_period — re-derive `col_dim.col_period` (and the cells that fell
back because it was missing) for columns loaded BEFORE a period-grammar
improvement.

WHY THIS EXISTS (not a one-off migration)
-----------------------------------------
`load_v7`'s period grammar is improved over time — the 2026-08-03 pass taught it
digit ordinals + 'Qtr'/'Q' abbreviations and 'Year YYYY' (see docs/DECISIONS.md).
A grammar fix only reaches rows that are RE-LOADED, and re-loading means
re-extraction. Every document loaded before the fix keeps `col_period IS NULL`
on columns the grammar can now parse perfectly well ('4th Qtr 2024',
'1st Qtr 2022', '9 Mths 2025'), so every cell in them falls through the
precedence chain (col > row > table > doc) to the TABLE or DOC period. Six
quarters of columns then collapse onto one date and compete for the same
`fact_metric` grain slot — the cross-period contamination DECISIONS.md predicted
when the grammar fix shipped ("fix ships as STEP-3 reload of all docs").

`col_period` is DERIVED data, not extracted data: it is a pure function of the
column's stored label. So it can be re-derived in place, no re-extraction, no
API cost. This script replays the loader's OWN decision rule over labels already
in the DB, which is what makes it general rather than a patch: it is not a
second grammar to keep in sync, it CALLS `load_v7.is_period_text` /
`parse_period_span` with the same `column=True` context and the same
leaf-beats-group-banner precedence (load_v7.py, "Period axis (site a + b)").
Re-run it after any future grammar change; a document loaded after the change is
a no-op.

WHAT IT TOUCHES
---------------
  * `col_dim.col_period` / `period_span` / `period_start` — only where currently
    NULL and the loader's rule now yields a value. A stored period is NEVER
    overwritten (verified: 0 disagreements between stored values and the current
    grammar corpus-wide).
  * `cell_fact.period` / `period_span` / `period_source` for cells in those
    columns — only where `period_source` is a FALLBACK bucket
    ('table_or_doc' / 'table_title' / 'doc'), i.e. exactly the cells that fell
    through for want of the column period. A cell already stamped from its own
    column ('col') or its row ('row') outranks the column and is left alone,
    preserving the loader's precedence chain rather than re-implementing it.

Idempotent: a second run finds nothing to do.

    python3 findociq/pipeline/pass2/backfill_col_period.py --db findociq/db/compiled_fs.db [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from pass2.load_v7 import is_period_text, parse_period_span  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]

#: period_source values that mean "this cell did NOT get its period from its own
#: column" — the only cells a newly-derived column period may re-stamp.
_FALLBACK_SOURCES = ("table_or_doc", "table_title", "doc")


def column_period(leaf: str | None, group: str | None):
    """The loader's own column-period decision, replayed.

    Mirrors load_v7's "Period axis (site a + b)": a period-expression GROUP
    banner stamps every leaf under it; a leaf's OWN explicit date/period takes
    precedence over its banner. `is_period_text` gates BEFORE parsing — that
    gate is what keeps a comparison banner ('2025 vs 2024', '1H25 vs 1H24')
    periodless, even though `parse_period_span` would happily return the first
    year it sees.
    """
    group_ps = (parse_period_span(group, column=True)
                if group and is_period_text(group, column=True) else None)
    leaf_ps = (parse_period_span(leaf, column=True)
               if leaf and is_period_text(leaf, column=True) else None)
    return leaf_ps or group_ps


def backfill(con: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    rows = con.execute("""
        SELECT d.doc_id, d.table_id, d.col_id,
               COALESCE(d.col_leaf_label_clean, d.col_leaf_label),
               COALESCE(p.col_leaf_label_clean, p.col_leaf_label)
        FROM col_dim d
        LEFT JOIN col_dim p ON p.doc_id = d.doc_id AND p.table_id = d.table_id
                           AND p.col_id = d.col_parent
        WHERE d.col_period IS NULL
    """).fetchall()

    cols_updated = 0
    cells_restamped = 0
    by_doc: dict[str, int] = {}
    for doc_id, table_id, col_id, leaf, group in rows:
        ps = column_period(leaf, group)
        if not ps or not ps[0]:
            continue
        period, span, start = ps
        cols_updated += 1
        by_doc[doc_id] = by_doc.get(doc_id, 0) + 1
        n = con.execute(
            "SELECT COUNT(*) FROM cell_fact WHERE doc_id=? AND table_id=? AND col_id=? "
            "AND period_source IN (?,?,?)",
            (doc_id, table_id, col_id, *_FALLBACK_SOURCES)).fetchone()[0]
        cells_restamped += n
        if dry_run:
            continue
        con.execute(
            "UPDATE col_dim SET col_period=?, period_span=?, period_start=? "
            "WHERE doc_id=? AND table_id=? AND col_id=?",
            (period, span, start, doc_id, table_id, col_id))
        con.execute(
            "UPDATE cell_fact SET period=?, period_span=?, period_source='col' "
            "WHERE doc_id=? AND table_id=? AND col_id=? AND period_source IN (?,?,?)",
            (period, span, doc_id, table_id, col_id, *_FALLBACK_SOURCES))
    if not dry_run:
        con.commit()
    return {"columns_scanned": len(rows), "columns_updated": cols_updated,
            "cells_restamped": cells_restamped, "by_doc": by_doc}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    r = backfill(con, dry_run=args.dry_run)
    print(f"columns with NULL col_period scanned: {r['columns_scanned']:,}")
    print(f"columns re-derived:                   {r['columns_updated']:,}")
    print(f"cells re-stamped to period_source='col': {r['cells_restamped']:,}")
    for doc, n in sorted(r["by_doc"].items(), key=lambda x: -x[1]):
        print(f"    {n:>4}  {doc}")
    con.close()


if __name__ == "__main__":
    main()
