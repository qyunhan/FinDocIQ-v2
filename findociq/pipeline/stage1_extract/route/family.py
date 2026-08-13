"""family.py — document-family classifier (the doc-family ROUTER's decision).

Per document, emit a manifest row
  {path, family, institution, period, fs_subtype, has_contents_page,
   contents_page_number, confidence, flags}
so the driver can route: family=='pillar3' -> the deterministic pass1_toc
framework; family=='fs' -> the Gemini toc_stage framework; family=='slides' ->
the slides pipeline; family=='other' -> a non-statement corporate/regulatory
notice (no FS vocabulary on page 1), which ingest discards. family=fs now
REQUIRES a financial-statement keyword on page 1 — an uncorroborated portrait
doc is 'other', not fs.

Design: docs/specs/2026-07-12-document-family-router-design.md. Every family
signal is general CONTENT/geometry — NO per-bank branch. institution/period are
filename-derived (registry + regex). Base python3 + pdfplumber.

CLI:  python3 findociq/pipeline/classify/family.py <path-or-dir> [--out manifest.csv]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import pdfplumber

# ---- registries / thresholds (named, one-line rationale) -----------------
INSTITUTIONS = {                       # keys are mutually non-overlapping
    "DBS": "DBS Group Holdings Ltd",
    "OCBC": "Oversea-Chinese Banking Corporation Ltd",
    "UOB": "United Overseas Bank Ltd",
}
SLIDE_MAX_WORDS   = 120   # page-1 word count below this + landscape => slide deck
CONTENTS_SCAN_PAGES = 12  # a printed contents page appears within the first N pages
MIN_TOC_ENTRIES   = 4     # a contents page needs >= this many entry lines

_FS_CORROBORATE = ("financial statement", "condensed", "interim", "unaudited",
                   "results", "highlights", "performance", "trading update",
                   "press release", "media release", "income statement",
                   "balance sheet")
_FS_FULL = ("financial statement", "condensed", "interim", "unaudited")
_CONTENTS_LABEL = ("table of contents", "contents", "index")

# entry line: leading OR trailing page-ref (int 1..n, or letter label like A-2)
_ENTRY_LEAD  = re.compile(r"^\s*([A-E]-?\d{1,3}|\d{1,4})\s+\D.*\S\s*$")
_ENTRY_TRAIL = re.compile(r"^\D.*?\S\s+([A-E]-?\d{1,3}|\d{1,4})\s*$")
_DOTLEADER   = re.compile(r"[.·…]{2,}")
# period tokens tolerate an optional separator and either a 2- or 4-digit year,
# so both compact ('4Q25', embedded 'DBS4Q25') and spelled-out, hyphenated
# forms ('1q-2025', UOB's naming) parse. Half-year ('1H'/'2H') maps to the
# period-END quarter (Q2 / Q4), matching how interim/FY docs are labelled.
_PERIOD_Q    = re.compile(r"([1-4])Q[-_ ]?(\d{4}|\d{2})", re.IGNORECASE)
_PERIOD_H    = re.compile(r"([12])H[-_ ]?(\d{4}|\d{2})", re.IGNORECASE)
_PERIOD_FY   = re.compile(r"FY[-_ ]?(\d{4}|\d{2})", re.IGNORECASE)
# date-based naming (OCBC Pillar 3: '... as at 30 september 2025'): a month name
# + 4-digit year -> the calendar quarter that month falls in.
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_PERIOD_DATE = re.compile(r"(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.IGNORECASE)
# earnings-call transcripts open with a title line naming themselves as such
# ("Edited transcript of DBS first-quarter ... analyst call"); the Q&A body
# is dense with FS vocabulary ('results', 'performance') so corroboration
# alone can't tell it apart from a real statement. The title-line signal is
# issuer-agnostic (any bank publishing a call transcript uses this convention).
_TRANSCRIPT_TITLE = re.compile(r"\btranscript\b", re.IGNORECASE)


def _yyyy(year: str) -> str:
    """Normalize a 2- or 4-digit year token to a 4-digit year ('25' -> '2025')."""
    return year if len(year) == 4 else f"20{year}"


def institution_from_stem(stem: str) -> str | None:
    """First registry key that appears (case-insensitive) as a SUBSTRING of the
    stem — handles 'DBS4Q25_CFO...' where the bank name has no separator."""
    low = stem.casefold()
    return next((k for k in INSTITUTIONS if k.casefold() in low), None)


def institution_from_path(path: str | Path) -> str | None:
    """Fallback when the filename itself carries no bank code (e.g. UOB's own
    IR filenames, 'performance-highlights-1q-2025.pdf'): the scraper always
    places docs under <out_root>/<BANK>/<year>/<quarter>/, so an exact path
    segment match is as deterministic a signal as the filename substring."""
    segments = {seg.casefold() for seg in Path(path).parts}
    return next((k for k in INSTITUTIONS if k.casefold() in segments), None)


def period_from_stem(stem: str) -> str | None:
    """Derive 'YYYY-Qn' from a filename stem. Handles:
      '4Q25' / 'DBS4Q25'  -> 2025-Q4   (quarter; 2-digit year; separator optional)
      '1q-2025'           -> 2025-Q1   (lowercase, hyphen, 4-digit year — UOB)
      '1H25' / '2H25'     -> 2025-Q2 / 2025-Q4   (half-year -> period-end quarter)
      'FY25'              -> 2025-Q4   (full-year fallback)
      '... 30 september 2025' -> 2025-Q3   (month+year -> calendar quarter; OCBC P3)
    None if no token (caller flags no_period)."""
    m = _PERIOD_Q.search(stem)
    if m:
        return f"{_yyyy(m.group(2))}-Q{m.group(1)}"
    m = _PERIOD_H.search(stem)
    if m:
        return f"{_yyyy(m.group(2))}-Q{'2' if m.group(1) == '1' else '4'}"
    m = _PERIOD_FY.search(stem)
    if m:
        return f"{_yyyy(m.group(1))}-Q4"
    m = _PERIOD_DATE.search(stem)
    if m:
        quarter = (_MONTHS.index(m.group(1).lower()) // 3) + 1
        return f"{m.group(2)}-Q{quarter}"
    return None


def detect_family(page1_text: str, page2_text: str, width: float, height: float,
                  n_words_p1: int) -> tuple[str, str, list[str]]:
    """(family, confidence, flags) from content + geometry. Precedence:
    pillar3 (text 'pillar 3') -> slides (landscape + sparse) -> fs (default)."""
    flags: list[str] = []
    head = (page1_text + "\n" + page2_text).casefold()

    if "pillar 3" in head:
        return "pillar3", "high", flags

    title_line = page1_text.strip().splitlines()[0] if page1_text.strip() else ""
    if _TRANSCRIPT_TITLE.search(title_line):
        flags.append("transcript")
        return "other", "high", flags

    landscape = width > height
    sparse = n_words_p1 < SLIDE_MAX_WORDS
    if landscape and sparse:
        return "slides", "high", flags
    if landscape or sparse:
        flags.append("weak_slide_signal")
        return "slides", "low", flags

    p1 = page1_text.casefold()
    if any(k in p1 for k in _FS_CORROBORATE):
        return "fs", "high", flags
    # Portrait and text-dense, but no financial-statement vocabulary on page 1:
    # this is a short corporate/regulatory notice (bond pricing, offer letter,
    # M&A announcement), NOT a financial statement. Previously mislabeled
    # fs/low, which let regulatory PDFs into extraction; route it out as 'other'
    # so ingest discards it. Corroboration is now REQUIRED for family=fs.
    flags.append("no_fs_signal")
    return "other", "low", flags


def fs_subtype(page1_text: str) -> str:
    """ADVISORY hint only — NEVER a pipeline branch (mislabels DBS
    performance_summary). full vs highlights."""
    p1 = page1_text.casefold()
    return "full" if any(k in p1 for k in _FS_FULL) else "highlights"


def _is_contents_page(text: str) -> bool:
    low = text.casefold()
    if not any(lbl in low for lbl in _CONTENTS_LABEL):
        return False
    entries = 0
    for line in text.splitlines():
        clean = _DOTLEADER.sub(" ", line)
        if _ENTRY_LEAD.match(clean) or _ENTRY_TRAIL.match(clean):
            entries += 1
    return entries >= MIN_TOC_ENTRIES


def has_contents_page(pdf) -> tuple[int, str]:
    """(1/0, 1-based page number or ''). Scans the first CONTENTS_SCAN_PAGES."""
    for i, page in enumerate(pdf.pages[:CONTENTS_SCAN_PAGES], start=1):
        if _is_contents_page(page.extract_text() or ""):
            return 1, str(i)
    return 0, ""


def classify(pdf_path: str | Path) -> dict:
    """One manifest row for one PDF. Never raises on a bad PDF -> family=ERROR."""
    p = Path(pdf_path)
    stem = p.stem
    inst_key = institution_from_stem(stem) or institution_from_path(p)
    period = period_from_stem(stem)
    row = {
        "path": str(p), "family": "", "institution": inst_key or "",
        "period": period or "", "fs_subtype": "", "has_contents_page": "",
        "contents_page_number": "", "confidence": "", "flags": "",
    }
    flags: list[str] = []
    if inst_key is None:
        flags.append("no_institution")
    if period is None:
        flags.append("no_period")
    try:
        with pdfplumber.open(p) as pdf:
            pg1 = pdf.pages[0]
            t1 = pg1.extract_text() or ""
            t2 = pdf.pages[1].extract_text() if len(pdf.pages) > 1 else ""
            t2 = t2 or ""
            n_words = len((t1).split())
            fam, conf, fflags = detect_family(
                t1, t2, float(pg1.width), float(pg1.height), n_words)
            flags += fflags
            row["family"], row["confidence"] = fam, conf
            if fam == "fs":
                row["fs_subtype"] = fs_subtype(t1)
            if fam in ("fs", "pillar3"):
                hcp, hpn = has_contents_page(pdf)
                row["has_contents_page"], row["contents_page_number"] = hcp, hpn
    except Exception as e:                              # noqa: BLE001
        row["family"] = "ERROR"
        row["confidence"] = "low"
        flags.append(f"open_error:{type(e).__name__}")
    row["flags"] = ";".join(flags)
    return row


FIELDS = ["path", "family", "institution", "period", "fs_subtype",
          "has_contents_page", "contents_page_number", "confidence", "flags"]


# `classify_doc` is the name the ingest scraper imports; it's just `classify`.
classify_doc = classify


def build_manifest(pdf_paths) -> list[dict]:
    """Classify a batch of PDFs into manifest rows — one row per path.

    Keep-all by design: no `fs_preferred` tie-break is applied, so every
    fs-family doc a bank publishes in a quarter is retained. Overlapping
    figures across those docs are reconciled downstream by build_fact_metric's
    conflict resolution (resolved_by = single/twin_collapse/prefer_table/
    conflict), not by dropping documents here. Paths are resolved so callers
    can match rows back by resolved path.
    """
    return [classify(Path(p).resolve()) for p in pdf_paths]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="a PDF, or a directory to walk for *.pdf")
    ap.add_argument("--out", help="write manifest CSV here (else print a table)")
    args = ap.parse_args(argv)

    tgt = Path(args.target)
    pdfs = sorted(tgt.rglob("*.pdf")) if tgt.is_dir() else [tgt]
    rows = [classify(p) for p in pdfs]

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {args.out}")
    else:
        print(f"{'family':8} {'conf':5} {'inst':5} {'period':8} {'toc':3} "
              f"{'flags':22} name")
        for r in rows:
            print(f"{r['family']:8} {r['confidence']:5} {r['institution']:5} "
                  f"{r['period']:8} {str(r['has_contents_page']):3} "
                  f"{r['flags'][:22]:22} {Path(r['path']).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
