"""discover — Stage-1 deterministic discovery: MinerU -> canonical TOC -> schema_v5 DB.

Pipeline (zero LLM tokens):
  MinerU content_list.json (one block schema for every doc type)
    -> NORMALIZE   sections (numbered A.12.2.6 OR unnumbered statements) + tables
                   (each table -> its section; contiguous pages merged into one unit)
    -> VERIFY      page-coverage accounting + counts (+ optional PASS1_TOC cross-check)
    -> WRITE DB    document -> section (the TOC tree) -> table_t (titles + page ranges)

The table_t rows are the manifest the extractor consumes later (resumable, see whole doc).

Usage:
    python3 discover.py <pdf> --db findociq/db/discovery.db [--out MINERU_DIR]
"""
from __future__ import annotations
import os, re, sys, json, sqlite3, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from discover_mineru import run_mineru                       # reuse the MinerU runner

SCHEMA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "schema", "schema_v5.sql"))

_PART = re.compile(r"^PART\s+([A-Z])\b", re.I)
_NUM  = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*)$")            # leading dotted number + title
_CONT = re.compile(r"\(cont(?:inued)?\.?\)", re.I)
_PAGEHDR = re.compile(r"GROUP HOLDINGS|SUBSIDIARIES|PILLAR 3 DISCLOSURES?\s*$", re.I)
_FOOTNOTEHDR = re.compile(r"^#|under\s+\$?\s*\d|^\(\d+\)")  # footnote-ish, not a real section

# provisional table_type from title keywords (Gemini confirms later)
_TYPES = [("nsfr", r"nsfr|net stable funding"), ("lcr", r"\blcr\b|liquidity coverage"),
          ("leverage", r"leverage ratio"), ("km1", r"key prudential|key metrics"),
          ("sa_cr", r"sa\(cr\)"), ("irba", r"\birba\b|internal ratings"),
          ("ccr", r"counterparty|\bccr\b"), ("securitisation", r"securitisation"),
          ("market_risk", r"market risk"), ("cva", r"credit valuation|\bcva\b"),
          ("rwa", r"risk-weighted assets|\brwa\b"), ("income_stmt", r"income statement"),
          ("balance_sheet", r"balance sheet"), ("cash_flow", r"cash flow"),
          ("equity", r"changes in equity")]
def classify(title: str) -> str:
    t = title.lower()
    for typ, rx in _TYPES:
        if re.search(rx, t):
            return typ
    return "unclassified"

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "section"


def normalize(blocks: list) -> list[dict]:
    """MinerU blocks -> ordered section nodes, each with merged table units."""
    part, sections, cur, seq, seen = None, [], None, 0, {}
    for b in blocks:
        typ, page = b.get("type"), b.get("page_idx", 0) + 1
        if typ == "text" and b.get("text_level"):
            t = (b.get("text") or "").strip()
            mp = _PART.match(t)
            if mp:
                part = mp.group(1).upper(); continue
            mn = _NUM.match(t)
            if mn:                                            # numbered (Pillar 3)
                no, title = mn.group(1), _CONT.sub("", mn.group(2)).strip()
                sid = f"{part}.{no}" if part else no
                if _CONT.search(t) and cur and cur["section_id"] == sid:
                    continue                                  # same section continuing
                node = dict(section_id=sid, section_no=no, title=title,
                            level=no.count(".") + 1, page=page, seq=seq, numbered=True)
            else:                                             # unnumbered (statements / notes)
                # a real heading is short; drop caption/intro sentences mis-tagged as headings
                if (_PAGEHDR.search(t) or _FOOTNOTEHDR.search(t) or len(t) < 3
                        or len(t.split()) > 10
                        or re.match(r"(?i)^(the |these |this |an? |note[: ]|refer |as )", t)):
                    continue
                node = dict(section_id=_slug(t), section_no=None, title=t,
                            level=b["text_level"], page=page, seq=seq, numbered=False)
            # global de-dup within this doc: a re-detected / continued heading (same
            # section_id) reuses its existing node so its tables merge into it
            if node["section_id"] in seen:
                cur = seen[node["section_id"]]
                continue
            node["tables"] = []; node["seq"] = seq; seq += 1
            seen[node["section_id"]] = node; sections.append(node); cur = node
        elif typ == "table":
            if cur is None:
                cur = dict(section_id="front-matter", section_no=None, title="(front matter)",
                           level=1, page=page, seq=seq, numbered=False, tables=[])
                seq += 1; sections.append(cur)
            cap = (b.get("table_caption") or [None])
            cur["tables"].append({"page": page, "caption": cap[0] if cap else None})

    # merge each section's table blocks into contiguous page-range UNITS (continuations)
    for s in sections:
        units = []
        for tb in sorted(s["tables"], key=lambda x: x["page"]):
            if units and tb["page"] - units[-1]["pages"][1] <= 1:
                units[-1]["pages"][1] = max(units[-1]["pages"][1], tb["page"])
                units[-1]["caption"] = units[-1]["caption"] or tb["caption"]
            else:
                units.append({"pages": [tb["page"], tb["page"]], "caption": tb["caption"]})
        s["units"] = units
    return sections


def resolve_parents(sections: list[dict]) -> None:
    ids = {s["section_id"] for s in sections}
    for s in sections:
        parent = None
        if s["numbered"] and s["section_no"] and "." in s["section_no"]:
            pre = s["section_no"]
            while "." in pre:
                pre = pre.rsplit(".", 1)[0]
                cand = f"{s['section_id'].split('.')[0]}.{pre}" if s["section_id"][:1].isalpha() else pre
                if cand in ids:
                    parent = cand; break
        else:                                                 # unnumbered: nearest shallower above
            for prev in reversed([x for x in sections if x["seq"] < s["seq"]]):
                if prev["level"] < s["level"]:
                    parent = prev["section_id"]; break
        s["parent"] = parent


def write_db(db_path: str, doc_id: str, inst: str, family: str, source_file: str,
             period: str | None, sections: list[dict]) -> dict:
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(open(SCHEMA).read())
    con.execute("PRAGMA foreign_keys = ON;")
    c = con.cursor()
    c.execute("INSERT INTO document(doc_id,institution,doc_family,source_file,doc_period) VALUES(?,?,?,?,?)",
              (doc_id, inst, family, source_file, period))
    # pass 1: insert every section with NULL parent (avoids self-ref FK ordering issues)
    for s in sections:
        c.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,section_level,parent_section,seq)"
                  " VALUES(?,?,?,?,?,NULL,?)",
                  (doc_id, s["section_id"], s["section_no"], s["title"], s["level"], s["seq"]))
    # pass 2: set parents now that all rows exist
    sec_ids = {s["section_id"] for s in sections}
    for s in sections:
        if s.get("parent") and s["parent"] in sec_ids:
            c.execute("UPDATE section SET parent_section=? WHERE doc_id=? AND section_id=?",
                      (s["parent"], doc_id, s["section_id"]))
    n_tab = 0
    for s in sections:
        for i, u in enumerate(s["units"], 1):
            n_tab += 1
            a, b = u["pages"]
            title = (u["caption"] or s["title"]).strip()[:200]
            c.execute("INSERT INTO table_t(doc_id,table_id,table_title,table_type,section_id,section_no,page_range)"
                      " VALUES(?,?,?,?,?,?,?)",
                      (doc_id, f"t_{n_tab:03d}", title, classify(title) if classify(title) != "unclassified"
                       else classify(s["title"]), s["section_id"], s["section_no"],
                       f"{a}" if a == b else f"{a}-{b}"))
    con.commit(); con.close()
    return {"sections": len(sections), "tables": n_tab}


def discover(pdf: str, db: str, out: str, mineru_bin: str,
             inst: str, family: str, period: str | None) -> dict:
    blocks = json.load(open(run_mineru(pdf, out, mineru_bin)))
    sections = normalize(blocks)
    resolve_parents(sections)
    doc_id = _slug(os.path.splitext(os.path.basename(pdf))[0])
    stats = write_db(db, doc_id, inst, family, os.path.basename(pdf), period, sections)
    # verify: page-coverage accounting
    pages_with_table = {u["pages"][0] for s in sections for u in s["units"]}
    stats.update(doc_id=doc_id, n_pages=max((b.get("page_idx",0)+1 for b in blocks), default=0),
                 table_pages=len(pages_with_table), db=db)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--db", default="findociq/db/discovery.db")
    ap.add_argument("--out", default="findociq/data/discovery")
    ap.add_argument("--mineru", default=".venv-mineru/bin/mineru")
    ap.add_argument("--inst", default="DBS"); ap.add_argument("--family", default="pillar3")
    ap.add_argument("--period", default=None)
    a = ap.parse_args()
    s = discover(a.pdf, a.db, a.out, a.mineru, a.inst, a.family, a.period)
    print(f"discovered {s['doc_id']}: {s['sections']} sections, {s['tables']} tables "
          f"({s['table_pages']}/{s['n_pages']} pages have tables) -> {s['db']}")
