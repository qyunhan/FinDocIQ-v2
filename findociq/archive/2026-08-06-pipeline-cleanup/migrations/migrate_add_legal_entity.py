"""migrate_add_legal_entity — the consolidation-basis axis (decision: option (a)).

Adds `legal_entity` as a COLUMN-level axis, because that is where the banks
actually put it. Measured, identical shape in all three balance sheets:

    DBS   col 100 'The Group'  col 101 'The Company'
    UOB   col 100 'The Group'  col 101 'The Bank'
    OCBC  col 100 'GROUP'      col 101 'BANK'

with the period columns hanging off them via `col_dim.col_parent`. A ROW map
cannot resolve this, which is why `bank_line_map` alone could never fix it.

This is the root cause of the merge bugs:
    DBS   bs.equity.total   17,643 (Company) shadowing 68,916 (Group)
    UOB   bs.assets.total  485,263 (Bank)    shadowing 572,061 (Group)

NOTE — those entity columns are exactly the `col_id >= 100` rows the loader
treats as phantom echo columns to be dropped from display. They carry ZERO
cells (they are span headers, not value columns), so they look like artifacts;
they are in fact the dimension headers. Nothing here changes the loader — the
axis is derived from `col_parent`, which already points at them.

Additive + idempotent. Does not touch extraction, row_dim, or fact_metric.

    python3 findociq/pipeline/mapping/migrate_add_legal_entity.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mapping.normalize import normalize_row_label  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]

DEFAULT_ENTITY = "CONSOLIDATED"

LEGAL_ENTITY_DIM = [
    ("CONSOLIDATED",   "Group / consolidated", "total"),
    ("PARENT_COMPANY", "The Company (holding company, unconsolidated)", "solo"),
    ("BANK_SOLO",      "The Bank (banking entity, unconsolidated)", "solo"),
]

# label_norm -> legal_entity_key. Mirrors geo_map / segment_map exactly.
LEGAL_ENTITY_MAP = [
    ("the_group", "CONSOLIDATED"), ("group", "CONSOLIDATED"),
    ("consolidated", "CONSOLIDATED"), ("the_group_consolidated", "CONSOLIDATED"),
    ("the_company", "PARENT_COMPANY"), ("company", "PARENT_COMPANY"),
    ("the_bank", "BANK_SOLO"), ("bank", "BANK_SOLO"),
]


def _has_column(con, table, column) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def migrate(con: sqlite3.Connection) -> dict:
    s = {"columns_added": [], "dim": 0, "map": 0}
    con.execute("""CREATE TABLE IF NOT EXISTS legal_entity_dim (
        legal_entity_key TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS legal_entity_map (
        label_norm TEXT PRIMARY KEY, legal_entity_key TEXT NOT NULL
            REFERENCES legal_entity_dim(legal_entity_key))""")
    con.executemany("INSERT OR REPLACE INTO legal_entity_dim VALUES (?,?,?)", LEGAL_ENTITY_DIM)
    con.executemany("INSERT OR REPLACE INTO legal_entity_map VALUES (?,?)", LEGAL_ENTITY_MAP)
    s["dim"], s["map"] = len(LEGAL_ENTITY_DIM), len(LEGAL_ENTITY_MAP)

    for tbl in ("col_dim", "cell_fact", "fact_metric"):
        if not _has_column(con, tbl, "legal_entity"):
            con.execute(f"ALTER TABLE {tbl} ADD COLUMN legal_entity TEXT")
            s["columns_added"].append(f"{tbl}.legal_entity")
    con.commit()
    return s


def stamp(con: sqlite3.Connection) -> dict:
    """Derive col_dim.legal_entity from the column's OWN label, else its PARENT
    column's label (the span header), else NULL. Then materialise onto
    cell_fact with col > table-default precedence — the same row>col>table
    cascade the geo/segment/industry axes already use."""
    lut = {r[0]: r[1] for r in con.execute("SELECT label_norm, legal_entity_key FROM legal_entity_map")}
    cols = con.execute("""
        SELECT c.doc_id, c.table_id, c.col_id, c.col_leaf_label, p.col_leaf_label
        FROM col_dim c
        LEFT JOIN col_dim p ON p.doc_id=c.doc_id AND p.table_id=c.table_id
                           AND p.col_id=c.col_parent
    """).fetchall()
    n_col = 0
    for doc_id, table_id, col_id, own, parent in cols:
        key = lut.get(normalize_row_label(own)) or lut.get(normalize_row_label(parent))
        if key:
            con.execute("UPDATE col_dim SET legal_entity=? WHERE doc_id=? AND table_id=? AND col_id=?",
                        (key, doc_id, table_id, col_id))
            n_col += 1

    # cell inherits its column's entity; everything else is the consolidated
    # default. An explicit default (not NULL) is what lets a query say
    # "consolidated only" without special-casing NULL.
    con.execute("""
        UPDATE cell_fact SET legal_entity = COALESCE(
            (SELECT c.legal_entity FROM col_dim c
              WHERE c.doc_id=cell_fact.doc_id AND c.table_id=cell_fact.table_id
                AND c.col_id=cell_fact.col_id), ?)""", (DEFAULT_ENTITY,))
    n_cell = con.execute("SELECT COUNT(*) FROM cell_fact WHERE legal_entity IS NOT NULL").fetchone()[0]
    n_non_consol = con.execute(
        "SELECT COUNT(*) FROM cell_fact WHERE legal_entity <> ?", (DEFAULT_ENTITY,)).fetchone()[0]
    con.commit()
    return {"col_dim_stamped": n_col, "cell_fact_stamped": n_cell,
            "cell_fact_non_consolidated": n_non_consol}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    m = migrate(con)
    print(f"columns added : {m['columns_added'] or '(already present)'}")
    print(f"legal_entity_dim / map seeded: {m['dim']} / {m['map']}")
    s = stamp(con)
    print(f"col_dim stamped              : {s['col_dim_stamped']}")
    print(f"cell_fact stamped            : {s['cell_fact_stamped']:,}")
    print(f"  of which NON-consolidated  : {s['cell_fact_non_consolidated']:,}")
    con.close()


if __name__ == "__main__":
    main()
