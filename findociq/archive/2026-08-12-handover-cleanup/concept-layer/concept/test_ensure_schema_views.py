"""Production-path regression test for the view-clobber bug found during the
2026-08-03 pre-flight pass: `concept.load_dictionary.ensure_schema()` is the
FIRST thing `concept/run.py` does, and `concept/run.py` is STEP 4a of
`run_doc.py` -- the standard production ingest path. `ensure_schema()` used to
carry its own, independent, pre-merge copy of the v_cell/v_cell_leaf/
v_cell_sumsafe/v_cell_flat DDL (no concept_key_human, no identity_source, no
period_source/period_end/period_label) and rebuilt it UNCONDITIONALLY on
every call -- so every real document ingest silently reverted the merged
migration's views. `test_migrate_serving_views.py` covers
`mapping.migrate_serving_views.migrate()` directly; this test covers the
OTHER call site that rebuilds the same views, via the actual production entry
point (`ensure_schema()`), so a future re-duplication of the view DDL at
either call site is caught here.

    python3 findociq/pipeline/concept/test_ensure_schema_views.py
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path

from concept.load_dictionary import ensure_schema  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_SRC_DB = _REPO / "findociq" / "db" / "compiled_fs.db"

_MERGED_COLUMNS = ("identity_source", "period_source", "period_end", "period_label")

_fail = 0


def check(name, cond, detail=""):
    global _fail
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        _fail += 1


def _view_snapshot(con: sqlite3.Connection) -> dict:
    snap = {}
    for view in ("v_cell", "v_cell_leaf", "v_cell_sumsafe", "v_cell_flat"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({view})")]
        rows = con.execute(f"SELECT * FROM {view} ORDER BY rowid").fetchall()
        snap[view] = {"columns": cols, "row_count": len(rows),
                      "digest": hashlib.sha256(repr(rows).encode()).hexdigest()}
    return snap


def main() -> None:
    if not _SRC_DB.exists():
        print(f"SKIP: {_SRC_DB} not found")
        return

    tmp_dir = Path(tempfile.mkdtemp(prefix="ensure_schema_views_test_"))
    tmp_db = tmp_dir / "compiled_fs.scratch.db"
    shutil.copyfile(_SRC_DB, tmp_db)

    con = sqlite3.connect(tmp_db)
    try:
        print("run 1 — ensure_schema() as run_doc.py STEP 4a would call it")
        ensure_schema(con)
        snap1 = _view_snapshot(con)
        for view, s in snap1.items():
            check(f"{view}: has rows after run 1", s["row_count"] > 0, f"row_count={s['row_count']}")
            for col in _MERGED_COLUMNS:
                check(f"{view}: carries merged column {col!r} after run 1", col in s["columns"])

        print("\nrun 2 — same connection, simulating the next document's STEP 4a")
        ensure_schema(con)
        snap2 = _view_snapshot(con)
        for view in snap1:
            check(f"{view}: columns unchanged across double run",
                  snap1[view]["columns"] == snap2[view]["columns"],
                  f"before={snap1[view]['columns']} after={snap2[view]['columns']}")
            check(f"{view}: content unchanged across double run (digest)",
                  snap1[view]["digest"] == snap2[view]["digest"])
    finally:
        con.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n{'ALL PASS' if _fail == 0 else f'{_fail} FAILED'}")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
