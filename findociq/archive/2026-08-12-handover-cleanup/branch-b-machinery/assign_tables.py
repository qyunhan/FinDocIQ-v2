"""assign_tables.py — the ONE deterministic table->section assigner.

See findociq/docs/specs/2026-07-09-section-table-tagging-design.md, amendment
"2026-07-09 PM — two-step arranger" (binding). BOTH branches (printed-TOC
matcher and Gemini structure arranger) validate headings into the shared
`boundaries` contract, then hand off to this module. LLMs never do positional
assignment; this code does.

Contract
--------
boundaries: [{section_id:str, section_title:str, level:int, page:int, y0:float,
              continued:bool}]  (each a validated heading INSTANCE, in any order)
regions:    [{page:int|str, table_idx:int|str, x0,y0,x1,y1}]  (regions.csv rows ok)
returns:    one dict per region, in the INPUT region order:
            {page:int, table_idx:int, section_id:str, section_title:str}

Algorithm (deepest-heading-above, expressed as a reading-order sweep)
--------------------------------------------------------------------
Interleave boundaries and regions and sweep in reading order:
  * page ascending, then y0 ascending;
  * at equal (page, y0) a BOUNDARY sorts BEFORE a region (a heading at the same
    y as its table governs it);
  * at equal (page, y0) between two boundaries, the SHALLOWER level sorts first
    so the DEEPER level is crossed last and wins the cursor (a subsection
    heading printed level with its parent still governs its own table).

A cursor = (section_id, section_title) tracks the last boundary crossed. Each
region is assigned the cursor's section. Because subsection headings follow
their parents in reading order, the cursor is exactly the deepest heading above
the region — no explicit tree walk needed.

Cursor update rules
-------------------
  * A NON-continued boundary ALWAYS sets the cursor.
  * A `continued` boundary whose section_id is an ANCESTOR of the cursor's
    section_id does NOT update the cursor. (A page-top
    "2. Material Accounting Policy Information (continued)" banner must not steal
    the cursor from "2.21.3", which is still continuing onto that page.)
    ancestor := cursor_id == boundary_id  OR  cursor_id startswith boundary_id+"."
  * A `continued` boundary whose section is NOT an ancestor of the cursor
    (sibling / unrelated, e.g. "2.11.3 Measurement (continued)" while the cursor
    is "2.10") DOES set the cursor — the subsection genuinely continues there.

Regions reached before any boundary -> section "PREAMBLE"/"PREAMBLE". The cursor
persists across pages with no boundary (continuation carry-over).

Base python, stdlib only. No per-doc conditionals. Fail-loud on bad input.
"""
from __future__ import annotations

import re

PREAMBLE = "PREAMBLE"

# A section_id "looks numbered" if it starts with an optional single letter,
# optional dot, then a digit: "2", "2.8", "A.1", "18.7" -> numbered.
_NUMBERED = re.compile(r"^[A-Za-z]?\.?\d")


def leaf_level(section_id: str) -> int:
    """Depth of a section_id: dots+1 for numbered ids ("2"->1, "2.8"->2,
    "2.21.3"->3), else 1. Used by the branch validators to fill boundaries'
    `level` and by downstream validators."""
    sid = (section_id or "").strip()
    if _NUMBERED.match(sid):
        return sid.count(".") + 1
    return 1


def _is_ancestor(boundary_id: str, cursor_id: str) -> bool:
    """True iff boundary_id is an ancestor of (or equal to) cursor_id.

    Equality counts (a node is treated as its own ancestor for the
    continuation rule). The trailing dot prevents a spurious prefix hit
    ("2" is NOT an ancestor of "20.1")."""
    if cursor_id is None:
        return False
    return cursor_id == boundary_id or cursor_id.startswith(boundary_id + ".")


def assign(boundaries: list, regions: list) -> list:
    """Attribute each region to a section via the reading-order sweep. See the
    module docstring for the full contract and rules."""
    events = []  # (page, y0, rank, level, kind, payload)
    for b in boundaries:
        page = int(b["page"])
        y0 = float(b["y0"])
        level = int(b["level"])
        events.append((page, y0, 0, level, "boundary", b))
    for i, r in enumerate(regions):
        page = int(r["page"])
        y0 = float(r["y0"])
        # rank 1 => every boundary at the same (page, y0) is crossed first.
        # level component is irrelevant for regions (rank already separates).
        events.append((page, y0, 1, 0, "region", i))

    events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    cur_id, cur_title = None, None
    out = [None] * len(regions)
    for _page, _y0, _rank, _level, kind, payload in events:
        if kind == "boundary":
            b = payload
            bid = str(b["section_id"])
            if b.get("continued") and _is_ancestor(bid, cur_id):
                # page-top parent "(continued)" banner: do NOT downgrade cursor.
                continue
            cur_id, cur_title = bid, b["section_title"]
        else:
            i = payload
            r = regions[i]
            if cur_id is None:
                sid, title = PREAMBLE, PREAMBLE
            else:
                sid, title = cur_id, cur_title
            out[i] = dict(
                page=int(r["page"]),
                table_idx=int(r["table_idx"]),
                section_id=sid,
                section_title=title,
            )
    return out
