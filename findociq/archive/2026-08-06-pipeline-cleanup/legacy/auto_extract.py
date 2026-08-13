"""auto_extract — automated OBSERVE→EXTRACT pipeline (HTML out). universal/ = runs unattended.

Per slide page: OBSERVE (structure + spatial failure-mode discipline) → observation text,
then EXTRACT that page to HTML (one <table data-element="…"> per element) guided by the
observation. Two Gemini calls per slide, NO plan step. Output HTML → html_to_cells → schema_v5,
the SAME path as Pillar 3 (no JSON schema, no render_json_to_excel).

Usage:
    python3 auto_extract.py <deck.pdf> --pages 1-30 --out out/deck
    python3 auto_extract.py <deck.pdf> --pages 3 --dry-run   # assemble prompts, no API call
"""
from __future__ import annotations
import os, sys, io, time, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = os.path.normpath(os.path.join(HERE, "..", "prompts"))
MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-latest"]   # fallback chain


def _p(name: str) -> str:
    return open(os.path.join(PROMPTS, name)).read()

def observe_prompt() -> str:
    return _p("slide_observe.txt")

def extract_prompt(observation: str) -> str:
    return _p("slide_extract_html.txt").replace("{OBSERVE_OUTPUT}", observation) + "\n\n" + _p("stage2_core.txt")


def _render(pdf_path: str, page_1based: int, scale: float = 2.5) -> bytes:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_path)
    pil = doc[page_1based - 1].render(scale=scale).to_pil()
    b = io.BytesIO(); pil.save(b, "PNG"); return b.getvalue()


def _call(client, img: bytes, text: str):
    from google.genai import types, errors
    parts = [types.Part.from_bytes(data=img, mime_type="image/png"), types.Part(text=text)]
    cfg = types.GenerateContentConfig(response_mime_type="text/plain", temperature=0.0, max_output_tokens=65536)
    try: cfg.thinking_config = types.ThinkingConfig(thinking_budget=0)   # validated: 0 for extraction
    except Exception: pass
    for model in MODELS:
        for attempt in range(4):
            try:
                return client.models.generate_content(model=model, contents=parts, config=cfg)
            except errors.ServerError:
                time.sleep(6 * (attempt + 1))                      # 503 saturation — retry
            except errors.ClientError as e:
                if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                    raise SystemExit("Gemini credits depleted (429) — top up to run.")
                break                                              # other client error — try next model
    raise RuntimeError("all models unavailable")


def _cost(resp) -> float:
    sys.path.insert(0, os.path.join(HERE, ".."))
    import cost
    um = resp.usage_metadata
    return cost.dollars(um.prompt_token_count or 0, um.candidates_token_count or 0,
                        getattr(um, "thoughts_token_count", 0) or 0)

def extract_page(pdf: str, page: int, out_dir: str) -> float:
    sys.path.insert(0, os.path.join(HERE, ".."))
    from gemini_client import build_client
    client = build_client()
    img = _render(pdf, page)
    obs_r = _call(client, img, observe_prompt()); observation = obs_r.text or ""
    html_r = _call(client, img, extract_prompt(observation)); html = html_r.text or ""
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, f"p{page}_observe.txt"), "w").write(observation)
    open(os.path.join(out_dir, f"p{page}.html"), "w").write(html)
    c = _cost(obs_r) + _cost(html_r)
    print(f"  ✓ p{page}: {html.count('<table')} element(s), obs {len(observation)}c → html {len(html)}c  ${c:.4f}", flush=True)
    return c


def _pages(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-"); out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("--pages", default="1")
    ap.add_argument("--out", default="out/deck"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        print("=== OBSERVE prompt (head) ===\n" + observe_prompt()[:400])
        print("\n=== EXTRACT prompt assembly check (slide rules + injected observation + table core) ===")
        ep = extract_prompt("[OBSERVATION WOULD BE INJECTED HERE]")
        print(ep[:300], "\n…\n", ep[-300:])
        print(f"\n[dry-run] pages that would run: {_pages(a.pages)}  (no API calls)")
        sys.exit(0)
    pages = _pages(a.pages)
    total = sum(extract_page(a.pdf, pg, a.out) for pg in pages)
    print(f"\ntotal: {len(pages)} slide(s) → ${total:.4f}  (out: {a.out})")
