"""migrate_add_equity_component_dim — give the equity column axis a dimension.

A statement of changes in equity decomposes ACROSS THE PAGE: its columns are
equity components, not periods. `geo_dim` / `segment_dim` / `industry_dim` cover
the other three axes; this one had nothing behind it, so all 33 such columns in
the corpus carried no key and the axis could not be addressed or rolled up.

Adds `equity_component_dim` + `equity_component_map` to an EXISTING database and
seeds them from `schema/schema_v7.sql` — the schema file is the single source of
truth, this script only replays its two INSERTs so a DB built before them catches
up. Idempotent: tables are created IF NOT EXISTS and rows are UPSERTed.

Optionally stamps `col_dim.equity_key` (added if missing) on every column whose
normalised label matches, using `load_v7.geo_norm` — the same normalisation
`geo_map` / `segment_map` lookups already use, so matching is exact full-label
equality, never substring.

    python3 findociq/pipeline/mapping/migrate_add_equity_component_dim.py \
        --db findociq/db/compiled_fs.db --stamp
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "findociq/pipeline"))

from mapping.Stamping.masterlist_derive import strip_footnote_markers  # noqa: E402
from pass2.load_v7 import geo_norm                            # noqa: E402


def eq_norm(label: str | None) -> str:
    """Normalise an equity column header for `equity_component_map` lookup.

    `geo_norm` (the convention for geo/segment/industry) plus a unit strip. The
    other three axes read identity off SPANNING headers, which carry no unit;
    equity reads it off the LEAF columns, and UOB prints those as 'Retained
    earnings $m'. `strip_footnote_markers` already removes unit suffixes, and it
    is what `propose_masterlist` uses for the same labels, so the map is
    authored against one form rather than one per bank's typography.
    """
    return geo_norm(strip_footnote_markers(label or ""))

SCHEMA = REPO / "findociq/schema/schema_v7.sql"

DDL = """
CREATE TABLE IF NOT EXISTS equity_component_dim (
  equity_key TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  eq_level   TEXT NOT NULL CHECK (eq_level IN ('component','subtotal','total')),
  parent_eq  TEXT REFERENCES equity_component_dim(equity_key)
);
CREATE TABLE IF NOT EXISTS equity_component_map (
  label_norm TEXT PRIMARY KEY,
  equity_key TEXT NOT NULL REFERENCES equity_component_dim(equity_key)
);
"""


def _seed_statements() -> list[str]:
    """The two INSERTs from schema_v7.sql, verbatim. Read rather than duplicated
    so the schema file stays the one place the members are authored."""
    # Strip `--` comments FIRST. The seed is heavily annotated and a comment
    # ending '... (DBS, OCBC);' terminates a naive `.*?\);` match mid-statement.
    sql = "\n".join(re.sub(r"--.*$", "", ln) for ln in
                    SCHEMA.read_text(encoding="utf-8").splitlines())
    out = []
    for table in ("equity_component_dim", "equity_component_map"):
        m = re.search(rf"INSERT INTO {table} \(.*?\);", sql, re.S)
        if not m:
            sys.exit(f"no INSERT INTO {table} found in {SCHEMA}")
        out.append(m.group(0))
    return out


def migrate(con: sqlite3.Connection, *, stamp: bool = False) -> dict:
    con.executescript(DDL)
    for stmt in _seed_statements():
        # UPSERT so a re-run refreshes labels without duplicating rows.
        con.execute(stmt.replace("INSERT INTO", "INSERT OR REPLACE INTO", 1))
    con.commit()

    stats = {
        "members": con.execute("SELECT count(*) FROM equity_component_dim").fetchone()[0],
        "aliases": con.execute("SELECT count(*) FROM equity_component_map").fetchone()[0],
        "stamped": 0, "unmatched": {},
    }
    if not stamp:
        return stats

    cols = [r[1] for r in con.execute("PRAGMA table_info(col_dim)")]
    if "equity_key" not in cols:
        con.execute("ALTER TABLE col_dim ADD COLUMN equity_key TEXT")

    amap = dict(con.execute("SELECT label_norm, equity_key FROM equity_component_map"))
    # Scope to the exhibits whose columns ARE this axis. A 'Total' column on a
    # segment table is SEG_TOTAL, not EQ_ATTRIBUTABLE — same collision geo and
    # segment already have on 'Others', and the fix is the same: stamp per axis,
    # inside the axis's own tables.
    rows = con.execute("""
        SELECT c.doc_id, c.table_id, c.col_id, c.col_leaf_label, c.col_leaf_label_clean
        FROM col_dim c JOIN table_t t
          ON t.doc_id = c.doc_id AND t.table_id = c.table_id
        WHERE t.section_id LIKE '%changes_in_equity%'
    """).fetchall()
    for doc_id, table_id, col_id, lbl, lbl_clean in rows:
        key = amap.get(eq_norm(lbl_clean or lbl))
        if key:
            con.execute("UPDATE col_dim SET equity_key=? WHERE doc_id=? AND table_id=? "
                        "AND col_id=?", (key, doc_id, table_id, col_id))
            stats["stamped"] += 1
        elif (lbl_clean or lbl):
            k = eq_norm(lbl_clean or lbl)
            stats["unmatched"][k] = stats["unmatched"].get(k, 0) + 1
    con.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "findociq/db/compiled_fs.db"))
    ap.add_argument("--stamp", action="store_true",
                    help="also write col_dim.equity_key on changes-in-equity tables")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    s = migrate(con, stamp=args.stamp)
    print(f"equity_component_dim: {s['members']} members, {s['aliases']} aliases")
    if args.stamp:
        print(f"col_dim.equity_key stamped on {s['stamped']} columns")
        if s["unmatched"]:
            print("  unmatched labels (period/unit headers are expected here):")
            for k, v in sorted(s["unmatched"].items(), key=lambda x: -x[1]):
                print(f"     {v:>3}  {k[:70]!r}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
