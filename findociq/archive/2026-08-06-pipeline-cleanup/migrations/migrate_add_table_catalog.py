"""migrate_add_table_catalog — G13 continuation, Step 0: the registry
infrastructure `docs/specs/2026-08-XX-anchor-row-resolution.md` depends on.

THE MASTERLIST WRITER (level L1 — table). `docs/specs/2026-08-04-masterlist.md`
is authoritative for what the masterlist is and where it is stored; this is the
one script that writes L1 from the seed CSV. Do not add a second script that
stores L1 masterlist state -- extend this one.

Three new tables, sourced from `data/derived/table_registry_seed.csv` (102 rows,
authored from the real 4Q25 documents) plus this file's own hand-authored
`section_registry`/`doc_cadence` rows (the seed has no raw-section or per-document
columns to derive those from automatically):

  table_catalog     — the seed, loaded verbatim + a normalized caption column.
                       Occurrence-level: one row per (bank, doc_kind, section,
                       table_type_id, caption) as actually observed. Carries
                       cadence/expected/is_narrative for Step 6 coverage.
  section_registry  — (bank, doc_kind, section_raw_norm) -> section_canonical.
                       Authored for the sections actually used by the 72 anchor
                       rows + the 1Q25/3Q25 docs Step 6 validates against.
  doc_cadence       — doc_id -> (bank, doc_kind, cadence). cadence='half_year'
                       means this doc's period position (Q2/Q4/FY) also carries
                       the half_year-cadence tables; 'quarter_only' means it
                       carries only the every_quarter subset (verified: DBS's
                       1Q25/3Q25 docs have exactly the 4 every_quarter tables,
                       nothing from the half_year set).

SCOPING DECISION (see resolution report for the full writeup): the seed's
table_type_id vocabulary renames/folds 11 of the old 26 `table_registry` ids
(e.g. FS_ALLOWANCES -> FS_ALLOWANCES_DETAIL, 4 REG_* folded into FS_RATIOS_KEY).
None of the 72 anchor rows or the 3 pending_extraction rows touch any of those
11 -- they use only the 7 "core" ids, which are byte-identical in both old and
new vocabularies. So this migration does NOT rename or re-stamp existing
`table_t`/`bank_line_map` rows for the other 11 ids -- that would touch corpus
documents (pillar3, 2022 filings) the seed was never authored against, which is
unverified guessing, not migration. `table_registry` gets the seed's 32 ids
added additively; the old, now-superseded ids for the 11 renamed/folded types
are kept alive (not deleted) with a `notes` pointer to their replacement, so
existing out-of-seed-scope corpus data stays valid. A full corpus re-stamp is a
separate, explicitly out-of-scope follow-up.

Additive + idempotent.

    python3 findociq/pipeline/mapping/migrate_add_table_catalog.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(_HERE.parent))
from mapping.normalize import normalize_exhibit_title as normalise_caption  # noqa: E402

SEED_CSV = _REPO / "findociq" / "data" / "derived" / "table_registry_seed.csv"

# old_table_type_id -> (new_table_type_id, why)
RENAMED = {
    "FS_NPA":            ("FS_NPA_COVERAGE",    "renamed in table_registry_seed.csv"),
    "FS_SEGMENT_INCOME": ("FS_PERF_BY_SEGMENT",  "renamed in table_registry_seed.csv"),
    "FS_GEO_INCOME":     ("FS_PERF_BY_GEOGRAPHY","renamed in table_registry_seed.csv"),
    "FS_NII_ANALYSIS":   ("FS_NII_DETAIL",       "renamed in table_registry_seed.csv"),
    "FS_OPEX":           ("FS_EXPENSES_DETAIL",  "renamed in table_registry_seed.csv"),
    "FS_CAPITAL":        ("FS_CAPITAL_ADEQUACY", "renamed in table_registry_seed.csv"),
    "REG_LCR":           ("FS_RATIOS_KEY",       "folded into FS_RATIOS_KEY in table_registry_seed.csv"),
    "REG_LEVERAGE":      ("FS_RATIOS_KEY",       "folded into FS_RATIOS_KEY in table_registry_seed.csv"),
    "REG_NSFR":          ("FS_RATIOS_KEY",       "folded into FS_RATIOS_KEY in table_registry_seed.csv"),
    "REG_KEY_METRICS":   ("FS_RATIOS_KEY",       "folded into FS_RATIOS_KEY in table_registry_seed.csv"),
    # FS_ALLOWANCES is context-dependent (OCBC splits it into FS_ASSET_QUALITY vs
    # FS_ALLOWANCES_DETAIL depending on which physical table; DBS/UOB fold cleanly
    # to FS_ALLOWANCES_DETAIL) -- deliberately NOT auto-renamed here.
}

# (bank, doc_kind, section_raw_norm) -> section_canonical.
# Scoped to: the 6 (bank, doc_section) pairs the 72 anchor rows actually use,
# plus enough of each doc_kind's other real sections to make doc_cadence /
# Step 6 coverage meaningful without over-claiming coverage we haven't verified.
SECTION_REGISTRY = [
    ("DBS", "performance_summary", "overview", "Overview"),
    ("DBS", "performance_summary", "audited_balance_sheets", "Financial Statements"),
    ("DBS", "performance_summary", "audited_consolidated_income_statement", "Financial Statements"),
    ("DBS", "performance_summary", "audited_consolidated_statement_of_comprehensive_income", "Financial Statements"),
    ("DBS", "performance_summary", "audited_consolidated_cash_flow_statement", "Financial Statements"),
    ("UOB", "condensed_financial_statements", "financial_highlights", "Financial Highlights"),
    ("OCBC", "condensed_financial_statements", "balance_sheets", "Condensed Financial Statements"),
    ("OCBC", "condensed_financial_statements", "consolidated_income_statement", "Condensed Financial Statements"),
    ("OCBC", "media_release_financial_highlights", "financial_highlights", "FINANCIAL HIGHLIGHTS"),
]

# doc_id -> (bank, doc_kind, cadence, period_position). cadence verified by
# reading each doc's real table_t: 1Q25/3Q25 DBS docs carry exactly the
# every_quarter 4-table subset (Selected income/balance/ratios/per-share),
# nothing from the half_year set -- so quarter_only is measured, not assumed.
DOC_CADENCE = [
    ("DBS_4Q25_performance_summary",                 "DBS",  "performance_summary",              "half_year",    "4Q25"),
    ("DBS_1Q25_trading_update",                       "DBS",  "performance_summary",              "quarter_only", "1Q25"),
    ("3Q25_trading_update",                            "DBS",  "performance_summary",              "quarter_only", "3Q25"),
    ("UOB_4Q25_condensed-financial-statements",        "UOB",  "condensed_financial_statements",   "half_year",    "4Q25"),
    ("UOB_1Q25_performance-highlights",                "UOB",  "condensed_financial_statements",   "quarter_only", "1Q25"),
    ("UOB_3Q25_Performance_Highlights",                "UOB",  "condensed_financial_statements",   "quarter_only", "3Q25"),
    ("OCBC_4Q25_Condensed_Financial_Statements",       "OCBC", "condensed_financial_statements",   "half_year",    "4Q25"),
    ("OCBC_4Q25_Media_Release_and_Financial_Highlights","OCBC","media_release_financial_highlights","half_year",   "4Q25"),
    ("OCBC_1Q25_Results__Press_Release",               "OCBC", "media_release_financial_highlights","quarter_only","1Q25"),
    ("OCBC_3Q25_Results_Press_Release",                "OCBC", "media_release_financial_highlights","quarter_only","3Q25"),
]


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def migrate(con: sqlite3.Connection) -> dict:
    s = {"table_catalog": 0, "section_registry": 0, "doc_cadence": 0,
         "table_registry_added": 0, "table_registry_superseded_noted": 0}

    if not _has_table(con, "table_catalog"):
        con.execute("""CREATE TABLE table_catalog (
            bank TEXT NOT NULL,
            doc_kind TEXT NOT NULL,
            cadence TEXT NOT NULL,
            section_canonical TEXT NOT NULL,
            table_type_id TEXT NOT NULL,
            caption_canonical TEXT NOT NULL,
            caption_norm TEXT NOT NULL,
            col_axis TEXT,
            row_dim_axis TEXT,
            value_kind TEXT,
            page TEXT,
            expected INTEGER NOT NULL,
            is_narrative INTEGER NOT NULL,
            evidence TEXT,
            notes TEXT,
            PRIMARY KEY (bank, doc_kind, section_canonical, table_type_id, caption_canonical)
        )""")
    if not _has_table(con, "section_registry"):
        con.execute("""CREATE TABLE section_registry (
            bank TEXT NOT NULL,
            doc_kind TEXT NOT NULL,
            section_raw_norm TEXT NOT NULL,
            section_canonical TEXT NOT NULL,
            PRIMARY KEY (bank, doc_kind, section_raw_norm)
        )""")
    if not _has_table(con, "doc_cadence"):
        con.execute("""CREATE TABLE doc_cadence (
            doc_id TEXT PRIMARY KEY REFERENCES document(doc_id),
            bank TEXT NOT NULL,
            doc_kind TEXT NOT NULL,
            cadence TEXT NOT NULL,
            period_position TEXT NOT NULL
        )""")

    with open(SEED_CSV) as f:
        for row in csv.DictReader(f):
            caption_norm = normalise_caption(row["caption_canonical"])
            con.execute("""INSERT OR REPLACE INTO table_catalog
                (bank, doc_kind, cadence, section_canonical, table_type_id, caption_canonical,
                 caption_norm, col_axis, row_dim_axis, value_kind, page, expected, is_narrative,
                 evidence, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["bank"], row["doc_kind"], row["cadence"], row["section_canonical"],
                 row["table_type_id"], row["caption_canonical"], caption_norm,
                 row["col_axis"], row["row_dim_axis"], row["value_kind"], row["page"],
                 1 if row["expected"] == "True" else 0,
                 1 if row["is_narrative"] == "True" else 0,
                 row["evidence"], row["notes"]))
            s["table_catalog"] += 1

    for bank, doc_kind, section_raw_norm, section_canonical in SECTION_REGISTRY:
        con.execute("INSERT OR REPLACE INTO section_registry VALUES (?,?,?,?)",
                    (bank, doc_kind, section_raw_norm, section_canonical))
        s["section_registry"] += 1

    known_docs = {r[0] for r in con.execute("SELECT doc_id FROM document")}
    for doc_id, bank, doc_kind, cadence, period_position in DOC_CADENCE:
        if doc_id not in known_docs:
            raise SystemExit(f"doc_cadence: doc_id {doc_id!r} not found in document table")
        con.execute("INSERT OR REPLACE INTO doc_cadence VALUES (?,?,?,?,?)",
                    (doc_id, bank, doc_kind, cadence, period_position))
        s["doc_cadence"] += 1

    seed_type_ids = {r[0] for r in con.execute("SELECT DISTINCT table_type_id FROM table_catalog "
                                                "WHERE table_type_id != 'UNCLASSIFIED'")}
    existing_type_ids = {r[0] for r in con.execute("SELECT table_type_id FROM table_registry")}
    for ttid in sorted(seed_type_ids - existing_type_ids):
        display_name = con.execute(
            "SELECT caption_canonical FROM table_catalog WHERE table_type_id=? "
            "ORDER BY expected DESC LIMIT 1", (ttid,)).fetchone()[0]
        con.execute("INSERT INTO table_registry (table_type_id, display_name, statement_class, "
                    "period_nature, legal_entity_default, is_regulatory, notes) "
                    "VALUES (?,?,'unclassified','period','CONSOLIDATED',0,'added from table_registry_seed.csv')",
                    (ttid, display_name))
        s["table_registry_added"] += 1

    for old_id, (new_id, why) in RENAMED.items():
        row = con.execute("SELECT notes FROM table_registry WHERE table_type_id=?", (old_id,)).fetchone()
        if row is not None and (row[0] or "") .find("SUPERSEDED") < 0:
            con.execute("UPDATE table_registry SET notes = COALESCE(notes || ' | ', '') || ? "
                        "WHERE table_type_id=?",
                        (f"SUPERSEDED by {new_id} ({why}); old id kept alive for out-of-seed-scope "
                         f"corpus rows (pillar3/2022 docs) not re-verified against the new registry",
                         old_id))
            s["table_registry_superseded_noted"] += 1

    con.commit()
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    m = migrate(con)
    for k, v in m.items():
        print(f"{k:32}: {v}")
    con.close()


if __name__ == "__main__":
    main()
