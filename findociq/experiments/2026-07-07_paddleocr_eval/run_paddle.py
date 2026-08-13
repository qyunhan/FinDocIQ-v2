"""run_paddle — PP-StructureV3 over a doc's NSFR pages (default) or the whole doc (--full).

Persists, per page: the 200-DPI PNG the model actually saw, the raw result JSON, and the
markdown. Resumable: pages with an existing .json are skipped unless --force.

RUNS ONLY IN .venv-paddle:
  .venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/run_paddle.py dbs_4q23_p3
  .venv-paddle/bin/python .../run_paddle.py ocbc_4q24_p3 --full     # T3 capture, ~1h CPU
"""
import argparse, os, sys
import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import ALL_DOCS, DPI, nsfr_pages, section_pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id", choices=sorted(ALL_DOCS))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="whole document (T3), not NSFR pages")
    mode.add_argument("--section", help="capture a printed-TOC section by section_id (T4a: 12.9)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    doc = ALL_DOCS[a.doc_id]
    sub = "pages_full" if a.full else (f"pages_sec{a.section}" if a.section else "pages")
    outdir = os.path.join(HERE, "outputs", a.doc_id, sub)
    os.makedirs(outdir, exist_ok=True)

    from paddleocr import PPStructureV3
    # Subsystems OFF (2026-07-08): chart/formula segfault on this host and don't apply to
    # financial tables; orientation/unwarping/seal target photographed or scanned docs —
    # ours are digital-native PDFs. Task-scoped capability toggles (documented kwargs),
    # general to the whole document class, and they cut peak model-load memory (the host
    # is swap-constrained). Never disable the table subsystems themselves.
    pipe = PPStructureV3(device="cpu", use_chart_recognition=False,
                         use_formula_recognition=False,
                         use_doc_orientation_classify=False,
                         use_doc_unwarping=False,
                         use_seal_recognition=False)

    with pdfplumber.open(doc["pdf"]) as pdf:
        pages = (list(range(1, len(pdf.pages) + 1)) if a.full
                 else section_pages(doc["toc"], a.section) if a.section
                 else nsfr_pages(doc["toc"]))
        print(f"[{a.doc_id}] {'FULL' if a.full else 'NSFR'} pages {pages[0]}..{pages[-1]} ({len(pages)})",
              flush=True)
        for pno in pages:
            base = os.path.join(outdir, f"{pno:03d}")
            if os.path.exists(base + ".json") and not a.force:
                print(f"  p{pno}: exists, skip", flush=True); continue
            pdf.pages[pno - 1].to_image(resolution=DPI).save(base + ".png")
            results = list(pipe.predict(base + ".png"))
            if len(results) != 1:
                raise RuntimeError(f"p{pno}: expected 1 page result, got {len(results)}")
            res = results[0]
            # Adjust save calls to the dir/file convention Task 1's smoke run established;
            # the on-disk contract that MUST hold is: <outdir>/NNN.json and <outdir>/NNN.md.
            res.save_to_json(save_path=base + ".json")
            res.save_to_markdown(save_path=base + ".md")
            if not (os.path.exists(base + ".json") and os.path.getsize(base + ".json") > 0):
                raise RuntimeError(f"p{pno}: empty/missing PP-Structure JSON output")
            n = open(base + ".json").read().count("<table")
            print(f"  p{pno}: ok ({n} table html blocks)"
                  + ("" if n or a.full else "  [WARN: no table detected on a targeted page]"),
                  flush=True)
    print("done.")


if __name__ == "__main__":
    main()
