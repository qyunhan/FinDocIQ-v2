"""stitch_demo.py — deterministic logical-table stitcher (demo, DBS 2Q25).

Consumes the Paddle scan artifacts (regions.csv, candidates.csv — both in PDF
points) + pdfplumber words, and decides for each consecutive region pair:

  CHECK 1  architectural break?  (heading candidate or date strip between the
           regions; mid-page whitespace gap. The whitespace signal is WAIVED
           across a page break — headings/dates still count there.)
  CHECK 2  column signature match?  (right-edge clustering of numeric words,
           support-filtered; labels are not required — headers are often not
           reprinted on continuations.)
  CHECK 3  do the rows CONTINUE?  (a continuation never repeats row labels
           already seen in the chain, except a reprinted header line; repeats/
           restarts OR a date change => NEW PERIOD INSTANCE, never stitched.)

Verdicts per pair: STITCH | NEW_TABLE(break) | PERIOD_INSTANCE(rows-restart or
date-change) | NEW_TABLE(cols-differ) | FLAG(labels-vs-geometry conflict).

Run: python3 stitch_demo.py [--pages 17-19]   (base venv; no Paddle needed)
"""
import argparse
import csv
import json
import re
from pathlib import Path

import pdfplumber

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PDF = (REPO / "findociq/data/sources/financial_statements/DBS/2025/2Q25"
       / "DBS_2Q25_performance_summary.pdf")
SCAN = HERE / "paddle_out" / "dbs_2q25_fs"

NUM_WORD = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?%?$")
DATEISH = re.compile(
    r"\b(?:3[01]|[12]?\d)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d\d\b"
    r"|\b(?:[1-4]Q|1H|2H|FY)\s?\d\d\b", re.I)

GAP_BREAK_PT = 40.0     # same-page pure-whitespace gap that separates tables
EDGE_TOL_PT = 5.0       # column right-edge match tolerance
MARGIN_PT = 60.0        # "reaches body edge" tolerance (soft, reported only)


def load_regions():
    rows = list(csv.DictReader(open(SCAN / "regions.csv")))
    regs = [{"page": int(r["page"]), "idx": int(r["table_idx"]),
             "x0": float(r["x0"]), "y0": float(r["y0"]),
             "x1": float(r["x1"]), "y1": float(r["y1"])} for r in rows]
    return sorted(regs, key=lambda r: (r["page"], r["y0"]))


def load_candidates():
    return list(csv.DictReader(open(SCAN / "candidates.csv")))


def words_by_page():
    out = {}
    with pdfplumber.open(PDF) as pdf:
        for i, pg in enumerate(pdf.pages, start=1):
            out[i] = (pg.extract_words(), float(pg.bbox[1]), float(pg.bbox[3]))
    return out


def in_region(w, reg, pad=3.0):
    ox = max(0.0, min(float(w["x1"]), reg["x1"]) - max(float(w["x0"]), reg["x0"]))
    if ox < 0.5 * (float(w["x1"]) - float(w["x0"])):
        return False
    yc = (float(w["top"]) + float(w["bottom"])) / 2
    return reg["y0"] - pad <= yc <= reg["y1"] + pad


def region_features(reg, words):
    ws = [w for w in words if in_region(w, reg)]
    # numeric column right edges, support-filtered
    nums = [w for w in ws if NUM_WORD.match(w["text"])]
    edges = sorted(float(w["x1"]) for w in nums)
    bands = []
    for e in edges:
        if bands and e - bands[-1][-1] <= 6.0:
            bands[-1].append(e)
        else:
            bands.append([e])
    lines_n = len({round(float(w["top"]) / 2) for w in ws}) or 1
    sig = [sum(b) / len(b) for b in bands if len(b) >= max(2, 0.3 * min(lines_n, 20))]
    # row lines: y-grouped words; label = words left of the first numeric band
    label_cut = (min(sig) - 30.0) if sig else reg["x0"] + 0.45 * (reg["x1"] - reg["x0"])
    lines = {}
    for w in ws:
        lines.setdefault(round(float(w["top"]) / 2.5), []).append(w)
    rows = []
    for key in sorted(lines):
        lws = sorted(lines[key], key=lambda w: float(w["x0"]))
        label = " ".join(w["text"] for w in lws if float(w["x1"]) <= label_cut).strip()
        full = " ".join(w["text"] for w in lws)
        rows.append({"label": label, "full": full})
    dates = sorted({m.group(0) for r in rows for m in DATEISH.finditer(r["full"])})
    return {"sig": sig, "rows": rows, "dates": dates, "n_words": len(ws)}


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).casefold().strip()


def sig_match(a, b):
    """Band-count equality + pairwise edge alignment. Subset-alignment was
    tried and REVERTED: it recovers the rare upstream region-merge case (UOB
    'Key financial ratios' — Paddle glued two tables into one region) but
    falsely stitches any narrower table printed on the same column grid
    (DBS highlights -> per-share). The merged-region case is an UPSTREAM
    region de-merge TODO, not a signature problem."""
    if not a["sig"] or not b["sig"]:
        return None                      # not evaluable (sparse region)
    if len(a["sig"]) != len(b["sig"]):
        return False
    return all(abs(x - y) <= EDGE_TOL_PT for x, y in zip(a["sig"], b["sig"]))


_furniture_cache = {}


def page_furniture(words):
    """Squashed line-texts recurring on >=3 pages (running headers, footers,
    page numbers) — never counted as break content. Cached per words-dict."""
    key = id(words)
    if key in _furniture_cache:
        return _furniture_cache[key]
    seen = {}
    for pg, (ws, _, _) in words.items():
        lines = {}
        for w in ws:
            lines.setdefault(round(float(w["top"]) / 2.5), []).append(w["text"])
        for parts in lines.values():
            t = re.sub(r"[^a-z0-9]", "", "".join(parts).casefold())
            if t:
                seen.setdefault(t, set()).add(pg)
    furn = {t for t, pgs in seen.items() if len(pgs) >= 3}
    _furniture_cache[key] = furn
    return furn


def own_title(featA, featB):
    """Region B opens with its own title line INSIDE the box: a non-numeric,
    non-date line that is not a reprint of A's header row. A table continuation
    starts with data (numeric) or a reprinted header — never a fresh title."""
    if not featB["rows"] or not featA["rows"]:
        return None
    first = featB["rows"][0]["full"]
    if any(NUM_WORD.match(tok) for tok in first.split()):
        return None
    if DATEISH.search(first):
        return None                       # stranded date strip: period signal
    headA = norm(featA["rows"][0]["full"])
    stripped = norm(re.sub(r"\(cont(?:'d|inued)?\.?\)", "", first))
    if stripped and stripped == norm(re.sub(r"\(cont(?:'d|inued)?\.?\)", "", featA["rows"][0]["full"])):
        return None                       # reprinted header/title (modulo cont'd)
    if norm(first) == headA:
        return None
    return first[:40]


def break_between(rA, rB, cands, words, featA=None, featB=None):
    """Enumerated CHECK-1 break markers between two regions."""
    marks = []
    if featA is not None and featB is not None:
        t = own_title(featA, featB)
        if t:
            marks.append(("own-title", t))
    same_page = rA["page"] == rB["page"]
    furn = page_furniture(words)
    # heading candidate strictly between the regions (or above rB on its page)
    for c in cands:
        if int(c["page"]) != rB["page"]:
            continue
        cy = float(c["y0"])
        lo = rA["y1"] if same_page else -1e9
        if lo < cy < rB["y0"]:
            kind = "date-strip" if c.get("is_dateish") in ("1", "True", "true") else "heading"
            marks.append((kind, c["text"][:40]))
    # intervening TEXT between the regions (headings/prose in ANY font size —
    # DBS prints section headings at body size, invisible to candidates; and
    # commentary between two tables separates them just as hard). Page
    # furniture (running headers/footers/page numbers) never counts.
    ws, _, _ = words[rB["page"]]
    strip = [w for w in ws
             if (rA["y1"] + 2 if same_page else -1e9) < float(w["top"]) < rB["y0"] - 4]
    lines = {}
    for w in strip:
        lines.setdefault(round(float(w["top"]) / 2.5), []).append(w)
    for key in sorted(lines):
        lws = sorted(lines[key], key=lambda w: float(w["x0"]))
        txt = " ".join(w["text"] for w in lws)
        squash = re.sub(r"[^a-z0-9]", "", txt.casefold())
        if not squash or squash in furn:
            continue                      # page furniture NEVER counts (incl. dated
        if DATEISH.search(txt):           # running headers — the stranded-date trap)
            marks.append(("date-strip", txt[:40]))
            continue
        if re.search(r"\(cont(?:'d|inued)?\.?\)", txt, re.I):
            continue                      # '(cont'd)' captions are NEUTRAL: banks
                                          # print them on new tables AND omit them
                                          # on real continuations — never evidence
        alpha = re.sub(r"[^A-Za-z]", "", txt)
        if len(squash) < 12 and not (alpha and alpha.isupper()):
            continue                      # stray marks; short ALL-CAPS headings count
        marks.append(("text-between", txt[:40]))
    # same-page pure-whitespace gap
    if same_page and not strip and (rB["y0"] - rA["y1"]) > GAP_BREAK_PT:
        marks.append(("whitespace-gap", f"{rB['y0']-rA['y1']:.0f}pt"))
    return marks


_UNIT_LABEL = re.compile(
    r"^\(?(?:in\s+)?s?\$\s?(?:m\b|millions?|'?000)\)?$|^\(\$m\)$|^%$", re.I)


def rows_continue(featA, featB):
    """CHECK 3. Returns (verdict, detail): True=continue, False=restart/repeat.
    Unit/currency lines ('S$ million', 'In $ millions') are NOT row labels —
    every statement repeats them; needs >=2 real repeated labels to call a
    restart (one shared label like 'Net profit' recurs across DIFFERENT
    statements legitimately)."""
    def real(lb):
        return len(lb) > 3 and not _UNIT_LABEL.match(lb)
    seenA = {norm(r["label"]) for r in featA["rows"] if real(norm(r["label"]))}
    headA = norm(featA["rows"][0]["full"]) if featA["rows"] else ""
    early = featB["rows"][:8]
    repeats = []
    for i, r in enumerate(early):
        if i == 0 and norm(r["full"]) == headA:
            continue                     # reprinted header row: tolerated
        lb = norm(r["label"])
        if real(lb) and lb in seenA:
            repeats.append(r["label"])
    if len(repeats) >= 2:
        return False, f"repeats {repeats[:3]}"
    dA, dB = set(featA["dates"]), set(featB["dates"])
    if dA and dB and dA != dB and not (dA & dB):
        return False, f"date change {sorted(dA)} -> {sorted(dB)}"
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default="", help="e.g. 17-19 (default: whole doc)")
    args = ap.parse_args()

    regs = load_regions()
    cands = load_candidates()
    words = words_by_page()
    if args.pages:
        a, _, b = args.pages.partition("-")
        lo, hi = int(a), int(b or a)
        regs = [r for r in regs if lo <= r["page"] <= hi]

    feats = [region_features(r, words[r["page"]][0]) for r in regs]

    print(f"{len(regs)} regions in scope\n")
    for r, f in zip(regs, feats):
        print(f"  p{r['page']:>2} #{r['idx']} y {r['y0']:.0f}-{r['y1']:.0f} "
              f"cols={len(f['sig'])} rows={len(f['rows'])} dates={f['dates'][:3]}")

    print("\nPAIRWISE VERDICTS:")
    chains = 1 if regs else 0
    for i in range(1, len(regs)):
        rA, rB, fA, fB = regs[i - 1], regs[i], feats[i - 1], feats[i]
        gap_pages = rB["page"] - rA["page"]
        if gap_pages > 1:
            print(f"  p{rA['page']}#{rA['idx']} -> p{rB['page']}#{rB['idx']}: "
                  f"NEW_TABLE (non-adjacent pages)")
            chains += 1
            continue
        marks = break_between(rA, rB, cands, words, fA, fB)
        cols = sig_match(fA, fB)
        cont, detail = rows_continue(fA, fB)
        # adjacency (soft, reported): does rA reach body bottom / rB start at top?
        _, top_a, bot_a = words[rA["page"]]
        adj = (bot_a - rA["y1"] <= MARGIN_PT) if gap_pages == 1 else True
        if cols is False:
            v = "NEW_TABLE (cols differ)"
            chains += 1
        elif not cont:
            v = f"PERIOD_INSTANCE ({detail})"
            chains += 1
        elif marks:
            v = f"NEW_TABLE (break: {marks[0][0]} '{marks[0][1]}')"
            chains += 1
        elif cols is None:
            v = "FLAG (col sig not evaluable)"
            chains += 1
        else:
            v = "STITCH"
        print(f"  p{rA['page']}#{rA['idx']} -> p{rB['page']}#{rB['idx']}: {v}"
              f"   [cols={'?' if cols is None else cols} cont={cont}"
              f" breaks={len(marks)} bottom-adj={adj}]")
    print(f"\nLOGICAL TABLES in scope: {chains}")


if __name__ == "__main__":
    main()
