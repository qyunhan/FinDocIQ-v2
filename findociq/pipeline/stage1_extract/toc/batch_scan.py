"""batch_scan.py — STEP 0 of the FS pipeline: scan ALL FS PDFs in ONE process
(the heavy PaddleOCR import is paid once). Emits candidates.csv / regions.csv /
stitch_verdicts.csv per doc into --out/<tag>/, consumed by the TOC stage.

Companion to candidates.py (emit_candidates lives there). Skips docs whose
regions.csv already exists. Run UNSANDBOXED in .venv-paddle (the sandbox blocks
the libomp dylib mmap):
  .venv-paddle/bin/python3 -u findociq/pipeline/discover/section/batch_scan.py
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent           # discover/section/
REPO = HERE.parents[4]                            # section -> discover -> pipeline -> findociq -> repo
sys.path.insert(0, str(HERE))
from stage1_extract.toc.candidates import emit_candidates  # noqa: E402  (heavy paddle import, once)

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--root", default=None,
                help="FS sources root; default findociq/data/sources/financial_statements")
ap.add_argument("--out", default=None,
                help="scan output root; default findociq/data/derived/paddle_scans")
args = ap.parse_args()

FS_ROOT = (Path(args.root).resolve() if args.root
           else REPO / "findociq" / "data" / "sources" / "financial_statements")
OUT = (Path(args.out).resolve() if args.out
       else REPO / "findociq" / "data" / "derived" / "paddle_scans")
OUT.mkdir(parents=True, exist_ok=True)

pdfs = sorted(FS_ROOT.rglob("*.pdf"))
print(f"{len(pdfs)} docs; output -> {OUT}", flush=True)
t_all = time.time()
for pdf in pdfs:
    tag = pdf.stem.replace(" ", "_")
    if (OUT / tag / "regions.csv").exists():
        print(f"[{tag}] SKIP (regions.csv exists)", flush=True)
        continue
    t0 = time.time()
    try:
        stats = emit_candidates(str(pdf), tag, str(OUT))
        print(f"[{tag}] done in {time.time()-t0:.0f}s — "
              f"{stats.get('n_regions','?')} regions, "
              f"{stats.get('n_candidates','?')} candidates", flush=True)
    except Exception as exc:  # noqa: BLE001 — keep batch going, report loudly
        print(f"[{tag}] ERROR {type(exc).__name__}: {exc}", flush=True)
print(f"ALL DONE in {time.time()-t_all:.0f}s", flush=True)
