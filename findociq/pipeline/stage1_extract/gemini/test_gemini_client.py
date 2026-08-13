"""Plain check() script for gemini_client.parse_llm_json (NO pytest).
Exit 0 all-pass / 1 any-fail.

Run:  python3 findociq/pipeline/test_gemini_client.py

These checks came from app/test_spec.py when parse_llm_json moved out of the app
tree into the pipeline (2026-08-12) — pipeline/toc/toc_stage.py is the live caller,
so the coverage belongs here, not next to a retired Streamlit app.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.gemini.gemini_client import LLMResponseError, parse_llm_json  # noqa: E402

_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    if not cond:
        _FAILS += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


print("parse_llm_json — robust extraction against realistic LLM chatter")

d = parse_llm_json('Sure thing (see {note above}) here you go: {"concepts": ["asf_total"]}')
check("leading prose w/ stray brace parses", d == {"concepts": ["asf_total"]}, str(d))

d = parse_llm_json('{"concepts": ["asf_total"]} — hope that helps {not json}')
check("trailing prose w/ stray brace parses", d == {"concepts": ["asf_total"]}, str(d))

d = parse_llm_json('{"concepts": ["asf_total"]} also consider {"concepts": ["rsf_total"]}')
check("second JSON-ish aside -> first object wins", d == {"concepts": ["asf_total"]}, str(d))

d = parse_llm_json('```JSON\n{"concepts": ["asf_total"]}\n```')
check("uppercase JSON fence parses", d == {"concepts": ["asf_total"]}, str(d))

d = parse_llm_json('```json\n{"a": 1}\n```')
check("lowercase json fence parses", d == {"a": 1}, str(d))

try:
    parse_llm_json("I cannot answer that. {not json at all")
    check("pure prose w/ no valid object raises", False)
except LLMResponseError:
    check("pure prose w/ no valid object raises", True)

print()
if _FAILS:
    print(f"{_FAILS} FAILED")
    sys.exit(1)
print("ALL PASS")
