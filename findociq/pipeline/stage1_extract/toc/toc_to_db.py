"""toc_to_db.py — populate document + section rows in a schema_v7 sqlite DB
from a TOC json emitted by toc_stage.py (<doc_id>_toc.json).

This is the TOC stage's DB WRITE step: it owns the document + section rows for
one doc. It writes ONLY the section hierarchy (id/no/title/level/parent/seq).
The TOC's has_tables / n_regions / anchor_source are ROUTE-MANIFEST state and
are deliberately NOT stored here — the DB truth for "does this section hold
tables" is a LEFT JOIN onto table_t after extraction (see the demo query), not
a boolean copied from the discovery pass.

Idempotent: the TOC stage owns document+section for its doc, so on each run it
doc-scoped DELETEs the doc's section rows and re-inserts in seq order (seq order
guarantees parents land before children for the self-FK), and INSERT-OR-REPLACEs
the document row. If any table_t rows already reference this doc's sections we
FAIL LOUD — those must be reloaded by the extraction stage after a TOC rewrite.

Base python3 + sqlite3 stdlib only.
Run: python3 findociq/pipeline/stage1_extract/toc/toc_to_db.py \
       --toc findociq/data/derived/toc/DBS_2Q25_performance_summary_toc.json \
       --db findociq/db/fs_v7.db --doc-period 2025-06-30
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[3] / "schema" / "schema_v7.sql"  # findociq/schema/

# Metadata registry ONLY — one line per bank, keyed on the doc_id prefix. This
# is display metadata, never a behavioural branch: nothing downstream forks on
# which key matched. A new bank is one new row, not new code.
INSTITUTIONS = {
    "DBS": "DBS Group Holdings Ltd",
    "OCBC": "Oversea-Chinese Banking Corporation Ltd",
    "UOB": "United Overseas Bank Ltd",
}


def institution_for(doc_id, source_pdf=None):
    """doc_id prefix first (works whenever the filename itself carries the
    bank code). Falls back to the source PDF's path: the scraper places every
    doc under <out_root>/<BANK>/<year>/<quarter>/, but some banks' own IR
    filenames (e.g. UOB's 'performance-highlights-1q-2025.pdf') never carry a
    bank code at all — that placement directory is the only remaining
    deterministic signal, so trust it exactly like the scraper does."""
    prefix = doc_id.split("_", 1)[0].upper()
    if prefix in INSTITUTIONS:
        return INSTITUTIONS[prefix]
    if source_pdf:
        segments = {seg.upper() for seg in Path(source_pdf).parts}
        for code, name in INSTITUTIONS.items():
            if code in segments:
                return name
    return None


def ensure_db(conn, db_path):
    """Create + executescript the full schema if the DB is fresh; otherwise
    verify the document/section tables exist. Fail loud on a half-built DB."""
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not have:
        conn.executescript(SCHEMA.read_text())
        conn.commit()
        return
    missing = {"document", "section"} - have
    if missing:
        sys.exit(f"FAIL: DB {db_path} exists but is missing tables: {sorted(missing)}")


def check(cond, msg):
    if not cond:
        sys.exit(f"VALIDATION FAIL: {msg}")


def topo_ordered(sections):
    """Insert order for the section self-FK: every parent BEFORE its children.

    `seq` is PHYSICAL reading order (toc_stage renumbers by anchor position),
    so a subsection that prints before its parent heading carries a smaller seq
    than its parent — seq is NOT a topological order over `parent_section`.
    Return sections in a stable topological order (parents first, ties broken by
    seq so output is deterministic). The stored `seq` value is never changed;
    only the INSERT sequence is. Cycle / unresolved parent => fail loud.
    """
    remaining = sorted(sections, key=lambda x: int(x["seq"]))
    ids = {s["id"] for s in remaining}
    emitted, out = set(), []
    while remaining:
        ready = [s for s in remaining
                 if not s.get("parent_id") or s["parent_id"] in emitted]
        check(ready,
              "cannot topologically order sections (cycle or unresolved "
              f"parent among {[s['id'] for s in remaining]})")
        for s in ready:            # already seq-sorted => stable
            emitted.add(s["id"])
        out.extend(ready)
        ready_set = {s["id"] for s in ready}
        remaining = [s for s in remaining if s["id"] not in ready_set]
    check(len(out) == len(sections), "topo order lost/added rows")
    return out


def load(conn, doc, sections, institution, doc_period):
    # Guard: refuse to delete sections that extraction has already hung tables
    # off of. For now no table_t rows exist; if that changes the operator must
    # reload extraction AFTER re-running this stage.
    n_tab = conn.execute(
        "SELECT COUNT(*) FROM table_t WHERE doc_id=?", (doc["doc_id"],)).fetchone()[0]
    check(n_tab == 0,
          f"{n_tab} table_t rows reference {doc['doc_id']}; reload extraction "
          f"AFTER re-running this TOC stage (drop/reload those tables first)")

    conn.execute("DELETE FROM section WHERE doc_id=?", (doc["doc_id"],))
    conn.execute(
        "INSERT OR REPLACE INTO document "
        "(doc_id, institution, doc_family, source_file, doc_period) "
        "VALUES (?,?,?,?,?)",
        (doc["doc_id"], institution, doc["doc_family"],
         doc.get("source_pdf"), doc_period))

    # topological order => parent inserted before child (self-FK on
    # (doc_id, parent)). seq is reading order, NOT a topo order — see
    # topo_ordered(). The stored seq value is unchanged; only insert order is.
    for s in topo_ordered(sections):
        conn.execute(
            "INSERT INTO section "
            "(doc_id, section_id, section_no, section_title, section_level, "
            " parent_section, section_path, seq) VALUES (?,?,?,?,?,?,?,?)",
            (doc["doc_id"], s["id"], s.get("section_no"), s["title"],
             int(s["level"]), s.get("parent_id"), s.get("path"), int(s["seq"])))
    conn.commit()


def validate(conn, doc_id, expected_n):
    rows = conn.execute(
        "SELECT section_id, parent_section, seq FROM section WHERE doc_id=?",
        (doc_id,)).fetchall()
    check(len(rows) == expected_n,
          f"expected {expected_n} section rows, got {len(rows)}")

    ids = {r[0] for r in rows}
    for sid, parent, _ in rows:
        check(parent is None or parent in ids,
              f"section {sid} parent {parent!r} does not resolve")

    seqs = [r[2] for r in rows]
    check(len(seqs) == len(set(seqs)), "seq not unique per doc")

    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    check(not fk, f"foreign_key_check violations: {fk}")


def demo(conn, doc_id):
    q = ("SELECT s.section_id, COUNT(t.table_id) "
         "FROM section s "
         "LEFT JOIN table_t t ON t.doc_id=s.doc_id AND t.section_id=s.section_id "
         "WHERE s.doc_id=? GROUP BY 1 ORDER BY s.seq")
    rows = conn.execute(q, (doc_id,)).fetchall()
    n = conn.execute(
        "SELECT COUNT(*) FROM section WHERE doc_id=?", (doc_id,)).fetchone()[0]
    print(f"sections for {doc_id}: {n}")
    print(f"{'section_id':44} {'n_tables':>8}")
    for sid, cnt in rows[:10]:
        print(f"{sid[:44]:44} {cnt:>8}")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--toc", required=True,
                    help="TOC json from toc_stage.py (<doc_id>_toc.json)")
    ap.add_argument("--db", required=True,
                    help="target schema_v7 sqlite DB, e.g. findociq/db/fs_v7.db")
    ap.add_argument("--doc-period", required=True,
                    help="document 'as at' date, e.g. 2025-06-30 (required)")
    ap.add_argument("--institution", default=None,
                    help="override; default derived from doc_id prefix registry")
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    payload = json.loads(Path(args.toc).read_text())
    doc, sections = payload["document"], payload["sections"]

    institution = args.institution or institution_for(doc["doc_id"], doc.get("source_pdf"))
    check(institution is not None,
          f"no institution for doc_id {doc['doc_id']!r}; pass --institution")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        ensure_db(conn, db_path)
        load(conn, doc, sections, institution, args.doc_period)
        validate(conn, doc["doc_id"], len(sections))
        demo(conn, doc["doc_id"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
