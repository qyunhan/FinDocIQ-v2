"""test_findociq_app.py — unit tests for findociq_app.py's pure helpers
(stage_states, doc_to_csv). No Streamlit runtime involved: these helpers take
plain rows / a raw sqlite3.Connection, so they're importable and callable
without `streamlit run`.
"""
from __future__ import annotations

import csv
import io
import sqlite3

import pytest

from findociq_app import (PERIOD_BASES, STAGES, available_bases,
                          anchor_highlights_frame, attach_sections,
                          dedupe_by_latest_document, load_dashboard_anchors,
                          compare_frame, default_basis, display_name,
                          doc_to_csv, filter_by_basis,
                          format_highlight_value, hierarchy_source_label,
                          highlights_compare_grid_frame,
                          highlights_grid_frame,
                          is_base_slice, period_column_headers,
                          pages_from_range, period_axis_order, period_label,
                          fiscal_period_axis, target_period_labels,
                          FILTER_BY_PERIOD_END_DATE,
                          raw_table_frame, resolve_title,
                          source_key_of, stage_states, table_view_labels,
                          anchor_declarations, anchor_coverage_frame,
                          unanchored_leaves_frame, select_clause,
                          resolve_source_pdf)



# ---------------------------------------------------------- raw_table_frame
_RT_ROWS = [
    {"row_id": 2, "row_hierarchy": 1, "row_leaf_label": "Total income"},
    {"row_id": 1, "row_hierarchy": 0, "row_leaf_label": "Header"},
    {"row_id": 3, "row_hierarchy": 2, "row_leaf_label": "Net interest income"},
]
_RT_COLS = [
    {"col_id": 2, "col_leaf_label": "% chg"},
    {"col_id": 1, "col_leaf_label": "1st Qtr 2026"},
    {"col_id": 3, "col_leaf_label": "% chg"},
]
_RT_CELLS = [
    {"row_id": 2, "col_id": 1, "value_raw": "5,948", "value_num": 5948.0},
    {"row_id": 2, "col_id": 2, "value_raw": None, "value_num": 1.0},
    {"row_id": 2, "col_id": 3, "value_raw": "NM", "value_num": None},
]


def test_raw_table_frame_preserves_pdf_order_and_shape():
    df = raw_table_frame(_RT_ROWS, _RT_COLS, _RT_CELLS)
    # rows back in row_id order, columns in col_id order
    assert list(df.iloc[:, 0].str.strip()) == [
        "Header", "Total income", "Net interest income"]
    assert [c.strip("\u200b") for c in df.columns] == [
        "", "1st Qtr 2026", "% chg", "% chg"]
    # duplicate headers stay SEPARATE columns (display-identical suffix)
    assert len(set(df.columns)) == 4


def test_raw_table_frame_values_and_indentation():
    df = raw_table_frame(_RT_ROWS, _RT_COLS, _RT_CELLS)
    total = df.iloc[1]
    assert list(total[1:]) == ["5,948", "1", "NM"]   # raw text kept; 1.0 -> '1'
    assert df.iloc[0, 0] == "Header"                  # depth 0: no indent
    assert df.iloc[2, 0].startswith("\u00a0" * 8)  # depth 2: indented
    # header row has no cells -> blanks, not NaN
    assert list(df.iloc[0][1:]) == ["", "", ""]


def test_raw_table_frame_drops_footnote_markers_via_clean_label():
    # the geometry stage decides typographically what is a footnote marker;
    # the browsing view shows that cleaned label, not the verbatim one.
    rows = [
        {"row_id": 1, "row_hierarchy": 0, "row_leaf_label": "Return on equity4, 5",
         "row_leaf_label_clean": "Return on equity"},
        {"row_id": 2, "row_hierarchy": 1, "row_leaf_label": "Diluted8",
         "row_leaf_label_clean": "Diluted"},
    ]
    df = raw_table_frame(rows, _RT_COLS, _RT_CELLS)
    assert list(df.iloc[:, 0].str.strip()) == ["Return on equity", "Diluted"]


def test_raw_table_frame_falls_back_to_verbatim_without_clean_label():
    # tables still on the 'model' branch (no geometry match) keep their
    # printed markers — NULL, missing, and blank all fall back.
    rows = [
        {"row_id": 1, "row_hierarchy": 0, "row_leaf_label": "Return on equity4, 5",
         "row_leaf_label_clean": None},
        {"row_id": 2, "row_hierarchy": 0, "row_leaf_label": "Earnings2"},
        {"row_id": 3, "row_hierarchy": 0, "row_leaf_label": "Net book value5",
         "row_leaf_label_clean": "   "},
        {"row_id": 4, "row_hierarchy": 0, "row_leaf_label": "Diluted8",
         "row_leaf_label_clean": float("nan")},
    ]
    df = raw_table_frame(rows, _RT_COLS, _RT_CELLS)
    assert list(df.iloc[:, 0].str.strip()) == [
        "Return on equity4, 5", "Earnings2", "Net book value5", "Diluted8"]


def test_raw_table_frame_clean_label_keeps_indent_depth():
    # cleaning the label must not disturb the hierarchy indentation
    rows = [{"row_id": 1, "row_hierarchy": 2, "row_leaf_label": "Diluted8",
             "row_leaf_label_clean": "Diluted"}]
    df = raw_table_frame(rows, _RT_COLS, _RT_CELLS)
    assert df.iloc[0, 0].startswith(" " * 8)
    assert df.iloc[0, 0].strip() == "Diluted"


def test_raw_table_frame_empty():
    assert raw_table_frame([], [], []).empty


def test_raw_table_frame_nan_cells_render_blank():
    # the app queries cells via pandas, where SQL NULL becomes float('nan')
    # (not None) — empty cells must render '', never the string 'nan'
    nan = float("nan")
    cells = [{"row_id": 2, "col_id": 1, "value_raw": nan, "value_num": nan},
             {"row_id": 2, "col_id": 2, "value_raw": nan, "value_num": 7.0}]
    df = raw_table_frame(_RT_ROWS, _RT_COLS, cells)
    assert list(df.iloc[1][1:]) == ["", "7"]


def test_raw_table_frame_drops_phantom_columns():
    # loader artifact: col_dim rows (col_id 100+) that no cell references
    cols = _RT_COLS + [{"col_id": 100, "col_leaf_label": "1st Qtr 2026"},
                       {"col_id": 101, "col_leaf_label": "% chg"}]
    df = raw_table_frame(_RT_ROWS, cols, _RT_CELLS)
    assert df.shape[1] == 1 + 3                      # label + 3 real columns
    df_all = raw_table_frame(_RT_ROWS, cols, _RT_CELLS, drop_empty_cols=False)
    assert df_all.shape[1] == 1 + 5                  # DB-defined shape kept


# ---------------------------------------------------------- pages_from_range
def test_pages_from_range_single_and_span():
    assert pages_from_range("6") == [6]
    assert pages_from_range("3-5") == [3, 4, 5]
    assert pages_from_range("3,5-7") == [3, 5, 6, 7]


def test_pages_from_range_tolerates_garbage_and_clamps():
    assert pages_from_range(None) == []
    assert pages_from_range("") == []
    assert pages_from_range("n/a") == []
    assert pages_from_range("5-3") == [3, 4, 5]          # inverted span
    assert pages_from_range("2-9", n_pages=4) == [2, 3, 4]
    assert pages_from_range("0,1", n_pages=4) == [1]      # no page 0


# --------------------------------------------------------- table_view_labels
def test_table_view_labels_full_first_then_titles():
    recs = [
        {"table_id": "t_income", "table_title": "Selected income statement items ($m)", "page_range": "6"},
        {"table_id": "t_ratios", "table_title": "Key financial ratios (%)2, 3", "page_range": "6"},
        {"table_id": "t_eps", "table_title": None, "page_range": None},
    ]
    options, by_label = table_view_labels(recs)
    assert options[0] == "Full view — all 3 tables (PDF order)"
    assert by_label[options[0]] is None
    assert by_label["Selected income statement items ($m) (p.6)"] == "t_income"
    assert by_label["t eps"] == "t_eps"           # falls back to table_id


def test_table_view_labels_dedupes_repeated_titles():
    recs = [{"table_id": f"t{i}", "table_title": "Deposits", "page_range": "9"}
            for i in range(3)]
    options, by_label = table_view_labels(recs)
    assert options[1:] == ["Deposits (p.9)", "Deposits (p.9) [2]",
                           "Deposits (p.9) [3]"]
    assert [by_label[o] for o in options[1:]] == ["t0", "t1", "t2"]


# ------------------------------------------------------------- display_name
def test_display_name_underscores_to_spaces():
    assert display_name("selected_income_statement_items_m") == \
        "selected income statement items m"


def test_display_name_all_caps_to_sentence_case():
    assert display_name("NET FEE AND COMMISSION INCOME") == \
        "Net fee and commission income"
    assert display_name("PERFORMANCE BY GEOGRAPHY") == "Performance by geography"


def test_display_name_mixed_case_preserved():
    assert display_name("Selected income statement items ($m)") == \
        "Selected income statement items ($m)"


def test_display_name_none_and_empty():
    assert display_name(None) == ""
    assert display_name("") == ""


# ------------------------------------------------------- hierarchy_source_label
def test_hierarchy_source_label_geometry():
    assert hierarchy_source_label("geometry") == "PDF geometry"


def test_hierarchy_source_label_model():
    assert hierarchy_source_label("model") == "model levels"


def test_hierarchy_source_label_none_falls_back_to_model_levels():
    assert hierarchy_source_label(None) == "model levels"


# --------------------------------------------------------------- resolve_title
def test_resolve_title_prefers_clean_when_present():
    assert resolve_title("NET FEE INCOME2", "Net fee income") == "Net fee income"


def test_resolve_title_falls_back_to_verbatim_when_clean_is_none():
    assert resolve_title("NET FEE INCOME2", None) == "NET FEE INCOME2"


def test_resolve_title_falls_back_to_verbatim_when_clean_is_empty_string():
    assert resolve_title("NET FEE INCOME2", "") == "NET FEE INCOME2"
    assert resolve_title("NET FEE INCOME2", "   ") == "NET FEE INCOME2"


# ------------------------------------------------------------ source_key_of
def test_source_key_of_strips_data_sources_prefix():
    assert source_key_of(
        "findociq/data/sources/financial_statements/DBS_1Q26_trading_update.pdf"
    ) == "financial_statements/DBS_1Q26_trading_update.pdf"
    # nested (bank/year/quarter) layout keeps its full relative key
    assert source_key_of(
        "findociq/data/sources/financial_statements/DBS/2025/2Q25/X.pdf"
    ) == "financial_statements/DBS/2025/2Q25/X.pdf"


def test_source_key_of_rejects_non_source_paths():
    assert source_key_of(None) is None
    assert source_key_of("") is None
    assert source_key_of("somewhere/else/X.pdf") is None


# ------------------------------------------------------------- stage_states
def test_stage_states_all_done():
    rows = [{"stage": "done", "state": "ok", "error_message": None}]
    result = stage_states(rows, STAGES)
    assert [s for s, _, _ in result] == STAGES
    assert all(state == "done" for _, state, _ in result)
    assert all(msg == "" for _, _, msg in result)


def test_stage_states_mid_run():
    rows = [{"stage": "extract", "state": "running", "error_message": None}]
    result = stage_states(rows, STAGES)
    by_stage = {s: (state, msg) for s, state, msg in result}
    assert by_stage["scan"] == ("done", "")
    assert by_stage["toc"] == ("done", "")
    assert by_stage["extract"] == ("running", "")
    assert by_stage["load"] == ("pending", "")
    assert by_stage["concepts"] == ("pending", "")
    assert by_stage["done"] == ("pending", "")


def test_stage_states_failed_carries_message():
    rows = [{"stage": "load", "state": "failed", "error_message": "boom: bad cell"}]
    result = stage_states(rows, STAGES)
    by_stage = {s: (state, msg) for s, state, msg in result}
    assert by_stage["scan"] == ("done", "")
    assert by_stage["toc"] == ("done", "")
    assert by_stage["extract"] == ("done", "")
    assert by_stage["load"] == ("failed", "boom: bad cell")
    assert by_stage["concepts"] == ("pending", "")


def test_stage_states_no_rows_all_pending():
    result = stage_states([], STAGES)
    assert [s for s, _, _ in result] == STAGES
    assert all(state == "pending" for _, state, _ in result)


# ---------------------------------------------------------------- doc_to_csv
@pytest.fixture
def tiny_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE table_t (
            doc_id TEXT, table_id TEXT, table_title TEXT, table_type TEXT,
            section_id TEXT, table_title_clean TEXT, hierarchy_source TEXT,
            PRIMARY KEY (doc_id, table_id)
        );
        CREATE TABLE section (doc_id TEXT, section_id TEXT, seq INTEGER);
        CREATE TABLE row_dim (
            doc_id TEXT, table_id TEXT, row_id INTEGER, row_leaf_label TEXT,
            line_no TEXT, concept_key TEXT, geo_key TEXT, segment_key TEXT,
            row_leaf_label_clean TEXT,
            PRIMARY KEY (doc_id, table_id, row_id)
        );
        CREATE TABLE col_dim (
            doc_id TEXT, table_id TEXT, col_id INTEGER, col_leaf_label TEXT,
            col_leaf_label_clean TEXT,
            PRIMARY KEY (doc_id, table_id, col_id)
        );
        CREATE TABLE cell_fact (
            doc_id TEXT, table_id TEXT, row_id INTEGER, col_id INTEGER,
            value_raw TEXT, value_num REAL, unit TEXT, period TEXT,
            period_span TEXT, concept_key TEXT, geo_key TEXT, segment_key TEXT,
            PRIMARY KEY (doc_id, table_id, row_id, col_id)
        );

        INSERT INTO table_t VALUES ('DOC1', 'T1', 'Income Statement', 'income_statement', NULL, NULL, 'model');

        INSERT INTO row_dim VALUES
            ('DOC1', 'T1', 1, 'Net profit', '1', 'NET_PROFIT', 'GLOBAL', 'SEG_TOTAL', NULL),
            ('DOC1', 'T1', 2, 'Total income', '2', 'TOTAL_INCOME', 'GLOBAL', 'SEG_TOTAL', NULL);

        INSERT INTO col_dim VALUES
            ('DOC1', 'T1', 1, '1Q25', NULL);

        INSERT INTO cell_fact VALUES
            ('DOC1', 'T1', 1, 1, '100', 100.0, 'S$m', '2025-03-31', '1Q',
             'NET_PROFIT', 'GLOBAL', 'SEG_TOTAL'),
            ('DOC1', 'T1', 2, 1, '200', 200.0, 'S$m', '2025-03-31', '1Q',
             'TOTAL_INCOME', 'GLOBAL', 'SEG_TOTAL');
        """
    )
    conn.commit()
    yield conn
    conn.close()


def test_doc_to_csv_header_and_rows(tiny_db):
    csv_text = doc_to_csv(tiny_db, "DOC1")
    reader = list(csv.reader(io.StringIO(csv_text)))
    header, *rows = reader
    assert header == [
        "table_type", "row_leaf_label", "col_label", "period", "period_label",
        "value_num", "unit", "concept_key", "geo_key", "segment_key",
    ]
    assert len(rows) == 2
    labels = {r[1] for r in rows}
    assert labels == {"Net profit", "Total income"}
    for r in rows:
        assert r[0] == "income_statement"
        assert r[3] == "2025-03-31"
        assert r[4] == "1Q25"
        assert r[6] == "S$m"


def test_doc_to_csv_no_rows_for_unknown_doc(tiny_db):
    csv_text = doc_to_csv(tiny_db, "NOPE")
    reader = list(csv.reader(io.StringIO(csv_text)))
    assert reader == [[
        "table_type", "row_leaf_label", "col_label", "period", "period_label",
        "value_num", "unit", "concept_key", "geo_key", "segment_key",
    ]]


# --------------------------------------------------------------- period_label
def test_period_label_flow_spans():
    assert period_label("2026-03-31", "1Q") == "1Q26"
    assert period_label("2026-06-30", "2Q") == "2Q26"
    assert period_label("2026-09-30", "3Q") == "3Q26"
    assert period_label("2026-12-31", "4Q") == "4Q26"
    assert period_label("2025-06-30", "1H") == "1H25"
    assert period_label("2025-12-31", "2H") == "2H25"
    assert period_label("2025-09-30", "9M") == "9M25"
    assert period_label("2025-12-31", "FY") == "FY25"


def test_period_label_as_at_and_null_span_use_date_form():
    assert period_label("2025-12-31", "as_at") == "31-Dec-25"
    assert period_label("2025-12-31", None) == "31-Dec-25"
    assert period_label("2025-12-31", "") == "31-Dec-25"
    assert period_label("2025-12-31", "  ") == "31-Dec-25"


def test_period_label_unrecognised_span_falls_back_to_date_form():
    assert period_label("2025-12-31", "bogus") == "31-Dec-25"


def test_period_label_as_at_fy_collision_render_differently():
    # An as-at STOCK and an FY FLOW can share the same 31-Dec period-end
    # date -- period_span exists precisely to disambiguate that collision,
    # so the two must render as different tokens.
    assert period_label("2025-12-31", "as_at") == "31-Dec-25"
    assert period_label("2025-12-31", "FY") == "FY25"


def test_period_label_missing_or_unparseable_period_is_blank():
    assert period_label(None, "FY") == ""
    assert period_label(float("nan"), "FY") == ""
    assert period_label("not-a-date", "FY") == ""


# -------------------------------------------------------------- compare_frame
_FM_ROWS = [
    {"institution": "DBS Group Holdings Ltd", "period": "2025-12-31",
     "period_span": "FY", "value_num": 100.0,
     "segment_key": "SEG_TOTAL", "geo_key": "GLOBAL"},
    {"institution": "DBS Group Holdings Ltd", "period": "2025-12-31",
     "period_span": "FY", "value_num": 40.0,
     "segment_key": "SEG_RETAIL", "geo_key": "GLOBAL"},
    {"institution": "Oversea-Chinese Banking Corporation Ltd", "period": "2025-12-31",
     "period_span": "FY", "value_num": 90.0,
     "segment_key": None, "geo_key": None},
    {"institution": "United Overseas Bank Ltd", "period": "2025-06-30",
     "period_span": "1H", "value_num": 30.0,
     "segment_key": "SEG_TOTAL", "geo_key": "HK"},
]


def test_compare_frame_base_only_drops_segment_and_geo_cuts():
    out = compare_frame(_FM_ROWS, base_only=True)
    assert list(out.columns) == ["institution", "period", "period_span", "value_num"]
    # Row 2 (SEG_RETAIL) and row 4 (geo=HK) are cuts -- dropped.
    assert len(out) == 2
    institutions = set(out["institution"])
    assert institutions == {"DBS Group Holdings Ltd",
                             "Oversea-Chinese Banking Corporation Ltd"}
    assert set(out["value_num"]) == {100.0, 90.0}


def test_compare_frame_base_only_false_keeps_all_rows():
    out = compare_frame(_FM_ROWS, base_only=False)
    assert len(out) == len(_FM_ROWS)
    assert set(out["value_num"]) == {100.0, 40.0, 90.0, 30.0}


def test_compare_frame_empty_rows():
    out = compare_frame([], base_only=True)
    assert list(out.columns) == ["institution", "period", "period_span", "value_num"]
    assert out.empty


# --------------------------------------------------------------- is_base_slice
def test_is_base_slice_accepts_sentinels_and_blanks():
    assert is_base_slice("SEG_TOTAL", "GLOBAL")
    assert is_base_slice(None, None)
    assert is_base_slice("", "")
    assert is_base_slice(float("nan"), float("nan"))


def test_is_base_slice_rejects_real_cuts():
    assert not is_base_slice("SEG_RETAIL", "GLOBAL")
    assert not is_base_slice("SEG_TOTAL", "HK")


# ---------------------------------------------------- load_highlights_config
def test_highlights_grid_frame_inserts_section_headers_in_config_order():
    items = [
        {"label": "Net interest income", "concept": "pnl.nii.net",
         "unit_hint": "S$m", "section": "Income statement"},
        {"label": "Total income", "concept": "pnl.income.total",
         "unit_hint": "S$m", "section": "Income statement"},
        {"label": "Total assets", "concept": "bs.assets.total",
         "unit_hint": "S$m", "section": "Balance sheet"},
    ]
    bank_rows = [
        {"label": "Net interest income", "period_label": "FY25",
         "value_num": 100.0, "unit_hint": "S$m", "is_derived": False},
        {"label": "Total income", "period_label": "FY25",
         "value_num": 5000.0, "unit_hint": "S$m", "is_derived": True},
    ]
    grid = highlights_grid_frame(bank_rows, items, ["FY25"])
    assert list(grid.index) == [
        "Income statement", "Net interest income", "Total income",
        "Balance sheet", "Total assets"]
    assert list(grid["_section_header"]) == [True, False, False, True, False]
    assert grid.loc["Net interest income", "FY25"] == "100"
    assert grid.loc["Total income", "FY25"] == "5,000 ᵈ"
    # no data for this bank -- row still present, cell blank (coverage gap
    # stays visible instead of being hidden)
    assert grid.loc["Total assets", "FY25"] == ""
    # section header rows carry no values
    assert grid.loc["Income statement", "FY25"] == ""


# --------------------------------------------- highlights_compare_grid_frame
_CMP_ITEMS = [
    {"label": "Net interest income", "concept": "pnl.nii.net",
     "unit_hint": "S$m", "section": "Income statement"},
    {"label": "Total income", "concept": "pnl.income.total",
     "unit_hint": "S$m", "section": "Income statement"},
    {"label": "Total assets", "concept": "bs.assets.total",
     "unit_hint": "S$m", "section": "Balance sheet"},
    {"label": "Basic EPS", "concept": None,
     "unit_hint": "per_share", "section": "Balance sheet"},
]


def _cmp_long_df():
    import pandas as pd
    return pd.DataFrame([
        {"bank": "DBS", "label": "Net interest income", "period_label": "FY25",
         "value_num": 14500.0, "unit_hint": "S$m", "is_derived": False},
        {"bank": "OCBC", "label": "Net interest income", "period_label": "FY25",
         "value_num": 6000.0, "unit_hint": "S$m", "is_derived": False},
        {"bank": "DBS", "label": "Total income", "period_label": "FY25",
         "value_num": 22900.0, "unit_hint": "S$m", "is_derived": True},
        {"bank": "DBS", "label": "Net interest income", "period_label": "2H25",
         "value_num": 7171.0, "unit_hint": "S$m", "is_derived": False},
    ])


def test_highlights_compare_grid_frame_rows_are_items_columns_are_banks():
    grid = highlights_compare_grid_frame(
        _cmp_long_df(), _CMP_ITEMS, ["DBS", "OCBC", "UOB"], "FY25")
    assert list(grid.columns) == ["_section_header", "_chartable", "DBS", "OCBC", "UOB"]
    assert list(grid.index) == [
        "Income statement", "Net interest income", "Total income",
        "Balance sheet", "Total assets", "Basic EPS"]
    assert grid.loc["Net interest income", "DBS"] == "14,500"
    assert grid.loc["Net interest income", "OCBC"] == "6,000"
    assert grid.loc["Total income", "DBS"] == "22,900 ᵈ"


def test_highlights_compare_grid_frame_filters_to_one_period():
    # DBS Net interest income exists at BOTH FY25 (14,500) and 2H25 (7,171) --
    # only the selected period's value should appear.
    grid = highlights_compare_grid_frame(
        _cmp_long_df(), _CMP_ITEMS, ["DBS"], "2H25")
    assert grid.loc["Net interest income", "DBS"] == "7,171"


def test_highlights_compare_grid_frame_missing_bank_cell_is_blank_not_hidden():
    # OCBC has no Total income row at all -- the row still exists, blank.
    grid = highlights_compare_grid_frame(
        _cmp_long_df(), _CMP_ITEMS, ["DBS", "OCBC"], "FY25")
    assert grid.loc["Total income", "OCBC"] == ""


def test_highlights_compare_grid_frame_chartable_flags():
    grid = highlights_compare_grid_frame(
        _cmp_long_df(), _CMP_ITEMS, ["DBS"], "FY25")
    # section headers are never chartable
    assert grid.loc["Income statement", "_chartable"] == False
    assert grid.loc["Balance sheet", "_chartable"] == False
    # a real item with a concept is chartable
    assert grid.loc["Net interest income", "_chartable"] == True
    # a coverage-gap item (concept: null) is NOT chartable, even though it
    # still gets a row -- clicking it must be a no-op, not an error
    assert grid.loc["Basic EPS", "_chartable"] == False


# ---------------------------------------------------------- format_highlight_value
def test_format_highlight_value_metric_thousands_separator():
    assert format_highlight_value(5000.0, "S$m") == "5,000"
    assert format_highlight_value(1234567.0, "S$m") == "1,234,567"


def test_format_highlight_value_ratio_not_padded_to_two_decimals():
    """CHANGED 2026-08-10 (§4.5 dashboard pass): precision is a property of
    the VALUE, not of `unit_hint`, so a ratio is no longer padded out to 2dp.
    `%` used to force '12.30'; the source printed 12.3 and that is now what
    renders. The cap is unchanged -- see test_format_highlight_value.py."""
    assert format_highlight_value(12.3, "%") == "12.3"
    assert format_highlight_value(0.5, "%") == "0.5"
    assert format_highlight_value(12.34, "%") == "12.34"


def test_format_highlight_value_per_share_keeps_original_decimals():
    """Regression (2026-08-04): 'per_share' fell through to the 0dp metric
    branch, so the dashboard showed DBS EPS 3.71 as `4` and NAV 24.29 as `24`.
    Per-share renders at the value's OWN precision -- not a fixed 2dp, which
    would invent a decimal the filing never printed. Real observed values."""
    assert format_highlight_value(3.71, "per_share") == "3.71"     # DBS EPS basic 2H25
    assert format_highlight_value(3.69, "per_share") == "3.69"     # DBS EPS diluted 2H25
    assert format_highlight_value(24.29, "per_share") == "24.29"   # DBS NAV/share
    assert format_highlight_value(1.62, "per_share") == "1.62"     # OCBC EPS
    assert format_highlight_value(2.18, "per_share") == "2.18"     # UOB EPS


def test_format_highlight_value_per_share_does_not_pad_or_invent_decimals():
    """A value carrying fewer decimals keeps them. No padding up to 2dp.

    CHANGED 2026-08-10 (§4.5 dashboard pass): a value carrying MORE than 2 is
    now capped rather than printed in full. Nothing in the corpus files a
    headline figure past 2dp, and the cap is what lets one rule serve every
    unit -- which is what stopped OCBC's S$m-stamped EPS rendering as `1`."""
    assert format_highlight_value(3.7, "per_share") == "3.7"       # not "3.70"
    assert format_highlight_value(4.0, "per_share") == "4"         # not "4.00"
    assert format_highlight_value(1.234, "per_share") == "1.23"    # capped
    # float representation error must not leak into the display
    assert format_highlight_value(0.1 + 0.2, "per_share") == "0.3"


def test_format_highlight_value_derived_marker():
    assert format_highlight_value(12.345, "%", is_derived=True) == "12.35 ᵈ"
    assert format_highlight_value(5000.0, "S$m", is_derived=True) == "5,000 ᵈ"
    assert format_highlight_value(3.71, "per_share", is_derived=True) == "3.71 ᵈ"


def test_format_highlight_value_none_and_nan_blank():
    assert format_highlight_value(None, "S$m") == ""
    assert format_highlight_value(float("nan"), "%") == ""


# ------------------------------------------------------------ period basis
_MIXED_ROWS = [
    {"period": "2025-03-31", "period_span": "1Q"},
    {"period": "2025-06-30", "period_span": "2Q"},
    {"period": "2025-06-30", "period_span": "1H"},
    {"period": "2025-09-30", "period_span": "3Q"},
    {"period": "2025-09-30", "period_span": "9M"},
    {"period": "2025-12-31", "period_span": "4Q"},
    {"period": "2025-12-31", "period_span": "2H"},
    {"period": "2025-12-31", "period_span": "FY"},
    {"period": "2025-12-31", "period_span": "as_at"},
]


def test_available_bases_mixed_returns_keys_in_period_bases_order():
    out = available_bases(_MIXED_ROWS)
    assert out == ["fy", "half", "quarter_cum", "quarter", "as_at", "all"]


def test_available_bases_null_or_blank_span_only_returns_all():
    rows = [
        {"period": "2025-12-31", "period_span": None},
        {"period": "2025-12-31", "period_span": ""},
        {"period": "2025-12-31", "period_span": "  "},
    ]
    assert available_bases(rows) == ["all"]


def test_available_bases_empty_rows():
    assert available_bases([]) == []


def test_filter_by_basis_quarter_cum_includes_9m_excludes_4q():
    out = filter_by_basis(_MIXED_ROWS, "quarter_cum")
    spans = {r["period_span"] for r in out}
    assert "9M" in spans
    assert "4Q" not in spans
    assert spans == {"1Q", "2Q", "3Q", "9M"}


def test_filter_by_basis_quarter_includes_4q_excludes_9m():
    out = filter_by_basis(_MIXED_ROWS, "quarter")
    spans = {r["period_span"] for r in out}
    assert "4Q" in spans
    assert "9M" not in spans
    assert spans == {"1Q", "2Q", "3Q", "4Q"}


def test_filter_by_basis_all_is_identity():
    out = filter_by_basis(_MIXED_ROWS, "all")
    assert out == _MIXED_ROWS
    out_unknown = filter_by_basis(_MIXED_ROWS, "not_a_basis")
    assert out_unknown == _MIXED_ROWS


def test_period_axis_order_chronological_3q_precedes_9m():
    rows = [
        {"period": "2025-09-30", "period_span": "9M"},
        {"period": "2025-09-30", "period_span": "3Q"},
        {"period": "2025-03-31", "period_span": "1Q"},
    ]
    order = period_axis_order(rows)
    assert order == ["1Q25", "3Q25", "9M25"]


def test_period_axis_order_2h_precedes_fy_same_end_date():
    rows = [
        {"period": "2025-12-31", "period_span": "FY"},
        {"period": "2025-12-31", "period_span": "2H"},
    ]
    assert period_axis_order(rows) == ["2H25", "FY25"]


def test_period_axis_order_dedupes_repeats():
    rows = [
        {"period": "2025-03-31", "period_span": "1Q"},
        {"period": "2025-03-31", "period_span": "1Q"},
        {"period": "2025-06-30", "period_span": "2Q"},
    ]
    assert period_axis_order(rows) == ["1Q25", "2Q25"]


def test_period_axis_order_as_at_mixed_with_flows_not_alphabetical():
    # '31-Dec-25' (as_at) sorts alphabetically BEFORE '1Q26' and 'FY25', but
    # chronologically the as_at snapshot is the LATEST period here -- so an
    # alphabetical sort would scramble it to the front.
    rows = [
        {"period": "2025-03-31", "period_span": "1Q"},
        {"period": "2025-12-31", "period_span": "FY"},
        {"period": "2026-03-31", "period_span": "as_at"},
    ]
    order = period_axis_order(rows)
    assert order == ["1Q25", "FY25", "31-Mar-26"]
    assert order != sorted(order)


# ------------------------------------------------------------- default_basis
def test_default_basis_picks_most_distinct_periods():
    # "quarter" (1Q,2Q,3Q,4Q) has 4 distinct periods; "fy" has 1; "half" has
    # 2; "quarter_cum" has 3 (1Q,2Q,3Q -- no 9M in this fixture); "as_at" 0.
    rows = [
        {"period": "2025-03-31", "period_span": "1Q"},
        {"period": "2025-06-30", "period_span": "2Q"},
        {"period": "2025-06-30", "period_span": "1H"},
        {"period": "2025-09-30", "period_span": "3Q"},
        {"period": "2025-12-31", "period_span": "4Q"},
        {"period": "2025-12-31", "period_span": "FY"},
    ]
    assert default_basis(rows) == "quarter"


def test_default_basis_tie_breaks_to_period_bases_order():
    # "fy" (1 period) and "as_at" (1 period) tie; "fy" precedes "as_at" in
    # PERIOD_BASES, so it wins. Nothing else present.
    rows = [
        {"period": "2025-12-31", "period_span": "FY"},
        {"period": "2025-12-31", "period_span": "as_at"},
    ]
    assert list(PERIOD_BASES.keys()).index("fy") < list(
        PERIOD_BASES.keys()).index("as_at")
    assert default_basis(rows) == "fy"


def test_default_basis_all_as_at_only_balance_sheet_concept():
    # A balance-sheet concept publishes only as_at snapshots -- default
    # should land on "as_at", not an empty "fy"/"half"/"quarter".
    rows = [
        {"period": "2025-03-31", "period_span": "as_at"},
        {"period": "2025-06-30", "period_span": "as_at"},
        {"period": "2025-09-30", "period_span": "as_at"},
    ]
    assert default_basis(rows) == "as_at"


def test_as_at_only_concept_never_defaults_to_an_empty_flow_basis():
    # Regression, pinned against measured live-DB corpus facts (not a
    # hypothetical): `bs.credit.allowances_stage3_sp` breaks down as
    # 0 FY / 0 half / 0 q+cum / 0 qtr / 8 as_at / 0 null-span, and
    # `bs.liabilities.deposits_casa` as 0/0/0/0/19 as_at/0 -- every flow
    # basis (fy/half/quarter_cum/quarter) is EMPTY for these concepts.
    # A naive default (PERIOD_BASES order, or always "fy") would pick a
    # flow basis and render a blank chart. `available_bases` must offer
    # only ["as_at", "all"] and `default_basis` must land on "as_at".
    rows = [{"period": "2025-03-31", "period_span": "as_at"}] * 8  # allowances_stage3_sp-shaped
    assert available_bases(rows) == ["as_at", "all"]
    assert default_basis(rows) == "as_at"

    rows_deposits = [{"period": "2025-03-31", "period_span": "as_at"}] * 19  # deposits_casa-shaped
    assert available_bases(rows_deposits) == ["as_at", "all"]
    assert default_basis(rows_deposits) == "as_at"


# ===========================================================================
# ANCHOR PATH — the Key Financial Highlights read path after fact_metric was
# removed from it. Values now come from the stamped DB keyed on
# (bank, table_type_id, canonical_leaf_id); the row list comes from
# data/derived/dashboards/*.csv.
# ===========================================================================
def _anchor_row(tt, leaf, period, span, val, unit="S$m",
                inst="DBS Group Holdings Ltd", title="Selected income statement items ($m)",
                doc_period="2025-12-31"):
    return dict(table_type_id=tt, canonical_leaf_id=leaf, table_title=title,
                institution=inst, period=period, period_span=span,
                value_num=val, unit=unit, doc_period=doc_period)


def test_load_dashboard_anchors_orders_by_row_order(tmp_path):
    (tmp_path / "DBS_highlights_dashboard_anchors.csv").write_text(
        "concept,row_order,bank,table_type_id,canonical_leaf_id,sign\n"
        "Total income,4,DBS,FS_INCOME_SELECTED,total_income,1\n"
        "Net fee,2,DBS,FS_INCOME_SELECTED,fee,1\n")
    items, members = load_dashboard_anchors("DBS", dashboards_dir=tmp_path)
    assert [i["label"] for i in items] == ["Net fee", "Total income"]
    # 6-tuple: the address is (table_type_id, canonical_leaf_id, sign,
    # canonical_col_id, row_dim_key, filter_by). A file with neither hard-axis
    # column yields None for both -- no column slice, no row-dim slice -- and
    # a file predating `filter_by` (this one) gets the table_type_id default,
    # 'period_label' for an income table. See test_filter_by_dispatch.py.
    assert members["Total income"] == [
        ("FS_INCOME_SELECTED", "total_income", 1, None, None, "period_label")]


def test_load_dashboard_anchors_ignores_other_banks(tmp_path):
    (tmp_path / "DBS_highlights_dashboard_anchors.csv").write_text(
        "concept,row_order,bank,table_type_id,canonical_leaf_id,sign\n"
        "Total income,1,DBS,FS_INCOME_SELECTED,total_income,1\n"
        "Total income,1,UOB,FS_INCOME_SELECTED,total_income,1\n")
    _items, members = load_dashboard_anchors("DBS", dashboards_dir=tmp_path)
    assert len(members["Total income"]) == 1


def test_anchor_frame_sums_declared_rollup_and_marks_it_derived():
    """DBS group NII = commercial book + markets. The formula file DECLARES it,
    so no resolver tie-break picks between the three rows that all print
    'Net interest income'."""
    rows = [_anchor_row("FS_INCOME_SELECTED", "commercial::nii", "2025-12-31", "FY", 14494.0),
            _anchor_row("FS_INCOME_SELECTED", "markets::nii", "2025-12-31", "FY", 6.0)]
    items = [{"label": "Net interest income", "concept": "Net interest income",
              "unit_hint": None, "section": None}]
    members = {"Net interest income": [("FS_INCOME_SELECTED", "commercial::nii", 1),
                                       ("FS_INCOME_SELECTED", "markets::nii", 1)]}
    df = anchor_highlights_frame(rows, items, members)
    assert len(df) == 1
    assert df.iloc[0]["value_num"] == 14500.0
    assert bool(df.iloc[0]["is_derived"]) is True
    assert df.iloc[0]["resolved_by"] == "formula"


def test_anchor_frame_slices_on_canonical_col_id():
    """THE PILOT. UOB prints geography on the COLUMN axis, so one leaf carries
    one fact per geography. Without the column half of the address every
    geography collapses into one row and whichever column survived dedupe
    stands in for all seven."""
    sg = _anchor_row("FS_PERF_BY_GEOGRAPHY", "net_interest_income", "2026-06-30",
                     "1H", 2517.0, inst="United Overseas Bank Ltd",
                     title="Performance by Geographical Segment 1 — 1H26")
    total = dict(sg, value_num=4621.0)
    sg["canonical_col_id"], total["canonical_col_id"] = "SG", "GLOBAL"
    items = [{"label": "NII — Singapore", "concept": "NII — Singapore",
              "unit_hint": None, "section": None}]
    members = {"NII — Singapore": [("FS_PERF_BY_GEOGRAPHY",
                                    "net_interest_income", 1, "SG", None)]}
    df = anchor_highlights_frame([sg, total], items, members)
    assert len(df) == 1
    assert df.iloc[0]["value_num"] == 2517.0        # not 4621, not their sum


def test_anchor_frame_ignores_hard_axis_when_not_declared():
    """A blank column field means 'do not slice on that axis' — the shape of
    every anchor authored before the tuple existed."""
    r = _anchor_row("FS_INCOME_SELECTED", "total_income", "2025-12-31", "FY", 22900.0)
    r["canonical_col_id"] = None
    df = anchor_highlights_frame(
        [r], [{"label": "Total income", "concept": "Total income",
               "unit_hint": None, "section": None}],
        {"Total income": [("FS_INCOME_SELECTED", "total_income", 1, None, None)]})
    assert df.iloc[0]["value_num"] == 22900.0


def test_anchor_frame_single_member_is_not_derived():
    rows = [_anchor_row("FS_INCOME_SELECTED", "total_income", "2025-12-31", "FY", 22900.0)]
    items = [{"label": "Total income", "concept": "Total income",
              "unit_hint": None, "section": None}]
    df = anchor_highlights_frame(rows, items, {"Total income":
                                               [("FS_INCOME_SELECTED", "total_income", 1)]})
    assert bool(df.iloc[0]["is_derived"]) is False
    assert df.iloc[0]["resolved_by"] == "anchor"


def test_anchor_frame_respects_sign():
    rows = [_anchor_row("T", "a", "2025-12-31", "FY", 100.0),
            _anchor_row("T", "b", "2025-12-31", "FY", 30.0)]
    items = [{"label": "Net", "concept": "Net", "unit_hint": None, "section": None}]
    df = anchor_highlights_frame(rows, items, {"Net": [("T", "a", 1), ("T", "b", -1)]})
    assert df.iloc[0]["value_num"] == 70.0


def test_anchor_frame_takes_unit_hint_from_the_data():
    """unit_hint is not re-declared in config — it is the unit the filing
    reported on that row's non-derived cells."""
    rows = [_anchor_row("FS_PER_SHARE", "earnings::basic", "2025-12-31", "FY",
                        3.84, unit="per_share", title="Per share data ($)")]
    items = [{"label": "- Basic", "concept": "- Basic", "unit_hint": None, "section": None}]
    df = anchor_highlights_frame(rows, items,
                                 {"- Basic": [("FS_PER_SHARE", "earnings::basic", 1)]})
    assert df.iloc[0]["unit_hint"] == "per_share"
    assert df.iloc[0]["section"] == "Per share data ($)"


def test_dedupe_keeps_the_most_recent_document():
    """4Q25 and 2Q25 both print 1H25; without this the winner is whichever row
    the query happened to yield last."""
    old = _anchor_row("T", "a", "2025-06-30", "1H", 111.0, doc_period="2025-06-30")
    new = _anchor_row("T", "a", "2025-06-30", "1H", 222.0, doc_period="2025-12-31")
    assert dedupe_by_latest_document([old, new])[0]["value_num"] == 222.0
    assert dedupe_by_latest_document([new, old])[0]["value_num"] == 222.0


def test_attach_sections_strips_vintage_footnote_markers():
    """DBS prints 'Key financial ratios (%)4' and '(%)1,2' for the SAME table in
    different documents; unnormalised they rendered as two sections."""
    import pandas as _pd
    long_df = _pd.DataFrame([
        {"label": "NIM", "section": "Key financial ratios (%)4"},
        {"label": "CET1", "section": "Key financial ratios (%)1,2"}])
    items = [{"label": "NIM"}, {"label": "CET1"}]
    attach_sections(items, long_df)
    assert items[0]["section"] == items[1]["section"] == "Key financial ratios (%)"


def test_attach_sections_unmapped_item_inherits_its_block():
    """An item with no data must not split the block it belongs to — DBS
    'Total equity' sits between Total liabilities and Shareholders' equity."""
    import pandas as _pd
    long_df = _pd.DataFrame([
        {"label": "Total liabilities", "section": "Selected balance sheet items ($m)"},
        {"label": "Shareholders' equity", "section": "Selected balance sheet items ($m)"}])
    items = [{"label": "Total liabilities"}, {"label": "Total equity"},
             {"label": "Shareholders' equity"}]
    attach_sections(items, long_df)
    assert [i["section"] for i in items] == ["Selected balance sheet items ($m)"] * 3


def test_load_dashboard_anchors_reads_the_declared_section(tmp_path):
    (tmp_path / "highlights_dashboard_anchors.csv").write_text(
        "concept,row_order,section,bank,table_type_id,canonical_leaf_id,sign\n"
        "Total liabilities,15,Balance sheet ($m),DBS,FS_BALANCE_SELECTED,tl,1\n"
        "Net profit,11,Income statement ($m),DBS,FS_INCOME_SELECTED,np,1\n")
    items, _members = load_dashboard_anchors("DBS", dashboards_dir=tmp_path)
    assert [(i["label"], i["section"]) for i in items] == [
        ("Net profit", "Income statement ($m)"),
        ("Total liabilities", "Balance sheet ($m)")]


def test_load_dashboard_anchors_without_a_section_column_defers(tmp_path):
    """Anchor files written before the column existed must still work — those
    items come back section=None for `attach_sections` to fill."""
    (tmp_path / "highlights_dashboard_anchors.csv").write_text(
        "concept,row_order,bank,table_type_id,canonical_leaf_id,sign\n"
        "Net profit,11,DBS,FS_INCOME_SELECTED,np,1\n")
    items, _members = load_dashboard_anchors("DBS", dashboards_dir=tmp_path)
    assert items[0]["section"] is None


def test_attach_sections_never_overrides_a_declared_section():
    """The declared grouping is a property of the ROW LIST, so a caption derived
    from one bank's data must not restate it."""
    import pandas as _pd
    long_df = _pd.DataFrame([
        {"label": "Total equity", "section": "UNAUDITED BALANCE SHEETS"}])
    items = [{"label": "Total equity", "section": "Balance sheet ($m)"}]
    attach_sections(items, long_df)
    assert items[0]["section"] == "Balance sheet ($m)"


def test_declared_sections_survive_a_one_bank_coverage_gap():
    """THE REGRESSION. `Total equity` resolves for OCBC only, so the derived
    caption for that one label differed from its neighbours' (which came from
    DBS) and the grid grew a header row between `Total liabilities` and
    `Total equity` — in EVERY bank's table, since `items` is the cross-bank
    union."""
    import pandas as _pd
    items = [{"label": "Total liabilities", "section": "Balance sheet ($m)"},
             {"label": "Total equity", "section": "Balance sheet ($m)"},
             {"label": "Shareholders' equity", "section": "Balance sheet ($m)"}]
    long_df = _pd.DataFrame([
        {"label": "Total liabilities", "section": "Selected balance sheet items ($m)"},
        {"label": "Total equity", "section": "UNAUDITED BALANCE SHEETS"},
        {"label": "Shareholders' equity", "section": "Selected balance sheet items ($m)"}])
    attach_sections(items, long_df)
    bank_rows = [{"label": "Total liabilities", "period_label": "FY25",
                  "value_num": 700.0, "unit_hint": "S$m"}]
    grid = highlights_grid_frame(bank_rows, items, ["FY25"])
    assert list(grid["_section_header"]) == [True, False, False, False]
    assert list(grid.index) == ["Balance sheet ($m)", "Total liabilities",
                                "Total equity", "Shareholders' equity"]
    # the bank with no value keeps its row, blank -- the gap stays visible
    assert grid.loc["Total equity", "FY25"] == ""


def test_period_column_headers_carry_the_exact_close_date():
    """A balance-sheet anchor is a STOCK placed by filter_by='period_end_date',
    so the figure under '1H26' is the balance AS AT that column's close. The
    fiscal label alone never says which date that is."""
    axis = [("1H26", "2026-06-30", "1H"), ("FY25", "2025-12-31", "FY")]
    assert period_column_headers(axis) == {
        "1H26": "1H26 · 30-Jun-26",
        "FY25": "FY25 · 31-Dec-25",
    }


def test_period_column_headers_skip_unusable_entries():
    """A column with no parseable close date keeps its bare label rather than
    rendering half a header; `rename` simply leaves an unmapped column alone."""
    assert period_column_headers([("FY25", None, "FY")]) == {}
    assert period_column_headers([("", "2025-12-31", "FY")]) == {}
    assert period_column_headers([]) == {}
    assert period_column_headers(None) == {}


def test_period_column_headers_do_not_disturb_grid_keys():
    """The rename is display-only: every grid column must still be reachable by
    its bare fiscal label before renaming, and by the decorated one after."""
    items = [{"label": "Total assets", "section": "Balance sheet ($m)"}]
    bank_rows = [{"label": "Total assets", "period_label": "1H26",
                  "value_num": 965597.0, "unit_hint": "S$m"}]
    grid = highlights_grid_frame(bank_rows, items, ["1H26"])
    display = grid.drop(columns="_section_header").rename(
        columns=period_column_headers([("1H26", "2026-06-30", "1H")]))
    assert list(display.columns) == ["1H26 · 30-Jun-26"]
    assert display.loc["Total assets", "1H26 · 30-Jun-26"] == "965,597"


def _write_pair(d, stem, rows):
    import csv as _csv
    p = d / f"{stem}_anchors.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=["concept", "row_order", "section", "bank",
                                           "table_type_id", "canonical_leaf_id", "sign"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def test_available_dashboards_is_discovered_from_the_folder(tmp_path):
    """A dashboard IS a CSV pair. Dropping one in must add it with no code
    change, and the headline set must stay first however they sort."""
    from findociq_app import available_dashboards
    assert available_dashboards(tmp_path) == []
    _write_pair(tmp_path, "highlights_dashboard", [])
    _write_pair(tmp_path, "breakdown_of_gross_nb_loans", [])
    _write_pair(tmp_path, "aaa_first_alphabetically", [])
    got = available_dashboards(tmp_path)
    assert [s for s, _ in got][0] == "highlights_dashboard"
    assert set(s for s, _ in got) == {"highlights_dashboard",
                                      "breakdown_of_gross_nb_loans",
                                      "aaa_first_alphabetically"}
    assert dict(got)["highlights_dashboard"] == "Key Financial Highlights"
    # a formulaanchors file is NOT a dashboard of its own
    (tmp_path / "highlights_dashboard_formulaanchors.csv").write_text(
        "concept,row_order,section,bank,table_type_id,canonical_leaf_id,sign\n")
    assert len(available_dashboards(tmp_path)) == 3


def test_anchor_sets_are_never_merged(tmp_path):
    """row_order is per-FILE and every set starts at 1, so merging two sets
    interleaves them row by row and the second set's rows then scatter through
    the first's sections unheadered. Selecting a set must isolate it."""
    from findociq_app import load_dashboard_anchors
    _write_pair(tmp_path, "highlights_dashboard", [
        {"concept": "Total income", "row_order": 1, "section": "Income statement ($m)",
         "bank": "DBS", "table_type_id": "FS_INCOME_SELECTED",
         "canonical_leaf_id": "total_income", "sign": 1}])
    _write_pair(tmp_path, "breakdown_of_gross_nb_loans", [
        {"concept": "Gross loans", "row_order": 1, "section": "By Allowance",
         "bank": "DBS", "table_type_id": "FS_CUSTOMER_LOANS",
         "canonical_leaf_id": "gross", "sign": 1}])

    hl, _ = load_dashboard_anchors("DBS", tmp_path, dashboard="highlights_dashboard")
    assert [i["label"] for i in hl] == ["Total income"]
    loans, _ = load_dashboard_anchors("DBS", tmp_path,
                                      dashboard="breakdown_of_gross_nb_loans")
    assert [i["label"] for i in loans] == ["Gross loans"]
    # dashboard=None keeps the pre-sets behaviour (whole directory)
    both, _ = load_dashboard_anchors("DBS", tmp_path)
    assert len(both) == 2


def test_fiscal_axis_falls_back_to_stock_closes_when_there_are_no_flows():
    """A balance-sheet-only anchor set has no flow rows, so the flows-only axis
    came back EMPTY — and an empty axis places nothing, so a dashboard whose
    facts all resolved rendered as a grid with no columns. Measured on
    breakdown_of_gross_nb_loans: 304 facts, every one `as_at`."""
    stocks = [{"period": "2026-06-30", "period_span": "as_at", "label": "Gross loans"},
              {"period": "2025-12-31", "period_span": "as_at", "label": "Gross loans"}]
    axis = fiscal_period_axis(stocks)
    assert [a[0] for a in axis] == ["31-Dec-25", "30-Jun-26"]
    assert [a[1] for a in axis] == ["2025-12-31", "2026-06-30"]


def test_flow_sets_still_exclude_stocks_from_the_axis():
    """The fallback must not weaken the no-minting rule: when ANY flow exists, a
    stock closing on the same day must not raise a second column beside it."""
    rows = [{"period": "2026-06-30", "period_span": "1H", "label": "Total income"},
            {"period": "2026-06-30", "period_span": "as_at", "label": "Total assets"}]
    axis = fiscal_period_axis(rows)
    assert [a[0] for a in axis] == ["1H26"], "the as_at stock must not mint 30-Jun-26"


def test_stock_only_axis_places_every_fact():
    """End to end: with the fallback axis, a period_end_date stock lands on the
    column closing on its own date."""
    axis = fiscal_period_axis([{"period": "2026-06-30", "period_span": "as_at"}])
    assert target_period_labels("2026-06-30", "as_at",
                                FILTER_BY_PERIOD_END_DATE, axis) == ["30-Jun-26"]


# ------------------------------------------------------------- select_clause
# The Database view died on `no such column: row_leaf_label_clean` because
# compiled_v2.db's row_dim does not carry it. These pin the degradation rule.
def test_select_clause_passes_through_columns_the_schema_has():
    assert select_clause(["a", "b"], {"a", "b", "c"}) == "a, b"


def test_select_clause_serves_a_missing_column_as_null_keeping_the_name():
    # The NAME must survive so downstream frames keep their shape — a caller
    # doing df["concept_key"] finds an empty column, not a KeyError.
    assert (select_clause(["a", "gone"], {"a"})
            == "a, NULL AS gone")


def test_select_clause_prefixes_real_columns_but_never_a_null_alias():
    # 'f.NULL AS x' is a syntax error; the alias is bare by construction.
    assert (select_clause(["a", "gone"], {"a"}, "f.")
            == "f.a, NULL AS gone")


def test_select_clause_on_an_empty_schema_blanks_everything():
    assert select_clause(["a", "b"], set()) == "NULL AS a, NULL AS b"


def test_select_clause_preserves_caller_order():
    assert (select_clause(["z", "a"], {"a", "z"}) == "z, a")


# -------------------------------------------------------- resolve_source_pdf
def _corpus(tmp_path, *rel):
    for r in rel:
        p = tmp_path / "findociq" / "data" / "sources" / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4\n")
    return tmp_path


def test_resolve_source_pdf_takes_the_recorded_path_when_it_exists(tmp_path):
    _corpus(tmp_path, "financial_statements/X.pdf")
    got = resolve_source_pdf("findociq/data/sources/financial_statements/X.pdf",
                             tmp_path)
    assert got.endswith("findociq/data/sources/financial_statements/X.pdf")


def test_resolve_source_pdf_falls_back_to_the_basename_across_conventions(tmp_path):
    # The real defect: document.source_file records the FOLDERED key while the
    # repo stores the file flat. 3 of 10 documents in compiled_v2.db do this.
    _corpus(tmp_path, "financial_statements/X.pdf")
    got = resolve_source_pdf(
        "findociq/data/sources/financial_statements/DBS/2025/4Q25/X.pdf",
        tmp_path)
    assert got.endswith("financial_statements/X.pdf")


def test_resolve_source_pdf_finds_a_sibling_folder(tmp_path):
    # DBS_1Q26_P3's PDF is under sources/pillar3/, not financial_statements/.
    _corpus(tmp_path, "pillar3/P3.pdf")
    got = resolve_source_pdf(
        "findociq/data/sources/financial_statements/P3.pdf", tmp_path)
    assert got.endswith("pillar3/P3.pdf")


def test_resolve_source_pdf_is_none_when_nothing_matches(tmp_path):
    _corpus(tmp_path, "financial_statements/X.pdf")
    assert resolve_source_pdf(
        "findociq/data/sources/financial_statements/NOPE.pdf", tmp_path) is None


def test_resolve_source_pdf_cannot_escape_the_sources_tree(tmp_path):
    # Only the basename is searched, and only under data/sources/.
    _corpus(tmp_path, "financial_statements/X.pdf")
    (tmp_path / "SECRET.md").write_text("x")
    assert resolve_source_pdf(
        "findociq/data/sources/../../SECRET.md", tmp_path) is None


def test_resolve_source_pdf_handles_empty_input(tmp_path):
    assert resolve_source_pdf(None, tmp_path) is None
    assert resolve_source_pdf("", tmp_path) is None


# --------------------------------------------- anchor-keyed Table Registry
def _write_anchor_set(d, stem, rows, header=None):
    header = header or ["concept", "row_order", "section", "bank",
                        "table_type_id", "canonical_leaf_id"]
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{stem}_anchors.csv").open("w", newline="",
                                          encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_anchor_declarations_reads_every_set_and_dedupes_the_address(tmp_path):
    _write_anchor_set(tmp_path, "highlights_dashboard",
                      [["Total assets", 1, "BS", "DBS", "FS_BAL", "total_assets"]])
    _write_anchor_set(tmp_path, "other_set",
                      [["Total assets", 1, "BS", "DBS", "FS_BAL", "total_assets"],
                       ["Net loans", 2, "BS", "DBS", "FS_BAL", "net_loans"]])
    decls = anchor_declarations(tmp_path)
    addrs = [(d["bank"], d["table_type_id"], d["canonical_leaf_id"])
             for d in decls]
    assert addrs.count(("DBS", "FS_BAL", "total_assets")) == 1
    assert ("DBS", "FS_BAL", "net_loans") in addrs
    # headline set is read first, so it owns the surviving declaration
    keep = next(d for d in decls if d["canonical_leaf_id"] == "total_assets")
    assert keep["dashboard"] == "highlights_dashboard"


def test_anchor_declarations_skips_rows_with_no_leaf_address(tmp_path):
    _write_anchor_set(tmp_path, "highlights_dashboard",
                      [["Declared", 1, "BS", "DBS", "FS_BAL", "total_assets"],
                       ["No leaf", 2, "BS", "DBS", "FS_BAL", ""],
                       ["No type", 3, "BS", "DBS", "", "x"]])
    assert len(anchor_declarations(tmp_path)) == 1


def test_anchor_coverage_frame_matches_on_the_leaf_address_alone():
    decls = [{"dashboard": "d", "bank": "DBS", "table_type_id": "FS_BAL",
              "canonical_leaf_id": "total_assets", "concept": "Total assets",
              "section": "BS", "row_order": 1}]
    captured = [
        {"bank": "DBS", "table_type_id": "FS_BAL",
         "canonical_leaf_id": "total_assets", "doc_id": "d1",
         "doc_period": "2025-12-31", "row_leaf_label": "Total assets"},
        {"bank": "DBS", "table_type_id": "FS_BAL",
         "canonical_leaf_id": "total_assets", "doc_id": "d2",
         "doc_period": "2026-06-30", "row_leaf_label": "Total assets1"},
    ]
    df = anchor_coverage_frame(decls, captured)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["times_captured"] == 2 and r["docs_captured"] == 2
    # latest_period is the MAX, not the last row yielded
    assert r["latest_period"] == "2026-06-30"


def test_anchor_coverage_frame_keeps_an_uncaptured_anchor_visible():
    decls = [{"dashboard": "d", "bank": "UOB", "table_type_id": "FS_BAL",
              "canonical_leaf_id": "never_stamped", "concept": "Ghost",
              "section": "BS", "row_order": 1}]
    df = anchor_coverage_frame(decls, [])
    assert len(df) == 1
    assert df.iloc[0]["times_captured"] == 0


def test_anchor_coverage_frame_does_not_credit_one_bank_for_anothers_capture():
    decls = [{"dashboard": "d", "bank": "UOB", "table_type_id": "FS_BAL",
              "canonical_leaf_id": "total_assets", "concept": "Total assets",
              "section": "BS", "row_order": 1}]
    captured = [{"bank": "DBS", "table_type_id": "FS_BAL",
                 "canonical_leaf_id": "total_assets", "doc_id": "d1",
                 "doc_period": "2025-12-31", "row_leaf_label": "Total assets"}]
    assert anchor_coverage_frame(decls, captured).iloc[0]["times_captured"] == 0


def test_unanchored_leaves_frame_lists_captured_addresses_no_anchor_declares():
    decls = [{"dashboard": "d", "bank": "DBS", "table_type_id": "FS_BAL",
              "canonical_leaf_id": "total_assets", "concept": "Total assets",
              "section": "BS", "row_order": 1}]
    captured = [
        {"bank": "DBS", "table_type_id": "FS_BAL",
         "canonical_leaf_id": "total_assets", "doc_id": "d1",
         "doc_period": "2025-12-31", "row_leaf_label": "Total assets"},
        {"bank": "DBS", "table_type_id": "FS_BAL",
         "canonical_leaf_id": "goodwill", "doc_id": "d1",
         "doc_period": "2025-12-31", "row_leaf_label": "Goodwill"},
    ]
    df = unanchored_leaves_frame(decls, captured)
    assert list(df["canonical_leaf_id"]) == ["goodwill"]
    assert df.iloc[0]["printed_label"] == "Goodwill"


def test_unanchored_leaves_frame_orders_most_captured_first():
    captured = (
        [{"bank": "DBS", "table_type_id": "T", "canonical_leaf_id": "rare",
          "doc_id": "d1", "doc_period": "2025-12-31", "row_leaf_label": "R"}]
        + [{"bank": "DBS", "table_type_id": "T", "canonical_leaf_id": "common",
            "doc_id": f"d{i}", "doc_period": "2025-12-31",
            "row_leaf_label": "C"} for i in range(3)])
    df = unanchored_leaves_frame([], captured)
    assert list(df["canonical_leaf_id"]) == ["common", "rare"]

def test_resolve_source_pdf_honours_an_explicit_sources_dir(tmp_path):
    # The deploy mirror flattens the repo: there is no findociq/ directory, so
    # the default 'repo/findociq/data/sources' resolves to nothing there and the
    # panel would report every PDF unavailable with the files present.
    flat = tmp_path / "data" / "sources" / "financial_statements"
    flat.mkdir(parents=True)
    (flat / "X.pdf").write_bytes(b"%PDF-1.4\n")
    got = resolve_source_pdf("findociq/data/sources/financial_statements/X.pdf",
                             tmp_path, tmp_path / "data" / "sources")
    assert got.endswith("data/sources/financial_statements/X.pdf")


# ------------------------------------------------------------- is_missing
# 162 of 342 table titles rendered as the literal text "nan": pandas >= 3 reads
# a text column as the `str` dtype whose missing value is pd.NA, which is
# truthy and stringifies. requirements.txt pins only pandas>=2.0.0, so the
# deploy picked this up with no code change on our side.
import pandas as _pd


@pytest.mark.parametrize("v", [None, float("nan"), _pd.NA])
def test_is_missing_covers_every_shape_of_sql_null(v):
    from findociq_app import is_missing
    assert is_missing(v) is True


@pytest.mark.parametrize("v", ["", " ", "x", 0, False, []])
def test_is_missing_is_false_for_real_values_including_falsy_ones(v):
    from findociq_app import is_missing
    assert is_missing(v) is False


@pytest.mark.parametrize("na", [None, float("nan"), _pd.NA])
def test_resolve_title_falls_back_when_clean_is_missing_in_any_shape(na):
    assert resolve_title("BALANCE SHEETS", na) == "BALANCE SHEETS"


@pytest.mark.parametrize("na", [None, float("nan"), _pd.NA])
def test_resolve_title_returns_none_not_nan_when_both_are_missing(na):
    # Callers write `resolve_title(...) or table_id`; NaN would WIN that `or`
    # and print as 'nan'. None is the only return value that lets it fall
    # through to the table_id.
    assert resolve_title(na, na) is None


@pytest.mark.parametrize("na", [None, float("nan"), _pd.NA])
def test_display_name_never_emits_the_text_nan(na):
    assert display_name(na) == ""


def test_display_name_of_a_missing_title_or_id_falls_through_to_the_id():
    # display_name only sentence-cases SHOUTING-CAPS; a lowercase id is left
    # as-is beyond the underscore->space rewrite.
    assert display_name(resolve_title(float("nan"), _pd.NA) or "tbl_7") == "tbl 7"


# ------------------------------------------------------------- _meta_line
def test_meta_line_drops_a_missing_unit_instead_of_printing_nan():
    from findociq_app import _meta_line
    # 105 of 342 tables have no unit; the old `if x` guard kept NaN.
    assert _meta_line(float("nan"), "p.12") == "p.12"
    assert _meta_line(_pd.NA, "p.12") == "p.12"
    assert _meta_line(None, "p.12") == "p.12"


def test_meta_line_keeps_real_parts_and_drops_blanks():
    from findociq_app import _meta_line
    assert _meta_line("S$m", "p.12", "", "  ") == "S$m · p.12"


def test_table_view_labels_does_not_render_page_nan():
    rows = [{"table_id": "t1", "table_title": "Income", "page_range": float("nan")}]
    options, _ = table_view_labels(rows)
    assert "p.nan" not in " ".join(options)
    assert "Income" in options[1]
