"""gemini_arrange.py — DEPRECATED thin back-compat shim.

The Gemini no-TOC branch of section->table tagging moved to
sections_from_gemini.py (see findociq/docs/specs/2026-07-09-section-table-
tagging-design.md, AMENDMENT 2026-07-09 PM). The old one-shot design — Gemini
emitting TOC + all table assignments in a single call, with a deterministic
gap-filler covering dropped assignments — is RETIRED: it dropped assignments
and lumped subsection tables to their parent note. Gemini now only validates/
arranges HEADINGS; a shared deterministic module (assign_tables.py) does all
table assignment for both the TOC and no-TOC branches.

This module only re-exports `attribute_from_gemini` for callers that haven't
migrated their import yet. New code should import from sections_from_gemini
directly.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from sections_from_gemini import (  # noqa: F401,E402  (re-export for back-compat)
    SectionArrangeError,
    attribute_from_gemini,
    build_prompt,
    gemini_llm,
)

# Old name some call sites may still reference; same exception class.
GeminiArrangeError = SectionArrangeError

if __name__ == "__main__":
    from sections_from_gemini import main
    main()
