"""Regression test for quarantine_duplicate_page_tables.py -- specifically
the table_id-not-globally-unique bug found while building the M2 gate
(docs/DECISIONS.md 2026-08-04): an earlier version of pick_canonical/
quarantine() took bare table_id strings and re-derived doc_id via a
`LIMIT 1` query with no ORDER BY. Confirmed live:
`loans_to_customers_loans_to_customers_2025-12-31` exists under TWO
different OCBC doc_ids, only one of which is part of any duplicate
cluster -- the old code silently tagged the WRONG document's (unrelated,
legitimate) table as a duplicate while leaving the REAL duplicate
untouched.

    python3 findociq/pipeline/mapping/test_quarantine_duplicate_page_tables.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapping.quarantine_duplicate_page_tables import (  # noqa: E402
    find_duplicate_clusters, pick_canonical, quarantine,
)

_fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _fail
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        _fail += 1


def _mk_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE table_t (
        doc_id TEXT, table_id TEXT, page_range TEXT, dedup_status TEXT)""")
    con.execute("""CREATE TABLE cell_fact (
        doc_id TEXT, table_id TEXT, row_id INTEGER, col_id INTEGER, value_raw TEXT)""")
    con.execute("""CREATE TABLE row_dim (
        doc_id TEXT, table_id TEXT, row_id INTEGER, row_lineage_id INTEGER)""")
    con.execute("""CREATE TABLE row_lineage (row_lineage_id INTEGER, depth INTEGER)""")
    return con


def test_pick_canonical_returns_a_doc_id_table_id_pair():
    members = [("docA", "shared_id"), ("docA", "other_id")]
    max_depth = {("docA", "shared_id"): 2, ("docA", "other_id"): 1}
    result = pick_canonical(members, max_depth)
    check("pick_canonical returns a (doc_id, table_id) tuple", result == ("docA", "other_id"))


def test_duplicate_table_id_across_two_docs_does_not_cross_contaminate():
    """The exact bug shape: table_id 'shared_id' exists in BOTH docA (part
    of a real duplicate cluster with 'other_id') and docB (a totally
    unrelated table, not a duplicate of anything). Quarantining must never
    touch docB's row."""
    con = _mk_db()
    con.execute("INSERT INTO table_t VALUES ('docA','shared_id','1',NULL)")
    con.execute("INSERT INTO table_t VALUES ('docA','other_id','1',NULL)")
    con.execute("INSERT INTO table_t VALUES ('docB','shared_id','9',NULL)")  # same table_id, unrelated doc
    for row_id in range(3):
        con.execute("INSERT INTO cell_fact VALUES ('docA','shared_id',?,1,?)", (row_id, f"v{row_id}"))
        con.execute("INSERT INTO cell_fact VALUES ('docA','other_id',?,1,?)", (row_id, f"v{row_id}"))
        con.execute("INSERT INTO cell_fact VALUES ('docB','shared_id',?,1,?)", (row_id, f"different{row_id}"))
    for doc_id, table_id in [("docA", "shared_id"), ("docA", "other_id"), ("docB", "shared_id")]:
        con.execute("INSERT INTO row_dim VALUES (?,?,0,1)", (doc_id, table_id))
    con.execute("INSERT INTO row_lineage VALUES (1, 1)")
    con.commit()

    clusters = find_duplicate_clusters(con)
    check("exactly one cluster found (docA's pair, not docB)", len(clusters) == 1,
          detail=str(clusters))
    members = next(iter(clusters.values()))
    check("cluster members are docA's pair only", set(members) == {("docA", "shared_id"), ("docA", "other_id")})

    quarantine(con)
    doc_b_status = con.execute(
        "SELECT dedup_status FROM table_t WHERE doc_id='docB' AND table_id='shared_id'").fetchone()[0]
    check("docB's unrelated table is NEVER tagged", doc_b_status is None)
    docA_tagged = con.execute(
        "SELECT COUNT(*) FROM table_t WHERE doc_id='docA' AND dedup_status='duplicate_page_split'"
    ).fetchone()[0]
    check("exactly one of docA's pair gets tagged", docA_tagged == 1)


def main() -> None:
    test_pick_canonical_returns_a_doc_id_table_id_pair()
    test_duplicate_table_id_across_two_docs_does_not_cross_contaminate()
    print(f"\n{'ALL PASS' if _fail == 0 else f'{_fail} FAILED'}")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
