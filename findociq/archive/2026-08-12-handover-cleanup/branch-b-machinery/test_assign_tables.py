"""test_assign_tables.py — plain check() tests for the shared deterministic
table->section assigner (assign_tables.assign / leaf_level).

No pytest. Run with base python:
    python3 findociq/pipeline/discover/section/test_assign_tables.py

Synthetic boundaries+regions fixtures prove the amendment's rules:
  (a) deepest-heading-above (subsection printed under its parent governs its own
      table);
  (b) a continued ANCESTOR banner does NOT steal the cursor;
  (c) a continued NON-ancestor (sibling) DOES set the cursor;
  (d) PREAMBLE before any boundary;
  (e) carry-over across a boundary-less page;
  (f) boundary-before-region at equal (page, y0);
plus leaf_level() and the same-position deeper-level-wins tiebreak.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import assign_tables as at

_FAILS = []


def check(cond, msg):
    status = "ok  " if cond else "FAIL"
    print(f"[{status}] {msg}")
    if not cond:
        _FAILS.append(msg)


def _b(section_id, page, y0, level=None, continued=False, title=None):
    return dict(
        section_id=section_id,
        section_title=title if title is not None else f"Section {section_id}",
        level=at.leaf_level(section_id) if level is None else level,
        page=page,
        y0=y0,
        continued=continued,
    )


def _r(page, table_idx, y0):
    # x0/x1/y1 present to prove regions.csv-shaped rows are accepted.
    return dict(page=page, table_idx=table_idx, x0=40.0, y0=y0, x1=500.0, y1=y0 + 100.0)


def _by_key(out):
    return {(o["page"], o["table_idx"]): o for o in out}


def run():
    # --- leaf_level ------------------------------------------------------
    check(at.leaf_level("2") == 1, "leaf_level('2') == 1")
    check(at.leaf_level("2.8") == 2, "leaf_level('2.8') == 2")
    check(at.leaf_level("2.21.3") == 3, "leaf_level('2.21.3') == 3")
    check(at.leaf_level("A.1") == 2, "leaf_level('A.1') == 2 (lettered-numbered)")
    check(at.leaf_level("PREAMBLE") == 1, "leaf_level('PREAMBLE') == 1 (non-numbered)")

    # --- (a) deepest-heading-above --------------------------------------
    # "2"(p13,y97,lvl1), "2.8"(p13,y97.5,lvl2), "2.9"(p13,y601,lvl2)
    boundaries = [
        _b("2", 13, 97.0), _b("2.8", 13, 97.5), _b("2.9", 13, 601.0),
    ]
    regions = [_r(13, 0, 382.0), _r(13, 1, 650.0)]
    got = _by_key(at.assign(boundaries, regions))
    check(got[(13, 0)]["section_id"] == "2.8",
          "(a) region p13 y382 under 2.8 (deepest heading above) -> 2.8")
    check(got[(13, 1)]["section_id"] == "2.9",
          "(a) region p13 y650 below 2.9 -> 2.9")

    # --- (b) continued ANCESTOR does not steal ---------------------------
    # cursor at 2.21.3 from p30; p31 top has only "2 ... (continued)".
    boundaries = [
        _b("2.21.3", 30, 500.0),
        _b("2", 31, 97.0, continued=True, title="Material Accounting Policy (continued)"),
    ]
    regions = [_r(31, 0, 200.0)]
    got = _by_key(at.assign(boundaries, regions))
    check(got[(31, 0)]["section_id"] == "2.21.3",
          "(b) continued ancestor '2' does NOT steal; region stays 2.21.3")

    # --- (c) continued NON-ancestor (sibling) DOES set -------------------
    # cursor 2.10; then "2.11.3 (continued)" -> region after it -> 2.11.3.
    boundaries = [
        _b("2.10", 40, 100.0),
        _b("2.11.3", 41, 90.0, continued=True, title="Measurement (continued)"),
    ]
    regions = [_r(41, 0, 300.0)]
    got = _by_key(at.assign(boundaries, regions))
    check(got[(41, 0)]["section_id"] == "2.11.3",
          "(c) continued non-ancestor sibling '2.11.3' DOES set cursor")

    # --- (d) PREAMBLE before any boundary --------------------------------
    boundaries = [_b("3", 5, 100.0)]
    regions = [_r(3, 0, 60.0), _r(5, 0, 200.0)]
    got = _by_key(at.assign(boundaries, regions))
    check(got[(3, 0)]["section_id"] == "PREAMBLE"
          and got[(3, 0)]["section_title"] == "PREAMBLE",
          "(d) region before any boundary -> PREAMBLE/PREAMBLE")
    check(got[(5, 0)]["section_id"] == "3", "(d) region after boundary -> 3")

    # --- (e) carry-over across a boundary-less page ----------------------
    boundaries = [_b("4", 5, 50.0)]
    regions = [_r(6, 0, 80.0)]   # page 6 has NO boundary
    got = _by_key(at.assign(boundaries, regions))
    check(got[(6, 0)]["section_id"] == "4",
          "(e) boundary-less page 6 carries section 4 from page 5")

    # --- (f) boundary-before-region at equal (page, y0) ------------------
    boundaries = [_b("7", 9, 250.0)]
    regions = [_r(9, 0, 250.0)]   # SAME (page, y0) as the boundary
    got = _by_key(at.assign(boundaries, regions))
    check(got[(9, 0)]["section_id"] == "7",
          "(f) heading at same (page,y0) as its table governs it -> 7")

    # --- same-position deeper-level-wins tiebreak ------------------------
    # "2"(lvl1) and "2.8"(lvl2) at IDENTICAL (page,y0): deeper wins the cursor.
    boundaries = [_b("2.8", 13, 97.0), _b("2", 13, 97.0)]  # order swapped on purpose
    regions = [_r(13, 0, 400.0)]
    got = _by_key(at.assign(boundaries, regions))
    check(got[(13, 0)]["section_id"] == "2.8",
          "same (page,y0): deeper level 2.8 crossed last, wins cursor over 2")

    # --- output is one row per region, in input order --------------------
    boundaries = [_b("1", 1, 10.0)]
    regions = [_r(1, 2, 100.0), _r(1, 0, 50.0), _r(1, 1, 75.0)]
    out = at.assign(boundaries, regions)
    check(len(out) == 3, "one output row per region")
    check([o["table_idx"] for o in out] == [2, 0, 1],
          "output preserves region INPUT order (not sorted)")

    print()
    if _FAILS:
        print(f"{len(_FAILS)} FAILED")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    run()
