"""run_cells_only — table-by-table arm: ONE Paddle model (table cell detection),
fed individual table crops instead of full pages.

Per captured page: the table bbox comes from the existing full-page capture's
cell_box_list extents (+ padding); the crop of the already-rendered 200-DPI PNG
is fed to the RT-DETR cell detector alone — no layout, no OCR, no classifier,
no structure model. Detected cell boxes are mapped back to full-page pixel
coordinates and written in the SAME capture shape
({"table_res_list":[{"cell_box_list": …}]}) under
outputs/<doc_id>/pages_cellsonly_<variant>/NNN.json, so fuse_cells.py consumes
them unchanged via its pages_sub switch.

Both detector variants run (wired = ruled prints, wireless = borderless) so the
scorecard can say whether the choice matters per render class.

RUNS ONLY IN .venv-paddle:
  .venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/run_cells_only.py dbs_4q23_p3 ocbc_4q24_p3
"""
import json
import os
import sys
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import ALL_DOCS

PAD_PX = 8
VARIANTS = {
    "wired": "RT-DETR-L_wired_table_cell_det",
    "wireless": "RT-DETR-L_wireless_table_cell_det",
}


def contiguous_block(boxes: list[list[float]]) -> list[list[float]]:
    """Clip a table's cell boxes to their largest vertically-CONTIGUOUS block.
    Paddle's full-page table region can bleed into content far below the real
    table (OCBC decoy strips, footnotes); a vertical gap several times the row
    pitch marks the break. Same density idea as the fragment-reconciliation
    rule — no doc-specific constants."""
    ys = sorted(set((b[1] + b[3]) / 2 for b in boxes))
    if len(ys) < 3:
        return boxes
    diffs = [b - a for a, b in zip(ys, ys[1:]) if b - a > 1.0]
    if not diffs:
        return boxes
    pitch = sorted(diffs)[len(diffs) // 2]
    runs, cur = [], [ys[0]]
    for a, b in zip(ys, ys[1:]):
        if b - a > 4.0 * pitch:
            runs.append(cur); cur = []
        cur.append(b)
    runs.append(cur)
    def members(run):
        lo, hi = run[0], run[-1]
        return [b for b in boxes if lo - 1 <= (b[1] + b[3]) / 2 <= hi + 1]
    best = max(runs, key=lambda r: len(members(r)))
    kept = members(best)
    if len(kept) < len(boxes):
        print(f"    [clip] {len(boxes)-len(kept)} below/above-gap cell boxes "
              f"excluded from crop region", flush=True)
    return kept


def result_boxes(res) -> list[list[float]]:
    """Normalize a paddlex detection result to flat [x0,y0,x1,y1] boxes."""
    if "boxes" in res:
        out = []
        for b in res["boxes"]:
            c = b["coordinate"] if isinstance(b, dict) else b
            out.append([float(v) for v in c])
        return out
    raise KeyError(f"unexpected detection result keys: {sorted(res.keys())}")


def main():
    doc_ids = sys.argv[1:]
    if not doc_ids:
        raise SystemExit("usage: run_cells_only.py <doc_id> [...]")

    from paddlex import create_model
    models = {}
    for var, name in VARIANTS.items():
        t0 = time.time()
        models[var] = create_model(model_name=name)
        print(f"[model] {name} loaded in {time.time()-t0:.1f}s", flush=True)

    for doc_id in doc_ids:
        pages_dir = os.path.join(HERE, "outputs", doc_id, "pages")
        pnos = sorted(int(f[:3]) for f in os.listdir(pages_dir) if f.endswith(".json"))
        for var in VARIANTS:
            os.makedirs(os.path.join(HERE, "outputs", doc_id,
                                     f"pages_cellsonly_{var}"), exist_ok=True)
        for pno in pnos:
            raw = json.load(open(os.path.join(pages_dir, f"{pno:03d}.json")))
            entries = raw.get("table_res_list", [])
            if not entries:
                print(f"[{doc_id}] p{pno}: no table in full-page capture, skip", flush=True)
                continue
            page_png = Image.open(os.path.join(pages_dir, f"{pno:03d}.png"))
            out = {v: [] for v in VARIANTS}
            for ti, entry in enumerate(entries):
                bx = contiguous_block(entry["cell_box_list"])
                x0 = max(0, min(b[0] for b in bx) - PAD_PX)
                y0 = max(0, min(b[1] for b in bx) - PAD_PX)
                x1 = min(page_png.width, max(b[2] for b in bx) + PAD_PX)
                y1 = min(page_png.height, max(b[3] for b in bx) + PAD_PX)
                crop_path = os.path.join(HERE, "outputs", doc_id,
                                         f"crop_{pno:03d}_{ti}.png")
                page_png.crop((x0, y0, x1, y1)).save(crop_path)
                for var, model in models.items():
                    t0 = time.time()
                    res = list(model.predict(crop_path))
                    if len(res) != 1:
                        raise RuntimeError(f"p{pno} t{ti} {var}: {len(res)} results")
                    boxes = [[b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0]
                             for b in result_boxes(res[0])]
                    if not boxes:
                        raise RuntimeError(f"p{pno} t{ti} {var}: zero cells detected")
                    out[var].append(dict(cell_box_list=boxes))
                    print(f"[{doc_id}] p{pno} t{ti} {var}: {len(boxes)} cells "
                          f"in {time.time()-t0:.1f}s", flush=True)
            for var, tables in out.items():
                op = os.path.join(HERE, "outputs", doc_id,
                                  f"pages_cellsonly_{var}", f"{pno:03d}.json")
                json.dump(dict(table_res_list=tables), open(op, "w"))
    print("done.")


if __name__ == "__main__":
    main()
