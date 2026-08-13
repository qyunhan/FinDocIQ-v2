"""Dropped-page rescue — a chunk that answers for only some of its pages.

The defect this pins: DBS's 2Q26 performance summary prints its overview TWICE —
half-year basis on pages 4-6, quarter basis on pages 7-8. `overview_p4-8` chunked
to [4,5] [6,7] [8]. The [6,7] call returned ONE table (page 6's per-share block)
and dropped page 7's 'Selected income statement items ($m)' entirely. Nothing
downstream noticed, so the whole quarter basis was missing from the database:
632 cells span 1H against 10 spanning 2Q, and every page-7 figure was absent.

The table COUNT cannot detect this — one table legitimately spans a chunk, which
is why spanning units exist at all. The rule is per page, and deterministic.

Run: PYTHONPATH=findociq/pipeline python3 findociq/pipeline/stage2_load/test_dropped_page_rescue.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stage1_extract.chunk.extract import (  # noqa: E402
    _MIN_TOKENS_FOR_A_TABLE_PAGE, _numeric_tokens, pages_with_no_output,
)
from stage1_extract.chunk.schema import GCell, GRow, GTable  # noqa: E402

_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    if cond:
        print(f"  [PASS] {name}")
    else:
        _FAILS += 1
        print(f"  [FAIL] {name}  -- {detail}")


def _table(title: str, *values: str) -> GTable:
    return GTable(title=title, columns=[], rows=[
        GRow(row_type="data", label="x", level=1,
             values=[GCell(value=v) for v in values])])


def main() -> int:
    print("Dropped-page rescue — pages_with_no_output")

    # --- the real defect -----------------------------------------------------
    # page 6 = per-share block (its numbers ARE in the returned table)
    # page 7 = quarter-basis income statement (none of its numbers returned)
    p6 = "Basic 4.27 4.04 3.71 Diluted 4.25 4.04 3.69 Net book value 24.69 23.82 24.29"
    p7 = ("Commercial book total income 5,624 5,314 6 5,559 1 "
          "Net interest income 3,483 3,625 3,475 Total income 6,093 5,732 5,948 "
          "Expenses 2,347 2,270 2,302 Profit before allowances 3,746 3,462 3,646")
    returned = [_table("DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES",
                       "4.27", "4.04", "3.71", "4.25", "3.69", "24.69", "23.82", "24.29")]
    check("page 7 flagged, page 6 not",
          pages_with_no_output({6: p6, 7: p7}, returned) == [7],
          str(pages_with_no_output({6: p6, 7: p7}, returned)))

    # --- a table SPANNING both pages must NOT be flagged ----------------------
    # One table, two pages, fewer tables than pages — the case a count-based
    # rule would wrongly rescue. Both pages' numbers are present, so neither is.
    span = [_table("Average Balance Sheet", "1,234", "5,678", "9,012", "3,456")]
    check("spanning table: neither page flagged",
          pages_with_no_output({10: "assets 1,234 5,678", 11: "equity 9,012 3,456"},
                               span) == [],
          str(pages_with_no_output({10: "assets 1,234 5,678", 11: "equity 9,012 3,456"}, span)))

    # --- a prose page owes nothing -------------------------------------------
    notes = ("Notes: 1 Refers to Corporate Social Responsibility commitment. "
             "2 Excludes impact arising from Provision for CSR. 3 Computed on an "
             "annualised basis.")
    check("prose/notes page is never flagged",
          pages_with_no_output({5: notes}, [_table("T", "99")]) == [],
          str(_numeric_tokens(notes)))
    check("...because it is under the density floor",
          len(_numeric_tokens(notes)) < _MIN_TOKENS_FOR_A_TABLE_PAGE,
          f"{len(_numeric_tokens(notes))} tokens vs floor {_MIN_TOKENS_FOR_A_TABLE_PAGE}")

    # --- single digits must not count as evidence ----------------------------
    check("footnote markers/ordinals are not numeric evidence",
          _numeric_tokens("see 1 2 3 4 5 6 7 8 9") == set(),
          str(_numeric_tokens("see 1 2 3 4 5 6 7 8 9")))
    check("thousands separators and decimals are kept verbatim",
          _numeric_tokens("5,624 and 4.27") == {"5,624", "4.27"},
          str(_numeric_tokens("5,624 and 4.27")))

    # --- empty / degenerate inputs -------------------------------------------
    check("no tables at all: a dense page is flagged",
          pages_with_no_output({1: p7}, []) == [1], "")
    check("no pages: nothing flagged", pages_with_no_output({}, returned) == [], "")
    check("unreadable page text ('') is treated as prose, never rescued",
          pages_with_no_output({1: ""}, []) == [], "")

    print("\nALL PASS" if not _FAILS else f"\n{_FAILS} FAILED")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
