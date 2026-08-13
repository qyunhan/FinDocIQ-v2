"""candidates.py — Stage 1 of section->table tagging (see
findociq/docs/specs/2026-07-09-section-table-tagging-design.md).

Paddle proposes, never decides: PP-DocLayout-L emits every header-looking
block per page in reading order, plus every table region. Each header
candidate is enriched with cheap pdfplumber typography (x0/alignment, font
size, bold) read at the candidate's box.

NOTE (2026-08-12): the "downstream arrangers" this file was written to feed
(`toc_match.py` printed-TOC branch, `gemini_arrange.py` no-TOC branch) are RETIRED
— see `archive/2026-08-12-handover-cleanup/branch-b-machinery/`. This module is now
a LEAF: `run_doc.py` STEP 0 invokes it to write `regions.csv` / `candidates.csv`
under `data/derived/paddle_scans/<doc_id>/`, and nothing imports it. The live TOC
routes are `toc/toc_stage.py` (fs) and `discover/pass1_toc.py` (pillar3), neither of
which consumes this file's arranger output.

v2 (2026-07-09, AMENDMENT PM): absorbs typographic_headings.py in the same
run — no separate base-python pass is needed anymore.
  1. Typographic fallback: a text line whose median font size >= 1.3x the
     page's body median, <= 90 chars, not inside any detected table region,
     and not already covered by a Paddle candidate at the same y (+/-6pt) is
     recovered as a candidate too (Paddle's paragraph_title detector misses
     primary-statement titles and some ALL-CAPS banners).
  2. Running-header filter: a normalized (casefold, whitespace-collapsed)
     candidate text recurring on >= 3 pages is page furniture, dropped.
     Applies to typographic-sourced candidates only (Paddle candidates are
     never dropped this way).
  3. Twin dedup: Paddle's box text and a typographic line can double-detect
     the same heading (e.g. "2.8 Property, Plant and Equipment" vs
     "2.8Property,PlantandEquipment" from a glued word rebuild). Two
     candidates on the same page with |y0 delta| <= 3pt whose space-stripped
     casefolded texts are equal, or one is a prefix of the other with a
     difflib ratio >= 0.85, are the same heading detected twice: keep one row
     (prefer more space characters i.e. better word-spacing, tie -> longer
     text), is_dateish = OR of the pair.

Ported/generalized from the working reference
findociq/experiments/2026-07-07_paddleocr_eval/unit_scan.py (do not edit that
file — it stays the spike copy). The critical coordinate rule, verified the
hard way on DBS (page bbox origin != (0,0)):

    Paddle boxes are in rendered PIXELS at 200 DPI. pdfplumber is in POINTS
    with the page bbox ORIGIN added (DBS ~= (-12.64,-12.64); OCBC = (0,0)).
    pt = px * 72/DPI + page.bbox[origin]   -- everywhere Paddle meets pdfplumber.

Text-in-box membership is OVERLAP-based (>=50% of a word's x-extent inside
the box, top within the box y-range +/- a small pad), not center-in-box:
detector boxes are a few pt loose and adaptive token rebuilds can glue a
long first word onto its neighbor.

Outputs, under <out_root>/<tag>/:
  pages/NNN.png     cached page renders at 200 DPI (skipped if present)
  candidates.csv     page,y0,x0,text,font_size,bold,alignment,is_dateish
                      one row per surviving header candidate (Paddle
                      paragraph_title block or typographic fallback line)
  regions.csv         page,table_idx,x0,y0,x1,y1
                      one row per PP-DocLayout `table` block (points, PDF space)

RUNS ONLY IN .venv-paddle:
  .venv-paddle/bin/python findociq/pipeline/stage1_extract/toc/candidates.py \
      <pdf_path> <tag> [--out findociq/experiments/2026-07-07_paddleocr_eval/outputs]
"""
from __future__ import annotations

import argparse
import csv
import difflib
import os
import re
import statistics
import sys
from collections import defaultdict

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))  # -> findociq/pipeline (verify_cells.py)
from common.verify_cells import words_from_chars

MODEL = "PP-DocLayout-L"
DPI = 200
PT = 72.0 / DPI

_DEFAULT_OUT = os.path.join(
    HERE, "..", "..", "..", "experiments", "2026-07-07_paddleocr_eval", "outputs")

_DATE_RE = re.compile(
    r"\b\d{1,2}\s*[A-Za-z]{3,9}\.?\s*20\d\d\b"
    r"|for the (quarter|period|year)"
    r"|as at",
    re.I,
)

_MARGIN_TOL = 8.0  # pt

# -- typographic-fallback constants (ported from typographic_headings.py) --
_TYPO_FACTOR = 1.3
_TYPO_MAX_LEN = 90
_TYPO_COVER_TOL = 6.0  # pt, "already a Paddle candidate at the same y"
_RUNHDR_MIN_PAGES = 3

# -- twin-dedup constants (AMENDMENT 2026-07-09 PM) --
_TWIN_Y_TOL = 3.0  # pt
_TWIN_RATIO_MIN = 0.85

# A numbered section heading: "19.4 Title...", "2. Title", "18.5.1 Title" —
# INCLUDING the glued print "19.4SecuritisationExposures..." (char-joined line
# text has no spaces). Glued form requires a DOTTED number or a trailing dot
# followed by an uppercase letter, so date lines ("31December2025") and bare
# numbers in prose can't fire; spaced form requires a following token.
_NUMBERED_HEADING = re.compile(
    r"^(?:\d+(?:\.\d+)+\.?|\d+\.)(?=[A-Z])"     # glued: 19.4Securitisation / 2.Material
    r"|^\d+(?:\.\d+)*\.?\s+\S")                 # spaced: 19.4 Securitisation / 2 Title


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def is_dateish(text: str) -> bool:
    """Hint only, never a decider (see spec): flags a date/period phrase so
    downstream arrangers can weigh a candidate down, but the arranger — not
    this emitter — makes the final call."""
    return bool(_DATE_RE.search(text or ""))


def text_in(words, box_px, origin=(0.0, 0.0)):
    """Words inside a Paddle box, in PDF points. See module docstring for the
    coordinate rule. Membership is overlap-based (>=50% of the word's
    x-extent), matching unit_scan.text_in."""
    ox, oy = origin
    x0 = box_px[0] * PT + ox
    y0 = box_px[1] * PT + oy
    x1 = box_px[2] * PT + ox
    y1 = box_px[3] * PT + oy
    hit = []
    for w in words:
        wx = min(w["x1"], x1) - max(w["x0"], x0)
        if wx < 0.5 * (w["x1"] - w["x0"]):
            continue
        if y0 - 4.0 <= w["top"] <= y1 + 1.0:
            hit.append(w)
    hit.sort(key=lambda w: (round(w["top"]), w["x0"]))
    return norm(" ".join(w["text"] for w in hit))


def _box_to_pt(box_px, origin):
    ox, oy = origin
    return (box_px[0] * PT + ox, box_px[1] * PT + oy,
            box_px[2] * PT + ox, box_px[3] * PT + oy)


def _chars_in_box(page, box_pt):
    x0, y0, x1, y1 = box_pt
    hit = []
    for c in page.chars:
        if not c["text"].strip():
            continue
        cx = min(c["x1"], x1) - max(c["x0"], x0)
        if cx < 0.5 * (c["x1"] - c["x0"]):
            continue
        if y0 - 4.0 <= c["top"] <= y1 + 1.0:
            hit.append(c)
    return hit


def _typography(page, box_pt):
    """font_size = median char size inside the box; bold = any char's
    fontname contains 'Bold'. Falls back to (0.0, False) if no chars found
    (e.g. box drawn slightly off a thin title line)."""
    chars = _chars_in_box(page, box_pt)
    if not chars:
        return 0.0, False
    size = statistics.median(c["size"] for c in chars)
    bold = any("bold" in (c.get("fontname") or "").lower() for c in chars)
    return round(float(size), 2), bold


def _text_margins(page):
    """Left/right text margins for this page, derived from the page's own
    char extents (not a fixed constant) so alignment classification is
    robust to per-doc page geometry (e.g. DBS's non-zero bbox origin)."""
    chars = [c for c in page.chars if c["text"].strip()]
    if not chars:
        return float(page.bbox[0]), float(page.bbox[2])
    left = min(c["x0"] for c in chars)
    right = max(c["x1"] for c in chars)
    return left, right


def _alignment(x0, x1, left_margin, right_margin):
    if abs(x0 - left_margin) <= _MARGIN_TOL:
        return "left"
    if abs(x1 - right_margin) <= _MARGIN_TOL:
        return "right"
    return "center"


def _typographic_fallback(page, table_boxes_pt, paddle_y0s):
    """Font-outlier heading lines for one page, ported verbatim from
    typographic_headings.augment's per-page loop. table_boxes_pt: list of
    (x0,y0,x1,y1) in points. paddle_y0s: y0 values (points) of this page's
    Paddle candidates, for the "not already covered" check."""
    chars = [c for c in page.chars if c["text"].strip()]
    if not chars:
        return []
    body = statistics.median(c["size"] for c in chars)
    xs = [c["x0"] for c in chars]
    left_margin, right_margin = min(xs), max(c["x1"] for c in chars)
    lines = defaultdict(list)
    for c in chars:
        lines[round(c["top"])].append(c)

    found = []
    for _, cs in lines.items():
        size = statistics.median(c["size"] for c in cs)
        txt = norm("".join(c["text"] for c in sorted(cs, key=lambda c: c["x0"])))
        if not txt:
            continue
        # Two independent heading signals (either admits the line), each with
        # its OWN length bound:
        #  (a) font outlier: markedly larger than the page body; <= 90 chars
        #      (the cap keeps large-font prose/quotes out of this weaker signal);
        #  (b) numbered-bold line: bold text starting with a section-number
        #      pattern ("19.4 Securitisation ...") — headings printed at BODY
        #      size are invisible to (a) (P3 p85's 19.4 was missed exactly so).
        #      Number+bold is already a strong double constraint, and official
        #      heading titles legitimately run long (p85's is 116 chars glued),
        #      so this signal gets a looser 200-char pathology bound only.
        font_outlier = size >= body * _TYPO_FACTOR and len(txt) <= _TYPO_MAX_LEN
        numbered_bold = (len(txt) <= 200
                         and _NUMBERED_HEADING.match(txt)
                         and any("bold" in (c.get("fontname") or "").lower()
                                 for c in cs))
        if not (font_outlier or numbered_bold):
            continue
        x0 = min(c["x0"] for c in cs)
        y0 = min(c["top"] for c in cs)
        cx = statistics.median((c["x0"] + c["x1"]) / 2 for c in cs)
        cy = (y0 + max(c["bottom"] for c in cs)) / 2
        if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in table_boxes_pt):
            continue                                   # inside a table region
        if any(abs(cy - y) < _TYPO_COVER_TOL for y in paddle_y0s):
            continue                                   # already a Paddle candidate
        x1 = max(c["x1"] for c in cs)
        align = ("left" if x0 - left_margin <= 8
                 else "right" if right_margin - x1 <= 8 else "center")
        bold = any("Bold" in c["fontname"] for c in cs)
        found.append(dict(
            page=None,  # filled by caller
            y0=round(y0, 2), x0=round(x0, 2), text=txt,
            font_size=round(size, 1), bold=bold, alignment=align,
            is_dateish=is_dateish(txt)))
    return found


def filter_running_headers(typo_rows, min_pages=_RUNHDR_MIN_PAGES):
    """Drop ALL instances of a normalized (casefold, whitespace-collapsed)
    typographic-sourced candidate text that recurs on >= min_pages distinct
    pages (page furniture: running headers/footers). Only ever called on
    typographic-sourced rows — Paddle candidates are never dropped this way.
    Returns (kept_rows, n_dropped)."""
    pages_of = defaultdict(set)
    for r in typo_rows:
        pages_of[r["text"].casefold()].add(r["page"])
    kept = [r for r in typo_rows if len(pages_of[r["text"].casefold()]) < min_pages]
    return kept, len(typo_rows) - len(kept)


def _twin_key(text: str) -> str:
    return re.sub(r"\s+", "", text or "").casefold()


def _is_twin_text(a_text: str, b_text: str) -> bool:
    ka, kb = _twin_key(a_text), _twin_key(b_text)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if ka.startswith(kb) or kb.startswith(ka):
        return difflib.SequenceMatcher(None, ka, kb).ratio() >= _TWIN_RATIO_MIN
    return False


def merge_twin_candidates(rows, y_tol=_TWIN_Y_TOL):
    """AMENDMENT 2026-07-09 PM twin-dedup rule. rows: candidate dicts with at
    least page,y0,text,is_dateish. Two candidates on the same page with
    |y0 delta| <= y_tol whose space-stripped casefolded texts are equal, or
    one-is-prefix-of-the-other with a difflib ratio >= 0.85, are the SAME
    heading detected twice (Paddle box text vs typographic line text). Keeps
    one row per twin group: prefers the text with more space characters
    (better-spaced), ties broken by longer text; is_dateish = OR of the
    group. Returns (merged_rows, n_merged) where n_merged counts collapsed
    duplicate rows (0 if nothing merged)."""
    by_page = defaultdict(list)
    for r in rows:
        by_page[r["page"]].append(r)

    out = []
    n_merged = 0
    for page, prows in by_page.items():
        prows = sorted(prows, key=lambda r: r["y0"])
        used = [False] * len(prows)
        for i in range(len(prows)):
            if used[i]:
                continue
            group = [prows[i]]
            used[i] = True
            for j in range(i + 1, len(prows)):
                if used[j]:
                    continue
                if (abs(prows[j]["y0"] - prows[i]["y0"]) <= y_tol
                        and _is_twin_text(prows[i]["text"], prows[j]["text"])):
                    group.append(prows[j])
                    used[j] = True
            if len(group) == 1:
                out.append(group[0])
                continue
            best = max(group, key=lambda r: (r["text"].count(" "), len(r["text"])))
            merged = dict(best)
            merged["is_dateish"] = any(bool(r["is_dateish"]) for r in group)
            out.append(merged)
            n_merged += len(group) - 1

    out.sort(key=lambda r: (r["page"], r["y0"]))
    return out, n_merged


def dedup_nested_regions(region_rows: list, containment: float = 0.90):
    """Drop table regions that are geometric ARTIFACTS of double-detection.

    Two cases, decided per page (general rule, no doc constants):
      * a WRAPPER: a region that ~fully CONTAINS >= 2 other regions (Paddle drew
        a box around a stack of tables it also detected individually) -> drop
        the wrapper, keep the children (the children are the real tables).
      * a CHILD DUPLICATE: a region >= `containment` contained in exactly ONE
        other region -> the pair is a double-detection of the same table; keep
        the LARGER (it carries the full extent), drop the contained one.

    Returns (kept_rows_with_renumbered_table_idx, n_dropped). table_idx is
    renumbered 0..k top-to-bottom per page so downstream keys stay contiguous.
    """
    from collections import defaultdict

    def _area(r):
        return max(0.0, (r["x1"] - r["x0"])) * max(0.0, (r["y1"] - r["y0"]))

    def _inter(a, b):
        ix = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
        iy = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
        return ix * iy

    by_page = defaultdict(list)
    for r in region_rows:
        by_page[r["page"]].append(r)

    kept_all, dropped = [], 0
    for page in sorted(by_page):
        rows = by_page[page]
        contains = {i: [] for i in range(len(rows))}   # i contains j
        within = {i: [] for i in range(len(rows))}     # i is inside j
        for i, a in enumerate(rows):
            for j, b in enumerate(rows):
                if i == j or _area(b) <= 0:
                    continue
                if _inter(a, b) / _area(b) >= containment:
                    contains[i].append(j)
            for j, b in enumerate(rows):
                if i == j or _area(a) <= 0:
                    continue
                if _inter(a, b) / _area(a) >= containment:
                    within[i].append(j)

        drop = set()
        for i in range(len(rows)):
            if len(contains[i]) >= 2:
                drop.add(i)                            # wrapper around a stack
        for i in range(len(rows)):
            if i in drop:
                continue
            parents = [j for j in within[i] if j not in drop]
            if len(parents) == 1 and len(contains[parents[0]]) < 2:
                drop.add(i)                            # duplicate inside one larger box
        kept = [r for i, r in enumerate(rows) if i not in drop]
        dropped += len(rows) - len(kept)
        kept.sort(key=lambda r: r["y0"])
        for idx, r in enumerate(kept):
            r = dict(r, table_idx=idx)
            kept_all.append(r)
    return kept_all, dropped


def emit_candidates(pdf_path: str, tag: str, out_root: str = _DEFAULT_OUT) -> dict:
    out_dir = os.path.join(out_root, tag)
    pages_dir = os.path.join(out_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)

    from paddlex import create_model
    model = create_model(model_name=MODEL)  # construct once, reuse across pages

    paddle_rows: list[dict] = []
    typo_rows: list[dict] = []
    region_rows: list[dict] = []

    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        for pno in range(1, n + 1):
            page = pdf.pages[pno - 1]
            png = os.path.join(pages_dir, f"{pno:03d}.png")
            if not os.path.exists(png):
                page.to_image(resolution=DPI).save(png)

            origin = (float(page.bbox[0]), float(page.bbox[1]))
            words = words_from_chars(page)
            left_margin, right_margin = _text_margins(page)

            res = list(model.predict(png))[0]
            blocks = [dict(label=b["label"], box=[float(v) for v in b["coordinate"]])
                      for b in res["boxes"]]

            titles = sorted((b for b in blocks if b["label"] == "paragraph_title"),
                             key=lambda b: b["box"][1])
            tables = sorted((b for b in blocks if b["label"] == "table"),
                             key=lambda b: b["box"][1])

            page_paddle_rows = []
            for t in titles:
                box_pt = _box_to_pt(t["box"], origin)
                text = text_in(words, t["box"], origin)
                font_size, bold = _typography(page, box_pt)
                x0, y0, x1, _y1 = box_pt
                align = _alignment(x0, x1, left_margin, right_margin)
                page_paddle_rows.append(dict(
                    page=pno, y0=round(y0, 2), x0=round(x0, 2), text=text,
                    font_size=font_size, bold=bold, alignment=align,
                    is_dateish=is_dateish(text)))
            paddle_rows.extend(page_paddle_rows)

            table_boxes_pt = []
            for idx, tb in enumerate(tables):
                x0, y0, x1, y1 = _box_to_pt(tb["box"], origin)
                region_rows.append(dict(
                    page=pno, table_idx=idx,
                    x0=round(x0, 2), y0=round(y0, 2),
                    x1=round(x1, 2), y1=round(y1, 2)))
                table_boxes_pt.append((x0, y0, x1, y1))

            # Step 1: typographic fallback, same page, same run.
            paddle_y0s = [r["y0"] for r in page_paddle_rows]
            page_typo = _typographic_fallback(page, table_boxes_pt, paddle_y0s)
            for f in page_typo:
                f["page"] = pno
                typo_rows.append(f)

            print(f"[{tag}] p{pno}/{n}: {len(titles)} candidate(s), "
                  f"{len(page_typo)} typographic, {len(tables)} region(s)", flush=True)

    # Step 2: running-header filter (typographic-sourced candidates only).
    typo_kept, n_runheaders_dropped = filter_running_headers(typo_rows)

    # Step 3: twin dedup across the combined candidate set.
    combined = paddle_rows + typo_kept
    cand_rows, n_twins_merged = merge_twin_candidates(combined)

    cand_path = os.path.join(out_dir, "candidates.csv")
    with open(cand_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "page", "y0", "x0", "text", "font_size", "bold", "alignment", "is_dateish"])
        w.writeheader()
        w.writerows({k: r[k] for k in
                     ("page", "y0", "x0", "text", "font_size", "bold", "alignment", "is_dateish")}
                    for r in cand_rows)

    # Step 4: nested-wrapper dedup — drop a region ~fully contained in another
    # on the same page (Paddle occasionally emits a container box AROUND a stack
    # of tables it also detected individually; rare — measured 5.3% P3 / 1.4% FS
    # — but it inflates every per-section table count by one).
    region_rows, n_wrappers_dropped = dedup_nested_regions(region_rows)

    regions_path = os.path.join(out_dir, "regions.csv")
    with open(regions_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["page", "table_idx", "x0", "y0", "x1", "y1"])
        w.writeheader()
        w.writerows(region_rows)

    summary = dict(
        tag=tag, n_pages=n, n_candidates=len(cand_rows), n_regions=len(region_rows),
        n_typographic_added=len(typo_kept), n_runheaders_dropped=n_runheaders_dropped,
        n_twins_merged=n_twins_merged, n_wrappers_dropped=n_wrappers_dropped)
    print(f"[{tag}] {summary} -> {cand_path}, {regions_path}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Stage 1: emit section-title candidates + "
                                               "table regions (runs in .venv-paddle)")
    ap.add_argument("pdf_path")
    ap.add_argument("tag")
    ap.add_argument("--out", default=_DEFAULT_OUT,
                     help="output root (default: findociq/experiments/"
                          "2026-07-07_paddleocr_eval/outputs)")
    args = ap.parse_args()
    if not os.path.exists(args.pdf_path):
        sys.exit(f"PDF not found: {args.pdf_path}")
    emit_candidates(args.pdf_path, args.tag, args.out)


if __name__ == "__main__":
    main()
