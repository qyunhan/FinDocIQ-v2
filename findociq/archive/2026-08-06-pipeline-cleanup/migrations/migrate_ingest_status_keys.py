"""migrate_ingest_status_keys.py — one-time rekey of ingest_status.source_file
to the bare canonical key K = "<folder>/<file>.pdf" (see source_store.py).

Old rows stored `findociq/data/sources/<...possibly nested...>/<file>.pdf`;
the pipeline now keys by K. Idempotent: safe to run more than once.

    python3 findociq/pipeline/migrate_ingest_status_keys.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

_FOLDERS = ("financial_statements", "pillar3")


def rekey(old: str) -> str:
    p = old.replace("\\", "/")
    for marker in ("findociq/data/sources/", "data/sources/"):
        if marker in p:
            p = p.split(marker, 1)[1]
            break
    parts = [seg for seg in p.split("/") if seg]
    folder = parts[0] if parts and parts[0] in _FOLDERS else "financial_statements"
    filename = parts[-1] if parts else p
    return f"{folder}/{filename}"


def migrate(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT source_file FROM ingest_status").fetchall()
        changed = 0
        for (old,) in rows:
            new = rekey(old)
            if new != old:
                con.execute(
                    "UPDATE ingest_status SET source_file = ? WHERE source_file = ?",
                    (new, old))
                changed += 1
        con.commit()
        return changed
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    default_db = Path(__file__).resolve().parents[1] / "db" / "compiled_fs.db"
    ap.add_argument("--db", default=str(default_db))
    args = ap.parse_args(argv)
    n = migrate(args.db)
    print(f"rekeyed {n} ingest_status row(s) to canonical key K")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
