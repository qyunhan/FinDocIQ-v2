"""cost — Gemini pricing + a pre-run cost estimate.

Prices ($/M tokens, gemini-3.5-flash):  input 0.30 · output 2.50 · thinking 3.50.
Thinking is the priciest tier — keep thinking_budget=0 for extraction.

Pre-flight estimate BEFORE a call so a run never surprises you on cost.
  - input tokens: exact via client.models.count_tokens (free, no generation credits),
    with a local fallback (prompt chars/4 + ~560 per PDF page).
  - output tokens: unknown until generated → a low/high band (default 1.5k–12k).
"""
from __future__ import annotations

IN_PER_M, OUT_PER_M, THINK_PER_M = 0.30, 2.50, 3.50

def dollars(inp: int, out: int, think: int = 0) -> float:
    return (inp * IN_PER_M + out * OUT_PER_M + think * THINK_PER_M) / 1e6

def count_input(client, model: str, parts) -> int | None:
    """Exact input tokens (count_tokens is free; may still work with depleted credits)."""
    try:
        return client.models.count_tokens(model=model, contents=parts).total_tokens
    except Exception:
        return None

def local_input_estimate(prompt: str, n_pages: int = 1) -> int:
    return len(prompt) // 4 + n_pages * 560            # ~560 tok per PDF page (image tokenisation)

def preflight(input_tokens: int, out_lo: int = 1500, out_hi: int = 12000,
              think: int = 0, label: str = "") -> str:
    lo, hi = dollars(input_tokens, out_lo, think), dollars(input_tokens, out_hi, think)
    warn = "  ⚠️ input is large" if input_tokens > 20000 else ""
    return (f"  est cost{(' '+label) if label else ''}: ${lo:.4f}–${hi:.4f}  "
            f"[input {input_tokens} tok @ $0.30/M{warn}; output ~{out_lo//1000}k–{out_hi//1000}k @ $2.50/M; "
            f"thinking {think}]")
