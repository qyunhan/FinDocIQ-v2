"""concept.normalize — norm(label): the single label-normalisation rule the
concept resolver keys on.

CONSISTENCY with the loader (findociq/pipeline/pass2/load_v7.py): the base is the
loader's own normalisation — _clean_label (strip trailing footnote markers:
unicode superscripts ¹²³ and parenthesised '(1)'/'(a)' tails, collapse
whitespace) then lowercase, EXACTLY as geo_norm / seg_norm normalise a label for
geo_map / segment_map lookup. On top of that base the concept layer adds three
DETERMINISTIC folds (approved in the build spec) so cross-bank wording variants
collapse to one key:
  * glued-footnote strip: a 1-2 digit run glued to the LAST word when that word
    still has >=5 letters ('EXPENSES1' -> 'expenses'). Same rule and guard as
    toc_stage.strip_footnote — keeps 'CET1'/'stage 1'/'stage 3' intact (guard, or
    digit not glued to letters).
  * '&' -> ' and ' fold ('Net fee & commission income' == 'net fee and
    commission income').
  * leading waterfall sign marker 'Less:' / 'Add:' stripped ('Less: Operating
    expenses' == 'operating expenses') — the sign is arithmetic direction, not
    concept identity. Anchored + colon-required, so 'Fees, less rebates' is safe.
  * punctuation -> spaces, whitespace collapsed.

Pure, deterministic, unit-tested (test_concept.py). No I/O, no per-bank branch.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from pass2.load_v7 import _clean_label  # noqa: E402  (footnote-tail + ws base rule)

# 1-2 digit run glued directly to a letter at the end of a word ('EXPENSES1').
# Same discriminator as toc_stage.strip_footnote; the >=5-letter guard below
# protects acronyms with real digits (CET1, LCR2).
_GLUED_FOOTNOTE = re.compile(r"(?<=[A-Za-z])\d{1,2}$")
_PUNCT = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")

# Leading waterfall SIGN marker 'Less:' / 'Add:' (case-insensitive, optional ws
# around the colon). These denote the arithmetic direction of a waterfall line,
# NOT the concept identity: 'Less: Operating expenses' is the SAME concept as
# 'operating expenses'. Anchored + colon-required so a label that merely contains
# 'less'/'add' mid-string ('Fees, less rebates') is NOT touched. Applied on the
# lowercased string before punctuation removal (while the colon is still present).
_SIGN_MARKER = re.compile(r"^(?:less|add)\s*:\s*")


def _strip_glued_footnote(s: str) -> str:
    """'EXPENSES1' -> 'EXPENSES', only when the final word keeps >=5 letters after
    dropping the glued 1-2 digit marker (so CET1 / stage 1 survive)."""
    words = s.split()
    if words and _GLUED_FOOTNOTE.search(words[-1]):
        letters = re.sub(r"[^A-Za-z]", "", words[-1])
        if len(letters) >= 5:
            words[-1] = _GLUED_FOOTNOTE.sub("", words[-1])
    return " ".join(words)


def norm(label: str | None) -> str:
    """Canonical concept-lookup form of a row/alias label. Deterministic.

    Order matters: footnote tails are peeled on the ORIGINAL text (before any
    punctuation removal, so '(1)' is recognised as a marker, not two tokens),
    the glued-digit marker is stripped, then lowercase / '&'->'and' /
    punctuation->space / whitespace-collapse."""
    s = _clean_label(label or "")          # ¹²³ / (1) / (a) tails + ws collapse (orig case)
    s = _strip_glued_footnote(s)           # 'EXPENSES1' -> 'EXPENSES'
    s = s.lower()
    s = _SIGN_MARKER.sub("", s)            # 'less: operating expenses' -> 'operating expenses'
    s = s.replace("&", " and ")            # deterministic fold (build spec, item 3)
    s = _PUNCT.sub(" ", s)                 # any other punctuation -> spaces
    return _WS.sub(" ", s).strip()
