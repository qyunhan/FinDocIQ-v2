"""Replay pass2 load from the stored audit artifacts into a FRESH database.

Non-destructive: writes a new DB and never touches compiled_fs.db (which is
git-tracked and serves the deployed dashboard).

WHY: the loader fixes of 2026-08-05 (printed-parent consumption, the
header-vs-terminal total discriminator, row-scoped width overflow) only take
effect at LOAD time. compiled_fs.db was loaded by the pre-fix code, so it still
carries 1,055 orphaned rows, the mis-parented DBS 3Q25 ECL rows, and is missing
the three tables that the OCBC 4Q25 media-release width-overflow abort destroyed.
Replaying from the tracked parsed.json/meta.json artifacts materialises all three
fixes against real data, with no Gemini calls and no re-extraction.

Replay coverage is complete: of the 25 documents in `document`, only two have no
stored audit artifacts (DBS_2Q22_pillar3, DBS_4Q22_pillar3) and BOTH contribute
zero rows to table_t, so nothing is lost.

`document` and `section` are load INPUTS, not outputs — they are seeded verbatim
from the source DB before replaying, mirroring what run_doc does on a real ingest.

    python3 findociq/tools/replay_load.py --out findociq/db/compiled_reload.db
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PIPELINE = REPO / "findociq/pipeline"
SRC = REPO / "findociq/db/compiled_fs.db"
SCHEMA = REPO / "findociq/schema/schema_v7.sql"
OUTPUTS = REPO / "findociq/outputs"


def audit_roots() -> dict:
    """doc_id (normalised) -> audit dir. Directory names sometimes carry spaces
    where doc_id uses underscores, so match on the normalised form.

    ONE doc_id can have SEVERAL audit roots — `DBS_1Q26_trading_update` exists
    under both `outputs/fs/` and `outputs/pillar3/`. They are not equivalent:
    the fs copy carries geometry side-cars, the pillar3 copy does not. The old
    `setdefault` took whichever the glob yielded first, i.e. filesystem order,
    and silently picked the geometry-less copy — so 1Q26's income table loaded on
    the model path with its phantom twin rows unmerged (`Commercial book total
    income` twice) and every line mis-parented under `Markets trading income`.

    RICHEST ARTIFACT WINS, deterministically: rank by how many of the root's
    units carry a geometry side-car, then by unit count, then by path. General
    rule, no per-document special case."""
    cand: dict[str, list] = {}
    for p in OUTPUTS.glob("*/*/audit/*"):
        if p.is_dir():
            cand.setdefault(p.name.replace(" ", "_"), []).append(p)
    out = {}
    for doc, paths in cand.items():
        out[doc] = max(paths, key=_artifact_rank)
    return out


def _artifact_rank(root: Path) -> tuple:
    """(units with geometry, total units, path) — higher is better."""
    parsed = list(root.glob("*/parsed.json"))
    geom = 0
    for q in parsed:
        try:
            if (json.loads(q.read_text()) or {}).get("geometry"):
                geom += 1
        except Exception:
            pass
    return (geom, len(parsed), str(root))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--out", default=str(REPO / "findociq/db/compiled_reload.db"))
    ap.add_argument("--only", default=None, help="replay a single doc_id")
    args = ap.parse_args(argv)

    dst = Path(args.out)
    if dst.exists():
        dst.unlink()

    src = sqlite3.connect(f"file:{args.src}?mode=ro", uri=True)
    con = sqlite3.connect(dst)
    con.executescript(SCHEMA.read_text())
    # document + section are load INPUTS — seed them verbatim.
    for t, n in (("document", 5), ("section", 8)):
        rows = list(src.execute(f"SELECT * FROM {t}"))
        con.executemany(f"INSERT OR REPLACE INTO {t} VALUES ({','.join('?'*n)})", rows)
    con.commit()
    docs = [r[0] for r in src.execute("SELECT doc_id FROM document ORDER BY doc_id")]
    before = {r[0]: r[1] for r in src.execute(
        "SELECT d.doc_id, (SELECT COUNT(*) FROM table_t t WHERE t.doc_id=d.doc_id) "
        "FROM document d")}
    src.close()
    con.close()

    sys.path.insert(0, str(PIPELINE))
    import run_doc  # noqa: E402

    roots = audit_roots()
    ok = skipped = failed = 0
    warn_total = 0
    for doc in docs:
        if args.only and doc != args.only:
            continue
        root = roots.get(doc)
        if root is None:
            print(f"  SKIP  {doc[:46]:46s} (no audit artifacts; had {before.get(doc,0)} tables)")
            skipped += 1
            continue
        try:
            summary = run_doc.load_doc(dst, doc, root)
            warn_total += len(summary.get("warnings", []))
            ok += 1
        except SystemExit as e:
            print(f"  FAIL  {doc[:46]:46s} {e}")
            failed += 1
        except Exception as e:                       # noqa: BLE001
            print(f"  FAIL  {doc[:46]:46s} {type(e).__name__}: {str(e)[:90]}")
            failed += 1

    print(f"\n  loaded {ok} docs, skipped {skipped}, failed {failed}, warnings {warn_total}")
    con = sqlite3.connect(dst)
    for t in ("table_t", "row_dim", "col_dim", "cell_fact"):
        print(f"  {t:10s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    con.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
