"""Plain check()-style schema test for candidates.py output. Runs in BASE
python (no paddlex import, no pytest) so it can be committed and executed
before any .venv-paddle capture run exists.

Validates, for any already-emitted <out_root>/<tag>/{candidates,regions}.csv:
  - candidates.csv columns exactly: page,y0,x0,text,font_size,bold,alignment,
    is_dateish
  - regions.csv columns exactly: page,table_idx,x0,y0,x1,y1
  - table_idx is contiguous per page starting at 0
  - is_dateish parses as a bool
  - alignment in {left,center,right}
  - every regions row has x1>x0 and y1>y0

If no CSVs are found under the default (or given) out_root, the checks are
skipped with a clear message rather than failing — this lets the module be
written/committed before the paddle capture is run.

Usage:
  python3 findociq/pipeline/discover/section/test_candidates.py [out_root]
"""
from __future__ import annotations

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # pipeline/ on path
from stage1_extract.toc.candidates import merge_twin_candidates  # noqa: E402

_DEFAULT_OUT = os.path.join(
    HERE, "..", "..", "..", "experiments", "2026-07-07_paddleocr_eval", "outputs")

CANDIDATE_COLS = ["page", "y0", "x0", "text", "font_size", "bold", "alignment", "is_dateish"]
REGION_COLS = ["page", "table_idx", "x0", "y0", "x1", "y1"]
ALIGNMENTS = {"left", "center", "right"}

_PASS = _FAIL = _SKIP = 0


def check(label, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def skip(label):
    global _SKIP
    _SKIP += 1
    print(f"  skip {label}")


def _read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _parse_bool(s):
    return s.strip().lower() in ("true", "false", "1", "0")


def check_candidates_csv(path):
    print(f"-- candidates.csv: {path}")
    rows = _read_csv(path)
    if not rows:
        skip("candidates.csv has no rows (nothing to validate beyond header)")
        with open(path, newline="") as fh:
            header = next(csv.reader(fh), [])
        check("candidates.csv header exact", header == CANDIDATE_COLS)
        return
    header = list(rows[0].keys())
    check("candidates.csv columns exact", header == CANDIDATE_COLS)

    all_bool = all(_parse_bool(r["is_dateish"]) for r in rows)
    check("is_dateish parses as bool for every row", all_bool)

    all_align = all(r["alignment"] in ALIGNMENTS for r in rows)
    check("alignment in {left,center,right} for every row", all_align)

    all_bold_bool = all(_parse_bool(r["bold"]) for r in rows)
    check("bold parses as bool for every row", all_bold_bool)

    all_pages_int = True
    for r in rows:
        try:
            int(r["page"])
        except ValueError:
            all_pages_int = False
            break
    check("page parses as int for every row", all_pages_int)


def check_regions_csv(path):
    print(f"-- regions.csv: {path}")
    rows = _read_csv(path)
    if not rows:
        skip("regions.csv has no rows (nothing to validate beyond header)")
        with open(path, newline="") as fh:
            header = next(csv.reader(fh), [])
        check("regions.csv header exact", header == REGION_COLS)
        return
    header = list(rows[0].keys())
    check("regions.csv columns exact", header == REGION_COLS)

    all_box_ok = True
    for r in rows:
        try:
            if not (float(r["x1"]) > float(r["x0"]) and float(r["y1"]) > float(r["y0"])):
                all_box_ok = False
                break
        except ValueError:
            all_box_ok = False
            break
    check("every region has x1>x0 and y1>y0", all_box_ok)

    # table_idx contiguous per page, starting at 0
    by_page: dict[str, list[int]] = {}
    for r in rows:
        by_page.setdefault(r["page"], []).append(int(r["table_idx"]))
    contiguous = True
    for pg, idxs in by_page.items():
        if sorted(idxs) != list(range(len(idxs))):
            contiguous = False
            break
    check("table_idx contiguous per page from 0", contiguous)


def _row(page, y0, text, is_dateish=False):
    return dict(page=page, y0=y0, x0=0.0, text=text, font_size=10.0,
                bold=False, alignment="left", is_dateish=is_dateish)


def check_merge_twin_candidates():
    print("-- merge_twin_candidates (pure-function fixtures)")

    # (a) spaced+glued pair at the same y merges to the spaced (better) text.
    spaced = _row(13, 100.0, "2.8 Property, Plant and Equipment")
    glued = _row(13, 101.5, "2.8Property,PlantandEquipment")
    merged, n = merge_twin_candidates([spaced, glued])
    check("(a) spaced+glued pair collapses to one row", len(merged) == 1)
    check("(a) n_merged == 1", n == 1)
    if merged:
        check("(a) kept text is the better-spaced (spaced) variant",
              merged[0]["text"] == "2.8 Property, Plant and Equipment")

    # (b) two DIFFERENT headings at a similar y (two-column layout) are NOT merged.
    left_col = _row(30, 200.0, "1. General")
    right_col = _row(30, 201.0, "2.2 Basis of Consolidation")
    merged_b, n_b = merge_twin_candidates([left_col, right_col])
    check("(b) dissimilar same-page/near-y headings are kept separate", len(merged_b) == 2)
    check("(b) n_merged == 0", n_b == 0)

    # (c) is_dateish ORs across the merged pair (either side flags it).
    dateless = _row(5, 50.0, "3.1 Income Statement", is_dateish=False)
    dateish = _row(5, 51.0, "3.1Income Statement", is_dateish=True)
    merged_c, n_c = merge_twin_candidates([dateless, dateish])
    check("(c) exactly one twin-merge", len(merged_c) == 1 and n_c == 1)
    if merged_c:
        check("(c) is_dateish is OR'd across the merged pair",
              bool(merged_c[0]["is_dateish"]) is True)

    # sanity: rows on different pages never merge, even with identical text/y0.
    p1 = _row(1, 100.0, "Notes to the Financial Statements")
    p2 = _row(2, 100.0, "Notes to the Financial Statements")
    merged_d, n_d = merge_twin_candidates([p1, p2])
    check("(sanity) same text/y0 on different pages does not merge", len(merged_d) == 2)
    check("(sanity) n_merged == 0 across pages", n_d == 0)


def main():
    check_merge_twin_candidates()

    out_root = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_OUT
    out_root = os.path.abspath(out_root)

    if not os.path.isdir(out_root):
        print(f"[skip] out_root does not exist yet: {out_root}")
        print("       (run the .venv-paddle capture first, then re-run this test)")
        print(f"\n{_PASS} passed, {_FAIL} failed, {_SKIP} skipped")
        return 1 if _FAIL else 0

    tags = [d for d in sorted(os.listdir(out_root))
            if os.path.isdir(os.path.join(out_root, d))]
    found_any = False
    for tag in tags:
        cand_path = os.path.join(out_root, tag, "candidates.csv")
        regions_path = os.path.join(out_root, tag, "regions.csv")
        if os.path.exists(cand_path):
            found_any = True
            check_candidates_csv(cand_path)
        if os.path.exists(regions_path):
            found_any = True
            check_regions_csv(regions_path)

    if not found_any:
        print(f"[skip] no candidates.csv / regions.csv found under {out_root} yet")
        print("       (run: .venv-paddle/bin/python "
              "findociq/pipeline/stage1_extract/toc/candidates.py <pdf> <tag>)")
        print(f"\n{_PASS} passed, {_FAIL} failed, {_SKIP} skipped")
        return 1 if _FAIL else 0

    print(f"\n{_PASS} passed, {_FAIL} failed, {_SKIP} skipped")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
