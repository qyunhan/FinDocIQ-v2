"""Plain check() script for the PERIOD-SPAN grammar + loader wiring (NO pytest).
Exit 0 all-pass / 1 any-fail.

Run:  python findociq/pipeline/stage2_load/test_period_span.py

Covers the two user-verified period defects fixed 2026-07-14:
  (a) bare-year column GROUP banners ('2025'/'2024') are periods in COLUMN context
      -> col_period + span FY, excluded from lineage (leaves converge to '$m');
      a bare year in a TITLE context stays guarded (not a period).
  (b) '2H25' and 'FY2025' share the 31-Dec END date but differ by SPAN (2H vs FY)
      and period_START (Jul-01 vs Jan-01) — flow DURATION is no longer lost.
Plus the 9M grammar and period_start per span branch. Loads a SYNTHETIC GTable
end-to-end against a fresh schema_v7 DB to assert col_period + period_span +
period_start + lineage exclusion.

Covers the two user-verified period defects fixed 2026-07-15:
  (c) a COMBINED period+unit column header ('2025 $m' / '2024 $m') — the unit
      token in the residual was defeating is_period_text, so col_period fell NULL
      and cells were mis-tagged the table/doc period. UNIT tokens are now stripped
      from the COLUMN residual before the boilerplate whitelist; the unit is still
      PARSED separately for col_dim.unit ('2025 $m' -> col_period FY2025 AND S$m).
  (d) a FOOTNOTED period column ('2H25¹ $m' with the superscript INSIDE the text,
      '2H 2025 (1)') — footnote markers are now stripped ANYWHERE in a COLUMN
      header (not just trailing) so the period grammar underneath is recognised.

Plus an EXHAUSTIVE span-vocabulary matrix (vocabulary_matrix_tests) covering every
span token {1Q,2Q,3Q,4Q,1H,2H,9M,FY,as_at} across every printed form the grammar
claims, each asserting the exact (period_start, period, span) triple, plus the
negative guards. The matrix is printed so the vocabulary is self-documenting.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path
from stage2_load.load_v7 import (  # noqa: E402
    _span_start, col_lineage, is_period_text, load_units, parse_period_expr,
    parse_period_span,
)
from stage1_extract.chunk.schema import GCell, GColumn, GRow, GTable, Extraction  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_SCHEMA = _REPO / "findociq/schema/schema_v7.sql"

_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILS += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


# ===========================================================================
# 1) GRAMMAR — parse_period_span returns (iso_end, span, iso_start)
# ===========================================================================
def grammar_tests() -> None:
    print("Grammar — parse_period_span (end, span, start)")

    def ps(t, column=False):
        return parse_period_span(t, column=column)

    # --- DEFECT (a): bare year is a period in COLUMN context, guarded in TITLE ---
    check("bare '2025' COLUMN -> (2025-12-31, FY, 2025-01-01)",
          ps("2025", True) == ("2025-12-31", "FY", "2025-01-01"), str(ps("2025", True)))
    check("bare '2024' COLUMN -> (2024-12-31, FY, 2024-01-01)",
          ps("2024", True) == ("2024-12-31", "FY", "2024-01-01"), str(ps("2024", True)))
    check("bare '2025' TITLE ctx -> None (guard kept)", ps("2025", False) is None,
          str(ps("2025", False)))
    check("parse_period_expr('2025') -> None (title accessor unchanged)",
          parse_period_expr("2025") is None, str(parse_period_expr("2025")))

    # --- TITLE-TRAILING bare year: the caption slot, and ONLY that slot --------
    # UOB 4Q25 splits one geography exhibit five ways by a trailing caption:
    # '— 1H25' / '— 2H24' / '— 2H25' parsed, '— 2024' / '— 2025' did not, so both
    # fell to doc_period and FY2024's cells were stamped 2025-12-31.
    check("'... — 2024' TITLE -> (2024-12-31, FY, 2024-01-01)",
          ps("Performance by Geographical Segment ¹ — 2024")
          == ("2024-12-31", "FY", "2024-01-01"),
          str(ps("Performance by Geographical Segment ¹ — 2024")))
    check("'... — 2025' TITLE -> FY2025 (sibling of the same exhibit)",
          ps("Performance by Geographical Segment ¹ — 2025")
          == ("2025-12-31", "FY", "2025-01-01"),
          str(ps("Performance by Geographical Segment ¹ — 2025")))
    check("'... — 2H24' still parses as 2H (printed token wins, branch order)",
          ps("Performance by Geographical Segment ¹(cont’d) — 2H24")
          == ("2024-12-31", "2H", "2024-07-01"),
          str(ps("Performance by Geographical Segment ¹(cont’d) — 2H24")))
    check("':' and '|' delimiters also open the caption slot",
          ps("Revenue: 2024") == ("2024-12-31", "FY", "2024-01-01"),
          str(ps("Revenue: 2024")))
    # NEGATIVES — a year that is not the whole trailing caption stays guarded.
    check("incidental year mid-title -> None ('Basel III 2024 framework')",
          ps("Basel III 2024 framework") is None, str(ps("Basel III 2024 framework")))
    check("trailing year with NO delimiter -> None ('Note 3 2025')",
          ps("Note 3 2025") is None, str(ps("Note 3 2025")))
    check("two-year comparative caption -> NOT collapsed by the new rule",
          ps("PERFORMANCE BY GEOGRAPHY — Year 2025 & Year 2024")[1] == "FY",
          str(ps("PERFORMANCE BY GEOGRAPHY — Year 2025 & Year 2024")))
    check("explicit printed date is untouched (branch tried first)",
          ps("Year ended 31 December 2024") == ("2024-12-31", "FY", "2024-01-01"),
          str(ps("Year ended 31 December 2024")))
    check("is_period_text('... — 2024') stays False (title is not a period header)",
          is_period_text("Performance by Geographical Segment ¹ — 2024") is False,
          str(is_period_text("Performance by Geographical Segment ¹ — 2024")))

    # --- DEFECT (b): 2H25 vs FY2025 share END date, differ by span + start -------
    check("'2H25' -> (2025-12-31, 2H, 2025-07-01)",
          ps("2H25", True) == ("2025-12-31", "2H", "2025-07-01"), str(ps("2H25", True)))
    check("'2H25' and bare '2025' share END date, DIFFER by span",
          ps("2H25", True)[0] == ps("2025", True)[0]
          and ps("2H25", True)[1] != ps("2025", True)[1], "END/span")
    check("'2H25' and '2025' DIFFER by period_start (Jul-01 vs Jan-01)",
          ps("2H25", True)[2] != ps("2025", True)[2],
          f"{ps('2H25', True)[2]} vs {ps('2025', True)[2]}")

    # --- halves ------------------------------------------------------------------
    check("'1H25' -> (2025-06-30, 1H, 2025-01-01)",
          ps("1H25") == ("2025-06-30", "1H", "2025-01-01"), str(ps("1H25")))
    check("'2H24' -> (2024-12-31, 2H, 2024-07-01)",
          ps("2H24") == ("2024-12-31", "2H", "2024-07-01"), str(ps("2H24")))
    check("'1st Half 2025' -> (2025-06-30, 1H, 2025-01-01)",
          ps("1st Half 2025") == ("2025-06-30", "1H", "2025-01-01"), str(ps("1st Half 2025")))
    check("'2nd Half 2024' -> (2024-12-31, 2H, 2024-07-01)",
          ps("2nd Half 2024") == ("2024-12-31", "2H", "2024-07-01"), str(ps("2nd Half 2024")))

    # --- quarters (printed 'nQ' convention; period_start = quarter first day) -----
    check("'1Q25' -> (2025-03-31, 1Q, 2025-01-01)",
          ps("1Q25") == ("2025-03-31", "1Q", "2025-01-01"), str(ps("1Q25")))
    check("'2Q25' -> (2025-06-30, 2Q, 2025-04-01)",
          ps("2Q25") == ("2025-06-30", "2Q", "2025-04-01"), str(ps("2Q25")))
    check("'3Q25' -> (2025-09-30, 3Q, 2025-07-01) [quarter 3, NOT nine-months]",
          ps("3Q25") == ("2025-09-30", "3Q", "2025-07-01"), str(ps("3Q25")))
    check("'4Q24' -> (2024-12-31, 4Q, 2024-10-01)",
          ps("4Q24") == ("2024-12-31", "4Q", "2024-10-01"), str(ps("4Q24")))
    check("'Second Quarter 2025' -> (2025-06-30, 2Q, 2025-04-01)",
          ps("Second Quarter 2025") == ("2025-06-30", "2Q", "2025-04-01"),
          str(ps("Second Quarter 2025")))
    check("'quarter ended 31 March 2025' -> (2025-03-31, 1Q, 2025-01-01)",
          ps("quarter ended 31 March 2025") == ("2025-03-31", "1Q", "2025-01-01"),
          str(ps("quarter ended 31 March 2025")))

    # --- nine-months (cumulative YTD, span 9M, ends Sep-30, start Jan-01) ---------
    check("'9M25' -> (2025-09-30, 9M, 2025-01-01)",
          ps("9M25") == ("2025-09-30", "9M", "2025-01-01"), str(ps("9M25")))
    check("'9M 2025' -> (2025-09-30, 9M, 2025-01-01)",
          ps("9M 2025") == ("2025-09-30", "9M", "2025-01-01"), str(ps("9M 2025")))
    check("'YTD25' -> (2025-09-30, 9M, 2025-01-01)",
          ps("YTD25") == ("2025-09-30", "9M", "2025-01-01"), str(ps("YTD25")))
    check("'Nine months ended 30 September 2025' -> (2025-09-30, 9M, 2025-01-01)",
          ps("Nine months ended 30 September 2025") == ("2025-09-30", "9M", "2025-01-01"),
          str(ps("Nine months ended 30 September 2025")))
    check("'9M25' and '3Q25' share END date, DIFFER by span (9M vs 3Q)",
          ps("9M25")[0] == ps("3Q25")[0] and ps("9M25")[1] != ps("3Q25")[1], "9M vs 3Q")
    check("'9M25' and '3Q25' DIFFER by period_start (Jan-01 vs Jul-01)",
          ps("9M25")[2] != ps("3Q25")[2], f"{ps('9M25')[2]} vs {ps('3Q25')[2]}")

    # --- full-year / FY ----------------------------------------------------------
    check("'FY2024' -> (2024-12-31, FY, 2024-01-01)",
          ps("FY2024") == ("2024-12-31", "FY", "2024-01-01"), str(ps("FY2024")))
    check("'Full Year 2024' -> (2024-12-31, FY, 2024-01-01)",
          ps("Full Year 2024") == ("2024-12-31", "FY", "2024-01-01"), str(ps("Full Year 2024")))
    check("'Year ended 31 December 2024' -> (2024-12-31, FY, 2024-01-01)",
          ps("Year ended 31 December 2024") == ("2024-12-31", "FY", "2024-01-01"),
          str(ps("Year ended 31 December 2024")))
    check("'Half year ended 31 December 2024' -> (2024-12-31, 2H, 2024-07-01)",
          ps("Half year ended 31 December 2024") == ("2024-12-31", "2H", "2024-07-01"),
          str(ps("Half year ended 31 December 2024")))

    # --- explicit DD-Month date (point-in-time balance): span 'as_at', start NULL -
    check("'30 Jun 2025' -> (2025-06-30, as_at, None)",
          ps("30 Jun 2025") == ("2025-06-30", "as_at", None), str(ps("30 Jun 2025")))
    check("'As at 30 Jun 2025' -> (2025-06-30, as_at, None)",
          ps("As at 30 Jun 2025") == ("2025-06-30", "as_at", None), str(ps("As at 30 Jun 2025")))
    check("as_at period_start is None (no interval)", _span_start("as_at", "2025-06-30") is None)

    # --- month-year (COLUMN context only): month-end, as_at -----------------------
    check("'Dec-25' COLUMN -> (2025-12-31, as_at, None)",
          ps("Dec-25", True) == ("2025-12-31", "as_at", None), str(ps("Dec-25", True)))
    check("'Dec 2025' COLUMN -> (2025-12-31, as_at, None)",
          ps("Dec 2025", True) == ("2025-12-31", "as_at", None), str(ps("Dec 2025", True)))
    check("'December 2024' COLUMN -> (2024-12-31, as_at, None)",
          ps("December 2024", True) == ("2024-12-31", "as_at", None), str(ps("December 2024", True)))
    check("'Jun 2025' COLUMN -> (2025-06-30, as_at, None)",
          ps("Jun 2025", True) == ("2025-06-30", "as_at", None), str(ps("Jun 2025", True)))
    check("'Dec 2025' TITLE ctx -> None (month-year is column-only)",
          ps("Dec 2025", False) is None, str(ps("Dec 2025", False)))

    # --- non-periods -------------------------------------------------------------
    check("'Net loans' COLUMN -> None", ps("Net loans", True) is None, str(ps("Net loans", True)))
    check("'Average balance ($m)' COLUMN -> None",
          ps("Average balance ($m)", True) is None, str(ps("Average balance ($m)", True)))
    check("'' -> None", ps("") is None)

    # --- is_period_text with column flag -----------------------------------------
    check("is_period_text('2025', column=True) True", is_period_text("2025", column=True) is True)
    check("is_period_text('2025') default False (title guard)", is_period_text("2025") is False)
    check("is_period_text('Note 3 2025', column=True) False (residual guard)",
          is_period_text("Note 3 2025", column=True) is False)
    check("is_period_text('Dec 2025', column=True) True", is_period_text("Dec 2025", column=True) is True)
    check("is_period_text('2H25', column=True) True", is_period_text("2H25", column=True) is True)

    # --- col_lineage: bare-year GROUP excluded, leaves converge, change kept ------
    check("col_lineage(group='2025', leaf='$m') -> ['$m'] (bare year excluded)",
          col_lineage(GColumn(group="2025", leaf="$m")) == ["$m"],
          str(col_lineage(GColumn(group="2025", leaf="$m"))))
    check("col_lineage(group='2024', leaf='$m') -> ['$m'] (converges with 2025)",
          col_lineage(GColumn(group="2024", leaf="$m")) == ["$m"])
    check("col_lineage(group='2H25', leaf='$m') -> ['$m'] (converges with bare years)",
          col_lineage(GColumn(group="2H25", leaf="$m")) == ["$m"])
    check("col_lineage(group='+/(-)', leaf='%') -> ['+/(-)', '%'] (change banner kept)",
          col_lineage(GColumn(group="+/(-)", leaf="%")) == ["+/(-)", "%"],
          str(col_lineage(GColumn(group="+/(-)", leaf="%"))))


# ===========================================================================
# 2) INTEGRATION — load a SYNTHETIC GTable ('2025'/'2024'/'2H25') into a
#    fresh schema_v7 DB and assert col_period + period_span + period_start +
#    lineage exclusion end-to-end.
# ===========================================================================
def integration_test() -> None:
    print("\nIntegration — synthetic '2025'/'2024'/'2H25' GTable")
    if not _SCHEMA.exists():
        check("schema_v7.sql exists", False, str(_SCHEMA))
        return

    gt = GTable(
        title="Allowance (synthetic)",
        label_header="$m",
        columns=[GColumn(group="2025", leaf="$m"),
                 GColumn(group="2024", leaf="$m"),
                 GColumn(group="2H25", leaf="$m")],
        rows=[GRow(row_id="1", row_type="data", level=1, label="Item A",
                   values=[GCell(value="100"), GCell(value="90"), GCell(value="50")]),
              GRow(row_id="2", row_type="data", level=1, label="Item B",
                   values=[GCell(value="200"), GCell(value="180"), GCell(value="100")]),
              GRow(row_id=None, row_type="total", level=0, label="Total",
                   values=[GCell(value="300"), GCell(value="270"), GCell(value="150")])],
    )

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "syn_v7.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA.read_text())
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('SYN','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES "
                    "('SYN','s1','1','Allowance',1,NULL,1)")
        con.commit()
        con.close()

        parsed = Path(td) / "parsed.json"
        parsed.write_text(json.dumps(Extraction(tables=[gt]).model_dump()))
        summary = load_units(str(db), "SYN",
                             [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])
        print(f"  load summary: {summary}")

        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys = ON")
        cur = con.cursor()

        leaves = cur.execute(
            "SELECT col_id, col_leaf_label, col_period, period_span, period_start, "
            "col_lineage_id FROM col_dim WHERE col_hierarchy=1 ORDER BY col_id").fetchall()
        check("3 leaf col_dim rows", len(leaves) == 3, str(leaves))
        # (col_period, period_span, period_start) per leaf — the fix's core assertion
        got = [(l[2], l[3], l[4]) for l in leaves]
        want = [("2025-12-31", "FY", "2025-01-01"),   # group '2025'
                ("2024-12-31", "FY", "2024-01-01"),   # group '2024' (was mis-stamped 2025-12-31)
                ("2025-12-31", "2H", "2025-07-01")]   # group '2H25'
        check("leaf (period, span, start) = FY2025 / FY2024 / 2H25", got == want, str(got))
        # DEFECT (a): the FY2024 column carries 2024-12-31, NOT the doc default 2025-12-31
        check("group '2024' col_period = 2024-12-31 (not doc default 2025-12-31)",
              leaves[1][2] == "2024-12-31", str(leaves[1]))
        # DEFECT (b): FY2025 (col 1) and 2H25 (col 3) share END date but differ by span
        check("col1 (FY) and col3 (2H) share END date 2025-12-31, differ by span",
              leaves[0][2] == leaves[2][2] and leaves[0][3] != leaves[2][3],
              f"{leaves[0]} vs {leaves[2]}")
        # lineage exclusion: all 3 leaves converge to ONE col_lineage_id = '$m'
        hdr_ids = {l[5] for l in leaves}
        check("all 3 leaves share ONE col_lineage_id (period axis excluded)",
              len(hdr_ids) == 1, str(hdr_ids))
        lk = cur.execute("SELECT lineage_key, depth FROM col_lineage WHERE col_lineage_id=?",
                         (leaves[0][5],)).fetchone()
        check("converged lineage = '$m' depth 1 (was '2025 > $m')", lk == ("$m", 1), str(lk))

        # every leaf carries col_period -> table_t.period NULL, and its span/start NULL
        tp = cur.execute(
            "SELECT period, period_span, period_start FROM table_t").fetchone()
        check("table_t.period NULL (all cols carry col_period)", tp[0] is None, str(tp))
        check("table_t.period_span/start NULL", tp[1] is None and tp[2] is None, str(tp))

        # cells: col 2 stamped 2024-12-31 (the defect-(a) fix), cols 1+3 -> 2025-12-31
        cell_p = dict(cur.execute(
            "SELECT col_id, period FROM cell_fact ORDER BY col_id").fetchall())
        check("cell col1 period 2025-12-31", cell_p.get(1) == "2025-12-31", str(cell_p))
        check("cell col2 period 2024-12-31 (mis-stamp fixed)", cell_p.get(2) == "2024-12-31", str(cell_p))
        check("cell col3 period 2025-12-31 (2H25)", cell_p.get(3) == "2025-12-31", str(cell_p))

        # v_cell / v_cell_flat expose effective period_span + period_start
        vspans = dict(cur.execute(
            "SELECT col_id, period_span FROM v_cell ORDER BY col_id").fetchall())
        check("v_cell.period_span col1=FY col3=2H", vspans.get(1) == "FY" and vspans.get(3) == "2H",
              str(vspans))
        vstart = dict(cur.execute(
            "SELECT col_id, period_start FROM v_cell ORDER BY col_id").fetchall())
        check("v_cell.period_start col1=2025-01-01 col3=2025-07-01",
              vstart.get(1) == "2025-01-01" and vstart.get(3) == "2025-07-01", str(vstart))
        flat = cur.execute("SELECT DISTINCT period_span, period_start FROM v_cell_flat "
                           "ORDER BY period_span, period_start").fetchall()
        check("v_cell_flat exposes 3 (span,start) pairs FY2025/FY2024/2H25",
              flat == [("2H", "2025-07-01"), ("FY", "2024-01-01"), ("FY", "2025-01-01")],
              str(flat))

        fk = cur.execute("PRAGMA foreign_key_check").fetchall()
        check("PRAGMA foreign_key_check clean", fk == [], str(fk))
        con.close()


def footnote_unit_tests() -> None:
    """DEFECTS (c) combined period+unit and (d) footnoted period columns."""
    print("\nFootnote + unit residual — defects (c)/(d)")
    from stage2_load.load_v7 import parse_unit  # noqa: E402

    def ps(t, column=True):
        return parse_period_span(t, column=column)

    # (c) combined period+unit: period parsed, residual unit stripped -> True; the
    #     unit is STILL parsed separately for col_dim.unit.
    check("'2025 $m' COLUMN -> (2025-12-31, FY, 2025-01-01)",
          ps("2025 $m") == ("2025-12-31", "FY", "2025-01-01"), str(ps("2025 $m")))
    check("'2024 $m' COLUMN -> (2024-12-31, FY, 2024-01-01)",
          ps("2024 $m") == ("2024-12-31", "FY", "2024-01-01"), str(ps("2024 $m")))
    check("is_period_text('2025 $m', column=True) True (unit stripped from residual)",
          is_period_text("2025 $m", column=True) is True)
    check("parse_unit('2025 $m') -> 'S$m' (unit still parsed for col_dim.unit)",
          parse_unit("2025 $m") == "S$m", str(parse_unit("2025 $m")))
    check("'2025 $m' TITLE ctx -> None (guard intact, no strip)",
          ps("2025 $m", column=False) is None, str(ps("2025 $m", column=False)))
    check("is_period_text('2025 $m') default False (title guard intact)",
          is_period_text("2025 $m") is False)

    # (d) footnoted period columns: superscript INSIDE ('2H25¹ $m') / trailing '(1)'.
    check("'2H25¹ $m' COLUMN -> (2025-12-31, 2H, 2025-07-01)",
          ps("2H25¹ $m") == ("2025-12-31", "2H", "2025-07-01"), str(ps("2H25¹ $m")))
    check("'2H24¹ $m' COLUMN -> (2024-12-31, 2H, 2024-07-01)",
          ps("2H24¹ $m") == ("2024-12-31", "2H", "2024-07-01"), str(ps("2H24¹ $m")))
    check("'2H 2025 (1)' COLUMN -> (2025-12-31, 2H, 2025-07-01)",
          ps("2H 2025 (1)") == ("2025-12-31", "2H", "2025-07-01"), str(ps("2H 2025 (1)")))
    check("'2H 2024 (1)' COLUMN -> (2024-12-31, 2H, 2024-07-01)",
          ps("2H 2024 (1)") == ("2024-12-31", "2H", "2024-07-01"), str(ps("2H 2024 (1)")))
    check("is_period_text('2H25¹ $m', column=True) True", is_period_text("2H25¹ $m", column=True) is True)
    check("parse_unit('2H25¹ $m') -> 'S$m'", parse_unit("2H25¹ $m") == "S$m", str(parse_unit("2H25¹ $m")))

    # negative: a descriptive header carrying a unit is NOT a period (residual keeps words)
    check("'Net loans $m' COLUMN -> None (residual 'net loans' not boilerplate)",
          ps("Net loans $m") is None, str(ps("Net loans $m")))
    check("is_period_text('Net loans $m', column=True) False", is_period_text("Net loans $m", column=True) is False)
    check("is_period_text('Note 3 2025 $m', column=True) False (residual 'note 3')",
          is_period_text("Note 3 2025 $m", column=True) is False)
    # all 4 UOB-style leaves converge to a single '$m' lineage (period axis excluded)
    leaves = [GColumn(group=None, leaf=x) for x in
              ("2025 $m", "2024 $m", "2H25¹ $m", "2H24¹ $m")]
    lins = {tuple(col_lineage(c)) for c in leaves}
    check("UOB 4 leaves converge to ONE lineage ('value')", lins == {("value",)}, str(lins))


def vocabulary_matrix_tests() -> None:
    """EXHAUSTIVE span vocabulary: every span token {1Q,2Q,3Q,4Q,1H,2H,9M,FY,as_at}
    across every printed form the grammar claims, each asserting the exact
    (period_start, period, span) triple. Printed as a self-documenting matrix."""
    print("\nVocabulary matrix — (period_start, period END, span) per printed form")
    print(f"  {'form':<38} {'col':<4} {'-> (start, end, span)':<34} mark")

    # (form, column_flag, expected_start, expected_end, expected_span)
    MATRIX = [
        # --- 1Q ---
        ("1Q25", False, "2025-01-01", "2025-03-31", "1Q"),
        ("1Q 2025", False, "2025-01-01", "2025-03-31", "1Q"),
        ("First Quarter 2025", False, "2025-01-01", "2025-03-31", "1Q"),
        ("quarter ended 31 March 2025", False, "2025-01-01", "2025-03-31", "1Q"),
        # --- 2Q ---
        ("2Q25", False, "2025-04-01", "2025-06-30", "2Q"),
        ("2Q 2025", False, "2025-04-01", "2025-06-30", "2Q"),
        ("Second Quarter 2025", False, "2025-04-01", "2025-06-30", "2Q"),
        ("quarter ended 30 June 2025", False, "2025-04-01", "2025-06-30", "2Q"),
        # --- 3Q ---
        ("3Q25", False, "2025-07-01", "2025-09-30", "3Q"),
        ("3Q 2025", False, "2025-07-01", "2025-09-30", "3Q"),
        ("Third Quarter 2025", False, "2025-07-01", "2025-09-30", "3Q"),
        ("quarter ended 30 September 2025", False, "2025-07-01", "2025-09-30", "3Q"),
        # --- 4Q ---
        ("4Q25", False, "2025-10-01", "2025-12-31", "4Q"),
        ("4Q 2025", False, "2025-10-01", "2025-12-31", "4Q"),
        ("Fourth Quarter 2025", False, "2025-10-01", "2025-12-31", "4Q"),
        ("quarter ended 31 December 2025", False, "2025-10-01", "2025-12-31", "4Q"),
        # --- 1H ---
        ("1H25", False, "2025-01-01", "2025-06-30", "1H"),
        ("1H 2025", False, "2025-01-01", "2025-06-30", "1H"),
        ("1st Half 2025", False, "2025-01-01", "2025-06-30", "1H"),
        ("First Half 2025", False, "2025-01-01", "2025-06-30", "1H"),
        ("half year ended 30 June 2025", False, "2025-01-01", "2025-06-30", "1H"),
        # --- 2H ---
        ("2H25", False, "2025-07-01", "2025-12-31", "2H"),
        ("2H 2025", False, "2025-07-01", "2025-12-31", "2H"),
        ("2nd Half 2025", False, "2025-07-01", "2025-12-31", "2H"),
        ("Second Half 2025", False, "2025-07-01", "2025-12-31", "2H"),
        ("half year ended 31 December 2025", False, "2025-07-01", "2025-12-31", "2H"),
        ("2H25¹ $m", True, "2025-07-01", "2025-12-31", "2H"),   # footnoted+unit
        ("2H 2025 (1)", True, "2025-07-01", "2025-12-31", "2H"),
        # --- 9M ---
        ("9M25", False, "2025-01-01", "2025-09-30", "9M"),
        ("9M 2025", False, "2025-01-01", "2025-09-30", "9M"),
        ("YTD25", False, "2025-01-01", "2025-09-30", "9M"),
        ("Nine Months 2025", False, "2025-01-01", "2025-09-30", "9M"),
        ("Nine months ended 30 September 2025", False, "2025-01-01", "2025-09-30", "9M"),
        # --- FY ---
        ("FY25", False, "2025-01-01", "2025-12-31", "FY"),
        ("FY2025", False, "2025-01-01", "2025-12-31", "FY"),
        ("Full Year 2025", False, "2025-01-01", "2025-12-31", "FY"),
        ("Year ended 31 December 2025", False, "2025-01-01", "2025-12-31", "FY"),
        ("2025", True, "2025-01-01", "2025-12-31", "FY"),           # bare year, COLUMN
        ("2025 $m", True, "2025-01-01", "2025-12-31", "FY"),        # bare year + unit
        # --- as_at (point-in-time balance; no interval -> start None) ---
        ("30 Jun 2025", False, None, "2025-06-30", "as_at"),
        ("As at 30 June 2025", False, None, "2025-06-30", "as_at"),
        ("31 December 2025", False, None, "2025-12-31", "as_at"),
        ("Dec-25", True, None, "2025-12-31", "as_at"),
        ("Dec 2025", True, None, "2025-12-31", "as_at"),
    ]

    seen_spans: set[str] = set()
    for form, col, e_start, e_end, e_span in MATRIX:
        r = parse_period_span(form, column=col)
        got = (r[2], r[0], r[1]) if r else None
        ok = got == (e_start, e_end, e_span)
        seen_spans.add(e_span)
        mark = "PASS" if ok else "FAIL"
        print(f"  {form:<38} {str(col):<5} -> {str(got):<32} [{mark}]")
        check(f"vocab {form!r} (col={col})", ok, f"got {got} want {(e_start, e_end, e_span)}")

    # the matrix must exercise EVERY span token
    want_spans = {"1Q", "2Q", "3Q", "4Q", "1H", "2H", "9M", "FY", "as_at"}
    check("matrix covers all 9 span tokens", seen_spans == want_spans,
          f"missing {want_spans - seen_spans}")

    # --- NEGATIVE GUARDS ---------------------------------------------------------
    print("  -- negative guards --")
    check("bare '2025' TITLE ctx -> None (guard)", parse_period_span("2025", column=False) is None)
    check("parse_period_expr('2025') -> None", parse_period_expr("2025") is None)
    check("is_period_text('2025 versus 2024', column=True) False (comparison banner)",
          is_period_text("2025 versus 2024", column=True) is False)
    check("is_period_text('2025 vs 2024', column=True) False",
          is_period_text("2025 vs 2024", column=True) is False)
    check("is_period_text('+/(-)', column=True) False (change banner, not a period)",
          is_period_text("+/(-)", column=True) is False)
    check("col_lineage(group='+/(-)', leaf='%') keeps change banner",
          col_lineage(GColumn(group="+/(-)", leaf="%")) == ["+/(-)", "%"])
    # '3Q25' is THE THIRD QUARTER, not cumulative nine-months (share END, differ span+start)
    q, m9 = parse_period_span("3Q25"), parse_period_span("9M25")
    check("'3Q25' span is '3Q' NOT '9M'", q[1] == "3Q" and q[1] != m9[1], f"{q} vs {m9}")
    check("'3Q25' and '9M25' share END 2025-09-30, differ by start (Jul vs Jan)",
          q[0] == m9[0] and q[2] == "2025-07-01" and m9[2] == "2025-01-01", f"{q} vs {m9}")


def integration_combined_unit_test() -> None:
    """DEFECTS (c)/(d) end-to-end: a UOB-style GTable whose leaves carry BOTH a
    period AND a unit ('2025 $m'/'2024 $m'/'2H25¹ $m'/'2H24¹ $m'). Asserts each
    leaf gets col_period + span AND col_dim.unit='S$m', leaves converge to ONE
    lineage, and the FY2024/2H24 cells land on 2024-12-31 (not the doc default)."""
    print("\nIntegration — UOB-style combined period+unit + footnoted columns")
    if not _SCHEMA.exists():
        check("schema_v7.sql exists", False, str(_SCHEMA))
        return

    gt = GTable(
        title="Income statement (synthetic)",
        label_header="$m",
        columns=[GColumn(group=None, leaf="2025 $m"),
                 GColumn(group=None, leaf="2024 $m"),
                 GColumn(group=None, leaf="2H25¹ $m"),
                 GColumn(group=None, leaf="2H24¹ $m")],
        rows=[GRow(row_id="1", row_type="data", level=1, label="Net interest income",
                   values=[GCell(value="9,355"), GCell(value="9,674"),
                           GCell(value="4,611"), GCell(value="4,620")]),
              GRow(row_id="2", row_type="data", level=1, label="Fee income",
                   values=[GCell(value="100"), GCell(value="90"),
                           GCell(value="50"), GCell(value="45")])],
    )

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "syn_iu.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA.read_text())
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('SYN2','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES "
                    "('SYN2','s1','1','Income',1,NULL,1)")
        con.commit(); con.close()

        parsed = Path(td) / "parsed.json"
        parsed.write_text(json.dumps(Extraction(tables=[gt]).model_dump()))
        summary = load_units(str(db), "SYN2",
                             [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])
        print(f"  load summary: {summary}")

        con = sqlite3.connect(db)
        cur = con.cursor()
        leaves = cur.execute(
            "SELECT col_id, col_leaf_label, col_period, period_span, unit, col_lineage_id "
            "FROM col_dim WHERE col_hierarchy=1 ORDER BY col_id").fetchall()
        got = [(l[2], l[3], l[4]) for l in leaves]
        want = [("2025-12-31", "FY", "S$m"),
                ("2024-12-31", "FY", "S$m"),
                ("2025-12-31", "2H", "S$m"),
                ("2024-12-31", "2H", "S$m")]
        check("4 leaves (period, span, unit) = FY25/FY24/2H25/2H24 all S$m",
              got == want, str(got))
        check("combined leaf carries BOTH col_period AND unit S$m (defect c)",
              leaves[0][2] == "2025-12-31" and leaves[0][4] == "S$m", str(leaves[0]))
        check("footnoted '2H25¹ $m' parsed to 2H (defect d)",
              leaves[2][2] == "2025-12-31" and leaves[2][3] == "2H", str(leaves[2]))
        check("all 4 leaves converge to ONE col_lineage_id",
              len({l[5] for l in leaves}) == 1, str({l[5] for l in leaves}))

        # NII FY24 9,674 lands on 2024-12-31 (the user-verified mis-tag)
        p = dict(cur.execute(
            "SELECT col_id, period FROM cell_fact cf JOIN row_dim rd USING(doc_id,table_id,row_id) "
            "WHERE rd.row_leaf_label='Net interest income' ORDER BY col_id").fetchall())
        check("NII col2 (2024 $m) cell period 2024-12-31 (9,674 no longer FY2025)",
              p.get(2) == "2024-12-31", str(p))
        check("NII col4 (2H24) cell period 2024-12-31", p.get(4) == "2024-12-31", str(p))
        check("NII col1 (2025 $m) cell period 2025-12-31", p.get(1) == "2025-12-31", str(p))
        fk = cur.execute("PRAGMA foreign_key_check").fetchall()
        check("PRAGMA foreign_key_check clean", fk == [], str(fk))
        con.close()


def integration_cell_span_test() -> None:
    """MATERIALISED cell_fact.period_span (2026-07-15). Asserts the span is
    denormalised onto cell_fact, PAIRED with the per-cell period:
      * a UOB-style combined-header table (FY cols + 2H cols sharing 31-Dec END):
        cells in an FY column say 'FY', cells in a 2H column say '2H' — proving the
        col span (not the table/doc span) rides each cell;
      * an as_at (DD-Month date) column: cells say 'as_at';
      * a table with NO printed duration (title date + doc_period fallback):
        cell_fact.period_span is NULL.
    cell_fact.period_span must equal the owning column's period_span throughout, and
    v_cell.period_span must now read f.period_span directly (identical values)."""
    print("\nIntegration — MATERIALISED cell_fact.period_span (paired with period)")
    if not _SCHEMA.exists():
        check("schema_v7.sql exists", False, str(_SCHEMA))
        return

    # (1) UOB-style: FY / 2H columns (share 31-Dec END) + an as_at date column.
    gt_span = GTable(
        title="Income statement (synthetic span)",
        label_header="$m",
        columns=[GColumn(group=None, leaf="2025 $m"),      # FY2025
                 GColumn(group=None, leaf="2H25¹ $m"),     # 2H25 (same END as FY2025)
                 GColumn(group=None, leaf="As at 31 December 2025 $m")],  # as_at
        rows=[GRow(row_id="1", row_type="data", level=1, label="Net interest income",
                   values=[GCell(value="9,355"), GCell(value="4,611"), GCell(value="120,000")]),
              GRow(row_id="2", row_type="data", level=1, label="Fee income",
                   values=[GCell(value="100"), GCell(value="50"), GCell(value="8,000")])],
    )
    # (2) NULL-span table: title carries only an explicit as_at date? No — use a
    # table whose period comes from the doc fallback (no title/col period at all) so
    # table_span is NULL and every cell inherits NULL span.
    gt_null = GTable(
        title="Miscellaneous ratios",
        label_header="",
        columns=[GColumn(group=None, leaf="Ratio")],
        rows=[GRow(row_id="1", row_type="data", level=1, label="Some measure",
                   values=[GCell(value="1.23")])],
    )

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "syn_span.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA.read_text())
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('SYN3','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES "
                    "('SYN3','s1','1','Income',1,NULL,1),"
                    "('SYN3','s2','2','Ratios',2,NULL,2)")
        con.commit(); con.close()

        p1 = Path(td) / "span.json"; p1.write_text(json.dumps(Extraction(tables=[gt_span]).model_dump()))
        p2 = Path(td) / "null.json"; p2.write_text(json.dumps(Extraction(tables=[gt_null]).model_dump()))
        summary = load_units(str(db), "SYN3",
                             [dict(section_id="s1", pages=[1], parsed_path=str(p1)),
                              dict(section_id="s2", pages=[2], parsed_path=str(p2))])
        print(f"  load summary: {summary}")

        con = sqlite3.connect(db); cur = con.cursor()

        # cell_fact.period_span PAIRED with the owning column's span, per cell.
        cells = dict(cur.execute(
            "SELECT cf.col_id, cf.period_span FROM cell_fact cf "
            "JOIN row_dim rd USING(doc_id,table_id,row_id) "
            "WHERE rd.row_leaf_label='Net interest income' "
            "AND cf.table_id LIKE 's1%' ORDER BY cf.col_id").fetchall())
        check("cell col1 (FY column) period_span = 'FY'", cells.get(1) == "FY", str(cells))
        check("cell col2 (2H column, SAME END date) period_span = '2H'", cells.get(2) == "2H", str(cells))
        check("cell col3 (as_at date column) period_span = 'as_at'", cells.get(3) == "as_at", str(cells))
        # FY and 2H cells share the 31-Dec END date but the SPAN disambiguates them
        ends = dict(cur.execute(
            "SELECT cf.col_id, cf.period FROM cell_fact cf "
            "JOIN row_dim rd USING(doc_id,table_id,row_id) "
            "WHERE rd.row_leaf_label='Net interest income' AND cf.table_id LIKE 's1%'").fetchall())
        check("FY(col1) and 2H(col2) share END 2025-12-31, differ by materialised span",
              ends.get(1) == ends.get(2) == "2025-12-31" and cells.get(1) != cells.get(2),
              f"ends={ends} spans={cells}")

        # cell_fact.period_span EQUALS the owning col_dim.period_span everywhere.
        mism = cur.execute(
            "SELECT cf.col_id, cf.period_span, c.period_span FROM cell_fact cf "
            "JOIN col_dim c ON c.doc_id=cf.doc_id AND c.table_id=cf.table_id AND c.col_id=cf.col_id "
            "WHERE cf.table_id LIKE 's1%' AND IFNULL(cf.period_span,'')<>IFNULL(c.period_span,'')").fetchall()
        check("every s1 cell_fact.period_span == owning col_dim.period_span", mism == [], str(mism))

        # NULL-span table: no printed duration -> cell_fact.period_span NULL.
        null_spans = cur.execute(
            "SELECT DISTINCT period_span FROM cell_fact WHERE table_id LIKE 's2%'").fetchall()
        check("NULL-span table yields NULL cell_fact.period_span", null_spans == [(None,)], str(null_spans))

        # v_cell reads f.period_span directly (identical to the stored column).
        vmis = cur.execute(
            "SELECT v.col_id, v.period_span, cf.period_span FROM v_cell v "
            "JOIN cell_fact cf ON cf.doc_id=v.doc_id AND cf.table_id=v.table_id "
            "AND cf.row_id=v.row_id AND cf.col_id=v.col_id "
            "WHERE IFNULL(v.period_span,'')<>IFNULL(cf.period_span,'')").fetchall()
        check("v_cell.period_span == cell_fact.period_span (view reads f.period_span)", vmis == [], str(vmis))

        fk = cur.execute("PRAGMA foreign_key_check").fetchall()
        check("PRAGMA foreign_key_check clean", fk == [], str(fk))
        con.close()


def integration_row_period_test() -> None:
    """ROW-AXIS PERIOD MIRROR (2026-07-15). A UOB NPL-style table puts the period
    in the ROW axis (date rows 'Dec-25'/'Jun-25'/'Dec-24') with geographies in the
    COLUMNS. Asserts:
      * each date ROW yields row_dim.row_period + span 'as_at' (mirror of col_dim);
      * every cell in a date row carries that ROW date + as_at span, regardless of
        its geography column (the row-axis mirror of the column bug);
      * row lineage EXCLUDES the period (the 3 date rows converge to 'value'), while
        row_leaf_label stays verbatim;
      * a descriptive row carrying an incidental date ('Balance at 1 January 2025')
        is REFUSED (row_period NULL) and its cells fall to the table/doc period with
        NULL span — and its lineage keeps the verbatim label;
      * BOTH-axes conflict: period columns + a period row -> col wins + a warning."""
    print("\nIntegration — ROW-axis period mirror (UOB NPL-style date rows)")
    if not _SCHEMA.exists():
        check("schema_v7.sql exists", False, str(_SCHEMA))
        return

    # (1) date rows x geo columns (+ a 'Balance at' negative row)
    gt_rows = GTable(
        title="Non-performing loans by geography",
        label_header="$m",
        columns=[GColumn(group=None, leaf="Singapore"),
                 GColumn(group=None, leaf="Malaysia"),
                 GColumn(group=None, leaf="Total")],
        rows=[GRow(row_id="1", row_type="data", level=1, label="Dec-25",
                   values=[GCell(value="100"), GCell(value="40"), GCell(value="140")]),
              GRow(row_id="2", row_type="data", level=1, label="Jun-25",
                   values=[GCell(value="90"), GCell(value="35"), GCell(value="125")]),
              GRow(row_id="3", row_type="data", level=1, label="Dec-24",
                   values=[GCell(value="80"), GCell(value="30"), GCell(value="110")]),
              GRow(row_id="4", row_type="data", level=1, label="Balance at 1 January 2025",
                   values=[GCell(value="10"), GCell(value="5"), GCell(value="15")])],
    )
    # (2) BOTH axes carry a period (FY columns + a Jun-25 row) -> col wins + warning
    gt_both = GTable(
        title="Both-axes (synthetic)",
        label_header="$m",
        columns=[GColumn(group=None, leaf="2025 $m"), GColumn(group=None, leaf="2024 $m")],
        rows=[GRow(row_id="1", row_type="data", level=1, label="Jun-25",
                   values=[GCell(value="100"), GCell(value="90")])],
    )

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "syn_rows.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA.read_text())
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('SYN4','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES "
                    "('SYN4','s1','1','NPL',1,NULL,1),('SYN4','s2','2','Both',2,NULL,2)")
        con.commit(); con.close()

        p1 = Path(td) / "rows.json"; p1.write_text(json.dumps(Extraction(tables=[gt_rows]).model_dump()))
        p2 = Path(td) / "both.json"; p2.write_text(json.dumps(Extraction(tables=[gt_both]).model_dump()))
        summary = load_units(str(db), "SYN4",
                             [dict(section_id="s1", pages=[1], parsed_path=str(p1)),
                              dict(section_id="s2", pages=[2], parsed_path=str(p2))])
        print(f"  load summary tables={summary['tables']} cells={summary['cells']}")

        con = sqlite3.connect(db); con.execute("PRAGMA foreign_keys = ON"); cur = con.cursor()

        # date ROWS carry row_period + as_at span; the 'Balance at' row does NOT
        rp = {lbl: (per, sp, st) for lbl, per, sp, st in cur.execute(
            "SELECT row_leaf_label, row_period, period_span, period_start FROM row_dim "
            "WHERE table_id LIKE 's1%' ORDER BY row_id").fetchall()}
        check("row 'Dec-25' -> row_period 2025-12-31, span as_at, start NULL",
              rp.get("Dec-25") == ("2025-12-31", "as_at", None), str(rp.get("Dec-25")))
        check("row 'Jun-25' -> row_period 2025-06-30, span as_at",
              rp.get("Jun-25") == ("2025-06-30", "as_at", None), str(rp.get("Jun-25")))
        check("row 'Dec-24' -> row_period 2024-12-31, span as_at",
              rp.get("Dec-24") == ("2024-12-31", "as_at", None), str(rp.get("Dec-24")))
        check("row 'Balance at 1 January 2025' REFUSED -> row_period NULL (residual guard)",
              rp.get("Balance at 1 January 2025") == (None, None, None),
              str(rp.get("Balance at 1 January 2025")))

        # every cell in a date row carries the ROW date + as_at span, per geo column
        for lbl, want_p in (("Dec-25", "2025-12-31"), ("Jun-25", "2025-06-30"),
                            ("Dec-24", "2024-12-31")):
            cells = cur.execute(
                "SELECT cf.period, cf.period_span FROM cell_fact cf "
                "JOIN row_dim rd USING(doc_id,table_id,row_id) "
                "WHERE rd.row_leaf_label=? AND cf.table_id LIKE 's1%'", (lbl,)).fetchall()
            check(f"row {lbl!r}: all 3 geo cells -> period {want_p} span as_at",
                  cells == [(want_p, "as_at")] * 3, str(cells))

        # 'Balance at' row cells fall to the table/doc period, span NULL (not as_at)
        bal = cur.execute(
            "SELECT DISTINCT cf.period, cf.period_span FROM cell_fact cf "
            "JOIN row_dim rd USING(doc_id,table_id,row_id) "
            "WHERE rd.row_leaf_label='Balance at 1 January 2025' AND cf.table_id LIKE 's1%'").fetchall()
        check("'Balance at' cells -> doc/table period 2025-12-31, span NULL (table fallback)",
              bal == [("2025-12-31", None)], str(bal))

        # row lineage: the 3 date rows CONVERGE (period excluded), 'Balance at' does not
        date_lin = cur.execute(
            "SELECT DISTINCT rd.row_lineage_id FROM row_dim rd WHERE rd.table_id LIKE 's1%' "
            "AND rd.row_leaf_label IN ('Dec-25','Jun-25','Dec-24')").fetchall()
        check("3 date rows converge to ONE row_lineage_id (period excluded)",
              len(date_lin) == 1, str(date_lin))
        conv = cur.execute("SELECT lineage_key, depth FROM row_lineage WHERE row_lineage_id=?",
                           (date_lin[0][0],)).fetchone()
        check("converged date-row lineage = 'value' depth 1", conv == ("value", 1), str(conv))
        bal_lin = cur.execute(
            "SELECT rl.lineage_key FROM row_dim rd JOIN row_lineage rl "
            "ON rl.row_lineage_id=rd.row_lineage_id "
            "WHERE rd.table_id LIKE 's1%' AND rd.row_leaf_label='Balance at 1 January 2025'").fetchone()
        check("'Balance at' row keeps verbatim lineage (not excluded)",
              bal_lin == ("balance at 1 january 2025",), str(bal_lin))

        # BOTH-axes conflict: col wins + warning
        both_cells = cur.execute(
            "SELECT col_id, period, period_span FROM cell_fact WHERE table_id LIKE 's2%' "
            "ORDER BY col_id").fetchall()
        check("both-axes: col1 cell -> COL period 2025-12-31 span FY (col wins over row Jun-25)",
              both_cells[0] == (1, "2025-12-31", "FY"), str(both_cells))
        warn_hit = [w for w in summary["warnings"] if "period on both axes" in w and "col wins" in w]
        check("both-axes: a 'period on both axes ... col wins' warning was emitted",
              len(warn_hit) >= 1, str(summary["warnings"]))
        check("both-axes warning names col 2025-12-31 vs row 2025-06-30",
              any("col 2025-12-31 vs row 2025-06-30" in w for w in warn_hit), str(warn_hit))

        fk = cur.execute("PRAGMA foreign_key_check").fetchall()
        check("PRAGMA foreign_key_check clean", fk == [], str(fk))
        con.close()


def integration_year_header_inheritance_test() -> None:
    """ROW-PERIOD INHERITANCE (2026-07-15). A performance-by-segment table where the
    period is a bare-year SECTION HEADER row ('2025' with line items nested under it,
    then '2024' with its own children). Asserts:
      * a bare-year ROW parses as FY of that year (own parse, stored on row_dim);
      * line items UNDER the '2025' header inherit FY2025 (2025-12-31/FY) and those
        under '2024' inherit FY2024 (2024-12-31/FY) — sibling blocks isolated by the
        parent chain, '2024' children never resolve to 2025;
      * inheriting children store NO own row_period (they are line items), and keep
        their REAL lineage (year header excluded, so both blocks' 'Net interest
        income' converge to ONE row_lineage_id);
      * the year-header rows themselves are excluded from lineage ('value')."""
    print("\nIntegration — bare-year ROW section headers + inheritance (2025/2024 blocks)")
    if not _SCHEMA.exists():
        check("schema_v7.sql exists", False, str(_SCHEMA))
        return

    gt = GTable(
        title="Performance by business segment",
        label_header="$m",
        columns=[GColumn(group=None, leaf="Consumer"), GColumn(group=None, leaf="Total")],
        rows=[GRow(row_id=None, row_type="section_header", level=0, label="2025", values=[]),
              GRow(row_id="1", row_type="data", level=1, label="Net interest income",
                   values=[GCell(value="600"), GCell(value="900")]),
              GRow(row_id="2", row_type="data", level=1, label="Fee income",
                   values=[GCell(value="200"), GCell(value="300")]),
              GRow(row_id=None, row_type="section_header", level=0, label="2024", values=[]),
              GRow(row_id="3", row_type="data", level=1, label="Net interest income",
                   values=[GCell(value="550"), GCell(value="850")]),
              GRow(row_id="4", row_type="data", level=1, label="Fee income",
                   values=[GCell(value="180"), GCell(value="280")])],
    )

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "syn_yr.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA.read_text())
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('SYN5','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES ('SYN5','s1','1','Segments',1,NULL,1)")
        con.commit(); con.close()

        parsed = Path(td) / "yr.json"; parsed.write_text(json.dumps(Extraction(tables=[gt]).model_dump()))
        summary = load_units(str(db), "SYN5",
                             [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])
        print(f"  load summary cells={summary['cells']}")

        con = sqlite3.connect(db); con.execute("PRAGMA foreign_keys = ON"); cur = con.cursor()

        # bare-year headers: OWN parse = FY; line items store NO own row_period
        hdr = {lbl: (per, sp) for lbl, per, sp in cur.execute(
            "SELECT row_leaf_label, row_period, period_span FROM row_dim "
            "WHERE row_hierarchy=0").fetchall()}
        check("header '2025' OWN row_period 2025-12-31 span FY", hdr.get("2025") == ("2025-12-31", "FY"), str(hdr))
        check("header '2024' OWN row_period 2024-12-31 span FY", hdr.get("2024") == ("2024-12-31", "FY"), str(hdr))
        items = cur.execute(
            "SELECT DISTINCT row_period, period_span FROM row_dim WHERE row_hierarchy>=1").fetchall()
        check("line items store NO own row_period (inheritance is cell-only)",
              items == [(None, None)], str(items))

        # cells INHERIT the FY of their block; '2024' children never resolve to 2025
        def block_cells(label_row, parent_year):
            return cur.execute(
                "SELECT DISTINCT cf.period, cf.period_span FROM cell_fact cf "
                "JOIN row_dim rd USING(doc_id,table_id,row_id) "
                "JOIN row_dim p ON p.doc_id=rd.doc_id AND p.table_id=rd.table_id AND p.row_id=rd.row_parent "
                "WHERE rd.row_leaf_label=? AND p.row_leaf_label=?", (label_row, parent_year)).fetchall()
        check("'Net interest income' under 2025 -> 2025-12-31/FY (inherited)",
              block_cells("Net interest income", "2025") == [("2025-12-31", "FY")],
              str(block_cells("Net interest income", "2025")))
        check("'Net interest income' under 2024 -> 2024-12-31/FY (sibling isolation)",
              block_cells("Net interest income", "2024") == [("2024-12-31", "FY")],
              str(block_cells("Net interest income", "2024")))
        check("'Fee income' under 2024 -> 2024-12-31/FY",
              block_cells("Fee income", "2024") == [("2024-12-31", "FY")],
              str(block_cells("Fee income", "2024")))

        # lineage: both blocks' 'Net interest income' converge (year header excluded)
        nii_lin = cur.execute(
            "SELECT DISTINCT row_lineage_id FROM row_dim WHERE row_leaf_label='Net interest income'").fetchall()
        check("both blocks' 'Net interest income' converge to ONE row_lineage_id",
              len(nii_lin) == 1, str(nii_lin))
        nii_key = cur.execute("SELECT lineage_key, depth FROM row_lineage WHERE row_lineage_id=?",
                              (nii_lin[0][0],)).fetchone()
        check("converged line-item lineage = 'net interest income' depth 1 (year header excluded)",
              nii_key == ("net interest income", 1), str(nii_key))
        yr_lin = cur.execute(
            "SELECT DISTINCT rl.lineage_key FROM row_dim rd JOIN row_lineage rl "
            "ON rl.row_lineage_id=rd.row_lineage_id WHERE rd.row_hierarchy=0").fetchall()
        check("year-header rows excluded from lineage -> 'value'", yr_lin == [("value",)], str(yr_lin))

        # The NESTED shape must keep resolving via the ANCESTOR rung, not the
        # sibling-banner rung added later — this is the regression guard proving
        # the new rung did not steal a case the old one already answered.
        srcs = cur.execute("SELECT DISTINCT period_source FROM cell_fact").fetchall()
        check("nested blocks still resolve via the ANCESTOR rung (period_source='row')",
              srcs == [("row",)], str(srcs))

        fk = cur.execute("PRAGMA foreign_key_check").fetchall()
        check("PRAGMA foreign_key_check clean", fk == [], str(fk))
        con.close()


# ===========================================================================
# 9) SIBLING PERIOD BANNERS — row_period_banners() pre-pass, pure, no DB
# ===========================================================================
def row_period_banner_tests() -> None:
    """The banner is a SIBLING of the rows it heads, not an ancestor. These pin
    the predicate (valueless + section_header/sub_header) and the stack rules."""
    print("\nrow_period_banners — pure pre-pass")
    from stage2_load.load_v7 import row_period_banners

    def R(label, level, row_type="data", n_values=0):
        return GRow(row_id=None, row_type=row_type, level=level, label=label,
                    values=[GCell(value="1") for _ in range(n_values)])

    # (a) the DBS shape: banner and data rows ALL at the same level
    got = row_period_banners([
        R("Selected income statement items", 0, "section_header"),
        R("2nd Half 2025", 1, "section_header"), R("Net interest income", 1),
        R("1st Half 2025", 1, "section_header"), R("Net interest income", 1)])
    check("flat siblings: each block gets its OWN banner period+span",
          got == [None, None, ("2025-12-31", "2H"), None, ("2025-06-30", "1H")], str(got))

    # (b) a VALUED period row is a balance row, never a scope
    got = row_period_banners([R("At 1 January 2026", 1, "data", n_values=3),
                              R("Additions", 1, "data", n_values=3)])
    check("valued 'At 1 January 2026' does NOT scope the rows after it",
          got == [None, None], str(got))

    # (c) a plain (non-period) header must not pop a live banner
    got = row_period_banners([R("At 31 December 2025", 0, "section_header"),
                              R("Gross loans", 1, "section_header"), R("Singapore", 2)])
    check("plain sub-header does NOT pop the live banner",
          got[2] == ("2025-12-31", "as_at"), str(got))

    # (d) a deeper banner scopes its own block; a later shallower BANNER unwinds
    got = row_period_banners([R("Year 2025", 0, "section_header"), R("NII", 1),
                              R("At 31 Dec 2025", 2, "section_header"), R("Sub", 3),
                              R("1st Half 2025", 1, "section_header"), R("NII", 1)])
    check("deeper banner scopes its own block", got[3] == ("2025-12-31", "as_at"), str(got))
    check("a later shallower BANNER unwinds the stack",
          got[5] == ("2025-06-30", "1H"), str(got))

    # (e) a footnote is never a scope
    got = row_period_banners([R("2nd Half 2025", 1, "note"), R("NII", 1)])
    check("a 'note' row is never a banner", got == [None, None], str(got))

    # (f) UOB 'Dec 24': the banner sits DEEPER than the rows it heads, so the
    #     read must not be level-filtered
    got = row_period_banners([R("Dec 24", 1, "section_header"),
                              R("Cash and balances", 0), R("Loans to customers", 0)])
    check("banner DEEPER than its data rows still scopes them (UOB Dec-24)",
          got[1] == ("2024-12-31", "as_at") and got[2] == ("2024-12-31", "as_at"), str(got))

    # (g) a bare-year banner may not resolve past the reporting date
    got = row_period_banners([R("2026", 0, "section_header"), R("NII", 1)], "2026-06-30")
    check("bare-year banner clamped to doc_period (not 31 Dec 2026)",
          got[1] == ("2026-06-30", None) or got[1][0] == "2026-06-30", str(got))


def integration_flat_period_block_test() -> None:
    """End-to-end on the DBS shape: three period blocks, banner and data rows all
    at level 1 sharing one parent. Before the sibling rung this collapsed to
    doc_period for every cell."""
    print("\nIntegration — FLAT period blocks (banner is a sibling, not an ancestor)")
    if not _SCHEMA.exists():
        check("schema_v7.sql exists", False, str(_SCHEMA))
        return

    def data(label, a, b):
        return GRow(row_id=None, row_type="data", level=1, label=label,
                    values=[GCell(value=a), GCell(value=b)])
    gt = GTable(
        title="Performance by business segments",
        label_header="$m",
        columns=[GColumn(group=None, leaf="Consumer"), GColumn(group=None, leaf="Total")],
        rows=[GRow(row_id=None, row_type="section_header", level=0,
                   label="Selected income statement items", values=[]),
              GRow(row_id=None, row_type="section_header", level=1, label="2nd Half 2025", values=[]),
              data("Net interest income", "600", "900"), data("Fee income", "200", "300"),
              GRow(row_id=None, row_type="section_header", level=1, label="1st Half 2025", values=[]),
              data("Net interest income", "550", "850"), data("Fee income", "180", "280"),
              GRow(row_id=None, row_type="section_header", level=1, label="Year 2024", values=[]),
              data("Net interest income", "500", "800"), data("Fee income", "150", "250")],
    )
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "syn_flat.db"
        con = sqlite3.connect(db); con.executescript(_SCHEMA.read_text())
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('SYN6','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES ('SYN6','s1','1','Segments',1,NULL,1)")
        con.commit(); con.close()
        parsed = Path(td) / "flat.json"
        parsed.write_text(json.dumps(Extraction(tables=[gt]).model_dump()))
        load_units(str(db), "SYN6", [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])

        con = sqlite3.connect(db); cur = con.cursor()
        got = sorted(cur.execute("SELECT DISTINCT period, period_span, period_source "
                                 "FROM cell_fact").fetchall())
        check("three blocks resolve to THREE distinct (period, span) pairs",
              got == sorted([("2024-12-31", "FY", "row_banner"),
                             ("2025-06-30", "1H", "row_banner"),
                             ("2025-12-31", "2H", "row_banner")]), str(got))
        check("period_source is 'row_banner' (not 'doc') on every cell",
              {g[2] for g in got} == {"row_banner"}, str(got))
        items = cur.execute("SELECT DISTINCT row_period, period_span FROM row_dim "
                            "WHERE row_hierarchy>=1 AND row_leaf_label LIKE '%income%'").fetchall()
        check("data rows keep NO own row_period (inheritance stays cell-only)",
              items == [(None, None)], str(items))
        nii = cur.execute("SELECT DISTINCT row_lineage_id FROM row_dim "
                          "WHERE row_leaf_label='Net interest income'").fetchall()
        check("all three blocks' 'Net interest income' converge to ONE lineage",
              len(nii) == 1, str(nii))
        check("PRAGMA foreign_key_check clean",
              cur.execute("PRAGMA foreign_key_check").fetchall() == [])
        con.close()


def integration_valued_period_row_no_propagate_test() -> None:
    """The guard for Changes in Equity / Level 3 movements: an opening balance
    row OWNS its figures and must never scope the movement rows beneath it."""
    print("\nIntegration — a VALUED period row must not scope its followers")
    if not _SCHEMA.exists():
        check("schema_v7.sql exists", False, str(_SCHEMA))
        return

    gt = GTable(
        title="Statement of changes in equity for the half year ended 30 June 2026",
        label_header="$m",
        columns=[GColumn(group=None, leaf="Share capital"), GColumn(group=None, leaf="Total")],
        rows=[GRow(row_id=None, row_type="data", level=1, label="At 1 January 2026",
                   values=[GCell(value="100"), GCell(value="900")]),
              GRow(row_id=None, row_type="data", level=1, label="Net profit",
                   values=[GCell(value="10"), GCell(value="90")]),
              GRow(row_id=None, row_type="data", level=1, label="Dividends",
                   values=[GCell(value="-5"), GCell(value="-45")])],
    )
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "syn_bal.db"
        con = sqlite3.connect(db); con.executescript(_SCHEMA.read_text())
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('SYN7','Synthetic Bank','financial_stmt','2026-06-30')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES ('SYN7','s1','1','Equity',1,NULL,1)")
        con.commit(); con.close()
        parsed = Path(td) / "bal.json"
        parsed.write_text(json.dumps(Extraction(tables=[gt]).model_dump()))
        load_units(str(db), "SYN7", [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])

        con = sqlite3.connect(db); cur = con.cursor()
        opening = cur.execute(
            "SELECT DISTINCT cf.period, cf.period_source FROM cell_fact cf "
            "JOIN row_dim rd USING(doc_id,table_id,row_id) "
            "WHERE rd.row_leaf_label='At 1 January 2026'").fetchall()
        check("the opening-balance row keeps its OWN period via the row rung",
              opening == [("2026-01-01", "row")], str(opening))
        movers = cur.execute(
            "SELECT DISTINCT cf.period, cf.period_source FROM cell_fact cf "
            "JOIN row_dim rd USING(doc_id,table_id,row_id) "
            "WHERE rd.row_leaf_label IN ('Net profit','Dividends')").fetchall()
        check("movement rows do NOT inherit 2026-01-01 from it",
              all(p != "2026-01-01" for p, _ in movers), str(movers))
        check("movement rows are never sourced 'row_banner'",
              all(s != "row_banner" for _, s in movers), str(movers))
        con.close()


if __name__ == "__main__":
    grammar_tests()
    footnote_unit_tests()
    vocabulary_matrix_tests()
    integration_test()
    integration_combined_unit_test()
    integration_cell_span_test()
    integration_row_period_test()
    integration_year_header_inheritance_test()
    row_period_banner_tests()
    integration_flat_period_block_test()
    integration_valued_period_row_no_propagate_test()
    print(f"\n{'ALL PASS' if _FAILS == 0 else str(_FAILS) + ' FAILED'}")
    raise SystemExit(1 if _FAILS else 0)
