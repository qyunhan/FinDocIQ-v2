"""Regression tests for toc_stage.build_windows() region attribution.

Root cause (2026-07-27 investigation, 3 already-BigQuery-synced DBS
trading_update docs missing their income-statement/balance-sheet tables):

  Bug 1 — window boundary inconsistency. page_end already applies a
  "does the next section's anchor sit in the page's top strip" correction
  (TOP_OF_PAGE_Y) so a section doesn't absorb a page that really belongs to
  the next section. The attribution window (_win_lo/_win_hi) used the next
  anchor's RAW (page, y) instead of the same snapped boundary, so a region
  sitting in the next page's top strip (above the next section's anchor, but
  on that next page) was wrongly swallowed by the PREVIOUS section's window.
  Fixed via _breakpoint(): every window boundary is snapped the same way
  page_end already snaps its own boundary.

  Bug 2 — exclusive point attribution. PaddleOCR sometimes emits ONE region
  spanning several consecutive sections' tables (visually contiguous tables
  stacked on one summary page). The old code attributed a region to exactly
  ONE section (first window whose point-test matched). Fixed via
  _window_overlaps_region(): a region attaches to EVERY section whose window
  vertically overlaps the region's [y0, y1] span, not just one.

See docs/specs (fs-branch-pipeline) and data/derived/paddle_scans/
1Q23_trading_update/regions.csv for the real region that exposed Bug 2.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.toc import toc_stage as T  # noqa: E402


def _sec(id_, page, y, level=1, parent_id=None, gseq=0):
    return {"id": id_, "title": id_, "parent_id": parent_id, "level": level,
            "anchor_page": page, "anchor_y": y, "_gseq": gseq}


def _region(page, table_idx, y0, y1, x0=0.0, x1=600.0):
    return {"page": page, "table_idx": table_idx,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _by_id(ordered):
    return {s["id"]: s for s in ordered}


# ---- Bug 1: window boundary must agree with page_end's top-of-page snap --
def test_region_in_next_pages_top_strip_not_stolen_by_prior_section():
    """DBS 1Q23 shape: 'dividends' anchored deep on page 4 (anchor_y > 200);
    the next section anchors at (5, 116.51) — in page 5's top strip. A region
    at (5, y0=89.74) sits ABOVE that next anchor, i.e. in page 5's top strip.
    It must NOT be attributed to 'dividends' — the entire page 5 belongs to
    the next section (mirroring page_end's own top-of-page correction)."""
    secs = [
        _sec("dividends", page=4, y=500.0, gseq=0),
        _sec("next_section", page=5, y=116.51, gseq=1),
    ]
    regions = [_region(page=5, table_idx=0, y0=89.74, y1=200.0)]
    ordered, attributed, preamble = T.build_windows(secs, last_page=5, regions=regions)
    by_id = _by_id(ordered)

    assert by_id["dividends"]["page_end"] == 4, by_id["dividends"]["page_end"]
    assert by_id["dividends"]["n_regions"] == 0
    assert by_id["dividends"]["has_tables"] is False
    assert by_id["next_section"]["n_regions"] == 1
    assert by_id["next_section"]["has_tables"] is True
    assert attributed[(5, 0)] == [by_id["next_section"]]
    assert not preamble


def test_window_boundary_matches_page_end_when_next_anchor_not_top_of_page():
    """Complementary case: next section's anchor_y > TOP_OF_PAGE_Y (not in the
    top strip) -> page_end includes that page, and a region on that shared
    page anchored ABOVE the next section's anchor still belongs to the FIRST
    section (nothing changed here vs. the old point-test — the top strip
    correction only fires for top-of-page next-anchors)."""
    secs = [
        _sec("first", page=4, y=500.0, gseq=0),
        _sec("second", page=5, y=400.0, gseq=1),   # not top-of-page
    ]
    regions = [_region(page=5, table_idx=0, y0=50.0, y1=150.0)]
    ordered, attributed, preamble = T.build_windows(secs, last_page=5, regions=regions)
    by_id = _by_id(ordered)

    assert by_id["first"]["page_end"] == 5
    assert by_id["first"]["n_regions"] == 1
    assert by_id["first"]["has_tables"] is True
    assert by_id["second"]["n_regions"] == 0
    assert not preamble


# ---- Bug 2: one region can legitimately belong to several sections -------
def test_one_region_spanning_four_sections_attributes_to_all_four():
    """1Q23_trading_update page 5 shape: one PaddleOCR region
    (y0=89.74, y1=728.14) visually covers 4 distinct stacked tables/sections.
    Every section whose window overlaps that span must get has_tables=True
    with the region counted in n_regions — not just the first one."""
    secs = [
        _sec("selected_income_statement_items_m", page=5, y=210.0, gseq=0),
        _sec("selected_balance_sheet_items_m", page=5, y=300.0, gseq=1),
        _sec("key_financial_ratios_4", page=5, y=450.0, gseq=2),
        _sec("per_share_data", page=5, y=600.0, gseq=3),
    ]
    regions = [_region(page=5, table_idx=0, y0=89.74, y1=728.14, x0=68.05, x1=540.73)]
    ordered, attributed, preamble = T.build_windows(secs, last_page=5, regions=regions)
    by_id = _by_id(ordered)

    for sid in ("selected_income_statement_items_m", "selected_balance_sheet_items_m",
                "key_financial_ratios_4", "per_share_data"):
        assert by_id[sid]["n_regions"] == 1, (sid, by_id[sid]["n_regions"])
        assert by_id[sid]["has_tables"] is True, sid
    assert len(attributed[(5, 0)]) == 4
    assert not preamble


# ---- common path must not regress -----------------------------------------
def test_region_cleanly_inside_one_section_unambiguous():
    """One region sitting entirely inside a single section's window (no
    overlap with any neighboring window) attributes to exactly that section —
    the ordinary, unambiguous case must keep working."""
    secs = [
        _sec("intro", page=1, y=50.0, gseq=0),      # top-of-page -> owns all p1
        _sec("next_page", page=2, y=100.0, gseq=1),  # top-of-page -> owns all p2
    ]
    regions = [_region(page=1, table_idx=0, y0=100.0, y1=300.0)]
    ordered, attributed, preamble = T.build_windows(secs, last_page=2, regions=regions)
    by_id = _by_id(ordered)

    assert by_id["intro"]["n_regions"] == 1
    assert by_id["intro"]["has_tables"] is True
    assert by_id["next_page"]["n_regions"] == 0
    assert by_id["next_page"]["has_tables"] is False
    assert attributed[(1, 0)] == [by_id["intro"]]
    assert not preamble


def test_region_before_first_section_is_preamble():
    """A region on a cover/contents page, entirely before the first kept
    section's window, is recorded as preamble — not attributed, not dropped."""
    secs = [_sec("first", page=3, y=100.0, gseq=0)]
    regions = [_region(page=1, table_idx=0, y0=10.0, y1=50.0)]
    ordered, attributed, preamble = T.build_windows(secs, last_page=3, regions=regions)
    by_id = _by_id(ordered)

    assert by_id["first"]["n_regions"] == 0
    assert by_id["first"]["has_tables"] is False
    assert preamble == [{"page": 1, "table_idx": 0}]
    assert (1, 0) not in attributed


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print("ALL PASS")
