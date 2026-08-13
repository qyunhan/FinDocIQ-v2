"""toc_stage.py — deterministic TOC producer for the FS (financial-statement) branch.

Pipeline (per document):
  1. Gemini (fs_toc_headings.txt) returns bare headings: title/page/level/parent_id
     only, transport ids sec_NNN. Raw response cached to
     <out-dir>/<doc_id>_toc_raw.json (API call skipped if the cache exists —
     resume-friendly and keeps re-runs free).
  2. Finalize ids (printed-number prefix, else normalized own-title slug) and drop
     preamble/contents headings.
  3. Anchor each heading to a PaddleOCR candidate row by squash-match (exact
     coordinates) or fall back to direct pdfplumber text search / page-top.
  4. Coordinate WINDOWS: sort by anchor (page, y); own-body window = anchor -> next
     anchor (any level, TOP_OF_PAGE_Y-snapped so the window boundary agrees with
     page_end's own top-of-page correction); hierarchical span = anchor page ->
     next same/higher-level anchor page. Attribute PaddleOCR table regions to
     EVERY section whose window vertically overlaps the region's [y0,y1] span
     (one region may legitimately belong to several stacked sections).
  5. Emit <out-dir>/<doc_id>_toc.json + human table.
  6. Validate (regions covered by >=1 section or recorded as preamble, parents
     exist, ids unique, seq order).

Contents page is AUTO-DETECTED (probe first 12 pages for a 'contents'/'index'
label). If none is found the preamble-drop rule only drops title-matching
'^contents' entries and the page guard is skipped.

Base python3 (pdfplumber). PaddleOCR NOT needed at run time (scan artifacts under
--scan-dir are pre-computed by STEP 0 batch_scan).

Run: python3 findociq/pipeline/stage1_extract/toc/toc_stage.py \
       --pdf findociq/data/sources/financial_statements/DBS/2025/2Q25/DBS_2Q25_performance_summary.pdf
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import pdfplumber

REPO = Path(__file__).resolve().parents[4]      # toc -> pipeline -> findociq -> repo
sys.path.insert(0, str(REPO / "findociq" / "pipeline"))
from stage1_extract.gemini.gemini_client import build_client, parse_llm_json  # noqa: E402

PROMPT_FILE = (REPO / "findociq" / "pipeline" / "stage1_extract" / "gemini"
               / "prompts" / "fs_toc_headings.txt")
MODEL = "gemini-3.5-flash"


# --- contents-page probe (inlined from run_spike.probe_and_contents) -------
CONTENTS_SCAN_PAGES = 12
# A real printed contents page carries the WORD 'Contents' as a heading. Match
# it at a line start only — NOT as a bare substring, which false-matched
# 'index' inside footnote URLs like '...website at ...index.html' (UOB
# performance-highlights p4) and mislabelled a data page as the contents page.
_CONTENTS_LINE = re.compile(r"^\s*(table of contents|contents|index)\s*$", re.I)


def probe_contents_page(pdf_path):
    """Return the 1-based page number of the first printed contents page within
    the first CONTENTS_SCAN_PAGES, or None. A contents page has a line that IS
    'Contents'/'Index' (whole line), not merely text containing those letters."""
    with pdfplumber.open(pdf_path) as pdf:
        for idx, pg in enumerate(pdf.pages[:CONTENTS_SCAN_PAGES], start=1):
            for line in (pg.extract_text() or "").splitlines():
                if _CONTENTS_LINE.match(line):
                    return idx
    return None


# --- stable-id derivation (inlined from the retired build_toc_final.py) ----
_NUM_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)[\.\s]")
_FOOTNOTE_TAIL = re.compile(r"(?<=[A-Za-z])\d{1,2}$")


def strip_footnote(title):
    """Drop a superscript footnote marker glued to the title's last word
    ('EXPENSES1' -> 'EXPENSES'). Guard: only when the final word keeps >=5
    letters, so acronyms with real digits (CET1, LCR2-style) survive.
    Production-grade discriminator = superscript font size via pdfplumber
    chars (marker prints smaller/raised) — TODO."""
    words = title.strip().split()
    if words and _FOOTNOTE_TAIL.search(words[-1]):
        letters = re.sub(r"[^A-Za-z]", "", words[-1])
        if len(letters) >= 5:
            words[-1] = _FOOTNOTE_TAIL.sub("", words[-1])
            return " ".join(words)
    return title


SLUG_MAX = 60   # word-boundary cap: long official titles stay readable, never mid-word cut


def slug(title):
    s = re.sub(r"[^a-z0-9]+", "_", strip_footnote(title).casefold()).strip("_")
    if len(s) > SLUG_MAX:
        s = s[:SLUG_MAX].rsplit("_", 1)[0] or s[:SLUG_MAX]
    return s


def derive_ids(sections):
    """Stable ids: the normalized OWN title only (user rule 2026-07-13 PM:
    no compressed lineage in the id — too long; the full ancestor chain is
    recorded separately in `path`). A printed number is provenance, not
    identity: stripped from the slug, recorded verbatim in section_no.
    Ordinal suffix on collisions (same heading under two parents). Gemini's
    sec_NNN ids are transport-only and rewritten here."""
    by_old = {}
    out = []
    used = set()
    for seq, s in enumerate(sections, start=1):
        m = _NUM_PREFIX.match(s["title"].strip())
        if m and re.fullmatch(r"(19|20)\d\d", m.group(1)):
            m = None                     # a bare year ('2025 versus 2024') is
                                         # never a printed section number
        title_sans_no = _NUM_PREFIX.sub("", s["title"].strip(), count=1) if m else s["title"]
        parent_old = s.get("parent_id")
        parent_rec = by_old.get(parent_old) if parent_old else None
        sid = slug(title_sans_no)
        cand, n = sid, 2
        while cand in used:
            cand, n = f"{sid}_{n}", n + 1
        used.add(cand)
        rec = {
            "id": cand,
            "title": s["title"],
            "page_start": int(s["page_start"]),
            "page_end": int(s.get("page_end") or s["page_start"]),
            "level": int(s["level"]),
            "parent_id": parent_rec["id"] if parent_rec else None,
            "path": f"{parent_rec['path']}.{cand}" if parent_rec else cand,
            "seq": seq,
            "section_no": m.group(1) if m else None,
            "kind": s.get("kind", ""),
            "expected_table_count": s.get("expected_table_count", None),
        }
        by_old[s["id"]] = rec
        out.append(rec)
    return out


# ---- deterministic thresholds (named, one-line rationale) ----------------
MATCH_PAGE_TOL = 1       # heading may print +/-1 page off Gemini's reported page
PAGE_FALLBACK_Y = 0.0    # unmatched heading anchors at page-top (y=0) as last resort
TOP_OF_PAGE_Y = 200.0    # y<=this => heading sits in the page's top third; the prior
                         # section then ends on the PREVIOUS page, not this one
LINE_ROUND = 2.5         # word->line clustering divisor; same constant stitch_demo.py
                         # uses to group pdfplumber words into printed lines
RUNHDR_MIN_SPAN     = 3   # a running-header candidate must span >=3 pages (a 1-2
                          # page "(continued)" is a real short section, not a header)
RUNHDR_RECUR_FRAC   = 0.6 # its title must recur as the page top-line on >=60% of the
                          # pages in its span (phantom=1.00; highest legit parent=0.33)
RUNHDR_MIN_CHILDREN = 2   # and it must group >=2 real per-page subsections (a wrapper,
                          # not a leaf) — otherwise there is nothing to re-home

# ---- per-run config (set in main from CLI) -------------------------------
PDF = None
CANDIDATES_CSV = None
REGIONS_CSV = None
OUT_DIR = None
RAW_CACHE = None
FINAL_JSON = None
DOC_ID = None
PROMPT = None


# ---- 1. Gemini call (cached) ---------------------------------------------
def get_raw_sections():
    """Return Gemini's bare heading list, reusing the cached raw response if present."""
    if RAW_CACHE.exists():
        print(f"[gemini] using cache {RAW_CACHE.name}", flush=True)
        return json.load(open(RAW_CACHE))["sections"]

    from google.genai import types
    client = build_client()
    # Vertex AI (the pipeline's client) does not support the Files API —
    # client.files.upload/get are Gemini-Developer-only. Inline the PDF bytes,
    # matching how extract_run/pass2 pass documents to the Vertex client.
    pdf_part = types.Part.from_bytes(data=Path(PDF).read_bytes(),
                                     mime_type="application/pdf")
    for attempt in range(4):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=[pdf_part, PROMPT],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    # gemini-3.5-flash THINKS BY DEFAULT, billed at the output
                    # rate ($9/M) — heading transcription doesn't need it, and
                    # unbounded thinking multiplied real TOC cost several-fold
                    # (user-observed ~$0.20/doc vs ~$0.05 without).
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            parsed = parse_llm_json(resp.text)
            um = getattr(resp, "usage_metadata", None)
            parsed["_usage"] = {
                "model": MODEL,
                "prompt_tokens": getattr(um, "prompt_token_count", None),
                "output_tokens": getattr(um, "candidates_token_count", None),
                "thinking_tokens": getattr(um, "thoughts_token_count", None),
            }
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            RAW_CACHE.write_text(json.dumps(parsed, indent=2))
            print(f"[gemini] ok — {len(parsed['sections'])} headings "
                  f"(cached {RAW_CACHE.name}; usage {parsed['_usage']})", flush=True)
            return parsed["sections"]
        except Exception as exc:  # noqa: BLE001 — retry transient failures
            wait = 2 * (2 ** attempt)
            print(f"[gemini] attempt {attempt+1} failed: {type(exc).__name__}: "
                  f"{str(exc)[:120]} (retry {wait}s)", flush=True)
            time.sleep(wait)
    raise RuntimeError("gemini: all attempts failed")


# ---- 2. drop preamble/contents -------------------------------------------
_CONTENTS = re.compile(r"^contents", re.I)


def drop_preamble(raw, contents_page):
    """Keep headings on pages past the contents page, plus any ancestor of a kept
    heading (so a top-level title on p<=contents_page survives if it parents real
    content). Always drop '^contents'. When contents_page is None the page guard is
    skipped and only '^contents' titles are dropped. Uses header page only."""
    by_id = {s["id"]: s for s in raw}

    def is_contents(s):
        return bool(_CONTENTS.match(s["title"].strip()))

    if contents_page is None:
        kept = [s for s in raw if not is_contents(s)]
        dropped = [s["title"] for s in raw if is_contents(s)]
        return kept, dropped

    # seed: headings printed past the contents page
    keep_ids = {s["id"] for s in raw
                if int(s["page"]) > contents_page and not is_contents(s)}
    # pull in ancestors of any kept heading
    changed = True
    while changed:
        changed = False
        for sid in list(keep_ids):
            pid = by_id[sid].get("parent_id")
            if pid and pid in by_id and pid not in keep_ids and not is_contents(by_id[pid]):
                keep_ids.add(pid)
                changed = True
    kept = [s for s in raw if s["id"] in keep_ids]
    dropped = [s["title"] for s in raw if s["id"] not in keep_ids]
    # Safety net: a document is never ENTIRELY preamble. If the page guard would
    # drop every heading, the contents-page detection was wrong (e.g. a short
    # highlights doc whose data sits on the mis-detected 'contents' page) — fall
    # back to the no-contents-page branch (keep all but '^contents' titles).
    if not kept:
        kept = [s for s in raw if not is_contents(s)]
        dropped = [s["title"] for s in raw if is_contents(s)]
    return kept, dropped


# ---- 3. anchor headings to candidate coordinates -------------------------
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹",
                                     "0123456789")


def squash(text):
    """Lowercase, strip non-alnum. Normalizes unicode superscript digits (Gemini
    sometimes transcribes a footnote marker as U+00B9 '¹' rather than ASCII '1')
    to their ASCII digit first, so footnote-marker titles key-match across runs
    regardless of which glyph got transcribed — general rule, not doc-specific."""
    return re.sub(r"[^a-z0-9]", "", text.translate(_SUPERSCRIPT_DIGITS).casefold())


def load_candidates():
    rows = list(csv.DictReader(open(CANDIDATES_CSV)))
    for r in rows:
        r["page"] = int(r["page"])
        r["y0"] = float(r["y0"])
        r["sq"] = squash(r["text"])
    return rows


_line_cache = {}


def get_page_lines(pdf, page_num):
    """Squashed printed lines on a page, cached: (page, line) -> [{"y","sq"}].
    Lines are pdfplumber words clustered by round(top/LINE_ROUND), the same
    line-grouping convention stitch_demo.py uses — general rule, not doc-specific."""
    if page_num in _line_cache:
        return _line_cache[page_num]
    if not (1 <= page_num <= len(pdf.pages)):
        _line_cache[page_num] = []
        return []
    words = pdf.pages[page_num - 1].extract_words()
    lines = {}
    for w in words:
        lines.setdefault(round(float(w["top"]) / LINE_ROUND), []).append(w)
    out = []
    for key in sorted(lines):
        lws = sorted(lines[key], key=lambda w: float(w["x0"]))
        text = " ".join(w["text"] for w in lws)
        out.append({"y": min(float(w["top"]) for w in lws), "sq": squash(text)})
    _line_cache[page_num] = out
    return out


def text_search_anchor(pdf, title, page):
    """Tier 2: the title was TRANSCRIBED off the page, so search for it directly.
    Scans pages [page-tol, page+tol] in that order (Gemini's reported page first,
    since it's the likeliest hit), squash-matches each printed line, and anchors
    to the topmost match on the earliest page with any match. strip_footnote first
    so 'EXPENSES1' still finds the printed 'EXPENSES' line (+superscript marker)."""
    stq = squash(strip_footnote(title))
    if not stq:
        return None
    page_order = sorted(range(page - MATCH_PAGE_TOL, page + MATCH_PAGE_TOL + 1),
                         key=lambda p: abs(p - page))
    for pg in page_order:
        lines = get_page_lines(pdf, pg)
        hits = [ln for ln in lines if ln["sq"] and (stq in ln["sq"] or ln["sq"] in stq)]
        if hits:
            topmost = min(hits, key=lambda ln: ln["y"])
            return pg, topmost["y"]
    return None


def match_anchors(sections, candidates, pdf):
    """Three-tier anchor: (1) PaddleOCR candidates.csv squash-match — exact
    coordinates, cheapest. (2) direct pdfplumber text search on the page (+/-1) —
    the title is always findable ON the page it was transcribed from, even when
    PaddleOCR's heading detector missed it (body-size running headers etc).
    (3) page-top fallback when the title is on neither. Anchor = (page, y)."""
    for s in sections:
        stq = squash(s["title"])
        pg = int(s["page_start"])   # derive_ids carries Gemini's page as page_start
        hits = []
        for c in candidates:
            if not c["sq"] or abs(c["page"] - pg) > MATCH_PAGE_TOL:
                continue
            if stq and (stq in c["sq"] or c["sq"] in stq):
                hits.append(c)
        if hits:
            best = min(hits, key=lambda c: (abs(c["page"] - pg), c["y0"]))
            s["anchor_page"] = best["page"]
            s["anchor_y"] = best["y0"]
            s["anchor_source"] = "candidates"
            continue
        found = text_search_anchor(pdf, s["title"], pg)
        if found is not None:
            s["anchor_page"], s["anchor_y"] = found
            s["anchor_source"] = "text_search"
            continue
        s["anchor_page"] = pg
        s["anchor_y"] = PAGE_FALLBACK_Y
        s["anchor_source"] = "page_fallback"
    return sections


def _parent_before_child(ordered):
    """Stable, order-preserving topological reorder so every section's parent
    precedes it. Anchor order (page, y) can place a section BEFORE its parent
    when the parent heading is mis-anchored to a later point — a page-1 title
    matched on page 2, or a higher-y sibling on the same page. `seq` must be
    parent-before-child for the section self-FK (toc_to_db) and every
    file-order consumer (pass2/extract.load_sections). This corrects ORDER
    only; `parent_id` is preserved verbatim — the fault is a mis-anchor, not a
    mis-nesting; reconciling a genuinely wrong Gemini parent is the dedicated
    hierarchy 2-pass's job. Reading order is otherwise preserved: a parent is
    lifted forward ONLY when a child would else precede it. Cycle => fail loud.
    """
    by_id = {s["id"]: s for s in ordered}
    emitted, out = set(), []

    def emit(s, stack):
        if s["id"] in emitted:
            return
        check(s["id"] not in stack, f"section parent cycle at {s['id']!r}")
        pid = s.get("parent_id")
        parent = by_id.get(pid) if pid else None
        if parent is not None and parent["id"] not in emitted:
            emit(parent, stack | {s["id"]})     # DFS: lift the parent first
        emitted.add(s["id"])
        out.append(s)

    for s in ordered:                            # anchor order drives the walk
        emit(s, set())
    return out


# ---- 4. windows + region attribution -------------------------------------
def _breakpoint(anchor_page, anchor_y):
    """Snap an anchor to a window BOUNDARY: an anchor sitting in the page's top
    strip (anchor_y <= TOP_OF_PAGE_Y) snaps to that page's top (page, 0.0), so
    the section owning that anchor claims its ENTIRE page — the same
    top-of-page correction page_end already applies (see TOP_OF_PAGE_Y, used
    below in the page_end branch). Anchors below the top strip are unchanged.
    Using this snapped value as BOTH a window's lo and the previous window's hi
    keeps the two computations in agreement (previously they disagreed: page_end
    was snapped but the attribution window used the raw anchor)."""
    return (anchor_page, 0.0) if anchor_y <= TOP_OF_PAGE_Y else (anchor_page, anchor_y)


def _window_overlaps_region(win_lo, win_hi, page, y0, y1):
    """Does window [win_lo, win_hi) (lexicographic (page, y), half-open) cover
    any part of `page`'s [y0, y1] span? A window's per-page y-range is: its own
    win_lo.y on the win_lo page, its own win_hi.y (exclusive) on the win_hi
    page (0.0 there means the page is wholly excluded — the exclusive bound
    already encodes that), and the full page in between."""
    if win_lo[0] > page or win_hi[0] < page:
        return False
    lo = win_lo[1] if win_lo[0] == page else 0.0
    hi = win_hi[1] if win_hi[0] == page else float("inf")
    return lo < y1 and y0 < hi


def build_windows(sections, last_page, regions):
    """Sort by anchor (page, y). Own-body window = anchor -> next anchor (any
    level), exclusive; last runs to doc end. Window boundaries are TOP_OF_PAGE_Y
    -snapped (_breakpoint) so they agree with page_end's own top-of-page
    correction. Hierarchical span = anchor page -> next SAME-OR-HIGHER-level
    anchor page (minus 1 if that anchor is top-of-page). Attribute each region
    to EVERY section whose window vertically overlaps the region's [y0, y1]
    span on the region's page — one region may legitimately belong to several
    stacked sections (PaddleOCR sometimes detects one box spanning several
    tightly-stacked tables)."""
    order = sorted(range(len(sections)),
                   key=lambda i: (sections[i]["anchor_page"],
                                  sections[i]["anchor_y"],
                                  sections[i]["_gseq"]))
    ordered = [sections[i] for i in order]
    anchors = [(s["anchor_page"], s["anchor_y"]) for s in ordered]
    breakpoints = [_breakpoint(*a) for a in anchors]
    END = (last_page + 1, 0.0)   # sentinel past the last page

    for idx, s in enumerate(ordered):
        # own-body window
        s["_win_lo"] = breakpoints[idx]
        s["_win_hi"] = breakpoints[idx + 1] if idx + 1 < len(ordered) else END
        # hierarchical span (page_start / page_end)
        s["page_start"] = s["anchor_page"]
        nxt = next((ordered[j] for j in range(idx + 1, len(ordered))
                    if ordered[j]["level"] <= s["level"]), None)
        if nxt is None:
            page_end = last_page
        else:
            page_end = (nxt["anchor_page"]
                        if nxt["anchor_y"] > TOP_OF_PAGE_Y
                        else nxt["anchor_page"] - 1)
        s["page_end"] = max(page_end, s["page_start"])
        s["n_regions"] = 0

    # attribute regions by page/y-span OVERLAP against every section's window
    # (one-to-many: a region can belong to several stacked sections). Regions
    # BEFORE the first kept section's window are PREAMBLE regions — the
    # detector is right that a cover grid or the printed contents page IS
    # tabular, but no content section owns them. They are recorded explicitly
    # (never silently dropped): validation requires every region to either be
    # attributed to >=1 section or recorded as preamble.
    attributed = {}
    preamble_regions = []
    first_lo = ordered[0]["_win_lo"] if ordered else (10**9, 0.0)
    for r in regions:
        page = int(r["page"])
        y0, y1 = float(r["y0"]), float(r["y1"])
        owners = [s for s in ordered
                  if _window_overlaps_region(s["_win_lo"], s["_win_hi"], page, y0, y1)]
        if not owners and (page, y0) < first_lo:
            preamble_regions.append({"page": page,
                                     "table_idx": int(r["table_idx"])})
            continue
        attributed[(page, int(r["table_idx"]))] = owners
        for s in owners:
            s["n_regions"] += 1
    for s in ordered:
        s["has_tables"] = s["n_regions"] >= 1
    # Restore parent-before-child ordering: anchor (page, y) order can place a
    # section before its parent when the parent heading is mis-anchored later.
    # seq must be topological for the section self-FK and every file-order
    # consumer; parent_id is left untouched (mis-anchor, not mis-nesting).
    ordered = _parent_before_child(ordered)
    # re-number seq in (topologically-corrected) reading order
    for seq, s in enumerate(ordered, start=1):
        s["seq"] = seq
    return ordered, attributed, preamble_regions


# ---- 4b. running-header detection ----------------------------------------
_CONT_MARKER_RE = re.compile(
    r"\s*[\(\[]?\s*(continued|cont'?d\.?|con'?t\.?)\s*[\)\]]?\s*$", re.IGNORECASE)


def strip_cont_marker(title):
    """Drop a trailing '(continued)'/'cont'd' marker — the base section title."""
    return _CONT_MARKER_RE.sub("", title).strip()


def detect_running_headers(sections, top_line_sq):
    """Flag Gemini headings that are actually RUNNING PAGE HEADERS, not sections.

    A bank repeats a section title ('FINANCIAL HIGHLIGHTS (continued)') at the
    top of every page of a long block; Gemini transcribes that repeated header as
    one section spanning the whole block, which then becomes a giant extraction
    unit. Three deterministic signals, all required (general — no title/bank
    literal; keyed only on geometry + the parent graph):

      1. RENDERS like a header: squash(base title) matches the page TOP-LINE on
         >= RUNHDR_RECUR_FRAC of the pages in [page_start, page_end], span >=
         RUNHDR_MIN_SPAN. (top_line_sq: {page -> squashed top printed line}.)
      2. WOULD BECOME A UNIT: has_tables — the harm gate. A prose parent that
         merely prints a running header (auditor's report) has has_tables=False
         and is left alone; only a table-bearing phantom is dangerous.
      3. GROUPS the real sections: >= RUNHDR_MIN_CHILDREN direct children on
         distinct pages — a wrapper, not a leaf.

    Returns a list of records {id, title, span, recurrence, n_children,
    reparent_to} for the flagged phantoms (reparent_to = base-title sibling near
    page_start if one exists, else the phantom's own parent_id).
    """
    by_id = {s["id"]: s for s in sections}
    kids = {}
    for s in sections:
        if s.get("parent_id"):
            kids.setdefault(s["parent_id"], []).append(s)

    flagged = []
    for s in sections:
        span_pages = list(range(int(s["page_start"]), int(s["page_end"]) + 1))
        if len(span_pages) < RUNHDR_MIN_SPAN or not s.get("has_tables"):
            continue
        children = kids.get(s["id"], [])
        child_pages = {int(c["page_start"]) for c in children}
        if len(children) < RUNHDR_MIN_CHILDREN or len(child_pages) < RUNHDR_MIN_CHILDREN:
            continue
        base_sq = squash(strip_cont_marker(s["title"]))
        if not base_sq:
            continue
        hits = sum(1 for p in span_pages
                   if (t := top_line_sq.get(p)) and (base_sq in t or t in base_sq))
        recurrence = hits / len(span_pages)
        if recurrence < RUNHDR_RECUR_FRAC:
            continue
        # reparent target: a DIFFERENT section with the base (marker-stripped)
        # title, anchored near this heading's start — the real base heading.
        target = next(
            (o["id"] for o in sections
             if o["id"] != s["id"] and squash(strip_cont_marker(o["title"])) == base_sq
             and abs(int(o["page_start"]) - int(s["page_start"])) <= MATCH_PAGE_TOL),
            s.get("parent_id"))
        flagged.append({"id": s["id"], "title": s["title"],
                        "span": f"{s['page_start']}-{s['page_end']}",
                        "recurrence": round(recurrence, 2),
                        "n_children": len(children), "reparent_to": target})
    return flagged


def apply_running_header_strip(sections, flagged):
    """Reparent each phantom's direct children to its reparent target, drop the
    phantom rows, and recompute every section's `path` from the new parent chain.
    Returns the cleaned section list (order preserved; build_windows re-runs)."""
    remap = {f["id"]: f["reparent_to"] for f in flagged}
    drop = set(remap)
    for s in sections:
        if s.get("parent_id") in remap:
            s["parent_id"] = remap[s["parent_id"]]      # child -> base heading
    kept = [s for s in sections if s["id"] not in drop]
    by_id = {s["id"]: s for s in kept}

    def path_of(sid, seen):
        s = by_id[sid]
        pid = s.get("parent_id")
        if not pid or pid not in by_id or pid in seen:
            return sid
        return path_of(pid, seen | {sid}) + "." + sid
    for s in kept:
        s["path"] = path_of(s["id"], set())
    return kept


# ---- helpers -------------------------------------------------------------
def check(cond, msg):
    if not cond:
        print(f"VALIDATION FAILED: {msg}", file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True, help="source FS PDF (required)")
    ap.add_argument("--scan-dir", default=None,
                    help="PaddleOCR scan dir (candidates.csv/regions.csv); "
                         "default findociq/data/derived/paddle_scans/<tag>")
    ap.add_argument("--out-dir", default=None,
                    help="TOC output dir; default findociq/data/derived/toc/")
    args = ap.parse_args()

    global PDF, CANDIDATES_CSV, REGIONS_CSV, OUT_DIR, RAW_CACHE, FINAL_JSON, DOC_ID, PROMPT

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")
    DOC_ID = pdf_path.stem.replace(" ", "_")
    tag = DOC_ID

    scan_dir = (Path(args.scan_dir).resolve() if args.scan_dir
                else REPO / "findociq" / "data" / "derived" / "paddle_scans" / tag)
    OUT_DIR = (Path(args.out_dir).resolve() if args.out_dir
               else REPO / "findociq" / "data" / "derived" / "toc")
    PDF = pdf_path
    CANDIDATES_CSV = scan_dir / "candidates.csv"
    REGIONS_CSV = scan_dir / "regions.csv"
    RAW_CACHE = OUT_DIR / f"{DOC_ID}_toc_raw.json"
    FINAL_JSON = OUT_DIR / f"{DOC_ID}_toc.json"
    PROMPT = PROMPT_FILE.read_text()

    for p in (CANDIDATES_CSV, REGIONS_CSV):
        if not p.exists():
            sys.exit(f"scan artifact not found: {p} (run STEP 0 batch_scan first)")

    # repo-relative source path for the manifest
    try:
        source_rel = str(pdf_path.relative_to(REPO))
    except ValueError:
        source_rel = str(pdf_path)

    contents_page = probe_contents_page(PDF)
    print(f"[contents] probe -> "
          f"{('p'+str(contents_page)) if contents_page else 'none detected'}", flush=True)

    raw = get_raw_sections()
    kept, dropped = drop_preamble(raw, contents_page)

    # finalize ids (derive_ids reads page_start/page_end)
    for s in kept:
        s["page_start"] = int(s["page"])
        s["page_end"] = int(s["page"])
    finals = derive_ids(kept)
    for gseq, s in enumerate(finals):      # preserve Gemini reading order for tie-breaks
        s["_gseq"] = gseq

    candidates = load_candidates()
    regions = list(csv.DictReader(open(REGIONS_CSV)))
    prev_final = (json.load(open(FINAL_JSON))["sections"]
                  if FINAL_JSON.exists() else None)
    running_headers = []
    with pdfplumber.open(PDF) as pdf:
        last_page = len(pdf.pages)
        match_anchors(finals, candidates, pdf)
        ordered, attributed, preamble_regions = build_windows(finals, last_page, regions)
        # Detect Gemini headings that are actually running PAGE HEADERS (a title
        # repeated at page-top across a long span) and would otherwise become one
        # giant extraction unit. Drop them, reparent their children to the real
        # base heading, and re-window on the cleaned list. General + fail-loud.
        top_line_sq = {p: (get_page_lines(pdf, p)[0]["sq"] if get_page_lines(pdf, p)
                           else "") for p in range(1, last_page + 1)}
        running_headers = detect_running_headers(ordered, top_line_sq)
        if running_headers:
            finals = apply_running_header_strip(finals, running_headers)
            ordered, attributed, preamble_regions = build_windows(
                finals, last_page, regions)

    tiers = {"candidates": 0, "text_search": 0, "page_fallback": 0}
    for s in finals:
        tiers[s["anchor_source"]] += 1

    # ---- 5. emit ---------------------------------------------------------
    doc = {
        "doc_id": DOC_ID,
        "source_pdf": source_rel,
        "doc_family": "financial_stmt",
        "contents_page_number": contents_page,
        "toc_source": "gemini prompt_v3 + coordinate-window finalize",
        "preamble_regions": preamble_regions,
        "running_headers_dropped": running_headers,
    }
    fields = ["id", "title", "page_start", "page_end", "level", "parent_id",
              "path", "seq", "section_no", "has_tables", "n_regions", "anchor_source"]
    out_secs = [{k: s[k] for k in fields} for s in ordered]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FINAL_JSON.write_text(json.dumps({"document": doc, "sections": out_secs}, indent=2))

    # ---- 6. validation ---------------------------------------------------
    # one-to-many attribution means sum(n_regions) can legitimately EXCEED
    # len(regions) (one region counted by several overlapping sections), so
    # the invariant is COVERAGE, not a strict count partition: every region
    # is attributed to >=1 section (non-empty owners list) or recorded as
    # preamble, and every regions.csv row lands in exactly one of those two
    # buckets.
    check(all(v for v in attributed.values()),
          "some region attributed to NO section")
    check(len(attributed) + len(preamble_regions) == len(regions),
          f"attributed {len(attributed)} + preamble "
          f"{len(preamble_regions)} != regions {len(regions)}")
    if preamble_regions:
        print(f"preamble regions (cover/contents pages, no owning section): "
              f"{[(r['page'], r['table_idx']) for r in preamble_regions]}")
    ids = {s["id"] for s in ordered}
    check(len(ids) == len(ordered), "duplicate section ids")
    check(all(s["parent_id"] is None or s["parent_id"] in ids for s in ordered),
          "parent_id references a missing section")
    check([s["seq"] for s in ordered] == list(range(1, len(ordered) + 1)),
          "sections not in contiguous seq order")
    # topological invariant: every parent precedes its children in seq. Locks
    # the parent-before-child guarantee at the point seq is produced, so any
    # future regression fails loud HERE, not as an FK error downstream.
    pos = {s["id"]: i for i, s in enumerate(ordered)}
    check(all(s["parent_id"] is None or pos[s["parent_id"]] < pos[s["id"]]
              for s in ordered),
          "parent must precede child in seq order")

    # ---- reports ---------------------------------------------------------
    print(f"\ndoc_id={DOC_ID}  contents_page={contents_page}  source={source_rel}")
    print(f"\ndropped as preamble/contents ({len(dropped)}): {dropped}")
    for rh in running_headers:
        print(f"RUNNING-HEADER DROPPED: {rh['id']!r} (span p{rh['span']}, "
              f"recurs {rh['recurrence']:.0%} of pages, {rh['n_children']} children) "
              f"-> children reparented to {rh['reparent_to']!r}")
    n = len(finals) or 1
    print(f"\nanchor tiers ({len(finals)} sections): "
          f"candidates={tiers['candidates']} ({tiers['candidates']/n:.0%})  "
          f"text_search={tiers['text_search']} ({tiers['text_search']/n:.0%})  "
          f"page_fallback={tiers['page_fallback']} ({tiers['page_fallback']/n:.0%})")
    print(f"\nhas_tables: yes={sum(1 for s in ordered if s['has_tables'])}  "
          f"no={sum(1 for s in ordered if not s['has_tables'])}  "
          f"sum(n_regions)={sum(s['n_regions'] for s in ordered)}  "
          f"regions.csv rows={len(regions)}")
    print(f"\n{'id':46} {'pages':7} {'L':1} {'no':>4} {'tbl':3} {'n':>2} {'anchor':13}")
    for s in ordered:
        pr = (f"{s['page_start']}" if s["page_start"] == s["page_end"]
              else f"{s['page_start']}-{s['page_end']}")
        print(f"{s['id'][:46]:46} {pr:7} {s['level']} "
              f"{(s['section_no'] or ''):>4} "
              f"{('yes' if s['has_tables'] else '  .'):3} {s['n_regions']:>2} "
              f"{s['anchor_source']:13}")

    # ---- attribution diff vs previous run for this doc -------------------
    if prev_final is not None:
        diff_vs_dict(ordered, prev_final, f"previous {FINAL_JSON.name} run",
                     lambda s: s.get("has_tables", False))
    else:
        print(f"\n(no previous {FINAL_JSON.name} to diff against — first run)")

    print(f"\nwrote {FINAL_JSON}")


def diff_vs_dict(ordered, prev_sections, label, bearing_fn):
    """Compare table-bearing status against a prior TOC's sections, keyed by
    squashed title (ids may differ across runs/versions)."""
    old_bear = {squash(s["title"]): bearing_fn(s) for s in prev_sections}
    print(f"\nATTRIBUTION DIFF vs {label} (table-bearing flips):")
    flips = 0
    for s in ordered:
        key = squash(s["title"])
        if key in old_bear and old_bear[key] != s["has_tables"]:
            flips += 1
            was = "tables" if old_bear[key] else "empty"
            now = "tables" if s["has_tables"] else "empty"
            print(f"  {s['id'][:44]:44} p{s['page_start']}-{s['page_end']:<3} "
                  f"{was:7}-> {now:7} (n_regions={s['n_regions']})")
    if not flips:
        print("  (none)")
    else:
        print(f"  {flips} section(s) changed table-bearing status")


if __name__ == "__main__":
    main()
