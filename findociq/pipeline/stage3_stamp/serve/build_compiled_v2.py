"""Build findociq/db/compiled_v2.db — the clean target schema.

Built CLEAN, not inherited: every table is CREATEd with exactly its target
columns and filled by INSERT..SELECT from compiled_fs.db. Nothing is copied and
then ALTERed, so no dropped column leaves a residue and no stale index survives.

TARGET SCHEMA
  document, section, ingest_status   as-is
  table_t      + table_type_id (CARRIED from the source: load_v7 stamps it)
  row_dim      + canonical_leaf_id (CARRIED: load_v7 stamps it)
               - concept_key, geo_key, segment_key, line_no, row_lineage_id
                 (and the concept_key_human / segment_key_human echoes,
                  and identity_source — all concept-binding residue)
  col_dim      + period_id, period_type (NULL); canonical_col_id, col_role CARRIED
               - concept/geo/segment stamps, col_lineage_id
  cell_fact    + period_id, period_source (NULL)
               - concept/geo/segment_key, colspan, is_shade,
                 row_lineage_id, col_lineage_id
  geo_dim, segment_dim   vocabulary only; SEG_TOTAL removed, geo flattened

EVERYTHING ELSE IS DROPPED — bank_line_map (+2 snapshots), canonical_leaf(_alias),
concept_* (5 tables incl. the 7,044-row resolution log), fact_metric, row_lineage,
col_lineage, table_catalog, table_registry(_alias), section_registry, doc_cadence,
industry_*, legal_entity_*, segment_map, document_alias,
m1_resolution_dryrun. 34 tables -> 9.

ROW_PARENT IS RECOMPUTED. compiled_fs.db's row_parent came from the pre-fix
row_parents_by_position(), whose blanket total-skip orphaned 1,055 rows on the
model path and mis-parented others (3Q25's ECL rows landed under 'Amortisation of
intangible assets'). Here the parent is the NEAREST EARLIER ROW AT A STRICTLY
LOWER LEVEL within the table, taken from row_hierarchy — the extractor's own
level, which parsed.json proves is correct. That rule reproduces the fixed
loader's output on both DBS 3Q25 and 4Q25 'Selected income statement items', and
it keeps the DEBTS ISSUED protection for free: 'Due within 1 year' sits at the
SAME level as the preceding Total, so a strictly-lower test never selects it.

PERIOD CHAIN PRESERVED — deliberately, every field the period resolver walks:
    cell_fact.period / period_span / period_source / period_id
      <- col_dim.col_leaf_label, col_parent, col_hierarchy,
         col_period, period_span, period_start
      <- row_dim.row_leaf_label, row_period, period_span, period_start
      <- table_t.table_title, period, period_span, period_start
      <- section.section_title, section_no, parent_section
      <- document.doc_period
Quarter / half / full-year and the date chain all remain derivable.

    python3 findociq/pipeline/stage3_stamp/serve/build_compiled_v2.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SRC = REPO / "findociq/db/compiled_fs.db"
DST = REPO / "findociq/db/compiled_v2.db"

DDL = """
CREATE TABLE document (
  doc_id TEXT PRIMARY KEY, institution TEXT, doc_family TEXT,
  source_file TEXT, doc_period DATE);

CREATE TABLE section (
  doc_id TEXT, section_id TEXT, section_no TEXT, section_title TEXT,
  section_level INTEGER, parent_section TEXT, section_path TEXT, seq INTEGER,
  PRIMARY KEY (doc_id, section_id));

CREATE TABLE ingest_status (
  source_file TEXT, doc_id TEXT, bank TEXT, period TEXT, family TEXT,
  stage TEXT, state TEXT, error_class TEXT, error_message TEXT,
  attempt_count INTEGER, last_attempt_at TEXT, updated_at TEXT);

CREATE TABLE table_t (
  doc_id TEXT, table_id TEXT, table_title TEXT, table_type TEXT,
  section_id TEXT, section_no TEXT,
  period DATE, period_span TEXT, period_start DATE,
  page_range TEXT, unit TEXT, hierarchy_source TEXT, dedup_status TEXT,
  table_type_id TEXT,                      -- CARRIED from source (load_v7)
  table_title_clean TEXT,                  -- CARRIED: the geometry stage's
                                           -- footnote-stripped caption. One table
                                           -- prints '(%)4' in one document and
                                           -- '(%)1,2' in another; without this the
                                           -- dashboard renders two sections for it.
  PRIMARY KEY (doc_id, table_id));

CREATE TABLE row_dim (
  doc_id TEXT, table_id TEXT, row_id INTEGER,
  row_hierarchy INTEGER, row_parent INTEGER, row_leaf_label TEXT,
  row_period DATE, period_span TEXT, period_start DATE,
  unit TEXT, sums_to INTEGER, sums_sign INTEGER,
  canonical_leaf_id TEXT,                  -- CARRIED from source (load_v7)
  table_type_id TEXT,                      -- CARRIED from source (load_v7). The other half of
                                           -- the anchor address; row grain, NOT table grain --
                                           -- see row_dim.table_type_id in schema_v7.sql.
  geo_key TEXT, segment_key TEXT, industry_key TEXT,
                                           -- CARRIED from source. These were DROPPED here as
                                           -- concept-binding residue, which they were under the
                                           -- old concept model. Under
                                           -- docs/specs/2026-08-09-column-axis-identity.md they
                                           -- are the opposite: §5 makes the dim vocabulary the
                                           -- SHARED address across both axes, so OCBC printing
                                           -- geography on rows can be compared to UOB printing
                                           -- it on columns. Dropping it on the row axis breaks
                                           -- exactly the comparison that spec exists to enable,
                                           -- and leaves §7's `r.<dim>_key = :row_dim_key`
                                           -- anchor predicate addressing a column that does not
                                           -- exist in the DB the app reads.
                                           -- col_dim needs no equivalent: canonical_col_id
                                           -- already carries the same member on that axis.
  PRIMARY KEY (doc_id, table_id, row_id));

CREATE TABLE col_dim (
  doc_id TEXT, table_id TEXT, col_id INTEGER,
  col_hierarchy INTEGER, col_parent INTEGER, col_leaf_label TEXT,
  col_period DATE, period_span TEXT, period_start DATE,
  unit TEXT, sums_to INTEGER, sums_sign INTEGER, legal_entity TEXT,
  period_id TEXT, period_type TEXT,        -- NULL until period resolution
  canonical_col_id TEXT, col_role TEXT,    -- CARRIED from source (load_v7)
  PRIMARY KEY (doc_id, table_id, col_id));

CREATE TABLE cell_fact (
  doc_id TEXT, table_id TEXT, row_id INTEGER, col_id INTEGER,
  value_raw TEXT, value_num REAL, unit TEXT, cell_state TEXT,
  period DATE, period_span TEXT, legal_entity TEXT, review_status TEXT,
  period_id TEXT,                          -- NULL until period resolution
  period_source TEXT,                      -- CARRIED: the loader's own
                                           -- provenance ('col'/'row'/'table_title'/
                                           -- 'doc'). Was being NULLed here, which
                                           -- silently discarded which cells fell
                                           -- back to the document date.
  PRIMARY KEY (doc_id, table_id, row_id, col_id));

CREATE TABLE geo_dim (                     -- flattened: no parent_geo
  geo_key TEXT PRIMARY KEY, label TEXT, geo_level TEXT, iso_alpha2 TEXT);

CREATE TABLE segment_dim (                 -- SEG_TOTAL removed; no parent_seg
  segment_key TEXT PRIMARY KEY, label TEXT, seg_level TEXT);
"""


def recompute_parents(src) -> dict:
    """(doc_id, table_id, row_id) -> row_parent, from row_hierarchy only."""
    by_table = defaultdict(list)
    for r in src.execute("""SELECT doc_id, table_id, row_id, row_hierarchy
                            FROM row_dim ORDER BY doc_id, table_id, row_id"""):
        by_table[(r[0], r[1])].append((r[2], r[3] or 0))
    out, stats = {}, {"total": 0, "with_parent": 0, "root": 0}
    for (doc, tbl), rows in by_table.items():
        for i, (rid, lvl) in enumerate(rows):
            stats["total"] += 1
            p = None
            for j in range(i - 1, -1, -1):
                if rows[j][1] < lvl:          # strictly shallower = the parent
                    p = rows[j][0]
                    break
            out[(doc, tbl, rid)] = p
            stats["with_parent" if p is not None else "root"] += 1
    return out, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--dst", default=str(DST))
    ap.add_argument("--carry-from", default=str(REPO / "findociq/db/compiled_fs.db"),
                    help="DB to copy REFERENCE tables from when --src lacks them "
                         "(table_catalog, bank_line_map, row_lineage). These are "
                         "not produced by the loader: table_catalog is the table "
                         "registry the dashboard's Table Registry view renders, and "
                         "without it that view is silently empty. Pass '' to skip.")
    ap.add_argument("--parents", choices=("trust", "recompute"), default="trust",
                    help="trust: keep the source DB's row_parent (correct when the "
                         "source was loaded by the FIXED loader — printed parents "
                         "consumed, header-vs-terminal totals, table-scoped gates). "
                         "recompute: derive from row_hierarchy instead, the "
                         "stand-in used while compiled_fs.db was still pre-fix.")
    args = ap.parse_args(argv)

    dst_path = Path(args.dst)
    if dst_path.exists():
        dst_path.unlink()
    src = sqlite3.connect(f"file:{args.src}?mode=ro", uri=True)
    dst = sqlite3.connect(args.dst)
    dst.executescript(DDL)

    if args.parents == "recompute":
        parents, pstats = recompute_parents(src)
    else:
        parents, pstats = None, None

    def have(table: str) -> set:
        return {r[1] for r in src.execute(f"PRAGMA table_info({table})")}

    def sel(table: str, names: list[str]) -> str:
        """SELECT that tolerates a source lacking a column. compiled_fs.db has
        migration-added columns (dedup_status) that a DB built straight from
        schema_v7.sql does not — a replayed load being exactly that case. A
        missing column yields NULL rather than failing the whole build."""
        present = have(table)
        cols = [n if (n in present or not n.isidentifier()) else "NULL" for n in names]
        return f"SELECT {', '.join(cols)} FROM {table}"

    def copy(sql, table, cols, rowfn=None):
        rows = list(src.execute(sql))
        if rowfn:
            rows = [rowfn(r) for r in rows]
        ph = ",".join("?" * cols)
        dst.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({ph})", rows)
        print(f"  {table:14s} {len(rows):6d}")

    copy("SELECT doc_id, institution, doc_family, source_file, doc_period FROM document",
         "document", 5)
    copy("""SELECT doc_id, section_id, section_no, section_title, section_level,
                   parent_section, section_path, seq FROM section""", "section", 8)
    copy("""SELECT source_file, doc_id, bank, period, family, stage, state,
                   error_class, error_message, attempt_count, last_attempt_at,
                   updated_at FROM ingest_status""", "ingest_status", 12)
    copy(sel("table_t", ["doc_id", "table_id", "table_title", "table_type",
                         "section_id", "section_no", "period", "period_span",
                         "period_start", "page_range", "unit", "hierarchy_source",
                         "dedup_status", "table_type_id",
                         "table_title_clean"]), "table_t", 15)
    copy(sel("row_dim", ["doc_id", "table_id", "row_id", "row_hierarchy", "row_parent",
                         "row_leaf_label", "row_period", "period_span", "period_start",
                         "unit", "sums_to", "sums_sign", "canonical_leaf_id",
                         "table_type_id", "geo_key", "segment_key", "industry_key"]),
         "row_dim", 17,
         rowfn=(lambda r: (r[0], r[1], r[2], r[3],
                           parents.get((r[0], r[1], r[2])),   # RECOMPUTED
                           *r[5:]))
                if parents is not None else None)
    copy(sel("col_dim", ["doc_id", "table_id", "col_id", "col_hierarchy", "col_parent",
                         "col_leaf_label", "col_period", "period_span", "period_start",
                         "unit", "sums_to", "sums_sign", "legal_entity",
                         "NULL", "NULL", "canonical_col_id", "col_role"]),
         "col_dim", 17)
    copy(sel("cell_fact", ["doc_id", "table_id", "row_id", "col_id", "value_raw",
                           "value_num", "unit", "cell_state", "period", "period_span",
                           "legal_entity", "review_status", "NULL", "period_source"]),
         "cell_fact", 14)
    copy("SELECT geo_key, label, geo_level, iso_alpha2 FROM geo_dim", "geo_dim", 4)
    copy("""SELECT segment_key, label, seg_level FROM segment_dim
            WHERE segment_key <> 'SEG_TOTAL'""", "segment_dim", 3)

    # REFERENCE TABLES the loader does not produce. compiled_v2's clean schema
    # drops the whole mapping layer, which is right for the binding tables — but
    # table_catalog is static reference data (the 102-row table registry) and the
    # dashboard's Table Registry view reads it directly. Dropping it made that
    # view render empty with no error. Copied verbatim, schema and all.
    carry = Path(args.carry_from) if args.carry_from else None
    if carry and carry.exists() and carry.resolve() != Path(args.src).resolve():
        ref = sqlite3.connect(f"file:{carry}?mode=ro", uri=True)
        for name in ("table_catalog", "bank_line_map", "row_lineage"):
            have_src = src.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (name,)).fetchone()[0]
            if have_src:
                continue                      # the source already has it
            ddl = ref.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                              "AND name=?", (name,)).fetchone()
            if not ddl:
                continue
            dst.execute(ddl[0])
            rows = list(ref.execute(f"SELECT * FROM {name}"))
            if rows:
                ph = ",".join("?" * len(rows[0]))
                dst.executemany(f"INSERT INTO {name} VALUES ({ph})", rows)
            print(f"  {name:14s} {len(rows):6d}  (carried from {carry.name})")
        ref.close()

    dst.commit()
    dst.execute("VACUUM")
    dst.commit()
    if pstats:
        print(f"\n  row_parent recomputed: {pstats['with_parent']} parented, "
              f"{pstats['root']} root, of {pstats['total']}")
    else:
        print("\n  row_parent: taken from the source DB (fixed loader)")
    src.close(); dst.close()
    print(f"  size: {dst_path.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
