"""run_spike.py — FS Gemini TOC spike (produce-and-eyeball).

Raw whole-PDF Gemini pass over each readable FS document: no PaddleOCR / candidate
scaffolding. Emits per-doc section headings (finest granularity) + start pages, so
we can eyeball whether raw Gemini reproduces a usable TOC.

Spec: findociq/docs/specs/2026-07-12-fs-gemini-toc-spike-design.md
Run:   GEMINI_API_KEY=... python3 run_spike.py [--model gemini-3.5-flash] [--only <substr>]
"""
import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

import pdfplumber

# reuse the repo's JSON-hardening parser (handles fences / trailing junk)
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]                       # experiments/<dir>/ -> findociq -> repo
sys.path.insert(0, str(REPO / "findociq" / "app"))
from spec import parse_llm_json               # noqa: E402

FS_ROOT = REPO / "findociq" / "data" / "sources" / "financial_statements"
OUT_DIR = HERE / "outputs"
PROMPT = (HERE / "prompt.txt").read_text()


def load_env_key():
    """Prefer the valid key in findociq/.env over any stale GEMINI_API_KEY in the
    environment (the shell env can hold a revoked key; .env is the pipeline's source)."""
    envfile = REPO / "findociq" / ".env"
    if not envfile.exists():
        return
    import re
    for line in envfile.read_text().splitlines():
        m = re.match(r'\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*["\']?([^"\'\s]+)', line)
        if m:
            os.environ["GEMINI_API_KEY"] = m.group(1)
            return

CONTENTS_SCAN_PAGES = 12
_TOC_LABELS = ("contents", "index")


def iter_fs_pdfs():
    return sorted(FS_ROOT.rglob("*.pdf"))


def probe_and_contents(pdf_path):
    """Readability probe + lightweight printed-contents-page finder (eyeball aid).

    Returns (n_pages, contents_page_number|None, contents_page_text|"").
    Raises if the PDF is unreadable (caller marks status='unreadable')."""
    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages
        n = len(pages)
        if n == 0:
            raise ValueError("no pages")
        cpn, ctext = None, ""
        for idx, pg in enumerate(pages[:CONTENTS_SCAN_PAGES], start=1):
            t = pg.extract_text() or ""
            if any(lbl in t.casefold() for lbl in _TOC_LABELS):
                cpn, ctext = idx, t
                break
        return n, cpn, ctext


def gemini_sections(pdf_path, model, attempts=4):
    """Upload PDF via Files API, ask for headings, parse JSON. Retries with backoff."""
    from google import genai
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    last = None
    for i in range(attempts):
        try:
            uploaded = client.files.upload(file=str(pdf_path))
            # wait for the file to become ACTIVE (large PDFs need processing)
            for _ in range(30):
                f = client.files.get(name=uploaded.name)
                state = getattr(getattr(f, "state", None), "name", str(getattr(f, "state", "")))
                if state == "ACTIVE":
                    break
                if state == "FAILED":
                    raise RuntimeError("file processing FAILED")
                time.sleep(2)
            resp = client.models.generate_content(
                model=model,
                contents=[uploaded, PROMPT],
            )
            parsed = parse_llm_json(resp.text)
            secs = parsed.get("sections", [])
            if not isinstance(secs, list):
                raise ValueError(f"'sections' not a list: {type(secs)}")
            return secs
        except Exception as exc:  # noqa: BLE001 — spike: retry any transient failure
            last = exc
            wait = 2 * (2 ** i)
            print(f"    attempt {i+1}/{attempts} failed: {type(exc).__name__}: {str(exc)[:120]} "
                  f"(retry in {wait}s)", flush=True)
            time.sleep(wait)
    raise last


def run(model, only):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for pdf in iter_fs_pdfs():
        doc_id = pdf.stem.replace(" ", "_")
        if only and only.lower() not in doc_id.lower():
            continue
        print(f"[{doc_id}] {pdf.relative_to(REPO)}", flush=True)
        rec = {"doc_id": doc_id, "source_pdf": str(pdf.relative_to(REPO)),
               "model": model, "status": "ok", "sections": [],
               "contents_page_number": None, "contents_page_text": ""}
        try:
            n, cpn, ctext = probe_and_contents(pdf)
            rec["n_pages"] = n
            rec["contents_page_number"] = cpn
            rec["contents_page_text"] = ctext
        except Exception as exc:  # noqa: BLE001
            rec["status"] = "unreadable"
            rec["error"] = f"{type(exc).__name__}: {exc}"
            print(f"    UNREADABLE — skipping upload ({rec['error']})", flush=True)
            (OUT_DIR / f"{doc_id}.json").write_text(json.dumps(rec, indent=2))
            rows.append(rec)
            continue
        try:
            rec["sections"] = gemini_sections(pdf, model)
            print(f"    ok — {len(rec['sections'])} sections", flush=True)
        except Exception as exc:  # noqa: BLE001
            rec["status"] = "error"
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            print(f"    ERROR — {rec['error']}", flush=True)
            traceback.print_exc()
        (OUT_DIR / f"{doc_id}.json").write_text(json.dumps(rec, indent=2))
        rows.append(rec)
    write_index(rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"\nDONE — {ok}/{len(rows)} ok. Outputs: {OUT_DIR.relative_to(REPO)}", flush=True)


def write_index(rows):
    lines = ["# FS Gemini TOC spike — results index", ""]
    for r in rows:
        lines.append(f"## {r['doc_id']}  ({r['status']})")
        lines.append(f"`{r['source_pdf']}`" + (f" — {r.get('n_pages','?')}pp" if r.get("n_pages") else ""))
        if r["status"] != "ok":
            lines.append(f"\n> {r.get('error','')}\n")
            continue
        cpn = r.get("contents_page_number")
        lines.append(f"\n**Printed contents page:** "
                     + (f"p{cpn}" if cpn else "_none detected_"))
        lines.append(f"\n**Gemini sections ({len(r['sections'])}):**\n")
        lines.append("| level | page | title |")
        lines.append("|---|---|---|")
        for s in r["sections"]:
            title = str(s.get("title", "")).replace("|", "\\|")
            lines.append(f"| {s.get('level','')} | {s.get('page','')} | {title} |")
        if cpn:
            lines.append(f"\n<details><summary>raw printed contents page (p{cpn})</summary>\n")
            lines.append("```\n" + r.get("contents_page_text", "") + "\n```\n</details>")
        lines.append("")
    (OUT_DIR / "index.md").write_text("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--only", default="", help="substring filter on doc_id (for a single retry)")
    args = ap.parse_args()
    load_env_key()
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY not set (checked env + findociq/.env)")
    run(args.model, args.only)
