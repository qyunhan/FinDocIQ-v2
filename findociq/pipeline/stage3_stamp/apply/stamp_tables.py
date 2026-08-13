"""stamp_tables — drive resolve_canonical_leaf table by table.

Resolves rows against data/derived/masterlist/ and (with --write) stamps
`row_dim.canonical_leaf_id`. Defaults to a DRY RUN and to a COPY of the DB, so
nothing is mutated by accident.

    # dry run, EVERY bank the masterlist declares (the normal case)
    python3 findociq/pipeline/stage3_stamp/apply/stamp_tables.py --db findociq/db/compiled_fs.db

    # actually stamp, into an explicit output DB (the source is never modified)
    python3 .../stamp_tables.py --db findociq/db/compiled_fs.db --out /tmp/stamped.db --write

    # narrowed — one bank, some types. `--bank` is a FILTER, not a requirement:
    # locate_tables already matches all (bank, table_type_id) pairs by content,
    # so omitting it stamps everything in one pass rather than needing a loop.
    python3 .../stamp_tables.py --bank DBS --tables FS_INCOME_SELECTED,FS_RATIOS_KEY

Table location is NOT hardcoded: `RCL.locate_tables()` finds the raw table_id by
CONTENT — a table is the one whose printed row paths match the masterlist's
`full_path` values, scoped first to the document's `source_family`.

(It used to resolve the printed caption against a seed CSV. That was retired on
2026-08-06 — caption matching fused DBS's two 'Selected balance sheet items'
tables, leaving 12 rows unresolved and 3 wearing the wrong table's ids, where
content matching scores the geography table 0. See
archive/2026-08-06-masterlist-retirement/README.md.)
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "findociq/pipeline"))

from stage3_stamp.resolve import resolve_canonical_leaf as RCL  # noqa: E402

# THE ONLY SOURCE OF canonical_leaf_id. No generated leaf list is readable from
# this path — table location, seed loading and matching all live in RCL.
MASTERLIST = REPO / "findociq/data/derived/masterlist"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "findociq/db/compiled_v2.db"))
    ap.add_argument("--out", default=None,
                    help="output DB (default: <db>_stamped.db). The source DB is "
                         "never modified — it is copied first.")
    ap.add_argument("--bank", default="",
                    help="restrict to ONE bank; empty (the default) = EVERY bank "
                         "the masterlist declares. `locate_tables` already matches "
                         "all (bank, table_type_id) pairs against the DB by "
                         "content, so filtering to one bank only threw that work "
                         "away and made the caller loop — re-running the whole "
                         "location pass once per bank for no gain.")
    ap.add_argument("--docs", default="",
                    help="comma-separated doc_ids to restrict to; empty = every "
                         "document in the DB")
    ap.add_argument("--tables", default="",
                    help="comma-separated table_type_ids; empty = all for the bank")
    ap.add_argument("--write", action="store_true", help="actually stamp (default: dry run)")
    args = ap.parse_args(argv)

    src = Path(args.db)
    dst = Path(args.out) if args.out else src.with_name(src.stem + "_stamped.db")
    if args.write:
        if dst.resolve() == src.resolve():
            print(f"refusing to write into the source DB {src}")
            return 2
        shutil.copyfile(src, dst)
        print(f"  copied {src.name} -> {dst.name}")
    target = dst if args.write else src

    master = RCL.load_masterlist()          # data/derived/masterlist/*.csv
    want = {t.strip() for t in args.tables.split(",") if t.strip()}

    con = sqlite3.connect(target)
    cur = con.cursor()

    # Locate tables BY CONTENT against the masterlist — no seed, no captions.
    docs = {d.strip() for d in args.docs.split(",") if d.strip()} or None
    hits = RCL.locate_tables(con, master, doc_ids=docs)

    grand_res = grand_tot = grand_stamped = 0
    srcs = sorted({e["source"] for e in master.values()})
    print(f"\n  masterlist: {', '.join(srcs)}"
          f"   mode: {'WRITE' if args.write else 'DRY RUN'}   db: {target.name}\n")
    # BANK IS NOT AN ARGUMENT, it is a property of the masterlist row. Every bank
    # is stamped in ONE pass unless --bank narrows it; ordered by bank, then by
    # the type's first printed ordinal so a run reads like the filings do.
    keys = sorted(k for k in master if not args.bank or k[0] == args.bank)
    keys.sort(key=lambda k: (k[0], min(o for o, _c, _l in master[k]["ordered"])))
    for (bank, tt) in keys:
        if want and tt not in want:
            continue
        refs = hits.get((bank, tt), [])
        entry = master[(bank, tt)]
        if not refs:
            print(f"  {tt:26s} SKIP — no table in this DB matches its leaves")
            continue
        for t in refs:
            # table_type_id is written HERE as well as in load_v7._stamp_identity.
            # Without it a masterlist edit forced a full re-load just to re-stamp:
            # re-reading every parsed.json and re-deriving parents, periods, sums
            # and units that did not change. The masterlist changes far more often
            # than the extraction does, so re-stamping must be cheap and standalone.
            if args.write:
                cur.execute("UPDATE table_t SET table_type_id = ? "
                            "WHERE doc_id = ? AND table_id = ?",
                            (tt, t["doc_id"], t["table_id"]))
            results, new_aliases = RCL.resolve_table(
                cur, t["doc_id"], t["table_id"], bank, tt, entry,
                alias_map=RCL.load_aliases(cur, bank, tt),
                seed_caption=t["title"],
                discriminator=t.get("discriminator"))
            if not results:
                continue
            n_ok = sum(1 for r in results if r["outcome"] != "unresolved")
            stamped = RCL.stamp_into_db(con, t["doc_id"], t["table_id"], results,
                                        bank=bank, table_type_id=tt,
                                        new_aliases=new_aliases,
                                        master_ids=entry["ids"],
                                        dry_run=not args.write)
            grand_res += n_ok
            grand_tot += len(results)
            grand_stamped += stamped
            print(f"  {tt:26s} {n_ok:3d}/{len(results):3d} resolved   "
                  f"{t['doc_id'][:30]:30s} p{t['page']}  "
                  f"(content {t['matched']}/{t['n_leaves']})")
            sugg = RCL.suggest_for_unresolved(results, entry["ordered"])
            for r in results:
                if r["outcome"] == "unresolved":
                    s = sugg.get(r["row_id"]) or []
                    print(f"      UNRESOLVED r{r['row_id']}: {r['label'][:44]!r}")
                    print(f"          printed path: {r['raw_path'][:70]}")
                    for o, cid, lab in (s or [])[:3]:
                        print(f"          candidate  : [{o}] {cid}")
    if args.write:
        con.commit()
    con.close()
    pct = 100 * grand_res / grand_tot if grand_tot else 0
    print(f"\n  TOTAL {grand_res}/{grand_tot} resolved ({pct:.0f}%)"
          f"   stamped={grand_stamped}{'' if args.write else ' (dry run — nothing written)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
