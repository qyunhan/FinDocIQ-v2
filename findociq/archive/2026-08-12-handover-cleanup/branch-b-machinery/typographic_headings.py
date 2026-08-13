"""SUPERSEDED: folded into candidates.py v2 (2026-07-09); kept for reference.

typographic_headings — recover heading candidates PP-DocLayout missed, from font
geometry alone (general signal: a line whose font is markedly larger than the page body
is a heading). Paddle's paragraph_title detector misses primary-statement titles
(OCBC financial statements print "Income Statements" at 20pt vs 9pt body) and some
ALL-CAPS section banners. This augments candidates.csv WITHOUT re-running Paddle.

General rules only (no per-doc constants):
  * heading line = a text line whose median font size >= 1.3x the page's median body size,
    <= 90 chars, not inside any detected table region, not already covered by a Paddle
    candidate at the same y.
  * running-header filter: a normalized heading text that recurs on >= 3 pages is page
    furniture (running header / footer), not a section — dropped.

Run (base python): python3 typographic_headings.py <pdf_path> <tag> [--out <root>]
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import statistics
import sys
from collections import defaultdict

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from verify_cells import words_from_chars  # noqa: E402

_DEFAULT_OUT = os.path.normpath(os.path.join(
    HERE, "..", "..", "..", "experiments", "2026-07-07_paddleocr_eval", "outputs"))
_DATEISH = re.compile(r"\b\d{1,2}\s*[A-Za-z]{3,9}\.?\s*20\d\d\b|for the (quarter|period|year)|as at", re.I)
FACTOR = 1.3
MAX_LEN = 90
RUNHDR_MIN_PAGES = 3


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _existing(out_dir: str):
    regions = defaultdict(list)
    with open(os.path.join(out_dir, "regions.csv")) as fh:
        for r in csv.DictReader(fh):
            regions[int(r["page"])].append(
                [float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"])])
    cand_y = defaultdict(list)
    header = None
    rows = []
    with open(os.path.join(out_dir, "candidates.csv")) as fh:
        rd = csv.DictReader(fh)
        header = rd.fieldnames
        for r in rd:
            rows.append(r)
            cand_y[int(r["page"])].append(float(r["y0"]))
    return regions, cand_y, rows, header


def augment(pdf_path: str, tag: str, out_root: str = _DEFAULT_OUT) -> dict:
    out_dir = os.path.join(out_root, tag)
    regions, cand_y, existing_rows, header = _existing(out_dir)

    found = []  # (page, y0, x0, text, size, bold, align)
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            chars = [c for c in page.chars if c["text"].strip()]
            if not chars:
                continue
            body = statistics.median(c["size"] for c in chars)
            xs = [c["x0"] for c in chars]
            left_margin, right_margin = min(xs), max(c["x1"] for c in chars)
            lines = defaultdict(list)
            for c in chars:
                lines[round(c["top"])].append(c)
            for _, cs in lines.items():
                size = statistics.median(c["size"] for c in cs)
                if size < body * FACTOR:
                    continue
                txt = _norm("".join(c["text"] for c in sorted(cs, key=lambda c: c["x0"])))
                if not txt or len(txt) > MAX_LEN:
                    continue
                x0 = min(c["x0"] for c in cs)
                y0 = min(c["top"] for c in cs)
                cx = statistics.median((c["x0"] + c["x1"]) / 2 for c in cs)
                cy = (y0 + max(c["bottom"] for c in cs)) / 2
                if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in regions.get(pno, [])):
                    continue                                   # inside a table region
                if any(abs(cy - y) < 6 for y in cand_y.get(pno, [])):
                    continue                                   # already a Paddle candidate
                x1 = max(c["x1"] for c in cs)
                align = ("left" if x0 - left_margin <= 8
                         else "right" if right_margin - x1 <= 8 else "center")
                bold = any("Bold" in c["fontname"] for c in cs)
                found.append((pno, round(y0, 1), round(x0, 1), txt, round(size, 1),
                              bold, align))

    # running-header filter: normalized text recurring on >= N pages is furniture
    pages_of = defaultdict(set)
    for pno, _, _, txt, *_ in found:
        pages_of[txt.casefold()].add(pno)
    kept = [f for f in found if len(pages_of[f[3].casefold()]) < RUNHDR_MIN_PAGES]

    # append to candidates.csv, re-sorted by (page, y0)
    new_rows = [dict(page=p, y0=y0, x0=x0, text=txt, font_size=size,
                     bold=bold, alignment=align, is_dateish=bool(_DATEISH.search(txt)))
                for (p, y0, x0, txt, size, bold, align) in kept]
    all_rows = existing_rows + new_rows
    all_rows.sort(key=lambda r: (int(r["page"]), float(r["y0"])))
    with open(os.path.join(out_dir, "candidates.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(all_rows)
    summary = dict(tag=tag, paddle_candidates=len(existing_rows),
                   typographic_added=len(kept),
                   running_headers_dropped=len(found) - len(kept),
                   total=len(all_rows))
    print(f"[{tag}] {summary}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf_path")
    ap.add_argument("tag")
    ap.add_argument("--out", default=_DEFAULT_OUT)
    a = ap.parse_args()
    augment(a.pdf_path, a.tag, a.out)
