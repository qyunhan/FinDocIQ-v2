"""Dimensional-breakdown scope: a geography/segment/industry decomposition never
receives a WILDCARD-matched concept_key.

Covers the F2 regression (2026-08-03): breakdown exhibits print row labels that
are character-for-character the spine's ("Total assets", "Net interest income"),
so an unscoped concept_map alias claimed them exactly as eagerly as a real
income statement's rows.

Built on a synthetic 3-table fixture, NOT the live corpus: the rule under test
is "any breakdown exhibit from any bank", so the fixture uses a bank and column
headers that appear nowhere in the registry.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.load_dictionary import (NO_WILDCARD_SCOPES,  # noqa: E402
                                     dimensional_scopes, map_table_type_norm)
from concept.resolve_deterministic import build_lookup, resolve_deterministic  # noqa: E402

_DDL = """
CREATE TABLE table_t (doc_id TEXT, table_id TEXT, table_title TEXT, table_type TEXT,
                      table_type_id TEXT, PRIMARY KEY (doc_id, table_id));
CREATE TABLE col_dim (doc_id TEXT, table_id TEXT, col_id INTEGER, col_leaf_label TEXT,
                      geo_key TEXT, segment_key TEXT, industry_key TEXT);
CREATE TABLE row_dim (doc_id TEXT, table_id TEXT, row_id INTEGER, row_leaf_label TEXT,
                      row_leaf_label_clean TEXT, row_parent INTEGER, concept_key TEXT,
                      concept_key_human TEXT, identity_source TEXT);
CREATE TABLE concept_map (table_type TEXT, label_norm TEXT, concept_key TEXT,
                          table_type_norm TEXT, PRIMARY KEY (table_type, label_norm));
CREATE TABLE table_registry (table_type_id TEXT PRIMARY KEY, dim_hint TEXT);
CREATE TABLE concept_resolution_log (doc_id TEXT, table_id TEXT, row_id INTEGER,
                      label TEXT, norm_label TEXT, concept_key TEXT, method TEXT,
                      confidence REAL, ts TEXT);
"""

# Three tables from a bank the registry has never seen:
#   T_IS  a real income statement                       -> must stamp
#   T_GEO a geography breakdown, columns = regions      -> must NOT stamp (structural)
#   T_SEG a segment breakdown whose column headers we
#         cannot map (segment_key all NULL), but whose
#         registry type declares dim_hint='segment'     -> must NOT stamp (declared)
_ROWS = ["Net interest income", "Total assets"]


def _fixture() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(_DDL)
    con.executemany("INSERT INTO table_t VALUES (?,?,?,?,?)", [
        ("D", "T_IS", "Income statement", "income_statement", "XB_INCOME"),
        ("D", "T_GEO", "Performance by region", "performance_by_region", None),
        ("D", "T_SEG", "Performance by division", "performance_by_division", "XB_DIVISION"),
    ])
    con.execute("INSERT INTO table_registry VALUES ('XB_DIVISION','segment')")
    con.execute("INSERT INTO table_registry VALUES ('XB_INCOME',NULL)")
    cols = [("D", "T_IS", 1, "2025", None, None, None)]
    for i, g in enumerate(["Norway", "Chile", "Kenya"], start=1):   # 3 geo members
        cols.append(("D", "T_GEO", i, g, f"GEO{i}", None, None))
    cols.append(("D", "T_GEO", 4, "Total", "GLOBAL", "SEG_TOTAL", None))
    for i, s in enumerate(["Div A", "Div B"], start=1):            # unmappable headers
        cols.append(("D", "T_SEG", i, s, None, None, None))
    con.executemany("INSERT INTO col_dim VALUES (?,?,?,?,?,?,?)", cols)
    rows = []
    for tid in ("T_IS", "T_GEO", "T_SEG"):
        for rid, label in enumerate(_ROWS, start=1):
            rows.append(("D", tid, rid, label, None, None, None, None, None))
    con.executemany("INSERT INTO row_dim VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO concept_map VALUES (?,?,?,?)", [
        ("*", "net interest income", "pnl.nii.net", "*"),
        ("*", "total assets", "bs.assets.total", "*"),
    ])
    con.commit()
    return con


def _keys(con) -> dict[str, list]:
    out: dict[str, list] = {}
    for tid, key in con.execute(
            "SELECT table_id, concept_key FROM row_dim ORDER BY table_id, row_id"):
        out.setdefault(tid, []).append(key)
    return out


def test_scope_detection_two_independent_signals():
    con = _fixture()
    scopes = dimensional_scopes(con)
    assert scopes.get(("D", "T_GEO")) == "dim_geo", "structural: >=2 non-GLOBAL geo cols"
    assert scopes.get(("D", "T_SEG")) == "dim_segment", "declared: registry dim_hint"
    assert ("D", "T_IS") not in scopes, "a real income statement is not a breakdown"
    con.close()


def test_wildcard_never_reaches_a_breakdown_scope():
    con = _fixture()
    resolve = build_lookup(con)
    assert resolve("total assets", "income_statement") == "bs.assets.total"
    assert resolve("total assets", "*") == "bs.assets.total"
    for scope in NO_WILDCARD_SCOPES:
        assert resolve("total assets", scope) is None, scope
    # ...but a DECLARED scoped alias still resolves: dimensional facts stay
    # opt-in-able without a code change.
    con.execute("INSERT INTO concept_map VALUES ('dim_geo','total assets',"
                "'bs.assets.total','dim_geo')")
    assert build_lookup(con)("total assets", "dim_geo") == "bs.assets.total"
    con.close()


def test_breakdown_rows_are_not_stamped_and_real_statements_are():
    con = _fixture()
    rep = resolve_deterministic(con)
    keys = _keys(con)
    assert keys["T_IS"] == ["pnl.nii.net", "bs.assets.total"], keys["T_IS"]
    assert keys["T_GEO"] == [None, None], keys["T_GEO"]
    assert keys["T_SEG"] == [None, None], keys["T_SEG"]
    assert rep["suppressed_dimensional"] == 4
    assert rep["unstamped_dimensional"] == 0      # nothing was stamped to begin with
    # a suppressed row is never offered to the LLM: inference must not re-do by
    # guess what the scope refuses to do by alias
    assert not [r for r in rep["residue"] if r["table_id"] in ("T_GEO", "T_SEG")]
    con.close()


def test_stale_stamp_is_cleared_but_a_human_decision_is_not():
    """The regression's actual repair path: rows stamped BEFORE the scope existed."""
    con = _fixture()
    con.execute("UPDATE row_dim SET concept_key='bs.assets.total' "
                "WHERE table_id='T_GEO' AND row_id=2")
    con.execute("UPDATE row_dim SET concept_key='pnl.nii.net', "
                "concept_key_human='pnl.nii.net', identity_source='human_anchor' "
                "WHERE table_id='T_GEO' AND row_id=1")
    con.commit()
    rep = resolve_deterministic(con)
    assert rep["unstamped_dimensional"] == 1
    assert _keys(con)["T_GEO"] == ["pnl.nii.net", None], _keys(con)["T_GEO"]
    logged = con.execute("SELECT method, concept_key FROM concept_resolution_log "
                         "WHERE table_id='T_GEO'").fetchall()
    assert logged == [("deterministic_dim_scope", None)], logged
    # idempotent: a second pass is a no-op, not a second log row
    assert resolve_deterministic(con)["unstamped_dimensional"] == 0
    assert con.execute("SELECT COUNT(*) FROM concept_resolution_log "
                       "WHERE table_id='T_GEO'").fetchone()[0] == 1
    con.close()


def test_raw_title_bucket_does_not_override_the_breakdown_scope():
    """DBS prints its geography breakdown under the title 'Selected income
    statement items', which map_table_type_norm reads as a real income
    statement. The table's own structure has to win."""
    con = _fixture()
    con.execute("UPDATE table_t SET table_type='selected_income_statement_items' "
                "WHERE table_id='T_GEO'")
    con.commit()
    assert map_table_type_norm("selected_income_statement_items") == "income_statement"
    resolve_deterministic(con)
    assert _keys(con)["T_GEO"] == [None, None]
    con.close()


if __name__ == "__main__":
    for t in (test_scope_detection_two_independent_signals,
              test_wildcard_never_reaches_a_breakdown_scope,
              test_breakdown_rows_are_not_stamped_and_real_statements_are,
              test_stale_stamp_is_cleared_but_a_human_decision_is_not,
              test_raw_title_bucket_does_not_override_the_breakdown_scope):
        t()
        print(f"  [PASS] {t.__name__}")
    print("ALL PASS")
