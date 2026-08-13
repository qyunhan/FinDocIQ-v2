"""Idempotency test for migrate_serving_views.py -- the merged, single owner
of v_cell/v_cell_leaf/v_cell_sumsafe/v_cell_flat (replaces the two clobbering
scripts migrate_add_human_anchor_projection.py + pass2/migrate_add_period_label.py,
see docs/DECISIONS.md 2026-08-03).

Runs migrate() twice against a scratch copy of the real DB and asserts the
views' column set, row counts, and full content are byte-identical after the
second run -- a re-run must be a no-op, not a throw and not a dropped column.

    python3 findociq/pipeline/mapping/test_migrate_serving_views.py
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapping.migrate_serving_views import migrate  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_SRC_DB = _REPO / "findociq" / "db" / "compiled_fs.db"

_fail = 0


def check(name, cond, detail=""):
    global _fail
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        _fail += 1


def _snapshot(con: sqlite3.Connection) -> dict:
    snap = {}
    for view in ("v_cell", "v_cell_leaf", "v_cell_sumsafe", "v_cell_flat"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({view})")]
        rows = con.execute(f"SELECT * FROM {view} ORDER BY rowid").fetchall()
        digest = hashlib.sha256(repr(rows).encode()).hexdigest()
        snap[view] = {"columns": cols, "row_count": len(rows), "digest": digest}
    return snap


def main() -> None:
    if not _SRC_DB.exists():
        print(f"SKIP: {_SRC_DB} not found")
        return

    tmp_dir = Path(tempfile.mkdtemp(prefix="migrate_serving_views_test_"))
    tmp_db = tmp_dir / "compiled_fs.scratch.db"
    shutil.copyfile(_SRC_DB, tmp_db)

    con = sqlite3.connect(tmp_db)
    try:
        print("run 1 (fresh columns/tables expected)")
        m1 = migrate(con)
        check("run 1 completes without raising", True)
        snap1 = _snapshot(con)
        for view, s in snap1.items():
            check(f"{view}: has rows after run 1", s["row_count"] > 0, f"row_count={s['row_count']}")

        print("\nrun 2 (idempotent: no-op, not a throw, not a drop)")
        m2 = migrate(con)
        check("run 2 completes without raising", True)
        check("run 2 adds no new columns", m2["columns_added"] == [], m2["columns_added"])
        check("run 2 restamps the same row_dim count",
              m2["row_dim_stamped"] == m1["row_dim_stamped"],
              f"run1={m1['row_dim_stamped']} run2={m2['row_dim_stamped']}")
        snap2 = _snapshot(con)

        for view in snap1:
            check(f"{view}: columns unchanged across double run",
                  snap1[view]["columns"] == snap2[view]["columns"],
                  f"before={snap1[view]['columns']} after={snap2[view]['columns']}")
            check(f"{view}: row count unchanged across double run",
                  snap1[view]["row_count"] == snap2[view]["row_count"],
                  f"before={snap1[view]['row_count']} after={snap2[view]['row_count']}")
            check(f"{view}: content unchanged across double run (digest)",
                  snap1[view]["digest"] == snap2[view]["digest"])

        for expect_col in ("period_source", "period_end", "period_label", "identity_source", "concept_key"):
            check(f"v_cell carries merged column {expect_col!r}",
                  expect_col in snap1["v_cell"]["columns"])
    finally:
        con.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n{'ALL PASS' if _fail == 0 else f'{_fail} FAILED'}")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
