"""migrate_consolidate_table_type_ids — the corpus re-stamp
migrate_add_table_catalog.py explicitly deferred: "A full corpus re-stamp is
a separate, explicitly out-of-scope follow-up."

migrate_add_table_catalog.py's RENAMED dict documents 11 old table_registry
ids that table_registry_seed.csv renamed/folded into new ones, but left BOTH
vocabularies live side by side: table_t/bank_line_map/table_registry_alias
kept pointing at the OLD id, while table_catalog (the new FS masterlist)
expects the NEW id. Net effect: real captured data for e.g. DBS's NPA table
sits under `FS_NPA` (36 table_t rows, 181 bank_line_map rows) while the
masterlist looks for `FS_NPA_COVERAGE` and sees zero live occurrences --
duplicate registries for the same real table, not a genuine coverage gap.

Scope: only the 6 pairs that are a clean 1:1 rename with ZERO bank_line_map
address collisions (verified by direct query before writing this):

    FS_NPA            -> FS_NPA_COVERAGE
    FS_SEGMENT_INCOME -> FS_PERF_BY_SEGMENT
    FS_GEO_INCOME     -> FS_PERF_BY_GEOGRAPHY
    FS_NII_ANALYSIS   -> FS_NII_DETAIL
    FS_OPEX           -> FS_EXPENSES_DETAIL
    FS_CAPITAL        -> FS_CAPITAL_ADEQUACY

Deliberately NOT in scope (do not add here without a human merge decision):

  - REG_LCR / REG_LEVERAGE / REG_NSFR / REG_KEY_METRICS -> FS_RATIOS_KEY: a
    4-way fold, and FS_RATIOS_KEY already has 143 of its own bank_line_map
    addresses -- 40 of the folded REG_* addresses collide with an EXISTING
    FS_RATIOS_KEY address. A blind rename would either violate
    bank_line_map's UNIQUE(bank, table_type_id, row_label_norm,
    parent_label_norm) constraint or silently overwrite a reviewed row;
    resolving which of the two rows wins per collision needs a look at the
    actual data, not a script.
  - FS_ALLOWANCES: migrate_add_table_catalog.py's own docstring marks this
    "context-dependent" (OCBC splits it into FS_ASSET_QUALITY vs
    FS_ALLOWANCES_DETAIL depending on which physical table; DBS/UOB fold
    cleanly) -- deliberately not auto-renamed there either, for the same
    reason.

For each of the 6 pairs:

  1. Copy the OLD id's REAL table_registry metadata (statement_class,
     period_nature, dim_hint, legal_entity_axis, is_regulatory) onto the NEW
     id's table_registry row. migrate_add_table_catalog.py auto-inserted the
     NEW id with PLACEHOLDER metadata (statement_class='unclassified',
     period_nature='period', dim_hint=NULL) since it didn't know the true
     classification. Skipping this step would silently degrade
     dim_hint-driven segment/geo resolution in build_fact_metric.py the
     moment table_t starts pointing at the new id -- confirmed live:
     FS_GEO_INCOME's real row is ('geography','duration','geo'), the
     auto-inserted FS_PERF_BY_GEOGRAPHY row was ('unclassified','period',
     NULL) before this fix.
  2. UPDATE table_t.table_type_id, bank_line_map.table_type_id,
     table_registry_alias.table_type_id: old -> new. All three move
     together so the (bank, table_type_id, row_label_norm, parent_label_norm)
     join stamp_human_anchors uses still resolves identically.
  3. Old table_registry row is left in place (migrate_add_table_catalog.py
     already appended a SUPERSEDED note to it) -- after step 2 nothing
     references it, so it's inert history, not live duplication.

Idempotent: re-running finds zero old-id rows left in table_t/bank_line_map/
table_registry_alias and is a no-op.

    python3 findociq/pipeline/mapping/migrate_consolidate_table_type_ids.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

RENAMED = {
    "FS_NPA": "FS_NPA_COVERAGE",
    "FS_SEGMENT_INCOME": "FS_PERF_BY_SEGMENT",
    "FS_GEO_INCOME": "FS_PERF_BY_GEOGRAPHY",
    "FS_NII_ANALYSIS": "FS_NII_DETAIL",
    "FS_OPEX": "FS_EXPENSES_DETAIL",
    "FS_CAPITAL": "FS_CAPITAL_ADEQUACY",
}


def migrate(con: sqlite3.Connection) -> dict:
    stats = {"table_registry_metadata_copied": 0, "table_t_rows": 0,
             "bank_line_map_rows": 0, "table_registry_alias_rows": 0}

    for old, new in RENAMED.items():
        meta = con.execute(
            "SELECT statement_class, period_nature, dim_hint, legal_entity_axis, "
            "is_regulatory FROM table_registry WHERE table_type_id=?", (old,)).fetchone()
        if meta is None:
            continue
        statement_class, period_nature, dim_hint, legal_entity_axis, is_regulatory = meta
        cur = con.execute(
            "UPDATE table_registry SET statement_class=?, period_nature=?, dim_hint=?, "
            "legal_entity_axis=?, is_regulatory=? WHERE table_type_id=?",
            (statement_class, period_nature, dim_hint, legal_entity_axis, is_regulatory, new))
        stats["table_registry_metadata_copied"] += cur.rowcount

        cur = con.execute("UPDATE table_t SET table_type_id=? WHERE table_type_id=?", (new, old))
        stats["table_t_rows"] += cur.rowcount

        cur = con.execute("UPDATE bank_line_map SET table_type_id=? WHERE table_type_id=?", (new, old))
        stats["bank_line_map_rows"] += cur.rowcount

        cur = con.execute(
            "UPDATE table_registry_alias SET table_type_id=? WHERE table_type_id=?", (new, old))
        stats["table_registry_alias_rows"] += cur.rowcount

    con.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    s = migrate(con)
    for k, v in s.items():
        print(f"{k:32}: {v}")
    con.close()


if __name__ == "__main__":
    main()
