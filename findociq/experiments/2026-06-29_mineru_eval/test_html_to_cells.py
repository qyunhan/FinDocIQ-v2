"""Test html_to_cells against the real Gemini sample (samples/gemini_ocbc_nsfr.html).

Asserts the pinned contract facts AND the two defensive rules (header rowspan tolerated;
leading line-number column mapped to line_no, not a data column).
Run: python3 test_html_to_cells.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from html_to_cells import parse_html, _parse_period

SAMPLES = [("2.5-flash", "gemini_ocbc_nsfr.html"),
           ("3.5-flash", "gemini35_ocbc_nsfr.html")]

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return cond

def run(tag, fname):
    path = os.path.join(HERE, "samples", fname)
    if not os.path.exists(path):
        print(f"\n[{tag}] sample missing ({fname}) — skipped")
        return True
    print(f"\n[{tag}] {fname}")
    tables = parse_html(open(path).read())
    return checks(tables)

def checks(tables):
    ok = True
    ok &= check("two tables (Dec + Sep)", len(tables) == 2, len(tables))
    t0, t1 = tables
    ok &= check("table0 period = 2025-12-31", t0.period == "2025-12-31", t0.period)
    ok &= check("table1 period = 2025-09-30", t1.period == "2025-09-30", t1.period)

    # header tolerated (rowspan OR flat) -> 5 value columns (No Maturity,<6m,6-12m,>=1yr,Weighted)
    ok &= check("5 value columns", len(t0.cols) == 5, [c.leaf_label for c in t0.cols])
    ok &= check("col group preserved", t0.cols[0].group == "Unweighted value by residual maturity", t0.cols[0].group)
    ok &= check("last col = Weighted value", t0.cols[-1].leaf_label == "Weighted value", t0.cols[-1].leaf_label)

    # 35 tbody rows = 34 numbered + the "RSF Item" section band (empty line_no, level 1)
    ok &= check("35 body rows total", len(t0.rows) == 35, len(t0.rows))
    ok &= check("34 numbered rows", sum(1 for r in t0.rows if r.line_no and r.line_no.isdigit()) == 34,
                sum(1 for r in t0.rows if r.line_no and r.line_no.isdigit()))
    ok &= check("'RSF Item' band present (no line_no, level 1)",
                any(r.label == "RSF Item" and not r.line_no and r.level == 1 for r in t0.rows))
    ok &= check("max indentation level = 3", max(r.level for r in t0.rows) == 3, max(r.level for r in t0.rows))

    # defensive rule 2: line numbers captured (separate col OR label prefix), label separated
    r2 = next(r for r in t0.rows if r.line_no == "2")
    ok &= check("line 2 label='Regulatory capital'", r2.label == "Regulatory capital", r2.label)
    ok &= check("line 2 weighted value = 59082 reported",
                r2.cells[-1].value_num == 59082 and r2.cells[-1].cell_state == "reported",
                (r2.cells[-1].value_num, r2.cells[-1].cell_state))

    # dash -> null
    r16 = next(r for r in t0.rows if r.line_no == "16")
    ok &= check("line 16 first value dash -> null",
                r16.cells[0].cell_state == "null", r16.cells[0].cell_state)

    # NSFR (%) total
    r34 = next(r for r in t0.rows if r.line_no == "34")
    ok &= check("line 34 NSFR(%)=114, kind=total",
                r34.cells[-1].value_num == 114 and r34.kind == "total",
                (r34.cells[-1].value_num, r34.kind))

    # row_parent derivation: line 2 (level2) -> parent line 1 'Capital:' (level1)
    ok &= check("line 2 parent = 'Capital:'",
                r2.parent_idx is not None and t0.rows[r2.parent_idx].label == "Capital:",
                t0.rows[r2.parent_idx].label if r2.parent_idx is not None else None)
    # line 21 (level3) -> parent line 20 (level2)
    r21 = next(r for r in t0.rows if r.line_no == "21")
    ok &= check("line 21 (lvl3) parent line_no = 20",
                r21.parent_idx is not None and t0.rows[r21.parent_idx].line_no == "20",
                t0.rows[r21.parent_idx].line_no if r21.parent_idx is not None else None)

    # shading captured somewhere (model-dependent volume; >=1 grey cell expected)
    shaded = sum(c.is_shade for r in t0.rows for c in r.cells)
    ok &= check("shading captured (>=1 grey cell)", shaded >= 1, shaded)

    # validation: no column-width warnings
    ok &= check("no column-width warnings", len(t0.warnings) == 0, t0.warnings)
    return ok


def run_parse_period():
    print("\n[_parse_period]")
    ok = True
    ok &= check("full month: '31 December 2023' -> 2023-12-31",
                _parse_period(["As at 31 December 2023"]) == "2023-12-31",
                _parse_period(["As at 31 December 2023"]))
    ok &= check("abbrev month: '31 Dec 2023' -> 2023-12-31",
                _parse_period(["In S$ million, as at 31 Dec 2023"]) == "2023-12-31",
                _parse_period(["In S$ million, as at 31 Dec 2023"]))
    ok &= check("abbrev 'Sept' variant: '30 Sept 2024' -> 2024-09-30",
                _parse_period(["30 Sept 2024"]) == "2024-09-30",
                _parse_period(["30 Sept 2024"]))
    ok &= check("non-date word before year stays None",
                _parse_period(["12 branches opened 2024"]) is None,
                _parse_period(["12 branches opened 2024"]))
    return ok


allok = True
for tag, fname in SAMPLES:
    allok &= run(tag, fname)
allok &= run_parse_period()
print("\nRESULT:", "ALL PASS ✓" if allok else "FAILURES ✗")
sys.exit(0 if allok else 1)
