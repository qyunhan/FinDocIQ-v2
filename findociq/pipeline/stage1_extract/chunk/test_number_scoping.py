"""Plain check() script for stage1_extract.chunk.transforms.validate_numbers section-scoping
(the "Companion fix" in docs/specs/2026-07-29-column-band-validator.md),
NO pytest. Exit 0 all-pass / 1 any-fail.

Run:  python findociq/pipeline/stage2_load/test_number_scoping.py

validate_numbers used to scan the WHOLE page for deficits while extraction
units are SECTION-scoped. On a multi-section page every unit got charged with
every OTHER section's numbers: DBS_1Q26 p6 carries 3 sections
(selected_income_statement_items_m, selected_balance_sheet_items_m,
key_financial_ratios_2_3) and the income-statement unit produced 51 spurious
deficits, symmetric for the balance-sheet unit. Fix: scope the scan to the
unit's own section y-region via section_region_for_unit(), which prefers
page_section_regions() (numbered Pillar-3 headings) and falls back to
title-text word-position search (slug-id FS sections, where
page_section_regions() returns {} — confirmed on this exact fixture).

Exercises the real DBS 1Q26 trading-update audit fixtures (parsed.json loaded
into the actual Extraction/GTable schema), against the real PDF, page 6 —
same fixtures as test_column_bands.py.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path
from stage1_extract.chunk.schema import Extraction, GCell  # noqa: E402
from stage1_extract.chunk.transforms import (  # noqa: E402
    validate_numbers,
    section_region_for_unit,
    page_section_regions,
)

_REPO = Path(__file__).resolve().parents[4]
_PDF = str(_REPO / "findociq/data/sources/financial_statements/DBS_1Q26_trading_update.pdf")

# Frozen pre-repair fixture (see fixtures/dbs_1Q26_col_shift/README.md). Was an
# absolute scratchpad path on a since-retired laptop, which made these checks
# unrunnable anywhere else. NOT the tracked audit dir: that artifact is the
# POST-repair extraction, so the col-shift defect is already gone from it.
_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "dbs_1Q26_col_shift"

# The three sibling sections sharing page 6, in printed top-to-bottom order —
# used to build realistic `unit` dicts (leaves + next_leaves) the same shape
# build_units() produces, so section_region_for_unit() sees the same sibling
# info it would see in the real pipeline.
_P6_SECTIONS = [
    ("selected_income_statement_items_m", "selected_income_statement_items_m_p6"),
    ("selected_balance_sheet_items_m",    "selected_balance_sheet_items_m_p6"),
    ("key_financial_ratios_2_3",          "key_financial_ratios_2_3_p6"),
]

_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILS += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


def _load_ext(unit_dir_name: str) -> Extraction:
    p = _FIXTURE_ROOT / unit_dir_name / "parsed.json"
    return Extraction(**json.loads(p.read_text()))


def _load_meta(unit_dir_name: str) -> dict:
    return json.loads((_FIXTURE_ROOT / unit_dir_name / "meta.json").read_text())


def _build_p6_leaves() -> list[dict]:
    """One leaf dict per p6 section, in printed order — mirrors what
    build_units() would construct from the section registry (section_id,
    title, start_page)."""
    leaves = []
    for sid, unit_dir in _P6_SECTIONS:
        meta = _load_meta(unit_dir)
        leaves.append({
            "section_id": sid,
            "title": meta["section_titles"][0],
            "start_page": meta["pages"][0],
        })
    return leaves


def _unit_for(idx: int, leaves: list[dict], meta: dict) -> dict:
    """Build a realistic unit dict for leaves[idx], with next_leaves computed
    the same way build_units() does: sibling leaves whose start_page equals
    this unit's boundary (last) page."""
    boundary = meta["pages"][-1]
    next_leaves = [leaves[j] for j in range(idx + 1, len(leaves))
                   if leaves[j]["start_page"] == boundary]
    return {"leaves": [leaves[idx]], "next_leaves": next_leaves, "pages": meta["pages"]}


# ===========================================================================
# 0) PRECONDITION — confirm page_section_regions() cannot resolve these
#    slug-id FS sections (the numbered-heading path is a no-op here), so the
#    test is actually exercising the title-match fallback, not the existing
#    numbered-heading machinery.
# ===========================================================================
def precondition_test() -> None:
    print("Precondition — page_section_regions() returns {} for slug-id FS page 6")
    regions = page_section_regions(_PDF, 6)
    check("page_section_regions(p6) == {} (slug ids, no numbered headings)",
          regions == {}, str(regions))


# ===========================================================================
# 1) POSITIVE — income-statement unit's deficit count drops from 51 to ~0
#    once scoped to its own y-region (was charged with every balance-sheet /
#    ratio number on the page).
# ===========================================================================
def income_statement_scoped_test() -> None:
    print("Income-statement unit (p6) — scoped deficits near zero")
    leaves = _build_p6_leaves()
    unit_dir = "selected_income_statement_items_m_p6"
    meta = _load_meta(unit_dir)
    ext = _load_ext(unit_dir)
    unit = _unit_for(0, leaves, meta)

    issues = validate_numbers(ext, _PDF, meta["pages"],
                               section_ids=tuple(meta["section_ids"]), unit=unit)
    deficits = [s for s in issues if "deficit:" in s]
    print("  issues:", issues)
    check("scoped deficit count <= 2 (was 51 page-wide)", len(deficits) <= 2, str(deficits))
    check("no cross-section deficit for balance-sheet number '453180'",
          not any("453180" in s for s in deficits), str(deficits))
    check("no cross-section deficit for balance-sheet number '935365'",
          not any("935365" in s for s in deficits), str(deficits))
    check("no scope_note (region resolved)",
          not any(s.strip().startswith("number-scan: unscoped") for s in issues), str(issues))


# ===========================================================================
# 2) SYMMETRIC — balance-sheet unit stops flagging income-statement numbers.
# ===========================================================================
def balance_sheet_scoped_test() -> None:
    print("Balance-sheet unit (p6) — scoped, no income-statement deficits")
    leaves = _build_p6_leaves()
    unit_dir = "selected_balance_sheet_items_m_p6"
    meta = _load_meta(unit_dir)
    ext = _load_ext(unit_dir)
    unit = _unit_for(1, leaves, meta)

    issues = validate_numbers(ext, _PDF, meta["pages"],
                               section_ids=tuple(meta["section_ids"]), unit=unit)
    deficits = [s for s in issues if "deficit:" in s]
    print("  issues:", issues)
    check("scoped deficit count == 0 (was 78 page-wide)", len(deficits) == 0, str(deficits))
    for income_num in ("2930", "2897", "5559"):
        check(f"no cross-section deficit for income-statement number '{income_num}'",
              not any(income_num in s for s in deficits), str(deficits))


# ===========================================================================
# 3) RECALL STILL CAUGHT — a genuinely missing number that IS inside the
#    unit's own section region must still be reported as a deficit. Proves
#    the fix scopes the scan rather than just suppressing output.
# ===========================================================================
def real_recall_failure_still_caught_test() -> None:
    print("Real recall failure inside own region — still flagged")
    leaves = _build_p6_leaves()
    unit_dir = "selected_income_statement_items_m_p6"
    meta = _load_meta(unit_dir)
    ext = _load_ext(unit_dir)
    unit = _unit_for(0, leaves, meta)

    # '3,475' (Net interest income, own section) is printed once. Blank out
    # the matching GCell to simulate a genuine extraction miss.
    ext = ext.model_copy(deep=True)
    hit = None
    for t in ext.tables:
        for row in t.rows:
            for cell in row.values:
                if isinstance(cell, GCell) and cell.value.replace(",", "") == "3475":
                    hit = cell
    check("setup: found '3,475' cell to blank out", hit is not None)
    if hit is not None:
        hit.value = ""
        hit.cell_state = "empty"

    issues = validate_numbers(ext, _PDF, meta["pages"],
                               section_ids=tuple(meta["section_ids"]), unit=unit)
    deficits = [s for s in issues if "deficit:" in s]
    print("  issues:", issues)
    check("'3475' reported as deficit after simulated recall failure",
          any("'3475'" in s for s in deficits), str(deficits))


# ===========================================================================
# 4) FALLBACK — unresolvable region falls back to page-wide scanning AND
#    emits a visible note. Silence must never read as "checked and clean".
# ===========================================================================
def unresolvable_region_falls_back_test() -> None:
    print("Unresolvable section title — falls back to page-wide, visibly")
    unit_dir = "selected_income_statement_items_m_p6"
    meta = _load_meta(unit_dir)
    ext = _load_ext(unit_dir)
    # A title that will never be found on the page.
    unit = {"leaves": [{"section_id": meta["section_ids"][0],
                         "title": "Totally Nonexistent Heading Text",
                         "start_page": meta["pages"][0]}],
            "next_leaves": [], "pages": meta["pages"]}

    scoped_issues   = validate_numbers(ext, _PDF, meta["pages"],
                                        section_ids=tuple(meta["section_ids"]), unit=unit)
    unscoped_issues = validate_numbers(ext, _PDF, meta["pages"],
                                        section_ids=tuple(meta["section_ids"]))  # no unit -> old page-wide path

    print("  fallback issues:", scoped_issues[:3], "...")
    note = [s for s in scoped_issues if s.strip().startswith("number-scan: unscoped")]
    check("visible unscoped note present", len(note) == 1, str(scoped_issues[:3]))

    fallback_deficits = [s for s in scoped_issues if "deficit:" in s]
    old_deficits       = [s for s in unscoped_issues if "deficit:" in s]
    check("fallback deficit count matches old page-wide behaviour",
          len(fallback_deficits) == len(old_deficits),
          f"fallback={len(fallback_deficits)} old={len(old_deficits)}")


# ===========================================================================
# 5) NO MUTATION — validate_numbers must never touch the Extraction.
# ===========================================================================
def no_mutation_test() -> None:
    print("No mutation — detection only")
    leaves = _build_p6_leaves()
    unit_dir = "selected_balance_sheet_items_m_p6"
    meta = _load_meta(unit_dir)
    ext = _load_ext(unit_dir)
    unit = _unit_for(1, leaves, meta)
    before = ext.model_dump_json()
    validate_numbers(ext, _PDF, meta["pages"], section_ids=tuple(meta["section_ids"]), unit=unit)
    after = ext.model_dump_json()
    check("Extraction unchanged after validate_numbers", before == after)


# ===========================================================================
# 6) DIRECT REGION CHECK — section_region_for_unit() returns the correct
#    y-bounds for all three p6 sections, matching the printed layout
#    (income 126.1-378.7, balance-sheet 378.7-496.4, ratios 496.4-page end).
# ===========================================================================
def region_bounds_test() -> None:
    print("section_region_for_unit — correct y-bounds for all 3 p6 sections")
    leaves = _build_p6_leaves()
    expected_starts = [126.1, 378.7, 496.4]
    for i, (sid, unit_dir) in enumerate(_P6_SECTIONS):
        meta = _load_meta(unit_dir)
        unit = _unit_for(i, leaves, meta)
        region, method = section_region_for_unit(_PDF, unit, 6)
        check(f"{sid}: region resolved via title-match", method == "title-match", method)
        check(f"{sid}: region is not None", region is not None)
        if region is not None:
            check(f"{sid}: y_start ~= {expected_starts[i]}",
                  abs(region[0] - expected_starts[i]) < 1.0, str(region))
    # Regions must be contiguous and non-overlapping across the three sections.
    regions = []
    for i, (sid, unit_dir) in enumerate(_P6_SECTIONS):
        meta = _load_meta(unit_dir)
        unit = _unit_for(i, leaves, meta)
        region, _ = section_region_for_unit(_PDF, unit, 6)
        regions.append(region)
    check("income end == balance-sheet start (contiguous)",
          abs(regions[0][1] - regions[1][0]) < 0.01, str(regions))
    check("balance-sheet end == ratios start (contiguous)",
          abs(regions[1][1] - regions[2][0]) < 0.01, str(regions))


if __name__ == "__main__":
    precondition_test()
    income_statement_scoped_test()
    balance_sheet_scoped_test()
    real_recall_failure_still_caught_test()
    unresolvable_region_falls_back_test()
    no_mutation_test()
    region_bounds_test()
    print(f"\n{'ALL PASS' if _FAILS == 0 else str(_FAILS) + ' FAILED'}")
    raise SystemExit(1 if _FAILS else 0)
