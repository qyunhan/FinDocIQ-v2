"""fuse_cells — STRUCTURE-ONLY arm: Paddle cell GEOMETRY + pdfplumber exact TEXT.

Pivot (user directive 2026-07-08): PP-StructureV3's recognized text is DISCARDED.
Paddle contributes only WHERE cells are (cell_box_list per detected table); every
character comes from the PDF's own text layer (verify_cells.words_from_chars — the
fleet-proven token rebuild that fixes DBS's split-first-digit and letter-spaced
layers), matched into cell boxes by coordinates. No OCR characters anywhere.

Per doc: pages json → cell boxes (px→pt) → rows (y-sweep) → column bands (x0
clusters) → text fill from pdfplumber → stitch page fragments (period change or
line_no restart) → fused tables → score vs the GT CSV on
(period, line_no, col_position 1..5):
  EXACT / TEXT_MISMATCH / MISSING (GT cell has no fused counterpart) / EXTRA_ROW
  (fused line_no absent from GT). Column-count != GT's 5 is a loud STRUCTURE failure.

All rules are dialect-general — no per-bank/per-doc constants.

Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/fuse_cells.py [doc_id ...]
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "2026-06-29_mineru_eval"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "pipeline"))
from docs_config import DOCS, PT_PER_PX
from html_to_cells import _parse_period
from verify_cells import words_from_chars

DASHES = {"-", "–", "—", "−"}


def norm_text(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return "-" if s in DASHES else s


# --------------------------------------------------------------- geometry helpers
def cluster_1d(xs: list[float], gap: float) -> list[list[float]]:
    xs = sorted(xs)
    out: list[list[float]] = []
    for x in xs:
        if out and x - out[-1][-1] <= gap:
            out[-1].append(x)
        else:
            out.append([x])
    return out


def group_rows(boxes: list[list[float]]) -> list[list[int]]:
    """Indices of boxes grouped into visual rows by y-CENTER clustering.
    Non-chaining (unlike an overlap sweep): two short adjacent rows whose tall
    neighbour box overlaps both can never be glued into one row."""
    hs = sorted(b[3] - b[1] for b in boxes)
    med_h = hs[len(hs) // 2]
    cys = [(b[1] + b[3]) / 2 for b in boxes]
    clusters = cluster_1d(sorted(cys), gap=0.6 * med_h)
    reps = [(c[0] + c[-1]) / 2 for c in clusters]
    rows: list[list[int]] = [[] for _ in reps]
    for i, cy in enumerate(cys):
        k = min(range(len(reps)), key=lambda j: abs(reps[j] - cy))
        rows[k].append(i)
    rows = [sorted(r, key=lambda i: boxes[i][0]) for r in rows if r]
    rows.sort(key=lambda r: min(boxes[i][1] for i in r))
    return rows


def column_lefts(boxes: list[list[float]], rows: list[list[int]],
                 gap: float = 6.0) -> list[float]:
    """Column left edges = clusters of cell x0 with ROW SUPPORT: a real column
    is started by boxes in a sizable fraction of rows; mis-gridded header cells
    contribute 1-2 stray x0s and are dropped. General rule, no doc constants."""
    row_of = {}
    for ri, idxs in enumerate(rows):
        for i in idxs:
            row_of[i] = ri
    clusters = cluster_1d([b[0] for b in boxes], gap)
    support = []
    x0_sorted = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    it = iter(x0_sorted)
    for c in clusters:
        members = set()
        for _ in c:
            members.add(row_of.get(next(it)))
        support.append(len(members))
    need = max(3, int(0.3 * len(rows)))
    kept = [min(c) for c, s in zip(clusters, support) if s >= need]
    return kept if kept else [min(c) for c in clusters]


def col_index_span(box: list[float], lefts: list[float]) -> tuple[int, int]:
    """(column index, span) of a box against the left-edge bands."""
    idx = max((k for k, l in enumerate(lefts) if l <= box[0] + 3.0), default=0)
    span = max(1, sum(1 for l in lefts if box[0] + 3.0 <= l < box[2] - 3.0) + 1
               if any(box[0] + 3.0 <= l < box[2] - 3.0 for l in lefts) else 1)
    return idx, span


def rule_rows(page, boxes: list[list[float]]) -> list[list[int]] | None:
    """When the table region is RULED (horizontal rules spanning most of its
    width), the rules — not Paddle's boxes — author the row bands: wrapped
    multi-line labels live inside one ruled row, which box clustering splits or
    glues. Returns box-index groups per ruled row, or None when the region has
    no usable rules (borderless render → caller falls back to box clustering)."""
    x0 = min(b[0] for b in boxes); x1 = max(b[2] for b in boxes)
    y0 = min(b[1] for b in boxes); y1 = max(b[3] for b in boxes)
    width = x1 - x0
    # rules are often drawn as PER-CELL segments (DBS: thin rects per cell
    # border) — collect every horizontal segment, cluster by y, and demand the
    # CLUSTER's combined x-span cover most of the table width.
    # y-window pads by ~half a row: the table's outer border rules sit at or
    # just OUTSIDE the cell boxes' extents (cell padding), so a tight +-2pt
    # window can miss the bottom border by under a point.
    pad = 8.0
    segs = []
    for ln in page.lines:
        if abs(ln["top"] - ln["bottom"]) < 0.5 and y0 - pad <= ln["top"] <= y1 + pad:
            segs.append((ln["top"], ln["x0"], ln["x1"]))
    for r in page.rects:
        if r["bottom"] - r["top"] < 1.5 and y0 - pad <= r["top"] <= y1 + pad:
            segs.append(((r["top"] + r["bottom"]) / 2, r["x0"], r["x1"]))
    segs = [(y, a, b) for y, a, b in segs if min(b, x1) - max(a, x0) > 0]
    edges = []
    for cl in cluster_1d(sorted({s[0] for s in segs}), gap=1.5):
        members = [s for s in segs if cl[0] <= s[0] <= cl[-1]]
        span = max(s[2] for s in members) - min(s[1] for s in members)
        if span >= 0.6 * width:
            edges.append(cl[0])
    if len(edges) < 4:
        return None
    bands = list(zip(edges, edges[1:]))
    rows: list[list[int]] = [[] for _ in bands]
    for i, b in enumerate(boxes):
        cy = (b[1] + b[3]) / 2
        hit = next((ri for ri, (lo, hi) in enumerate(bands) if lo <= cy < hi), None)
        if hit is not None:
            rows[hit].append(i)
    out = []
    for (lo, hi), idxs in zip(bands, rows):
        if hi - lo < 4.0:                     # decorative double-rule gap
            continue
        out.append(dict(y0=lo, y1=hi, idxs=sorted(idxs, key=lambda i: boxes[i][0])))
    return out or None


# --------------------------------------------------------------- text fill
def fill_text(words: list[dict], box: list[float], tol: float = 2.0) -> str:
    x0, y0, x1, y1 = box
    hit = [w for w in words
           if x0 - tol <= (w["x0"] + w["x1"]) / 2 <= x1 + tol
           and y0 - tol <= w["top"] <= y1 - 1.0]
    hit.sort(key=lambda w: (w["top"], w["x0"]))
    return norm_text(" ".join(w["text"] for w in hit))


def parse_period_loose(lines: list[str]) -> str | None:
    """_parse_period tolerant of glued tokens ('31Dec2023'): re-open the
    digit<->letter boundaries the adaptive word rebuild may have fused."""
    opened = [re.sub(r"(?<=[A-Za-z])(?=\d)", " ",
                     re.sub(r"(?<=\d)(?=[A-Za-z])", " ", l)) for l in lines]
    return _parse_period(opened)


def context_lines(words: list[dict], above_y: float) -> list[str]:
    """Page text lines fully above a table top edge (for period parsing)."""
    ws = [w for w in words if w["top"] < above_y - 2]
    lines: dict[int, list[dict]] = {}
    for w in ws:
        lines.setdefault(int(w["top"] // 4), []).append(w)
    out = []
    for k in sorted(lines):
        toks = sorted(lines[k], key=lambda w: w["x0"])
        out.append(norm_text(" ".join(t["text"] for t in toks)))
    return [l for l in out if l]


# --------------------------------------------------------------- per-page fusion
def page_fragments(doc_id: str, pno: int, pdf, pages_sub: str = "pages") -> list[dict]:
    """Paddle authors WHERE (row bands + column left edges); pdfplumber words are
    distributed into the row x column GRID by coordinates. Wide detected boxes
    (shaded band rows, merged headers) thus still yield per-column cells — the
    band split is the text authority, the detected box only located the table."""
    jp = os.path.join(HERE, "outputs", doc_id, pages_sub, f"{pno:03d}.json")
    raw = json.load(open(jp))
    page = pdf.pages[pno - 1]
    words = words_from_chars(page)
    frags = []
    for entry in raw.get("table_res_list", []):
        boxes = [[v * PT_PER_PX for v in b] for b in entry["cell_box_list"]]
        if not boxes:
            continue
        row_groups = rule_rows(page, boxes)
        if row_groups is None:
            row_groups = [dict(y0=min(boxes[i][1] for i in idxs),
                               y1=max(boxes[i][3] for i in idxs), idxs=idxs)
                          for idxs in group_rows(boxes)]
        lefts = column_lefts(boxes, [g["idxs"] for g in row_groups])
        right = max(b[2] for b in boxes)
        edges = lefts[1:] + [right + 1.0]          # band k = [lefts[k], edges[k])

        def band_of(x: float) -> int:
            return max((k for k, l in enumerate(lefts) if l - 2.0 <= x), default=0)

        # ---- phase 1: plain per-band grid for every row
        base_rows = []
        for g in row_groups:
            row_idxs, y0, y1 = g["idxs"], g["y0"], g["y1"]
            in_row = [w for w in words if y0 - 1.0 <= w["top"] <= y1 - 1.0]
            band_txt, band_x1 = [], []
            for lo, hi in zip(lefts, edges):
                toks = sorted((w for w in in_row
                               if lo - 2.0 <= (w["x0"] + w["x1"]) / 2 < hi - 2.0),
                              key=lambda w: (w["top"], w["x0"]))
                band_txt.append(norm_text(" ".join(w["text"] for w in toks)))
                band_x1.append(max((w["x1"] for w in toks), default=None))
            base_rows.append(dict(idxs=row_idxs, y0=y0, y1=y1,
                                  in_row=in_row, band_txt=band_txt,
                                  band_x1=band_x1))

        # ---- phase 2: classify bands, then merge overlay on VALUE bands only.
        # GT's merge space is value columns (colspans never include the label
        # band), and a long label whose detected box leaks rightwards must not
        # swallow a value band.
        grid = [[dict(col=k, span=1, text=t) for k, t in enumerate(br["band_txt"])]
                for br in base_rows]
        ln_col, lab_col, val_cols = classify_columns(grid, len(lefts))
        val_set = set(val_cols)

        # per-band right-alignment line: real cells right-align; a value whose
        # right edge falls well short of its band's alignment line is a CENTERED
        # value spanning its empty flanking bands (the template's colspan print)
        align_x: dict[int, float] = {}
        for k in val_set:
            x1s = sorted(br["band_x1"][k] for br in base_rows
                         if br["band_x1"][k] is not None
                         and re.search(r"\d", br["band_txt"][k]))
            if x1s:
                # right-align line = MAX right extent: every right-aligned value
                # ends there, centered (colspan) values end left of it. A median
                # collapses onto the centered values in dash-heavy columns.
                align_x[k] = x1s[-1]

        rows = []
        for br in base_rows:
            merges: dict[int, dict] = {}
            claimed: set[int] = set()

            # alignment-based merges (no box evidence needed; dashes exempt —
            # they are always colspan=1 and centered by convention)
            for o in sorted(val_set):
                t = br["band_txt"][o]
                if (not t or t in DASHES or not re.search(r"\d", t)
                        or o not in align_x or br["band_x1"][o] is None
                        or align_x[o] - br["band_x1"][o] <= 4.0):
                    continue
                # symmetric +-1 span only: a centered value's span center is
                # indistinguishable from wider symmetric spans by geometry alone
                # (uniform bands), and every GT colspan-3 prints as value-in-the-
                # middle. Wider merges need rule/template authority (Gate 2).
                k0, m0 = o - 1, o + 1
                if (k0 in val_set and m0 in val_set
                        and not br["band_txt"][k0] and not br["band_txt"][m0]
                        and not claimed & {k0, o, m0}):
                    merges[k0] = dict(col=k0, span=3, text=t)
                    claimed |= {k0, o, m0}
            for i in br["idxs"]:
                b = boxes[i]
                k, m = band_of(b[0] + 3.0), band_of(b[2] - 3.0)
                # clip the box's band range to value bands (label/line_no leakage
                # of a detected box must not veto a genuine value-column merge)
                vk = sorted(val_set)
                k = max(k, vk[0])
                m = min(m, vk[-1])
                if (m <= k or not set(range(k, m + 1)) <= val_set
                        or claimed & set(range(k, m + 1))):
                    continue
                # occupancy over the FULL spanned band range, not the box extent:
                # a value in a spanned band the box fails to cover must veto the
                # merge, or it would be silently swallowed.
                span_lo, span_hi = lefts[k], edges[m]
                ws = [w for w in br["in_row"]
                      if span_lo - 2.0 <= (w["x0"] + w["x1"]) / 2 < span_hi - 2.0]
                occupied = {band_of((w["x0"] + w["x1"]) / 2) for w in ws}
                if len(occupied) == 1:
                    text = norm_text(" ".join(
                        w["text"] for w in sorted(ws, key=lambda w: (w["top"], w["x0"]))))
                    o = occupied.pop()
                    # anchor semantics (GT convention): a value sitting in the
                    # FIRST/LAST band of the span is a normal cell in its own
                    # column; the remainder is an EMPTY merge. Only an interior
                    # (centered) value makes the whole span one merged cell.
                    if o == m:
                        merges[k] = dict(col=k, span=m - k, text="")
                        merges[m] = dict(col=m, span=1, text=text)
                    elif o == k:
                        merges[k] = dict(col=k, span=1, text=text)
                        merges[k + 1] = dict(col=k + 1, span=m - k, text="")
                    else:
                        merges[k] = dict(col=k, span=m - k + 1, text=text)
                    claimed |= set(range(k, m + 1))
            cells, k = [], 0
            while k < len(lefts):
                if k in merges:
                    cells.append(merges[k])
                    k += merges[k]["span"]
                else:
                    cells.append(dict(col=k, span=1, text=br["band_txt"][k]))
                    k += 1
            rows.append(cells)
        top = min(b[1] for b in boxes)
        frags.append(dict(page=pno, n_cols=len(lefts), rows=rows, top=top,
                          period=parse_period_loose(context_lines(words, top))))
    frags.sort(key=lambda f: f["top"])
    return frags


def classify_columns(rows: list[list[dict]], n_cols: int) -> tuple[int | None, int | None, list[int]]:
    """(line_no col, label col, value col indices) — decided by content shape,
    same rule for every doc: line_no col = mostly small ints; label col = longest
    median text; value cols = the rest, left→right."""
    texts: dict[int, list[str]] = {k: [] for k in range(n_cols)}
    for r in rows:
        for c in r:
            if c["span"] == 1 and c["text"]:
                texts[c["col"]].append(c["text"])
    def int_frac(vals):
        return (sum(bool(re.fullmatch(r"\d{1,2}[a-z]?", v)) for v in vals) / len(vals)
                if vals else 0.0)
    ln_col = next((k for k in sorted(texts) if int_frac(texts[k]) >= 0.6), None)
    lab_col = max((k for k in texts if k != ln_col),
                  key=lambda k: sorted(len(t) for t in texts[k])[len(texts[k]) // 2]
                  if texts[k] else 0, default=None)
    val_cols = [k for k in sorted(texts) if k not in (ln_col, lab_col)]
    return ln_col, lab_col, val_cols


def line_no_of(row: list[dict], ln_col: int | None) -> int | None:
    """Leading integer of the line-no cell. Trailing junk is tolerated: a short
    label word at deep indent ('16 for', '34 Net') can drift into the band —
    the number is still authoritative (junk is re-attached to the label by the
    caller)."""
    if ln_col is None:
        return None
    for c in row:
        if c["col"] == ln_col:
            m = re.match(r"(\d{1,2})[a-z]?(?:\s|$)", c["text"])
            return int(m.group(1)) if m else None
    return None


def ln_cell_remainder(row: list[dict], ln_col: int | None) -> str:
    """Non-numeric tail of the line-no cell (drifted label words)."""
    if ln_col is None:
        return ""
    for c in row:
        if c["col"] == ln_col:
            m = re.match(r"\d{1,2}[a-z]?\s+(.+)$", c["text"])
            return m.group(1) if m else ""
    return ""


# --------------------------------------------------------------- stitch + emit
def build_fused(doc_id: str, pages_sub: str = "pages") -> list[dict]:
    doc = DOCS[doc_id]
    pages_dir = os.path.join(HERE, "outputs", doc_id, pages_sub)
    pnos = sorted(int(f[:3]) for f in os.listdir(pages_dir) if f.endswith(".json"))
    if not pnos:
        raise FileNotFoundError(f"no captures under {pages_dir}")
    tables, cur = [], None
    with pdfplumber.open(doc["pdf"]) as pdf:
        for pno in pnos:
            for f in page_fragments(doc_id, pno, pdf, pages_sub):
                ln_col, lab_col, val_cols = classify_columns(f["rows"], f["n_cols"])
                lns = [line_no_of(r, ln_col) for r in f["rows"]]
                nums = [n for n in lns if n is not None]
                first, last = (nums[0], nums[-1]) if nums else (None, None)
                new = (cur is None
                       or (f["period"] and f["period"] != cur["period"])
                       or (first is not None and cur["last_ln"] is not None
                           and first <= cur["last_ln"]))
                rec = dict(rows=f["rows"], lns=lns, ln_col=ln_col,
                           lab_col=lab_col, val_cols=val_cols, page=pno)
                if new:
                    if cur:
                        tables.append(cur)
                    if not f["period"]:
                        raise ValueError(f"p{pno}: new table, no period in context")
                    cur = dict(period=f["period"], frags=[rec],
                               last_ln=last, pages=[pno])
                else:
                    cur["frags"].append(rec)
                    cur["pages"].append(pno)
                    cur["last_ln"] = last if last is not None else cur["last_ln"]
    if cur:
        tables.append(cur)

    fused_name = ("fused" if pages_sub == "pages"
                  else "fused_" + pages_sub.removeprefix("pages_"))
    out_dir = os.path.join(HERE, "outputs", doc_id, fused_name)
    os.makedirs(out_dir, exist_ok=True)
    fused = []
    for t in tables:
        cells = []          # (line_no, label, {col_pos 1..N: text})
        for fr in t["frags"]:
            for r, ln in zip(fr["rows"], fr["lns"]):
                label = next((c["text"] for c in r if c["col"] == fr["lab_col"]), "")
                drift = ln_cell_remainder(r, fr["ln_col"])
                if drift:
                    label = norm_text(drift + " " + label)
                vals = {}
                for c in r:
                    if c["col"] in fr["val_cols"]:
                        vals[fr["val_cols"].index(c["col"]) + 1] = c["text"]
                cells.append(dict(page=fr["page"], line_no=ln, label=label,
                                  values=vals, n_val_cols=len(fr["val_cols"])))
        rec = dict(doc_id=doc_id, period=t["period"], pages=t["pages"], cells=cells)
        fused.append(rec)
        cp = os.path.join(out_dir, f"nsfr_{t['period']}.cells.csv")
        with open(cp, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["page", "line_no", "label"]
                       + [f"v{k}" for k in range(1, 6)])
            for c in cells:
                w.writerow([c["page"], c["line_no"], c["label"]]
                           + [c["values"].get(k, "") for k in range(1, 6)])
        print(f"[{doc_id}] fused nsfr_{t['period']} pages={t['pages']} "
              f"rows={len(cells)} -> {os.path.relpath(cp, HERE)}")
    return fused


# --------------------------------------------------------------- scoring
def score_doc(doc_id: str, fused: list[dict]) -> dict:
    gt_rows = list(csv.DictReader(open(DOCS[doc_id]["gt"])))
    report = dict(doc_id=doc_id, tables=[])
    for t in fused:
        gt = [r for r in gt_rows if r["period"] == t["period"]]
        if not gt:
            continue
        gt_cell = {(int(r["line_no"]), int(r["col_id"])):
                   (norm_text(r["value_raw"]), int(r["colspan"] or 1))
                   for r in gt if r["line_no"]}
        gt_lines = {int(r["line_no"]) for r in gt if r["line_no"]}
        by_ln: dict[int, dict] = {}
        for c in t["cells"]:
            if c["line_no"] is not None and c["line_no"] not in by_ln:
                by_ln[c["line_no"]] = c
        exact = mism = missing = 0
        mism_ex, extra = [], sorted(set(by_ln) - gt_lines)
        dup = [c["line_no"] for c, n in
               Counter(c["line_no"] for c in t["cells"]
                       if c["line_no"] is not None).items() if n > 1] if False else \
              [ln for ln, n in Counter(c["line_no"] for c in t["cells"]
                                       if c["line_no"] is not None).items() if n > 1]
        n_val_cols = {c["n_val_cols"] for c in t["cells"]}
        for (ln, col), (want, span) in sorted(gt_cell.items()):
            vals = by_ln.get(ln, {}).get("values")
            if vals is None:
                missing += 1
                mism_ex.append((ln, col, want, "<MISSING>"))
                continue
            # a GT cell spans positions col..col+span-1 (colspan is GT's own
            # declaration of cell identity): the value may sit at any position
            # inside the span, the rest of the span must be empty.
            inside = [vals.get(p) for p in range(col, col + span)]
            nonempty = [v for v in inside if v]
            got = (nonempty[0] if len(nonempty) == 1
                   else "" if not nonempty else " | ".join(nonempty))
            if all(v is None for v in inside):
                missing += 1
                mism_ex.append((ln, col, want, "<MISSING>"))
            elif got == want:
                exact += 1
            else:
                mism += 1
                mism_ex.append((ln, col, want, got))
        report["tables"].append(dict(
            period=t["period"], pages=t["pages"], n_gt=len(gt_cell),
            exact=exact, text_mismatch=mism, missing=missing,
            extra_lines=extra, dup_lines=dup, val_col_counts=sorted(n_val_cols),
            examples=mism_ex[:12]))
    return report


def main():
    args = sys.argv[1:]
    pages_sub = "pages"
    for a in list(args):
        if a.startswith("--pages-sub="):
            pages_sub = a.split("=", 1)[1]
            args.remove(a)
    doc_ids = args or list(DOCS)
    all_ok = True
    for doc_id in doc_ids:
        fused = build_fused(doc_id, pages_sub)
        rep = score_doc(doc_id, fused)
        for tr in rep["tables"]:
            tot = tr["n_gt"]
            print(f"\n=== {doc_id} {tr['period']} (pages {tr['pages']}) ===")
            print(f"  GT cells {tot} | EXACT {tr['exact']} "
                  f"({100*tr['exact']/tot:.1f}%) | TEXT_MISMATCH {tr['text_mismatch']} "
                  f"| MISSING {tr['missing']}")
            print(f"  value-col counts seen: {tr['val_col_counts']} (GT: 5) | "
                  f"extra lines: {tr['extra_lines']} | dup lines: {tr['dup_lines']}")
            for ln, col, want, got in tr["examples"]:
                print(f"    line {ln} col {col}: GT={want!r} fused={got!r}")
            all_ok &= (tr["exact"] == tot)
        fused_name = ("fused" if pages_sub == "pages"
                      else "fused_" + pages_sub.removeprefix("pages_"))
        sp = os.path.join(HERE, "outputs", doc_id, fused_name, "score.json")
        json.dump(rep, open(sp, "w"), indent=1)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
