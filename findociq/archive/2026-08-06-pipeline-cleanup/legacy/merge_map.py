"""merge_map — per-table geometric structure map, ALL derived from the table's own ink.

For a table bbox on a pdfplumber page:
  grid     <- clustered vertical-edge x-positions inside the bbox (the column boundaries),
              supplemented by aligned numeric right-edges when ruling is sparse
  bands    <- word rows (visual lines) inside the bbox
  segments <- per band: drawn rects mapped onto the grid. A rect covering >1 column is a
              MERGE (colspan = column count). Mid-tone fill = SHADE. No rect = plain cell.

This is the structure AUTHORITY for bordered tables: Gemini's colspans/shading are
overwritten from here (finding 2026-07-02: 9/9 model arms misread merges; ink never does).
Zero LLM tokens. Nothing bank- or template-specific.

Usage:
    python3 merge_map.py <pdf> <page_1based>       # prints the map for每 detected table
"""
from __future__ import annotations
import re
import sys

NUM = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?%?$|^-$")
TOL = 2.5                      # px cluster tolerance (edges, word rows)


def _cluster(vals: list[float], tol: float = TOL) -> list[float]:
    """Sorted 1-D cluster centres."""
    out: list[list[float]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _is_grey(fill) -> bool:
    """Mid-tone fill = shading (excludes white ~1 and black ~0). Scalar, grey or RGB."""
    if fill is None:
        return False
    vals = [fill] if isinstance(fill, (int, float)) else list(fill)
    if len(vals) == 4:                                   # CMYK -> approx luminance
        c, m, y, k = vals
        vals = [(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)]
    if not vals or max(vals) - min(vals) > 0.12:         # coloured, not grey
        return False
    lum = sum(vals) / len(vals)
    return 0.30 <= lum <= 0.93


def derive_grid(page, bbox) -> list[float]:
    """Column boundaries from the table's own vertical edges (+ numeric alignment fallback)."""
    x0, top, x1, bot = bbox
    xs = [e["x0"] for e in page.edges
          if e["orientation"] == "v" and x0 - TOL <= e["x0"] <= x1 + TOL
          and e["top"] < bot and e["bottom"] > top]
    grid = _cluster(xs)
    if len(grid) < 3:                                    # sparse ruling -> numeric right-edges
        words = [w for w in page.extract_words()
                 if x0 <= w["x0"] and w["x1"] <= x1 and top <= w["top"] <= bot]
        rights = [w["x1"] for w in words if NUM.match(w["text"])]
        grid = sorted(set(grid) | set(_cluster(rights)))
    return grid


def _bands(page, bbox) -> list[dict]:
    """Visual word-rows inside the bbox -> [{top, bottom, label}]."""
    x0, top, x1, bot = bbox
    words = [w for w in page.extract_words()
             if x0 - TOL <= w["x0"] and w["x1"] <= x1 + TOL and top - TOL <= w["top"] <= bot + TOL]
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and w["top"] - rows[-1][0]["top"] <= TOL:
            rows[-1].append(w)
        else:
            rows.append([w])
    out = []
    for r in rows:
        out.append(dict(top=min(w["top"] for w in r) - 1, bottom=max(w["bottom"] for w in r) + 1,
                        label=" ".join(w["text"] for w in r)[:60]))
    return out


def _col_span(grid: list[float], rx0: float, rx1: float) -> tuple[int, int] | None:
    """Map a rect's x-extent to (first_col, last_col), 1-based; None if outside/degenerate."""
    def snap(x):
        return min(range(len(grid)), key=lambda i: abs(grid[i] - x))
    a, b = snap(rx0), snap(rx1)
    if b <= a:
        return None
    return a + 1, b                                       # boundaries i..j -> cols i+1..j


def table_map(page, bbox) -> dict:
    """The structure map for one table bbox."""
    grid = derive_grid(page, bbox)
    if len(grid) < 2:
        return dict(grid=grid, rows=[])
    min_col_w = min(b - a for a, b in zip(grid, grid[1:]))
    x0, top, x1, bot = bbox
    rects = [r for r in page.rects
             if x0 - TOL <= r["x0"] and r["x1"] <= x1 + TOL
             and r["top"] >= top - TOL and r["bottom"] <= bot + TOL
             and (r["x1"] - r["x0"]) >= min_col_w * 0.6]
    rows = []
    for band in _bands(page, bbox):
        mid = (band["top"] + band["bottom"]) / 2
        hits = [r for r in rects if r["top"] <= mid <= r["bottom"]
                and (r["bottom"] - r["top"]) <= (band["bottom"] - band["top"]) * 2.5]
        segs, seen = [], []
        for r in sorted(hits, key=lambda r: (r["x0"], -(r["x1"] - r["x0"]))):
            span = _col_span(grid, r["x0"], r["x1"])
            if span is None:
                continue
            c1, c2 = span
            if any(a <= c1 and c2 <= b for a, b in seen):  # inner/duplicate rect
                continue
            seen.append((c1, c2))
            segs.append(dict(c1=c1, c2=c2, span=c2 - c1 + 1, shade=_is_grey(r.get("non_stroking_color"))))
        if any(s["span"] > 1 or s["shade"] for s in segs):
            rows.append(dict(label=band["label"], segments=sorted(segs, key=lambda s: s["c1"])))
    return dict(grid=[round(g, 1) for g in grid], n_cols=len(grid) - 1, rows=rows)


if __name__ == "__main__":
    import pdfplumber
    pdf_path, pg = sys.argv[1], int(sys.argv[2])
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[pg - 1]
        tables = page.find_tables()
        regions = [t.bbox for t in tables] or [page.bbox]
        # merge touching/fragmented ruled regions into one table region per vertical run
        for i, bbox in enumerate(regions, 1):
            m = table_map(page, bbox)
            print(f"table {i}  bbox={tuple(round(v) for v in bbox)}  cols={m['n_cols']}")
            for r in m["rows"]:
                segs = " | ".join(f"c{s['c1']}" + (f"-c{s['c2']}" if s["span"] > 1 else "")
                                  + (" GREY" if s["shade"] else "")
                                  + (f" span={s['span']}" if s["span"] > 1 else "")
                                  for s in r["segments"])
                print(f"    {r['label'][:44]:44} {segs}")
