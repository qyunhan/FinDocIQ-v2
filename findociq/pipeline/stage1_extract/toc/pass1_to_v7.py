"""pass1_to_v7.py — adapt a deterministic Pillar 3 TOC (discover/pass1_toc.py)
into the schema_v7 section shape that toc_to_db.py loads.

Pillar 3 routes to the PROVEN pass1_toc framework (zero-API, deterministic), but
its TOC JSON differs from the FS toc_stage JSON. This converter maps it into the
one FS-shaped contract so BOTH families load into the SAME schema_v7
compiled_fs.db via the unchanged toc_to_db.py. Deterministic, no API.

pass1_toc section:  {section_id:'A.5.1', part:'A', number:'5.1', title,
                     page_ref, start_page, end_page}
schema_v7 section:  {id, section_no, title, level, parent_id, path, seq,
                     page_start, page_end, has_tables}

Derivations (general — the dotted section_id IS the hierarchy, no bank literal):
  level      = depth of `number` (dots + 1): '2'->1, '5.1'->2, '12.1.1'->3.
  parent_id  = the LONGEST existing section_id that is a strict dotted prefix of
               this section_id (A.5.1 -> A.5 if present, else A, else None). Never
               invents a parent; a missing intermediate resolves to the nearest
               real ancestor or None — so the section self-FK always holds.
  path       = section_id (already the dotted ancestor chain).
  section_no = printed `number`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parent_id(section_id: str, ids: set[str]) -> str | None:
    parts = section_id.split(".")
    for k in range(len(parts) - 1, 0, -1):
        cand = ".".join(parts[:k])
        if cand in ids:
            return cand
    return None


def adapt(pass1: dict, doc_id: str, source_rel: str) -> dict:
    """pass1_toc dict -> FS-shaped {document, sections} for toc_to_db."""
    secs_in = pass1.get("sections", [])
    ids = {s["section_id"] for s in secs_in}
    out = []
    for seq, s in enumerate(secs_in, start=1):
        sid = s["section_id"]
        number = str(s.get("number") or "")
        start = int(s["start_page"])
        end = int(s.get("end_page") or start)
        out.append({
            "id": sid,
            "title": s["title"],
            "page_start": start,
            "page_end": max(end, start),
            "level": (number.count(".") + 1) if number else 1,
            "parent_id": _parent_id(sid, ids),
            "path": sid,
            "seq": seq,
            "section_no": number or sid,
            # region-attributed has_tables is a STEP-0/2 refinement for Pillar 3;
            # until then every TOC section is an extraction candidate (a prose
            # section simply yields no tables). toc_to_db does not store this.
            "has_tables": True,
        })
    doc = {
        "doc_id": doc_id,
        "source_pdf": source_rel,
        "doc_family": "pillar3",
    }
    return {"document": doc, "sections": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pass1", required=True, help="pass1_toc.py output JSON")
    ap.add_argument("--doc-id", required=True)
    ap.add_argument("--source-rel", required=True, help="repo-relative source pdf")
    ap.add_argument("--out", required=True, help="write FS-shaped toc JSON here")
    args = ap.parse_args(argv)

    pass1 = json.loads(Path(args.pass1).read_text())
    fs = adapt(pass1, args.doc_id, args.source_rel)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(fs, indent=2))
    print(f"pass1_to_v7: {len(fs['sections'])} sections -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
