"""Tests for the geometry-into-loader wiring: transforms.apply_geometry (pure)
and its consumption in load_v7.py (row_parents_by_position(skip_terminal=False),
row_lineage(labels_clean=...), _read_geometry, _load_table's hierarchy_source /
table_title_clean / row_leaf_label_clean / col_leaf_label_clean persistence).

WHAT is pinned, and WHY:
  Part A pins apply_geometry's contract in isolation (synthetic GTable/GRow/
  GColumn/GCell — no PDF, no DB): the twin-merge rules, the all-or-nothing
  fallback gates, and the purity guarantee (GRow.label is never rewritten,
  the input table is never mutated). This is the function every downstream
  loader decision reads from, so its edge cases must be nailed down without
  needing a real PDF fixture.

  Part B pins row_parents_by_position(skip_terminal=False) in isolation: the
  exact behaviour that lets an indented row parent to a preceding 'total' row
  when the levels came from PRINTED geometry rather than the model — the fix
  for DBS's 'Of which: Net interest income' nesting under 'Total income'.

  Part C is the end-to-end proof, against the real DBS 1Q26 trading-update
  audit units (which already carry geometry side-cars written by
  stage1_extract.chunk.geometry): every table loads with hierarchy_source='geometry', the
  parent walk lands on the rows the printed page actually implies (not the
  model's wobbly levels), the printed-line twin merge removes the phantom
  rows it should, and the footnote-stripped clean labels ride onto row_dim /
  table_t / the row_lineage registry without disturbing the verbatim columns.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path

import pytest

from stage1_extract.chunk.schema import GCell, GColumn, GRow, GTable  # noqa: E402
from stage1_extract.chunk.transforms import apply_geometry  # noqa: E402
from stage2_load.load_v7 import load_units, row_lineage, row_parents_by_position  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_AUDIT_ROOT = (_REPO / "findociq/outputs/fs/dbs_1Q26/audit/DBS_1Q26_trading_update")
_SCHEMA_V7 = _REPO / "findociq/schema/schema_v7.sql"


def _require_fixtures() -> None:
    if not _AUDIT_ROOT.exists():
        pytest.skip("DBS 1Q26 audit fixtures not present on this workstation")


def _row(row_id: str | None, row_type: str, level: int, label: str,
        *vals: str) -> GRow:
    values = [GCell(value=v) for v in vals] if vals else []
    return GRow(row_id=row_id, row_type=row_type, level=level, label=label,
                values=values)


def _tg_row(line_id: int, indent: int, label_clean: str | None = None) -> dict:
    return {"line_id": line_id, "indent": indent, "label_clean": label_clean}


# ===========================================================================
# PART A — apply_geometry, pure / synthetic
# ===========================================================================

# --- A1: twin merge — valueless section_header + valued data row, same line_id
def test_twin_merge_valued_survives():
    t = GTable(title="T", columns=[GColumn(leaf="2025")], rows=[
        _row(None, "section_header", 0, "Total income"),
        _row("8", "total", 0, "Total income", "100"),
    ])
    tg = {
        "all_rows_matched": True,
        "rows": [_tg_row(1, 0, "Total income"), _tg_row(1, 0, "Total income")],
        "title_clean": "T", "col_labels_clean": ["2025"],
    }
    result = apply_geometry(t, tg)
    assert result.applied is True
    assert len(result.table.rows) == 1
    kept = result.table.rows[0]
    assert kept.values, "the surviving row must be the valued one"
    assert kept.row_type == "total"
    assert any("merged" in w for w in result.warnings)


# --- A2: run with NO valued row -> first survives, rest dropped
def test_twin_merge_no_valued_row_keeps_first():
    t = GTable(title="T", columns=[GColumn(leaf="2025")], rows=[
        _row(None, "section_header", 0, "Commercial book total income"),
        _row(None, "section_header", 0, "Commercial book total income"),
    ])
    tg = {
        "all_rows_matched": True,
        "rows": [_tg_row(3, 0, "Commercial book total income"),
                 _tg_row(3, 0, "Commercial book total income")],
        "title_clean": None, "col_labels_clean": ["2025"],
    }
    result = apply_geometry(t, tg)
    assert result.applied is True
    assert len(result.table.rows) == 1
    assert result.table.rows[0].label == t.rows[0].label
    assert any("merged" in w for w in result.warnings)


# --- A3: TWO valued rows sharing a line_id -> NOT merged, warns
def test_twin_run_two_valued_rows_not_merged():
    t = GTable(title="T", columns=[GColumn(leaf="2025")], rows=[
        _row("1", "data", 1, "Basic", "10"),
        _row("2", "data", 1, "Basic", "20"),
    ])
    tg = {
        "all_rows_matched": True,
        "rows": [_tg_row(5, 1, "Basic"), _tg_row(5, 1, "Basic")],
        "title_clean": None, "col_labels_clean": ["2025"],
    }
    result = apply_geometry(t, tg)
    assert result.applied is True
    assert len(result.table.rows) == 2
    assert any("not merged" in w for w in result.warnings)


# --- A4: depth override — level always := side-car indent
def test_depth_override_ignores_model_level():
    t = GTable(title="T", columns=[GColumn(leaf="2025")], rows=[
        _row("1", "data", 0, "Net interest income", "10"),
        _row("2", "data", 5, "Net fee income", "20"),
    ])
    tg = {
        "all_rows_matched": True,
        "rows": [_tg_row(1, 1, "Net interest income"), _tg_row(2, 1, "Net fee income")],
        "title_clean": None, "col_labels_clean": ["2025"],
    }
    result = apply_geometry(t, tg)
    assert result.applied is True
    assert [r.level for r in result.table.rows] == [1, 1]


# --- A5: fallback gates ----------------------------------------------------
def _plain_table() -> GTable:
    return GTable(title="T", columns=[GColumn(leaf="2025")], rows=[
        _row("1", "data", 1, "A", "10"),
        _row("2", "data", 1, "B", "20"),
    ])


def _assert_unchanged_fallback(t: GTable, result) -> None:
    assert result.applied is False
    assert result.table.rows == t.rows
    assert [r.level for r in result.table.rows] == [r.level for r in t.rows]
    assert result.row_labels_clean == [None] * len(t.rows)


def test_fallback_gate_tg_none():
    t = _plain_table()
    result = apply_geometry(t, None)
    _assert_unchanged_fallback(t, result)


def test_fallback_gate_all_rows_not_matched():
    t = _plain_table()
    tg = {"all_rows_matched": False,
          "rows": [_tg_row(1, 0), _tg_row(2, 0)],
          "title_clean": "X", "col_labels_clean": ["2025"]}
    result = apply_geometry(t, tg)
    _assert_unchanged_fallback(t, result)


def test_fallback_gate_row_count_mismatch():
    t = _plain_table()
    tg = {"all_rows_matched": True,
          "rows": [_tg_row(1, 0)],   # 1 side-car row for a 2-row table
          "title_clean": "X", "col_labels_clean": ["2025"]}
    result = apply_geometry(t, tg)
    _assert_unchanged_fallback(t, result)
    assert any("falling back to model levels" in w for w in result.warnings)


def test_fallback_gate_missing_indent():
    t = _plain_table()
    tg = {"all_rows_matched": True,
          "rows": [_tg_row(1, 0), {"line_id": 2, "indent": None, "label_clean": "B"}],
          "title_clean": "X", "col_labels_clean": ["2025"]}
    result = apply_geometry(t, tg)
    _assert_unchanged_fallback(t, result)
    assert any("falling back to model levels" in w for w in result.warnings)


# --- A6: title_clean / col_labels_clean survive even when applied=False,
#         col_labels_clean is padded/truncated to len(columns)
def test_title_and_col_clean_survive_fallback_and_are_padded():
    t = GTable(title="T", columns=[GColumn(leaf="A"), GColumn(leaf="B"),
                                    GColumn(leaf="C")], rows=[
        _row("1", "data", 1, "X", "10", "20", "30"),
    ])
    tg = {"all_rows_matched": False,
          "rows": [_tg_row(1, 0)],
          "title_clean": "Clean Title", "col_labels_clean": ["A clean"]}
    result = apply_geometry(t, tg)
    assert result.applied is False
    assert result.title_clean == "Clean Title"
    assert len(result.col_labels_clean) == len(t.columns) == 3
    assert result.col_labels_clean == ["A clean", None, None]

    # truncation: side-car has MORE entries than columns
    tg2 = dict(tg, col_labels_clean=["A", "B", "C", "D", "E"])
    result2 = apply_geometry(t, tg2)
    assert len(result2.col_labels_clean) == 3
    assert result2.col_labels_clean == ["A", "B", "C"]


# --- A7: purity — input not mutated, labels never rewritten
def test_purity_input_not_mutated_and_labels_verbatim():
    t = GTable(title="T", columns=[GColumn(leaf="2025")], rows=[
        _row("1", "data", 0, "Return on equity4, 5", "10"),
        _row("2", "data", 0, "Net book value5", "20"),
    ])
    before = copy.deepcopy(t)
    tg = {
        "all_rows_matched": True,
        "rows": [_tg_row(1, 0, "Return on equity"), _tg_row(2, 0, "Net book value")],
        "title_clean": "T", "col_labels_clean": ["2025"],
    }
    result = apply_geometry(t, tg)

    # input GTable untouched
    assert t == before
    assert t.rows[0].label == "Return on equity4, 5"
    assert t.rows[1].label == "Net book value5"

    # verbatim labels survive on the OUTPUT too — footnote markers included
    assert result.table.rows[0].label == "Return on equity4, 5"
    assert result.table.rows[1].label == "Net book value5"
    # the clean labels ride alongside, never replacing GRow.label
    assert result.row_labels_clean == ["Return on equity", "Net book value"]


# ===========================================================================
# PART B — row_parents_by_position(skip_terminal=False): the DBS 'Of which:
# Net interest income' under 'Total income' fix.
# ===========================================================================
def test_skip_terminal_false_parents_to_preceding_total():
    rows = [
        _row("8", "total", 0, "Total income", "300"),
        _row("9", "data", 1, "Of which: Net interest income", "150"),
    ]

    # skip_terminal=True: a `total` row that HEADS A BLOCK (aggregates nothing
    # and is immediately followed by deeper rows) is now a legitimate parent.
    # CONTRACT CHANGED 2026-08-05 — this used to assert [None, None]. The old
    # blanket skip is what orphaned 1,055 rows on the model path and mis-parented
    # DBS 3Q25's ECL rows to 'Amortisation of intangible assets'. The extractor
    # itself disagrees with the old behaviour: DBS 4Q25 emits parent='h3' for
    # 'Of which: Net interest income', i.e. Total income — exactly this case, and
    # the defect lineage_identity_map.csv logs for pnl.nii.net.
    parents_default = row_parents_by_position(rows)
    assert parents_default == [None, 0]

    # geometry depths (skip_terminal=False): unchanged.
    parents_geom = row_parents_by_position(rows, skip_terminal=False)
    assert parents_geom == [None, 0]

    # PROTECTION PRESERVED: a total that genuinely AGGREGATES the rows above it
    # is terminal and must never become a parent — the DEBTS ISSUED defect
    # ('Due within/after 1 year' parenting to the preceding Total). sums_to maps
    # member row_id -> total row_id, so the total appears among its VALUES.
    parents_terminal = row_parents_by_position(rows, sums_to={2: 1})
    assert parents_terminal == [None, None]


# ===========================================================================
# PART C — end-to-end loader integration against the real DBS 1Q26 fixtures.
# ===========================================================================
_SECTION_IDS = ("key_financial_ratios_2_3", "per_share_data_3",
                "selected_balance_sheet_items_m", "selected_income_statement_items_m")
_DOC_ID = "DBS_1Q26_trading_update_geomtest"


def _load_fixture_db(tmp_path: Path) -> sqlite3.Connection:
    """Stand up a fresh schema_v7 DB with the document/section rows the 4
    audit units need, then load_units() them — mirrors test_load_v7.py's
    fixture machinery (executescript schema_v7.sql + insert document/section)."""
    import run_doc  # local import: pipeline/ is on sys.path via the insert above

    db_path = tmp_path / "geom_load.db"
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA_V7.read_text())
    con.execute(
        "INSERT INTO document(doc_id,institution,doc_family,doc_period) "
        "VALUES (?,?,?,?)", (_DOC_ID, "DBS Group Holdings Ltd", "financial_stmt",
                             "2026-03-31"))
    for sid in _SECTION_IDS:
        con.execute(
            "INSERT INTO section(doc_id,section_id,section_no,section_title,"
            "section_level,parent_section,seq) VALUES (?,?,?,?,1,NULL,1)",
            (_DOC_ID, sid, sid, sid))
    con.commit()
    con.close()

    units = run_doc.build_units_from_audit(_AUDIT_ROOT)
    assert len(units) == 4, units
    load_units(str(db_path), _DOC_ID, units)

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _row_parent_label(cur: sqlite3.Cursor, table_id: str, label: str) -> str | None:
    """The row_leaf_label of `label`'s parent in `table_id`, or None."""
    parent_id = cur.execute(
        "SELECT row_parent FROM row_dim WHERE table_id = ? AND row_leaf_label = ?",
        (table_id, label)).fetchone()
    assert parent_id is not None, f"no row {label!r} in {table_id}"
    if parent_id[0] is None:
        return None
    return cur.execute(
        "SELECT row_leaf_label FROM row_dim WHERE table_id = ? AND row_id = ?",
        (table_id, parent_id[0])).fetchone()[0]


def test_c1_all_tables_hierarchy_source_geometry(tmp_path):
    _require_fixtures()
    con = _load_fixture_db(tmp_path)
    sources = con.execute("SELECT table_id, hierarchy_source FROM table_t").fetchall()
    assert len(sources) == 4
    assert all(src == "geometry" for _, src in sources), sources
    con.close()


def test_c2_income_statement_parentage(tmp_path):
    _require_fixtures()
    con = _load_fixture_db(tmp_path)
    cur = con.cursor()
    tid = cur.execute(
        "SELECT table_id FROM table_t WHERE table_id LIKE "
        "'%selected_income_statement_items_m%'").fetchone()[0]

    assert _row_parent_label(cur, tid, "Of which: Net interest income") == "Total income"

    assert _row_parent_label(cur, tid, "Net fee and commission income") == \
        "Commercial book total income"
    assert _row_parent_label(cur, tid, "Treasury customer sales and other income") == \
        "Commercial book total income"
    # 'Net interest income' repeats (Commercial book AND Markets trading
    # blocks); row_id 2 is the Commercial-book occurrence.
    parent_id = cur.execute(
        "SELECT row_parent FROM row_dim WHERE table_id = ? AND row_leaf_label = "
        "'Net interest income' ORDER BY row_id LIMIT 1", (tid,)).fetchone()[0]
    parent_label = cur.execute(
        "SELECT row_leaf_label FROM row_dim WHERE table_id = ? AND row_id = ?",
        (tid, parent_id)).fetchone()[0]
    assert parent_label == "Commercial book total income"

    for label in ("Expenses", "Profit before tax", "Net profit", "Reported net profit"):
        assert _row_parent_label(cur, tid, label) is None, label
    con.close()


def test_c3_per_share_data_parentage(tmp_path):
    _require_fixtures()
    con = _load_fixture_db(tmp_path)
    cur = con.cursor()
    tid = cur.execute(
        "SELECT table_id FROM table_t WHERE table_id LIKE "
        "'%per_share_data_3%'").fetchone()[0]

    assert _row_parent_label(cur, tid, "Net book value5") is None
    # 'Basic'/'Diluted8' each appear twice (Earnings block, Reported earnings
    # block) — the FIRST occurrence (lowest row_id) is the Earnings block.
    assert _row_parent_label(cur, tid, "Basic") == "Earnings2"

    diluted_ids = [rid for (rid,) in cur.execute(
        "SELECT row_id FROM row_dim WHERE table_id = ? AND row_leaf_label = "
        "'Diluted8' ORDER BY row_id", (tid,)).fetchall()]
    assert len(diluted_ids) == 2, diluted_ids
    parent_labels = []
    for rid in diluted_ids:
        pid = cur.execute(
            "SELECT row_parent FROM row_dim WHERE table_id = ? AND row_id = ?",
            (tid, rid)).fetchone()[0]
        parent_labels.append(cur.execute(
            "SELECT row_leaf_label FROM row_dim WHERE table_id = ? AND row_id = ?",
            (tid, pid)).fetchone()[0])
    assert parent_labels == ["Earnings2", "Reported earnings"], parent_labels
    con.close()


def test_c4_twin_merge_row_and_cell_totals(tmp_path):
    _require_fixtures()
    con = _load_fixture_db(tmp_path)
    cur = con.cursor()
    tid = cur.execute(
        "SELECT table_id FROM table_t WHERE table_id LIKE "
        "'%selected_income_statement_items_m%'").fetchone()[0]
    n_rows = cur.execute(
        "SELECT COUNT(*) FROM row_dim WHERE table_id = ?", (tid,)).fetchone()[0]
    assert n_rows == 20   # not 22 — the phantom section_header/data twins merged

    total_rows = cur.execute("SELECT COUNT(*) FROM row_dim").fetchone()[0]
    total_cells = cur.execute("SELECT COUNT(*) FROM cell_fact").fetchone()[0]
    assert total_rows == 47
    assert total_cells == 201
    con.close()


def test_c5_row_leaf_label_clean_strips_footnotes_verbatim_untouched(tmp_path):
    _require_fixtures()
    con = _load_fixture_db(tmp_path)
    cur = con.cursor()

    cases = {
        "Return on equity4, 5": "Return on equity",
        "Earnings2": "Earnings",
        "Net book value5": "Net book value",
        "Provision for CSR¹": "Provision for CSR",
    }
    for verbatim, clean in cases.items():
        row = cur.execute(
            "SELECT row_leaf_label, row_leaf_label_clean FROM row_dim "
            "WHERE row_leaf_label = ?", (verbatim,)).fetchone()
        assert row is not None, verbatim
        assert row[0] == verbatim, row
        assert row[1] == clean, row
    con.close()


def test_c6_row_lineage_registry_uses_clean_label(tmp_path):
    _require_fixtures()
    con = _load_fixture_db(tmp_path)
    cur = con.cursor()
    tid = cur.execute(
        "SELECT table_id FROM table_t WHERE table_id LIKE "
        "'%key_financial_ratios%'").fetchone()[0]
    hdr_id = cur.execute(
        "SELECT row_lineage_id FROM row_dim WHERE table_id = ? AND "
        "row_leaf_label = 'Return on equity4, 5'", (tid,)).fetchone()[0]
    lvl1 = cur.execute(
        "SELECT lvl1 FROM row_lineage WHERE row_lineage_id = ?", (hdr_id,)).fetchone()[0]
    assert lvl1 == "Return on equity"
    con.close()


def test_c7_table_title_clean_strips_footnotes(tmp_path):
    _require_fixtures()
    con = _load_fixture_db(tmp_path)
    cur = con.cursor()
    row = cur.execute(
        "SELECT table_title, table_title_clean FROM table_t WHERE table_id LIKE "
        "'%key_financial_ratios%'").fetchone()
    assert row[0] == "Key financial ratios (%)2, 3"
    assert row[1] == "Key financial ratios (%)"
    con.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
