"""Tests for the build_units containment guard (extract._drop_containing_wrappers).

Locks the 2026-07-16 backstop: no extraction unit may STRICTLY CONTAIN another;
the outer wrapper is dropped (inner per-page units carry the data). This catches
a table-bearing section left spanning many pages after its children were re-homed
(e.g. 'financial_highlights' p10-22) that would otherwise become a giant dense
extraction unit. See docs/specs/2026-07-16-running-header-detection.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.chunk import extract as E  # noqa: E402


def _leaf(sid, p0, p1):
    return {"section_id": sid, "start_page": p0, "end_page": p1}


def test_wrapper_dropped_inner_units_kept():
    leaves = [
        _leaf("financial_highlights", 10, 22),     # wrapper
        _leaf("selected_balance_sheet_items", 10, 10),
        _leaf("non_interest_income", 14, 14),
        _leaf("non_performing_assets", 17, 18),     # legit, contains no unit
    ]
    kept = {l["section_id"] for l in E._drop_containing_wrappers(leaves)}
    assert kept == {"selected_balance_sheet_items", "non_interest_income",
                    "non_performing_assets"}


def test_equal_span_units_both_kept():
    """Two tables on the same page — neither strictly contains the other."""
    leaves = [_leaf("a", 13, 13), _leaf("b", 13, 13)]
    assert len(E._drop_containing_wrappers(leaves)) == 2


def test_legit_spanning_section_kept_when_contains_nothing():
    """A real 2-page table that wraps no other unit is kept."""
    leaves = [_leaf("npa", 17, 18), _leaf("elsewhere", 20, 20)]
    kept = {l["section_id"] for l in E._drop_containing_wrappers(leaves)}
    assert kept == {"npa", "elsewhere"}


def test_nested_wrapper_dropped_not_inner():
    """Outer p1-5 wraps inner p3 (strictly interior) -> drop outer, keep inner."""
    leaves = [_leaf("outer", 1, 5), _leaf("inner", 3, 3)]
    kept = {l["section_id"] for l in E._drop_containing_wrappers(leaves)}
    assert kept == {"inner"}


def test_p3_boundary_share_start_not_dropped():
    """Pillar 3 regression: A.6.3 p17-18 next to sibling A.6.2 p17 — the shared
    START page is a span-estimate touch, NOT containment. Both kept."""
    leaves = [_leaf("A.6.2", 17, 17), _leaf("A.6.3", 17, 18)]
    kept = {l["section_id"] for l in E._drop_containing_wrappers(leaves)}
    assert kept == {"A.6.2", "A.6.3"}


def test_p3_boundary_share_end_not_dropped():
    """Pillar 3 regression: A.12.2.7 p45-49 next to A.12.2.8 p49 — shared END
    page is a touch, not containment. Both kept."""
    leaves = [_leaf("A.12.2.7", 45, 49), _leaf("A.12.2.8", 49, 49)]
    kept = {l["section_id"] for l in E._drop_containing_wrappers(leaves)}
    assert kept == {"A.12.2.7", "A.12.2.8"}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print("ALL PASS")
