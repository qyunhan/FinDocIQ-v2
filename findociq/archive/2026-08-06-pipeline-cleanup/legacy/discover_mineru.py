"""discover_mineru — Stage-1 discovery via MinerU (zero LLM tokens).

Builds the document's TOC tree the way the layout overlay shows it:
  - section headings (the "purple" boxes)  -> the section tree (PART + dotted numbering)
  - tables (the "yellow" boxes)            -> assigned to their enclosing section, with pages
  - cross-page continuations               -> merged into page ranges

Table *naming* is deferred to Gemini at extraction; here each table carries the section it
sits under + its PDF page(s). Works on Pillar 3 AND no-TOC financial statements.

Usage:
    # uses an existing MinerU run if present, else runs MinerU into <out>:
    python3 discover_mineru.py <pdf> [--out DIR] [--mineru .venv-mineru/bin/mineru]
Output: <out>/<doc>_toc.json  + a readable tree to stdout.
"""
from __future__ import annotations
import os, sys, re, json, subprocess, argparse

# A real section heading: "PART A ...", or a leading dotted number "5", "5.1", "12.2.6".
_PART = re.compile(r"^PART\s+([A-Z])\b", re.I)
_NUM  = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*)$")
_CONT = re.compile(r"\(cont(?:inued)?\.?\)", re.I)


def run_mineru(pdf: str, out: str, mineru_bin: str) -> str:
    """Return path to content_list.json, running MinerU only if not already present."""
    stem = os.path.splitext(os.path.basename(pdf))[0]
    for root, _, files in os.walk(out):
        for f in files:
            if f.endswith("content_list.json") and stem in root:
                return os.path.join(root, f)
    os.makedirs(out, exist_ok=True)
    subprocess.run([mineru_bin, "-p", pdf, "-o", out, "-b", "pipeline", "-m", "auto"],
                   check=True)
    for root, _, files in os.walk(out):
        for f in files:
            if f.endswith("content_list.json"):
                return os.path.join(root, f)
    raise FileNotFoundError("MinerU produced no content_list.json")


def build_tree(blocks: list) -> dict:
    part = None
    sections: list[dict] = []          # ordered; section_id encodes hierarchy via dots
    cur: dict | None = None

    def is_pageheader(t: str) -> bool:  # recurring bank/page-header lines, not sections
        return bool(re.search(r"GROUP HOLDINGS|SUBSIDIARIES|PILLAR 3 DISCLOSURES?$", t, re.I)) \
               and not _NUM.match(t)

    for b in blocks:
        typ = b.get("type")
        page = b.get("page_idx", 0) + 1
        if typ == "text" and b.get("text_level"):
            t = (b.get("text") or "").strip()
            mp = _PART.match(t)
            if mp:
                part = mp.group(1).upper()
                continue
            mn = _NUM.match(t)
            if not mn or is_pageheader(t):
                continue
            num, title = mn.group(1), mn.group(2).strip()
            sid = f"{part}.{num}" if part else num
            cont = bool(_CONT.search(t))
            if cont and cur and cur["section_id"] == sid:
                continue                # same section continuing — keep collecting its tables
            cur = {"section_id": sid, "title": _CONT.sub("", title).strip(),
                   "page": page, "tables": []}
            sections.append(cur)
        elif typ == "table":
            if cur is None:
                cur = {"section_id": "(front matter)", "title": "", "page": page, "tables": []}
                sections.append(cur)
            cur["tables"].append(page)

    # collapse each section's table pages into logical tables with page ranges
    out_secs = []
    for s in sections:
        pages = s["tables"]
        ranges = []
        for p in pages:
            if ranges and p - ranges[-1][1] <= 1 and p >= ranges[-1][0]:
                ranges[-1][1] = max(ranges[-1][1], p)   # contiguous -> extend (stitch continuation)
            else:
                ranges.append([p, p])
        out_secs.append({**{k: s[k] for k in ("section_id", "title", "page")},
                         "n_table_blocks": len(pages),
                         "tables": [{"pages": [a, b] if b > a else [a]} for a, b in ranges]})
    return {"sections": out_secs,
            "n_sections": sum(1 for s in out_secs if s["section_id"] != "(front matter)"),
            "n_tables": sum(len(s["tables"]) for s in out_secs)}


def discover(pdf: str, out: str, mineru_bin: str) -> dict:
    cl = run_mineru(pdf, out, mineru_bin)
    blocks = json.load(open(cl))
    tree = build_tree(blocks)
    tree["document"] = os.path.basename(pdf)
    dest = os.path.join(out, os.path.splitext(os.path.basename(pdf))[0] + "_toc.json")
    json.dump(tree, open(dest, "w"), indent=2)
    tree["_out"] = dest
    return tree


def render(tree: dict) -> None:
    print(f"\n{tree['document']}  —  {tree['n_sections']} sections, {tree['n_tables']} tables")
    print(f"(saved: {tree['_out']})\n")
    for s in tree["sections"]:
        depth = s["section_id"].count(".") if s["section_id"][0:1].isalpha() else s["section_id"].count(".")
        indent = "  " * max(0, depth)
        tbls = " ".join("p" + ("-".join(map(str, t["pages"]))) for t in s["tables"]) or "—"
        print(f"  {indent}{s['section_id']:10} p{s['page']:<3} {s['title'][:46]:46} tables: {tbls}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default="findociq/data/discovery")
    ap.add_argument("--mineru", default=".venv-mineru/bin/mineru")
    a = ap.parse_args()
    render(discover(a.pdf, a.out, a.mineru))
