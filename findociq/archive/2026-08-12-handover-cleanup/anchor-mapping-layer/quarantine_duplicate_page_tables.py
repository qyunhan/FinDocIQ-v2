"""quarantine_duplicate_page_tables — tag (never delete) table_t rows that
are a duplicate EXTRACTION of the same physical page, not a real second
table.

Root cause (not fixed here, see docs/DECISIONS.md 2026-08-04): for some
documents, the table-detection stage creates one `table_t` row PER
SECTION-HEADER it finds on a page, instead of recognizing them as sub-
sections of ONE continuous table. Confirmed live: OCBC's 4Q25 media
release page 12 ("FINANCIAL HIGHLIGHTS (continued)") was extracted 8
times -- once per section header on that page (`earnings_per_share_s_2`,
`net_asset_value_per_share_s`, `capital_adequacy_ratios_8_9`, ...) -- each
with BYTE-IDENTICAL 136-cell values. Corpus-wide: 49 of 375 `table_t` rows
(13%) sit in a same-page, identical-value duplicate cluster, ALL of them
OCBC's `media_release_financial_highlights` doc_kind, across all 4 periods
it appears in (1Q25/1H25/3Q25/4Q25) -- isolated to that one doc_kind, not
DBS, not UOB, not OCBC's `condensed_financial_statements` doc_kind.

This is a real, corpus-wide double-count: every duplicate table's row_dim
rows contribute their own extra addresses to `bank_line_map` (inflating the
Table Registry masterlist) and their own extra candidate cells to
`build_fact_metric.py`'s conflict resolution (inflating apparent conflicts)
-- all for the SAME real value, just extracted N times.

Detection: within one (doc_id, page_range), group table_t rows by the
SORTED TUPLE of their cell_fact.value_raw values. A group of 2+ tables
whose value-tuple is identical (and non-empty) is a duplicate cluster.

Canonical pick within a cluster: the table_id with the SHALLOWEST max
row_lineage.depth (joined via row_dim.row_lineage_id -- NOT
row_dim.row_hierarchy, a different column that disagrees with depth on
4626/6531 row_dim rows corpus-wide) wins, ties broken by table_id
alphabetically -- the
shallower hierarchy consistently matched the page's real layout in manual
inspection (section headers at depth 1, line items at depth 2), while the
deeper variants collapse everything under one over-eager root instead of
recognizing the real sub-sections. This is a STOPGAP so downstream
aggregation stops double-counting today, not a claim that the canonical
pick's hierarchy is perfect -- the real fix is at the table-detection
stage, explicitly out of scope here.

What gets tagged: `table_t.dedup_status = 'duplicate_page_split'` on every
NON-canonical member of a cluster. The canonical member's `dedup_status`
stays NULL. Nothing is deleted -- row_dim/cell_fact for the tagged tables
are untouched, so undoing this (once the root cause is fixed) is just
clearing the column.

Idempotent: re-running clears all `duplicate_page_split` tags first, then
recomputes -- a table that no longer clusters (e.g. after a re-extraction)
is correctly un-tagged rather than left stale.

    python3 findociq/pipeline/mapping/quarantine_duplicate_page_tables.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

TAG = "duplicate_page_split"


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def pick_canonical(members: list[tuple[str, str]],
                    max_depth: dict[tuple[str, str], int],
                    anchor_count: dict[tuple[str, str], int] | None = None) -> tuple[str, str]:
    """`members` and `max_depth`'s keys are `(doc_id, table_id)` pairs, NOT
    bare `table_id` strings -- `table_id` is NOT globally unique across the
    corpus (confirmed: `loans_to_customers_loans_to_customers_2025-12-31`
    exists under two different OCBC doc_ids, one of them a completely
    unrelated table). An earlier version of this function took bare
    table_ids, and the caller re-derived doc_id via a `LIMIT 1` query with
    no ORDER BY -- non-deterministic, and confirmed live to have tagged the
    WRONG document's table as a duplicate while leaving the REAL duplicate
    untouched. Caught while building the M2 gate (docs/DECISIONS.md
    2026-08-04), fixed by carrying doc_id through explicitly.

    PICK ORDER (2026-08-04): MOST HUMAN ANCHORS first, then shallowest max
    row_dim.depth, then alphabetically by (doc_id, table_id).

    Anchors outrank depth because the two mechanisms were silently disagreeing.
    `stamp_human_anchors` matches on (bank, table_type_id, row_label_norm,
    parent_label_norm) -- an address with NO table_id in it -- so on a
    duplicate-split page it stamps whichever physical copy it happens to find.
    When that was not the copy this function chose, `build_fact_metric`
    (which excludes `dedup_status` tables) never saw the anchored row at all,
    and the concept fell back to whatever unanchored candidate won on value
    clustering. Confirmed live on OCBC `bs.equity.shareholders` FY25: the
    anchored 61,768 sat in a `duplicate_page_split` table while an unanchored
    copy of the same page survived, so the dashboard served 60,070.

    Cluster members are byte-identical by construction (that is what makes
    them a cluster), so preferring the anchored copy cannot change any VALUE
    -- it only decides which copy carries the provenance downstream. Depth
    remains the tie-break for the (common) case where no member is anchored,
    so clusters without anchors behave exactly as before.

    `anchor_count` maps (doc_id, table_id) -> number of row_dim rows carrying
    concept_key_human. Optional so existing callers/tests keep working.
    Pure, testable without a DB."""
    ac = anchor_count or {}
    return min(members, key=lambda m: (-ac.get(m, 0), max_depth.get(m, 0), m))


def find_duplicate_clusters(con: sqlite3.Connection) -> dict[tuple, list[tuple[str, str]]]:
    """`(doc_id, page_range, sorted member tuple)` -> `[(doc_id, table_id), ...]`
    for every cluster of 2+ tables on the same page sharing an identical,
    non-empty sorted `cell_fact.value_raw` tuple. Members are `(doc_id,
    table_id)` pairs, not bare `table_id` strings -- `table_id` alone is NOT
    globally unique across the corpus, only unique within one `doc_id`. Read-only."""
    tables = con.execute(
        "SELECT doc_id, table_id, page_range FROM table_t").fetchall()
    by_page: dict[tuple[str, str], list[str]] = defaultdict(list)
    for doc_id, table_id, page_range in tables:
        by_page[(doc_id, page_range)].append(table_id)

    out: dict[tuple, list[tuple[str, str]]] = {}
    for (doc_id, page_range), table_ids in by_page.items():
        if len(table_ids) < 2:
            continue
        by_values: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
        for table_id in table_ids:
            values = tuple(sorted(
                r[0] for r in con.execute(
                    "SELECT value_raw FROM cell_fact WHERE doc_id=? AND table_id=?",
                    (doc_id, table_id)).fetchall()))
            if values:
                by_values[values].append((doc_id, table_id))
        for values, members in by_values.items():
            if len(members) > 1:
                out[(doc_id, page_range, tuple(sorted(members)))] = members
    return out


def quarantine(con: sqlite3.Connection) -> dict:
    if not _has_column(con, "table_t", "dedup_status"):
        con.execute("ALTER TABLE table_t ADD COLUMN dedup_status TEXT")
    con.commit()

    con.execute("UPDATE table_t SET dedup_status = NULL WHERE dedup_status = ?", (TAG,))

    clusters = find_duplicate_clusters(con)
    n_tagged = 0
    n_clusters = 0
    for members in clusters.values():
        max_depth: dict[tuple[str, str], int] = {}
        for doc_id, table_id in members:
            # row_lineage.depth, NOT row_dim.row_hierarchy -- the two disagree
            # on 4626/6531 rows corpus-wide (checked directly); depth is the
            # SAME metric line_item_display_order/stamp_human_anchors already
            # trust for hierarchy-shape decisions, so this stays consistent
            # with the rest of the address-computation logic in this app.
            row = con.execute("""
                SELECT MAX(rl.depth) FROM row_dim rd
                JOIN row_lineage rl ON rl.row_lineage_id = rd.row_lineage_id
                WHERE rd.doc_id=? AND rd.table_id=?
            """, (doc_id, table_id)).fetchone()
            max_depth[(doc_id, table_id)] = row[0] if row and row[0] is not None else 0
        # How many rows of this copy carry a human anchor. Column-existence
        # checked so this stays additive on a DB predating the _human columns
        # (migrate_serving_views.py adds them).
        anchor_count: dict[tuple[str, str], int] = {}
        if _has_column(con, "row_dim", "concept_key_human"):
            for doc_id, table_id in members:
                anchor_count[(doc_id, table_id)] = con.execute(
                    "SELECT COUNT(*) FROM row_dim WHERE doc_id=? AND table_id=? "
                    "AND concept_key_human IS NOT NULL", (doc_id, table_id)).fetchone()[0]
        canonical = pick_canonical(members, max_depth, anchor_count)
        n_clusters += 1
        for doc_id, table_id in members:
            if (doc_id, table_id) == canonical:
                continue
            con.execute(
                "UPDATE table_t SET dedup_status=? WHERE doc_id=? AND table_id=?",
                (TAG, doc_id, table_id))
            n_tagged += 1
    con.commit()
    return {"clusters_found": n_clusters, "tables_tagged_duplicate": n_tagged}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    r = quarantine(con)
    print(f"duplicate clusters found: {r['clusters_found']}")
    print(f"table_t rows tagged '{TAG}': {r['tables_tagged_duplicate']}")
    con.close()


if __name__ == "__main__":
    main()
