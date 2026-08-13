"""Plain check() script for stage1_extract.chunk.transforms.repair_column_bands (NO pytest).
Exit 0 all-pass / 1 any-fail.

Run:  python findociq/pipeline/stage2_load/test_column_repair.py

Phase 2 of docs/specs/2026-07-29-column-band-validator.md: deterministic,
guarded RE-SLOTTING of column-shifted rows. Uses the real DBS 1Q26
trading-update audit fixtures against the real PDF (pages 6/7).

Ground truth (printed page 6, x-positions from pdfplumber):
  column bands           1: 312.6  2: 357.6  3: 430.0  4: 447.7  5: 520.2
  'Customer loans'       453,180 | 435,295 |   4  | 445,011 |  2   (extracted OK)
  'Constant-currency'         —   |    —    |   6  |    —    |  2   (extracted WRONG:
                                                                     6 and 2 landed
                                                                     one column left)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path
from stage1_extract.chunk.schema import Extraction, GCell  # noqa: E402
from stage1_extract.chunk.transforms import (  # noqa: E402
    repair_column_bands,
    validate_column_bands,
    _repair_row,
)

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


def _vals(ext: Extraction, label: str, nth: int = 0) -> list[str]:
    hits = [r for t in ext.tables for r in t.rows if r.label == label]
    return [c.value for c in hits[nth].values]


def _word(text: str, x0: float, x1: float) -> dict:
    return {"text": text, "x0": x0, "x1": x1}


_BS = "selected_balance_sheet_items_m_p6"


# ===========================================================================
# a) POSITIVE — both 'Constant-currency change' rows are re-slotted into the
#    columns the printed geometry proves they belong to.
# ===========================================================================
def repair_test() -> None:
    print("a) Repair — both Constant-currency rows re-slotted to their printed bands")
    ext = _load_ext(_BS)
    pages = _load_pages(_BS)

    before_1 = _vals(ext, "Constant-currency change", 0)
    before_2 = _vals(ext, "Constant-currency change", 1)
    check("pre-repair row 1 is the known-bad ['','6','','2','']",
          before_1 == ["", "6", "", "2", ""], str(before_1))
    check("pre-repair row 2 is the known-bad ['','12','','3','']",
          before_2 == ["", "12", "", "3", ""], str(before_2))

    issues = repair_column_bands(ext, _PDF, pages)
    print("  issues:")
    for s in issues:
        print("   ", s)

    check("row 1 repaired to ['','','6','','2']",
          _vals(ext, "Constant-currency change", 0) == ["", "", "6", "", "2"],
          str(_vals(ext, "Constant-currency change", 0)))
    check("row 2 repaired to ['','','12','','3']",
          _vals(ext, "Constant-currency change", 1) == ["", "", "12", "", "3"],
          str(_vals(ext, "Constant-currency change", 1)))

    t = ext.tables[0]
    check("row count unchanged (guard 4)", len(t.rows) == 8, str(len(t.rows)))
    check("every row still has 5 cells (guard 4)",
          all(len(r.values) == 5 for r in t.rows if r.values),
          str([len(r.values) for r in t.rows]))

    # The moved cells keep their original GCell objects — nothing reformatted,
    # nothing invented: the repair is a pure permutation of the row's cells.
    row1 = [r for r in t.rows if r.label == "Constant-currency change"][0]
    check("moved cells keep cell_state 'reported'",
          [c.cell_state for c in row1.values] ==
          ["empty", "empty", "reported", "empty", "reported"],
          str([c.cell_state for c in row1.values]))


# ===========================================================================
#    …and re-running DETECTION on the repaired Extraction is now clean, while
#    detection on the RAW extraction still reports what was wrong.
# ===========================================================================
def detection_still_reports_test() -> None:
    print("a2) Detection reports the shift BEFORE repair, and is clean AFTER")
    ext = _load_ext(_BS)
    pages = _load_pages(_BS)

    before = [s for s in validate_column_bands(ext, _PDF, pages)
              if s.strip().startswith("col-shift:")]
    check("detection on raw extraction still reports 2 col-shift issues",
          len(before) == 2, str(before))

    repair_column_bands(ext, _PDF, pages)

    after = [s for s in validate_column_bands(ext, _PDF, pages)
             if s.strip().startswith("col-shift:")]
    check("detection after repair reports 0 col-shift issues", after == [], str(after))


# ===========================================================================
# b) NO FALSE REPAIRS — correctly-extracted rows/units are byte-identical.
# ===========================================================================
def no_false_repair_test() -> None:
    print("b) No false repairs — correct rows are byte-identical after repair")
    ext = _load_ext(_BS)
    pages = _load_pages(_BS)

    keep = {lbl: _vals(ext, lbl) for lbl in
            ("Customer loans", "Total assets", "Customer deposits",
             "of which: Non-performing assets", "Total liabilities",
             "Shareholders’ funds")}

    issues = repair_column_bands(ext, _PDF, pages)

    for lbl, before in keep.items():
        check(f"'{lbl}' unchanged", _vals(ext, lbl) == before,
              f"{before} -> {_vals(ext, lbl)}")
    check("only the 2 shifted rows were repaired",
          len([s for s in issues if "slots [" in s]) == 2, str(issues))
    check("no repair issue names a correct row",
          not any(lbl in s for s in issues if "slots [" in s
                  for lbl in ("Customer loans", "Total assets")), str(issues))

    # Whole correctly-extracted units must come out byte-identical.
    for unit_dir in ("selected_income_statement_items_m_p6", "key_financial_ratios_2_3_p6"):
        e = _load_ext(unit_dir)
        snapshot = e.model_dump_json()
        e_issues = repair_column_bands(e, _PDF, _load_pages(unit_dir))
        check(f"{unit_dir}: Extraction byte-identical after repair",
              e.model_dump_json() == snapshot)
        check(f"{unit_dir}: no repair performed",
              not any("slots [" in s for s in e_issues), str(e_issues))


# ===========================================================================
# c) GUARDS — each one declines rather than corrupting.
# ===========================================================================
def guard1_uncalibrated_test() -> None:
    print("c1) Guard 1 — uncalibrated table never repairs")
    unit_dir = "per_share_data_3_p7"
    ext = _load_ext(unit_dir)
    snapshot = ext.model_dump_json()
    issues = repair_column_bands(ext, _PDF, _load_pages(unit_dir))
    print("  issues:", issues)
    check("Extraction untouched", ext.model_dump_json() == snapshot)
    check("declined issue emitted", any("declined" in s for s in issues), str(issues))
    check("decline reason names uncalibrated",
          any("uncalibrated" in s for s in issues), str(issues))


def guard2_value_mismatch_test() -> None:
    print("c2) Guard 2 — extracted values that don't match the printed tokens")
    ext = _load_ext(_BS)
    pages = _load_pages(_BS)
    row = [r for t in ext.tables for r in t.rows
           if r.label == "Constant-currency change"][0]
    # '6' -> '7': still shifted (detection fires) but the values are NOT the
    # printed ones, so re-slotting them would launder a wrong value into the
    # right-looking column. Must decline.
    row.values[1] = GCell(value="7", cell_state="reported")
    before = [c.value for c in row.values]

    issues = repair_column_bands(ext, _PDF, pages)
    print("  issues:", [s for s in issues if "declined" in s])
    check("mismatched row untouched", [c.value for c in row.values] == before,
          str([c.value for c in row.values]))
    check("declined issue emitted for that row",
          any("declined" in s and "Constant-currency change" in s for s in issues),
          str(issues))
    check("decline reason names the value mismatch",
          any("declined" in s and ("7" in s) for s in issues), str(issues))
    # The OTHER Constant-currency row is untouched by the failure of this one.
    check("the second (still-valid) row was repaired independently",
          _vals(ext, "Constant-currency change", 1) == ["", "", "12", "", "3"],
          str(_vals(ext, "Constant-currency change", 1)))


def guard2_extra_value_test() -> None:
    print("c3) Guard 2 — extracted row carries a value the printed line does not")
    ext = _load_ext(_BS)
    pages = _load_pages(_BS)
    row = [r for t in ext.tables for r in t.rows
           if r.label == "Constant-currency change"][0]
    row.values[0] = GCell(value="99", cell_state="reported")  # 3 values vs 2 printed
    before = [c.value for c in row.values]
    issues = repair_column_bands(ext, _PDF, pages)
    check("row untouched", [c.value for c in row.values] == before,
          str([c.value for c in row.values]))
    check("declined issue emitted",
          any("declined" in s and "Constant-currency change" in s for s in issues),
          str(issues))


def guard3_ambiguous_band_test() -> None:
    print("c4) Guard 3 — a printed token that does not map to exactly one band")
    # White-box: geometry this pathological cannot be produced from the fixture
    # PDF, so drive the row-level repair directly with synthetic bands.
    from stage1_extract.chunk.schema import GRow

    def _row():
        return GRow(level=1, label="X", values=[
            GCell(value="", cell_state="empty"),
            GCell(value="6", cell_state="reported"),
            GCell(value="", cell_state="empty"),
        ])

    # (i) overlapping bands — the token's centre sits in TWO bands.
    r = _row()
    before = [c.value for c in r.values]
    out = _repair_row(r, [_word("6", 100, 110)], [(0, 50), (90, 120), (105, 200)], 3, {1})
    check("overlapping bands: row untouched", [c.value for c in r.values] == before)
    check("overlapping bands: declined with reason",
          len(out) == 1 and "declined" in out[0] and "2 band" in out[0], str(out))

    # (ii) token outside every band.
    r = _row()
    out = _repair_row(r, [_word("6", 300, 310)], [(0, 50), (90, 120), (150, 200)], 3, {1})
    check("unbanded token: row untouched", [c.value for c in r.values] == before)
    check("unbanded token: declined with reason",
          len(out) == 1 and "declined" in out[0] and "0 band" in out[0], str(out))

    # (iii) two printed tokens collapsing into the SAME band — no unambiguous
    #       destination for either.
    r = GRow(level=1, label="X", values=[
        GCell(value="6", cell_state="reported"),
        GCell(value="2", cell_state="reported"),
        GCell(value="", cell_state="empty"),
    ])
    before3 = [c.value for c in r.values]
    out = _repair_row(r, [_word("6", 95, 100), _word("2", 105, 110)],
                      [(0, 50), (90, 120), (150, 200)], 3, {0, 1})
    check("two tokens one band: row untouched", [c.value for c in r.values] == before3)
    check("two tokens one band: declined with reason",
          len(out) == 1 and "declined" in out[0] and "share band" in out[0], str(out))


def guard4_cell_count_test() -> None:
    print("c5) Guard 4 — cell count must not change")
    ext = _load_ext(_BS)
    pages = _load_pages(_BS)
    row = [r for t in ext.tables for r in t.rows
           if r.label == "Constant-currency change"][0]
    del row.values[4]                      # 4 cells under a 5-column table
    before = [c.value for c in row.values]
    issues = repair_column_bands(ext, _PDF, pages)
    check("short row untouched", [c.value for c in row.values] == before,
          str([c.value for c in row.values]))
    check("declined issue emitted",
          any("declined" in s and "Constant-currency change" in s for s in issues),
          str(issues))
    check("decline reason names the cell/column count",
          any("declined" in s and "cell" in s for s in issues), str(issues))


# ===========================================================================
# d) AUDITABILITY — every repair is recorded, in the issue list and in
#    meta.json's validation.column_band_issues.
# ===========================================================================
def auditability_test() -> None:
    print("d) Auditability — the repair is recorded in the issue list")
    ext = _load_ext(_BS)
    pages = _load_pages(_BS)
    issues = repair_column_bands(ext, _PDF, pages)
    repairs = [s for s in issues if "slots [" in s]
    check("exactly 2 repair records", len(repairs) == 2, str(issues))
    check("records use the col-repair: '<label>' slots [2,4] -> bands [3,5] form",
          all(s.strip().startswith("col-repair: 'Constant-currency change' "
                                   "slots [2,4] -> bands [3,5]") for s in repairs),
          str(repairs))


def meta_wiring_test() -> None:
    print("d2) Auditability — repairs land in meta.json's column_band_issues")
    from stage1_extract.chunk.extract import _finalize_unit

    ext = _load_ext(_BS)
    pages = _load_pages(_BS)
    unit = {"unit_id": _BS, "leaves": [], "pages": pages, "type": "single"}
    ext_out, meta = _finalize_unit(ext, {}, unit, _PDF, pages, False, {}, False, "")

    cbi = meta["validation"]["column_band_issues"]
    check("meta carries the 2 col-shift detections",
          len([s for s in cbi if "col-shift:" in s]) == 2, str(cbi))
    check("meta carries the 2 col-repair records",
          len([s for s in cbi if "col-repair:" in s and "slots [" in s]) == 2, str(cbi))
    check("the Extraction handed downstream is the repaired one",
          _vals(ext_out, "Constant-currency change", 0) == ["", "", "6", "", "2"],
          str(_vals(ext_out, "Constant-currency change", 0)))


if __name__ == "__main__":
    repair_test()
    detection_still_reports_test()
    no_false_repair_test()
    guard1_uncalibrated_test()
    guard2_value_mismatch_test()
    guard2_extra_value_test()
    guard3_ambiguous_band_test()
    guard4_cell_count_test()
    auditability_test()
    meta_wiring_test()
    print(f"\n{'ALL PASS' if _FAILS == 0 else str(_FAILS) + ' FAILED'}")
    raise SystemExit(1 if _FAILS else 0)
