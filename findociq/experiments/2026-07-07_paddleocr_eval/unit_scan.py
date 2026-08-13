"""unit_scan — full-doc layout scan marking SECTION / TABLE / FOOTNOTE units.

All block geometry comes from PP-DocLayout-L (tables, paragraph_titles, texts).
pdfplumber supplies only the text INSIDE those boxes (digital-native PDFs, zero
tokens) so the units carry readable labels. Attachment rules (general, code-only):

  * a table's SECTION = nearest paragraph_title whose bottom is above the table
    top on the same page; if none, the section carries over from the previous
    page (continuation).
  * CAPTION = text blocks between that title (or page top) and the table top.
  * FOOTNOTES = text blocks below the table bottom and above the next
    title/table on the page.

Outputs per doc under outputs/<tag>_layout/:
  NNN_units.png   overlay: red=table, blue=paragraph_title, green=footnote,
                  orange=caption
  units.json      per-table records (page, bbox, section, caption, footnotes)
  paddle_toc.csv  Paddle-derived TOC: section title -> pages, table count
  + a normalized title match rate vs the printed toc.json (Gate-3 preview)

RUNS ONLY IN .venv-paddle:
  .venv-paddle/bin/python .../unit_scan.py <pdf> <toc.json> <tag>
"""
import csv
import json
import os
import re
import sys
import time

import pdfplumber
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "pipeline"))
from verify_cells import words_from_chars

MODEL = "PP-DocLayout-L"
DPI = 200
PT = 72.0 / DPI

COLORS = dict(table=(220, 30, 30), paragraph_title=(30, 90, 220),
              footnote=(30, 160, 60), caption=(240, 140, 20))


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def norm_title(s: str) -> str:
    """Casefold, collapse spaces, strip leading section numbering."""
    s = norm(s).casefold()
    return re.sub(r"^[a-z]?\.?\d+(\.\d+)*\.?\s*", "", s)


def text_in(words, box_px, origin=(0.0, 0.0)):
    """Words inside a Paddle box. CRITICAL: pdfplumber coords include the page
    bbox origin (DBS pages: (-12.64,-12.64)); rendered-pixel coords do not —
    px*72/DPI + origin aligns the spaces. Membership is OVERLAP-based (>=50% of
    the word's extent), not center-in-box: detector boxes are a few pt loose
    and adaptive token rebuilds can glue a long first word."""
    ox, oy = origin
    x0 = box_px[0] * PT + ox; y0 = box_px[1] * PT + oy
    x1 = box_px[2] * PT + ox; y1 = box_px[3] * PT + oy
    hit = []
    for w in words:
        wx = min(w["x1"], x1) - max(w["x0"], x0)
        if wx < 0.5 * (w["x1"] - w["x0"]):
            continue
        if y0 - 4.0 <= w["top"] <= y1 + 1.0:
            hit.append(w)
    hit.sort(key=lambda w: (round(w["top"]), w["x0"]))
    return norm(" ".join(w["text"] for w in hit))


def main(pdf_path: str, toc_path: str, tag: str):
    out = os.path.join(HERE, "outputs", f"{tag}_layout")
    os.makedirs(out, exist_ok=True)

    from paddlex import create_model
    model = create_model(model_name=MODEL)

    units, last_section = [], None
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        for pno in range(1, n + 1):
            png = os.path.join(out, f"{pno:03d}.png")
            if not os.path.exists(png):
                pdf.pages[pno - 1].to_image(resolution=DPI).save(png)
            page = pdf.pages[pno - 1]
            words = words_from_chars(page)
            origin = (float(page.bbox[0]), float(page.bbox[1]))
            t0 = time.time()
            res = list(model.predict(png))[0]
            blocks = [dict(label=b["label"],
                           box=[float(v) for v in b["coordinate"]])
                      for b in res["boxes"]]
            tables = sorted((b for b in blocks if b["label"] == "table"),
                            key=lambda b: b["box"][1])
            titles = sorted((b for b in blocks if b["label"] == "paragraph_title"),
                            key=lambda b: b["box"][1])
            texts = sorted((b for b in blocks if b["label"] == "text"),
                           key=lambda b: b["box"][1])
            for t in titles:
                t["text"] = text_in(words, t["box"], origin)

            img = Image.open(png).convert("RGB")
            dr = ImageDraw.Draw(img)
            for t in titles:
                dr.rectangle(t["box"], outline=COLORS["paragraph_title"], width=4)

            for ti, tb in enumerate(tables):
                top, bot = tb["box"][1], tb["box"][3]
                above = [t for t in titles if t["box"][3] <= top + 5]
                sec = above[-1]["text"] if above else None
                carried = sec is None
                if carried:
                    sec = last_section
                else:
                    last_section = sec
                next_top = min([t["box"][1] for t in titles if t["box"][1] > bot]
                               + [x["box"][1] for x in tables[ti + 1:]]
                               + [float("inf")])
                caption = [x for x in texts
                           if (above[-1]["box"][3] if above else 0) - 5
                           <= x["box"][1] and x["box"][3] <= top + 5]
                foots = [x for x in texts
                         if bot - 5 <= x["box"][1] and x["box"][3] <= next_top + 5]
                units.append(dict(
                    page=pno, bbox=tb["box"], section=sec, carried=carried,
                    caption=" | ".join(text_in(words, c["box"], origin)
                                       for c in caption),
                    footnotes=" | ".join(text_in(words, f["box"], origin)
                                         for f in foots)))
                dr.rectangle(tb["box"], outline=COLORS["table"], width=6)
                for c in caption:
                    dr.rectangle(c["box"], outline=COLORS["caption"], width=3)
                for f in foots:
                    dr.rectangle(f["box"], outline=COLORS["footnote"], width=4)
            img.save(os.path.join(out, f"{pno:03d}_units.png"))
            print(f"p{pno}/{n}: {len(tables)} table(s), {len(titles)} title(s)"
                  f"  {time.time()-t0:.1f}s", flush=True)

    json.dump(units, open(os.path.join(out, "units.json"), "w"), indent=1)

    # ---- Paddle-derived TOC (section -> pages, table count)
    toc_rows, seen = [], {}
    for u in units:
        key = u["section"] or "<NO SECTION>"
        if key not in seen:
            seen[key] = dict(section=key, first_page=u["page"],
                             last_page=u["page"], n_tables=0)
            toc_rows.append(seen[key])
        seen[key]["n_tables"] += 1
        seen[key]["last_page"] = u["page"]
    cp = os.path.join(out, "paddle_toc.csv")
    with open(cp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["section", "first_page",
                                           "last_page", "n_tables"])
        w.writeheader()
        w.writerows(toc_rows)

    # ---- printed-TOC title match rate (Gate-3 preview)
    printed = json.load(open(toc_path))["sections"]
    printed_norm = {norm_title(s["title"]): s for s in printed if s.get("title")}
    got_norm = {norm_title(r["section"]) for r in toc_rows if r["section"] != "<NO SECTION>"}
    matched = sum(1 for t in got_norm if t in printed_norm)
    print(f"\n[{tag}] tables total: {len(units)} | sections found: "
          f"{len(toc_rows)} | carried-over attributions: "
          f"{sum(1 for u in units if u['carried'])}")
    print(f"[{tag}] paddle sections matching printed TOC titles exactly "
          f"(normalized): {matched}/{len(got_norm)}")
    print(f"toc -> {cp}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
