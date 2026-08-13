"""docs_config — the spike's document registry + deterministic NSFR-section lookup.

Metadata only (paths, verbatim institution strings). All BEHAVIOR elsewhere is
dialect-general; nothing may branch on these keys.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))          # repo root
PDF_DIR = os.path.join(ROOT, "findociq", "data", "sources", "pillar3")
TOC_DIR = os.path.join(ROOT, "findociq", "_legacy", "DELIVERABLE", "outputs", "pillar3")

DPI = 200          # PNG render resolution; px -> pt scale is exactly 72/DPI
PT_PER_PX = 72.0 / DPI

DOCS = {
    "dbs_4q23_p3": dict(
        pdf=os.path.join(PDF_DIR, "DBS_4Q23_Pillar3.pdf"),
        toc=os.path.join(TOC_DIR, "dbs_4Q23", "toc.json"),
        institution="DBS Group Holdings Ltd",
        gt=os.path.join(ROOT, "GT_dbs_4q23_p3.csv"),
        render="ruled",
    ),
    "ocbc_4q24_p3": dict(
        pdf=os.path.join(PDF_DIR, "OCBC_4Q24_Pillar3.pdf"),
        toc=os.path.join(TOC_DIR, "ocbc_4Q24", "toc.json"),
        institution="Oversea-Chinese Banking Corporation Limited",
        gt=os.path.join(ROOT, "GT_ocbc_4q24_p3.csv"),
        render="borderless",
    ),
}

# Region-detection corpus (T4a) — registry-only third doc: no GT cells, no NSFR capture.
EXTRA_DOCS = {
    "uob_4q25_p3": dict(
        pdf=os.path.join(PDF_DIR, "UOB_4Q25_Pillar 3.pdf"),    # filename contains a space
        toc=os.path.join(TOC_DIR, "uob_4Q25", "toc.json"),
        institution="United Overseas Bank Limited",
        gt=None,
        render="ruled",
    ),
}
ALL_DOCS = {**DOCS, **EXTRA_DOCS}

TABLE_TYPE = "nsfr"
TABLE_TITLE = "NSFR Disclosure Template"   # MAS 653 template name (report-only identity)

_NSFR = re.compile(r"nsfr|net stable funding", re.I)


def nsfr_pages(toc_path: str) -> list[int]:
    """1-indexed NSFR-section pages from the printed-TOC output (never hardcoded).
    Deepest (most dotted section_id) match wins; residual ambiguity is a hard error."""
    toc = json.load(open(toc_path))
    hits = [s for s in toc["sections"] if _NSFR.search(s.get("title") or "")]
    if not hits:
        raise ValueError(f"no NSFR section found in {toc_path}")
    depth = lambda s: (s.get("section_id") or "").count(".")
    best = max(depth(s) for s in hits)
    finals = [s for s in hits if depth(s) == best]
    if len(finals) != 1:
        raise ValueError(f"ambiguous NSFR sections in {toc_path}: "
                         f"{[s['section_id'] for s in finals]}")
    s = finals[0]
    return list(range(int(s["start_page"]), int(s["end_page"]) + 1))


def section_pages(toc_path: str, section_id: str) -> list[int]:
    """1-indexed pages of a printed-TOC section, selected by EXACT section_id
    (T4a uses '12.9'). Same deterministic mechanism as nsfr_pages, keyed lookup."""
    toc = json.load(open(toc_path))
    hits = [s for s in toc["sections"] if s.get("section_id") == section_id]
    if len(hits) != 1:
        raise ValueError(f"section_id {section_id!r}: {len(hits)} matches in {toc_path}")
    return list(range(int(hits[0]["start_page"]), int(hits[0]["end_page"]) + 1))
