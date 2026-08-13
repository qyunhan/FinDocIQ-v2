"""restamp_columns — re-apply column-axis identity to an ALREADY-BUILT database.

`col_dim.canonical_col_id` is normally written at LOAD time (load_v7 stage 3,
driven by `resolve_canonical_col`). That makes a masterlist column-block edit
invisible to any database already on disk: the shipped `compiled_v2.db` carries
56 stamped columns, all geography, because it predates the resolver being wired
in. A full re-load is the clean fix, but the replay lineage currently loses 531
row-stamped leaves against the shipped build (see PROGRESS.md 2026-08-12), so
re-loading to pick up a COLUMN edit would trade one gap for a bigger one.

This applies stage 3 ALONE, in place:

    python3 findociq/pipeline/stage3_stamp/apply/restamp_columns.py \
        --db findociq/db/compiled_v2.db --write

(The curated `*_cols_curated.csv` files were promoted over the live
`*_masterlist_cols.csv` on 2026-08-12, so the DEFAULT glob is now correct and
`--masterlist-glob` is only needed to point at an alternative set.)

Every id written is copied VERBATIM from the masterlist, exactly as the loader
does — this drives the SAME `RCC.resolve_columns`, it does not reimplement the
matching. What it deliberately does NOT do is re-run `locate_tables`: the target
DB already carries `table_t.table_type_id`, so the (bank, table_type_id) pair
each column block is keyed by is read from the DB rather than re-decided. That
also means the COLUMN VETO does not apply here — a table's type is taken as
already settled, not re-litigated.

Defaults to a DRY RUN. `--write` mutates the DB in place; take a copy first.
"""
from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "findociq/pipeline"))
sys.path.insert(0, str(REPO / "findociq/app"))

from stage3_stamp.resolve import resolve_canonical_col as RCC  # noqa: E402

MASTERLIST_DIR = REPO / "findociq/data/derived/masterlist"


def bank_of(institution: str) -> str:
    """institution -> the masterlist's bank token. Reuses the app's mapping so
    'Oversea-Chinese Banking Corporation Ltd' -> OCBC rather than a prefix
    guess (which silently matches nothing for OCBC and UOB alike)."""
    from findociq_app import _bank_of
    return _bank_of(institution or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "findociq/db/compiled_v2.db"))
    ap.add_argument("--masterlist-glob", default="*_masterlist_cols.csv",
                    help="which column masterlists to read from "
                         "data/derived/masterlist (default: the live ones)")
    ap.add_argument("--write", action="store_true",
                    help="actually stamp (default: dry run)")
    args = ap.parse_args(argv)

    paths = sorted(MASTERLIST_DIR.glob(args.masterlist_glob))
    if not paths:
        print(f"no masterlists match {args.masterlist_glob!r} in {MASTERLIST_DIR}",
              file=sys.stderr)
        return 1
    col_master = RCC.load_col_members(paths)
    print(f"column blocks: {len(col_master)} (bank, table_type_id) pairs "
          f"from {', '.join(p.name for p in paths)}")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    tables = cur.execute(
        """SELECT t.doc_id, t.table_id, t.table_type_id, d.institution
           FROM table_t t JOIN document d ON d.doc_id = t.doc_id
           WHERE t.table_type_id IS NOT NULL""").fetchall()

    outcomes: collections.Counter = collections.Counter()
    stamped = 0
    per_id: collections.Counter = collections.Counter()
    no_block = set()

    for t in tables:
        bank = bank_of(t["institution"])
        entry = col_master.get((bank, t["table_type_id"]))
        if entry is None:
            no_block.add((bank, t["table_type_id"]))
            continue
        for r in RCC.resolve_columns(cur, t["doc_id"], t["table_id"],
                                     bank, t["table_type_id"], entry):
            outcomes[r["outcome"]] += 1
            if r["outcome"] != "matched":
                continue
            per_id[(bank, t["table_type_id"], r["canonical_col_id"])] += 1
            stamped += 1
            if args.write:
                cur.execute(
                    "UPDATE col_dim SET canonical_col_id = ? "
                    "WHERE doc_id = ? AND table_id = ? AND col_id = ?",
                    (r["canonical_col_id"], t["doc_id"], t["table_id"],
                     r["col_id"]))

    print(f"\ntables with a column block: "
          f"{len(tables) - len({t['table_id'] for t in tables if col_master.get((bank_of(t['institution']), t['table_type_id'])) is None})}"
          f" of {len(tables)}")
    print("column outcomes:", dict(outcomes))
    print(f"columns matched: {stamped}")
    if no_block:
        print(f"\n(bank, table_type_id) with NO column block authored: "
              f"{len(no_block)}")

    print("\nstamped ids:")
    for (bank, tt, cid), n in sorted(per_id.items()):
        print(f"  {bank:<5} {tt:<26} {cid:<34} x{n}")

    if args.write:
        con.commit()
        total = cur.execute(
            "SELECT COUNT(*) FROM col_dim WHERE canonical_col_id IS NOT NULL"
        ).fetchone()[0]
        grand = cur.execute("SELECT COUNT(*) FROM col_dim").fetchone()[0]
        print(f"\nWROTE. col_dim now {total} of {grand} columns stamped.")
    else:
        print("\nDRY RUN — nothing written. Pass --write to apply.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
