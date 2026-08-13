"""layout_scan — TABLE-REGION detection over a full document (T4-flavored).

ONE model: PP-DocLayout-L (chosen 2026-07-08 benchmark: PicoDet-S missed a real
single-table page; plus-L costs +40% latency for identical detections). Per page:
render 200 DPI -> layout predict -> keep label=='table' boxes -> overlay PNG with
red boxes + regions.json. Then aggregate table counts per printed-TOC section
(toc.json page ranges) into a TOC-with-counts CSV.

RUNS ONLY IN .venv-paddle:
  .venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/layout_scan.py \
      findociq/data/sources/pillar3/DBS_4Q25_Pillar3.pdf \
      findociq/_legacy/DELIVERABLE/outputs/pillar3/dbs_4Q25/toc.json \
      dbs_4q25
"""
import csv
import json
import os
import sys
import time

import pdfplumber
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "PP-DocLayout-L"
DPI = 200


def main(pdf_path: str, toc_path: str, tag: str):
    out = os.path.join(HERE, "outputs", f"{tag}_layout")
    os.makedirs(out, exist_ok=True)

    from paddlex import create_model
    model = create_model(model_name=MODEL)

    regions = {}
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        for pno in range(1, n + 1):
            png = os.path.join(out, f"{pno:03d}.png")
            if not os.path.exists(png):
                pdf.pages[pno - 1].to_image(resolution=DPI).save(png)
            t0 = time.time()
            res = list(model.predict(png))[0]
            tabs = [dict(box=[float(v) for v in b["coordinate"]],
                         score=float(b["score"]))
                    for b in res["boxes"] if b["label"] == "table"]
            regions[pno] = tabs
            img = Image.open(png).convert("RGB")
            dr = ImageDraw.Draw(img)
            for t in tabs:
                dr.rectangle(t["box"], outline=(220, 30, 30), width=6)
            img.save(os.path.join(out, f"{pno:03d}_tables.png"))
            print(f"p{pno}/{n}: {len(tabs)} table(s)  {time.time()-t0:.1f}s", flush=True)

    json.dump(regions, open(os.path.join(out, "regions.json"), "w"), indent=1)

    # ---- aggregate per printed-TOC section
    toc = json.load(open(toc_path))
    rows = []
    for s in toc["sections"]:
        p0, p1 = int(s["start_page"]), int(s["end_page"])
        cnt = sum(len(regions.get(p, [])) for p in range(p0, p1 + 1))
        rows.append(dict(section_id=s.get("section_id") or s.get("number"),
                         title=s["title"], pages=f"{p0}-{p1}", n_tables=cnt))
    cp = os.path.join(out, "toc_table_counts.csv")
    with open(cp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["section_id", "title", "pages", "n_tables"])
        w.writeheader()
        w.writerows(rows)
    attributed = {p for s in toc["sections"]
                  for p in range(int(s["start_page"]), int(s["end_page"]) + 1)}
    orphans = {p: len(t) for p, t in regions.items() if t and p not in attributed}
    print(f"\nTOC sections: {len(rows)} | total tables detected: "
          f"{sum(len(t) for t in regions.values())} | on pages outside any "
          f"TOC section: {orphans}")
    print(f"counts -> {cp}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
