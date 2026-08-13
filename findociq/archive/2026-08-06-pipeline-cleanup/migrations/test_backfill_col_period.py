"""backfill_col_period — re-deriving a stale col_period must replay the LOADER's
decision, not a second grammar, and must respect its precedence chain.

The bug this guards (pre-flight B6 / D2 class A1): a grammar improvement only
reaches re-loaded documents, so columns labelled '4th Qtr 2024' / '1st Qtr 2022'
kept col_period NULL, every cell in them fell through to the table/doc period,
and six quarters collapsed onto one fact_metric grain slot.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from pass2.backfill_col_period import backfill, column_period  # noqa: E402

_DDL = """
CREATE TABLE col_dim (doc_id TEXT, table_id TEXT, col_id INTEGER, col_parent INTEGER,
                      col_leaf_label TEXT, col_leaf_label_clean TEXT,
                      col_period TEXT, period_span TEXT, period_start TEXT);
CREATE TABLE cell_fact (doc_id TEXT, table_id TEXT, row_id INTEGER, col_id INTEGER,
                        period TEXT, period_span TEXT, period_source TEXT);
"""


def _fixture() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(_DDL)
    cols = [
        # stale: the current grammar parses this fine, the loader that ran didn't
        ("D", "T", 1, None, "4th Qtr 2024", None, None, None, None),
        # a comparison banner: must STAY periodless (parse_period_span alone
        # would happily return 2025 -- is_period_text is the gate that refuses it)
        ("D", "T", 2, None, "2025 vs 2024", None, None, None, None),
        # legitimately periodless
        ("D", "T", 3, None, "% chg", None, None, None, None),
        # already stamped: must never be rewritten
        ("D", "T", 4, None, "1st Qtr 2025", None, "2025-03-31", "1Q", "2025-01-01"),
        # leaf has no period of its own but its GROUP banner does
        ("D", "T", 6, 5, "S$m", None, None, None, None),
        ("D", "T", 5, None, "3rd Qtr 2025", None, None, None, None),
    ]
    con.executemany("INSERT INTO col_dim VALUES (?,?,?,?,?,?,?,?,?)", cols)
    cells = [
        ("D", "T", 1, 1, "2025-12-31", "FY", "doc"),          # fell back -> re-stamp
        ("D", "T", 2, 1, "2025-12-31", "FY", "table_or_doc"),  # fell back -> re-stamp
        ("D", "T", 3, 1, "2024-12-31", "4Q", "row"),           # row outranks col -> keep
        ("D", "T", 1, 2, "2025-12-31", "FY", "doc"),           # comparison col -> keep
        ("D", "T", 1, 3, "2025-12-31", "FY", "doc"),           # '% chg' -> keep
        ("D", "T", 1, 6, "2025-12-31", "FY", "doc"),           # via group banner -> re-stamp
    ]
    con.executemany("INSERT INTO cell_fact VALUES (?,?,?,?,?,?,?)", cells)
    con.commit()
    return con


def test_column_period_uses_the_loader_gate_not_bare_parsing():
    assert column_period("4th Qtr 2024", None)[:2] == ("2024-12-31", "4Q")
    # the gate, not the parser, is what makes a comparison banner periodless
    assert column_period("2025 vs 2024", None) is None
    assert column_period("1H25 vs 1H24", None) is None
    assert column_period("% chg", None) is None
    # a leaf's own period beats its group banner
    assert column_period("1st Qtr 2025", "3rd Qtr 2025")[0] == "2025-03-31"
    # ...and a periodless leaf inherits the banner
    assert column_period("S$m", "3rd Qtr 2025")[0] == "2025-09-30"


def test_backfill_updates_only_stale_columns():
    con = _fixture()
    rep = backfill(con)
    got = {cid: (cp, sp) for cid, cp, sp in
           con.execute("SELECT col_id, col_period, period_span FROM col_dim")}
    assert got[1] == ("2024-12-31", "4Q"), got[1]
    assert got[2] == (None, None), "comparison banner must stay periodless"
    assert got[3] == (None, None), "'% chg' must stay periodless"
    assert got[4] == ("2025-03-31", "1Q"), "an existing period is never rewritten"
    assert got[5] == ("2025-09-30", "3Q")
    assert got[6] == ("2025-09-30", "3Q"), "leaf inherits its group banner"
    assert rep["columns_updated"] == 3, rep
    con.close()


def test_restamp_respects_the_loader_precedence_chain():
    con = _fixture()
    backfill(con)
    got = {(r, c): (p, s, src) for r, c, p, s, src in
           con.execute("SELECT row_id, col_id, period, period_span, period_source FROM cell_fact")}
    assert got[(1, 1)] == ("2024-12-31", "4Q", "col")
    assert got[(2, 1)] == ("2024-12-31", "4Q", "col")
    # row-level period outranks the column: untouched
    assert got[(3, 1)] == ("2024-12-31", "4Q", "row")
    # cells in columns that legitimately have no period keep their fallback
    assert got[(1, 2)] == ("2025-12-31", "FY", "doc")
    assert got[(1, 3)] == ("2025-12-31", "FY", "doc")
    assert got[(1, 6)] == ("2025-09-30", "3Q", "col")
    con.close()


def test_idempotent():
    con = _fixture()
    first = backfill(con)
    second = backfill(con)
    assert first["columns_updated"] == 3 and second["columns_updated"] == 0
    assert second["cells_restamped"] == 0
    con.close()


def test_dry_run_changes_nothing():
    con = _fixture()
    rep = backfill(con, dry_run=True)
    assert rep["columns_updated"] == 3
    assert con.execute("SELECT COUNT(*) FROM col_dim WHERE col_id=1 "
                       "AND col_period IS NOT NULL").fetchone()[0] == 0
    con.close()


if __name__ == "__main__":
    for t in (test_column_period_uses_the_loader_gate_not_bare_parsing,
              test_backfill_updates_only_stale_columns,
              test_restamp_respects_the_loader_precedence_chain,
              test_idempotent, test_dry_run_changes_nothing):
        t()
        print(f"  [PASS] {t.__name__}")
    print("ALL PASS")
