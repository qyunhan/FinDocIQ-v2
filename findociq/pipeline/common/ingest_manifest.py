"""ingest_manifest.py — the manifest-driven orchestrator: full ingestion of
every doc already on disk, reconciled against `data/sources/manifest.csv`.

This is the top-level entry point over the already-downloaded corpus (vs
`ingest_quarter.py`, which scrapes one bank+period fresh). It does not
reimplement routing / TOC / Gemini / DB-load — that's `run_doc.py`'s job, one
doc at a time (route -> TOC -> extract -> load -> verify -> fact_metric ->
ratios). This script:

    1. Runs `run_doc.py --all`  — sweeps every PDF under
       data/sources/{financial_statements,pillar3} not yet in the DB, one
       BigQuery sync at the end of the whole batch (not per doc).
    2. Reconciles the resulting DB against `manifest.csv`'s planned
       (bank, period, family) checklist: fills `have(y/n)` + `file_notes` for
       every row now matched by an ingested doc, and prints a gap report for
       `available` rows still unmatched (docs to go source).

Matching is entirely CONTENT-derived (DB institution/doc_family/doc_period),
never by filename — manifest.csv's `target_filename` is aspirational (banks
routinely publish under a different name than planned) and must never gate
ingestion.

    python3 findociq/pipeline/ingest_manifest.py
    python3 findociq/pipeline/ingest_manifest.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FINDOCIQ = REPO / "findociq"
sys.path.insert(0, str(FINDOCIQ / "pipeline"))
from stage1_extract.route.family import INSTITUTIONS  # noqa: E402  bank-code -> full name

DEFAULT_MANIFEST = FINDOCIQ / "data" / "sources" / "manifest.csv"
DEFAULT_DB = FINDOCIQ / "db" / "compiled_fs.db"
RUN_DOC = "findociq/pipeline/run_doc.py"
# Subprocesses must run under THIS interpreter (the venv with the pipeline
# deps), never whatever bare `python3` resolves to on PATH.
PYTHON = sys.executable

_BANK_OF = {full: code for code, full in INSTITUTIONS.items()}
_DB_FAMILY_TO_MANIFEST = {"financial_stmt": "fs", "pillar3": "pillar3"}


def _quarter_label(doc_period: str) -> str:
    """'2026-03-31' -> '1Q26' (manifest's period format)."""
    year, month, _ = doc_period.split("-")
    quarter = (int(month) - 1) // 3 + 1
    return f"{quarter}Q{year[2:]}"


def _run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=REPO).returncode


def _sweep(args) -> int:
    # --defer-db-steps: concepts/fact_metric/ratios/sync_bq are O(whole-DB) and
    # would otherwise re-run once per doc across this sweep; --all passes the
    # flag through to every per-doc run_one() call (see run_doc.py run_all()).
    # The deferred steps are then run ONCE, after the sweep, via --db-steps-only.
    cmd = [PYTHON, RUN_DOC, "--all", "--db", args.db, "--defer-db-steps"]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.force:
        cmd.append("--force")
    if args.batch:
        cmd.append("--batch")
    if args.no_ipv4_shim:
        cmd.append("--no-ipv4-shim")
    if args.no_sync_bq:
        cmd.append("--no-sync-bq")
    if args.bank:
        cmd.extend(["--bank", args.bank])
    rc = _run(cmd)
    if args.dry_run:
        return rc

    db_steps_cmd = [PYTHON, RUN_DOC, "--db-steps-only", "--db", args.db]
    if args.no_sync_bq:
        db_steps_cmd.append("--no-sync-bq")
    print(f"\n{'='*70}\n[db-steps-only] concepts -> fact_metric -> ratios -> "
          f"sync_bq\n{'='*70}")
    db_rc = _run(db_steps_cmd)
    return rc or db_rc


def _db_coverage(db_path: Path) -> dict[tuple[str, str, str], list[str]]:
    """(bank, period, family) -> [doc_id, ...] for every doc currently loaded.
    Docs whose institution isn't in the registry, or whose family isn't
    fs/pillar3, are silently excluded (nothing in manifest.csv could match
    them anyway)."""
    coverage: dict[tuple[str, str, str], list[str]] = {}
    if not db_path.exists():
        return coverage
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT doc_id, institution, doc_family, doc_period FROM document"
        ).fetchall()
    finally:
        con.close()
    for doc_id, institution, doc_family, doc_period in rows:
        bank = _BANK_OF.get(institution)
        family = _DB_FAMILY_TO_MANIFEST.get(doc_family)
        if bank is None or family is None or not doc_period:
            continue
        key = (bank, _quarter_label(doc_period), family)
        coverage.setdefault(key, []).append(doc_id)
    return coverage


def _read_manifest(path: Path) -> list[dict]:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return list(csv.DictReader(lines))


def _write_manifest(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _reconcile(manifest_path: Path, db_path: Path, update: bool,
               bank: str | None = None) -> None:
    rows = _read_manifest(manifest_path)
    fieldnames = list(rows[0].keys()) if rows else []
    coverage = _db_coverage(db_path)

    n_matched = n_gap = 0
    gaps: list[dict] = []
    for row in rows:
        key = (row["bank"], row["period"], row["family"])
        doc_ids = coverage.get(key)
        if doc_ids:
            n_matched += 1
            row["have(y/n)"] = "y"
            row["file_notes"] = ",".join(sorted(doc_ids))
        elif row["availability"] == "available":
            n_gap += 1
            gaps.append(row)

    print(f"\n{'#'*60}\n# MANIFEST RECONCILIATION" + (f"  (bank={bank})" if bank else ""))
    print(f"#   rows matched (have=y):      {n_matched}/{len(rows)}")
    print(f"#   available but NOT ingested: {n_gap}")
    print("#" * 60)
    shown = [r for r in gaps if not bank or r["bank"] == bank]
    if shown:
        print("\nStill need to source (availability=available, no matching doc):")
        for r in shown:
            print(f"   {r['bank']:5} {r['period']:6} {r['family']:8} "
                  f"{r['doc_type']:24} (expected ~{r['target_filename']})")

    if update and rows:
        _write_manifest(manifest_path, rows, fieldnames)
        print(f"\n[manifest] wrote {manifest_path.relative_to(REPO)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--bank", default=None,
                    help="restrict the sweep + gap report to DBS|OCBC|UOB")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan only (passthrough to run_doc --all --dry-run); "
                         "reconciliation still reports CURRENT db coverage")
    ap.add_argument("--force", action="store_true",
                    help="re-run docs already loaded (passthrough to run_doc --all)")
    ap.add_argument("--batch", action="store_true",
                    help="Gemini Batch API for extraction (passthrough)")
    ap.add_argument("--no-ipv4-shim", action="store_true",
                    help="passthrough to run_doc.py (Cloud Shell paddle workaround)")
    ap.add_argument("--no-sync-bq", action="store_true",
                    help="skip the BigQuery sync at the end of the sweep")
    ap.add_argument("--no-manifest-update", action="store_true",
                    help="report the reconciliation but don't write manifest.csv")
    args = ap.parse_args(argv)

    rc = _sweep(args)

    _reconcile(Path(args.manifest).resolve(), Path(args.db).resolve(),
               update=not args.no_manifest_update and not args.dry_run,
               bank=args.bank.strip().upper() if args.bank else None)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
