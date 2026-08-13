"""Tests for stage1_extract.chunk.geometry — printed-line ground truth for row hierarchy.

Runs offline against the local PDF
findociq/data/sources/financial_statements/DBS_1Q26_trading_update.pdf and the
real audit units under
findociq/outputs/fs/dbs_1Q26/audit/DBS_1Q26_trading_update. Ground truth
(char positions, superscript sizes/tops, ink_x0 cluster values) was verified
directly against pdfplumber page.chars for pages 6-7 before writing these
assertions — see the module docstring in geometry.py for the algorithm and
docs/specs for the design writeup.

Each test COPIES the relevant audit unit into a tmp dir before running
process_unit(), so this suite never mutates the checked-in fixtures.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path
from stage1_extract.chunk import geometry as G  # noqa: E402

_REPO = Path(__file__).resolve().parents[4]
_PDF = _REPO / "findociq/data/sources/financial_statements/DBS_1Q26_trading_update.pdf"
_AUDIT_ROOT = (_REPO / "findociq/outputs/fs/dbs_1Q26/audit/DBS_1Q26_trading_update")
_OCBC_PDF = (_REPO / "findociq/data/sources/financial_statements/"
             "OCBC_4Q25_Condensed_Financial_Statements.pdf")


def _copy_unit(tmp_path: Path, unit_name: str) -> Path:
    src = _AUDIT_ROOT / unit_name
    dst = tmp_path / unit_name
    shutil.copytree(src, dst)
    return dst


def _geometry_for(tmp_path: Path, unit_name: str) -> tuple[dict, dict]:
    """(parsed.json dict after processing, its geometry['tables'][0])."""
    unit_dir = _copy_unit(tmp_path, unit_name)
    G.process_unit(unit_dir)
    parsed = json.loads((unit_dir / "parsed.json").read_text())
    return parsed, parsed["geometry"]["tables"][0]


def _by_label(parsed: dict, geom_table: dict, label: str) -> list[dict]:
    """Every geometry row (in table order) whose parsed.json row has this
    exact label — a label may repeat (e.g. 'Basic')."""
    rows = parsed["tables"][0]["rows"]
    out = []
    for row, grow in zip(rows, geom_table["rows"]):
        if row["label"] == label:
            out.append(grow)
    return out


def _indent_of(parsed: dict, geom_table: dict, label: str) -> int:
    matches = _by_label(parsed, geom_table, label)
    assert matches, f"no row with label {label!r}"
    return matches[0]["indent"]


# ===========================================================================
# Fixture availability guard — these tests need the local PDF + DBS 1Q26
# audit fixtures. Skip cleanly (not fail) if the workstation doesn't have them.
# ===========================================================================
def _require_fixtures() -> None:
    if not _PDF.exists() or not _AUDIT_ROOT.exists():
        import pytest
        pytest.skip("DBS 1Q26 fixtures not present on this workstation")


# ===========================================================================
# 1) Income statement p.6 — indent clustering: exactly 2 levels, section
#    totals at depth 0, line items at depth 1.
# ===========================================================================
def test_income_statement_indent_levels(tmp_path):
    _require_fixtures()
    parsed, gt = _geometry_for(tmp_path, "selected_income_statement_items_m_p6")

    assert gt["all_rows_matched"] is True
    assert gt["band_calibrated"] is True

    depth0_labels = ["Commercial book total income", "Markets trading income",
                     "Total income", "Expenses"]
    depth1_labels = ["Net interest income", "Net fee and commission income",
                     "ECL Stage 3 (SP)"]

    for label in depth0_labels:
        assert _indent_of(parsed, gt, label) == 0, label
    for label in depth1_labels:
        assert _indent_of(parsed, gt, label) == 1, label

    all_indents = {r["indent"] for r in gt["rows"] if r["indent"] is not None}
    assert all_indents == {0, 1}, all_indents


# ===========================================================================
# 2) Per share data p.7 — 'Net book value' clusters with 'Earnings' /
#    'Reported earnings' (depth 0); 'Basic'/'Diluted' at depth 1.
# ===========================================================================
def test_per_share_data_indent_levels(tmp_path):
    _require_fixtures()
    parsed, gt = _geometry_for(tmp_path, "per_share_data_3_p7")

    assert gt["all_rows_matched"] is True

    net_book_value_indent = _indent_of(parsed, gt, "Net book value5")
    assert net_book_value_indent == 0

    for label in ("Basic", "Diluted8"):
        for grow in _by_label(parsed, gt, label):
            assert grow["indent"] == 1, (label, grow)


# ===========================================================================
# 3) Superscript strips (label_clean) + title_clean.
# ===========================================================================
def test_superscript_strips(tmp_path):
    _require_fixtures()
    parsed, gt = _geometry_for(tmp_path, "per_share_data_3_p7")
    assert gt["title_clean"] == "Per share data ($)"

    cases = {
        "Earnings2": "Earnings",
        "Diluted8": "Diluted",
        "Net book value5": "Net book value",
    }
    for raw_label, expected_clean in cases.items():
        matches = _by_label(parsed, gt, raw_label)
        assert matches, raw_label
        for grow in matches:
            assert grow["label_clean"] == expected_clean, (raw_label, grow)

    parsed2, gt2 = _geometry_for(tmp_path, "key_financial_ratios_2_3_p6")
    cases2 = {
        "Return on equity4, 5": "Return on equity",
        "Return on tangible equity4,5,6": "Return on tangible equity",
        "Provision for CSR¹": None,  # not in this table; checked below
    }
    for raw_label, expected_clean in cases2.items():
        if expected_clean is None:
            continue
        matches = _by_label(parsed2, gt2, raw_label)
        assert matches, raw_label
        assert matches[0]["label_clean"] == expected_clean, raw_label

    parsed3, gt3 = _geometry_for(tmp_path, "selected_income_statement_items_m_p6")
    matches = _by_label(parsed3, gt3, "Provision for CSR¹")
    assert matches, "Provision for CSR¹"
    assert matches[0]["label_clean"] == "Provision for CSR"


# ===========================================================================
# 4) Trap cases — labels containing digits/slashes/parens that are NOT
#    footnote markers must survive untouched.
# ===========================================================================
def test_trap_cases_untouched(tmp_path):
    _require_fixtures()
    parsed, gt = _geometry_for(tmp_path, "key_financial_ratios_2_3_p6")
    traps = ["Common Equity Tier 1 (CET-1) ratio", "Total allowances/ NPA",
             "SP for loans/ average loans (bp)"]
    for label in traps:
        matches = _by_label(parsed, gt, label)
        assert matches, label
        assert matches[0]["label_clean"] == label, label

    parsed2, gt2 = _geometry_for(tmp_path, "selected_income_statement_items_m_p6")
    for label in ["ECL Stage 3 (SP)", "ECL Stage 1 and 2 (GP)"]:
        matches = _by_label(parsed2, gt2, label)
        assert matches, label
        assert matches[0]["label_clean"] == label, label


# ===========================================================================
# 5) Duplicate-label rows: distinct printed lines get distinct line_ids;
#    phantom section_header row + its data twin share the SAME line_id.
# ===========================================================================
def test_duplicate_labels_line_ids(tmp_path):
    _require_fixtures()
    parsed, gt = _geometry_for(tmp_path, "per_share_data_3_p7")
    basic_matches = _by_label(parsed, gt, "Basic")
    assert len(basic_matches) == 2
    assert basic_matches[0]["line_id"] != basic_matches[1]["line_id"]
    assert None not in (basic_matches[0]["line_id"], basic_matches[1]["line_id"])

    parsed2, gt2 = _geometry_for(tmp_path, "selected_income_statement_items_m_p6")
    twin_matches = _by_label(parsed2, gt2, "Commercial book total income")
    assert len(twin_matches) == 2
    assert twin_matches[0]["line_id"] == twin_matches[1]["line_id"]
    assert twin_matches[0]["line_id"] is not None


# ===========================================================================
# 6) Monotone scan tolerates an unmatched row without breaking alignment of
#    the rows around it.
# ===========================================================================
def test_unmatched_row_does_not_break_monotone_scan():
    lines = [
        {"raw_norm": "alpha", "clean_norm": "alpha", "raw_tail": "alpha",
         "clean_tail": "alpha", "ink_x0": 10.0, "has_values": False,
         "raw_text": "Alpha", "clean_text": "Alpha"},
        {"raw_norm": "gamma", "clean_norm": "gamma", "raw_tail": "gamma",
         "clean_tail": "gamma", "ink_x0": 10.0, "has_values": False,
         "raw_text": "Gamma", "clean_text": "Gamma"},
    ]
    result, spans = G.align_rows_to_lines(["Alpha", "Beta (not printed)", "Gamma"], lines)
    assert result == [0, None, 1]
    assert spans == [1, 1, 1]


def _rec(raw: str, clean: str, ink_x0: float | None, has_values: bool) -> dict:
    """A synthetic printed-line record with exactly the fields
    build_table_lines() would produce, for direct align_rows_to_lines()
    unit tests (no PDF needed)."""
    return {
        "raw_text": raw, "clean_text": clean,
        "raw_norm": G.norm(raw), "clean_norm": G.norm(clean),
        "raw_tail": G.norm_tail_stripped(raw), "clean_tail": G.norm_tail_stripped(clean),
        "ink_x0": ink_x0, "has_values": has_values,
    }


# ===========================================================================
# 6b) Wrapped (word-wrapped) label merge — synthetic, DBS-fixture-style
#     values (72.45/77.97-ish spacing seen on real OCBC p.4). Exercises
#     conditions (a)-(d) from the wrap-merge spec, including two NEGATIVE
#     cases that must NOT merge.
# ===========================================================================
def test_wrap_merge_positive_and_conditions():
    # Positive: 'Operating profit before allowances and' (no values, ink_x0
    # 74.66) wraps onto 'amortisation 4,334 ...' (HAS values, ink_x0 77.97 >=
    # 74.66) -> merges into one row, span=2, line_id = the FIRST line.
    lines = [
        _rec("Total income", "Total income", 74.66, True),
        _rec("Operating profit before allowances and",
             "Operating profit before allowances and", 74.66, False),
        _rec("amortisation 4,334 4,195 8,732 8,731",
             "amortisation 4,334 4,195 8,732 8,731", 77.97, True),
        _rec("Amortisation of intangible assets (10) (12)",
             "Amortisation of intangible assets (10) (12)", 74.66, True),
    ]
    row_labels = [
        "Total income",
        "Operating profit before allowances and amortisation",
        "Amortisation of intangible assets",
    ]
    matches, spans = G.align_rows_to_lines(row_labels, lines)
    assert matches == [0, 1, 3]
    assert spans == [1, 2, 1]


def test_wrap_merge_negative_first_line_has_values():
    """Condition (a): if the EARLIER line already carries value-band content,
    it is not a genuine wrapped label (real wraps never have values before
    the continuation) — must NOT merge, row stays unmatched."""
    lines = [
        _rec("Operating profit before allowances and 100",
             "Operating profit before allowances and 100", 74.66, True),  # has_values=True
        _rec("amortisation 4,334", "amortisation 4,334", 77.97, True),
    ]
    matches, spans = G.align_rows_to_lines(
        ["Operating profit before allowances and amortisation"], lines)
    assert matches == [None]
    assert spans == [1]


def test_wrap_merge_negative_continuation_left_of_start():
    """Condition (d): a continuation line whose ink_x0 is LEFT of the first
    line's must NOT merge (wraps hang at or right of the start column)."""
    lines = [
        _rec("Operating profit before allowances and",
             "Operating profit before allowances and", 74.66, False),
        _rec("amortisation 4,334", "amortisation 4,334", 70.0, True),  # 70.0 < 74.66
    ]
    matches, spans = G.align_rows_to_lines(
        ["Operating profit before allowances and amortisation"], lines)
    assert matches == [None]
    assert spans == [1]


# ===========================================================================
# 6c) Real OCBC p.4 case — the wrapped label that motivated this feature.
# 'Operating profit before allowances and' / 'amortisation  4,334 ...' on the
# actual printed page, driven through the full compute_table_geometry()
# pipeline with synthetic "model rows" (there is no parsed.json for OCBC —
# only the source PDF, materialized during the cross-bank threshold check).
# ===========================================================================
def test_ocbc_p4_wrapped_label_real_pdf():
    if not _OCBC_PDF.exists():
        import pytest
        pytest.skip("OCBC source PDF not materialized on this workstation")

    table = {
        "columns": [{"leaf": "2H 2025"}, {"leaf": "2H 2024"}, {"leaf": "2025"}, {"leaf": "2024"}],
        "rows": [
            {"label": "Interest income"},
            {"label": "Interest expense"},
            {"label": "Net interest income"},
            {"label": "Total income"},
            {"label": "Total operating expenses"},
            {"label": "Operating profit before allowances and amortisation"},
            {"label": "Amortisation of intangible assets"},
            {"label": "Allowances for loans and other assets"},
            {"label": "Operating profit after allowances and amortisation"},
            {"label": "Profit before income tax"},
            {"label": "Profit for the period/year"},
            {"label": "Equity holders of the Bank"},
            {"label": "Non-controlling interests"},
            {"label": "Basic"},
            {"label": "Diluted"},
        ],
    }
    with pdfplumber.open(str(_OCBC_PDF)) as pdf:
        page = pdf.pages[3]  # p.4 (0-indexed)
        geo = G.compute_table_geometry(table, [page])

    by_label = {r["label"]: g for r, g in zip(table["rows"], geo["rows"])}

    wrapped = by_label["Operating profit before allowances and amortisation"]
    assert wrapped["line_id"] is not None, "wrapped label failed to align at all"
    assert wrapped["label_clean"] == "Operating profit before allowances and amortisation"

    # Neighbours on either side still align to their OWN (unmerged) lines,
    # in increasing line_id order — the merge did not disturb the monotone
    # scan for the rest of the table.
    before = by_label["Total operating expenses"]
    after = by_label["Amortisation of intangible assets"]
    assert before["line_id"] is not None
    assert after["line_id"] is not None
    assert before["line_id"] < wrapped["line_id"] < after["line_id"]

    # The wrap must not distort indent: it clusters at the SAME depth as the
    # other top-level income-statement lines (e.g. 'Net interest income'),
    # since ink_x0 is taken from the FIRST (unindented) physical line.
    nii = by_label["Net interest income"]
    assert nii["line_id"] is not None
    assert wrapped["indent"] == nii["indent"]

    # The second wrap on this same page ('Operating profit after allowances
    # and' / 'amortisation ...') also aligns — not just the first occurrence.
    wrapped2 = by_label["Operating profit after allowances and amortisation"]
    assert wrapped2["line_id"] is not None
    assert wrapped2["line_id"] > wrapped["line_id"]


# ===========================================================================
# 7) Indent clustering — single-linkage on ink_x0, threshold-driven, not
#    exact-equality-driven (values validated char-level: {74.66} vs
#    {84.74, 88.34} for the DBS income statement -> 2 clusters).
# ===========================================================================
def test_indent_clustering_thresholds():
    threshold = 0.5 * 9.0  # 4.5pt, DBS body char size
    values = [74.66, 74.66, 84.74, 88.34]
    mapping = G.cluster_indent_levels(values, threshold)
    assert mapping[74.66] == 0
    assert mapping[84.74] == 1
    assert mapping[88.34] == 1  # 88.34 - 84.74 = 3.6 <= 4.5, chains into cluster 1
    assert len(set(mapping.values())) == 2


# ===========================================================================
# 8) Backfill CLI / find_units — processes every unit under an audit root
#    and reports match stats without raising.
# ===========================================================================
def test_backfill_processes_every_unit(tmp_path):
    _require_fixtures()
    for name in ("selected_income_statement_items_m_p6", "per_share_data_3_p7",
                 "key_financial_ratios_2_3_p6", "selected_balance_sheet_items_m_p6"):
        _copy_unit(tmp_path, name)

    units = G.find_units(tmp_path)
    assert len(units) == 4

    rc = G.main(["--audit-root", str(tmp_path)])
    assert rc == 0
    for unit_dir in units:
        parsed = json.loads((unit_dir / "parsed.json").read_text())
        assert parsed["geometry"]["source"] == "pages.pdf"
        for gt in parsed["geometry"]["tables"]:
            assert gt["all_rows_matched"] is True


# ===========================================================================
# 9) Missing source -> {"source": "unavailable"}, never a guess.
# ===========================================================================
def test_missing_source_is_unavailable(tmp_path):
    _require_fixtures()
    unit_dir = _copy_unit(tmp_path, "selected_income_statement_items_m_p6")
    (unit_dir / "pages.pdf").unlink()
    meta = json.loads((unit_dir / "meta.json").read_text())
    meta["document"] = "no_such_document_anywhere.pdf"
    (unit_dir / "meta.json").write_text(json.dumps(meta))

    G.process_unit(unit_dir)
    parsed = json.loads((unit_dir / "parsed.json").read_text())
    assert parsed["geometry"] == {"source": "unavailable"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# Label/value boundary — the remainder test that replaced "next char is a space"
# ---------------------------------------------------------------------------
def test_boundary_accepts_nil_value_flush_against_label():
    """UOB/OCBC print a nil as a bare dash with NO space before it. The old
    space-only test lost the row, and under apply_geometry's all-or-nothing
    rule that cost geometry for the whole table."""
    assert G._startswith_boundary("bills and drafts payable- - -",
                                  "bills and drafts payable")
    assert G._startswith_boundary("debts issued- 3,599-", "debts issued")


def test_boundary_rejects_a_shorter_label_that_prefixes_a_longer_one():
    """The failure the space test could not see: 'debts' matched the printed
    line for 'debts issued' because a space follows 'debts'. The remainder
    decides — ' issued- 3,599-' is more label, not values."""
    assert not G._startswith_boundary("debts issued- 3,599-", "debts")
    assert not G._startswith_boundary("total assets 1,234", "total")
    assert not G._startswith_boundary("net profit before tax 5,657", "net profit")


def test_boundary_accepts_the_marks_printed_instead_of_a_number():
    """OCBC prints '#' for a value below its rounding floor; banks print 'nm'
    for not-meaningful and '>100' for a capped change."""
    assert G._startswith_boundary("shares issued to non-executive directors # #",
                                  "shares issued to non-executive directors")
    assert G._startswith_boundary("cost/income ratio nm", "cost/income ratio")
    assert G._startswith_boundary("overseas profit before tax contribution >100",
                                  "overseas profit before tax contribution")
    assert G._startswith_boundary("basic 4.19 4.11 3.30", "basic")


def test_boundary_leaves_a_hyphen_inside_a_label_alone():
    assert G._startswith_boundary("non-controlling interests 975",
                                  "non-controlling interests")


# ---------------------------------------------------------------------------
# Invisible glyphs — colour is meaningless without its colour space
# ---------------------------------------------------------------------------
def _char(text, fill, ncs, x0=100.0):
    return {"text": text, "non_stroking_color": fill, "ncs": ncs,
            "x0": x0, "x1": x0 + 5, "top": 100.0, "bottom": 108.0, "size": 8.6}


def test_devicegray_white_on_a_white_page_is_invisible():
    assert G.is_invisible(_char("L", (1.0,), "DeviceGray"), [])
    assert not G.is_invisible(_char("L", (0,), "DeviceGray"), [])


def test_separation_one_is_full_colorant_not_white():
    """OCBC sets its body text in a Separation space, where (1.0,) is solid
    black. Reading the number without the space marked every OCBC page as
    entirely invisible and deleted the printed text from `clean_text`."""
    assert not G.is_invisible(_char("O", (1.0,), "Separation"), [])


def test_white_text_on_a_dark_backdrop_stays_visible():
    dark = [(0.0, 90.0, 600.0, 120.0, (0.0, 0.0, 0.0))]
    assert not G.is_invisible(_char("L", (1.0,), "DeviceGray"), dark)


def test_uob_2q26_phantom_less_is_dropped_from_the_printed_truth():
    """The real defect, end to end on the real PDF: page 5 carries a white
    'Less:' on the allowance row. `raw_text` must KEEP it (the extractor read
    the same text layer, and alignment matches against raw), while
    `clean_text` and `ink_x0` must reflect only what is painted."""
    pdf_path = (_REPO / "findociq/data/sources/financial_statements/"
                "UOB_2Q26_Condensed_Interim_Financial_Statements.pdf")
    if not pdf_path.exists():
        import pytest
        pytest.skip("UOB 2Q26 source PDF not available")
    with pdfplumber.open(str(pdf_path)) as pdf:
        lines = G.build_table_lines([pdf.pages[4]], None)
    row = next(l for l in lines if "llowance for credit" in l["raw_text"])
    sibling = next(l for l in lines if "mortisation of intangible" in l["raw_text"])

    assert row["raw_text"] == "Less: Allowance for credit and other losses"
    assert row["clean_text"] == "Allowance for credit and other losses"
    # and the invisible glyph no longer drags the row out to the margin
    assert row["ink_x0"] > sibling["ink_x0"] + 10
