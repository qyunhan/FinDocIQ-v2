"""filter_by dispatch — spec 2026-08-09 §4.5.

A flow joins a period by its LABEL; a stock joins by its END DATE. The point
of the rule is that both then land in the same fiscal column, which no
span-partitioned basis could do.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from findociq_app import (                                     # noqa: E402
    FILTER_BY_PERIOD_END_DATE, FILTER_BY_PERIOD_LABEL,
    anchor_highlights_frame, default_filter_by, fiscal_period_axis,
    load_dashboard_anchors, resolve_filter_by, target_period_labels)


# An axis with two windows closing on the same date (4Q25 and FY25 both end
# 2025-12-31) plus one that does not -- the case that separates the two modes.
AXIS = [("4Q25", "2025-12-31", "4Q"),
        ("FY25", "2025-12-31", "FY"),
        ("1H26", "2026-06-30", "1H")]


def _fact(leaf, period, span, value, tt="FS_INCOME_SELECTED"):
    return {"table_type_id": tt, "canonical_leaf_id": leaf,
            "institution": "DBS Group Holdings", "period": period,
            "period_span": span, "value_num": value, "unit": "S$m",
            "table_title": "T", "canonical_col_id": None, "geo_key": None,
            "segment_key": None, "industry_key": None, "doc_period": period}


# ------------------------------------------------------ 1. period_label mode
def test_period_label_anchor_matches_only_its_own_window():
    """A flow measured over FY25 is FY25 data. It must NOT also claim 4Q25,
    even though both windows close on 2025-12-31 -- they are different
    measurements over different windows."""
    assert target_period_labels(
        "2025-12-31", "FY", FILTER_BY_PERIOD_LABEL, AXIS) == ["FY25"]
    assert target_period_labels(
        "2025-12-31", "4Q", FILTER_BY_PERIOD_LABEL, AXIS) == ["4Q25"]
    # A flow whose window is not on the axis renders nowhere rather than
    # minting a column of its own.
    assert target_period_labels(
        "2025-09-30", "3Q", FILTER_BY_PERIOD_LABEL, AXIS) == []

    items = [{"label": "Net profit", "concept": "Net profit",
              "unit_hint": None, "section": "P&L"}]
    members = {"Net profit": [("FS_INCOME_SELECTED", "net_profit", 1,
                               None, None, FILTER_BY_PERIOD_LABEL)]}
    df = anchor_highlights_frame(
        [_fact("net_profit", "2025-12-31", "FY", 11033.0),
         _fact("net_profit", "2025-12-31", "4Q", 2358.0)],
        items, members, axis=AXIS)
    got = {(r["period"], r["period_span"]): r["value_num"]
           for r in df.to_dict("records")}
    assert got == {("2025-12-31", "FY"): 11033.0,
                   ("2025-12-31", "4Q"): 2358.0}


# --------------------------------------------------- 2. period_end_date mode
def test_period_end_date_anchor_matches_every_window_closing_that_day():
    """A balance is a photograph taken on the closing date, so it is equally
    the 4Q25 figure and the FY25 figure. This fan-out is what puts the
    balance sheet beside the income statement in one column."""
    assert target_period_labels(
        "2025-12-31", "as_at", FILTER_BY_PERIOD_END_DATE, AXIS) == ["4Q25", "FY25"]
    # The span it happens to be stamped with is irrelevant in this mode --
    # that is the entire point, since banks stamp the same balance `as_at`,
    # `4Q`, `2H` and `FY` depending on the column it was printed under.
    assert target_period_labels(
        "2025-12-31", "FY", FILTER_BY_PERIOD_END_DATE, AXIS) == ["4Q25", "FY25"]
    assert target_period_labels(
        "2026-06-30", "as_at", FILTER_BY_PERIOD_END_DATE, AXIS) == ["1H26"]


def test_period_end_date_repeats_the_balance_it_does_not_sum_it():
    """The same balance filed under three spans is ONE fact recorded three
    times. Summing them rendered DBS 4Q25 total assets as 2,692,464 against a
    filed 897,488."""
    items = [{"label": "Total assets", "concept": "Total assets",
              "unit_hint": None, "section": "BS"}]
    members = {"Total assets": [("FS_BALANCE_SELECTED", "total_assets", 1,
                                 None, None, FILTER_BY_PERIOD_END_DATE)]}
    df = anchor_highlights_frame(
        [_fact("total_assets", "2025-12-31", "4Q", 897488.0, "FS_BALANCE_SELECTED"),
         _fact("total_assets", "2025-12-31", "2H", 897488.0, "FS_BALANCE_SELECTED"),
         _fact("total_assets", "2025-12-31", "FY", 897488.0, "FS_BALANCE_SELECTED")],
        items, members, axis=AXIS)
    got = {(r["period"], r["period_span"]): r["value_num"]
           for r in df.to_dict("records")}
    assert got == {("2025-12-31", "4Q"): 897488.0,
                   ("2025-12-31", "FY"): 897488.0}


def test_flow_and_stock_land_in_the_same_column():
    """Issue #2 in one assertion: under the old basis radio, `fy` showed the
    income and blanked the balance sheet and `as_at` did the reverse."""
    items = [{"label": "Net profit", "concept": "Net profit",
              "unit_hint": None, "section": "P&L"},
             {"label": "Total assets", "concept": "Total assets",
              "unit_hint": None, "section": "BS"}]
    members = {
        "Net profit": [("FS_INCOME_SELECTED", "net_profit", 1, None, None,
                        FILTER_BY_PERIOD_LABEL)],
        "Total assets": [("FS_BALANCE_SELECTED", "total_assets", 1, None, None,
                          FILTER_BY_PERIOD_END_DATE)]}
    df = anchor_highlights_frame(
        [_fact("net_profit", "2026-06-30", "1H", 6009.0),
         _fact("total_assets", "2026-06-30", "as_at", 965597.0,
               "FS_BALANCE_SELECTED")],
        items, members, axis=AXIS)
    in_1h26 = {r["label"]: r["value_num"] for r in df.to_dict("records")
               if (r["period"], r["period_span"]) == ("2026-06-30", "1H")}
    assert in_1h26 == {"Net profit": 6009.0, "Total assets": 965597.0}


# --------------------------------------------------------- 3. the NULL default
def test_null_filter_by_falls_back_to_table_type_default():
    """A blank CSV cell -- and a CSV with no `filter_by` column at all --
    takes the table_type_id default. Matched on the FS_BALANCE prefix, so an
    unseen balance-sheet variant is classified without an edit."""
    for tt in ("FS_BALANCE_SELECTED", "FS_BALANCE_CONSOLIDATED",
               "FS_BALANCE_STATUTORY", "FS_BALANCE_SOMETHING_NEW",
               "fs_balance_selected"):
        assert default_filter_by(tt) == FILTER_BY_PERIOD_END_DATE, tt
    for tt in ("FS_INCOME_SELECTED", "FS_RATIOS_KEY", "FS_PER_SHARE",
               "", None):
        assert default_filter_by(tt) == FILTER_BY_PERIOD_LABEL, tt

    for blank in (None, "", "   "):
        assert resolve_filter_by(blank, "FS_BALANCE_SELECTED") == \
            FILTER_BY_PERIOD_END_DATE
        assert resolve_filter_by(blank, "FS_INCOME_SELECTED") == \
            FILTER_BY_PERIOD_LABEL


def test_unrecognised_filter_by_falls_back_rather_than_becoming_a_third_mode():
    """A hand-typed CSV value. A typo that silently became its own mode would
    match nothing and blank the row with no error anywhere."""
    assert resolve_filter_by("period_enddate", "FS_BALANCE_SELECTED") == \
        FILTER_BY_PERIOD_END_DATE
    assert resolve_filter_by("PERIOD_LABEL", "FS_BALANCE_SELECTED") == \
        FILTER_BY_PERIOD_END_DATE


# ------------------------------------------------------- 4. explicit override
def test_explicit_filter_by_overrides_the_default():
    """Declaring the mode wins over the table type, in both directions."""
    assert resolve_filter_by(FILTER_BY_PERIOD_LABEL, "FS_BALANCE_SELECTED") == \
        FILTER_BY_PERIOD_LABEL
    assert resolve_filter_by(FILTER_BY_PERIOD_END_DATE, "FS_RATIOS_KEY") == \
        FILTER_BY_PERIOD_END_DATE
    # Whitespace around an authored value is tolerated.
    assert resolve_filter_by("  period_end_date  ", "FS_RATIOS_KEY") == \
        FILTER_BY_PERIOD_END_DATE


def test_override_changes_where_a_fact_lands(tmp_path):
    """End to end through the CSV loader: the same ratio anchor placed by
    label, then by end date, from the file alone."""
    def _write(filter_by):
        p = tmp_path / f"{filter_by or 'blank'}_anchors.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["concept", "row_order", "section", "bank",
                        "table_type_id", "canonical_leaf_id", "sign",
                        "filter_by"])
            w.writerow(["CET1", 1, "Ratios", "DBS", "FS_RATIOS_KEY",
                        "cet1", 1, filter_by])
        return p

    _write(FILTER_BY_PERIOD_END_DATE)
    items, members = load_dashboard_anchors("DBS", dashboards_dir=tmp_path)
    assert members["CET1"][0][5] == FILTER_BY_PERIOD_END_DATE
    df = anchor_highlights_frame(
        [_fact("cet1", "2025-12-31", "4Q", 17.4, "FS_RATIOS_KEY")],
        items, members, axis=AXIS)
    # By end date it reaches BOTH windows closing 2025-12-31...
    assert sorted(r["period_span"] for r in df.to_dict("records")) == ["4Q", "FY"]

    for f in tmp_path.glob("*_anchors.csv"):
        f.unlink()
    _write("")            # blank -> FS_RATIOS_KEY default -> period_label
    items, members = load_dashboard_anchors("DBS", dashboards_dir=tmp_path)
    assert members["CET1"][0][5] == FILTER_BY_PERIOD_LABEL
    df = anchor_highlights_frame(
        [_fact("cet1", "2025-12-31", "4Q", 17.4, "FS_RATIOS_KEY")],
        items, members, axis=AXIS)
    # ...and by label it reaches only its own.
    assert [r["period_span"] for r in df.to_dict("records")] == ["4Q"]


def test_csv_without_a_filter_by_column_still_loads(tmp_path):
    """Backward compatibility: the column is new, and a file predating it
    must load with every member taking its table-type default."""
    p = tmp_path / "old_anchors.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["concept", "row_order", "section", "bank",
                    "table_type_id", "canonical_leaf_id", "sign"])
        w.writerow(["Total assets", 1, "BS", "DBS", "FS_BALANCE_SELECTED",
                    "total_assets", 1])
        w.writerow(["Net profit", 2, "P&L", "DBS", "FS_INCOME_SELECTED",
                    "net_profit", 1])
    _items, members = load_dashboard_anchors("DBS", dashboards_dir=tmp_path)
    assert members["Total assets"][0][5] == FILTER_BY_PERIOD_END_DATE
    assert members["Net profit"][0][5] == FILTER_BY_PERIOD_LABEL


# ------------------------------------------------------------ the axis itself
def test_axis_is_built_from_flows_only():
    """A stock must not mint a column. Letting it put '30-Jun-26' beside
    '1H26' as two columns describing the same close, each holding half the
    grid."""
    rows = [{"period": "2026-06-30", "period_span": "1H", "label": "x"},
            {"period": "2026-06-30", "period_span": "as_at", "label": "y"},
            {"period": "2025-12-31", "period_span": "FY", "label": "z"}]
    assert fiscal_period_axis(rows) == [("FY25", "2025-12-31", "FY"),
                                        ("1H26", "2026-06-30", "1H")]


def test_no_axis_keeps_the_pre_spec_behaviour():
    """`axis=None` is the identity placement every caller had before §4.5."""
    items = [{"label": "Total assets", "concept": "Total assets",
              "unit_hint": None, "section": "BS"}]
    members = {"Total assets": [("FS_BALANCE_SELECTED", "total_assets", 1)]}
    df = anchor_highlights_frame(
        [_fact("total_assets", "2026-06-30", "as_at", 965597.0,
               "FS_BALANCE_SELECTED")],
        items, members)
    assert df.to_dict("records")[0]["period_span"] == "as_at"
