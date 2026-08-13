"""Tests for the PRINTED-PARENT resolution path: load_v7.resolve_printed_parents
and its consumption inside row_parents_by_position / _load_table.

WHY this file exists: as of 2026-08-05 (commit 01151d1) `GRow.parent` stopped
being a cross-check that only ever warned and became LOAD-BEARING hierarchy
input — when the extractor supplies a resolvable reference it now OVERRIDES the
positional walk. That contract had zero coverage. Everything pinned here is a
rule the loader now silently depends on.

WHAT is pinned, and WHY:
  Part A — resolve_printed_parents in isolation (synthetic GRow lists, no PDF,
    no DB). The `hN` decoding (headers are POSITIONAL, section_header rows do
    not consume a slot), the literal-label fallback (nearest preceding match),
    and the shallower-than-child guard that DROPS a bad reference rather than
    clamping it.
  Part B — precedence inside row_parents_by_position: where printed and
    positional disagree and printed is valid, printed wins. This is the DBS 4Q25
    'Of which: Net interest income' -> 'Total income' case (the pnl.nii.net
    defect in lineage_identity_map.csv).
  Part C — the RESIDUAL warning, end-to-end through load_units against a
    synthetic parsed.json + temp schema_v7 DB. Under the new contract a
    resolvable printed parent is NOT a warnable event; only an UNRESOLVABLE
    reference is (position silently decided instead), and even that is
    suppressed under geometry, where GRow.parent echoes the model's own level
    scheme and disagrees by construction.

Run:  python -m pytest findociq/pipeline/stage2_load/test_printed_parents.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path

from stage2_load.load_v7 import (  # noqa: E402
    load_units, resolve_printed_parents, row_parents_by_position,
)
from stage1_extract.chunk.schema import GCell, GColumn, GRow, GTable  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_SCHEMA_V7 = _REPO / "findociq/schema/schema_v7.sql"


def _row(level: int, label: str, *vals: str, row_type: str = "data",
         parent: str | None = None) -> GRow:
    return GRow(row_type=row_type, level=level, label=label, parent=parent,
                values=[GCell(value=v) for v in vals])


# ===========================================================================
# PART A — resolve_printed_parents, pure / synthetic
# ===========================================================================

# --- A1: hN DECODING -------------------------------------------------------
# `hN` is the Nth HEADER row, where a header is a row immediately followed by a
# strictly deeper row. section_header rows are excluded from the numbering.
def _h_fixture() -> list[GRow]:
    return [
        _row(0, "Sec", row_type="section_header"),   # 0 — NOT an hN slot
        _row(1, "Alpha", "1"),                       # 1 — h1
        _row(2, "a1", "2", parent="h1"),             # 2
        _row(1, "Beta", "3"),                        # 3 — h2
        _row(2, "b1", "4"),                          # 4
        _row(1, "Gamma", "5"),                       # 5 — h3
        _row(2, "g1", "6", parent="h3"),             # 6
        _row(2, "g2", "7", parent="h2"),             # 7
    ]


def test_a1_hn_decoding_h1_h2_h3():
    rows = _h_fixture()
    out = resolve_printed_parents(rows)
    assert out == {2: 1, 6: 5, 7: 3}, out


def test_a1_section_header_does_not_consume_an_hn_slot():
    """If 'Sec' consumed h1, row 2's parent='h1' would resolve to index 0."""
    rows = _h_fixture()
    out = resolve_printed_parents(rows)
    assert out[2] == 1, out
    assert 0 not in out.values(), "section_header must not be numbered as h1"

    # Control: make the very same row a plain data row and it DOES take h1,
    # shifting every slot by one — proves the exclusion is what drove A1.
    rows[0] = _row(0, "Sec", row_type="data")
    shifted = resolve_printed_parents(rows)
    assert shifted[2] == 0, shifted     # h1 is now 'Sec'
    assert shifted[6] == 3, shifted     # h3 is now 'Beta'


def test_a1_hn_reference_pointing_forward_is_dropped():
    """`hN` may only resolve to a header EARLIER than the child (p < i)."""
    rows = [
        _row(1, "Alpha", "1", parent="h2"),   # h2 is index 2 — ahead of it
        _row(2, "a1", "2"),
        _row(1, "Beta", "3"),                 # h2
        _row(2, "b1", "4"),
    ]
    assert resolve_printed_parents(rows) == {}


# --- A2: LITERAL-LABEL FALLBACK -------------------------------------------
# The extractor sometimes emits the parent's LABEL rather than an hN reference
# (DBS 2Q25 CUSTOMER DEPOSITS ends with four rows carrying parent='Total').
# It must bind to the NEAREST preceding row with that label.
def test_a2_literal_label_binds_to_nearest_preceding_match():
    rows = [
        _row(0, "Total", "10", row_type="total"),   # 0 — the FAR 'Total'
        _row(1, "x", "1"),
        _row(0, "Total", "20", row_type="total"),   # 2 — the NEAR 'Total'
        _row(1, "y", "2", parent="Total"),          # 3
    ]
    out = resolve_printed_parents(rows)
    assert out == {3: 2}, out
    assert out[3] != 0, "must bind to the nearer 'Total', not the first one"


def test_a2_literal_label_is_case_and_whitespace_insensitive():
    rows = [
        _row(0, "Total", "10", row_type="total"),
        _row(1, "y", "2", parent="  total  "),
    ]
    assert resolve_printed_parents(rows) == {1: 0}


def test_a2_literal_label_matching_nothing_is_dropped():
    rows = [
        _row(0, "Total", "10", row_type="total"),
        _row(1, "y", "2", parent="Grand Total"),
    ]
    assert resolve_printed_parents(rows) == {}


# --- A3: SHALLOWER-THAN-CHILD GUARD ---------------------------------------
# A reference that resolves to a row at depth >= the child's is REJECTED
# outright. It is not clamped, not re-pointed at an ancestor, not mapped to
# None — the key is simply ABSENT, and the positional walk decides.
def test_a3_hn_resolving_to_same_level_is_dropped_not_clamped():
    rows = [
        _row(1, "Alpha", "1"),                  # 0 — h1 (followed by level 2)
        _row(2, "a1", "2"),
        _row(1, "Beta", "3", parent="h1"),      # 2 — same level as h1 -> reject
    ]
    out = resolve_printed_parents(rows)
    assert out == {}, out
    assert 2 not in out, "the mapping must be dropped, not clamped"

    # positional decides, unaffected: 'Beta' has no level-0 ancestor -> None
    assert row_parents_by_position(rows, printed=out) == [None, 0, None]


def test_a3_literal_label_at_same_or_deeper_level_is_dropped():
    # same level
    same = [
        _row(0, "Total", "10", row_type="total"),
        _row(0, "y", "2", parent="Total"),
    ]
    assert resolve_printed_parents(same) == {}

    # DEEPER than the child — and the nearest-match `break` means the search
    # does NOT walk past it to a shallower row with the same label.
    deeper = [
        _row(0, "Total", "10", row_type="total"),   # shallower, but farther
        _row(2, "Total", "5", row_type="total"),    # nearer, too deep
        _row(1, "y", "2", parent="Total"),
    ]
    assert resolve_printed_parents(deeper) == {}


# ===========================================================================
# PART B — precedence: printed WINS over the positional walk
# ===========================================================================
def _nii_fixture() -> list[GRow]:
    """DBS 'Selected income statement items', reduced to the disputed rows.

    Headers: 0 = h1 'Commercial book total income', 2 = h2 'Markets trading
    income', 4 = h3 'Total income'. The extractor emits parent='h3' for 'Of
    which: Net interest income'; the positional walk (with 'Total income'
    established as a genuine aggregating total) instead reaches back past it to
    the markets book. That is the pnl.nii.net defect."""
    return [
        _row(1, "Commercial book total income", "100"),        # 0 — h1
        _row(2, "Net interest income", "60"),                  # 1
        _row(1, "Markets trading income", "40"),               # 2 — h2
        _row(2, "Trading income", "40"),                       # 3
        _row(1, "Total income", "140", row_type="total"),      # 4 — h3
        _row(2, "Of which: Net interest income", "70",
             parent="h3"),                                     # 5
    ]


def test_b1_printed_wins_where_position_disagrees():
    rows = _nii_fixture()
    printed = resolve_printed_parents(rows)
    assert printed == {5: 4}, printed

    # sums_to={6: 5} (1-based row ids): 'Total income' AGGREGATES, so
    # _heads_a_block calls it terminal and the positional walk skips it,
    # landing on the markets book at index 2. THIS IS THE WRONG ANSWER.
    positional = row_parents_by_position(rows, sums_to={6: 5})
    assert positional[5] == 2, positional

    # With the printed parent supplied, the extractor's answer wins.
    resolved = row_parents_by_position(rows, sums_to={6: 5}, printed=printed)
    assert resolved[5] == 4, resolved
    assert resolved[:5] == positional[:5], "rows without a printed parent unchanged"


def test_b1_valid_printed_parent_is_not_a_warnable_event(tmp_path):
    """Agreement, or a valid printed parent that merely differs, must produce
    NO printed-parent warning — only an UNRESOLVABLE reference warns."""
    warnings = _load_synthetic(tmp_path, _nii_fixture())
    assert _printed_parent_warnings(warnings) == [], warnings


# --- REGRESSION GUARD: the short-circuit must not disturb the positional path
def test_b2_positional_path_unchanged_when_no_printed_parent():
    """The `if i in printed` short-circuit only fires for supplied rows. These
    are the cases test_load_v7.py and test_geometry_load.py already pin —
    re-asserted here with printed={} and with an unrelated printed entry, so a
    regression in the short-circuit is caught in THIS file too."""
    # terminal-total skip (the DEBTS ISSUED defect)
    debts = [
        _row(1, "A", "1"),
        _row(0, "Total", "1", row_type="total"),
        _row(1, "Due within 1 year", "1"),
    ]
    assert row_parents_by_position(debts, sums_to={1: 2}) == [None, None, None]
    assert row_parents_by_position(debts, sums_to={1: 2}, printed={}) == \
        [None, None, None]

    # notes nest only to notes
    notes = [_row(0, "Notes:", row_type="note"),
             _row(1, "Unsecured", row_type="note")]
    assert row_parents_by_position(notes, printed={}) == [None, 0]

    # skip_terminal=False (geometry depths): an indented row under a printed
    # total genuinely IS its child
    geom_rows = [_row(0, "Total income", "300", row_type="total"),
                 _row(1, "Of which: Net interest income", "150")]
    assert row_parents_by_position(geom_rows, skip_terminal=False) == [None, 0]
    assert row_parents_by_position(geom_rows, skip_terminal=False,
                                   printed={}) == [None, 0]

    # a printed entry for ONE row leaves every other row on the positional path
    mixed = _h_fixture()
    base = row_parents_by_position(mixed)
    with_printed = row_parents_by_position(mixed, printed={7: 3})
    assert with_printed[7] == 3
    assert with_printed[:7] == base[:7], (base, with_printed)


# ===========================================================================
# PART C — the RESIDUAL warning, end-to-end through load_units
# ===========================================================================
_DOC_ID = "TEST_PRINTED_PARENTS"
_SECTION_ID = "printed_parents_s1"


def _load_synthetic(tmp_path: Path, rows: list[GRow],
                    geometry: dict | None = None) -> list[str]:
    """Load a one-table synthetic document into a fresh schema_v7 DB and return
    load_units' warnings. `geometry` is the per-table side-car entry.

    Asserts the table actually LOADED: a table refused by a gate emits no
    printed-parent warning either, which would make every "no warning" assertion
    below pass for the wrong reason."""
    table = GTable(title="Synthetic", label_header="In $ millions",
                   columns=[GColumn(group=None, leaf="30 Jun 2025")],
                   rows=rows)
    payload: dict = {"tables": [table.model_dump()]}
    if geometry is not None:
        payload["geometry"] = {"tables": [geometry]}
    tmp_path.mkdir(parents=True, exist_ok=True)
    parsed = tmp_path / "parsed.json"
    parsed.write_text(json.dumps(payload))

    db_path = tmp_path / "printed_parents.db"
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA_V7.read_text())
    con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                "VALUES (?,?,?,?)",
                (_DOC_ID, "DBS Group Holdings Ltd", "financial_stmt", "2025-06-30"))
    con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                "section_level,parent_section,seq) VALUES (?,?,?,?,1,NULL,1)",
                (_DOC_ID, _SECTION_ID, "1", "Synthetic section"))
    con.commit()
    con.close()

    res = load_units(str(db_path), _DOC_ID, [{
        "section_id": _SECTION_ID, "pages": [1], "parsed_path": str(parsed),
    }])
    assert res["tables"] == 1 and res["skipped_tables"] == 0, res
    assert res["rows"] == len(rows) if geometry is None else res["rows"] > 0, res
    return list(res["warnings"])


def _printed_parent_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "printed parent" in w]


def _unresolvable_fixture() -> list[GRow]:
    """Three headers (indices 0, 2, 4) — so 'h9' cannot resolve — plus a
    literal label matching no preceding row."""
    return [
        _row(1, "Alpha", "1"),                                    # 0 — h1
        _row(2, "a1", "2"),                                       # 1
        _row(1, "Beta", "3"),                                     # 2 — h2
        _row(2, "b1", "4"),                                       # 3
        _row(1, "Gamma", "5"),                                    # 4 — h3
        _row(2, "g1", "6", parent="h9"),                          # 5 — dangling
        _row(2, "g2", "7", parent="Nowhere In This Table"),       # 6 — dangling
    ]


def test_c1_unresolvable_references_do_not_resolve():
    rows = _unresolvable_fixture()
    out = resolve_printed_parents(rows)
    assert 5 not in out and 6 not in out, out
    # position decides for both: nearest earlier level-1 row is 'Gamma' (4)
    parents = row_parents_by_position(rows, printed=out)
    assert parents[5] == 4 and parents[6] == 4, parents


def test_c2_residual_warning_fires_once_per_unresolvable_row(tmp_path):
    warnings = _load_synthetic(tmp_path, _unresolvable_fixture())
    pp = _printed_parent_warnings(warnings)
    assert len(pp) == 2, pp
    assert len([w for w in pp if "'h9'" in w]) == 1, pp
    assert len([w for w in pp if "Nowhere In This Table" in w]) == 1, pp
    assert all("position used instead" in w for w in pp), pp


def test_c3_residual_warning_suppressed_under_geometry(tmp_path):
    """GRow.parent echoes the model's OWN level scheme, so on a
    geometry-corrected table it disagrees by construction — one warning per
    reparented row. Suppressed on that branch only."""
    rows = _unresolvable_fixture()
    geometry = {
        "all_rows_matched": True,
        "rows": [{"line_id": i + 1, "indent": (r.level or 0) * 10,
                  "label_clean": r.label} for i, r in enumerate(rows)],
        "title_clean": "Synthetic",
        "col_labels_clean": ["30 Jun 2025"],
    }
    warnings = _load_synthetic(tmp_path, rows, geometry=geometry)
    assert _printed_parent_warnings(warnings) == [], warnings

    # SANITY: geometry really did apply. apply_geometry falls back SILENTLY on
    # some gates, so absence of warnings is not proof — read hierarchy_source.
    con = sqlite3.connect(tmp_path / "printed_parents.db")
    src = con.execute("SELECT hierarchy_source FROM table_t").fetchone()[0]
    con.close()
    assert src == "geometry", (
        f"hierarchy_source={src!r} — geometry did not apply, so this test "
        f"proved nothing about suppression")

    # CONTROL: the same rows WITHOUT the side-car do warn. Pins that the
    # suppression is the geometry branch, not the fixture being warning-free.
    assert len(_printed_parent_warnings(
        _load_synthetic(tmp_path / "nogeom", rows))) == 2


def test_c4_printed_parent_reaches_row_dim(tmp_path):
    """The end-to-end proof that this is hierarchy input and not a diagnostic:
    row_dim.row_parent for 'Of which: Net interest income' is 'Total income'."""
    rows = _nii_fixture()
    parsed_dir = tmp_path
    _load_synthetic(parsed_dir, rows)
    con = sqlite3.connect(parsed_dir / "printed_parents.db")
    cur = con.cursor()
    tid = cur.execute("SELECT table_id FROM table_t").fetchone()[0]
    pid = cur.execute(
        "SELECT row_parent FROM row_dim WHERE table_id = ? AND row_leaf_label = ?",
        (tid, "Of which: Net interest income")).fetchone()[0]
    assert pid is not None, "printed parent was dropped on the way to row_dim"
    parent_label = cur.execute(
        "SELECT row_leaf_label FROM row_dim WHERE table_id = ? AND row_id = ?",
        (tid, pid)).fetchone()[0]
    assert parent_label == "Total income", parent_label
    con.close()
