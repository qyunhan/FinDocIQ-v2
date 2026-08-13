"""toc_match.py — Stage 2 (TOC branch) of section->table tagging.

See findociq/docs/specs/2026-07-09-section-table-tagging-design.md.

Paddle proposes, the arranger disposes. `candidates.py` emits EVERY
header-looking block per page (never deciding which are real). This module is
the deterministic arranger for the printed-TOC branch: it keeps only the
candidates whose text matches a printed-TOC section title, and attributes each
detected table region to the nearest valid (matched) section boundary above it
in reading order, carrying the section across pages that have no boundary.

The whole point of the TOC-membership test is robustness to the exact failure
this branch fixes: PP-DocLayout sometimes labels a bold left-margin DATE line
(OCBC p92 "For the Quarter ended 31 December 2024") as a paragraph_title,
typographically identical to a real header. Geometry and font cannot reject it;
the printed-TOC section list can. is_dateish is IGNORED here on purpose — the
TOC-membership test is the sole decider (a date line that somehow matched a real
title would still be a real section; a header that isn't in the TOC is noise).

No LLM tokens, no per-doc conditionals, base python only (reuses stdlib difflib).

Public entry point:
    attribute_from_toc(tag, toc_json_path, out_root=<default>)
        -> writes <out_root>/<tag>/section_tags.csv
           columns: page,table_idx,section_id,section_title,source
           source is always the literal string "printed_toc".
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import assign_tables  # noqa: E402  (the ONE shared deterministic assigner)

_DEFAULT_OUT = os.path.join(
    HERE, "..", "..", "..", "experiments", "2026-07-07_paddleocr_eval", "outputs")

# --- matching thresholds / knobs (documented in the report) ---
MATCH_RATIO = 0.9          # difflib.SequenceMatcher ratio floor for a fuzzy title match
PREAMBLE = "PREAMBLE"      # section for regions with no valid boundary before them

# Leading section numbering: "5", "5.1", "5.1.", "18.7 " etc. at the start.
_LEAD_NUM = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")
# Trailing "(continued)" (any case, optional surrounding space).
_CONT = re.compile(r"\s*\(\s*continued\s*\)\s*$", re.I)


def normalize_title(s: str) -> str:
    """casefold, collapse whitespace, strip leading section numbering, strip a
    trailing "(continued)". Applied identically to candidate text and printed-TOC
    titles so the comparison is symmetric."""
    s = (s or "").casefold()
    s = re.sub(r"\s+", " ", s).strip()
    s = _LEAD_NUM.sub("", s)
    s = _CONT.sub("", s).strip()
    return s


def _ends_continued(text: str) -> bool:
    """True iff the candidate's raw text (casefolded, whitespace-collapsed) ends
    with a "(continued)" marker. normalize_title() strips this, so it is tested
    on the collapsed-but-unstripped text to flag continuation boundaries."""
    s = (text or "").casefold()
    s = re.sub(r"\s+", " ", s).strip()
    return bool(_CONT.search(s))


def deglue(s: str) -> str:
    """Insert spaces at case/digit boundaries so an adaptive-word-rebuild glued
    token ("DisclosureofKey", "Metrics2024") can still match. Best-effort extra
    variant; the fuzzy ratio already tolerates a single missing space, this
    recovers the harder camel/digit fusions."""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", s)
    s = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", s)
    return s


def _candidate_variants(text: str) -> set:
    """Normalized forms of a candidate to try against each section title: the
    plain normalization and the de-glued normalization."""
    variants = {normalize_title(text), normalize_title(deglue(text))}
    return {v for v in variants if v}


# Leading printed section number, tolerating the GLUED print (char-joined line
# text: "19.4SecuritisationExposures..." has no space after the number). Glued
# capture requires the number be dotted (or carry a trailing dot) and be
# followed by an uppercase letter — so "31December2025" never captures "31".
_LEAD_NUM_CAPTURE = re.compile(
    r"^\s*(\d+(?:\.\d+)+|\d+)\.?\s+\S"          # spaced form
    r"|^\s*(\d+(?:\.\d+)+)\.?(?=[A-Z])"         # glued form (dotted numbers only)
    r"|^\s*(\d+)\.(?=[A-Z])")                   # glued top-level with dot: "2.Material"


def match_candidate(text: str, sections: list):
    """Return (section_id, section_title) of the printed-TOC section this
    candidate matches, or None.

    NUMBER-ONLY MATCHING (user decision 2026-07-09, replacing fuzzy-title
    matching after the 7.2 miss — a WRAPPED long heading loses its middle line
    in the candidate text and lands just under any fuzzy threshold):
      * the candidate's printed leading section number ("7.2 ...") equals a
        printed-TOC section_id -> match. Banks print the number verbatim; it is
        exact and immune to wrapped/glued titles AND to near-identical sibling
        titles (19.3 vs 19.4). The matched section takes the PRINTED TOC title.
      * NO fuzzy fallback: documents whose headings are unnumbered ride the
        Gemini branch anyway (pick_branch), so a title-similarity guess here
        only adds a silent-misroute risk for zero coverage gain.
    A None here does NOT discard the candidate — unmatched headings are kept as
    SUB-HEADINGS by the caller (recorded to subheadings.csv, attached to units
    as table-title context; never section boundaries).

    `sections` is a list of (section_id, raw_title, normalized_title).
    """
    m = _LEAD_NUM_CAPTURE.match(text or "")
    if not m:
        return None
    num = next(g for g in m.groups() if g)
    for sid, raw_title, _norm in sections:
        if sid == num:
            return (sid, raw_title)
    # numbered, but finer than the TOC ("2.13.6.1") or not a TOC id -> sub-heading
    return None


def _load_sections(toc_json_path: str) -> list:
    with open(toc_json_path) as fh:
        toc = json.load(fh)
    out = []
    for sec in toc.get("sections", []):
        sid = str(sec.get("section_id", "")).strip()
        title = str(sec.get("title", "")).strip()
        if not sid or not title:
            continue
        out.append((sid, title, normalize_title(title)))
    return out


def _read_candidates(path: str) -> list:
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append(dict(
                page=int(r["page"]),
                y0=float(r["y0"]),
                text=r.get("text", "") or "",
                is_dateish=(r.get("is_dateish", "") or "").strip().lower()
                           in ("true", "1")))
    return rows


def _read_regions(path: str) -> list:
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append(dict(
                page=int(r["page"]),
                table_idx=int(r["table_idx"]),
                y0=float(r["y0"])))
    return rows


def build_boundaries(candidates: list, sections: list) -> list:
    """Turn matched candidate instances into the shared `boundaries` contract
    (see assign_tables.py). A candidate is a VALID boundary iff its text matches
    a printed-TOC section title (fuzzy/de-glue, deepest-id-wins).

    For each valid instance, in reading order (page, y0):
      * level    = assign_tables.leaf_level(section_id) (dots+1 for numbered ids)
      * continued= the raw text carries a trailing "(continued)" marker, OR this
        section_id already appeared at an earlier (page, y0) (a repeat instance,
        i.e. the section spilling onto/further down the document).

    The continuation flag is what lets the shared assigner keep a page-top parent
    "(continued)" banner from stealing a table from a subsection that is still
    continuing on that page.
    """
    matched = []  # (page, y0, section_id, section_title, raw_text)
    for c in candidates:
        # DATE GUARD (user hard rule: a date/period line is NEVER a section):
        # "25 March 2026" would otherwise number-capture "25" and steal TOC id
        # 25. Dateish candidates are kept as sub-headings, never boundaries.
        m = None if c.get("is_dateish") else match_candidate(c["text"], sections)
        if m is not None:
            matched.append((c["page"], c["y0"], m[0], m[1], c["text"]))
        else:
            # KEEP, never discard (user rule): an unmatched heading is a real
            # sub-heading (a table's printed title, finer than the TOC) — it is
            # recorded for the extraction units but must not steal the cursor.
            c["_subheading"] = True

    # Reading order so "repeat instance" is judged against EARLIER (page, y0).
    matched.sort(key=lambda t: (t[0], t[1]))

    seen = set()
    boundaries = []
    for page, y0, sid, title, raw in matched:
        continued = _ends_continued(raw) or (sid in seen)
        seen.add(sid)
        boundaries.append(dict(
            section_id=sid,
            section_title=title,
            level=assign_tables.leaf_level(sid),
            page=page,
            y0=y0,
            continued=continued,
        ))
    return boundaries


def attribute(candidates: list, regions: list, sections: list) -> list:
    """Core arranger, pure function over already-parsed rows (kept separate from
    file IO so the test can drive it directly).

    Returns one dict per region: {page, table_idx, section_id, section_title}.

    Now a thin two-step: validate headings into the `boundaries` contract
    (build_boundaries) then hand off to the ONE shared deterministic assigner
    (assign_tables.assign). All positional/continuation logic lives there.
    """
    boundaries = build_boundaries(candidates, sections)
    return assign_tables.assign(boundaries, regions)


def attribute_from_toc(tag: str, toc_json_path: str,
                       out_root: str = _DEFAULT_OUT) -> str:
    """Public entry point. Reads candidates.csv + regions.csv under
    <out_root>/<tag>/ and the printed-TOC json, writes section_tags.csv, returns
    its path."""
    out_dir = os.path.join(out_root, tag)
    cand_path = os.path.join(out_dir, "candidates.csv")
    regions_path = os.path.join(out_dir, "regions.csv")

    sections = _load_sections(toc_json_path)
    candidates = _read_candidates(cand_path)
    regions = _read_regions(regions_path)

    rows = attribute(candidates, regions, sections)

    out_path = os.path.join(out_dir, "section_tags.csv")
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "page", "table_idx", "section_id", "section_title", "source"])
        w.writeheader()
        for r in rows:
            w.writerow(dict(r, source="printed_toc"))

    # KEEP-ALL (user rule 2026-07-09): every detected heading that did not match
    # a TOC section id is recorded as a sub-heading — the printed table titles
    # the extraction units hand to Gemini. Nothing detected is ever dropped.
    sub_path = os.path.join(out_dir, "subheadings.csv")
    subs = [c for c in candidates if c.get("_subheading")]
    with open(sub_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["page", "y0", "text"])
        w.writeheader()
        for c in sorted(subs, key=lambda c: (c["page"], c["y0"])):
            w.writerow(dict(page=c["page"], y0=c["y0"], text=c["text"]))
    print(f"[{tag}] {len(rows)} region(s) tagged -> {out_path}")
    print(f"[{tag}] {len(subs)} sub-heading(s) kept -> {sub_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Stage 2 TOC branch: attribute table regions to printed-TOC "
                    "sections (base python, no paddle).")
    ap.add_argument("tag")
    ap.add_argument("toc_json_path")
    ap.add_argument("--out", default=_DEFAULT_OUT, help="output root")
    args = ap.parse_args()
    attribute_from_toc(args.tag, args.toc_json_path, args.out)


if __name__ == "__main__":
    main()
