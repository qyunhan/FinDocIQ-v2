"""ingest_quarter.py — one bank, one period, end to end.

The per-bank-per-period orchestrator: the single command to drop in a new
quarter. It scrapes the bank's IR pages scoped to one period, then runs each
kept fs/pillar3 doc through run_doc (scan -> TOC -> extract -> load -> verify)
with --defer-db-steps, and finally runs run_doc --db-steps-only ONCE for the
whole batch (concepts -> fact_metric -> ratios -> sync_bq) — those four steps
are O(whole-DB), so this sweep pays for them once instead of once per doc.

    python3 findociq/pipeline/ingest_quarter.py --bank DBS --period 2026-Q2

Inputs are only bank + period. Discovery, classification, routing, and
keep/discard are all code-decided (classify/family.py + scrape_bank_ir.py) — no
per-doc human step. The as-at date is derived from the period and passed to
run_doc as --doc-period, so ingestion is robust to each bank's filename quirks
(UOB '-1q-2025', OCBC '1H25', OCBC Pillar 3 '... as at 30 september 2025').

Flags:
    --no-scrape   reuse PDFs already on disk (skip download)
    --dry-run     print the plan (docs + run_doc commands); run nothing
    --batch       pass --batch to run_doc (Gemini Batch API, ~50% cost)
    --no-sync-bq  skip the final BigQuery sync
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FS_ROOT = REPO / "findociq" / "data" / "sources" / "financial_statements"
DEFAULT_DB = REPO / "findociq" / "db" / "compiled_fs.db"
SCRAPER = "findociq/pipeline/ingest/scrape_bank_ir.py"
RUN_DOC = "findociq/pipeline/run_doc.py"
# Subprocesses must run under THIS interpreter (the venv with the pipeline
# deps), never whatever bare `python3` resolves to on PATH.
PYTHON = sys.executable

# quarter -> the doc's "as at" month/day (period-end), passed to run_doc so the
# period is correct regardless of how the source file happens to be named.
_QUARTER_END = {"Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31"}


def _parse_period(period: str) -> tuple[int, str]:
    """'2026-Q2' -> (2026, 'Q2'). Fails loud on anything else."""
    try:
        year_s, q = period.strip().upper().split("-")
        year = int(year_s)
        if q not in _QUARTER_END:
            raise ValueError
        return year, q
    except (ValueError, AttributeError):
        sys.exit(f"bad --period {period!r}; expected 'YYYY-Qn', e.g. 2026-Q2")


def _run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=REPO).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bank", required=True, help="DBS|OCBC|UOB")
    ap.add_argument("--period", required=True, help="e.g. 2026-Q2")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="target schema_v7 sqlite DB")
    ap.add_argument("--no-scrape", action="store_true",
                    help="skip download; ingest PDFs already on disk for this bank+period")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan (docs + run_doc commands); run nothing")
    ap.add_argument("--batch", action="store_true",
                    help="pass --batch to run_doc (Gemini Batch API)")
    ap.add_argument("--no-ipv4-shim", action="store_true",
                    help="pass --no-ipv4-shim to run_doc — needed where run_doc's "
                         "IPv4-shim sitecustomize shadows a PYTHONPATH paddle "
                         "workaround (e.g. Cloud Shell CPU + mkldnn patch)")
    ap.add_argument("--no-sync-bq", action="store_true",
                    help="skip the final BigQuery sync")
    args = ap.parse_args(argv)

    bank = args.bank.strip().upper()
    year, q = _parse_period(args.period)
    period = f"{year}-{q}"
    doc_period = f"{year}-{_QUARTER_END[q]}"          # as-at date for run_doc
    place_dir = FS_ROOT / bank / str(year) / q

    # 1. scrape this bank + period (skipped for --no-scrape / --dry-run)
    if not args.no_scrape and not args.dry_run:
        rc = _run([PYTHON, SCRAPER, "--bank", bank, "--periods", period])
        if rc != 0:
            print(f"[warn] scraper exited {rc}; continuing with whatever it placed")

    # 2. collect the placed docs for this exact bank/year/quarter
    docs = sorted(place_dir.glob("*.pdf")) if place_dir.exists() else []
    if not docs:
        sys.exit(f"no docs under {place_dir.relative_to(REPO)} — nothing to ingest "
                 f"(scrape may have found none for {bank} {period})")

    print(f"\n{bank} {period}: {len(docs)} doc(s) -> {place_dir.relative_to(REPO)} "
          f"(as-at {doc_period})")
    for d in docs:
        print(f"   • {d.name}")

    # 3. run_doc each, with --defer-db-steps: concepts/fact_metric/ratios/sync_bq
    #    are O(whole-DB) and would otherwise re-run once per doc in this batch.
    #    --no-sync-bq is kept too (defer-db-steps already implies no per-doc
    #    sync_bq, but this stays correct even if that ever changes).
    db_steps_cmd = [PYTHON, RUN_DOC, "--db-steps-only", "--db", args.db]
    if args.no_sync_bq:
        db_steps_cmd.append("--no-sync-bq")

    run_cmds = []
    for pdf in docs:
        cmd = [PYTHON, RUN_DOC, "--pdf", str(pdf), "--db", args.db,
               "--doc-period", doc_period, "--defer-db-steps", "--no-sync-bq"]
        if args.batch:
            cmd.append("--batch")
        if args.no_ipv4_shim:
            cmd.append("--no-ipv4-shim")
        run_cmds.append((pdf.name, cmd))

    if args.dry_run:
        print("\n[dry-run] would run:")
        for _, cmd in run_cmds:
            print(f"   $ {' '.join(cmd)}")
        print(f"   $ {' '.join(db_steps_cmd)}")
        return 0

    results = []
    for i, (name, cmd) in enumerate(run_cmds, 1):
        print(f"\n{'='*70}\n[{i}/{len(run_cmds)}] {name}\n{'='*70}")
        results.append((name, _run(cmd)))

    # 4. the whole-DB steps (concepts -> fact_metric -> ratios -> sync_bq),
    #    deferred by every per-doc run above, run ONCE here for the whole batch.
    print(f"\n{'='*70}\n[db-steps-only] concepts -> fact_metric -> ratios -> "
          f"sync_bq\n{'='*70}")
    _run(db_steps_cmd)

    # 5. summary
    print("\n" + "#" * 60 + f"\n# INGEST SUMMARY  {bank} {period}")
    for name, rc in results:
        print(f"#   {'OK  ' if rc == 0 else 'FAIL'}  {name}")
    n_fail = sum(1 for _, rc in results if rc)
    print(f"#   {len(results) - n_fail} ok, {n_fail} failed")
    print("#" * 60)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
