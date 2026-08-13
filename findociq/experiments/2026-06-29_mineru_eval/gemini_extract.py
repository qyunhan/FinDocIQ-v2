"""gemini_extract — Stage-2 HTML extraction via the Gemini API (the baseline backend).

Reads the verbatim prompts from ../../pipeline/prompts/, sends a PDF as a native
application/pdf Part, returns the Plan-9 HTML contract (response_mime_type=text/plain).
Mirrors the production transport (model gemini-3.5-flash, temperature 0, thinking 8192),
with 503-backoff. Saves HTML to samples/ and reports token usage.

Usage:
    set -a; . ../../.env; set +a
    python3 gemini_extract.py <pdf> --framing spanning --section 12.9 \
        --title "SA(CR) - Exposures by Asset Classes and Risk Weights" \
        --pages "1-4" --out samples/gemini35_uob_12_9.html
"""
from __future__ import annotations
import os, sys, time, argparse
from google import genai
from google.genai import types, errors as genai_errors

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = os.path.normpath(os.path.join(HERE, "..", "..", "pipeline", "prompts"))
MODEL = "gemini-3.5-flash"


def _core() -> str:
    return open(os.path.join(PROMPTS, "stage2_core.txt")).read().strip()


def _framing(kind: str, section: str, title: str, pages: str) -> str:
    """Pull the named framing block from stage2_framings.txt and fill placeholders."""
    text = open(os.path.join(PROMPTS, "stage2_framings.txt")).read()
    token = {"single": "SINGLE", "spanning": "SPANNING", "multiple": "MULTIPLE"}[kind]
    body, capturing = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("==="):           # a header line
            if capturing:                 # reached the NEXT section -> stop
                break
            capturing = token in s        # entering our section (skip the header itself)
            continue
        if capturing:
            if s.startswith("#"):         # stop at first comment (modifier/notes follow)
                break
            body.append(line)
    body = "\n".join(body).strip()
    return (body.replace("<num>", section).replace("<title>", title)
                .replace("P-Q", pages).replace("page P", f"page {pages}"))


def extract(pdf_path: str, framing: str, section: str, title: str, pages: str,
            out_path: str, model: str = MODEL) -> dict:
    prompt = _framing(framing, section, title, pages) + "\n\n" + _core()
    client = genai.Client()
    parts = [types.Part.from_bytes(data=open(pdf_path, "rb").read(), mime_type="application/pdf"),
             types.Part(text=prompt)]
    cfg = types.GenerateContentConfig(response_mime_type="text/plain",
                                      temperature=0.0, max_output_tokens=65536)
    try:
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=0)   # validated: 0 for extraction
    except Exception:
        pass
    # --- pre-run cost estimate (before any generation is billed) ---
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pipeline"))
    import cost
    n_pages = sum((int(b) - int(a) + 1) if "-" in p else 1
                  for p in pages.split(",") for a, b in [p.split("-") if "-" in p else (p, p)])
    n_in = cost.count_input(client, model, parts) or cost.local_input_estimate(prompt, n_pages)
    print(cost.preflight(n_in, label=f"[{model}]"), flush=True)
    resp = None
    for attempt in range(12):
        try:
            resp = client.models.generate_content(model=model, contents=parts, config=cfg)
            break
        except genai_errors.ServerError:
            w = min(8 * (attempt + 1), 60)
            print(f"  503 (try {attempt+1}/12) — wait {w}s", flush=True)
            time.sleep(w)
    if resp is None:
        raise SystemExit(f"{model} saturated after retries")
    html = resp.text or ""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w").write(html)
    um = resp.usage_metadata
    usage = dict(model=model, in_=um.prompt_token_count, out=um.candidates_token_count,
                 think=getattr(um, "thoughts_token_count", None), total=um.total_token_count,
                 chars=len(html), n_tables=html.count("<table"))
    print(f"  ✓ {out_path}  tokens in/out/think/total="
          f"{usage['in_']}/{usage['out']}/{usage['think']}/{usage['total']}  "
          f"tables={usage['n_tables']} chars={usage['chars']}")
    return usage


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--framing", default="spanning", choices=["single", "spanning", "multiple"])
    ap.add_argument("--section", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--pages", default="1-1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()
    extract(a.pdf, a.framing, a.section, a.title, a.pages, a.out, a.model)
