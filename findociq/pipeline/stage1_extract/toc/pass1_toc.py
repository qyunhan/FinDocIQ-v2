"""
pass1_toc.py — deterministic Table of Contents extraction. ZERO API calls.

Ported from findociq/_legacy/DELIVERABLE/pillar3/PASS1_TOC.py (2026-07-02): this is the
primary section discoverer for the printed-TOC filing family (DBS/OCBC/UOB Pillar 3) per the
2026-07-02 reconciliation — Gemini/MinerU TOC discovery is NOT used for these filings (MinerU
dropped for this family). _legacy/ stays the reference copy; this is the live pipeline module.

Template-agnostic: handles both
  * Part-structured filings (DBS full Pillar 3: "PART A", page labels "A-2"), and
  * Sequentially-numbered filings (OCBC / UOB: "1 Introduction … 5", plain page
    numbers in the footer).

How it anchors sections to physical pages without hard-coded page numbers:
  1. Parse the printed CONTENTS for the section hierarchy and each section's
     printed page reference (a letter label like "A-2" OR a plain number like "5").
  2. Scan every physical page's footer for its printed page token
     ("A-2", "Page 5", or a trailing page number) -> {token: physical_page}.
  3. Map each section's page reference through that table -> physical start page.
  4. (Fallback) if a section can't be anchored that way, use the PDF's embedded
     outline (bookmarks), when present.

Output -> findociq/data/discovery/<stem>_toc.json   (document, provenance, parts, sections[])

Usage:
  python pass1_toc.py "findociq/data/sources/pillar3/DBS_4Q25_Pillar3.pdf"
  python pass1_toc.py "findociq/data/sources/pillar3/OCBC_4Q25_Pillar3.pdf" --out findociq/data/discovery/ocbc_toc.json
"""
from __future__ import annotations
import os, sys, re, json, argparse
from pathlib import Path
import pypdfium2 as pdfium
try:
    import pdfplumber                # better text ordering for footer scans
except Exception:
    pdfplumber = None

_P3_OUT = Path(__file__).resolve().parents[3] / "data" / "discovery" / "pillar3"

def _toc_out_path(pdf_path: str, toc: dict | None = None) -> str:
    """Derive the output path: outputs/pillar3/{bank}_{period}/toc.json.
    Falls back to a flat {doc_stem}_toc.json if bank/period can't be determined."""
    stem = Path(pdf_path).stem
    name_up = stem.upper()
    if "OCBC" in name_up:
        bank = "ocbc"
    elif "UOB" in name_up:
        bank = "uob"
    elif "DBS" in name_up:
        bank = "dbs"
    else:
        return str(_P3_OUT / f"{stem}_toc.json")
    doc_date = (toc or {}).get("document", {}).get("doc_date", "")
    period = _derive_period_simple(doc_date, stem)
    run_dir = _P3_OUT / f"{bank}_{period}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return str(run_dir / "toc.json")

def _derive_period_simple(doc_date: str, doc_stem: str = "") -> str:
    """Minimal period derivation for PASS1 (no PASS2 import)."""
    _MONTH_Q = {"march":"1Q","mar":"1Q","june":"2Q","jun":"2Q",
                "september":"3Q","sep":"3Q","december":"4Q","dec":"4Q"}
    _MONTH_A = {"january":"Jan","jan":"Jan","february":"Feb","feb":"Feb",
                "april":"Apr","apr":"Apr","may":"May","july":"Jul","jul":"Jul",
                "august":"Aug","aug":"Aug","october":"Oct","oct":"Oct",
                "november":"Nov","nov":"Nov"}
    if doc_date:
        m = re.search(r'\b(\d{1,2})?\s*([A-Za-z]+)\s+(\d{4})\b', doc_date.strip())
        if m:
            mo, yr = m.group(2).lower(), m.group(3)[-2:]
            if mo in _MONTH_Q:
                return f"{_MONTH_Q[mo]}{yr}"
            return f"{_MONTH_A.get(mo, mo.capitalize()[:3])}{yr}"
    # Fallback: try to parse from stem e.g. "DBS_4Q25_Pillar3" -> "4Q25"
    mp = re.search(r'([1-4][Qq]\d{2})', doc_stem)
    if mp:
        return mp.group(1)
    return re.sub(r"[^a-zA-Z0-9]", "", doc_stem)[:12] or "unknown"

PART_RE  = re.compile(r"^PART\s+([A-Z])\b\s*[:\-]?\s*(.+?)(?:\.{2,}|…|$)", re.I)
# section line WITH a printed page ref (letter label A-2 or plain integer) at the end.
SEC_RE   = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+?)[\s.…]{2,}\s*([A-E]-\d{1,3}|\d{1,4})\s*$")
# section line WITHOUT a page ref (e.g. DBS subsections): a DOTTED number + optional title.
# Title is optional to handle DBS 6.1/6.2/6.3 which have numbers but blank titles.
SEC_NOREF_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)*)\.?\s*([A-Za-z(].*?)?\s*$")
LABEL_RE = re.compile(r"^([A-E])\s*-\s*(\d{1,3})$")           # standalone letter label

def _norm_ref(ref: str) -> str:
    """Normalise a page reference token: 'A - 2' -> 'A-2'; '007' -> '7'."""
    if not ref:
        return ""
    ref = ref.strip()
    m = re.fullmatch(r"([A-E])\s*-\s*(\d{1,3})", ref)
    if m:
        return f"{m.group(1).upper()}-{int(m.group(2))}"
    if ref.isdigit():
        return str(int(ref))
    return ref

# ---------------------------------------------------------------------------
def _page_text(pdf, i: int) -> str:
    return pdf[i].get_textpage().get_text_range()

def _find_contents_start(pdf, max_scan: int = 8) -> int:
    for i in range(min(max_scan, len(pdf))):
        t = _page_text(pdf, i).upper()
        if "CONTENT" in t:                       # "Contents" / "Table of Contents"
            return i
    return 1

_SEC_LINE_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+\S")   # any "N.N.N  Title" line

def _toc_section_density(txt: str) -> int:
    """Count lines that look like section entries (number + text)."""
    return sum(1 for l in txt.splitlines() if _SEC_LINE_RE.match(l))

def _looks_like_toc(txt: str) -> bool:
    return bool(re.search(r"CONTENT", txt, re.I) or
                re.search(r"^PART\s+[A-Z]", txt, re.M) or
                _toc_section_density(txt) >= 3)

def _collect_contents_text(pdf, start_idx: int) -> str:
    """Collect all TOC pages. Stops when a page has NO dot-leader TOC entries
    AND either: more than 4 long prose lines, OR no dot leaders at all.
    TOC entries are distinguished from body text by dot leaders (….…).
    A body page that happens to start with a numbered heading is NOT a TOC page.
    Page headers/footers ('Pillar 3 Disclosure Report', 'Page 3') are ignored."""
    n = len(pdf)
    out = ""
    _dotleader_re = re.compile(r"[.…]{4,}")  # 4+ dots/ellipses = TOC dot leader
    for ci in range(start_idx, min(start_idx + 8, n)):
        txt = _page_text(pdf, ci)
        clean_lines = [l for l in txt.split("\n")
                       if l.strip() and not re.match(r"^\s*(Page\s+\d+|Pillar\s+3\b)", l, re.I)]
        clean_txt = "\n".join(clean_lines)
        # dot-leader lines are the reliable TOC signal
        dotleader_lines = [l for l in clean_lines if _dotleader_re.search(l)]
        # long prose lines (after removing dot leaders) signal body text
        prose_lines = [l for l in clean_lines
                       if len(re.sub(r"[.…]{2,}", "", l).strip()) > 130]
        has_toc_entries = len(dotleader_lines) >= 2
        if ci > start_idx and not has_toc_entries:
            break   # no dot-leader TOC entries on this page — we've left the TOC
        out += "\n" + txt
    return out

SEC_START   = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+\S")           # line begins a section entry
PAGEREF_END = re.compile(r"([A-E]-\d{1,3}|\d{1,4})\s*$")          # entry ends with a page ref

def _merge_wrapped_lines(text: str) -> list[str]:
    """Collapse wrapped TOC entries into one logical line each. A long title can
    wrap so its page number lands on the next line; we append continuation lines
    to the current entry until it ends with a page reference (or the next
    section/part begins)."""
    logical: list[str] = []
    cur, cur_done = None, False
    for raw in text.replace("\r", "").split("\n"):
        s = raw.strip()
        if not s:
            continue
        sclean = re.sub(r"[\.…]{2,}", "  ", s)
        is_part = bool(PART_RE.match(sclean))
        is_sec  = bool(SEC_START.match(s))
        if is_part or is_sec:
            if cur is not None:
                logical.append(cur)
            cur = s
            cur_done = is_part or bool(PAGEREF_END.search(s))     # parts/single-line entries are complete
        elif cur is not None and not cur_done:
            cur = cur + " " + s                                   # wrapped continuation
            if PAGEREF_END.search(cur):
                cur_done = True
        # else: stray line after a completed entry (footer, etc.) — ignore
    if cur is not None:
        logical.append(cur)
    return logical

_BARE_NUM_RE = re.compile(r"^\s*(\d+\.\d+(?:\.\d+)*)\s*$")  # line is ONLY a section number

def _patch_twocol_toc(raw_text: str) -> str:
    """DBS PDF two-column layout: pypdfium2 emits bare number entries first
    ('6.1', '6.2', '6.3'), then their titles appear as a detached block later.

    For each bare-number entry N.M, we scan forward through the orphan title
    block and read lines until we see the next sibling number N.M+1 (or the block
    ends).  That gives us the full (possibly-wrapped) title for each entry."""
    lines = raw_text.replace("\r", "").split("\n")
    n = len(lines)

    # collect bare-number line indices: line is ONLY "N.M" or "N.M.K" with no title
    bare: dict[int, str] = {}
    for i, l in enumerate(lines):
        m = _BARE_NUM_RE.match(l)
        if m:
            bare[i] = m.group(1)

    if not bare:
        return raw_text

    # group consecutive (within 3 lines) bare-number entries
    bare_indices = sorted(bare)
    groups: list[list[int]] = []
    cur: list[int] = [bare_indices[0]]
    for idx in bare_indices[1:]:
        if idx - cur[-1] <= 3:
            cur.append(idx)
        else:
            groups.append(cur); cur = [idx]
    groups.append(cur)

    consumed: set[int] = set()
    replacements: dict[int, str] = {}

    for grp in groups:
        if len(grp) < 2:
            continue  # single bare number is likely a data cell, not a TOC anomaly

        # build a set of the sibling numbers so we know when one title ends
        sibling_nums = {bare[i] for i in grp}

        # locate the orphan title block: first non-numeric, non-empty line after the group
        start_search = grp[-1] + 1
        orphan_start = None
        for j in range(start_search, min(start_search + 30, n)):
            l = lines[j].strip()
            if not l:
                continue
            sclean = re.sub(r"[\.…]{2,}", "  ", l)
            if SEC_START.match(l) or PART_RE.match(sclean):
                continue  # skip over sibling section entries that have titles already
            orphan_start = j
            break

        if orphan_start is None:
            continue

        # Collect all orphan title lines starting at orphan_start.
        # Stop at any section entry, PART marker, or page header.
        orphan_lines: list[str] = []
        orphan_idxs: list[int] = []
        j = orphan_start
        while j < n:
            l = lines[j].strip()
            if not l:
                j += 1
                continue
            sclean = re.sub(r"[\.…]{2,}", "  ", l)
            if SEC_START.match(l) or PART_RE.match(sclean):
                break
            if re.search(r"CONTENTS\s+Page|DBS GROUP|OCBC|UOB GROUP", l, re.I):
                break
            orphan_lines.append(l)
            orphan_idxs.append(j)
            j += 1

        nslots = len(grp)
        total = len(orphan_lines)
        if total < nslots:
            continue  # not enough lines to fill all slots

        # Divide evenly: each slot gets (total // nslots) lines;
        # the last slot absorbs any remainder.
        per = total // nslots
        for k, bare_idx in enumerate(grp):
            start = k * per
            end = start + per if k < nslots - 1 else total
            title = " ".join(orphan_lines[start:end])
            replacements[bare_idx] = f"{bare[bare_idx]} {title}"
        for idx in orphan_idxs:
            consumed.add(idx)

    result = []
    for i, l in enumerate(lines):
        if i in consumed:
            continue
        result.append(replacements.get(i, l))
    return "\n".join(result)


def _parse_contents(text: str) -> list[dict]:
    """Parse printed CONTENTS into ordered Part + section entries.
    PART headers are optional (DBS has them, OCBC/UOB do not). Wrapped TOC lines
    (long titles whose page number falls on the next line) are merged first."""
    entries, current_part = [], None
    for line in _merge_wrapped_lines(_patch_twocol_toc(text)):
        sclean = re.sub(r"[\.…]{2,}", "  ", line)
        m = PART_RE.match(sclean)
        if m:
            current_part = m.group(1).upper()
            entries.append({"kind": "part", "part": current_part,
                            "title": m.group(2).strip().rstrip(". ")})
            continue
        m = SEC_RE.match(line)                    # 1) section with a printed page ref
        if m:
            number, title, ref = m.group(1), m.group(2), _norm_ref(m.group(3))
        else:                                     # 2) section without a page ref (e.g. DBS subsections)
            m = SEC_NOREF_RE.match(line)
            if not m:
                continue
            number = m.group(1)
            title  = (m.group(2) or "").strip().rstrip(". ") or number  # blank title -> use number
            ref    = ""
        # For part-structured docs (DBS), prefix the number with the part letter
        # so B.1.1 stays B.1.1 rather than colliding with A.1.1
        if current_part:
            sid = f"{current_part}.{number}"
        else:
            sid = number
        entries.append({"kind": "section", "part": current_part, "number": number,
                        "section_id": sid, "title": title, "page_ref": ref})
    # dedupe by id/part, keep first
    seen, out = set(), []
    for e in entries:
        key = f"{e['kind']}:{e.get('section_id') or e.get('part')}"
        if key not in seen:
            seen.add(key); out.append(e)
    return out

def _footer_token(lines: list[str]) -> str:
    """Extract a page token from page header/footer lines.
    Handles:
      - 'A-2' standalone letter label (DBS Part-structured)
      - 'Page 5' anywhere
      - trailing letter label e.g. 'A-2' at end of line
      - OCBC header pattern: 'Pillar 3 Disclosures December 2025 6' (page# at end of first line)
      - short footer with trailing page number
    """
    if not lines:
        return ""
    # OCBC: "Pillar 3 Disclosures December 2025 6" — year followed by page number.
    # pypdfium2 puts this on the first line; pdfplumber puts it on the last line.
    # Search all lines so both orderings work.
    _year_page_re = re.compile(r"\b20\d\d\s+(\d{1,4})\s*$")
    for l in lines:
        m = _year_page_re.search(l)
        if m:
            return str(int(m.group(1)))

    # UOB: "Page 36" appears as the second line under a title header
    for l in lines[:3]:
        m = re.match(r"^Page\s+(\d{1,4})\s*$", l, re.I)
        if m:
            return str(int(m.group(1)))

    tail = lines[-2:]
    for l in tail:                                # standalone letter label
        m = LABEL_RE.match(l)
        if m:
            return f"{m.group(1)}-{int(m.group(2))}"
    for l in tail:                                # "Page 5"
        m = re.search(r"\bPage\s+(\d{1,4})\b", l, re.I)
        if m:
            return str(int(m.group(1)))
    for l in tail:                                # trailing letter label
        m = re.search(r"([A-E]-\d{1,3})\s*$", l)
        if m:
            return _norm_ref(m.group(1))
    if tail:                                      # trailing page number on a short footer
        last = tail[-1]
        # reject lines that look like data rows: contain commas, multiple numbers,
        # currency symbols, or are too long — these are table footers not page footers
        if (len(last) <= 60
                and "," not in last
                and "$" not in last
                and len(re.findall(r"\d+", last)) <= 2):
            m = re.search(r"(\d{1,4})\s*$", last)
            if m:
                return str(int(m.group(1)))
    return ""

def _scan_page_map(pdf_path: str, pdf) -> dict[str, int]:
    """token -> physical page (1-based). First occurrence wins.
    Uses pdfplumber for correct visual line order (the footer must be the last
    line); falls back to pypdfium2 if pdfplumber is unavailable."""
    m: dict[str, int] = {}
    if pdfplumber is not None:
        with pdfplumber.open(pdf_path) as pl:
            for i, page in enumerate(pl.pages):
                lines = [l.strip() for l in (page.extract_text() or "").splitlines() if l.strip()]
                tok = _footer_token(lines)
                if tok and tok not in m:
                    m[tok] = i + 1
    else:
        for i in range(len(pdf)):
            lines = [l.strip() for l in _page_text(pdf, i).splitlines() if l.strip()]
            tok = _footer_token(lines)
            if tok and tok not in m:
                m[tok] = i + 1
    return m

def _norm_title(s: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation — for fuzzy matching."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _title_sim(a: str, b: str) -> float:
    """Word-overlap Jaccard between two normalized title strings."""
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)

def _outline_map(pdf) -> tuple[dict[str, int], dict[str, int]]:
    """Embedded bookmarks -> two maps:
    number_map: {leading-number-in-title: physical page}  (existing behavior)
    title_map:  {norm_title: physical page}               (new: collision-safe title lookup)
    """
    number_map: dict[str, int] = {}
    title_map:  dict[str, int] = {}
    try:
        for b in pdf.get_toc():
            dest = b.get_dest()
            pg = (dest.get_index() + 1) if dest else None
            title = (b.get_title() or "").strip()
            if not pg:
                continue
            mm = re.match(r"^(\d+(?:\.\d+)*)\b", title)
            if mm and mm.group(1) not in number_map:
                number_map[mm.group(1)] = pg
            nt = _norm_title(title)
            if nt and nt not in title_map:
                title_map[nt] = pg
    except Exception:
        pass
    return number_map, title_map

def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())

def _page_lines(pdf_path: str, pdf) -> list[list[str]]:
    """Per physical page, its non-empty text lines (visual order via pdfplumber)."""
    out: list[list[str]] = []
    if pdfplumber is not None:
        with pdfplumber.open(pdf_path) as pl:
            for page in pl.pages:
                out.append([l.strip() for l in (page.extract_text() or "").splitlines() if l.strip()])
    else:
        for i in range(len(pdf)):
            out.append([l.strip() for l in _page_text(pdf, i).splitlines() if l.strip()])
    return out

def _heading_page(page_lines: list[list[str]], number: str, title: str,
                  start: int, end: int, top_lines: int = 9) -> int | None:
    """First page in [start, end] whose content matches this subsection.

    Pass 1 (precise): ANY line on the page that STARTS with the exact subsection
      number, e.g. "11.2 Comparison of Modelled...". Scans the full page because
      some sections begin mid-page (their heading is not in the first 9 lines).
      The (?!\\d) guard stops "12.2.1" matching "12.2.10".
    Pass 2 (fallback): a line in the top `top_lines` whose words cover >=80% of
      the title — kept narrow to avoid false positives from table data."""
    n = len(page_lines)
    hi = min(end, n)
    num_re = re.compile(rf"^{re.escape(number)}(?!\d)\b")
    for p in range(start, hi + 1):
        for ln in page_lines[p - 1]:          # full page scan for exact number match
            if num_re.match(ln):
                return p
    tw = set(_words(title))
    if tw:
        for p in range(start, hi + 1):
            for ln in page_lines[p - 1][:top_lines]:   # top lines only for fuzzy match
                if len(tw & set(_words(ln))) / len(tw) >= 0.80:
                    return p
    return None

# ---------------------------------------------------------------------------
def build_toc(pdf_path: str) -> dict:
    pdf = pdfium.PdfDocument(pdf_path)
    n = len(pdf)
    warnings: list[str] = []

    c_idx = _find_contents_start(pdf)
    contents = _parse_contents(_collect_contents_text(pdf, c_idx))
    token_map              = _scan_page_map(pdf_path, pdf)
    outline_map, outline_title_map = _outline_map(pdf)

    parts = [e for e in contents if e["kind"] == "part"]
    secs  = [e for e in contents if e["kind"] == "section"]
    style = "part_structured" if parts else "numbered"

    # LEAF sections = the smallest subsections (no child whose number extends them).
    # e.g. given 5, 5.1, 5.2 -> leaves are 5.1 and 5.2; a section like 4 with no
    # children is itself a leaf. These become one tab each.
    def _is_leaf(s):
        pre = s["number"] + "."
        return not any(o["number"].startswith(pre) for o in secs if o is not s)
    leaves = [s for s in secs if _is_leaf(s)]
    if not leaves:
        warnings.append("no sections parsed from contents page")

    # Sort key: part letter maps to a number (A=0, B=1, C=2...) so that Part C
    # sections always sort AFTER Part B, which sorts after Part A.
    _PART_ORDER = {None: 0, "A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
    def _numlist(s):
        part_ord = _PART_ORDER.get(s.get("part"), 0)
        return [part_ord] + [int(x) for x in s["number"].split(".")]

    # --- 1) anchor TOP-LEVEL sections first (they carry the printed page refs) ---
    top = [s for s in secs if "." not in s["number"]]
    for s in top:
        phys = token_map.get(s["page_ref"]) if s["page_ref"] else None
        if phys is None:
            phys = outline_map.get(s["number"])
        # part-structured fallback: Part B section 1 -> try token "B-1"
        if phys is None and s.get("part"):
            derived = f"{s['part']}-{s['number']}"
            phys = token_map.get(derived)
        s["start_page"] = phys
    last = None
    for s in sorted(top, key=_numlist):
        if s.get("start_page") is None:
            s["start_page"] = last if last is not None else 1
            warnings.append(f"top-level {s['section_id']} ('{s['title'][:30]}') unanchored; inherited p{s['start_page']}")
        last = s["start_page"]
    top.sort(key=lambda s: (s["start_page"], _numlist(s)))
    # Key by section_id (part-aware) so Part B "1" and Part C "1" don't collide.
    top_range: dict[str, tuple[int, int]] = {}
    for i, s in enumerate(top):
        nxt = top[i + 1]["start_page"] if i + 1 < len(top) else n + 1
        top_range[s["section_id"]] = (s["start_page"], nxt - 1)

    # --- 2) anchor each leaf: page ref -> outline -> HEADING SEARCH (body) -> inherit ---
    # In part-structured docs (DBS), bare numbers like "1.1" appear in multiple
    # parts (B.1.1 AND C.1.1 both have number="1.1"). The outline only has one
    # entry per number so we skip number-based outline lookup for non-Part-A leaves.
    # Title-based outline lookup is collision-safe and applies to all parts.
    def _outline_get(s):
        part = s.get("part")
        # Number-based: Part A only (numbers repeat across parts)
        by_num = outline_map.get(s["number"]) if (not part or part == "A") else None
        # Title-based: safe for all parts — exact norm match first, then fuzzy ≥0.85
        by_title = None
        nt = _norm_title(s["title"])
        if nt:
            by_title = outline_title_map.get(nt)
            if by_title is None:
                for otitle, opg in outline_title_map.items():
                    if _title_sim(nt, otitle) >= 0.85:
                        by_title = opg
                        break
        return by_num or by_title

    def _ancestor_range(section_id: str) -> tuple[int, int] | None:
        """Walk the section_id prefix chain upward until we find a top_range entry.
        e.g. A.12.2.1 -> try A.12.2, A.12, A — returns first hit."""
        parts = section_id.split(".")
        for depth in range(len(parts) - 1, 0, -1):
            ancestor = ".".join(parts[:depth])
            if ancestor in top_range:
                rng = top_range[ancestor]
                # Guard against inverted range (siblings sharing a start page
                # produce end < start via nxt-1). Extend end to at least start.
                return (rng[0], max(rng[0], rng[1]))
        return None

    # always build page_lines — needed for heading search and end_page refinement
    page_lines = _page_lines(pdf_path, pdf)

    anchored = heading_anchored = 0
    # Track last anchored page per sorted order for monotonicity enforcement
    _last_anchored: list[int] = [1]

    for s in sorted(leaves, key=_numlist):
        if "." not in s["number"]:                       # top-level leaf already anchored above
            if s.get("start_page"):
                anchored += 1
                _last_anchored[0] = s["start_page"]
            continue
        phys = token_map.get(s["page_ref"]) if s["page_ref"] else None

        # Fallback 1: outline (number-based for Part A, title-based for all parts)
        if phys is None:
            phys = _outline_get(s)

        # Fallback 2 + heading search: walk ancestor chain to find search range,
        # then scan body text for number match (Pass 1) or title match (Pass 2).
        if phys is None and page_lines is not None:
            rng = _ancestor_range(s["section_id"])
            if rng:
                # Constrain search start to be >= last anchored page (monotonicity)
                search_start = max(rng[0], _last_anchored[0])
                phys = _heading_page(page_lines, s["number"], s["title"],
                                     search_start, rng[1])
                if phys:
                    heading_anchored += 1

        s["start_page"] = phys
        if phys:
            anchored += 1
            _last_anchored[0] = phys

    # fill remaining gaps in document order (heading search missed -> inherit prior leaf)
    last = None
    for s in sorted(leaves, key=_numlist):
        if s.get("start_page") is None:
            s["start_page"] = last if last is not None else 1
            warnings.append(f"{s['section_id']} ('{s['title'][:30]}') "
                            f"unanchored (ref={s['page_ref'] or '—'}); inherited p{s['start_page']}")
        last = s["start_page"]

    def _numkey(s):
        return (s["start_page"], _numlist(s))
    leaves.sort(key=_numkey)
    for i, s in enumerate(leaves):
        nxt = leaves[i + 1]["start_page"] if i + 1 < len(leaves) else n + 1
        s["end_page"] = max(s["start_page"], nxt - 1)

    # Refine end_page using the heading scan: find the last physical page where
    # this section's number appears as a heading (handles both over-extension into
    # trailing content AND under-extension when a section continues onto a shared
    # page that is also the start_page of the next section).
    if page_lines is not None:
        num_re_cache: dict[str, re.Pattern] = {}
        _cont_re = re.compile(r"\(cont(?:inued|'d|’d|d)?\.?\)", re.I)
        for i, s in enumerate(leaves):
            num = s["number"]
            if num not in num_re_cache:
                num_re_cache[num] = re.compile(rf"^{re.escape(num)}(?!\d)\b")
            nr = num_re_cache[num]
            next_start = leaves[i + 1]["start_page"] if i + 1 < len(leaves) else n + 1
            # Scan up to end_page + 1 to catch (cont'd) headings on shared pages.
            # On the shared page (next section's start_page), only extend end_page
            # if the heading appears WITH a continuation marker — meaning the section
            # genuinely continues there. Without a marker, the heading belongs to the
            # next section's page and we should not claim it.
            scan_end = min(s["end_page"] + 1, n)
            last_seen = None
            for p in range(s["start_page"], scan_end + 1):
                for ln in page_lines[p - 1]:
                    if nr.match(ln):
                        # On a shared page, require (continued) marker to extend
                        if p >= next_start and not _cont_re.search(ln):
                            break
                        last_seen = p
                        break
            if last_seen is not None and last_seen != s["end_page"]:
                s["end_page"] = last_seen

    # ── Orphan-page repair ──────────────────────────────────────────────────────
    # For every page strictly between one section's end_page and the next section's
    # start_page, assign ownership by evidence:
    #   (a) page text matches NEXT section's number/title → pull next start back
    #   (b) page text matches CURRENT section's number/title (incl. cont markers)
    #       → extend current end_page
    #   (c) no evidence → extend current end_page + loud warning
    # Part-divider pages (before the first leaf or after the last) are skipped.
    if page_lines is not None:
        _cont_re_orphan = re.compile(r"\(cont(?:inued|'d|’d|d)?\.?\)|continued\b", re.I)
        for i, cur in enumerate(leaves):
            if i + 1 >= len(leaves):
                break
            nxt = leaves[i + 1]
            # Interior gap: pages strictly between cur.end_page and nxt.start_page
            for g in range(cur["end_page"] + 1, nxt["start_page"]):
                if g < 1 or g > n:
                    continue
                g_lines = page_lines[g - 1]
                # Build patterns for current and next sections.
                # Also include the next section's top-level parent number so that
                # a part-intro page like "23. LCR Disclosures" (before leaf 23.1)
                # is correctly attributed to the next section family.
                cur_nr = re.compile(rf"^{re.escape(cur['number'])}(?!\d)\b")
                nxt_nr = re.compile(rf"^{re.escape(nxt['number'])}(?!\d)\b")
                nxt_top = nxt["number"].split(".")[0]
                nxt_top_nr = re.compile(rf"^{re.escape(nxt_top)}[\.\s]")
                cur_tw = set(_words(cur["title"]))
                nxt_tw = set(_words(nxt["title"]))
                # Check for evidence
                evidence_next = any(
                    nxt_nr.match(ln) or nxt_top_nr.match(ln) or
                    (nxt_tw and len(nxt_tw & set(_words(ln))) / len(nxt_tw) >= 0.85)
                    for ln in g_lines
                )
                evidence_cur = any(
                    cur_nr.match(ln) or _cont_re_orphan.search(ln) or
                    (cur_tw and len(cur_tw & set(_words(ln))) / len(cur_tw) >= 0.85)
                    for ln in g_lines
                )
                if evidence_next:
                    print(f"   🔧 orphan p{g}: matches next section {nxt['section_id']} "
                          f"— pulling start back from p{nxt['start_page']}")
                    nxt["start_page"] = g
                elif evidence_cur:
                    print(f"   🔧 orphan p{g}: matches current section {cur['section_id']} "
                          f"(continuation) — extending end from p{cur['end_page']}")
                    cur["end_page"] = g
                else:
                    print(f"   ⚠ orphan p{g}: no heading evidence — assigned to "
                          f"{cur['section_id']} by default (verify)")
                    warnings.append(f"orphan page {g} assigned to {cur['section_id']} "
                                    f"by default — no heading evidence found")
                    cur["end_page"] = g

    # flag leaves that share a start page (two subsections on one page -> their
    # tables would otherwise be extracted into both tabs)
    by_start: dict[int, list] = {}
    for s in leaves:
        by_start.setdefault(s["start_page"], []).append(s["section_id"])
    for pg, ids in by_start.items():
        if len(ids) > 1:
            warnings.append(f"subsections share page {pg}: {', '.join(ids)} "
                            f"(tables on that page may need splitting between tabs)")

    part_nodes = []
    for p in parts:
        members = [s for s in leaves if s["part"] == p["part"]]
        if members:
            part_nodes.append({"part": p["part"], "title": p["title"],
                               "start_page": min(s["start_page"] for s in members),
                               "end_page":   max(s["end_page"]   for s in members)})

    title = next((l.strip() for l in _page_text(pdf, 0).splitlines() if l.strip()), "Unknown")

    # Detect reporting date from early pages for period-keyed naming
    _date_re = re.compile(
        r'\b(\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+20\d{2}\b', re.I)
    doc_date = ""
    for pi in range(min(4, n)):
        m = _date_re.search(_page_text(pdf, pi))
        if m:
            doc_date = m.group(0).strip()
            break

    # build a page-range lookup for intermediate nodes from their leaf descendants
    leaf_by_sid = {s["section_id"]: s for s in leaves}
    def _node_range(number: str, part: str | None) -> tuple[int, int] | None:
        kids = [s for s in leaves if
                s["number"].startswith(number + ".") and s.get("part") == part]
        if not kids:
            return None
        return min(s["start_page"] for s in kids), max(s["end_page"] for s in kids)

    # all_sections: full hierarchy (intermediate + leaf), sorted by document order
    all_sec_list = []
    for s in secs:
        is_leaf = not any(o["number"].startswith(s["number"] + ".") and o.get("part") == s.get("part")
                          for o in secs if o is not s)
        if is_leaf and s["section_id"] in leaf_by_sid:
            ls = leaf_by_sid[s["section_id"]]
            all_sec_list.append({
                "section_id": s["section_id"], "part": s["part"], "number": s["number"],
                "title": s["title"], "page_ref": s["page_ref"],
                "start_page": ls["start_page"], "end_page": ls["end_page"],
                "is_leaf": True,
            })
        else:
            rng = _node_range(s["number"], s.get("part"))
            all_sec_list.append({
                "section_id": s["section_id"], "part": s["part"], "number": s["number"],
                "title": s["title"], "page_ref": s["page_ref"],
                "start_page": rng[0] if rng else None,
                "end_page":   rng[1] if rng else None,
                "is_leaf": False,
            })

    return {
        "document":  {"title": title, "pages": n, "doc_date": doc_date},
        "provenance": {
            "method": "deterministic: printed contents + footer page-token scan (zero API)",
            "toc_style": style,
            "granularity": "leaf_subsections",
            "contents_page": c_idx + 1,
            "footer_tokens_found": len(token_map),
            "outline_entries": len(outline_map),
            "sections_anchored": f"{anchored}/{len(leaves)}",
            "heading_search_anchored": heading_anchored,
            "warnings": warnings,
        },
        "parts": part_nodes,
        "sections": [                              # LEAVES only — used by extract_to_excel.py
            {"section_id": s["section_id"], "part": s["part"], "number": s["number"],
             "title": s["title"], "page_ref": s["page_ref"],
             "start_page": s["start_page"], "end_page": s["end_page"]}
            for s in leaves
        ],
        "all_sections": all_sec_list,             # FULL hierarchy — for display / audit
    }

# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Deterministic TOC extraction (zero API)")
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None,
                    help="output path (default: outputs/pillar3/{bank}_{period}_toc.json)")
    args = ap.parse_args()
    if not os.path.exists(args.pdf):
        sys.exit(f"PDF not found: {args.pdf}")

    toc = build_toc(args.pdf)
    out_path = args.out or _toc_out_path(args.pdf, toc)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(toc, f, indent=2)

    pv = toc["provenance"]
    print(f"📑 {toc['document']['title'][:60]}  ({toc['document']['pages']} pages)")
    print(f"   style={pv['toc_style']}  footer-tokens={pv['footer_tokens_found']}  "
          f"outline={pv['outline_entries']}  anchored={pv['sections_anchored']}")
    print(f"   {len(toc['parts'])} parts, {len(toc['sections'])} top-level sections -> {out_path}")
    for s in toc["sections"]:
        print(f"     {s['section_id']:<8} p{s['start_page']}-{s['end_page']:<3} "
              f"[ref {s['page_ref'] or '—'}]  {s['title'][:46]}")
    if pv["warnings"]:
        print(f"   ⚠️  {len(pv['warnings'])} warning(s):")
        for w in pv["warnings"]:
            print(f"       - {w}")

if __name__ == "__main__":
    main()
