"""harden.py — run the 3-check stitcher over every scanned FS doc.

For each paddle_out/<tag>/ with a regions.csv, locates the source PDF by stem,
computes pairwise verdicts for page-adjacent region pairs, writes
paddle_out/<tag>/stitch_verdicts.csv, and prints a per-doc + corpus summary
with every non-STITCH-non-obvious case listed for review.

Run: python3 harden.py            (base venv; no Paddle needed)
"""
import csv
import re
from pathlib import Path

import pdfplumber

import stitch_demo as S   # reuse the parameterized core: features/checks

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
FS_ROOT = REPO / "findociq" / "data" / "sources" / "financial_statements"
OUT = HERE / "paddle_out"


def pdf_for(tag):
    for pdf in FS_ROOT.rglob("*.pdf"):
        if pdf.stem.replace(" ", "_") == tag:
            return pdf
    return None


def load_scan(tag):
    regs = [{"page": int(r["page"]), "idx": int(r["table_idx"]),
             "x0": float(r["x0"]), "y0": float(r["y0"]),
             "x1": float(r["x1"]), "y1": float(r["y1"])}
            for r in csv.DictReader(open(OUT / tag / "regions.csv"))]
    cands = list(csv.DictReader(open(OUT / tag / "candidates.csv")))
    return sorted(regs, key=lambda r: (r["page"], r["y0"])), cands


def run_doc(tag):
    pdf_path = pdf_for(tag)
    if pdf_path is None:
        return None
    regs, cands = load_scan(tag)
    words = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, pg in enumerate(pdf.pages, start=1):
            words[i] = (pg.extract_words(), float(pg.bbox[1]), float(pg.bbox[3]))
    feats = [S.region_features(r, words[r["page"]][0]) for r in regs]

    verdicts = []
    for i in range(1, len(regs)):
        rA, rB, fA, fB = regs[i - 1], regs[i], feats[i - 1], feats[i]
        if rB["page"] - rA["page"] > 1:
            v, detail = "NEW_TABLE", "non-adjacent pages"
        else:
            marks = S.break_between(rA, rB, cands, words, fA, fB)
            cols = S.sig_match(fA, fB)
            cont, cdetail = S.rows_continue(fA, fB)
            if cols is False:
                v, detail = "NEW_TABLE", "cols differ"
            elif not cont:
                # rows restart/repeat or date change: a dated re-instance of the
                # same shape — takes precedence over break marks (comparatives
                # legitimately have a date strip/heading between them)
                v, detail = "PERIOD_INSTANCE", cdetail
            elif marks:
                v, detail = "NEW_TABLE", f"break:{marks[0][0]}:{marks[0][1]}"
            elif cols is None:
                v, detail = "FLAG", "col sig not evaluable"
            else:
                v, detail = "STITCH", f"{len(fA['sig'])} cols match"
        verdicts.append({
            "pageA": rA["page"], "idxA": rA["idx"],
            "pageB": rB["page"], "idxB": rB["idx"],
            "cross_page": int(rA["page"] != rB["page"]),
            "verdict": v, "detail": detail,
        })
    with open(OUT / tag / "stitch_verdicts.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(verdicts[0].keys()) if verdicts
                           else ["pageA", "idxA", "pageB", "idxB", "cross_page", "verdict", "detail"])
        w.writeheader()
        w.writerows(verdicts)
    chains = 1 + sum(1 for v in verdicts if v["verdict"] != "STITCH") if regs else 0
    return {"tag": tag, "regions": len(regs), "pairs": len(verdicts),
            "chains": chains, "verdicts": verdicts}


def main():
    tags = sorted(p.name for p in OUT.iterdir()
                  if (p / "regions.csv").exists())
    totals = {"STITCH": 0, "NEW_TABLE": 0, "PERIOD_INSTANCE": 0, "FLAG": 0}
    interesting = []
    print(f"{'doc':52} {'regs':>4} {'pairs':>5} {'stitch':>6} {'newtbl':>6} "
          f"{'period':>6} {'flag':>4} {'tables':>6}")
    for tag in tags:
        r = run_doc(tag)
        if r is None:
            print(f"{tag:52} — NO PDF MATCH")
            continue
        c = {"STITCH": 0, "NEW_TABLE": 0, "PERIOD_INSTANCE": 0, "FLAG": 0}
        for v in r["verdicts"]:
            c[v["verdict"]] += 1
            totals[v["verdict"]] += 1
            # review-worthy: any cross-page decision, every stitch/period/flag
            if v["verdict"] in ("STITCH", "PERIOD_INSTANCE", "FLAG") or v["cross_page"]:
                interesting.append((tag, v))
        print(f"{tag:52} {r['regions']:>4} {r['pairs']:>5} {c['STITCH']:>6} "
              f"{c['NEW_TABLE']:>6} {c['PERIOD_INSTANCE']:>6} {c['FLAG']:>4} "
              f"{r['chains']:>6}")
    print(f"\nCORPUS: {totals}")
    print("\nREVIEW CASES (all stitches/period-instances/flags + cross-page calls):")
    for tag, v in interesting:
        short = re.sub(r"_(performance|condensed|Results|Media|Unaudited).*", "", tag)[:28]
        print(f"  {short:28} p{v['pageA']}#{v['idxA']}->p{v['pageB']}#{v['idxB']} "
              f"{'X' if v['cross_page'] else ' '} {v['verdict']:16} {v['detail'][:60]}")


if __name__ == "__main__":
    main()
