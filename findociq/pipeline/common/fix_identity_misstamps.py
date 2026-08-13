"""fix_identity_misstamps.py — one-time, re-runnable corrections for a small
set of CONFIRMED row_dim.concept_key mis-stamps in the live DB, keyed strictly
by (doc_id, table_id, row_id) — never by label — so it cannot over-reach onto
a row we did not specifically diagnose (mirrors migrate_add_industry_dim.py's
idempotent/guarded style).

Every target below is verified against the row's ACTUAL cell_fact value (for
value-bearing rows) or its cell COUNT (for zero-cell structural headers)
before writing anything. If a reload has shifted row_ids since this list was
compiled, the current value/cell-count will not match and the fix is SKIPPED
with a warning rather than silently overwriting the wrong row.

Fixes applied (see findociq/docs/ for the diagnosis this encodes):
  1. (RETIRED 2026-08-04) gross mis-stamped on NET loan values -> net.
     `bs.assets.customer_loans_net` no longer exists; these three entries are
     now no-ops. Policy: "for customer loans, we use Gross customer loans".
  2. bs.equity.shareholders mis-stamped on TOTAL EQUITY rows (UOB/OCBC balance
     sheets, which include non-controlling interests) -> bs.equity.total.
  3. UOB's printed "Other non-interest income" (1884, Financial Highlights)
     wrongly carrying pnl.noninterest.total (the true total is 4453; 1884 is
     the residual) -> cleared to NULL. The new derived concept
     pnl.noninterest.other supplies this identity once concept/run.py +
     compute_ratios.py are re-run.
  4. Bare structural section-header rows (no cell_fact rows at all) that
     nonetheless carry a concept_key -> cleared to NULL. Only rows with ZERO
     cells are ever touched here.

Because concept/run.py's deterministic pass unconditionally re-derives
row_dim.concept_key from the CURRENT alias table on every run (it does not
respect a prior manual correction), this script is designed to be run again,
harmlessly, any time after concept/run.py -- see
findociq/docs/2026-07-30-ingest-handoff.md-style handoff notes / PROGRESS.md
for the exact re-run order used in this pass.

    python3 findociq/pipeline/fix_identity_misstamps.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def _row_value_ok(con: sqlite3.Connection, doc_id: str, table_id: str, row_id: int,
                   expected_value: float, tol: float = 0.5) -> bool:
    """True iff SOME cell_fact row for this (doc,table,row) carries the expected
    value (within a small float tolerance) -- guards against a shifted row_id
    silently matching a totally different figure."""
    rows = con.execute(
        "SELECT value_num FROM cell_fact WHERE doc_id=:d AND table_id=:t AND row_id=:r "
        "AND value_num IS NOT NULL",
        dict(d=doc_id, t=table_id, r=row_id)).fetchall()
    return any(abs(v[0] - expected_value) <= tol for v in rows)


def _cell_count(con: sqlite3.Connection, doc_id: str, table_id: str, row_id: int) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM cell_fact WHERE doc_id=:d AND table_id=:t AND row_id=:r",
        dict(d=doc_id, t=table_id, r=row_id)).fetchone()[0]


def _current_concept(con: sqlite3.Connection, doc_id: str, table_id: str, row_id: int):
    row = con.execute(
        "SELECT concept_key, row_leaf_label FROM row_dim "
        "WHERE doc_id=:d AND table_id=:t AND row_id=:r",
        dict(d=doc_id, t=table_id, r=row_id)).fetchone()
    return row  # (concept_key, row_leaf_label) or None if the row doesn't exist


def _set_concept(con: sqlite3.Connection, doc_id: str, table_id: str, row_id: int,
                  concept_key: str | None) -> None:
    con.execute(
        "UPDATE row_dim SET concept_key=:k WHERE doc_id=:d AND table_id=:t AND row_id=:r",
        dict(k=concept_key, d=doc_id, t=table_id, r=row_id))


# ---------------------------------------------------------------- fix tables
# (doc_id, table_id, row_id, expected_value, target_concept_key, note)
_VALUE_FIXES = [
    ("DBS_4Q25_performance_summary",
     "overview_selected_balance_sheet_items_m_2025-12-31", 2, 445011.0,
     "bs.assets.customer_loans_gross", "Customer loans (DBS overview). RETIRED 2026-08-04: this "
     "fix used to re-stamp gross->net, but bs.assets.customer_loans_net no longer exists "
     "(policy: 'for customer loans, we use Gross'). Now a no-op, kept so the value/cell-count "
     "assertion still documents the row."),
    ("UOB_4Q25_condensed-financial-statements",
     "balance_sheets_audited_as_at_31_december_2025_balance_sheets_audited_as_at_31_december_2025_2025-12-31",
     26, 347877.0,
     "bs.assets.customer_loans_gross", "Loans to customers (UOB balance sheet). RETIRED 2026-08-04 -- see DBS entry above"),
    ("OCBC_4Q25_Condensed_Financial_Statements",
     "balance_sheets_balance_sheets_as_at_31_december_2025_2025-12-31", 32, 336692.0,
     "bs.assets.customer_loans_gross", "Loans to customers (OCBC balance sheet). RETIRED 2026-08-04 -- see DBS entry above"),
    ("UOB_4Q25_condensed-financial-statements",
     "balance_sheets_audited_as_at_31_december_2025_balance_sheets_audited_as_at_31_december_2025_2025-12-31",
     7, 51493.0,
     "bs.equity.total", "Total equity (UOB balance sheet) -- includes NCI, was shareholders"),
    ("OCBC_4Q25_Condensed_Financial_Statements",
     "balance_sheets_balance_sheets_as_at_31_december_2025_2025-12-31", 11, 63570.0,
     "bs.equity.total", "Total equity (OCBC balance sheet) -- includes NCI, was shareholders"),
]

# (doc_id, table_id, row_id, expected_value, note) -- cleared to NULL
_CLEAR_VALUE_FIXES = [
    ("UOB_4Q25_condensed-financial-statements",
     "financial_highlights_financial_highlights_2025-12-31", 4, 1884.0,
     "Other non-interest income (UOB Financial Highlights) -- residual, not the true total"),
]

# (doc_id, table_id, row_id, expected_label, note) -- cleared to NULL, ONLY if
# the row currently has ZERO cell_fact rows (never a row that carries values).
_CLEAR_HEADER_FIXES = [
    ("UOB_4Q25_condensed-financial-statements",
     "balance_sheets_audited_as_at_31_december_2025_balance_sheets_audited_as_at_31_december_2025_2025-12-31",
     1, "Equity", "bare section header, no cells"),
    ("UOB_4Q25_condensed-financial-statements",
     "balance_sheets_audited_as_at_31_december_2025_balance_sheets_audited_as_at_31_december_2025_2025-12-31",
     8, "Liabilities", "bare section header, no cells"),
    ("UOB_4Q25_condensed-financial-statements",
     "balance_sheets_audited_as_at_31_december_2025_balance_sheets_audited_as_at_31_december_2025_2025-12-31",
     20, "Assets", "bare section header, no cells"),
    ("OCBC_4Q25_Condensed_Financial_Statements",
     "balance_sheets_balance_sheets_as_at_31_december_2025_2025-12-31",
     2, "Attributable to equity holders of the Bank", "bare section header, no cells"),
]


def fix(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    summary = {"changed": [], "already_correct": [], "skipped": []}
    try:
        for doc_id, table_id, row_id, expected_value, target_key, note in _VALUE_FIXES:
            cur = _current_concept(con, doc_id, table_id, row_id)
            if cur is None:
                summary["skipped"].append(
                    f"SKIP {doc_id}/{table_id}/row{row_id}: row does not exist -- {note}")
                continue
            cur_key, label = cur
            if not _row_value_ok(con, doc_id, table_id, row_id, expected_value):
                summary["skipped"].append(
                    f"SKIP {doc_id}/{table_id}/row{row_id} ('{label}'): expected value "
                    f"{expected_value} not found among this row's cells -- {note}")
                continue
            if cur_key == target_key:
                summary["already_correct"].append(
                    f"OK {doc_id}/{table_id}/row{row_id} ('{label}'): already {target_key}")
                continue
            _set_concept(con, doc_id, table_id, row_id, target_key)
            summary["changed"].append(
                f"FIXED {doc_id}/{table_id}/row{row_id} ('{label}'): "
                f"{cur_key!r} -> {target_key!r} ({note})")

        for doc_id, table_id, row_id, expected_value, note in _CLEAR_VALUE_FIXES:
            cur = _current_concept(con, doc_id, table_id, row_id)
            if cur is None:
                summary["skipped"].append(
                    f"SKIP {doc_id}/{table_id}/row{row_id}: row does not exist -- {note}")
                continue
            cur_key, label = cur
            if not _row_value_ok(con, doc_id, table_id, row_id, expected_value):
                summary["skipped"].append(
                    f"SKIP {doc_id}/{table_id}/row{row_id} ('{label}'): expected value "
                    f"{expected_value} not found among this row's cells -- {note}")
                continue
            if cur_key is None:
                summary["already_correct"].append(
                    f"OK {doc_id}/{table_id}/row{row_id} ('{label}'): already NULL")
                continue
            _set_concept(con, doc_id, table_id, row_id, None)
            summary["changed"].append(
                f"CLEARED {doc_id}/{table_id}/row{row_id} ('{label}'): "
                f"{cur_key!r} -> NULL ({note})")

        for doc_id, table_id, row_id, expected_label, note in _CLEAR_HEADER_FIXES:
            cur = _current_concept(con, doc_id, table_id, row_id)
            if cur is None:
                summary["skipped"].append(
                    f"SKIP {doc_id}/{table_id}/row{row_id}: row does not exist -- {note}")
                continue
            cur_key, label = cur
            if label != expected_label:
                summary["skipped"].append(
                    f"SKIP {doc_id}/{table_id}/row{row_id}: label {label!r} != expected "
                    f"{expected_label!r} -- {note}")
                continue
            n_cells = _cell_count(con, doc_id, table_id, row_id)
            if n_cells != 0:
                summary["skipped"].append(
                    f"SKIP {doc_id}/{table_id}/row{row_id} ('{label}'): has {n_cells} "
                    f"cell_fact row(s), refusing to clear a row that carries values")
                continue
            if cur_key is None:
                summary["already_correct"].append(
                    f"OK {doc_id}/{table_id}/row{row_id} ('{label}'): already NULL")
                continue
            _set_concept(con, doc_id, table_id, row_id, None)
            summary["changed"].append(
                f"CLEARED {doc_id}/{table_id}/row{row_id} ('{label}'): "
                f"{cur_key!r} -> NULL ({note})")

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    default_db = _REPO / "findociq" / "db" / "compiled_fs.db"
    ap.add_argument("--db", default=str(default_db))
    args = ap.parse_args(argv)
    summary = fix(args.db)
    for bucket in ("changed", "already_correct", "skipped"):
        print(f"\n-- {bucket} ({len(summary[bucket])}) --")
        for line in summary[bucket]:
            print(" ", line)
    print(f"\nTOTAL: {len(summary['changed'])} changed, "
          f"{len(summary['already_correct'])} already correct, "
          f"{len(summary['skipped'])} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
