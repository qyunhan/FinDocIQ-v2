"""quarantine_f2_geo_wildcard — tag (never delete) the spurious spine-concept
stamps found by the 2026-08-03 pre-flight pass (finding "F2"; see
docs/DECISIONS.md).

Root cause: `concept/run.py`'s deterministic resolver matches row labels
against `concept_map` WILDCARD aliases with no `table_type_id` scoping. UOB's
`FS_PERF_BY_GEOGRAPHY` table (geography-segment breakdown -- Singapore/
Malaysia/Thailand/Indonesia/Greater China/Other, deliberately NOT wired into
the anchor/spine system, see the 2026-08-03 "UOB title-context bare-year gap"
decision -- renamed from `FS_GEO_INCOME` by
migrate_consolidate_table_type_ids.py, 2026-08-04) has row labels ("Total
assets", "Net interest income", ...) that
are IDENTICAL to genuine spine line items elsewhere. Those aliases are
wildcarded because they correctly need to match dozens of legitimate table
types; they have no way to know FS_GEO_INCOME is a breakdown, not a
statement. Before the mandatory re-resolve, these row_dim rows had
concept_key=NULL (unmatched residue) -- confirmed against
db/snapshots/pre_reresolve_2026-08-03.db. The re-resolve is what stamped
them; this migration marks the DAMAGE, not the mechanism (the concept_map
scoping fix is a separate, not-yet-done follow-up).

What gets tagged: every `cell_fact` row under a `table_t.table_type_id =
'FS_PERF_BY_GEOGRAPHY'` table whose resolved concept_key (row_dim human-anchor,
else row_dim deterministic, else cell_fact) is a spine concept, EXCEPT one
stamped via a genuine human_anchor (identity_source='human_anchor' -- if a
future anchor deliberately targets this table_type_id, that's an intentional
decision, not this bug). Marked cell_fact.review_status='F2_geo_wildcard'.
Not deleted: the value, geo_key, and table lineage are the evidence needed
to fix the root cause (scope the concept_map alias) and to confirm the fix
worked (this migration's SELECT-only companion check re-runs clean).
`v_fact_metric_serving`/dashboard queries should filter these out via
`v_cell.identity_source` remaining correctly 'human_anchor'-or-NULL (no
schema change needed there) -- OR, until the root cause is fixed, by
excluding cell_fact.review_status IS NOT NULL explicitly.

Idempotent: re-running recomputes and re-tags the same set; a row that no
longer matches (e.g. after the real fix lands and re-stamps it away) is
correctly un-tagged (review_status reset to NULL) rather than left stale.

    python3 findociq/pipeline/mapping/quarantine_f2_geo_wildcard.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

_AFFECTED_TABLE_TYPE = "FS_PERF_BY_GEOGRAPHY"


def _has_column(con, table, column) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def quarantine(con: sqlite3.Connection) -> dict:
    if not _has_column(con, "cell_fact", "review_status"):
        con.execute("ALTER TABLE cell_fact ADD COLUMN review_status TEXT")
    con.commit()

    # clear stale tags first (idempotent: a row that no longer matches must
    # not stay tagged from a prior run)
    con.execute("UPDATE cell_fact SET review_status = NULL WHERE review_status = 'F2_geo_wildcard'")

    rows = con.execute("""
        SELECT f.doc_id, f.table_id, f.row_id, f.col_id,
               COALESCE(r.concept_key_human, r.concept_key, f.concept_key) AS resolved_concept,
               r.identity_source
        FROM cell_fact f
        JOIN row_dim r ON r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id
        JOIN table_t t ON t.doc_id=f.doc_id AND t.table_id=f.table_id
        WHERE t.table_type_id = ?
    """, (_AFFECTED_TABLE_TYPE,)).fetchall()

    spine = set()
    import csv
    with open(_REPO / "findociq" / "data" / "derived" / "lineage_identity_map.csv") as fh:
        for r in csv.DictReader(fh):
            if r["resolution"] in ("anchor", "derived", "pending_extraction"):
                spine.add(r["concept_key"])

    n_tagged = 0
    for doc_id, table_id, row_id, col_id, concept, identity_source in rows:
        if concept in spine and identity_source != "human_anchor":
            con.execute(
                "UPDATE cell_fact SET review_status='F2_geo_wildcard' "
                "WHERE doc_id=? AND table_id=? AND row_id=? AND col_id=?",
                (doc_id, table_id, row_id, col_id))
            n_tagged += 1
    con.commit()
    return {"cells_tagged": n_tagged}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    r = quarantine(con)
    print(f"cells tagged review_status='F2_geo_wildcard': {r['cells_tagged']:,}")
    con.close()


if __name__ == "__main__":
    main()
