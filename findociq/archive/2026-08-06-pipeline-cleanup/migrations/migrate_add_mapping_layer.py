"""migrate_add_mapping_layer.py — STEP 1 of docs/specs/MAPPING_LAYER.md §4.

Additive and idempotent. Creates the mapping layer's three tables and one
column. Touches NO existing data: `cell_fact`, `row_dim`, `col_dim` and
`fact_metric` are not read or written here.

  * table_registry        — the controlled vocabulary of exhibit types
  * table_registry_alias  — many normalized titles -> one stable table_type_id
  * bank_line_map         — the per-bank line template (THE durable identity)
  * table_t.table_type_id — resolved pointer; NULL = UNCLASSIFIED

`table_t.table_type` (the model's title slug) is NEVER overwritten — as-reported
is preserved and the normalized pointer rides alongside, the same asymmetry as
`row_leaf_label` / `row_leaf_label_clean`.

    python3 findociq/pipeline/migrate_add_mapping_layer.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


DDL_TABLE_REGISTRY = """
CREATE TABLE IF NOT EXISTS table_registry (
  table_type_id        TEXT PRIMARY KEY,   -- STABLE, registry-assigned. Never
                                           -- derived from a title.
  display_name         TEXT NOT NULL,
  statement_class      TEXT NOT NULL,      -- income_statement | balance_sheet
                                           -- | ratios | per_share | equity
                                           -- | credit_quality | capital
                                           -- | regulatory | segment | geography
  period_nature        TEXT NOT NULL,      -- duration | instant | mixed
  dim_hint             TEXT,               -- axis this exhibit decomposes along:
                                           -- segment | geo | industry | NULL
  legal_entity_default TEXT NOT NULL DEFAULT 'CONSOLIDATED',
  legal_entity_axis    TEXT,               -- NULL  = every row is the default
                                           -- 'column' = Group/Company live in
                                           --   COLUMNS (e.g. DBS balance sheets);
                                           --   a row map alone CANNOT resolve it
  is_regulatory        INTEGER NOT NULL DEFAULT 0,
  notes                TEXT
)"""

DDL_ALIAS = """
CREATE TABLE IF NOT EXISTS table_registry_alias (
  alias_norm    TEXT NOT NULL,        -- normalize_exhibit_title() output
  bank          TEXT NOT NULL DEFAULT '*',   -- '*' = all banks
  table_type_id TEXT NOT NULL REFERENCES table_registry(table_type_id),
  source        TEXT NOT NULL,        -- seed | human_confirmed
  added_at      TEXT NOT NULL,
  PRIMARY KEY (alias_norm, bank)
)"""

DDL_BANK_LINE_MAP = """
CREATE TABLE IF NOT EXISTS bank_line_map (
  map_id            INTEGER PRIMARY KEY,
  -- ANCHOR (must be stable across quarters)
  bank              TEXT NOT NULL,
  table_type_id     TEXT NOT NULL REFERENCES table_registry(table_type_id),
  row_label_norm    TEXT NOT NULL,
  parent_label_norm TEXT NOT NULL DEFAULT '',   -- '' = top-level row
  -- IDENTITY
  concept_key       TEXT,
  legal_entity      TEXT NOT NULL DEFAULT 'CONSOLIDATED',
  segment_key       TEXT,
  geo_key           TEXT,
  industry_key      TEXT,
  -- INTRINSIC (projected from the concept dictionary; denormalized for the gate)
  period_type       TEXT,                       -- instant | duration
  balance           TEXT,                       -- debit | credit
  basis             TEXT,                       -- reported | underlying | NULL
                                                -- Explicit on EVERY anchor of an
                                                -- exhibit that mixes bases; never
                                                -- an implicit default. DBS's
                                                -- overview prints opex/operating
                                                -- profit/PBT on the UNDERLYING
                                                -- basis and net profit on BOTH,
                                                -- so the basis has to ride on the
                                                -- anchor, not on the concept.
  is_abstract       INTEGER NOT NULL DEFAULT 0,
  negated_label     INTEGER NOT NULL DEFAULT 0,
  -- GOVERNANCE
  map_status        TEXT NOT NULL,              -- ai_proposed | ai_verified
                                                -- | human_confirmed
                                                -- | human_corrected | deprecated
  mapped_by         TEXT NOT NULL,
  confidence        REAL,
  mapped_at         TEXT NOT NULL,
  superseded_by     INTEGER REFERENCES bank_line_map(map_id),
  note              TEXT,
  UNIQUE (bank, table_type_id, row_label_norm, parent_label_norm)
)"""

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_blm_anchor ON bank_line_map(bank, table_type_id)",
    "CREATE INDEX IF NOT EXISTS ix_blm_status ON bank_line_map(map_status)",
    "CREATE INDEX IF NOT EXISTS ix_tra_type   ON table_registry_alias(table_type_id)",
]


def migrate(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    summary: dict = {"tables_created": [], "columns_added": [], "indexes": 0}
    try:
        for name, ddl in (("table_registry", DDL_TABLE_REGISTRY),
                          ("table_registry_alias", DDL_ALIAS),
                          ("bank_line_map", DDL_BANK_LINE_MAP)):
            existed = _has_table(con, name)
            con.execute(ddl)
            if not existed:
                summary["tables_created"].append(name)

        if not _has_column(con, "table_t", "table_type_id"):
            con.execute("ALTER TABLE table_t ADD COLUMN table_type_id TEXT")
            summary["columns_added"].append("table_t.table_type_id")

        # added after the table was first created on live DBs
        if not _has_column(con, "bank_line_map", "basis"):
            con.execute("ALTER TABLE bank_line_map ADD COLUMN basis TEXT")
            summary["columns_added"].append("bank_line_map.basis")

        for ddl in DDL_INDEXES:
            con.execute(ddl)
            summary["indexes"] += 1
        con.commit()
    finally:
        con.close()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    s = migrate(args.db)
    print(f"mapping layer migration on {args.db}")
    print(f"  tables created : {s['tables_created'] or '(all already present)'}")
    print(f"  columns added  : {s['columns_added'] or '(already present)'}")
    print(f"  indexes ensured: {s['indexes']}")


if __name__ == "__main__":
    main()
