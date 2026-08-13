"""test_toc_match.py — plain check() tests for the TOC-branch arranger.

No pytest. Run with base python:
    python findociq/pipeline/discover/section/test_toc_match.py

Uses small synthetic candidates.csv / regions.csv / toc.json fixtures written to
a temp dir. Proves the three spec-mandated behaviours:
  (a) a DATE-line candidate typographically identical to a header is rejected
      because it is not in the printed TOC;
  (b) carry-over of the current section across a page that has no boundary;
  (c) nearest-above wins when two sections share a page.
Plus: deepest/most-dotted tiebreak, de-glue/fuzzy matching, and PREAMBLE.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import toc_match as tm

_FAILS = []


def check(cond, msg):
    status = "ok  " if cond else "FAIL"
    print(f"[{status}] {msg}")
    if not cond:
        _FAILS.append(msg)


# --- fixtures -------------------------------------------------------------

TOC = {"sections": [
    {"section_id": "4",   "title": "Key Metrics"},
    {"section_id": "5.1", "title": "Disclosure of G-SIB Indicators"},
    {"section_id": "6.1", "title": "Overview of Risk Management"},
    # deepest-wins fixture: same title at two depths
    {"section_id": "18",   "title": "Credit Risk"},
    {"section_id": "18.1", "title": "Credit Risk"},
]}

# candidates.csv rows (reading order). font_size/bold/alignment DELIBERATELY
# identical between the real header (p5 y50) and the date line (p5 y400): the
# arranger must reject the date line on TOC-membership, not typography.
CANDIDATES = [
    # page, y0, x0, text, font_size, bold, alignment, is_dateish
    (5,  50.0, 40.0, "4 Key Metrics",                             12.0, True, "left", False),
    (5, 400.0, 40.0, "For the Quarter ended 31 December 2024",    12.0, True, "left", True),
    # glued title text -> the printed NUMBER carries the match (number-only rule)
    (7,  40.0, 40.0, "5.1 Disclosure of G-SIBIndicators",         12.0, True, "left", False),
    (7, 300.0, 40.0, "6.1 Overview of Risk Management",           12.0, True, "left", False),
]

# regions.csv rows.
REGIONS = [
    # page, table_idx, x0, y0, x1, y1
    (3, 0,  40.0,  60.0, 500.0, 200.0),   # (PREAMBLE) before any boundary
    (5, 0,  40.0, 100.0, 500.0, 300.0),   # after "Key Metrics" -> 4
    (5, 1,  40.0, 450.0, 500.0, 600.0),   # BELOW the date line -> still 4 (date rejected)
    (6, 0,  40.0,  80.0, 500.0, 300.0),   # (carry-over) page 6 has no boundary -> 4
    (7, 0,  40.0, 100.0, 500.0, 250.0),   # between 5.1 and 6.1 -> 5.1 (nearest-above)
    (7, 1,  40.0, 350.0, 500.0, 500.0),   # below 6.1 -> 6.1
]


def _write_fixtures(root):
    tag_dir = os.path.join(root, "synth")
    os.makedirs(tag_dir, exist_ok=True)
    with open(os.path.join(tag_dir, "candidates.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["page", "y0", "x0", "text", "font_size", "bold", "alignment", "is_dateish"])
        w.writerows(CANDIDATES)
    with open(os.path.join(tag_dir, "regions.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["page", "table_idx", "x0", "y0", "x1", "y1"])
        w.writerows(REGIONS)
    toc_path = os.path.join(root, "toc.json")
    with open(toc_path, "w") as fh:
        json.dump(TOC, fh)
    return toc_path


# --- tests ----------------------------------------------------------------

def run():
    root = tempfile.mkdtemp(prefix="toc_match_test_")
    toc_path = _write_fixtures(root)

    out_path = tm.attribute_from_toc("synth", toc_path, out_root=root)
    with open(out_path, newline="") as fh:
        rows = list(csv.DictReader(fh))

    # index by (page, table_idx)
    got = {(int(r["page"]), int(r["table_idx"])): r for r in rows}

    # contract: one row per region, exact columns, source literal
    check(len(rows) == len(REGIONS), f"one row per region ({len(rows)} == {len(REGIONS)})")
    check(list(rows[0].keys()) == ["page", "table_idx", "section_id", "section_title", "source"],
          "exact column order page,table_idx,section_id,section_title,source")
    check(all(r["source"] == "printed_toc" for r in rows), "source is literal 'printed_toc'")

    # (a) date-line rejection: region BELOW the date line still resolves to 4.
    check(got[(5, 1)]["section_id"] == "4",
          "(a) region below typography-identical date line stays section 4 (date rejected)")
    # and prove the date text itself matches NO printed section
    sections = tm._load_sections(toc_path)
    check(tm.match_candidate("For the Quarter ended 31 December 2024", sections) is None,
          "(a) date-line text matches no printed-TOC section")

    # (b) carry-over across a boundary-less page
    check(got[(6, 0)]["section_id"] == "4",
          "(b) page 6 (no boundary) carries section 4 from page 5")

    # (c) nearest-above wins when two sections share a page
    check(got[(7, 0)]["section_id"] == "5.1",
          "(c) region between 5.1 and 6.1 attributes to nearest-above 5.1")
    check(got[(7, 1)]["section_id"] == "6.1",
          "(c) region below 6.1 attributes to 6.1")

    # basic happy path + fuzzy/deglue match
    check(got[(5, 0)]["section_id"] == "4", "region under 'Key Metrics' -> 4")
    check(got[(7, 0)]["section_title"] == "Disclosure of G-SIB Indicators"
          and got[(7, 0)]["section_id"] == "5.1",
          "glued title still matches 5.1 via its printed NUMBER")

    # PREAMBLE fallback
    check(got[(3, 0)]["section_id"] == "PREAMBLE",
          "region before any boundary -> PREAMBLE")

    # number-only matching: the number is decisive, an unnumbered heading is a
    # SUB-HEADING (kept by the caller, never a boundary), and a wrapped title
    # with its number intact still matches exactly.
    m = tm.match_candidate("18.1 Credit Risk", sections)
    check(m is not None and m[0] == "18.1",
          "printed number 18.1 matches its TOC id exactly")
    check(tm.match_candidate("Credit Risk", sections) is None,
          "unnumbered heading -> None (kept as sub-heading, not a boundary)")
    check(tm.match_candidate("7.2 Main Sources of Differences between "
                             "Financial Statements",
                             sections + [("7.2", "Main Sources of Differences "
                                          "between Regulatory Exposure Amounts "
                                          "and Carrying Amounts in Financial "
                                          "Statements",
                                          tm.normalize_title("whatever"))]) is not None,
          "wrapped/truncated title with number intact still matches (the 7.2 case)")

    print()
    if _FAILS:
        print(f"{len(_FAILS)} FAILED")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    run()
