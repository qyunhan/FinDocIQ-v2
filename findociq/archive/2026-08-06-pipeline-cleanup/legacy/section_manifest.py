"""section_manifest.py — Stage 4 of section->table tagging (see
findociq/docs/specs/2026-07-09-section-table-tagging-design.md).

Joins the shared substrate (regions.csv, one row per detected table region)
with whichever arranger ran (toc_match.py for the printed-TOC branch,
gemini_arrange.py for the no-TOC branch — both write section_tags.csv in the
same pinned shape) into ONE downstream extraction contract:
`section_manifest.csv`, plus a human-visible `section_map.html` so the
pivot (which branch fired, which sections it produced) is observable
without reading code (CLAUDE.md manifest-visibility requirement).

INPUTS (pinned, under <out_root>/<tag>/):
  regions.csv       page,table_idx,x0,y0,x1,y1
  section_tags.csv  page,table_idx,section_id,section_title,source
                     (source = printed_toc | gemini)

OUTPUT (the downstream extraction contract):
  section_manifest.csv
    doc_id,page,section_id,section_title,table_idx,bbox,template_type,prompt,source
  section_map.html   human-visible per-section rollup (id, title, page range,
                      table count, source)

template_type: matched against the existing template registry — REUSES
findociq/pipeline/route/scan.py's `best_template_match` (same
_TemplateRegistry backed by findociq/db/final.db's template_col; no new
matcher, no per-doc conditionals). Note: scan.py's matcher was built to run
over a page's FULL text (title keyword + column-header signature); here it
only sees `section_title` (the pinned section_tags.csv schema carries no
body text), so it will only fire when the title itself carries a strong
column-header hit — expect this to be blank far more often than scan.py's
own page-level call. Documented limitation, not patched around here (that
would be scope creep on a shared matcher).

prompt: there is exactly ONE Stage-2 HTML-output contract file today,
prompts/stage2_core.txt (see extract_run.py: template-specific rows/cols are
injected at RUNTIME from the DB template as the "KNOWN-TABLE MODIFIER" in
stage2_framings.txt, not authored as separate per-type prompt files). So
every registry-known template_type routes to the same file; unknown/blank
template_type -> blank prompt. If/when a table_type gets a genuinely
different prompt file, add it to TEMPLATE_PROMPT here (pipeline change, per
CLAUDE.md) rather than hand-tuning a call downstream.

Usage:
  python3 section_manifest.py <tag> <doc_id> [--out <out_root>]
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTE_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "route"))
if ROUTE_DIR not in sys.path:
    sys.path.insert(0, ROUTE_DIR)
import scan  # noqa: E402  (best_template_match / template_match / _TemplateRegistry)

_DEFAULT_OUT = os.path.normpath(os.path.join(
    HERE, "..", "..", "..", "experiments", "2026-07-07_paddleocr_eval", "outputs"))

MANIFEST_COLS = ["doc_id", "page", "section_id", "section_title", "table_idx",
                  "bbox", "unit_id", "unit_n_tables", "page_shared", "framing",
                  "template_type", "prompt", "source"]

# Stage-2 framing lead-ins (prepended to stage2_core.txt); names match
# prompts/stage2_framings.txt. Chosen deterministically from cardinality — the
# section, page, and per-table bbox in this manifest fully determine the route,
# so no LLM is needed to pick it.
#   SINGLE   — every table on the page belongs to this one section: extract all.
#   MULTIPLE — page is SHARED by >1 section: scope each table to its section by
#              bbox (the "other tables not belonging to that header" case).
# SPANNING (a table continuing across pages) needs the continuation stitch (not
# yet wired) and is therefore not emitted here — flagged as a follow-up.
_CORE_PROMPT = "stage2_core.txt"

# template_type (findociq/db/final.db template_col) -> routed Stage-2 prompt
# file, relative to findociq/pipeline/prompts/. See module docstring: one
# HTML-output contract file today, so every known type maps to it.
TEMPLATE_PROMPT = {
    "nsfr": "stage2_core.txt",
    "km1": "stage2_core.txt",
    "lcr": "stage2_core.txt",
}


def _read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _template_type_for(section_title: str) -> str:
    """Best-effort template match against the section title alone (see
    module docstring caveat). Blank if no match, per spec."""
    title = (section_title or "").strip()
    if not title:
        return ""
    m = scan.best_template_match(title)
    return m["table_type"] if m else ""


def build_manifest(tag: str, doc_id: str, out_root: str = _DEFAULT_OUT) -> str:
    tag_dir = os.path.join(out_root, tag)
    regions_path = os.path.join(tag_dir, "regions.csv")
    tags_path = os.path.join(tag_dir, "section_tags.csv")

    regions = _read_csv(regions_path)
    tags = _read_csv(tags_path)
    tag_by_key = {(t["page"], t["table_idx"]): t for t in tags}

    # --- cardinality for prompt routing (deterministic; no LLM) ---
    # unit = one Stage-2 call = (section_id, page): a section's tables on a page.
    from collections import defaultdict
    unit_count: dict[tuple, int] = defaultdict(int)   # (section, page) -> n tables
    page_sections: dict[str, set] = defaultdict(set)  # page -> distinct sections
    for r in regions:
        t = tag_by_key.get((r["page"], r["table_idx"]))
        sid = (t["section_id"].strip() if t else "") or "UNASSIGNED"
        unit_count[(sid, r["page"])] += 1
        page_sections[r["page"]].add(sid)

    rows = []
    for r in regions:
        key = (r["page"], r["table_idx"])
        t = tag_by_key.get(key)
        section_id = t["section_id"].strip() if t else ""
        section_title = t["section_title"].strip() if t else ""
        source = t["source"].strip() if t else ""
        template_type = _template_type_for(section_title)
        bbox = "[{},{},{},{}]".format(r["x0"], r["y0"], r["x1"], r["y1"])

        sid = section_id or "UNASSIGNED"
        k = unit_count[(sid, r["page"])]                       # this section's tables here
        shared = len(page_sections[r["page"]]) > 1             # other sections on the page?
        framing = "MULTIPLE" if shared else "SINGLE"
        unit_id = "{}@p{}".format(sid, r["page"])
        # which prompt to call: base contract + framing lead-in (+ known-table
        # modifier when the section matches a registered template).
        prompt = "{} + {}".format(_CORE_PROMPT, framing)
        if k > 1:
            prompt += " (x{} tables, this section only)".format(k)
        if template_type:
            prompt += " + KNOWN[{}]".format(template_type)

        rows.append(dict(
            doc_id=doc_id, page=r["page"], section_id=section_id,
            section_title=section_title, table_idx=r["table_idx"], bbox=bbox,
            unit_id=unit_id, unit_n_tables=k, page_shared=int(shared),
            framing=framing, template_type=template_type, prompt=prompt,
            source=source,
        ))

    os.makedirs(tag_dir, exist_ok=True)
    manifest_path = os.path.join(tag_dir, "section_manifest.csv")
    with open(manifest_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLS)
        w.writeheader()
        w.writerows(rows)

    _write_section_map(tag_dir, doc_id, rows)
    return manifest_path


def _write_section_map(tag_dir: str, doc_id: str, rows: list[dict]) -> str:
    """Human-visible per-section rollup (id, title, page range, table count,
    source) — the manifest-visibility half of the pivot, mirroring the
    lightweight-table style of scan.py's render_html without reproducing its
    routing-mindmap machinery (not needed here)."""
    groups: dict[str, dict] = {}
    order: list[str] = []
    unassigned = 0
    for r in rows:
        sid = r["section_id"]
        if not sid:
            unassigned += 1
            continue
        if sid not in groups:
            groups[sid] = dict(title=r["section_title"], min_page=int(r["page"]),
                                max_page=int(r["page"]), n_tables=0, sources=set())
            order.append(sid)
        g = groups[sid]
        pg = int(r["page"])
        g["min_page"] = min(g["min_page"], pg)
        g["max_page"] = max(g["max_page"], pg)
        g["n_tables"] += 1
        if r["source"]:
            g["sources"].add(r["source"])

    # section_map.csv — the leaf-section rollup (one row per table-bearing leaf
    # section, tables already attached to their deepest section in the manifest):
    # section_id, section_title, start_page, end_page (span of its tables), n_tables.
    SECTION_MAP_COLS = ["doc_id", "section_id", "section_title",
                        "start_page", "end_page", "n_tables", "source"]
    csv_path = os.path.join(tag_dir, "section_map.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SECTION_MAP_COLS)
        w.writeheader()
        for sid in order:
            g = groups[sid]
            w.writerow(dict(doc_id=doc_id, section_id=sid, section_title=g["title"],
                            start_page=g["min_page"], end_page=g["max_page"],
                            n_tables=g["n_tables"],
                            source=", ".join(sorted(g["sources"])) or ""))

    trs = []
    for sid in order:
        g = groups[sid]
        pages = f'{g["min_page"]}' if g["min_page"] == g["max_page"] else f'{g["min_page"]}-{g["max_page"]}'
        src = ", ".join(sorted(g["sources"])) or "-"
        trs.append(
            "<tr><td>{sid}</td><td>{title}</td><td>{pages}</td>"
            "<td>{n}</td><td>{src}</td></tr>".format(
                sid=html.escape(sid), title=html.escape(g["title"]),
                pages=pages, n=g["n_tables"], src=html.escape(src)))

    body = "\n".join(trs) if trs else '<tr><td colspan="5">(no tagged sections)</td></tr>'
    doc = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>section_map — {html.escape(doc_id)}</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 13px; }}
th {{ background: #f0f0f0; }}
caption {{ text-align: left; font-size: 12px; color: #666; margin-bottom: 6px; }}
</style></head>
<body>
<h2>section_map — {html.escape(doc_id)}</h2>
<table>
<caption>{len(order)} section(s); {unassigned} region(s) unassigned (no section_tags match)</caption>
<thead><tr><th>section_id</th><th>section_title</th><th>pages</th>
<th>table_count</th><th>source</th></tr></thead>
<tbody>
{body}
</tbody>
</table>
</body></html>
"""
    out_path = os.path.join(tag_dir, "section_map.html")
    with open(out_path, "w") as fh:
        fh.write(doc)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("doc_id")
    ap.add_argument("--out", default=_DEFAULT_OUT)
    args = ap.parse_args()
    path = build_manifest(args.tag, args.doc_id, args.out)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
