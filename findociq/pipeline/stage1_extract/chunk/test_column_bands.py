"""Plain check() script for stage1_extract.chunk.transforms.validate_column_bands (NO pytest).
Exit 0 all-pass / 1 any-fail.

Run:  python findociq/pipeline/stage2_load/test_column_bands.py

Exercises the real DBS 1Q26 trading-update audit fixtures (parsed.json loaded
into the actual Extraction/GTable schema), against the real PDF, page 6/7 —
see docs/specs/2026-07-29-column-band-validator.md for ground truth.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path
from stage1_extract.chunk.schema import Extraction  # noqa: E402
from stage1_extract.chunk.transforms import validate_column_bands  # noqa: E402

_REPO = Path(__file__).resolve().parents[4]
_PDF = str(_REPO / "findociq/data/sources/financial_statements/DBS_1Q26_trading_update.pdf")

# Frozen pre-repair fixture (see fixtures/dbs_1Q26_col_shift/README.md). Was an
# absolute scratchpad path on a since-retired laptop, which made these checks
# unrunnable anywhere else. NOT the tracked audit dir: that artifact is the
# POST-repair extraction, so the col-shift defect is already gone from it.
_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "dbs_1Q26_col_shift"

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


def _load_pages(unit_dir_name: str) -> list[int]:
    m = json.loads((_FIXTURE_ROOT / unit_dir_name / "meta.json").read_text())
    return m["pages"]


# ===========================================================================
# 1) POSITIVE — selected_balance_sheet_items_m: the two 'Constant-currency
#    change' rows are geometrically shifted (values landed one column left
#    of where they are printed) and must be flagged.
# ===========================================================================
def positive_test() -> None:
    print("Positive — selected_balance_sheet_items_m (p6) flags the col-shift")
    unit_dir = "selected_balance_sheet_items_m_p6"
    ext = _load_ext(unit_dir)
    pages = _load_pages(unit_dir)

    issues = validate_column_bands(ext, _PDF, pages)
    print("  issues:")
    for s in issues:
        print("   ", s)

    shift_issues = [s for s in issues if s.strip().startswith("col-shift:")]
    check("exactly 2 col-shift issues (both Constant-currency change rows)",
          len(shift_issues) == 2, str(shift_issues))
    check("both col-shift issues mention 'Constant-currency change'",
          all("Constant-currency change" in s for s in shift_issues), str(shift_issues))
    # Ground truth: printed bands [3,5] (the two '% chg' columns), extracted
    # slots [2,4] (one column left of true position).
    check("printed bands [3,5] -> extracted slots [2,4] reported",
          all("printed bands [3,5] -> extracted slots [2,4]" in s for s in shift_issues),
          str(shift_issues))
    # No other row in this unit should be flagged (Customer loans etc. are correct).
    check("no col-shift issue for 'Customer loans'",
          not any("Customer loans" in s for s in shift_issues), str(shift_issues))
    check("no col-shift issue for 'Total assets'",
          not any("Total assets" in s for s in shift_issues), str(shift_issues))


# ===========================================================================
# 2) NEGATIVE — units that extracted correctly must not be flagged.
# ===========================================================================
def negative_test() -> None:
    print("Negative — correctly-extracted units are not flagged")
    for unit_dir in (
        "selected_income_statement_items_m_p6",
        "key_financial_ratios_2_3_p6",
        "per_share_data_3_p7",
    ):
        ext = _load_ext(unit_dir)
        pages = _load_pages(unit_dir)
        issues = validate_column_bands(ext, _PDF, pages)
        shift_issues = [s for s in issues if s.strip().startswith("col-shift:")]
        check(f"{unit_dir}: no col-shift issues", shift_issues == [], str(issues))


# ===========================================================================
# 3) UNCALIBRATED GUARD — per_share_data_3 (p7) only prints 3 of its 5 value
#    columns per row (the two '% chg' columns are blank for per-share data),
#    and page 7 has no neighboring table sharing its column grid, so no line
#    on the page reaches the 5-numeric-token density needed to calibrate.
#    The guard must fire visibly, not silently pass.
#
#    (key_financial_ratios_2_3 on p6 is the same 3-of-5 shape, but p6 also
#    hosts the income-statement and balance-sheet tables which DO print
#    dense 5-token lines on the same physical column grid — calibration
#    borrows those page-wide, per the spec's "collect ... across all dense
#    lines" step, and correctly validates the ratios rows as unshifted. That
#    is covered by negative_test() above; the guard itself is exercised
#    here.)
# ===========================================================================
def uncalibrated_guard_test() -> None:
    print("Uncalibrated guard — visible note, not silent pass")
    unit_dir = "per_share_data_3_p7"
    ext = _load_ext(unit_dir)
    pages = _load_pages(unit_dir)
    issues = validate_column_bands(ext, _PDF, pages)
    note_issues = [s for s in issues if "uncalibrated" in s]
    check(f"{unit_dir}: uncalibrated note present", len(note_issues) >= 1, str(issues))
    check(f"{unit_dir}: uncalibrated note mentions dense-line count",
          any("dense lines" in s for s in note_issues), str(note_issues))


# ===========================================================================
# 4) NO MUTATION — validate_column_bands must never touch the Extraction.
# ===========================================================================
def no_mutation_test() -> None:
    print("No mutation — detection only")
    unit_dir = "selected_balance_sheet_items_m_p6"
    ext = _load_ext(unit_dir)
    pages = _load_pages(unit_dir)
    before = ext.model_dump_json()
    validate_column_bands(ext, _PDF, pages)
    after = ext.model_dump_json()
    check("Extraction unchanged after validate_column_bands", before == after)


if __name__ == "__main__":
    positive_test()
    negative_test()
    uncalibrated_guard_test()
    no_mutation_test()
    print(f"\n{'ALL PASS' if _FAILS == 0 else str(_FAILS) + ' FAILED'}")
    raise SystemExit(1 if _FAILS else 0)
