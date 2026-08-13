"""migrate_add_document_alias — G13, level 1: analyst filename -> repo doc_id.

`lineage_identity_map.csv` cites source_doc as the analyst's own filename
(e.g. "OCBC_Full_Year_2025_Condensed_Financial_Statements.pdf"), which is not
always the repo `doc_id` (OCBC titles its Q4 release "Full Year"; the repo
doc_id is `OCBC_4Q25_Condensed_Financial_Statements`, same pack). The map is
NOT mutated to the repo name — the alias table carries the join, so the map
keeps the human-verifiable string an analyst can compare against the source
PDF. See findociq/docs/specs/2026-08-03-anchor-scope-resolution.md.

Also adds two `table_registry_alias` rows that fix real classification bugs
surfaced while resolving the 12 anchor tables (see same spec, Level 3):

  - OCBC media release: the generic '*'-scoped `financial_highlights` section
    alias (added for UOB's combined-table shape) was winning over the
    section-before-title priority for OCBC's "Key Financial Ratios" anchor,
    misrouting it to FS_HIGHLIGHTS_COMBINED instead of FS_RATIOS_KEY. A
    bank-scoped composite alias outranks the wildcard without touching any
    other document that legitimately relies on it.
  - UOB condensed statements: "Balance Sheets (Audited)" sits, in the map,
    under the `Financial Highlights` section (its Total liabilities / Total
    equity are not printed on the highlights pages at all — confirmed via
    cell_fact/row_lineage), but the real statutory Balance Sheet table lives
    in its own section elsewhere in the same doc_id. A bank-scoped composite
    alias routes straight to the real FS_BALANCE_STATUTORY table rather than
    leaving it to fall through to the generic highlights bucket.

Additive + idempotent. Does not touch cell_fact, fact_metric, or the 89
existing bank_line_map anchors.

    python3 findociq/pipeline/mapping/migrate_add_document_alias.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

DOCUMENT_ALIASES = [
    ("DBS_4Q25_performance_summary.pdf", "DBS_4Q25_performance_summary"),
    ("UOB_4Q25_condensed-financial-statements.pdf", "UOB_4Q25_condensed-financial-statements"),
    ("OCBC_Full_Year_2025_Condensed_Financial_Statements.pdf", "OCBC_4Q25_Condensed_Financial_Statements"),
    ("OCBC_4Q25_Media_Release_and_Financial_Highlights.pdf", "OCBC_4Q25_Media_Release_and_Financial_Highlights"),
]

NEW_TABLE_ALIASES = [
    # (alias_norm, bank, table_type_id)
    ("financial_highlights__key_financial_ratios", "OCBC", "FS_RATIOS_KEY"),
    ("financial_highlights__balance_sheets", "UOB", "FS_BALANCE_STATUTORY"),
]


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def migrate(con: sqlite3.Connection) -> dict:
    s = {"document_alias_created": False, "document_alias_rows": 0, "table_alias_rows": 0}
    if not _has_table(con, "document_alias"):
        con.execute("""CREATE TABLE document_alias (
            alias_filename TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES document(doc_id)
        )""")
        s["document_alias_created"] = True

    known_docs = {r[0] for r in con.execute("SELECT doc_id FROM document")}
    for alias_filename, doc_id in DOCUMENT_ALIASES:
        if doc_id not in known_docs:
            raise SystemExit(f"document_alias: doc_id {doc_id!r} (for {alias_filename!r}) "
                              f"not found in document table")
        con.execute("INSERT OR REPLACE INTO document_alias (alias_filename, doc_id) VALUES (?, ?)",
                    (alias_filename, doc_id))
        s["document_alias_rows"] += 1

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for alias_norm, bank, table_type_id in NEW_TABLE_ALIASES:
        con.execute(
            "INSERT OR REPLACE INTO table_registry_alias (alias_norm, bank, table_type_id, source, added_at) "
            "VALUES (?, ?, ?, 'anchor_scope_g13', ?)",
            (alias_norm, bank, table_type_id, now))
        s["table_alias_rows"] += 1

    con.commit()
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    m = migrate(con)
    print(f"document_alias table created : {m['document_alias_created']}")
    print(f"document_alias rows written  : {m['document_alias_rows']}")
    print(f"table_registry_alias rows added: {m['table_alias_rows']}")
    con.close()


if __name__ == "__main__":
    main()
