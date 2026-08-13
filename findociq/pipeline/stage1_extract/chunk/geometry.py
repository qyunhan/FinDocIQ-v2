"""stage1_extract.chunk.geometry — PASS2 geometry stage.

Derives, from the PDF *text layer* (pdfplumber chars, not the model's JSON),
per-row ground truth for row hierarchy: {printed-line identity, indent depth,
superscript-stripped clean label}. This is a side-car written into a unit's
parsed.json under a top-level "geometry" key — it never touches "tables".

Why: the model's `level` field wobbles on financial-statement typography
(section-header/twin rows, footnote superscripts glued to labels, indentation
rendered with leading spaces rather than a stable x0). The printed page itself
carries all of this deterministically. Every threshold below is expressed
relative to font size, so it generalises across banks/documents rather than
being tuned to one PDF.

Algorithm
---------
1. Line clustering: chars on a page are grouped into printed lines by `top`,
   tolerance 0.55 x modal char size (banks emit detached superscript char runs
   a hair above the baseline; a fixed bucket splits them apart, this
   tolerance keeps them attached to their row while staying below normal row
   pitch).
2. Superscript test per char within a line: size < line_median_size - 0.5 AND
   top < line_median_top - 0.15 (both required).
3. Label band: chars left of the value-column band start are the "label"
   portion of a line. Value-column bands are calibrated with
   `transforms._calibrate_bands` (word-level, dense-line based) — reused, not
   duplicated. If calibration fails, the whole line is treated as label and
   the table's geometry is flagged low-confidence.
4. ink_x0: x0 of the first non-space, non-superscript char of a line's label
   portion (banks indent with leading spaces, so the raw first-char x0 is
   NOT indent-safe).
5. Indent levels: per table, single-linkage cluster the ink_x0 of matched
   rows, threshold 0.5 x body (modal) char size; indent = cluster rank
   (leftmost = 0).
6. Row<->line alignment: monotone forward scan matching norm(row.label)
   against norm(line.clean_text) OR norm(line.raw_text). A row may re-match
   the same line as the previous row (phantom section-header + data twin) but
   never an earlier one. If no single line matches, a 2- or 3-line WRAP MERGE
   is tried (word-wrapped labels split across physical lines): earlier
   line(s) must carry no value-band content, the continuation's ink_x0 must
   be >= the first line's, and the concatenated text must match — see
   `_try_wrap_merge`. A wrap merge's line_id/ink_x0/indent are the FIRST
   line's; label_clean is the full concatenated clean text.

Output (written into parsed.json, replacing only the "geometry" key):
    {"geometry": {"source": "pages.pdf"|"source_pdf"|"unavailable",
                  "tables": [{"rows": [{"line_id", "indent", "label_clean"}, ...],
                              "title_clean", "col_labels_clean",
                              "all_rows_matched"}, ...]}}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path
from stage1_extract.chunk.transforms import _calibrate_bands, _is_numeric_token, _tag_printed_lines  # noqa: E402

# ---------------------------------------------------------------------------
# Tunables (bank-agnostic, relative to font size — see module docstring).
# ---------------------------------------------------------------------------
LINE_TOL_FACTOR = 0.55          # x modal char size, for grouping chars into lines
SUPERSCRIPT_SIZE_DELTA = 0.5    # pt, size < line_median_size - this
SUPERSCRIPT_TOP_DELTA = 0.15    # pt, top < line_median_top - this
INDENT_CLUSTER_FACTOR = 0.5     # x body (modal) char size

# Unicode superscript characters banks/models sometimes emit inline in labels
# (e.g. '¹²³'); stripped defensively from clean_text even though this PDF's
# text layer renders footnote markers as plain small digits, not these code
# points.
_UNICODE_SUPERSCRIPT_RE = re.compile(
    "[¹²³⁰⁴-⁹]"
)

# Trailing footnote-marker tail: digits/commas/spaces/superscripts/asterisks
# at the END of a string. Used only as a *fallback* norm when the literal
# casefold+whitespace norm fails to align a row to a line — handles the model
# occasionally keeping a footnote digit in row.label (e.g. 'Provision for
# CSR¹') while the PDF text layer renders it as a plain small digit that
# the superscript test strips from clean_text but not from raw_text.
_FOOTNOTE_TAIL_RE = re.compile(r"[\s\d¹²³⁰⁴-⁹,*]+$")


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------
def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def strip_unicode_superscripts(s: str) -> str:
    """Remove Unicode superscript characters (defensive; see module docstring)."""
    return _UNICODE_SUPERSCRIPT_RE.sub("", s or "")


def norm(s: str) -> str:
    """casefold + collapse whitespace — the row<->line matching key."""
    return _collapse_ws(s).casefold()


def norm_tail_stripped(s: str) -> str:
    """`norm()` plus a trailing footnote-marker strip — fallback matching key."""
    return _FOOTNOTE_TAIL_RE.sub("", norm(s)).strip()


# ---------------------------------------------------------------------------
# Char-level line clustering (step 1) + superscript test (step 2)
# ---------------------------------------------------------------------------
def modal_char_size(page) -> float:
    sizes = [round(c["size"], 2) for c in page.chars if c.get("size")]
    if not sizes:
        return 9.0
    return Counter(sizes).most_common(1)[0][0]


def cluster_char_lines(page, modal_size: float | None = None) -> list[list[dict]]:
    """Group a pdfplumber page's chars into printed lines by `top`, tolerance
    LINE_TOL_FACTOR x modal char size. Each line is chars sorted by x0."""
    if modal_size is None:
        modal_size = modal_char_size(page)
    tol = LINE_TOL_FACTOR * modal_size
    chars_sorted = sorted(page.chars, key=lambda c: c["top"])
    if not chars_sorted:
        return []
    lines: list[list[dict]] = []
    current = [chars_sorted[0]]
    current_top = chars_sorted[0]["top"]
    for c in chars_sorted[1:]:
        if abs(c["top"] - current_top) <= tol:
            current.append(c)
        else:
            lines.append(sorted(current, key=lambda c: c["x0"]))
            current = [c]
            current_top = c["top"]
    lines.append(sorted(current, key=lambda c: c["x0"]))
    return lines


def _is_superscript(c: dict, med_size: float, med_top: float) -> bool:
    return (c["size"] < med_size - SUPERSCRIPT_SIZE_DELTA
            and c["top"] < med_top - SUPERSCRIPT_TOP_DELTA)


def _as_rgb(value, space, *, infer: bool = False) -> tuple | None:
    """A pdfplumber colour -> an RGB triple, or None when it CANNOT be read.

    The component value is meaningless without its colour space, and the two
    disagree about the same number: in DeviceGray `(1.0,)` is white, in a
    Separation space `(1.0,)` is FULL colorant — solid black. OCBC's filings
    set their body text in a Separation space and report `(1.0,)` for every
    character on the page; UOB's set theirs in DeviceGray, where `(1.0,)` is
    the invisible glyph we are hunting. Reading the number alone marked every
    OCBC page as entirely invisible.

    So: interpret only the device spaces, and return None for anything else
    (Separation, DeviceN, ICCBased, Indexed, Pattern) — None means "unknown",
    and every caller treats unknown as VISIBLE. Under-detecting an invisible
    glyph costs one unstamped row; over-detecting deletes printed content."""
    if value is None:
        return None
    t = tuple(value) if isinstance(value, (list, tuple)) else (value,)
    # pdfplumber reports `ncs` for chars but NOT for rects/curves. For a shape
    # the component count is the only signal there is, and `infer=True` says to
    # use it. Never inferred for a CHARACTER: that is exactly where guessing
    # DeviceGray from a 1-tuple would misread OCBC's Separation black as white.
    if space is None and infer:
        space = {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(len(t))
    if space == "DeviceGray" and len(t) == 1:
        return (t[0],) * 3
    if space == "DeviceRGB" and len(t) == 3:
        return t
    if space == "DeviceCMYK" and len(t) == 4:
        c, m, y, k = t
        return (1 - min(1, c + k), 1 - min(1, m + k), 1 - min(1, y + k))
    return None


def page_backdrops(page) -> list[tuple]:
    """(x0, top, x1, bottom, rgb) for every FILLED shape on the page, in paint
    order — the candidate backgrounds a character can be sitting on. Shapes in
    an uninterpretable colour space are skipped, so a character over one keeps
    the page's own white as its background and stays visible unless it is white
    itself."""
    out = []
    for s in (page.rects + page.curves):
        if not s.get("fill"):
            continue
        # rgb may be None -- an unreadable backdrop is KEPT, so is_invisible
        # can see that it cannot prove what is behind the character.
        rgb = _as_rgb(s.get("non_stroking_color"), s.get("ncs"), infer=True)
        out.append((s["x0"], s["top"], s["x1"], s["bottom"], rgb))
    return out


def is_invisible(c: dict, backdrops: list[tuple]) -> bool:
    """Does this character paint no ink a reader could see?

    True when its fill matches whatever is painted behind it — the last filled
    shape covering it, else the page's own white. NOT simply "is it white":
    white text on a dark band is legitimately visible content.

    Why this exists: UOB's 2Q26 filing carries a white 'Less:' on the
    'Allowance for credit and other losses' row — real character objects with a
    font and a position, painted invisibly. Nothing downstream could see it was
    invisible, so both readers ingested it: the label gained a prefix the page
    never shows, and because the glyph sits at the outer margin it also dragged
    `ink_x0` to the left and flattened the row's printed indent."""
    fill = _as_rgb(c.get("non_stroking_color"), c.get("ncs"))
    if fill is None:
        return False                      # unreadable colour space -> visible
    bg = (1.0, 1.0, 1.0)          # the page itself, when nothing is painted over it
    for x0, top, x1, bottom, rgb in backdrops:
        if x0 <= c["x0"] and x1 >= c["x1"] and top <= c["top"] and bottom >= c["bottom"]:
            bg = rgb
    if bg is None:
        return False              # unreadable backdrop -> cannot prove invisibility
    return fill == bg


def _line_stats(line: list[dict]) -> tuple[float, float]:
    sizes = [c["size"] for c in line]
    tops = [c["top"] for c in line]
    return statistics.median(sizes), statistics.median(tops)


# ---------------------------------------------------------------------------
# Per-table printed-line records: label band (step 3), ink_x0 (step 4)
# ---------------------------------------------------------------------------
def _line_words(line: list[dict]) -> list[dict]:
    """Group a line's chars (already sorted by x0) into whitespace-separated
    words: [{"text", "x0"}, ...]."""
    words: list[dict] = []
    cur: list[dict] = []
    for c in line:
        if c["text"].strip() == "":
            if cur:
                words.append({"text": "".join(w["text"] for w in cur), "x0": cur[0]["x0"]})
                cur = []
        else:
            cur.append(c)
    if cur:
        words.append({"text": "".join(w["text"] for w in cur), "x0": cur[0]["x0"]})
    return words


def _per_line_numeric_cutoff(line: list[dict]) -> float | None:
    """x0 of the leftmost numeric-token word on this printed line, or None if
    the line has no numeric tokens (e.g. a bare section-header line). Reuses
    transforms._is_numeric_token so the "what counts as a value, not a label
    word" rule can never drift between the column-band calibrator and here."""
    xs = [w["x0"] for w in _line_words(line) if _is_numeric_token(w["text"])]
    return min(xs) if xs else None


def build_table_lines(pages: list, band_start: float | None) -> list[dict]:
    """One record per printed line across `pages` (in page order, top order),
    each: {"raw_text", "clean_text", "raw_norm", "clean_norm", "raw_tail",
    "clean_tail", "ink_x0"}. `band_start` is the x0 of the leftmost
    value-column band from table-wide calibration. When calibration
    succeeded, `band_start` is authoritative (trusted over any single line —
    a label word that happens to BE a bare number, e.g. 'ECL Stage 3 (SP)',
    must not truncate the label). Only when calibration failed for the whole
    table (`band_start is None`) does each line fall back to its OWN
    leftmost numeric-token x0 as a cutoff — needed because otherwise an
    uncalibrated table would glue trailing printed values onto every row's
    "label" (e.g. row 'Basic' vs printed 'Basic 4.19 4.11 3.30')."""
    out: list[dict] = []
    for page in pages:
        m_size = modal_char_size(page)
        backdrops = page_backdrops(page)
        for line in cluster_char_lines(page, m_size):
            med_size, med_top = _line_stats(line)
            cutoff = band_start if band_start is not None else _per_line_numeric_cutoff(line)
            if cutoff is None:
                label_chars = line
            else:
                label_chars = [c for c in line if c["x0"] < cutoff] or line

            # `raw_*` keeps EVERY character, including any that paint no ink.
            # The extractor is handed the PDF and reads the same text layer, so
            # its label carries an invisible glyph too — raw_* is what has to
            # match it. `clean_*` and `ink_x0` are the PRINTED truth and drop
            # them: that split is what lets alignment succeed on the model's
            # label while identity and indent use what the page actually shows.
            raw_text = "".join(c["text"] for c in label_chars)

            clean_chars = [c for c in label_chars
                           if not _is_superscript(c, med_size, med_top)
                           and not is_invisible(c, backdrops)]
            clean_text = strip_unicode_superscripts("".join(c["text"] for c in clean_chars))
            clean_text = _collapse_ws(clean_text)

            ink_chars = [c for c in clean_chars if c["text"].strip() != ""]
            ink_x0 = min((c["x0"] for c in ink_chars), default=None)

            has_values = cutoff is not None and any(
                c["x0"] >= cutoff and c["text"].strip() != "" for c in line
            )

            out.append({
                "raw_text": _collapse_ws(raw_text),
                "clean_text": clean_text,
                "raw_norm": norm(raw_text),
                "clean_norm": norm(clean_text),
                "raw_tail": norm_tail_stripped(raw_text),
                "clean_tail": norm_tail_stripped(clean_text),
                "ink_x0": ink_x0,
                "has_values": has_values,
            })
    return out


# ---------------------------------------------------------------------------
# Row <-> line alignment (step 6) — monotone forward scan
# ---------------------------------------------------------------------------
# What may legitimately follow a label on an uncalibrated line: the row's own
# printed VALUES. Digits and value punctuation, plus the nil markers banks
# print in place of a number. Deliberately excludes ordinary letters — they are
# how a longer label is told apart from a shorter one that prefixes it.
# The symbol classes cover the marks a bank prints INSTEAD of a number: dashes
# for nil, and footnote/threshold marks — OCBC prints '#' for a value below its
# rounding floor ('Shares issued to non-executive directors # #').
_VALUE_PUNCT_RE = re.compile(r"[\s\d.,()%<>+/*#†‡§¶^~\-‐-―]+")
_NIL_MARKERS = {"", "nm", "na", "nil", "nmf", "nap"}


def _is_values_only(tail: str) -> bool:
    """Is `tail` nothing but this row's printed values?"""
    return _VALUE_PUNCT_RE.sub("", tail) in _NIL_MARKERS


def _startswith_boundary(haystack: str, prefix: str) -> bool:
    """haystack == prefix, or haystack starts with prefix and everything after
    it is just this row's printed VALUES.

    Needed when band calibration failed and a line's "label" text still carries
    its trailing printed values (e.g. row label 'Basic' vs uncalibrated line
    text 'basic 4.19 4.11 3.30').

    The test used to be "the next character is a space", which was wrong in both
    directions. It REJECTED a nil value printed flush against the label —
    'Bills and drafts payable- - -' — losing the row and, under
    apply_geometry's all-or-nothing rule, geometry for its whole table. And it
    ACCEPTED a longer label that merely starts with a shorter one: row 'Debts'
    matched the printed line 'debts issued- 3,599-' because a space follows
    'debts'. Asking whether the remainder is VALUES answers both: '- - -' is,
    ' issued- 3,599-' is not."""
    if not prefix:
        return False
    if haystack == prefix:
        return True
    return haystack.startswith(prefix) and _is_values_only(haystack[len(prefix):])


def _matches_label(rec: dict, t_norm: str, t_tail: str) -> bool:
    """The same 4-tier test used by the single-line scan below, applied to any
    record with raw_norm/clean_norm/raw_tail/clean_tail (a real line OR a
    synthetic merged-line record) — so wrapped-label matching can never drift
    from single-line matching."""
    if rec["raw_norm"] == t_norm or rec["clean_norm"] == t_norm:
        return True
    if _startswith_boundary(rec["raw_norm"], t_norm) or _startswith_boundary(rec["clean_norm"], t_norm):
        return True
    if t_tail and (rec["raw_tail"] == t_tail or rec["clean_tail"] == t_tail):
        return True
    if t_tail and (_startswith_boundary(rec["raw_tail"], t_tail)
                   or _startswith_boundary(rec["clean_tail"], t_tail)):
        return True
    return False


def _merge_lines(parts: list[dict]) -> dict:
    """Concatenate consecutive printed-line records into one synthetic record
    (raw_norm/clean_norm/raw_tail/clean_tail only) for matching a wrapped
    (word-wrapped) row label against 2-3 physical lines at once."""
    raw = " ".join(p["raw_text"] for p in parts if p["raw_text"])
    clean = " ".join(p["clean_text"] for p in parts if p["clean_text"])
    return {
        "raw_norm": norm(raw), "clean_norm": norm(clean),
        "raw_tail": norm_tail_stripped(raw), "clean_tail": norm_tail_stripped(clean),
    }


def _try_wrap_merge(lines: list[dict], start: int, t_norm: str, t_tail: str) -> int | None:
    """If a 2- or 3-line group starting at `start` is a valid wrapped-label
    merge candidate for this row (conditions a-d below) AND the merged text
    matches, return the group's END index; else None. Tries span 2 before 3.

    Conditions (all required):
      (a) every line EXCEPT THE LAST in the group carries no value-band
          content (`has_values` False) — a wrapped continuation line of a
          label is never followed by more label text once real values start;
      (b) the last line is unrestricted (may or may not carry values) —
          that's what makes it "last";
      (c) the merged raw/clean text matches the row label under the same
          4-tier test as single-line matching;
      (d) each continuation line's ink_x0 is >= the first line's ink_x0
          (wraps hang at or right of the start column, never left of it).
    """
    for span in (2, 3):
        end = start + span - 1
        if end >= len(lines):
            continue
        group = lines[start:end + 1]
        if any(g["has_values"] for g in group[:-1]):
            continue
        first_ink = group[0]["ink_x0"]
        if first_ink is None:
            continue
        if any(g["ink_x0"] is None or g["ink_x0"] < first_ink for g in group[1:]):
            continue
        if _matches_label(_merge_lines(group), t_norm, t_tail):
            return end
    return None


def align_rows_to_lines(row_labels: list[str], lines: list[dict]) -> tuple[list[int | None], list[int]]:
    """(matches, spans) — `matches[i]` is the line index (of the FIRST line,
    for a wrapped/merged match) or None; `spans[i]` is how many consecutive
    printed lines that row consumed (1 for a normal or unmatched row, 2/3 for
    a wrapped-label merge). Positionally aligned to `row_labels`.

    Monotone: never matches a line earlier than the previous row's match; may
    re-match the SAME line as the previous row (phantom section-header + data
    twin). A merge advances the cursor past every line it consumed.

    Matching tiers (first hit wins), tried in order because an uncalibrated
    table (band start unknown) leaves trailing printed values glued to the
    line's "label" text, so exact equality alone would never fire there:
      1. exact norm equality (raw or clean)
      2. norm prefix-with-word-boundary (raw or clean)
      3. tail-stripped (footnote-marker-agnostic) equality
      4. tail-stripped prefix-with-word-boundary
    Only if a row matches NONE of the above on any single line is a wrapped
    (2-3 line) merge attempted — see `_try_wrap_merge`.
    """
    result: list[int | None] = []
    spans: list[int] = []
    cursor = 0
    for label in row_labels:
        t_norm = norm(label)
        t_tail = norm_tail_stripped(label)
        found = None
        for idx in range(cursor, len(lines)):
            ln = lines[idx]
            if ln["raw_norm"] == t_norm or ln["clean_norm"] == t_norm:
                found = idx
                break
        if found is None:
            for idx in range(cursor, len(lines)):
                ln = lines[idx]
                if _startswith_boundary(ln["raw_norm"], t_norm) or _startswith_boundary(ln["clean_norm"], t_norm):
                    found = idx
                    break
        if found is None and t_tail:
            for idx in range(cursor, len(lines)):
                ln = lines[idx]
                if ln["raw_tail"] == t_tail or ln["clean_tail"] == t_tail:
                    found = idx
                    break
        if found is None and t_tail:
            for idx in range(cursor, len(lines)):
                ln = lines[idx]
                if _startswith_boundary(ln["raw_tail"], t_tail) or _startswith_boundary(ln["clean_tail"], t_tail):
                    found = idx
                    break

        span = 1
        merge_end = None
        if found is None:
            for idx in range(cursor, len(lines)):
                end = _try_wrap_merge(lines, idx, t_norm, t_tail)
                if end is not None:
                    found = idx
                    merge_end = end
                    span = end - idx + 1
                    break

        if found is not None:
            result.append(found)
            spans.append(span)
            cursor = merge_end if merge_end is not None else found
        else:
            result.append(None)
            spans.append(1)
    return result, spans


# ---------------------------------------------------------------------------
# Indent clustering (step 5) — single-linkage on ink_x0
# ---------------------------------------------------------------------------
def cluster_indent_levels(values: list[float], threshold: float) -> dict[float, int]:
    """value -> cluster rank (leftmost=0), single-linkage chain with `threshold`."""
    uniq = sorted(set(values))
    if not uniq:
        return {}
    clusters: list[list[float]] = [[uniq[0]]]
    for v in uniq[1:]:
        if v - clusters[-1][-1] <= threshold:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    mapping: dict[float, int] = {}
    for rank, cluster in enumerate(clusters):
        for v in cluster:
            mapping[v] = rank
    return mapping


# ---------------------------------------------------------------------------
# Per-table geometry
# ---------------------------------------------------------------------------
def compute_table_geometry(table: dict, pages: list) -> dict:
    """Geometry entry for one parsed.json table, against pdfplumber Page objects
    `pages` (the unit's full page span, in document order)."""
    n_cols = len(table.get("columns") or [])
    lines_words = _group_page_lines_for_pages(pages)
    tagged = _tag_printed_lines(lines_words)
    band_ranges, _n_dense = _calibrate_bands(tagged, n_cols) if n_cols else (None, 0)
    band_start = band_ranges[0][0] if band_ranges else None

    lines = build_table_lines(pages, band_start)

    rows = table.get("rows") or []
    row_labels = [r.get("label") or "" for r in rows]
    matches, spans = align_rows_to_lines(row_labels, lines)

    body_size = modal_char_size(pages[0]) if pages else 9.0
    indent_threshold = INDENT_CLUSTER_FACTOR * body_size
    matched_ink_x0 = [lines[i]["ink_x0"] for i in matches if i is not None and lines[i]["ink_x0"] is not None]
    indent_map = cluster_indent_levels(matched_ink_x0, indent_threshold)

    out_rows = []
    for idx, span in zip(matches, spans):
        if idx is None:
            out_rows.append({"line_id": None, "indent": None, "label_clean": None})
            continue
        ln = lines[idx]
        # ink_x0/indent/line_id are always the FIRST line's — that's the
        # visual indent, even for a wrapped (multi-line) label merge.
        ink = ln["ink_x0"]
        indent = indent_map.get(ink) if ink is not None else None
        if span > 1:
            label_clean = _collapse_ws(
                " ".join(lines[j]["clean_text"] for j in range(idx, idx + span) if lines[j]["clean_text"])
            ) or None
        else:
            label_clean = ln["clean_text"] or None
        out_rows.append({
            "line_id": idx,
            "indent": indent,
            "label_clean": label_clean,
        })

    title_clean = _find_clean_match(table.get("title") or "", lines)
    col_labels_clean = [
        _find_clean_match(col.get("leaf") or "", lines) for col in (table.get("columns") or [])
    ]

    return {
        "rows": out_rows,
        "title_clean": title_clean,
        "col_labels_clean": col_labels_clean,
        "all_rows_matched": bool(rows) and all(r["line_id"] is not None for r in out_rows),
        "band_calibrated": band_start is not None,
    }


def _find_clean_match(text: str, lines: list[dict]) -> str | None:
    """Best-effort: first printed line whose norm (or tail-stripped norm)
    equals norm(text); returns that line's superscript-stripped clean_text.
    Used for title/column-header cosmetics — not part of row alignment, so
    no monotonicity requirement."""
    if not text:
        return None
    t_norm = norm(text)
    t_tail = norm_tail_stripped(text)
    for ln in lines:
        if ln["raw_norm"] == t_norm or ln["clean_norm"] == t_norm:
            return ln["clean_text"] or None
    if t_tail:
        for ln in lines:
            if ln["raw_tail"] == t_tail or ln["clean_tail"] == t_tail:
                return ln["clean_text"] or None
    return None


def _group_page_lines_for_pages(pages: list) -> list[list[dict]]:
    """`transforms._group_page_lines` takes a pdf path + page numbers and
    re-opens the PDF; here we already hold open pdfplumber Page objects (they
    may come from a materialized/cropped pages.pdf with its own numbering), so
    reimplement the identical word-grouping logic directly against them. This
    duplicates _group_page_lines' *loop* (not the band-calibration logic,
    which is reused via _calibrate_bands) because that function's signature is
    path+page-numbers, incompatible with already-open Page objects."""
    tol = 3.0
    out: list[list[dict]] = []
    for page in pages:
        words = page.extract_words()
        if not words:
            continue
        ws = sorted(words, key=lambda w: w["top"])
        current = [ws[0]]
        current_top = ws[0]["top"]
        for w in ws[1:]:
            if abs(w["top"] - current_top) <= tol:
                current.append(w)
            else:
                out.append(current)
                current = [w]
                current_top = w["top"]
        out.append(current)
    return out


# ---------------------------------------------------------------------------
# Source resolution (PDF for a unit)
# ---------------------------------------------------------------------------
def resolve_source(unit_dir: Path, meta: dict) -> tuple[str, Path | None]:
    """("pages.pdf"|"source_pdf"|"unavailable", path|None)."""
    pages_pdf = unit_dir / "pages.pdf"
    if pages_pdf.exists():
        return "pages.pdf", pages_pdf

    document = meta.get("document")
    if not document:
        return "unavailable", None

    repo_root = Path(__file__).resolve().parents[3]  # findociq/
    sources_root = repo_root / "data" / "sources"

    # Local cache: search known family subfolders first (cheap, no network).
    for candidate in sources_root.glob(f"**/{document}"):
        if candidate.is_file():
            return "source_pdf", candidate

    # Not cached locally — try materializing from GCS under each known family
    # subfolder (financial_statements, pillar3). Bank/family-agnostic: we do
    # not special-case which subfolder a given bank lives under.
    try:
        sys.path.insert(0, str(repo_root / "pipeline"))
        from common import source_store  # noqa: PLC0415
    except Exception:
        return "unavailable", None

    for sub in ("financial_statements", "pillar3"):
        key = f"{sub}/{document}"
        try:
            path = source_store.materialize(key)
            if path.exists():
                return "source_pdf", path
        except Exception:
            continue
    return "unavailable", None


# ---------------------------------------------------------------------------
# Per-unit processing
# ---------------------------------------------------------------------------
def process_unit(unit_dir: Path) -> dict:
    """Compute + atomically write the geometry side-car for one audit unit.
    Returns stats: {"unit": str, "source": str, "tables_matched": int,
    "tables_total": int, "rows_matched": int, "rows_total": int}."""
    unit_dir = Path(unit_dir)
    parsed_path = unit_dir / "parsed.json"
    meta_path = unit_dir / "meta.json"
    stats = {
        "unit": unit_dir.name, "source": "unavailable",
        "tables_matched": 0, "tables_total": 0,
        "rows_matched": 0, "rows_total": 0,
    }
    if not parsed_path.exists():
        return stats

    parsed = json.loads(parsed_path.read_text())
    tables = parsed.get("tables") or []
    stats["tables_total"] = len(tables)
    stats["rows_total"] = sum(len(t.get("rows") or []) for t in tables)

    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    source, pdf_path = resolve_source(unit_dir, meta)
    stats["source"] = source

    if source == "unavailable" or pdf_path is None:
        geometry = {"source": "unavailable"}
    else:
        page_nums = meta.get("pages") or []
        with pdfplumber.open(str(pdf_path)) as pdf:
            if source == "pages.pdf":
                # pages.pdf is already cropped to exactly this unit's pages,
                # in order — use every page it contains.
                pdf_pages = list(pdf.pages)
            else:
                pdf_pages = [pdf.pages[p - 1] for p in page_nums if 1 <= p <= len(pdf.pages)]

            table_geoms = []
            for table in tables:
                if pdf_pages:
                    tg = compute_table_geometry(table, pdf_pages)
                else:
                    n_rows = len(table.get("rows") or [])
                    tg = {
                        "rows": [{"line_id": None, "indent": None, "label_clean": None}] * n_rows,
                        "title_clean": None, "col_labels_clean": [], "all_rows_matched": False,
                        "band_calibrated": False,
                    }
                table_geoms.append(tg)
                if tg["all_rows_matched"]:
                    stats["tables_matched"] += 1
                stats["rows_matched"] += sum(1 for r in tg["rows"] if r["line_id"] is not None)

        geometry = {"source": source, "tables": table_geoms}

    parsed["geometry"] = geometry
    _write_atomic(parsed_path, parsed)
    return stats


def _write_atomic(path: Path, data: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


# ---------------------------------------------------------------------------
# Backfill entrypoint
# ---------------------------------------------------------------------------
def find_units(audit_root: Path) -> list[Path]:
    """Every unit dir (has parsed.json) under `audit_root`, sorted for
    deterministic output."""
    return sorted(
        p.parent for p in Path(audit_root).rglob("parsed.json")
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-root", help="process every unit under this dir")
    ap.add_argument("--unit", help="process a single unit dir")
    args = ap.parse_args(argv)

    if not args.audit_root and not args.unit:
        ap.error("one of --audit-root or --unit is required")

    units = [Path(args.unit)] if args.unit else find_units(Path(args.audit_root))
    if not units:
        print("no units found")
        return 1

    tot_tables_matched = tot_tables = tot_rows_matched = tot_rows = 0
    for unit_dir in units:
        stats = process_unit(unit_dir)
        print(f"{stats['unit']}: source={stats['source']} "
              f"tables_matched {stats['tables_matched']}/{stats['tables_total']} "
              f"rows_matched {stats['rows_matched']}/{stats['rows_total']}")
        tot_tables_matched += stats["tables_matched"]
        tot_tables += stats["tables_total"]
        tot_rows_matched += stats["rows_matched"]
        tot_rows += stats["rows_total"]

    print(f"TOTAL: tables_matched {tot_tables_matched}/{tot_tables} "
          f"rows_matched {tot_rows_matched}/{tot_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
