"""run_demo.py — contract-v2 demo on DBS 2Q25 performance summary.

Tests the user-proposed richer TOC contract (id/page_end/parent_id/kind/
expected_table_count/notes) two ways:
  1. TWO independent Gemini runs (temperature 0) — id/granularity stability.
  2. A deterministic pdfplumber referee (ruled-table count + numeric-token
     density per page) to score kind/expected_table_count against.

Run: python3 run_demo.py   (key auto-loaded from findociq/.env)
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import pdfplumber

HERE = Path(__file__).resolve().parent
SPIKE = HERE.parent
REPO = SPIKE.parents[2]
sys.path.insert(0, str(SPIKE))
sys.path.insert(0, str(REPO / "findociq" / "app"))
from spec import parse_llm_json          # noqa: E402
from run_spike import load_env_key       # noqa: E402

PDF = (REPO / "findociq/data/sources/financial_statements/DBS/2025/2Q25"
       / "DBS_2Q25_performance_summary.pdf")
PROMPT = (HERE / "prompt_v2.txt").read_text()
MODEL = "gemini-3.5-flash"

_NUM_WORD = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?%?$")


def referee():
    """Per-page deterministic signals: ruled-table count + numeric-word count."""
    out = {}
    with pdfplumber.open(PDF) as pdf:
        for i, pg in enumerate(pdf.pages, start=1):
            words = [w["text"] for w in pg.extract_words()]
            out[i] = {
                "ruled_tables": len(pg.find_tables()),
                "num_words": sum(1 for w in words if _NUM_WORD.match(w)),
            }
    return out


def gemini_run(client, uploaded, tag):
    from google.genai import types
    for attempt in range(4):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=[uploaded, PROMPT],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
            parsed = parse_llm_json(resp.text)
            secs = parsed["sections"]
            (HERE / f"{tag}.json").write_text(json.dumps(parsed, indent=2))
            print(f"[{tag}] ok — {len(secs)} sections", flush=True)
            return secs
        except Exception as exc:  # noqa: BLE001 — spike: retry transient failures
            wait = 2 * (2 ** attempt)
            print(f"[{tag}] attempt {attempt+1} failed: "
                  f"{type(exc).__name__}: {str(exc)[:120]} (retry {wait}s)", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"{tag}: all attempts failed")


def main():
    load_env_key()
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY not set")

    print("referee: pdfplumber per-page scan...", flush=True)
    ref = referee()
    (HERE / "referee.json").write_text(json.dumps(ref, indent=2))

    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    uploaded = client.files.upload(file=str(PDF))
    for _ in range(30):
        f = client.files.get(name=uploaded.name)
        state = getattr(getattr(f, "state", None), "name", "")
        if state == "ACTIVE":
            break
        if state == "FAILED":
            raise RuntimeError("file processing FAILED")
        time.sleep(2)

    r1 = gemini_run(client, uploaded, "run1")
    r2 = gemini_run(client, uploaded, "run2")

    # ---- stability report ------------------------------------------------
    def norm(t):
        return " ".join(t.split()).casefold()
    m1 = {norm(s["title"]): s["id"] for s in r1}
    m2 = {norm(s["title"]): s["id"] for s in r2}
    common = set(m1) & set(m2)
    same_id = sum(1 for t in common if m1[t] == m2[t])
    print(f"\nSTABILITY: run1={len(r1)} run2={len(r2)} sections; "
          f"{len(common)} titles common; same sec_NNN id for {same_id}/{len(common)}")
    only1 = sorted(set(m1) - set(m2))
    only2 = sorted(set(m2) - set(m1))
    if only1:
        print(f"  only in run1 ({len(only1)}): {only1[:6]}")
    if only2:
        print(f"  only in run2 ({len(only2)}): {only2[:6]}")

    # ---- referee comparison (leaf sections of run1) ------------------------
    kids = {s["id"] for s in r1 if s.get("parent_id")}
    parents = {s.get("parent_id") for s in r1 if s.get("parent_id")}
    print("\nKIND / COUNT vs REFEREE (run1, leaf sections only):")
    print(f"{'id':7} {'pages':7} {'kind':14} {'exp':>3} {'ruled':>5} {'numw':>5}  title")
    for s in r1:
        if s["id"] in parents:      # leaf = never referenced as a parent
            continue
        p0, p1 = int(s["page_start"]), int(s.get("page_end") or s["page_start"])
        ruled = sum(ref[p]["ruled_tables"] for p in range(p0, p1 + 1) if p in ref)
        numw = sum(ref[p]["num_words"] for p in range(p0, p1 + 1) if p in ref)
        pr = f"{p0}" if p0 == p1 else f"{p0}-{p1}"
        print(f"{s['id']:7} {pr:7} {s.get('kind',''):14} "
              f"{s.get('expected_table_count','?'):>3} {ruled:>5} {numw:>5}  {s['title'][:56]}")


if __name__ == "__main__":
    main()
