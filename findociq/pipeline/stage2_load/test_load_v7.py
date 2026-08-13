"""Plain check() script for stage2_load.load_v7 (NO pytest). Exit 0 all-pass / 1 any-fail.

Run:  python findociq/pipeline/stage2_load/test_load_v7.py
Unit-tests the pure mappers, then loads the real DBS 2Q25 'DEBTS ISSUED' fixture
into a working COPY of the schema_v7 spike DB and asserts the mapping end-to-end.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path
from stage2_load.load_v7 import (  # noqa: E402
    _apply_document_default_unit, _clean_label, col_lineage,
    ind_lookup, ind_norm, is_date_text, is_period_text, lineage_key, load_units,
    parse_iso_date, parse_period_expr, parse_period_span, parse_row_label_unit,
    parse_unit, parse_value, resolve_axis_labels, resolve_cell_unit, row_lineage,
    row_parents_by_position, seg_lookup, seg_norm, slug, unit_from_value,
    verified_sums_to,
)
from stage1_extract.chunk.schema import Extraction, GCell, GColumn, GRow, GTable  # noqa: E402
from stage1_extract.chunk.transforms import drop_echo_groups  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_PARSED = (_REPO / "findociq/outputs/pillar3/dbs_2Q25/audit/"
           "DBS_2Q25_performance_summary/debts_issued_p25/parsed.json")
_DOC_ID = "DBS_2Q25_performance_summary"
_DOC_PERIOD = "2025-06-30"
_SCHEMA_V7 = _REPO / "findociq/schema/schema_v7.sql"
_TOC = _REPO / "findociq/data/derived/toc/DBS_2Q25_performance_summary_toc.json"

# The two integration blocks used to copy a spike DB from
# experiments/2026-07-12_fs_gemini_toc_spike/contract_v2/outputs/fs_eval_v7.db.
# That file was never tracked (findociq/.gitignore: `db/*.db`) and did not survive
# the machine it was built on, so both blocks failed on "original spike DB exists".
# It was only ever schema_v7 + this doc's document/section rows, and both inputs
# ARE tracked (schema/schema_v7.sql, data/derived/toc/<doc>_toc.json) — so build it
# instead of depending on a lost artifact. Deterministic and self-contained.
_BASE_DB: Path | None = None

_FAILS = 0


def fixture_db() -> Path | None:
    """A fresh schema_v7 DB carrying ONLY this doc's document + section rows (no
    table_t) — built once per run, into a temp dir. None if the build fails."""
    global _BASE_DB
    if _BASE_DB is not None:
        return _BASE_DB if str(_BASE_DB) else None
    import subprocess
    tmp = Path(tempfile.mkdtemp(prefix="load_v7_fixture_"))
    db = tmp / "fs_eval_v7.db"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA_V7.read_text())
    con.commit()
    con.close()
    cp = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "stage1_extract" / "toc" / "toc_to_db.py"),
         "--toc", str(_TOC), "--db", str(db), "--doc-period", _DOC_PERIOD],
        cwd=str(_REPO), capture_output=True, text=True)
    if cp.returncode != 0:
        print(f"  [fixture] toc_to_db failed: {cp.stdout}\n{cp.stderr}")
        _BASE_DB = Path("")
        return None
    _BASE_DB = db
    return db


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILS += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


# ===========================================================================
# 1) PURE MAPPER UNIT TESTS
# ===========================================================================
def unit_tests() -> None:
    print("Unit tests — pure mappers")
    check("value_num '(1,234)' -> -1234", parse_value("(1,234)") == ("reported", -1234.0),
          str(parse_value("(1,234)")))
    check("value_num '-' -> null/None", parse_value("-") == ("null", None), str(parse_value("-")))
    check("value_num '' -> empty/None", parse_value("") == ("empty", None), str(parse_value("")))
    check("value_num '#' -> suppressed/None", parse_value("#") == ("suppressed", None))
    check("value_num '0' -> zero/0.0", parse_value("0") == ("zero", 0.0))
    check("value_num '1,260' -> 1260.0", parse_value("1,260") == ("reported", 1260.0))
    check("value_num 'AAA to BBB+' -> reported/None (text)",
          parse_value("AAA to BBB+") == ("reported", None))

    check("iso '30 Jun 2025' -> 2025-06-30", parse_iso_date("30 Jun 2025") == "2025-06-30",
          parse_iso_date("30 Jun 2025"))
    check("iso '31 December 2024' -> 2024-12-31", parse_iso_date("31 December 2024") == "2024-12-31")

    check("is_date_text '30 Jun 2025' True", is_date_text("30 Jun 2025") is True)
    check("is_date_text 'As at 30 Jun 2025' True", is_date_text("As at 30 Jun 2025") is True)
    check("is_date_text 'Net loans' False", is_date_text("Net loans") is False)
    check("is_date_text 'Amount' False", is_date_text("Amount") is False)

    check("slug('DEBTS ISSUED') -> debts_issued", slug("DEBTS ISSUED") == "debts_issued", slug("DEBTS ISSUED"))

    # --- GENERAL PERIOD-EXPRESSION grammar (parse_period_expr) ----------------
    # existing DD-Month-YYYY dates delegate to parse_iso_date
    check("period '30 Jun 2025' -> 2025-06-30", parse_period_expr("30 Jun 2025") == "2025-06-30",
          str(parse_period_expr("30 Jun 2025")))
    check("period '31 December 2024' -> 2024-12-31", parse_period_expr("31 December 2024") == "2024-12-31")
    # halves -> H1 30 Jun, H2 31 Dec (calendar-fiscal)
    check("period '1st Half 2025' -> 2025-06-30", parse_period_expr("1st Half 2025") == "2025-06-30",
          str(parse_period_expr("1st Half 2025")))
    check("period 'First Half 2025' -> 2025-06-30", parse_period_expr("First Half 2025") == "2025-06-30")
    check("period '1H25' -> 2025-06-30", parse_period_expr("1H25") == "2025-06-30", str(parse_period_expr("1H25")))
    check("period '1H 2025' -> 2025-06-30", parse_period_expr("1H 2025") == "2025-06-30")
    check("period '2nd Half 2024' -> 2024-12-31", parse_period_expr("2nd Half 2024") == "2024-12-31",
          str(parse_period_expr("2nd Half 2024")))
    check("period '2H24' -> 2024-12-31", parse_period_expr("2H24") == "2024-12-31")
    # quarters -> last day of month 3*Q
    check("period '2Q25' -> 2025-06-30", parse_period_expr("2Q25") == "2025-06-30", str(parse_period_expr("2Q25")))
    check("period '2Q 2025' -> 2025-06-30", parse_period_expr("2Q 2025") == "2025-06-30")
    check("period 'Second Quarter 2025' -> 2025-06-30", parse_period_expr("Second Quarter 2025") == "2025-06-30")
    check("period '1Q25' -> 2025-03-31", parse_period_expr("1Q25") == "2025-03-31", str(parse_period_expr("1Q25")))
    check("period '4Q24' -> 2024-12-31", parse_period_expr("4Q24") == "2024-12-31")
    check("period '3Q25' -> 2025-09-30", parse_period_expr("3Q25") == "2025-09-30")
    # years / full-year -> 31 Dec
    check("period 'FY2024' -> 2024-12-31", parse_period_expr("FY2024") == "2024-12-31")
    check("period 'FY24' -> 2024-12-31", parse_period_expr("FY24") == "2024-12-31")
    check("period 'Full Year 2024' -> 2024-12-31", parse_period_expr("Full Year 2024") == "2024-12-31")
    check("period 'Year ended 31 December 2024' -> 2024-12-31",
          parse_period_expr("Year ended 31 December 2024") == "2024-12-31")
    check("period 'Half year ended 30 June 2025' -> 2025-06-30",
          parse_period_expr("Half year ended 30 June 2025") == "2025-06-30")
    check("period 'quarter ended 31 March 2025' -> 2025-03-31",
          parse_period_expr("quarter ended 31 March 2025") == "2025-03-31")
    # BARE-YEAR GUARD: a 4-digit year alone is ambiguous (start vs end) -> None
    check("period '2024' -> None (bare year guard)", parse_period_expr("2024") is None,
          str(parse_period_expr("2024")))
    check("period 'Net loans' -> None", parse_period_expr("Net loans") is None)
    check("period 'Average balance ($m)' -> None", parse_period_expr("Average balance ($m)") is None)
    check("period '' -> None", parse_period_expr("") is None)
    # LOOSE title extraction: a period trailing a descriptive title still resolves
    check("period 'Selected income statement items 1st Half 2025' -> 2025-06-30 (loose)",
          parse_period_expr("Selected income statement items 1st Half 2025") == "2025-06-30",
          str(parse_period_expr("Selected income statement items 1st Half 2025")))

    # --- QTR/HALF/FULLYEAR grammar extension (digit ordinals, 'qtr'/'q'/'h'
    # abbreviations, 2-digit years, 'Year 2025' without 'full') — the col-header
    # forms that were missing and left col_period NULL on the *_trading_update
    # docs (bare-year fallback stopped only by the residual guard).
    check("parse_period_span('1st Qtr 2026') -> (2026-03-31, 1Q, ...)",
          parse_period_span("1st Qtr 2026") == ("2026-03-31", "1Q", "2026-01-01"),
          str(parse_period_span("1st Qtr 2026")))
    check("parse_period_span('4th Qtr 2025') -> (2025-12-31, 4Q, ...)",
          parse_period_span("4th Qtr 2025") == ("2025-12-31", "4Q", "2025-10-01"),
          str(parse_period_span("4th Qtr 2025")))
    check("parse_period_span('1st Qtr 25') (2-digit year) -> (2025-03-31, 1Q, ...)",
          parse_period_span("1st Qtr 25") == ("2025-03-31", "1Q", "2025-01-01"),
          str(parse_period_span("1st Qtr 25")))
    check("parse_period_span('Year 2025') -> (2025-12-31, FY, ...)",
          parse_period_span("Year 2025") == ("2025-12-31", "FY", "2025-01-01"),
          str(parse_period_span("Year 2025")))
    check("parse_period_span('Full Year 2025') still -> (2025-12-31, FY, ...)",
          parse_period_span("Full Year 2025") == ("2025-12-31", "FY", "2025-01-01"),
          str(parse_period_span("Full Year 2025")))
    check("parse_period_span('2nd Half 2025') -> (2025-12-31, 2H, ...)",
          parse_period_span("2nd Half 2025") == ("2025-12-31", "2H", "2025-07-01"),
          str(parse_period_span("2nd Half 2025")))
    check("parse_period_span('1st Half 2025') -> (2025-06-30, 1H, ...)",
          parse_period_span("1st Half 2025") == ("2025-06-30", "1H", "2025-01-01"),
          str(parse_period_span("1st Half 2025")))
    check("parse_period_span('% chg') -> None (no period)",
          parse_period_span("% chg") is None, str(parse_period_span("% chg")))
    # bare-year fallback UNCHANGED (title/prose context, column=False, keeps the
    # bare-year guard -> None; this behaviour predates and must survive the change)
    check("parse_period_span('2025') (column=False) -> None (bare-year guard unchanged)",
          parse_period_span("2025") is None, str(parse_period_span("2025")))
    check("is_period_text('1st Qtr 2026') True (after grammar extension)",
          is_period_text("1st Qtr 2026") is True)

    # --- is_period_text (residual-guarded axis predicate) ---------------------
    check("is_period_text '1st Half 2025' True", is_period_text("1st Half 2025") is True)
    check("is_period_text '1H25' True", is_period_text("1H25") is True)
    check("is_period_text 'As at 30 Jun 2025' True", is_period_text("As at 30 Jun 2025") is True)
    check("is_period_text 'Average balance ($m)' False", is_period_text("Average balance ($m)") is False)
    check("is_period_text 'Singapore' False", is_period_text("Singapore") is False)
    # a comparison/change banner is NOT a single period axis -> not collapsed
    check("is_period_text '1st Half 2025 vs 1st Half 2024' False (change banner)",
          is_period_text("1st Half 2025 vs 1st Half 2024") is False,
          str(is_period_text("1st Half 2025 vs 1st Half 2024")))
    # a descriptive title carrying an incidental period is NOT the axis itself
    check("is_period_text 'Selected income statement items 1st Half 2025' False (incidental)",
          is_period_text("Selected income statement items 1st Half 2025") is False)

    # --- col_lineage under a PERIOD-EXPRESSION group banner -------------------
    # the banner is period-axis -> excluded; leaves converge to the leaf label
    check("col_lineage(period group '1st Half 2025' + 'Average balance ($m)') -> ['Average balance ($m)']",
          col_lineage(GColumn(group="1st Half 2025", leaf="Average balance ($m)")) == ["Average balance ($m)"],
          str(col_lineage(GColumn(group="1st Half 2025", leaf="Average balance ($m)"))))
    check("col_lineage converges across period groups (2nd Half 2024 banner)",
          col_lineage(GColumn(group="2nd Half 2024", leaf="Average balance ($m)")) == ["Average balance ($m)"])
    # a NON-period group ('vs' change banner) stays IN the lineage
    check("col_lineage(change group kept) -> [group, leaf]",
          col_lineage(GColumn(group="1st Half 2025 vs 1st Half 2024", leaf="Volume"))
          == ["1st Half 2025 vs 1st Half 2024", "Volume"])

    # --- unit token grammar (FEATURE A) --------------------------------------
    check("parse_unit '($m)' -> S$m", parse_unit("($m)") == "S$m", parse_unit("($m)"))
    check("parse_unit 'In $ millions' -> S$m", parse_unit("In $ millions") == "S$m")
    check("parse_unit 'In $m' -> S$m", parse_unit("In $m") == "S$m")
    check("parse_unit 'S$m' -> S$m", parse_unit("S$m") == "S$m")
    check("parse_unit '(%)' -> %", parse_unit("Key financial ratios (%)2,3") == "%")
    check("parse_unit '% chg' -> %", parse_unit("% chg") == "%")
    check("parse_unit '% change' -> %", parse_unit("% change") == "%")
    check("parse_unit 'Average rate (%)' -> %", parse_unit("Average rate (%)") == "%")
    check("parse_unit \"('000)\" -> '000", parse_unit("('000)") == "'000", parse_unit("('000)"))
    check("parse_unit 'thousands' -> '000", parse_unit("in thousands") == "'000")
    check("parse_unit 'Number of shares (million)' -> count",
          parse_unit("Number of shares (million)") == "count", parse_unit("Number of shares (million)"))
    check("parse_unit 'no. of' -> count", parse_unit("no. of units") == "count")
    check("parse_unit 'per share' -> per_share", parse_unit("Earnings per share") == "per_share")
    check("parse_unit 'cents' -> per_share", parse_unit("(cents)") == "per_share")
    check("parse_unit 'times' -> x", parse_unit("Interest cover (times)") == "x")
    check("parse_unit '(x)' -> x", parse_unit("Leverage (x)") == "x")
    check("parse_unit 'Net loans' -> None", parse_unit("Net loans") is None, str(parse_unit("Net loans")))
    check("parse_unit '30 Jun 2025' -> None", parse_unit("30 Jun 2025") is None)
    check("parse_unit '' -> None", parse_unit("") is None)
    # first-match ordering: a '%' anywhere wins over a co-present $
    check("parse_unit '% of $m' -> % (first match)", parse_unit("% of $m base") == "%")

    # --- CELL VALUE TOKEN (top of the unit chain) ----------------------------
    check("unit_from_value '137%' -> %", unit_from_value("137%") == "%", str(unit_from_value("137%")))
    check("unit_from_value '9.2 %' -> %", unit_from_value("9.2 %") == "%")
    check("unit_from_value '1.2x' -> x", unit_from_value("1.2x") == "x", str(unit_from_value("1.2x")))
    check("unit_from_value '3 times' -> x", unit_from_value("3 times") == "x")
    check("unit_from_value '3X' -> x (case-insens)", unit_from_value("3X") == "x")
    check("unit_from_value '1,234' -> None", unit_from_value("1,234") is None, str(unit_from_value("1,234")))
    check("unit_from_value '(1,505)' -> None", unit_from_value("(1,505)") is None)
    check("unit_from_value 'tax' -> None", unit_from_value("tax") is None)
    check("unit_from_value '' -> None", unit_from_value("") is None)
    check("unit_from_value None -> None", unit_from_value(None) is None)

    # --- resolve_cell_unit precedence order ----------------------------------
    #   (1) cell value token beats a col unit ('137%' in an S$m column -> '%')
    check("resolve: cell token '137%' beats col S$m",
          resolve_cell_unit("137%", None, "S$m", "S$m") == "%",
          resolve_cell_unit("137%", None, "S$m", "S$m"))
    #   (2) row '%' beats a non-% col
    check("resolve: row '%' beats col S$m",
          resolve_cell_unit("12", "%", "S$m", "S$m") == "%")
    #   (3) col beats a non-% row and the table default
    check("resolve: col beats row + table",
          resolve_cell_unit("12", "count", "%", "S$m") == "%")
    #   (4) row (non-%) when no col
    check("resolve: row wins when no col",
          resolve_cell_unit("12", "count", None, "S$m") == "count")
    #   (5) table default when neither row nor col
    check("resolve: table default last",
          resolve_cell_unit("12", None, None, "S$m") == "S$m")
    #   (6) nothing resolvable by 1-5 -> None (doc default handled downstream)
    check("resolve: all None -> None",
          resolve_cell_unit("12", None, None, None) is None)

    # --- ROW-LABEL unit + COUPON-IN-NAME guard (parse_row_label_unit) ---------
    # a coupon/rate printed IN THE ROW NAME is NOT the row unit (cells are S$m).
    check("row-label coupon '3.58% non-cumulative ... perpetual capital securities' -> None",
          parse_row_label_unit(
              "3.58% non-cumulative non-convertible perpetual capital securities "
              "issued on 17 July 2019") is None,
          str(parse_row_label_unit(
              "3.58% non-cumulative non-convertible perpetual capital securities")))
    check("row-label coupon '3.0% perpetual capital securities' -> None",
          parse_row_label_unit("3.0% perpetual capital securities") is None,
          str(parse_row_label_unit("3.0% perpetual capital securities")))
    # a standalone / terminal '%' marker IS still a real row unit.
    check("row-label 'Net interest margin (%)' -> % (real unit marker)",
          parse_row_label_unit("Net interest margin (%)") == "%",
          str(parse_row_label_unit("Net interest margin (%)")))
    check("row-label \"Capital Adequacy Ratio ('CAR') (%)\" -> %",
          parse_row_label_unit("Capital Adequacy Ratio ('CAR') (%)") == "%",
          str(parse_row_label_unit("Capital Adequacy Ratio ('CAR') (%)")))
    # non-'%' tokens unaffected; a legit unit elsewhere still wins past a coupon.
    check("row-label 'Total allowances/ NPA' -> None (no % in label)",
          parse_row_label_unit("Total allowances/ NPA") is None)
    check("row-label 'Interest cover (times)' -> x", parse_row_label_unit("Interest cover (times)") == "x")

    # footnote-marker strip (superscript + parenthesised) — schema §2b comment
    check("_clean_label superscript strip", _clean_label("Subordinated term debts¹") == "Subordinated term debts",
          _clean_label("Subordinated term debts¹"))
    check("_clean_label superscript ² strip", _clean_label("Covered bonds and other secured notes²")
          == "Covered bonds and other secured notes")
    check("_clean_label '(1)' strip", _clean_label("Total NSFR HQLA (1)") == "Total NSFR HQLA")

    # lineage key normalisation: footnote strip + lowercase + ' > ' join
    check("lineage_key normalise", lineage_key(["Total", "Due within 1 year"]) == "total > due within 1 year",
          lineage_key(["Total", "Due within 1 year"]))

    # pure-date column lineage -> canonical 'value'
    check("col_lineage(date leaf) -> ['value']", col_lineage(GColumn(group=None, leaf="30 Jun 2025")) == ["value"],
          str(col_lineage(GColumn(group=None, leaf="30 Jun 2025"))))
    check("col_lineage(text leaf) -> [leaf]",
          col_lineage(GColumn(group=None, leaf="Net loans¹")) == ["Net loans"])

    # row-parent position chain: level-1 'b' after level-0 'T' -> parent = T
    rows = [GRow(level=1, label="a"), GRow(level=0, label="T"), GRow(level=1, label="b")]
    parents = row_parents_by_position(rows)
    check("row_parents_by_position -> [None,None,1]", parents == [None, None, 1], str(parents))
    check("row_lineage chain 'T > b'", row_lineage(rows, parents, 2) == ["T", "b"], str(row_lineage(rows, parents, 2)))
    check("row_lineage top row 'a'", row_lineage(rows, parents, 0) == ["a"])

    # --- terminal-total/note parent rule -------------------------------------
    # CONTRACT CHANGED 2026-08-05 (commit 01151d1): the total-skip is no longer
    # blanket. `_heads_a_block` discriminates a TERMINAL total (one that
    # AGGREGATES rows — it appears among sums_to's values) from a total-shaped
    # SECTION HEADER (aggregates nothing, followed by deeper rows). Only the
    # terminal one is skipped. These two cases therefore need `sums_to`, which
    # the loader always computes before the parent walk (load_v7.py:1563-1566)
    # — without it the discriminator has no aggregation evidence and treats the
    # total as a header. Same fixtures, same expectations, now supplied the
    # evidence the production caller supplies. See test_geometry_load.py Part B
    # and test_printed_parents.py for the header-total side of the rule.
    #
    # a level-1 data row after a level-0 TERMINAL total must NOT parent to it
    prows = [GRow(row_type="data", level=1, label="A"),
             GRow(row_type="total", level=0, label="Total"),
             GRow(row_type="data", level=1, label="Due within 1 year")]
    pp = row_parents_by_position(prows, sums_to={1: 2})   # 'A' sums to 'Total'
    check("data row skips preceding total -> parent None", pp == [None, None, None], str(pp))
    # a subtotal is passed over to the next eligible ancestor (a section header)
    srows = [GRow(row_type="section_header", level=0, label="Sec"),
             GRow(row_type="data", level=1, label="a"),
             GRow(row_type="total", level=0, label="Subtotal"),
             GRow(row_type="data", level=1, label="b")]
    sp = row_parents_by_position(srows, sums_to={2: 3})   # 'a' sums to 'Subtotal'
    check("data row passes over subtotal to section header", sp == [None, 0, None, 0], str(sp))
    # a note under 'Notes:' still nests to the note (display-harmless), preserved
    nrows = [GRow(row_type="note", level=0, label="Notes:"),
             GRow(row_type="note", level=1, label="Unsecured")]
    npar = row_parents_by_position(nrows)
    check("note nests to preceding 'Notes:' note", npar == [None, 0], str(npar))

    # --- verified_sums_to block rule -----------------------------------------
    def _row(rt, lvl, lbl, *vals):
        return GRow(row_type=rt, level=lvl, label=lbl,
                    values=[GCell(value=v) for v in vals])

    # basic: two level-1 members sum to a level-0 total across 2 columns
    b = [_row("data", 1, "A", "100", "10"), _row("data", 1, "B", "200", "20"),
         _row("total", 0, "Total", "300", "30")]
    sm, sg, w = verified_sums_to(b, 2)
    check("sums_to basic block: members 1,2 -> total 3", sm == {1: 3, 2: 3}, str(sm))
    check("sums_to basic block: all signs +1", sg == {1: 1, 2: 1}, str(sg))
    check("sums_to basic block: no warning", w == [], str(w))

    # 'of which' deeper row EXCLUDED (shallowest level only, no double-count)
    d = [_row("data", 1, "A", "100"), _row("data", 2, "of which X", "30"),
         _row("data", 1, "B", "200"), _row("total", 0, "Total", "300")]
    sm2, sg2, w2 = verified_sums_to(d, 1)
    check("sums_to excludes deeper 'of which' row", sm2 == {1: 4, 3: 4}, str(sm2))
    check("sums_to: deeper row 2 not a member", 2 not in sm2, str(sm2))
    check("sums_to 'of which' block verifies", w2 == [], str(w2))

    # printed-rounding tolerance: sum 301 vs total 300 with 3 members -> tol 3.0 OK
    rnd = [_row("data", 1, "A", "100"), _row("data", 1, "B", "101"),
           _row("data", 1, "C", "100"), _row("total", 0, "Total", "300")]
    smr, sgr, wr = verified_sums_to(rnd, 1)
    check("sums_to within rounding tolerance passes", smr == {1: 4, 2: 4, 3: 4}, str(smr))
    check("rounding block: all signs +1", sgr == {1: 1, 2: 1, 3: 1}, str(sgr))

    # failing sum -> NULL for the whole block + a warning (never a failure)
    f = [_row("data", 1, "A", "100"), _row("data", 1, "B", "200"),
         _row("total", 0, "Total", "999")]
    smf, sgf, wf = verified_sums_to(f, 1)
    check("sums_to failing block -> empty map", smf == {}, str(smf))
    check("sums_to failing block -> empty sign map", sgf == {}, str(sgf))
    check("sums_to failing block -> one warning", len(wf) == 1, str(wf))

    # --- FEATURE B: SIGN-AWARE subtraction ------------------------------------
    # Net profit = PBT - tax (single column). Fast path (all +1) FAILS; the sign
    # search finds the unique {+1,-1} assignment.
    sub = [_row("data", 1, "Profit before tax", "1000"),
           _row("data", 1, "Tax", "200"),
           _row("total", 0, "Net profit", "800")]
    sms, sgs, ws = verified_sums_to(sub, 1)
    check("subtraction: members 1,2 -> total 3", sms == {1: 3, 2: 3}, str(sms))
    check("subtraction: PBT +1, tax -1", sgs == {1: 1, 2: -1}, str(sgs))
    check("subtraction: no warning", ws == [], str(ws))

    # chained-subtotal CARRY-IN: grand total = prior subtotal + two new lines.
    # The fast path over the data lines alone (C+D=250 != 900) fails, so the
    # previous total (650) is carried in and the block verifies all +1.
    carry = [_row("data", 1, "A", "300"), _row("data", 1, "B", "350"),
             _row("total", 0, "Subtotal", "650"),
             _row("data", 1, "C", "100"), _row("data", 1, "D", "150"),
             _row("total", 0, "Grand total", "900")]
    smc, sgc, wc = verified_sums_to(carry, 1)
    check("carry-in: subtotal(3) is a member of grand total(6)", smc.get(3) == 6, str(smc))
    check("carry-in: new lines 4,5 -> grand total 6", smc.get(4) == 6 and smc.get(5) == 6, str(smc))
    check("carry-in: A,B -> subtotal 3 (unchanged additive block)",
          smc.get(1) == 3 and smc.get(2) == 3, str(smc))
    check("carry-in: subtotal member sign +1", sgc.get(3) == 1, str(sgc))
    check("carry-in: no warning", wc == [], str(wc))

    # AMBIGUOUS: two equal-magnitude members can be +/- interchangeably to hit 0
    # -> more than one sign solution -> whole block NULL + 'ambiguous' warning.
    amb = [_row("data", 1, "A", "100"), _row("data", 1, "B", "100"),
           _row("total", 0, "Total", "0")]
    sma, sga, wa = verified_sums_to(amb, 1)
    check("ambiguous block -> empty map", sma == {}, str(sma))
    check("ambiguous block -> empty sign map", sga == {}, str(sga))
    check("ambiguous block -> one 'ambiguous' warning",
          len(wa) == 1 and "ambiguous" in wa[0], str(wa))

    # --- %-COLUMN GATING (follow-up, 2026-07-13) ------------------------------
    # A block that fails ONLY because a non-additive '%' column doesn't reconcile
    # (e.g. a printed '% chg' column) now VERIFIES once that column is excluded.
    pct = [_row("data", 1, "A", "100", "17"), _row("data", 1, "B", "200", "9"),
           _row("total", 0, "Total", "300", "999")]     # col1 '%' bogus, would fail as-is
    smp, sgp, wp = verified_sums_to(pct, 2, col_units=[None, "%"])
    check("%-gated block verifies on col0, col1('%') excluded",
          smp == {1: 3, 2: 3}, str(smp))
    check("%-gated block: signs +1", sgp == {1: 1, 2: 1}, str(sgp))
    check("%-gated block: no warning", wp == [], str(wp))
    # without gating (no col_units passed) the same block fails on col1
    smp0, sgp0, wp0 = verified_sums_to(pct, 2)
    check("without gating the same block is NOT verified", smp0 == {}, str(smp0))

    # All-%-columns block: not arithmetically verifiable at all -> NULL + warning
    allpct = [_row("data", 1, "A", "17"), _row("data", 1, "B", "9"),
              _row("total", 0, "Total", "999")]
    smq, sgq, wq = verified_sums_to(allpct, 1, col_units=["%"])
    check("all-%-columns block -> empty map", smq == {}, str(smq))
    check("all-%-columns block -> empty sign map", sgq == {}, str(sgq))
    check("all-%-columns block -> 'only %-columns' warning",
          len(wq) == 1 and "only %-columns" in wq[0], str(wq))


# ===========================================================================
# 1b) DOCUMENT-DEFAULT unit pass (step 6 of the chain) — in-memory, no fixture
# ===========================================================================
def _mini_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE table_t(doc_id TEXT, table_id TEXT, unit TEXT);"
        "CREATE TABLE cell_fact(doc_id TEXT, table_id TEXT, row_id INT, "
        "col_id INT, unit TEXT);")
    return con


def doc_default_tests() -> None:
    print("\nDocument-default unit pass (step 6)")

    # modal exists (2x 'S$m' vs 1x '%') -> 'S$m'; a table with NULL cells consumes it
    con = _mini_db()
    con.executemany("INSERT INTO table_t VALUES (?,?,?)", [
        ("D", "t_socie", "S$m"), ("D", "t_bs", "S$m"), ("D", "t_ratio", "%")])
    con.executemany("INSERT INTO cell_fact VALUES (?,?,?,?,?)", [
        ("D", "t_socie", 1, 1, None),   # unresolved by 1-5 -> should take doc default
        ("D", "t_socie", 2, 1, None),
        ("D", "t_bs", 1, 1, "S$m"),     # already resolved -> untouched, no warning
        ("D", "t_ratio", 1, 1, "%")])
    w: list[str] = []
    _apply_document_default_unit(con.cursor(), "D", w)
    filled = con.execute(
        "SELECT unit FROM cell_fact WHERE table_id='t_socie'").fetchall()
    check("doc default fills NULL cells with modal 'S$m'",
          filled == [("S$m",), ("S$m",)], str(filled))
    check("doc default: no cell left NULL", con.execute(
          "SELECT COUNT(*) FROM cell_fact WHERE unit IS NULL").fetchone()[0] == 0)
    check("doc default: ONE warning for the consuming table (t_socie)",
          w == ["t_socie: unit from document default 'S$m' (modal across 2/3 tables)"],
          str(w))
    con.close()

    # strict tie (2x 'S$m', 2x '%') -> NO default -> NULL cells stay NULL + warning
    con = _mini_db()
    con.executemany("INSERT INTO table_t VALUES (?,?,?)", [
        ("D", "a", "S$m"), ("D", "b", "S$m"), ("D", "c", "%"), ("D", "e", "%")])
    con.executemany("INSERT INTO cell_fact VALUES (?,?,?,?,?)", [
        ("D", "a", 1, 1, None), ("D", "a", 2, 1, None), ("D", "b", 1, 1, "S$m")])
    w = []
    _apply_document_default_unit(con.cursor(), "D", w)
    check("tie -> no doc default applied (cells stay NULL)", con.execute(
          "SELECT COUNT(*) FROM cell_fact WHERE unit IS NULL").fetchone()[0] == 2)
    check("tie -> unresolvable warning names the table + count",
          w == ["a: 2 cells with unresolvable unit"], str(w))
    con.close()


# ===========================================================================
# 1b) COUPON-IN-NAME UNIT INTEGRATION — synthetic load into a fresh schema_v7 DB.
#     The UOB share_capital perpetual rows print a coupon '%' IN THE ROW NAME but
#     hold S$m capital amounts; a coverage-ratio row prints '137%'/'236%' as the
#     CELL value. After the guard: perpetual cells resolve S$m (col/table chain),
#     value-token '%' cells stay '%'.
# ===========================================================================
def _cells(*vals: str) -> list[GCell]:
    return [GCell(value=v) for v in vals]


def coupon_unit_integration_tests() -> None:
    print("\nCoupon-in-name unit integration (synthetic schema_v7 load)")

    # share_capital: '$m' label_header -> table_unit S$m. Perpetual rows carry a
    # coupon '%' in the NAME; their cells are S$m amounts (749,150,599,400,850).
    # 'Total allowances/ NPA' row prints value-token '137%' -> that cell stays '%'.
    share_cap = GTable(
        title="Share capital and other capital",
        label_header="$m",
        columns=[GColumn(group=None, leaf="2025")],
        rows=[
            GRow(row_id="1", row_type="data", level=1,
                 label="3.58% non-cumulative non-convertible perpetual capital "
                       "securities issued on 17 July 2019", values=_cells("749")),
            GRow(row_id="2", row_type="data", level=1,
                 label="2.25% non-cumulative non-convertible perpetual capital "
                       "securities issued on 15 January 2021", values=_cells("150")),
            GRow(row_id="3", row_type="data", level=1,
                 label="2.55% non-cumulative non-convertible perpetual capital "
                       "securities issued on 22 June 2021", values=_cells("599")),
            GRow(row_id="4", row_type="data", level=1,
                 label="4.25% non-cumulative non-convertible perpetual capital "
                       "securities issued on 4 July 2022", values=_cells("400")),
            GRow(row_id="5", row_type="data", level=1,
                 label="5.25% non-cumulative non-convertible perpetual capital "
                       "securities issued on 19 January 2023", values=_cells("850")),
            GRow(row_id="6", row_type="data", level=1,
                 label="Total allowances/ NPA", values=_cells("137%")),
        ],
    )

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "coupon_v7.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA_V7.read_text())
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('CPN','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES ('CPN','s1','1','S',1,NULL,1)")
        con.commit()
        con.close()

        parsed = Path(td) / "parsed.json"
        parsed.write_text(json.dumps(Extraction(tables=[share_cap]).model_dump()))
        load_units(str(db), "CPN",
                   [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])

        con = sqlite3.connect(db)
        cur = con.cursor()
        # row_dim.unit on the perpetual rows must NOT be '%' (coupon-in-name guard)
        perp_units = [u for (u,) in cur.execute(
            "SELECT unit FROM row_dim WHERE doc_id='CPN' AND "
            "row_leaf_label LIKE '%perpetual capital securities%'").fetchall()]
        check("perpetual row_dim.unit never '%' (coupon-in-name dropped)",
              all(u != "%" for u in perp_units), str(perp_units))
        # the 5 perpetual cells resolve S$m via the col/table chain
        perp_cell_units = [u for (u,) in cur.execute(
            "SELECT f.unit FROM cell_fact f JOIN row_dim r ON r.doc_id=f.doc_id AND "
            "r.table_id=f.table_id AND r.row_id=f.row_id WHERE f.doc_id='CPN' AND "
            "r.row_leaf_label LIKE '%perpetual capital securities%'").fetchall()]
        check("5 perpetual cells resolve unit 'S$m'",
              perp_cell_units == ["S$m"] * 5, str(perp_cell_units))
        # the value-token '137%' cell stays '%'
        cov = cur.execute(
            "SELECT f.unit, f.value_raw FROM cell_fact f JOIN row_dim r ON "
            "r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id "
            "WHERE f.doc_id='CPN' AND r.row_leaf_label='Total allowances/ NPA'").fetchone()
        check("coverage-ratio value-token cell '137%' stays unit '%'",
              cov == ("%", "137%"), str(cov))
        con.close()


# ===========================================================================
# 2) INTEGRATION — load the real fixture into a DB copy
# ===========================================================================
def integration_tests() -> None:
    print("\nIntegration — load real fixture into DB copy")
    base = fixture_db()
    if base is None:
        check("fixture DB built (schema_v7 + toc_to_db)", False, "see [fixture] above")
        return
    _COPY_DB = base.parent / "fs_eval_v7_loaded.db"
    shutil.copy2(base, _COPY_DB)
    print(f"  built {base.name} -> {_COPY_DB.name}")

    unit = dict(section_id="debts_issued", pages=[25], parsed_path=str(_PARSED))
    summary = load_units(str(_COPY_DB), _DOC_ID, [unit])
    print(f"  load summary: {summary}")

    con = sqlite3.connect(_COPY_DB)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()

    # 1 table_t row
    trows = cur.execute("SELECT table_id, section_id, page_range, period FROM table_t").fetchall()
    check("exactly 1 table_t row", len(trows) == 1, str(trows))
    if trows:
        tid, sec, prange, tperiod = trows[0]
        check("table_t.section_id = debts_issued", sec == "debts_issued", sec)
        check("table_t.page_range = '25'", prange == "25", prange)
        check("table_t.period IS NULL (all cols carry col_period)", tperiod is None, str(tperiod))

    # 3 leaf col_dim rows, each col_period set, sharing ONE col_lineage_id ('value')
    leaves = cur.execute(
        "SELECT col_id, col_leaf_label, col_period, col_lineage_id FROM col_dim "
        "WHERE col_hierarchy = 1 ORDER BY col_id").fetchall()
    check("3 leaf col_dim rows", len(leaves) == 3, str(leaves))
    check("every leaf has col_period set", all(l[2] for l in leaves), str(leaves))
    hdr_ids = {l[3] for l in leaves}
    check("all leaves share ONE col_lineage_id", len(hdr_ids) == 1, str(hdr_ids))
    if hdr_ids:
        lk = cur.execute("SELECT lineage_key, lvl1, depth FROM col_lineage WHERE col_lineage_id = ?",
                         (hdr_ids.pop(),)).fetchone()
        check("shared col_lineage lineage = 'value' depth 1", lk == ("value", "value", 1), str(lk))
    check("leaf col_periods = 2025-06-30/2024-12-31/2024-06-30",
          [l[2] for l in leaves] == ["2025-06-30", "2024-12-31", "2024-06-30"],
          str([l[2] for l in leaves]))

    # 14 row_dim rows
    nrows = cur.execute("SELECT COUNT(*) FROM row_dim").fetchone()[0]
    check("14 row_dim rows", nrows == 14, str(nrows))

    # 30 cell_fact rows, ALL period NOT NULL
    ncells = cur.execute("SELECT COUNT(*) FROM cell_fact").fetchone()[0]
    check("30 cell_fact rows", ncells == 30, str(ncells))
    nnull = cur.execute("SELECT COUNT(*) FROM cell_fact WHERE period IS NULL").fetchone()[0]
    check("no cell_fact has NULL period", nnull == 0, str(nnull))
    check("cell periods sourced from col_period",
          cur.execute("SELECT COUNT(*) FROM cell_fact WHERE period = '2025-06-30'").fetchone()[0] == 10)

    # value_num spot-checks (expected read from parsed.json)
    def cell(label_like: str, col_id: int) -> tuple:
        return cur.execute(
            "SELECT f.value_raw, f.value_num FROM cell_fact f JOIN row_dim r "
            "ON r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id "
            "WHERE r.row_leaf_label LIKE ? AND f.col_id=?", (label_like, col_id)).fetchone()
    check("Subordinated term debts / 30Jun2025 = 1260", cell("Subordinated term debts%", 1) == ("1,260", 1260.0),
          str(cell("Subordinated term debts%", 1)))
    check("Total (first) / 30Jun2025 = 76297",
          ("76,297", 76297.0) in cur.execute(
              "SELECT value_raw,value_num FROM cell_fact WHERE col_id=1 AND value_raw='76,297'").fetchall())
    check("Other debt securities / 30Jun2024 = 18336", cell("Other debt securities%", 3) == ("18,336", 18336.0),
          str(cell("Other debt securities%", 3)))

    # cell_fact.unit materialised (FEATURE A cell chain): DEBTS ISSUED is an S$m
    # table, so every value cell resolves to 'S$m' (no NULL) via the table default.
    unit_null = cur.execute("SELECT COUNT(*) FROM cell_fact WHERE unit IS NULL").fetchone()[0]
    check("no cell_fact.unit is NULL (S$m table)", unit_null == 0, str(unit_null))
    dist = dict(cur.execute(
        "SELECT COALESCE(unit,'NULL'), COUNT(*) FROM cell_fact GROUP BY 1").fetchall())
    check("all 30 cells unit='S$m'", dist == {"S$m": 30}, str(dist))
    # v_cell.unit reads the stored column (no CASE)
    vc = cur.execute("SELECT DISTINCT unit FROM v_cell").fetchall()
    check("v_cell.unit = {'S$m'}", vc == [("S$m",)], str(vc))

    # note rows have zero cells (note rows are the last 4 by enumeration)
    note_rows = cur.execute("SELECT row_id, row_leaf_label FROM row_dim r "
                            "WHERE row_leaf_label IN ('Notes:','Unsecured') OR line_no IS NOT NULL").fetchall()
    note_ids = [r[0] for r in note_rows]
    cells_on_notes = cur.execute(
        f"SELECT COUNT(*) FROM cell_fact WHERE row_id IN ({','.join('?' * len(note_ids))})",
        note_ids).fetchone()[0] if note_ids else -1
    check("note rows carry ZERO cells", cells_on_notes == 0, f"note_ids={note_ids} cells={cells_on_notes}")

    # --- CHANGE 1 (terminal-total parent fix) + CHANGE 2 (verified sums_to) ---
    rd = {rid: (parent, s2, lbl) for rid, parent, s2, lbl in cur.execute(
        "SELECT row_id, row_parent, sums_to, row_leaf_label FROM row_dim "
        "ORDER BY row_id").fetchall()}
    check("row 8 'Due within 1 year' row_parent NULL (not the preceding Total)",
          rd[8][0] is None, str(rd[8]))
    check("row 9 'Due after 1 year' row_parent NULL", rd[9][0] is None, str(rd[9]))
    check("rows 1-6 sums_to = Total#7", all(rd[i][1] == 7 for i in range(1, 7)),
          str([rd[i][1] for i in range(1, 7)]))
    check("rows 8-9 sums_to = second Total#10", rd[8][1] == 10 and rd[9][1] == 10,
          str((rd[8][1], rd[9][1])))
    check("Total#7 itself sums_to NULL", rd[7][1] is None, str(rd[7]))
    check("Total#10 itself sums_to NULL", rd[10][1] is None, str(rd[10]))
    check("note rows 11-14 sums_to NULL", all(rd[i][1] is None for i in range(11, 15)),
          str({i: rd[i][1] for i in range(11, 15)}))
    check("both totals verified all 3 cols (no sum-verification warning)",
          not any("does not sum" in w for w in summary["warnings"]),
          str(summary["warnings"]))

    # PRAGMA foreign_key_check clean
    fk = cur.execute("PRAGMA foreign_key_check").fetchall()
    check("PRAGMA foreign_key_check clean", fk == [], str(fk))

    # demo LEFT JOIN: debts_issued=1 table, other sections 0
    join = dict(cur.execute(
        "SELECT s.section_id, COUNT(t.table_id) FROM section s "
        "LEFT JOIN table_t t ON t.doc_id=s.doc_id AND t.section_id=s.section_id "
        "WHERE s.doc_id=? GROUP BY s.section_id", (_DOC_ID,)).fetchall())
    check("LEFT JOIN: debts_issued has 1 table", join.get("debts_issued") == 1, str(join.get("debts_issued")))
    others_nonzero = {k: v for k, v in join.items() if k != "debts_issued" and v != 0}
    check("LEFT JOIN: all other sections have 0 tables", others_nonzero == {}, str(others_nonzero))
    con.close()

    # idempotency: re-run -> identical counts
    load_units(str(_COPY_DB), _DOC_ID, [unit])
    con2 = sqlite3.connect(_COPY_DB)
    c2 = con2.cursor()
    counts = {t: c2.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("table_t", "col_dim", "row_dim", "cell_fact")}
    check("idempotent: table_t=1 col_dim=3 row_dim=14 cell_fact=30",
          counts == {"table_t": 1, "col_dim": 3, "row_dim": 14, "cell_fact": 30}, str(counts))
    check("idempotent: foreign_key_check still clean", c2.execute("PRAGMA foreign_key_check").fetchall() == [])

    # v_cell_flat sample
    print("\n  v_cell_flat sample (first 5 numeric rows):")
    flat = c2.execute(
        "SELECT institution, period, table_type, row_lvl1, col_lvl1, value_num, cell_state "
        "FROM v_cell_flat WHERE value_num IS NOT NULL ORDER BY row_id, col_id LIMIT 5").fetchall()
    for r in flat:
        print(f"    {r}")
    check("v_cell_flat returns rows", len(flat) == 5, str(len(flat)))
    con2.close()



_AUDIT = _REPO / "findociq/outputs/pillar3/dbs_2Q25/audit/DBS_2Q25_performance_summary"


def period_expr_integration_tests() -> None:
    """Group-banner col_period + lineage exclusion (site a) and title-expression
    table period (site c) on the REAL NII + geography units."""
    print("\nPeriod-expression integration — NII avg-balance-sheet + geography instances")
    base = fixture_db()
    if base is None:
        check("fixture DB built (period test)", False, "see [fixture] above")
        return
    _COPY_DB2 = base.parent / "fs_eval_v7_period_test.db"
    shutil.copy2(base, _COPY_DB2)
    units = [
        dict(section_id="net_interest_income", pages=[10, 11],
             parsed_path=str(_AUDIT / "net_interest_income_p10-11/parsed.json")),
        dict(section_id="performance_by_geography", pages=[17, 18, 19],
             parsed_path=str(_AUDIT / "performance_by_geography_p17-19/parsed.json")),
    ]
    load_units(str(_COPY_DB2), _DOC_ID, units)
    con = sqlite3.connect(_COPY_DB2)
    cur = con.cursor()

    # --- SITE a: Average balance sheet — 3 period GROUPS x 3 metric leaves ----
    abs_tid = cur.execute(
        "SELECT table_id FROM table_t WHERE section_id='net_interest_income' AND "
        "table_title='Average balance sheet'").fetchone()
    check("avg-balance-sheet table_t row exists", abs_tid is not None, str(abs_tid))
    if abs_tid:
        tid = abs_tid[0]
        leaves = cur.execute(
            "SELECT col_id, col_leaf_label, col_period, col_lineage_id FROM col_dim "
            "WHERE table_id=? AND col_hierarchy=1 ORDER BY col_id", (tid,)).fetchall()
        check("avg-balance-sheet has 9 leaf cols", len(leaves) == 9, str(len(leaves)))
        periods = [l[2] for l in leaves]
        check("avg-balance-sheet col_periods = H1'25,H1'24,H2'24 x3",
              periods == ["2025-06-30"] * 3 + ["2024-06-30"] * 3 + ["2024-12-31"] * 3,
              str(periods))
        # each metric converges to ONE col_lineage_id across the 3 period groups
        by_label: dict[str, set] = {}
        for _cid, lbl, _cp, hid in leaves:
            by_label.setdefault(lbl, set()).add(hid)
        check("'Average balance ($m)' converges to 1 lineage across 3 periods",
              len(by_label.get("Average balance ($m)", set())) == 1, str(by_label))
        check("'Interest ($m)' converges to 1 lineage",
              len(by_label.get("Interest ($m)", set())) == 1, str(by_label))
        check("'Average rate (%)' converges to 1 lineage",
              len(by_label.get("Average rate (%)", set())) == 1, str(by_label))
        check("3 distinct leaf lineages total (period axis excluded)",
              len({l[3] for l in leaves}) == 3, str({l[3] for l in leaves}))
        # lineage lvl1 is the leaf label (banner excluded, no 'value' fallback)
        hid = by_label["Average balance ($m)"].pop()
        lk = cur.execute("SELECT lvl1, depth FROM col_lineage WHERE col_lineage_id=?", (hid,)).fetchone()
        check("converged lineage lvl1 = 'Average balance ($m)' depth 1",
              lk == ("Average balance ($m)", 1), str(lk))
        # table_t.period NULL (every leaf carries col_period)
        tp = cur.execute("SELECT period FROM table_t WHERE table_id=?", (tid,)).fetchone()[0]
        check("avg-balance-sheet table_t.period NULL (all cols carry col_period)", tp is None, str(tp))
        # cells span the real periods, not a single doc-fallback date
        cell_periods = dict(cur.execute(
            "SELECT period, COUNT(*) FROM cell_fact WHERE table_id=? GROUP BY period", (tid,)).fetchall())
        check("avg-balance-sheet cells span 3 periods (not doc fallback)",
              set(cell_periods) == {"2025-06-30", "2024-06-30", "2024-12-31"}, str(cell_periods))

    # --- SITE c: geography title-expression period-instances ------------------
    geo = dict(cur.execute(
        "SELECT table_title, period FROM table_t WHERE section_id='performance_by_geography'").fetchall())
    check("geo 'Selected income statement items 1st Half 2025' period 2025-06-30",
          geo.get("Selected income statement items 1st Half 2025") == "2025-06-30", str(geo))
    check("geo '... 2nd Half 2024' period 2024-12-31 (NOT doc fallback 2025-06-30)",
          geo.get("Selected income statement items 2nd Half 2024") == "2024-12-31", str(geo))
    check("geo '... 1st Half 2024' period 2024-06-30",
          geo.get("Selected income statement items 1st Half 2024") == "2024-06-30", str(geo))
    con.close()


def drop_echo_groups_tests() -> None:
    """drop_echo_groups(extraction): group == leaf (case/whitespace-insensitive)
    -> group set to None; a distinct group/leaf pair, and a None group, are
    left untouched. Pure — the input Extraction is not mutated."""
    print("\ndrop_echo_groups tests")
    echo_col = GColumn(group="1st Qtr 2026", leaf="1st Qtr 2026")
    distinct_col = GColumn(group="1st Qtr 2026", leaf="Net profit ($m)")
    none_col = GColumn(group=None, leaf="Net profit ($m)")
    casefold_col = GColumn(group="  1ST qtr 2026 ", leaf="1st Qtr 2026")
    t = GTable(title="t", columns=[echo_col, distinct_col, none_col, casefold_col], rows=[])
    ext = Extraction(tables=[t])
    out = drop_echo_groups(ext)

    check("echo col (group==leaf) -> group None",
          out.tables[0].columns[0].group is None, str(out.tables[0].columns[0]))
    check("distinct group/leaf -> untouched",
          out.tables[0].columns[1].group == "1st Qtr 2026", str(out.tables[0].columns[1]))
    check("None group -> untouched (still None)",
          out.tables[0].columns[2].group is None, str(out.tables[0].columns[2]))
    check("echo col, case/whitespace-insensitive -> group None",
          out.tables[0].columns[3].group is None, str(out.tables[0].columns[3]))
    # input not mutated
    check("input extraction's echo column untouched (pure function)",
          ext.tables[0].columns[0].group == "1st Qtr 2026", str(ext.tables[0].columns[0]))


# ===========================================================================
# 3) INDUSTRY DIMENSION + AXIS EXCLUSIVITY (mirror of test_geo_stamp.py /
#    test_segment_stamp.py, plus the new cross-axis collision rule)
# ===========================================================================
def _fresh_v7_db(td: str) -> Path:
    db = Path(td) / "industry_v7.db"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA_V7.read_text())
    con.commit()
    con.close()
    return db


def industry_map_tests(ind_map: dict[str, str]) -> None:
    print("\nIndustry map lookups (ind_norm / ind_lookup)")
    check("ind_norm('Manufacturing') lowercases",
          ind_norm("Manufacturing") == "manufacturing")
    check("ind_norm(None) -> ''", ind_norm(None) == "")

    check("'Manufacturing' -> IND_MFG", ind_lookup("Manufacturing", ind_map) == "IND_MFG")
    check("'Building and construction' -> IND_CONSTRUCTION",
          ind_lookup("Building and construction", ind_map) == "IND_CONSTRUCTION")
    check("'Housing loans' -> IND_HOUSING", ind_lookup("Housing loans", ind_map) == "IND_HOUSING")
    check("'General commerce' -> IND_COMMERCE",
          ind_lookup("General commerce", ind_map) == "IND_COMMERCE")
    # '&'/'and' + UOB spelling variants
    check("'Transport, storage and communication' -> IND_TRANSPORT_COMMS",
          ind_lookup("Transport, storage and communication", ind_map) == "IND_TRANSPORT_COMMS")
    check("'Transportation, storage & communications' -> IND_TRANSPORT_COMMS (UOB variant)",
          ind_lookup("Transportation, storage & communications", ind_map) == "IND_TRANSPORT_COMMS")
    check("'Financial institutions, investment and holding companies' -> IND_FI_INVEST",
          ind_lookup("Financial institutions, investment and holding companies", ind_map)
          == "IND_FI_INVEST")
    check("'Financial institutions, investment & holding companies' -> IND_FI_INVEST ('&' variant)",
          ind_lookup("Financial institutions, investment & holding companies", ind_map)
          == "IND_FI_INVEST")
    check("'Professionals and private individuals' -> IND_PROF_INDIV",
          ind_lookup("Professionals and private individuals", ind_map) == "IND_PROF_INDIV")
    check("'Professionals and individuals' -> IND_PROF_INDIV (shortened variant)",
          ind_lookup("Professionals and individuals", ind_map) == "IND_PROF_INDIV")
    check("'Professionals & private individuals (excluding housing loans)' -> IND_PROF_INDIV",
          ind_lookup("Professionals & private individuals (excluding housing loans)", ind_map)
          == "IND_PROF_INDIV")
    check("'Agriculture, mining and quarrying' -> IND_AGRI_MINING",
          ind_lookup("Agriculture, mining and quarrying", ind_map) == "IND_AGRI_MINING")
    check("'Others' -> IND_OTHERS", ind_lookup("Others", ind_map) == "IND_OTHERS")

    # totals
    for lbl in ("Total", "Total NPLs", "Total NPAs", "Total non-performing loans",
                "Total Non-performing assets (NPA)", "Total non-performing assets (NPA)"):
        check(f"{lbl!r} -> IND_TOTAL", ind_lookup(lbl, ind_map) == "IND_TOTAL",
              str(ind_lookup(lbl, ind_map)))

    # deliberately unmapped non-members
    for lbl in ("Classified debt securities", "Classified contingent liabilities",
                "Debt securities, contingent liabilities & others", "Loans and advances"):
        check(f"{lbl!r} -> None (not a MAS industry category)",
              ind_lookup(lbl, ind_map) is None, str(ind_lookup(lbl, ind_map)))

    # footnote-glued label: 'Others2' has NO superscript/paren marker for
    # _FOOTNOTE_TAIL to strip (a bare trailing digit), so it does NOT normalise
    # to 'others' today. This is a KNOWN LIMITATION of the current normaliser,
    # not something to special-case here (the geometry stage owns footnote
    # repair) — the test documents the current, unpatched behaviour.
    check("'Others2' (bare digit, no footnote marker) does NOT normalise to 'others' "
          "(geometry stage's job, not the loader's)",
          ind_lookup("Others2", ind_map) is None, str(ind_lookup("Others2", ind_map)))


def industry_stamp_and_exclusivity_load_tests() -> None:
    print("\nIndustry stamping + axis exclusivity — synthetic load")

    # --- Table IND: real 'NPL/NPA by industry' shape. Industries in ROWS, one
    # unmapped composite row ('Classified debt securities'), an 'Others' row
    # (3-way label collision: geo OTH / segment SEG_OTHER / industry IND_OTHERS —
    # industry DOMINATES here, >=2 unambiguous industry member rows), a Total row.
    ind_tbl = GTable(
        title="Non-performing assets by industry",
        label_header="$m",
        columns=[GColumn(group=None, leaf="2025")],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Manufacturing", values=_cells("10")),
            GRow(row_id="2", row_type="data", level=1, label="Building and construction",
                 values=_cells("20")),
            GRow(row_id="3", row_type="data", level=1, label="Housing loans", values=_cells("30")),
            GRow(row_id="4", row_type="data", level=1, label="General commerce", values=_cells("15")),
            GRow(row_id="5", row_type="data", level=1, label="Others", values=_cells("5")),
            GRow(row_id="6", row_type="data", level=1, label="Classified debt securities",
                 values=_cells("2")),
            GRow(row_id="7", row_type="total", level=0, label="Total", values=_cells("82")),
        ],
    )
    # --- Table SEG: segment-in-cols shape (the ORIGINAL contaminated-cell bug).
    # 'Others' COLUMN is a 3-way collision too; segment DOMINATES (2 other
    # unambiguous segment member cols) -> SEG_OTHER, geo/industry stay default.
    seg_tbl = GTable(
        title="Business segment performance",
        label_header="$m",
        columns=[GColumn(group=None, leaf="Consumer Banking/ Wealth Management"),
                 GColumn(group=None, leaf="Institutional Banking"),
                 GColumn(group=None, leaf="Others"),
                 GColumn(group=None, leaf="Total")],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Net interest income",
                 values=_cells("10", "20", "5", "35")),
        ],
    )
    # --- Table AMBIG: 'Others' collides across all 3 axes but NO axis reaches
    # the >=2-unambiguous-member dominance threshold (only 1 other geo member
    # row, 0 other segment/industry member rows) -> stamps NOTHING + warns.
    ambig_tbl = GTable(
        title="Mixed residual table",
        label_header="$m",
        columns=[GColumn(group=None, leaf="2025")],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Singapore", values=_cells("100")),
            GRow(row_id="2", row_type="data", level=1, label="Others", values=_cells("5")),
        ],
    )

    with tempfile.TemporaryDirectory() as td:
        db = _fresh_v7_db(td)
        con = sqlite3.connect(db)
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('IND','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES ('IND','s1','1','S',1,NULL,1)")
        con.commit()
        con.close()

        parsed = Path(td) / "parsed.json"
        parsed.write_text(json.dumps(
            Extraction(tables=[ind_tbl, seg_tbl, ambig_tbl]).model_dump()))
        summary = load_units(str(db), "IND",
                             [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])

        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys = ON")
        cur = con.cursor()

        def tid(prefix: str) -> str:
            return cur.execute("SELECT table_id FROM table_t WHERE doc_id='IND' AND "
                               "table_id LIKE ?", (f"%{prefix}%",)).fetchone()[0]

        ind_id = tid("non_performing_assets_by_industry")
        seg_id = tid("business_segment_performance")
        amb_id = tid("mixed_residual_table")

        # --- industry table: members + IND_TOTAL total + unmapped stays default ---
        rind = dict(cur.execute("SELECT row_leaf_label, industry_key FROM row_dim WHERE "
                                "doc_id='IND' AND table_id=?", (ind_id,)).fetchall())
        check("industry row 'Manufacturing' -> IND_MFG", rind.get("Manufacturing") == "IND_MFG",
              str(rind))
        check("industry row 'Building and construction' -> IND_CONSTRUCTION",
              rind.get("Building and construction") == "IND_CONSTRUCTION")
        check("industry row 'Housing loans' -> IND_HOUSING", rind.get("Housing loans") == "IND_HOUSING")
        check("industry row 'General commerce' -> IND_COMMERCE",
              rind.get("General commerce") == "IND_COMMERCE")
        check("industry row 'Classified debt securities' -> NULL (not a MAS category, default)",
              rind.get("Classified debt securities") is None, str(rind))
        # 'Total' matches BOTH segment_map ('total'->SEG_TOTAL) and industry_map
        # ('total'->IND_TOTAL) — but both are their OWN axis's sentinel/default,
        # not a real member collision (a Total row legitimately IS both
        # simultaneously), so axis exclusivity leaves it stamped as-is (mirrors
        # test_segment_stamp.py's 'row Total -> SEG_TOTAL').
        check("industry row 'Total' -> IND_TOTAL (sentinel-vs-sentinel, not a collision)",
              rind.get("Total") == "IND_TOTAL", str(rind))

        # --- AXIS EXCLUSIVITY: industry-table 'Others' row -> IND_OTHERS, seg default.
        # f.geo_key is asserted NULL: geography stamping retired 2026-08-12, so the
        # column survives but the loader never writes it (was 'GLOBAL'). ---
        others_row = cur.execute(
            "SELECT r.industry_key, f.geo_key, f.segment_key FROM row_dim r "
            "JOIN cell_fact f ON f.doc_id=r.doc_id AND f.table_id=r.table_id AND f.row_id=r.row_id "
            "WHERE r.doc_id='IND' AND r.table_id=? AND r.row_leaf_label='Others'", (ind_id,)
        ).fetchone()
        check("industry-table 'Others' row: industry_key=IND_OTHERS, cell geo=NULL, seg=SEG_TOTAL "
              "(industry axis DOMINATES the collision)",
              others_row == ("IND_OTHERS", None, "SEG_TOTAL"), str(others_row))

        # --- AXIS EXCLUSIVITY: segment-table 'Others' column -> SEG_OTHER, geo NULL ---
        seg_others = cur.execute(
            "SELECT c.segment_key, c.industry_key, f.geo_key FROM col_dim c "
            "JOIN cell_fact f ON f.doc_id=c.doc_id AND f.table_id=c.table_id AND f.col_id=c.col_id "
            "WHERE c.doc_id='IND' AND c.table_id=? AND c.col_leaf_label='Others' LIMIT 1", (seg_id,)
        ).fetchone()
        check("segment-table 'Others' column: segment_key=SEG_OTHER, industry_key=NULL, "
              "cell geo=NULL (segment axis DOMINATES)",
              seg_others == ("SEG_OTHER", None, None), str(seg_others))

        # --- ambiguous label, NO dominant axis: stamps nothing + warns ---
        amb_others = cur.execute(
            "SELECT geo_key, segment_key, industry_key FROM row_dim WHERE doc_id='IND' "
            "AND table_id=? AND row_leaf_label='Others'", (amb_id,)).fetchone()
        check("AMBIG table 'Others' row: geo/segment/industry all NULL (no axis dominates)",
              amb_others == (None, None, None), str(amb_others))
        ambig_warnings = [w for w in summary["warnings"] if "ambiguous axis label" in w]
        check("exactly one 'ambiguous axis label' warning, naming 'Others' + 'no dominant axis'",
              len(ambig_warnings) == 1 and "'Others'" in ambig_warnings[0]
              and "no dominant axis" in ambig_warnings[0], str(ambig_warnings))

        fk = cur.execute("PRAGMA foreign_key_check").fetchall()
        check("PRAGMA foreign_key_check clean", fk == [], str(fk))
        con.close()


def axis_exclusivity_pure_tests() -> None:
    """Pure unit tests for resolve_axis_labels(), independent of a DB load."""
    print("\nresolve_axis_labels() pure tests")
    seg_map = {"consumer banking": "SEG_RETAIL", "institutional banking": "SEG_WHOLESALE",
               "others": "SEG_OTHER", "total": "SEG_TOTAL"}
    ind_map = {"manufacturing": "IND_MFG", "housing loans": "IND_HOUSING",
               "others": "IND_OTHERS", "total": "IND_TOTAL"}

    # 'Others' collides on BOTH axes; segment + industry each have >=2 OTHER
    # unambiguous member matches in this pool -> 2 candidates -> no dominant.
    # (Geography was a third axis here until 2026-08-12; stamping is retired.)
    labels = ["Consumer Banking", "Institutional Banking", "Manufacturing", "Housing loans",
              "Others", "Singapore", "Total"]
    resolved, warnings = resolve_axis_labels(labels, seg_map, ind_map)
    by_label = dict(zip(labels, resolved))
    check("'Others' ambiguous (segment AND industry both have >=2 unambiguous members) -> no stamp",
          by_label["Others"] == {"segment": None, "industry": None}, str(by_label["Others"]))
    check("resolve_axis_labels warns 'ambiguous axis label' for 'Others'",
          any("ambiguous axis label 'Others'" in w and "no dominant axis" in w for w in warnings),
          str(warnings))
    check("'Singapore' stamps NOTHING now that geography is retired",
          by_label["Singapore"] == {"segment": None, "industry": None},
          str(by_label["Singapore"]))
    check("'Total' (segment+industry both SENTINEL, not a collision) stamps BOTH",
          by_label["Total"] == {"segment": "SEG_TOTAL", "industry": "IND_TOTAL"},
          str(by_label["Total"]))

    # Segment-dominant scenario: 3 unambiguous segment members present, industry
    # has none -> 'Others' resolves to segment alone.
    labels2 = ["Consumer Banking", "Institutional Banking", "Manufacturing", "Others"]
    seg_map2 = {"consumer banking": "SEG_RETAIL", "institutional banking": "SEG_WHOLESALE",
                "markets": "SEG_MARKETS", "others": "SEG_OTHER"}
    resolved2, warnings2 = resolve_axis_labels(labels2, seg_map2,
                                               {"manufacturing": "IND_MFG", "others": "IND_OTHERS"})
    by_label2 = dict(zip(labels2, resolved2))
    check("'Others' resolves to SEGMENT alone when segment dominates (>=2 unambiguous members)",
          by_label2["Others"] == {"segment": "SEG_OTHER", "industry": None},
          str(by_label2["Others"]))
    check("no warning when a dominant axis is found", warnings2 == [], str(warnings2))


if __name__ == "__main__":
    unit_tests()
    doc_default_tests()
    coupon_unit_integration_tests()
    integration_tests()
    period_expr_integration_tests()
    drop_echo_groups_tests()
    with tempfile.TemporaryDirectory() as _td:
        _db = _fresh_v7_db(_td)
        _im = dict(sqlite3.connect(_db).execute(
            "SELECT label_norm, industry_key FROM industry_map").fetchall())
        industry_map_tests(_im)
    industry_stamp_and_exclusivity_load_tests()
    axis_exclusivity_pure_tests()
    print(f"\n{'ALL PASS' if _FAILS == 0 else str(_FAILS) + ' FAILED'}")
    raise SystemExit(1 if _FAILS else 0)
