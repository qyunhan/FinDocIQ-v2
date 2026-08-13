"""migrate_serving_views — the single, idempotent owner of `v_cell` /
`v_cell_leaf` / `v_cell_sumsafe` / `v_cell_flat`.

**MERGES** `migrate_add_human_anchor_projection.py` (2026-08-03, human-anchor
projection onto `row_dim`) and `pass2/migrate_add_period_label.py`
(2026-08-03, uniform period-label rule). Both scripts `DROP`/`CREATE` the
SAME four views with overlapping but non-identical column sets — run in the
wrong order, whichever ran last silently drops the other's columns. This has
been worked around by manual ordering at least three times across three
passes (each one a real, live incident: `period_label`/`period_end` vanished
after a routine anchor-projection re-run, twice). An ordering-dependent
migration pair is a standing hazard for a system whose whole purpose is
*unattended* quarterly automation — a scheduled run, a concurrent session, or
a fresh clone has no way to know the required order. There is now only one
script; the ordering hazard cannot recur because there is nothing left to
order.

Three pieces, run together, in the only order that has ever mattered
(row_dim columns must exist before they're stamped; stamping must happen
before the views read it) — but that ordering is now INTERNAL to one
function, not a fact the operator has to know:

  1. `row_dim.concept_key_human` / `segment_key_human` / `identity_source` —
     project `bank_line_map`'s human-confirmed anchors onto `row_dim`, in
     columns `resolve_deterministic` never touches (so a reload restores
     identity by re-deriving `row_dim.concept_key` underneath an anchor
     that's still there, never fighting over one cell). Address computed the
     same way `resolve_anchors.py` does -- leaf at `lvl{depth}` (preferring
     the raw `row_dim.row_leaf_label` over the footnote-resolved lineage
     form), parent at `lvl{depth-1}` collapsed to `''` when it's a
     table-title constant rather than a real grouping. Both refinements were
     found live, after the scripts had already diverged once (see
     docs/DECISIONS.md 2026-08-03) -- keeping this logic in ONE place is
     itself part of what this merge fixes.
  2. `concept_period_kind` -- kept as a small reference table (6 point-in-time
     ratio concepts), UNUSED by the views below. Available if a later
     display layer wants a ratio's own point-in-time-vs-annualised hint for
     *rendering*; must never again gate what `period_label` stores (that was
     the bug the uniform rule replaced).
  3. The views themselves -- ONE definition, all columns from both merged
     scripts at once: identity (`concept_key`/`segment_key` COALESCE +
     `identity_source`), and period (`period_source`, `period_end`,
     `period_label` via the uniform column-label rule -- every cell carries
     its own column label verbatim; `period_end` alone is what makes a stock
     collapse and a flow stay separate, no concept-level branch).

Idempotent: every step is safe to re-run and produces byte-identical output
on a second run with no data changes in between (see
`test_migrate_serving_views.py`, which asserts exactly this).

    python3 findociq/pipeline/mapping/migrate_serving_views.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(_HERE.parent))
from mapping.normalize import normalize_row_label  # noqa: E402

# Positively-evidenced point-in-time concepts. NOT read by the views below --
# see module docstring point 2.
POINT_IN_TIME_CONCEPTS = [
    "bs.nav_per_share",
    "reg.capital.cet1_ratio",
    "reg.capital.rwa",
    "reg.liquidity.nsfr_ratio",
    "reg.liquidity.lcr_ratio",
    "ratio.npl",
]

VIEWS = {
    "v_cell": """
CREATE VIEW v_cell AS
SELECT f.doc_id, f.table_id, t.table_type, f.period, d.institution,
       f.row_id, r.row_leaf_label, r.row_hierarchy, r.line_no, r.unit AS row_unit,
       f.col_id, f.colspan, c.col_leaf_label, c.col_period, c.unit AS col_unit,
       f.period_span                            AS period_span,
       COALESCE(c.period_start, t.period_start) AS period_start,
       t.unit AS table_unit,
       f.unit AS unit,
       COALESCE(r.concept_key_human, r.concept_key, f.concept_key) AS concept_key,
       f.geo_key     AS geo_key,
       COALESCE(r.segment_key_human, f.segment_key) AS segment_key,
       f.industry_key AS industry_key,
       f.value_raw, f.value_num, f.cell_state, f.is_shade,
       f.row_lineage_id, f.col_lineage_id,
       CASE WHEN r.concept_key_human IS NOT NULL THEN 'human_anchor' ELSE r.identity_source END AS identity_source,
       f.period_source,
       f.period AS period_end,
       CASE
           WHEN f.period_span IS NULL OR f.period_span = 'as_at' THEN f.period
           ELSE f.period_span || substr(f.period, 3, 2)
       END AS period_label
FROM cell_fact f
JOIN row_dim   r ON r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id
JOIN col_dim   c ON c.doc_id=f.doc_id AND c.table_id=f.table_id AND c.col_id=f.col_id
JOIN table_t   t ON t.doc_id=f.doc_id AND t.table_id=f.table_id
JOIN document  d ON d.doc_id=f.doc_id
""",
    "v_cell_leaf": "CREATE VIEW v_cell_leaf AS SELECT * FROM v_cell WHERE row_hierarchy >= 1",
    "v_cell_sumsafe": "CREATE VIEW v_cell_sumsafe AS SELECT * FROM v_cell_leaf WHERE cell_state = 'reported' AND value_num IS NOT NULL",
    "v_cell_flat": """
CREATE VIEW v_cell_flat AS
SELECT d.institution, f.period,
       f.period_span                            AS period_span,
       COALESCE(c.period_start, t.period_start) AS period_start,
       t.table_type, t.table_title,
       t.section_no,
       r.line_no,
       rh.lvl1 AS row_lvl1, rh.lvl2 AS row_lvl2, rh.lvl3 AS row_lvl3,
       rh.lvl4 AS row_lvl4, rh.lvl5 AS row_lvl5, rh.depth AS row_depth,
       ch.lvl1 AS col_lvl1, ch.lvl2 AS col_lvl2, ch.depth AS col_depth,
       f.unit AS unit,
       f.value_num, f.value_raw, f.cell_state, f.is_shade, f.colspan,
       COALESCE(r.concept_key_human, r.concept_key, f.concept_key) AS concept_key,
       f.geo_key     AS geo_key,
       COALESCE(r.segment_key_human, f.segment_key) AS segment_key,
       f.industry_key AS industry_key,
       f.row_lineage_id, f.col_lineage_id,
       f.doc_id, f.table_id, f.row_id, f.col_id, r.row_hierarchy,
       CASE WHEN r.concept_key_human IS NOT NULL THEN 'human_anchor' ELSE r.identity_source END AS identity_source,
       f.period_source,
       f.period AS period_end,
       CASE
           WHEN f.period_span IS NULL OR f.period_span = 'as_at' THEN f.period
           ELSE f.period_span || substr(f.period, 3, 2)
       END AS period_label
FROM cell_fact f
JOIN row_lineage rh ON rh.row_lineage_id = f.row_lineage_id
JOIN col_lineage ch ON ch.col_lineage_id = f.col_lineage_id
JOIN row_dim  r ON r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id
JOIN col_dim  c ON c.doc_id=f.doc_id AND c.table_id=f.table_id AND c.col_id=f.col_id
JOIN table_t  t ON t.doc_id=f.doc_id AND t.table_id=f.table_id
JOIN document d ON d.doc_id=f.doc_id
""",
}


def _has_column(con, table, column) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def ensure_columns(con: sqlite3.Connection) -> dict:
    added = []
    for col, ddl in [("concept_key_human", "TEXT"), ("segment_key_human", "TEXT"),
                      ("identity_source", "TEXT")]:
        if not _has_column(con, "row_dim", col):
            con.execute(f"ALTER TABLE row_dim ADD COLUMN {col} {ddl}")
            added.append(f"row_dim.{col}")
    con.commit()
    return {"columns_added": added}


def stamp_human_anchors(con: sqlite3.Connection) -> dict:
    """For every row_dim row, compute its (bank, table_type_id, row_label_norm,
    parent_label_norm) address the same way resolve_anchors.py / load_anchors.py
    do, and look it up in bank_line_map at map_status='human_confirmed'.
    Additive: never overwrites resolve_deterministic's row_dim.concept_key,
    only fills the _human columns. Idempotent -- re-running recomputes the
    same address for the same data and writes the same value; rows already
    correctly stamped are simply overwritten with themselves.

    Tolerant of a DB that predates the mapping layer: table_t.table_type_id
    (added by migrate_add_mapping_layer.py, not part of schema_v7.sql, and not
    populated by the standard run_doc.py load path) and bank_line_map (created
    by the same migration) may not exist yet -- this function is now called
    unconditionally from concept.load_dictionary.ensure_schema() (STEP 4a of
    every document ingest), so it must be a no-op rather than crash on a DB
    that hasn't had the mapping layer applied. Confirmed live: the
    concept-layer test suite builds a synthetic DB straight from
    schema_v7.sql, which has row_dim/table_t/document but no table_type_id
    column -- exercising exactly this path."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(table_t)")}
    have_tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "table_type_id" not in cols or "bank_line_map" not in have_tables:
        return {"row_dim_stamped": 0}

    rows = con.execute("""
        SELECT rd.doc_id, rd.table_id, rd.row_id, rd.row_leaf_label, rd.row_lineage_id,
               d.institution, tt.table_type_id,
               rl.lvl1, rl.lvl2, rl.lvl3, rl.lvl4, rl.lvl5, rl.depth
        FROM row_dim rd
        JOIN document d ON d.doc_id = rd.doc_id
        JOIN table_t tt ON tt.doc_id = rd.doc_id AND tt.table_id = rd.table_id
        JOIN row_lineage rl ON rl.row_lineage_id = rd.row_lineage_id
        WHERE tt.table_type_id IS NOT NULL
    """).fetchall()

    # Per (doc_id, table_id, depth): is lvl{depth-1} the SAME value for every
    # row at this depth? If so it's a table-title constant, not a real
    # grouping -- collapse to no parent (matches bank_line_map's convention).
    parent_at_depth: dict[tuple[str, str, int], set] = {}
    for doc_id, table_id, row_id, leaf_label, row_lineage_id, institution, ttid, \
            lvl1, lvl2, lvl3, lvl4, lvl5, depth in rows:
        if depth >= 2:
            lvls = [None, lvl1, lvl2, lvl3, lvl4, lvl5]
            key = (doc_id, table_id, depth)
            parent_at_depth.setdefault(key, set()).add(lvls[depth - 1])
    title_like = {k: len(v) <= 1 for k, v in parent_at_depth.items()}

    from mapping.registry import bank_of
    n_stamped = 0
    for doc_id, table_id, row_id, leaf_label, row_lineage_id, institution, ttid, \
            lvl1, lvl2, lvl3, lvl4, lvl5, depth in rows:
        bank = bank_of(institution)
        lvls = [None, lvl1, lvl2, lvl3, lvl4, lvl5]
        # Prefer row_dim.row_leaf_label (RAW, pre-footnote-resolution) over
        # the row_lineage lvl{depth} form (footnote-RESOLVED to a display
        # digit, e.g. 'Total assets' vs 'Total assets 5 72') -- bank_line_map
        # addresses were themselves written preferring the raw form.
        leaf = leaf_label or (lvls[depth] if depth <= 5 else None)
        raw_parent = lvls[depth - 1] if depth >= 2 else None
        parent = None if title_like.get((doc_id, table_id, depth), depth == 1) else raw_parent
        row_label_norm = normalize_row_label(leaf)
        parent_label_norm = normalize_row_label(parent) if parent else ""

        hit = con.execute(
            "SELECT concept_key, segment_key FROM bank_line_map "
            "WHERE bank=? AND table_type_id=? AND row_label_norm=? AND parent_label_norm=? "
            "AND map_status='human_confirmed'",
            (bank, ttid, row_label_norm, parent_label_norm)).fetchone()
        if hit and hit[0] is not None:
            con.execute(
                "UPDATE row_dim SET concept_key_human=?, segment_key_human=?, identity_source='human_anchor' "
                "WHERE doc_id=? AND table_id=? AND row_id=?",
                (hit[0], hit[1], doc_id, table_id, row_id))
            n_stamped += 1
    con.commit()
    return {"row_dim_stamped": n_stamped}


def ensure_concept_period_kind(con: sqlite3.Connection) -> dict:
    con.execute("""CREATE TABLE IF NOT EXISTS concept_period_kind (
        concept_key TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('point_in_time','annualised'))
    )""")
    n = 0
    for ck in POINT_IN_TIME_CONCEPTS:
        con.execute("INSERT OR REPLACE INTO concept_period_kind VALUES (?, 'point_in_time')", (ck,))
        n += 1
    con.commit()
    return {"concept_period_kind_rows": n}


def rebuild_views(con: sqlite3.Connection) -> dict:
    for name in ("v_cell_sumsafe", "v_cell_leaf", "v_cell", "v_cell_flat"):
        con.execute(f"DROP VIEW IF EXISTS {name}")
    for name in ("v_cell", "v_cell_leaf", "v_cell_sumsafe", "v_cell_flat"):
        con.execute(VIEWS[name])
    con.commit()
    return {"views_replaced": 4}


def migrate(con: sqlite3.Connection) -> dict:
    """Single entry point, internally ordered, safe to call any number of
    times: columns must exist before stamping; stamping must happen before
    the views are (re)built so a stale row_dim never leaks into them."""
    out = {}
    out.update(ensure_columns(con))
    out.update(stamp_human_anchors(con))
    out.update(ensure_concept_period_kind(con))
    out.update(rebuild_views(con))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    m = migrate(con)
    print(f"columns added            : {m['columns_added'] or '(already present)'}")
    print(f"row_dim rows stamped     : {m['row_dim_stamped']:,}")
    print(f"concept_period_kind rows : {m['concept_period_kind_rows']}")
    print(f"views replaced           : {m['views_replaced']}")
    con.close()


if __name__ == "__main__":
    main()
