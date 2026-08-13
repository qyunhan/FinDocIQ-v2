"""retry_worker.py — unattended ingest sweep (Phase B/C of the ingest-status
plan, `~/.claude/plans/lively-beaming-twilight.md`). This is the Cloud Run
Job body triggered on a schedule (Cloud Workflows + Cloud Scheduler, Phase C):

    python3 findociq/pipeline/common/retry_worker.py

On each run:
  1. Pull compiled_fs.db + data/sources/ from the GCS bucket (the durable
     checkpoint — a container has no persistent disk between job executions).
  2. Sweep every FS/Pillar3 PDF under data/sources/ and ask
     ingest_status.should_retry() whether it's still worth a run_doc.py
     attempt. This covers BOTH brand-new arrivals (no ingest_status row yet)
     AND docs that got SOME table_t rows loaded but failed a later stage —
     the "looks done forever" gap flagged in the plan, since run_doc.py --all
     only ever checks table_t membership, never ingest_status. Docs already
     in table_t with no ingest_status row (loaded before Phase A shipped) are
     treated as done, not re-run.
  3. Run the existing `run_doc.py --pdf <file>` per eligible doc (no
     duplicated pipeline logic) with --no-ipv4-shim: this container has no
     IPv6 blackhole, and the shim's sitecustomize.py would otherwise shadow
     the paddle/mkldnn fix baked into the image at /opt/paddle-fix (same
     PYTHONPATH-shadowing bug documented in the 2026-07-24 ingest handoff).
  4. Push compiled_fs.db back to GCS, then run the existing sync_bq.py once
     for the whole sweep (matches run_doc.py --all's own convention).

Env:
    GCS_BUCKET   gs://<bucket> holding data/sources/ + db/compiled_fs.db
                 (default: findociq-sources-igc2026-team08-6311)
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from google.cloud import storage

REPO = Path(__file__).resolve().parents[3]          # pipeline -> findociq -> repo
FINDOCIQ = REPO / "findociq"
DB_PATH = FINDOCIQ / "db" / "compiled_fs.db"
SOURCES_ROOT = FINDOCIQ / "data" / "sources"
RUN_DOC = "findociq/pipeline/run_doc.py"
SYNC_BQ = "findociq/pipeline/ingest/sync_bq.py"

DEFAULT_BUCKET = "findociq-sources-igc2026-team08-6311"
GCS_DB_BLOB = "db/compiled_fs.db"
GCS_SOURCES_PREFIX = "data/sources/"

sys.path.insert(0, str(FINDOCIQ / "pipeline"))
from common import ingest_status                                          # noqa: E402
from common import source_store                                           # noqa: E402
from run_doc import SOURCE_ROOTS, doc_id_for, _loaded_doc_ids  # noqa: E402


def _source_file_for(pdf) -> str:
    return source_store.key_for(pdf)


def _bucket(name: str):
    return storage.Client().bucket(name)


def pull_from_gcs(bucket_name: str) -> None:
    bkt = _bucket(bucket_name)
    db_blob = bkt.blob(GCS_DB_BLOB)
    if db_blob.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_blob.download_to_filename(str(DB_PATH))
        print(f"pulled gs://{bucket_name}/{GCS_DB_BLOB} -> {DB_PATH}")
    else:
        print(f"no {GCS_DB_BLOB} in gs://{bucket_name} yet; "
              f"starting from whatever's baked into the image (if anything)")

    keys = source_store.list_sources()
    for key in keys:
        source_store.materialize(key)
    print(f"pulled {len(keys)} source file(s) -> {SOURCES_ROOT}")


def push_to_gcs(bucket_name: str) -> None:
    bkt = _bucket(bucket_name)
    bkt.blob(GCS_DB_BLOB).upload_from_filename(str(DB_PATH))
    print(f"pushed {DB_PATH} -> gs://{bucket_name}/{GCS_DB_BLOB}")


def _has_status_row(db_path: Path, source_file: str) -> bool:
    if not db_path.exists():
        return False
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(
            "SELECT 1 FROM ingest_status WHERE source_file = ?", (source_file,))
        return cur.fetchone() is not None
    finally:
        con.close()


def eligible_pdfs(db_path: Path, max_attempts: int) -> list[Path]:
    loaded = _loaded_doc_ids(db_path)
    pdfs = sorted({p for root in SOURCE_ROOTS if root.exists() for p in root.rglob("*.pdf")})
    todo = []
    for pdf in pdfs:
        source_file = _source_file_for(pdf)
        if doc_id_for(pdf) in loaded and not _has_status_row(db_path, source_file):
            continue  # loaded before ingest_status existed (pre-Phase-A) -> done
        if ingest_status.should_retry(str(db_path), source_file, max_attempts=max_attempts):
            todo.append(pdf)
    return todo


def run_one(pdf: Path, *, db_path: Path, batch: bool) -> int:
    cmd = [sys.executable, RUN_DOC, "--pdf", str(pdf), "--db", str(db_path),
           "--no-ipv4-shim", "--no-sync-bq"]
    if batch:
        cmd.append("--batch")
    print(f"\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=REPO).returncode


def sync_bq(db_path: Path) -> None:
    cmd = [sys.executable, SYNC_BQ, "--db", str(db_path)]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=REPO, check=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", default=os.environ.get("GCS_BUCKET", DEFAULT_BUCKET))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--batch", dest="batch", action="store_true", default=True,
                     help="Gemini Batch API for extraction (default ON; "
                          "an unattended sweep should minimize cost)")
    ap.add_argument("--no-batch", dest="batch", action="store_false")
    ap.add_argument("--dry-run", action="store_true",
                     help="print the eligible-doc plan; run/push/sync nothing")
    ap.add_argument("--no-sync-bq", action="store_true")
    ap.add_argument("--no-push", action="store_true",
                     help="skip pushing the DB back to GCS (local testing)")
    args = ap.parse_args(argv)

    db_path = Path(args.db).resolve()

    pull_from_gcs(args.bucket)

    todo = eligible_pdfs(db_path, args.max_attempts)
    print(f"\n{len(todo)} doc(s) eligible for a run_doc.py attempt this sweep")
    if args.dry_run:
        for pdf in todo:
            print(f"   [todo]  {os.path.relpath(pdf, REPO)}")
        return 0

    results: list[tuple[str, int]] = []
    for i, pdf in enumerate(todo, 1):
        print(f"\n{'='*70}\n[{i}/{len(todo)}] {os.path.relpath(pdf, REPO)}\n{'='*70}")
        rc = run_one(pdf, db_path=db_path, batch=args.batch)
        results.append((doc_id_for(pdf), rc))

    print("\n" + "#" * 60 + "\n# RETRY SWEEP SUMMARY")
    for doc_id, rc in results:
        print(f"#   {'OK  ' if rc == 0 else 'FAIL'}  {doc_id}")
    print(f"#   eligible this sweep: {len(todo)}")
    print("#" * 60)

    if not args.no_push:
        push_to_gcs(args.bucket)
    if not args.no_sync_bq:
        sync_bq(db_path)

    return 1 if any(rc for _, rc in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
