"""run_doc.py — the ONE-COMMAND driver for the FS (financial-statement) pipeline.

    python3 findociq/pipeline/run_doc.py --pdf <path>

extracts one document end-to-end, every step idempotent and resumable:

    STEP 0  scan check  (PaddleOCR regions.csv — skipped if present)
    STEP 1  TOC         (toc_stage cached Gemini -> toc_to_db sections)
    STEP 2  extraction  (PASS2_v2, resumes existing audit units for free)
    STEP 3  load        (stage2_load.load_v7.load_units — ONE doc-scoped call)
    STEP 5  verify      ($0 pdfplumber check) + auto re-extract loop (<=2 rounds)
    STEP 6  xlsx        (db_check_xlsx — DB-truth view)

THE THREE STAGES (--stage1/--stage2/--stage3), mirroring pipeline/'s own layout.
Pass none and nothing changes: the default is stages 1+2, exactly what a bare
`--pdf` run has always done. Pass any and ONLY those run, so you can reload
without re-extracting, or rebuild the serving DB without touching either:

    --stage1  EXTRACT  STEP 0, 1, 2, 2b   -> outputs/fs/<bank>_<period>/
    --stage2  LOAD     STEP 3, 3b, 5, 6, 7 -> --db (compiled_fs.db)
    --stage3  SERVE    build_compiled_v2   -> --compiled-v2 (compiled_v2.db)

    python3 findociq/pipeline/run_doc.py --pdf <path> --stage2      # reload only
    python3 findociq/pipeline/run_doc.py --pdf <path> --stage2 --stage3
    python3 findociq/pipeline/run_doc.py --stage3                   # no --pdf needed

Two things the names do NOT mean, both worth reading once:
  * STAGE 3 is not where rows get stamped. `row_dim.canonical_leaf_id` and
    `table_type_id` are written by load_v7 during STAGE 2 (load_v7.py:2199);
    build_compiled_v2 CARRIES those columns into the serving DB. Changing what
    is stamped means re-running STAGE 2, then STAGE 3 — never STAGE 3 alone.
  * STAGE 1 is not purely file-output: STEP 1's toc_to_db writes this document's
    `section` rows into --db. That is pre-existing behaviour, kept as-is. Running
    STAGE 2 alone against a DB that has never seen the doc re-seeds those
    sections from the cached TOC first.
STAGE 3 DELETES its --compiled-v2 target and rebuilds it from scratch, which is
why it is opt-in and absent from the default.

Two other entrypoints in this same file (both user-mandated):

    --rebuild-db    rebuild the WHOLE db from schema_v7 + every cached TOC that
                    has a matching audit dir, then verify / xlsx.
                    Replaces the recipe agents have hand-written 5+ times.
    --verify-only   load-from-artifacts + verify only (no extraction, $0).

Doc metadata is inferred deterministically (never per-bank):
  doc_id     = pdf stem, spaces -> underscores.
  doc_period = the filename period token: 1Q25->2025-03-31, 2Q25->2025-06-30,
               3Q25->2025-09-30, 4Q25/FY2025->2025-12-31 (case-insensitive).
               --doc-period overrides; no token and no flag -> fail loud.

This host has an IPv6 blackhole; --ipv4-shim (default ON) drops a sitecustomize
getaddrinfo->AF_INET shim onto PYTHONPATH for the Gemini-touching subprocesses.
Harmless elsewhere.

SELF-BOOTSTRAPPING. Run it with ANY python3 >= 3.10, from a bare clone, with no
setup step:

    python3 findociq/pipeline/run_doc.py --pdf <path>

If the interpreter is not the project venv, this file creates <repo>/.venv,
pip-installs findociq/requirements.txt into it, and re-execs itself there before
importing anything. The heavy PaddleOCR stack (.venv-paddle, ~1GB) is NOT built
up front — STEP 0 builds it on demand, and only when a document actually needs a
scan. Escape hatches: FINDOCIQ_NO_BOOTSTRAP=1 (never touch venvs),
FINDOCIQ_NO_PADDLE_BOOTSTRAP=1 (never build .venv-paddle).
"""
from __future__ import annotations

# ===========================================================================
# BOOTSTRAP — stdlib ONLY, and MUST stay above every project/third-party import
# (`import ingest_status` below already needs the venv's deps). Re-execs into
# <repo>/.venv so a fresh clone runs with one command and no setup.
# ===========================================================================
import hashlib
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASE_VENV = _REPO_ROOT / ".venv"
_BASE_VENV_PY = _BASE_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")
_BASE_REQS = _REPO_ROOT / "findociq" / "requirements.txt"


def _venv_usable(venv_py: Path) -> bool:
    """A venv is only usable if its interpreter exists AND has pip. `python -m venv`
    on a Debian box without python3-venv exits non-zero but still leaves the
    directory + bin/python3 behind, so existence alone is not enough."""
    if not venv_py.exists():
        return False
    try:
        return subprocess.run([str(venv_py), "-m", "pip", "--version"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def _create_venv(venv: Path, venv_py: Path) -> bool:
    """Create <venv>. Returns True if the venv has its OWN pip, False if it is a
    --without-pip venv that must be driven by the parent interpreter's pip.

    Three strategies, because a stock Debian/Ubuntu box has NO ensurepip (it is
    in the separately-packaged python3-venv), which makes plain `python -m venv`
    exit non-zero — while still leaving a half-built directory behind. Each
    attempt therefore wipes the directory first, so a retry is never poisoned by
    the previous one.
    """
    import shutil
    print(f"[bootstrap] creating {venv} …", flush=True)
    # Each attempt's output is CAPTURED, not streamed: strategy 1 fails loudly on
    # a stock Debian box ("apt install python3.12-venv"), and printing that when
    # strategy 3 goes on to succeed just scares the reader. Only shown if all fail.
    log_tail = []
    for cmd, has_pip in (
            ([sys.executable, "-m", "venv", str(venv)], True),
            ([sys.executable, "-m", "virtualenv", str(venv)], True),
            # No ensurepip AND no virtualenv: a --without-pip venv still works,
            # driven by THIS interpreter's pip via `pip --python <venv_py>`
            # (pip >= 22.3). Needs no sudo and no apt.
            ([sys.executable, "-m", "venv", "--without-pip", str(venv)], False)):
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)
        try:
            cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
            log_tail.append(f"  $ {' '.join(cmd)}\n{(cp.stdout or '').rstrip()}")
            if cp.returncode != 0:
                continue
        except OSError as e:
            log_tail.append(f"  $ {' '.join(cmd)}\n  {e}")
            continue
        if has_pip and _venv_usable(venv_py):
            return True
        if not has_pip and venv_py.exists():
            print("[bootstrap] no ensurepip/virtualenv on this box — using a "
                  "--without-pip venv driven by the parent pip", flush=True)
            return False
    shutil.rmtree(venv, ignore_errors=True)
    print("\n".join(log_tail), file=sys.stderr)
    sys.exit(
        f"[bootstrap] could not create a usable venv at {venv}.\n"
        f"  Install ONE of these, then re-run:\n"
        f"    sudo apt install python3-venv        # or python3.12-venv\n"
        f"    python3 -m pip install --user virtualenv\n"
        f"  Or build the venv yourself and re-run with FINDOCIQ_NO_BOOTSTRAP=1:\n"
        f"    python3 -m venv {venv} && {venv_py} -m pip install -r {_BASE_REQS}")


def _venv_pip_install(venv_py: Path, reqs: Path, label: str,
                      *, internal_pip: bool = True) -> None:
    """pip install -r <reqs> into <venv_py>, stamped by the reqs hash so a second
    run is a no-op. internal_pip=False drives a --without-pip venv from the parent
    interpreter. Raises CalledProcessError on failure (caller decides)."""
    stamp = venv_py.parent.parent / ".findociq-reqs.sha256"
    want = hashlib.sha256(reqs.read_bytes()).hexdigest()
    if stamp.exists() and stamp.read_text().strip() == want:
        return
    print(f"[bootstrap] installing {label} deps from {reqs.name} "
          f"(first run only, a few minutes) …", flush=True)
    if internal_pip:
        pip = [str(venv_py), "-m", "pip"]
        subprocess.run(pip + ["install", "--quiet", "--upgrade", "pip"], check=True)
    else:
        pip = [sys.executable, "-m", "pip", "--python", str(venv_py)]
    subprocess.run(pip + ["install", "--quiet", "-r", str(reqs)], check=True)
    stamp.write_text(want + "\n")


def _bootstrap_base_venv() -> None:
    """Ensure we are running inside <repo>/.venv with requirements.txt installed;
    re-exec into it if not. No-op when already there, or when opted out."""
    if os.environ.get("FINDOCIQ_NO_BOOTSTRAP"):
        return
    try:
        already = Path(sys.prefix).resolve() == _BASE_VENV.resolve()
    except OSError:
        already = False
    if already:
        return
    if os.environ.get("_FINDOCIQ_BOOTSTRAPPED"):
        # We re-exec'd once and still are not in the venv — bail loudly rather
        # than loop forever.
        sys.exit(f"[bootstrap] re-exec did not land in {_BASE_VENV}; "
                 f"run with FINDOCIQ_NO_BOOTSTRAP=1 and install "
                 f"{_BASE_REQS} yourself.")
    if sys.version_info < (3, 10):
        sys.exit(f"[bootstrap] need python >= 3.10, got {sys.version.split()[0]}")
    internal_pip = True
    if not _venv_usable(_BASE_VENV_PY):
        internal_pip = _create_venv(_BASE_VENV, _BASE_VENV_PY)
    try:
        _venv_pip_install(_BASE_VENV_PY, _BASE_REQS, "pipeline",
                          internal_pip=internal_pip)
    except subprocess.CalledProcessError as e:
        sys.exit(f"[bootstrap] pip install failed ({e}). Fix the network/proxy, or "
                 f"install {_BASE_REQS} manually and re-run with "
                 f"FINDOCIQ_NO_BOOTSTRAP=1.")
    env = dict(os.environ, _FINDOCIQ_BOOTSTRAPPED="1")
    print(f"[bootstrap] re-exec -> {_BASE_VENV_PY}", flush=True)
    os.execve(str(_BASE_VENV_PY), [str(_BASE_VENV_PY), *sys.argv], env)


_bootstrap_base_venv()

import argparse
import glob
import json
import re
import sqlite3
import tempfile
import time
# os / subprocess / sys / Path / hashlib already imported by the bootstrap above.

from common import ingest_status
from common import source_store

REPO = Path(__file__).resolve().parents[2]          # pipeline -> findociq -> repo
FINDOCIQ = REPO / "findociq"
PIPELINE = FINDOCIQ / "pipeline"
CHUNK_DIR = PIPELINE / "stage1_extract" / "chunk"   # PASS2_v2.py lives here
DEFAULT_DB = FINDOCIQ / "db" / "compiled_fs.db"
TOC_DIR = FINDOCIQ / "data" / "derived" / "toc"
SCAN_ROOT = FINDOCIQ / "data" / "derived" / "paddle_scans"
OUTPUTS_ROOT = FINDOCIQ / "outputs"
P3_ROOT = OUTPUTS_ROOT / "pillar3"    # legacy name; pillar3 stays byte-identical
SCHEMA_V7 = FINDOCIQ / "schema" / "schema_v7.sql"
VENV_PADDLE = REPO / ".venv-paddle" / "bin" / "python3"
# Subprocess steps must run under THIS interpreter (the venv that has the
# pipeline deps), never whatever bare `python3` resolves to on PATH.
PYTHON = sys.executable


# ===========================================================================
# Pure helpers (unit-tested in test_run_doc.py — no I/O, no subprocess)
# ===========================================================================
_QTR_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
# period token: allow a following '_' or end but never another digit (so a 2-digit
# year is exactly two digits). Case-insensitive; the ONLY period grammar (no
# per-bank rule).
# Grammar kept at PARITY with classify/family.py's period_of() — they used to
# diverge, and `--rebuild-db` died mid-corpus on the first half-year filing:
#   ValueError: no period token ... in doc_id 'OCBC_1H25_Media_Release_Financial_Highlights'
# family.py already read 1H/2H, an optional separator and 2- OR 4-digit years;
# this one read only `([1-4])Q(\d\d)`. Same corpus, two answers. Keep them equal.
_QTR_RX  = re.compile(r"([1-4])Q[-_ ]?(\d{4}|\d{2})(?![0-9])", re.I)
# Half-year maps to the period-END quarter (1H -> Q2, 2H -> Q4), matching how
# interim/FY filings are labelled — family.py:104.
_HALF_RX = re.compile(r"([12])H[-_ ]?(\d{4}|\d{2})(?![0-9])", re.I)
_FY_RX   = re.compile(r"FY[-_ ]?(\d{4}|\d{2})(?![0-9])", re.I)


def _yyyy(year: str) -> int:
    """'25' -> 2025, '2025' -> 2025 (same rule as classify/family.py:_yyyy)."""
    return int(year) if len(year) == 4 else 2000 + int(year)


def infer_period(doc_id: str, override: str | None = None) -> str:
    """Deterministic 'as at' ISO date from the filename period token.
    --doc-period (override) wins; no token and no override -> ValueError (fail loud)."""
    if override:
        return override
    m = _QTR_RX.search(doc_id)
    if m:
        return f"{_yyyy(m.group(2)):04d}-{_QTR_END[int(m.group(1))]}"
    m = _HALF_RX.search(doc_id)
    if m:
        return f"{_yyyy(m.group(2)):04d}-{_QTR_END[2 if m.group(1) == '1' else 4]}"
    m = _FY_RX.search(doc_id)
    if m:
        return f"{_yyyy(m.group(1)):04d}-12-31"
    raise ValueError(
        f"no period token (1Q25/2Q25/3Q25/4Q25/1H25/2H25/FY2025) in doc_id "
        f"{doc_id!r}; pass --doc-period YYYY-MM-DD")


def doc_id_for(pdf_path: str | Path) -> str:
    """doc_id = pdf stem with spaces -> underscores (matches toc_stage/PASS2)."""
    return Path(pdf_path).stem.replace(" ", "_")


# The three named stages, mirroring pipeline/'s own layout (stage1_extract /
# stage2_load / stage3_stamp). --stage1/--stage2/--stage3 select any subset.
#   1 extract  STEP 0 scan · STEP 1 toc · STEP 2 extract · STEP 2b geometry
#              -> outputs/fs/<bank>_<period>/  (+ sections in the DB, see below)
#   2 load     STEP 3 load · 3b registry · STEP 5 verify · 6 xlsx · 7 sync_bq
#              -> compiled_fs.db
#   3 serve    build_compiled_v2 -> compiled_v2.db
DEFAULT_STAGES = frozenset({1, 2})


def stages_from_args(stage1: bool, stage2: bool, stage3: bool) -> frozenset[int]:
    """Which stages to run. NO flag -> DEFAULT_STAGES, which is {1,2} and NOT
    {1,2,3}: run_doc has never built compiled_v2.db, and a bare `--pdf` run must
    stay byte-for-byte what it is today. Stage 3 is opt-in for that reason —
    it also DELETES and rebuilds its --dst.

    Any flag -> exactly the flagged stages (they compose: --stage2 --stage3)."""
    picked = {n for n, on in ((1, stage1), (2, stage2), (3, stage3)) if on}
    return frozenset(picked) if picked else DEFAULT_STAGES


def unit_from_meta(meta: dict, unit_dir: str | Path) -> dict:
    """One load_units unit from an audit meta.json: section_id = section_ids[0]
    (router-authoritative), pages verbatim, parsed_path in the same dir."""
    return {
        "section_id": meta["section_ids"][0],
        "pages": [int(p) for p in meta["pages"]],
        "parsed_path": str(Path(unit_dir) / "parsed.json"),
    }


def aggregate_geometry_stats(unit_stats: list[dict], *, unit_errors: int = 0) -> dict:
    """Fold a list of stage1_extract.chunk.geometry.process_unit() per-unit stats dicts into
    the run-level totals step2b_geometry reports/returns. `unit_errors` is
    passed through (units that raised are never in `unit_stats` — they are
    counted by the caller, not summed here)."""
    stats = {"units": len(unit_stats) + unit_errors, "unit_errors": unit_errors,
             "tables_matched": 0, "tables_total": 0, "rows_matched": 0, "rows_total": 0}
    for u in unit_stats:
        stats["tables_matched"] += u["tables_matched"]
        stats["tables_total"] += u["tables_total"]
        stats["rows_matched"] += u["rows_matched"]
        stats["rows_total"] += u["rows_total"]
    return stats


def failing_table_ids(report: dict) -> list[str]:
    """table_ids in a verify_cells report that did NOT fully verify — any row
    failed OR any value missing. These drive the re-extract loop."""
    out = []
    for t in report.get("tables", []):
        if t.get("rows_failed", 0) > 0 or t.get("values_missing"):
            out.append(t["table_id"])
    return out


# ===========================================================================
# Audit / artifact discovery
# ===========================================================================
def _find_source_pdf(basename: str | None, doc_id: str) -> Path | None:
    """Locate a document's source PDF under data/sources by NAME, ignoring the
    directory it was first ingested from.

    A cached TOC records `document.source_pdf` as a repo-relative path, frozen at
    ingest time. The corpus was later flattened from
    `financial_statements/<BANK>/<year>/<qtr>/x.pdf` to `financial_statements/x.pdf`,
    so 12 of 25 cached TOCs point at directories that no longer exist. The stored
    path is a HINT, not an address: what identifies the file is its name.

    Without this, `--rebuild-db` on a fresh clone silently skipped verification for
    those 12 documents — and verify is the only check that the numbers in the DB
    match the filings. Matching is tolerant of the spaces/underscores split
    (`OCBC 3Q25 Results Press Release.pdf` vs doc_id `OCBC_3Q25_Results_Press_Release`),
    the same normalisation `find_audit_root` already applies.
    """
    root = FINDOCIQ / "data" / "sources"
    if not root.exists():
        return None
    wanted = {w.replace(" ", "_").lower()
              for w in filter(None, (basename, f"{doc_id}.pdf"))}
    for p in root.rglob("*.pdf"):
        if p.name.replace(" ", "_").lower() in wanted:
            return p.resolve()
    return None


def find_audit_root(doc_id: str) -> Path | None:
    """PASS2 derives its own bank_period run dir AND names the audit subdir from
    the raw PDF stem (which may contain spaces), while our doc_id has spaces
    normalised to underscores. Match tolerantly: compare with spaces→underscores
    on both sides. Globs every family root (findociq/outputs/{family}/*/audit/<stem>),
    not just pillar3 — PASS2 now files fs docs under outputs/fs/."""
    want = doc_id.replace(" ", "_")
    hits = [p for p in glob.glob(str(OUTPUTS_ROOT / "*" / "*" / "audit" / "*"))
            if Path(p).name.replace(" ", "_") == want]
    return Path(sorted(hits)[0]) if hits else None


def build_units_from_audit(audit_root: Path) -> list[dict]:
    """Every audit unit with a non-empty parsed.json -> a load_units unit
    (skips narrative/empty units; load_v7 rejects 0-table artifacts)."""
    units = []
    for unit_dir in sorted(Path(audit_root).iterdir()):
        if not unit_dir.is_dir():
            continue
        mj, pj = unit_dir / "meta.json", unit_dir / "parsed.json"
        if not (mj.exists() and pj.exists()):
            continue
        meta = json.loads(mj.read_text())
        if not meta.get("section_ids"):
            continue
        try:
            parsed = json.loads(pj.read_text())
        except json.JSONDecodeError:
            continue
        if not parsed.get("tables"):
            continue
        units.append(unit_from_meta(meta, unit_dir))
    return units


def cost_note_for(audit_root: Path | None) -> str:
    """Pull a one-line cost note from the run's cost_summary.json if present."""
    if audit_root is None:
        return "n/a"
    cost_json = audit_root.parent.parent / "logs" / "cost_summary.json"
    if not cost_json.exists():
        return "no cost_summary.json (fully cached / $0 run)"
    try:
        tot = json.loads(cost_json.read_text()).get("totals", {})
        return (f"{tot.get('calls', '?')} call(s), "
                f"${tot.get('est_cost_usd', 0):.4f}")
    except Exception:  # noqa: BLE001
        return "cost_summary.json unreadable"


# ===========================================================================
# Environment / subprocess plumbing
# ===========================================================================
_SHIM_DIR: str | None = None


def ipv4_shim_dir() -> str:
    """Write (once) a sitecustomize.py that pins socket.getaddrinfo to AF_INET,
    and return its dir for PYTHONPATH prepend. This host blackholes IPv6."""
    global _SHIM_DIR
    if _SHIM_DIR is None:
        d = tempfile.mkdtemp(prefix="run_doc_ipv4_")
        Path(d, "sitecustomize.py").write_text(
            "import socket\n"
            "_orig = socket.getaddrinfo\n"
            "def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):\n"
            "    return _orig(host, port, socket.AF_INET, type, proto, flags)\n"
            "socket.getaddrinfo = getaddrinfo\n")
        _SHIM_DIR = d
    return _SHIM_DIR


# --- PaddleOCR (STEP 0) environment -----------------------------------------
# The paddle stack is ~1GB and is only needed to produce regions.csv, so it is
# built ON DEMAND (never at import, never for a doc whose scan already exists).
# It deliberately lives OUTSIDE $HOME — see tools/setup_paddle_venv.sh.
PADDLE_SCRATCH = Path(os.environ.get("FINDOCIQ_PADDLE_SCRATCH", "/tmp/paddle-scratch"))
PADDLE_SETUP_SH = REPO / "tools" / "setup_paddle_venv.sh"


def ensure_paddle_venv() -> bool:
    """Build .venv-paddle if absent. True if it is now usable."""
    if VENV_PADDLE.exists():
        return True
    if os.environ.get("FINDOCIQ_NO_PADDLE_BOOTSTRAP"):
        print("[bootstrap] .venv-paddle missing and FINDOCIQ_NO_PADDLE_BOOTSTRAP set")
        return False
    if not PADDLE_SETUP_SH.exists():
        print(f"[bootstrap] missing {PADDLE_SETUP_SH}")
        return False
    print("[bootstrap] building .venv-paddle (PaddleOCR stack, ~1GB, first scan "
          "only — a few minutes) …", flush=True)
    cp = subprocess.run(["bash", str(PADDLE_SETUP_SH)], cwd=str(REPO))
    return cp.returncode == 0 and VENV_PADDLE.exists()


def paddle_env() -> dict:
    """Env for the PaddleOCR child. PYTHONPATH must point at the scratch dir
    holding the mkldnn-disabling sitecustomize.py (paddlepaddle 3.3.1 segfaults
    on CPU via the oneDNN/PIR path without it), and HOME at the scratch
    paddlehome so model weights land off the ~5GB /home quota. This is the fix
    docs/2026-07-24-ingest-handoff.md line 96 left open: the IPv4 shim is
    deliberately NOT applied here, because Python loads only the FIRST
    sitecustomize.py on PYTHONPATH and paddle's must win."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PADDLE_SCRATCH)
    env["HOME"] = str(PADDLE_SCRATCH / "paddlehome")
    return env


def subprocess_env(*, shim: bool) -> dict:
    """Env for a child process: prepend the IPv4 shim dir to PYTHONPATH (when
    enabled). Gemini auth is Vertex AI/ADC (gemini_client.py) — inherited from
    the parent env automatically, nothing to inject."""
    env = dict(os.environ)
    if shim:
        d = ipv4_shim_dir()
        env["PYTHONPATH"] = d + (os.pathsep + env["PYTHONPATH"]
                                 if env.get("PYTHONPATH") else "")
    return env


def run_cmd(cmd: list[str], *, cwd: Path, env: dict | None = None,
            capture: bool = False) -> subprocess.CompletedProcess:
    """Run a child process (streaming by default). capture=True tees output into
    the returned .stdout so the caller can inspect it (e.g. detect a guard)."""
    printable = " ".join(str(c) for c in cmd)
    print(f"\n$ (cd {cwd.relative_to(REPO) if cwd != REPO else '.'}) {printable}",
          flush=True)
    if capture:
        cp = subprocess.run(cmd, cwd=str(cwd), env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(cp.stdout, end="", flush=True)
        return cp
    return subprocess.run(cmd, cwd=str(cwd), env=env)


def log(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


# ===========================================================================
# DB helpers
# ===========================================================================
def db_counts(db: Path, doc_id: str | None = None) -> dict:
    con = sqlite3.connect(db)
    try:
        where = " WHERE doc_id=?" if doc_id else ""
        args = (doc_id,) if doc_id else ()
        out = {}
        for t, key in (("section", "sections"), ("table_t", "tables"),
                       ("row_dim", "rows"), ("cell_fact", "cells")):
            out[key] = con.execute(
                f"SELECT COUNT(*) FROM {t}{where}", args).fetchone()[0]
        return out
    finally:
        con.close()


def sections_for_tables(db: Path, doc_id: str, table_ids: list[str]) -> list[str]:
    """Map failing table_ids back to their owning section_ids (table_t.section_id)."""
    con = sqlite3.connect(db)
    try:
        secs = []
        for tid in table_ids:
            row = con.execute(
                "SELECT section_id FROM table_t WHERE doc_id=? AND table_id=?",
                (doc_id, tid)).fetchone()
            if row and row[0] not in secs:
                secs.append(row[0])
        return secs
    finally:
        con.close()


def document_exists(db: Path, doc_id: str) -> bool:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT 1 FROM document WHERE doc_id=?",
                           (doc_id,)).fetchone() is not None
    finally:
        con.close()


# ===========================================================================
# In-process load + verify (bootstrapped onto sys.path)
# ===========================================================================
def _bootstrap_pipeline_path() -> None:
    if str(PIPELINE) not in sys.path:
        sys.path.insert(0, str(PIPELINE))


def load_doc(db: Path, doc_id: str, audit_root: Path) -> dict:
    """ONE doc-scoped load_units call over every audit unit."""
    _bootstrap_pipeline_path()
    from stage2_load.load_v7 import load_units  # noqa: E402
    units = build_units_from_audit(audit_root)
    if not units:
        raise SystemExit(f"no loadable audit units under {audit_root}")
    summary = load_units(str(db), doc_id, units)
    # warning breakdown by leading category token
    cats: dict[str, int] = {}
    for w in summary["warnings"]:
        cat = w.split(":")[0].split()[-1] if ":" in w else "misc"
        cats[cat] = cats.get(cat, 0) + 1
    print(f"[load] {doc_id}: units={len(units)} tables={summary['tables']} "
          f"rows={summary['rows']} cells={summary['cells']} "
          f"warnings={len(summary['warnings'])}")
    if summary["warnings"]:
        # summarise by category keyword so the human sees the shape, not 200 lines
        kinds: dict[str, int] = {}
        for w in summary["warnings"]:
            for kw in ("unresolvable unit", "document default", "unmapped geography",
                       "unmapped segment", "sums_to left NULL", "unit conflict",
                       "not among table periods", "LEAKED", "echo"):
                if kw in w:
                    kinds[kw] = kinds.get(kw, 0) + 1
                    break
            else:
                kinds["other"] = kinds.get("other", 0) + 1
        print("[load] warning categories: "
              + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    return summary


def verify_doc_report(db: Path, doc_id: str, pdf: Path) -> dict:
    """Run the $0 pdfplumber verifier for one doc via a temp manifest; return
    the report dict (also parseable by failing_table_ids)."""
    _bootstrap_pipeline_path()
    from common import verify_cells  # noqa: E402
    manifest = {"docs": [{"doc_id": doc_id, "pdf": str(pdf)}]}
    con = sqlite3.connect(db)
    try:
        report = verify_cells.verify_doc(manifest, con, doc_id)
    finally:
        con.close()
    verify_cells.print_summary([report])
    return report


# ===========================================================================
# Pipeline steps (single-doc mode)
# ===========================================================================
def step0_scan(pdf: Path, doc_id: str, shim: bool) -> None:
    log("STEP 0 — PaddleOCR scan check")
    regions = SCAN_ROOT / doc_id / "regions.csv"
    if regions.exists():
        print(f"[scan] {regions.relative_to(REPO)} present — skip")
        return
    if not ensure_paddle_venv():
        sys.exit(f"scan missing and could not build {VENV_PADDLE.relative_to(REPO)}; "
                 f"build it then re-run run_doc.py:\n"
                 f"  bash tools/setup_paddle_venv.sh")
    cmd = [str(VENV_PADDLE),
           "findociq/pipeline/stage1_extract/toc/candidates.py",
           str(pdf), doc_id, "--out", "findociq/data/derived/paddle_scans"]
    cp = run_cmd(cmd, cwd=REPO, env=paddle_env())
    if cp.returncode != 0 or not regions.exists():
        sys.exit(
            "\nSTEP 0 FAILED (libomp/sandbox or PaddleOCR). Run this MANUALLY, "
            "UNSANDBOXED, then re-run run_doc.py:\n"
            f"  .venv-paddle/bin/python3 findociq/pipeline/discover/section/"
            f"candidates.py {pdf} {doc_id} --out findociq/data/derived/paddle_scans\n"
            "(exit 2)")
    print(f"[scan] wrote {regions.relative_to(REPO)}")


def _classify_row(pdf: Path) -> dict:
    """The full classify() row (family + institution + ...). Empty dict if the
    classifier can't run (fail-safe; callers apply their own defaults)."""
    try:
        sys.path.insert(0, str(PIPELINE))
        from stage1_extract.route.family import classify
        return classify(pdf)
    except Exception as e:                              # noqa: BLE001
        print(f"[route] classify failed ({type(e).__name__}: {e})")
        return {}


def classify_family(pdf: Path) -> str:
    """Route decision: 'pillar3' | 'fs' | 'slides' from the content classifier.
    Deterministic; falls back to 'fs' if the classifier can't run (fail-safe to
    the general Gemini TOC path, never crashes the driver on classification)."""
    return _classify_row(pdf).get("family") or "fs"


def step1_toc(pdf: Path, doc_id: str, db: Path, period: str, shim: bool) -> Path:
    log("STEP 1 — TOC (sections)")
    family = classify_family(pdf)
    toc_json = TOC_DIR / f"{doc_id}_toc.json"
    if family == "pillar3":
        # PIPELINE PIVOT (2026-07-16): Pillar 3 routes to the PROVEN deterministic
        # pass1_toc framework, adapted into the schema_v7 section shape — NOT the
        # Gemini toc_stage path. Both families land in the same compiled_fs.db.
        # (Gemini auth is Vertex/ADC now — subprocess_env carries only the shim.)
        print(f"[route] family=pillar3 -> pass1_toc framework (deterministic, "
              f"zero-API); adapt -> schema_v7", flush=True)
        p1_json = FINDOCIQ / "data" / "discovery" / f"{doc_id}_toc.json"
        cp = run_cmd([PYTHON, "findociq/pipeline/stage1_extract/toc/pass1_toc.py",
                      str(pdf), "--out", str(p1_json)],
                     cwd=REPO, env=subprocess_env(shim=False))
        if cp.returncode != 0 or not p1_json.exists():
            sys.exit(f"STEP 1 pass1_toc failed (no {p1_json})")
        source_rel = os.path.relpath(pdf, REPO)
        cp = run_cmd([PYTHON, "findociq/pipeline/stage1_extract/toc/pass1_to_v7.py",
                      "--pass1", str(p1_json), "--doc-id", doc_id,
                      "--source-rel", source_rel, "--out", str(toc_json)],
                     cwd=REPO, env=subprocess_env(shim=False))
        if cp.returncode != 0 or not toc_json.exists():
            sys.exit(f"STEP 1 pass1_to_v7 adapter failed (no {toc_json})")
    else:
        print(f"[route] family={family} -> Gemini toc_stage framework", flush=True)
        cp = run_cmd([PYTHON, "findociq/pipeline/stage1_extract/toc/toc_stage.py", "--pdf", str(pdf)],
                     cwd=REPO, env=subprocess_env(shim=shim))
        if cp.returncode != 0 or not toc_json.exists():
            sys.exit(f"STEP 1 toc_stage failed (no {toc_json})")
    cp = run_cmd([PYTHON, "findociq/pipeline/stage1_extract/toc/toc_to_db.py",
                  "--toc", str(toc_json), "--db", str(db), "--doc-period", period],
                 cwd=REPO, env=subprocess_env(shim=False), capture=True)
    if cp.returncode != 0:
        if "table_t rows reference" in (cp.stdout or ""):
            print("\n[toc_to_db] this doc already has extracted table_t rows. That is\n"
                  "expected on a re-run: the sections are unchanged, so we SKIP the\n"
                  "section rewrite and let load_units (STEP 3, doc-scoped) reload the\n"
                  "tables from artifacts. Continuing.")
        else:
            sys.exit(f"STEP 1 toc_to_db failed:\n{cp.stdout}")
    return toc_json


def step2_extract(pdf: Path, toc_json: Path, *, batch: bool, force: bool,
                  shim: bool, section: str | None = None,
                  family: str | None = None) -> None:
    log("STEP 2 — extraction (PASS2)"
        + (f" [section {section}]" if section else ""))
    # PASS2 runs with cwd=CHUNK_DIR (stage1_extract/chunk/), so the pdf path must
    # be relative to THAT, not to pipeline/ — it moved there in the 3-stage split.
    rel_pdf = os.path.relpath(pdf, CHUNK_DIR)
    cmd = [PYTHON, "PASS2_v2.py", rel_pdf, "--toc", str(toc_json),
           "--no-pause", "--workers", "5"]
    if family:
        cmd += ["--family", family]
    if section:
        cmd += ["--section", section]
    if batch:
        cmd.append("--batch")
    if force or section:                    # a targeted re-extract always forces
        cmd.append("--force")
    cp = run_cmd(cmd, cwd=CHUNK_DIR, env=subprocess_env(shim=shim))
    if cp.returncode != 0:
        sys.exit(f"STEP 2 PASS2 failed (rc={cp.returncode})")


def step2b_geometry(doc_id: str) -> dict:
    """STEP 2b — PDF-layer row ground truth (stage1_extract.chunk.geometry side-car), run
    in-process (like the loader steps) over every unit under this doc's audit
    root. NEVER fails the run: a unit that raises (unreadable PDF, missing
    source) is caught, counted, and reported as a warning line — load_v7
    falls back to the model's per-table levels when the side-car is absent or
    incomplete. Returns aggregate stats: {"units": n, "unit_errors": n,
    "tables_matched", "tables_total", "rows_matched", "rows_total"}."""
    log("STEP 2b — geometry (PDF-layer row ground truth)")
    audit_root = find_audit_root(doc_id)
    if audit_root is None:
        print(f"[geometry] no audit dir for {doc_id} — skipping (nothing to derive)")
        return aggregate_geometry_stats([])

    _bootstrap_pipeline_path()
    from stage1_extract.chunk import geometry  # noqa: E402

    units = geometry.find_units(audit_root)
    unit_stats: list[dict] = []
    unit_errors = 0
    for unit_dir in units:
        try:
            u_stats = geometry.process_unit(unit_dir)
        except Exception as e:                                  # noqa: BLE001
            unit_errors += 1
            print(f"[geometry]   ⚠ {unit_dir.name}: {type(e).__name__}: {e} — "
                  f"skipping (loader falls back to model levels for this unit)")
            continue
        print(f"{u_stats['unit']}: source={u_stats['source']} "
              f"tables_matched {u_stats['tables_matched']}/{u_stats['tables_total']} "
              f"rows_matched {u_stats['rows_matched']}/{u_stats['rows_total']}")
        unit_stats.append(u_stats)

    stats = aggregate_geometry_stats(unit_stats, unit_errors=unit_errors)
    print(f"TOTAL: tables_matched {stats['tables_matched']}/{stats['tables_total']} "
          f"rows_matched {stats['rows_matched']}/{stats['rows_total']}")
    if stats["units"] and stats["tables_matched"] == 0 and stats["tables_total"] > 0:
        print(f"[geometry] ⚠⚠⚠ ZERO tables matched across {stats['units']} unit(s) — "
              f"geometry side-car will not contribute row ground truth for {doc_id}")
    return stats


def step3b_registry(db: Path) -> None:
    """STEP 3b — registry seed + corpus classify (writes table_t.table_type_id).

    THE MISSING LINK. `classify_corpus()` is what assigns `table_type_id`, and
    it was reachable only by running `mapping/seed_registry.py` by hand — so a
    freshly-loaded document kept `table_type_id IS NULL`, and because
    `stamp_human_anchors()` returns early without a table_type_id, NONE of the
    bank's already-loaded human_confirmed anchors projected onto it. Measured on
    a full dry-run re-ingest of DBS_4Q25_performance_summary: 0/45 tables
    classified, 0 anchors projected, and the Commercial/Markets segment split
    never formed. One command by hand was the whole gap between "loaded" and
    "served".

    Runs BEFORE STEP 4a because the concept layer's own STEP 4a
    (`ensure_schema` -> `stamp_human_anchors`) reads `table_type_id`.

    Whole-DB step (classify_corpus is O(corpus)), so it belongs to the same
    group as 4a/4b/4c: deferred by --defer-db-steps and re-run by
    --db-steps-only, never once per document in a batch sweep.

    Idempotent and non-destructive: `seed()` UPSERTs the YAML types/aliases and
    NEVER overwrites an alias row with source='human_confirmed';
    `classify_corpus()` recomputes the pointer only, never `table_t.table_type`
    (as-reported is preserved).
    """
    log("STEP 3b — registry classify")
    cmd = [PYTHON, "findociq/pipeline/stage3_stamp/resolve/seed_registry.py", "--db", str(db)]
    cp = run_cmd(cmd, cwd=REPO, env=subprocess_env(shim=False))
    if cp.returncode != 0:
        sys.exit(f"STEP 3b registry classify failed (rc={cp.returncode})")


def step7_sync_bq(db: Path) -> int:
    log("STEP 7 — sync_bq")
    cmd = [PYTHON, "findociq/pipeline/common/sync_bq.py", "--db", str(db)]
    cp = run_cmd(cmd, cwd=REPO, env=subprocess_env(shim=False))
    if cp.returncode != 0:
        print(f"  ⚠ STEP 7 sync_bq failed (rc={cp.returncode}) — continuing")
    return cp.returncode


def step6_xlsx(db: Path) -> None:
    log("STEP 6 — db_check_xlsx")
    cp = run_cmd([PYTHON, "findociq/pipeline/common/db_check_xlsx.py", "--db", str(db)],
                 cwd=REPO, env=subprocess_env(shim=False))
    if cp.returncode != 0:
        sys.exit(f"STEP 6 xlsx failed (rc={cp.returncode})")


def step8_serve(db: Path, dst: Path) -> None:
    """STAGE 3 — build compiled_v2.db (the DB the app reads) from compiled_fs.db.

    NOT a re-stamp. The canonical identity (row_dim.canonical_leaf_id +
    table_type_id) is written by load_v7 during STAGE 2's load — see
    load_v7.py:2199. build_compiled_v2 CARRIES those columns across into the
    serving DB; it cannot create identity that the load did not resolve. To
    change what is stamped, re-run STAGE 2, then STAGE 3.

    Whole-DB and destructive by construction: build_compiled_v2 unlinks --dst
    and rebuilds it from scratch. Never on by default — see stages_from_args."""
    log("STAGE 3 — build compiled_v2 (serving DB)")
    cp = run_cmd([PYTHON, "findociq/pipeline/stage3_stamp/serve/build_compiled_v2.py",
                  "--src", str(db), "--dst", str(dst)],
                 cwd=REPO, env=subprocess_env(shim=False))
    if cp.returncode != 0:
        sys.exit(f"STAGE 3 build_compiled_v2 failed (rc={cp.returncode})")


# Stages that never run once verify fails for good (D29): whole-DB steps
# (fact_metric/ratios) aren't tracked per-doc in ingest_status (see run_one's
# comment on that), but xlsx and sync_bq ARE per-doc STAGES this document will
# now never reach -- that's the silent-skip this record exists to kill.
_SKIPPED_ON_VERIFY_FAIL = ("xlsx", "sync_bq")


def verify_with_reextract(db: Path, doc_id: str, pdf: Path, toc_json: Path, *,
                          batch: bool, shim: bool, max_rounds: int = 2,
                          family: str | None = None, source_file: str) -> dict:
    """STEP 5 — verify, and on any failure re-extract JUST the failing sections
    (--section --force), doc-scoped reload, re-verify. Up to max_rounds retries.

    If it is STILL failing after max_rounds, this is a hard gate (D29): the
    document must not silently proceed to xlsx/sync_bq with unverified data.
    Before exiting non-zero, it (a) writes a durable ingest_status record
    naming doc_id, the failed stage, the cell-failure count, and exactly which
    downstream stages were consequently skipped, and (b) prints the same
    facts as a loud, clearly-formatted block to stderr -- so a failed verify
    is never just a dashboard that quietly stopped moving."""
    log("STEP 5 — verify")
    audit_root = find_audit_root(doc_id)
    report = verify_doc_report(db, doc_id, pdf)
    failing = failing_table_ids(report)
    rounds = 0
    while failing and rounds < max_rounds:
        rounds += 1
        secs = sections_for_tables(db, doc_id, failing)
        print(f"\n[verify] round {rounds}: {len(failing)} table(s) failed -> "
              f"re-extract sections {secs}")
        for sid in secs:
            step2_extract(pdf, toc_json, batch=batch, force=True, shim=shim,
                          section=sid, family=family)
        step2b_geometry(doc_id)
        audit_root = find_audit_root(doc_id)
        load_doc(db, doc_id, audit_root)     # doc-scoped: reloads ALL units
        report = verify_doc_report(db, doc_id, pdf)
        failing = failing_table_ids(report)
    if failing:
        cells_failed = sum(len(t.get("values_missing", [])) for t in report["tables"])
        skipped = ", ".join(_SKIPPED_ON_VERIFY_FAIL)
        print(f"\n[verify] STILL FAILING after {rounds} round(s):")
        for tid in failing:
            print(f"   ✗ {tid}")

        error_message = (
            f"verify FAILED after {rounds} re-extract round(s): "
            f"{len(failing)} table(s) / {cells_failed} cell(s) failed "
            f"({failing}); downstream stages SKIPPED: {skipped}"
        )
        ingest_status.mark(str(db), source_file, "verify", "failed",
                           doc_id=doc_id, error=error_message)

        print("\n" + "!" * 72, file=sys.stderr)
        print(f"! VERIFY FAILED -- {doc_id}", file=sys.stderr)
        print(f"!   stage           : verify", file=sys.stderr)
        print(f"!   rounds tried    : {rounds}", file=sys.stderr)
        print(f"!   tables failed   : {len(failing)} {failing}", file=sys.stderr)
        print(f"!   cells failed    : {cells_failed}", file=sys.stderr)
        print(f"!   DOWNSTREAM STAGES SKIPPED: {skipped}", file=sys.stderr)
        print(f"!   recorded in ingest_status (source_file={source_file!r}, "
              f"stage='verify', state='failed')", file=sys.stderr)
        print("!" * 72 + "\n", file=sys.stderr)

        # The status record above is authoritative for this failure; mark it
        # so run_one's generic except-handler (which exists for OTHER
        # exceptions verify_with_reextract can raise, e.g. a re-extract
        # subprocess crash) doesn't immediately overwrite it with a bare "1".
        exc = SystemExit(1)
        exc.ingest_status_recorded = True
        raise exc
    print("\n[verify] all tables verified (0 fail, 0 missing).")
    return report


# ===========================================================================
# MODE: single document end-to-end
# ===========================================================================
def run_one(args) -> int:
    t0 = time.time()
    try:
        pdf = source_store.resolve_to_local(args.pdf)
    except FileNotFoundError as e:
        sys.exit(str(e))
    doc_id = doc_id_for(pdf)
    db = Path(args.db).resolve()
    source_file = source_store.key_for(pdf)
    try:
        period = infer_period(doc_id, args.doc_period)
    except ValueError as e:
        sys.exit(str(e))
    shim = args.ipv4_shim
    stages = stages_from_args(args.stage1, args.stage2, args.stage3)

    print(f"doc_id={doc_id}  doc_period={period}  db={_display_path(db)}")
    if stages != DEFAULT_STAGES:
        print(f"stages   = {sorted(stages)}  "
              f"({'extract ' if 1 in stages else ''}"
              f"{'load ' if 2 in stages else ''}"
              f"{'serve' if 3 in stages else ''})".rstrip())

    row = _classify_row(pdf)
    bank, family = row.get("institution"), row.get("family") or "fs"
    ingest_status.mark(str(db), source_file, "scan", "running",
                       doc_id=doc_id, bank=bank, period=period, family=family)

    if family in ("other", "slides"):
        print(f"[skip] doc_id={doc_id} family={family} — no statement/table content "
              f"to extract (see classify/family.py); exiting clean, no API spend")
        ingest_status.mark(str(db), source_file, "done", "ok")
        return 0

    # ---- STAGE 1 — extract -------------------------------------------------
    if 1 not in stages:
        # Stage 2's re-extract loop still needs the TOC path, and stage 1 is the
        # only producer of it. Fall back to the cached TOC, exactly as
        # run_verify_only does — no Gemini, no scan.
        toc_json = TOC_DIR / f"{doc_id}_toc.json"
        log("STAGE 1 — extract [SKIPPED]")
        if not toc_json.exists():
            sys.exit(f"--stage2/--stage3 without --stage1, but no cached TOC "
                     f"({_display_path(toc_json)}). Run --stage1 for this doc first.")
        print(f"[skip] {doc_id}: scan/toc/extract/geometry — using cached "
              f"{_display_path(toc_json)} and the existing audit artifacts")
    else:
        try:
            step0_scan(pdf, doc_id, shim)
        except (SystemExit, Exception) as e:
            ingest_status.mark(str(db), source_file, "scan", "failed", error=e)
            raise
        ingest_status.mark(str(db), source_file, "scan", "ok")

        ingest_status.mark(str(db), source_file, "toc", "running")
        try:
            toc_json = step1_toc(pdf, doc_id, db, period, shim)
        except (SystemExit, Exception) as e:
            ingest_status.mark(str(db), source_file, "toc", "failed", error=e)
            raise
        ingest_status.mark(str(db), source_file, "toc", "ok", doc_id=doc_id)

        ingest_status.mark(str(db), source_file, "extract", "running")
        try:
            step2_extract(pdf, toc_json, batch=args.batch, force=args.force, shim=shim,
                          family=family)
        except (SystemExit, Exception) as e:
            ingest_status.mark(str(db), source_file, "extract", "failed", error=e)
            raise
        ingest_status.mark(str(db), source_file, "extract", "ok")

        ingest_status.mark(str(db), source_file, "geometry", "running")
        try:
            step2b_geometry(doc_id)
        except (SystemExit, Exception) as e:
            ingest_status.mark(str(db), source_file, "geometry", "failed", error=e)
            raise
        ingest_status.mark(str(db), source_file, "geometry", "ok")

    # ---- STAGE 2 — load ----------------------------------------------------
    if 2 not in stages:
        log("STAGE 2 — load [SKIPPED]")
        print(f"[skip] {doc_id}: load/verify/xlsx/sync_bq — {_display_path(db)} "
              f"NOT written by this run")
        audit_root = find_audit_root(doc_id)
    else:
        # Stage 1 seeds the doc's sections via toc_to_db. Running stage 2 alone
        # against a DB that has never seen this doc would load tables with no
        # parent sections, so seed them from the cached TOC first — the same
        # guard, and the same command, run_verify_only uses.
        if 1 not in stages and not document_exists(db, doc_id):
            log("STAGE 2 — seeding sections from the cached TOC (stage 1 skipped)")
            run_cmd([PYTHON, "findociq/pipeline/stage1_extract/toc/toc_to_db.py",
                     "--toc", str(toc_json), "--db", str(db),
                     "--doc-period", period],
                    cwd=REPO, env=subprocess_env(shim=False))

        log("STEP 3 — load")
        ingest_status.mark(str(db), source_file, "load", "running")
        try:
            audit_root = find_audit_root(doc_id)
            if audit_root is None:
                sys.exit(f"no audit dir for {doc_id} under {OUTPUTS_ROOT} (did STEP 2 run?)")
            load_doc(db, doc_id, audit_root)
        except (SystemExit, Exception) as e:
            ingest_status.mark(str(db), source_file, "load", "failed", error=e)
            raise
        ingest_status.mark(str(db), source_file, "load", "ok")

        # The concept layer (old STEP 4a/4b/4c) was RETIRED 2026-08-12: row identity
        # comes from the masterlist at load (STEP 3), and compiled_v2.db — the DB the
        # app reads — dropped the concept tables by design. STEP 3b (registry seed +
        # classify) survives it, because the masterlist AUTHORING flow
        # (mapping/propose_masterlist.py) resolves table types through
        # table_registry_alias. It stays opt-in: it is a whole-DB O(corpus) step.
        if args.defer_db_steps or not args.seed_registry:
            why = ("whole-DB step deferred for this batch sweep" if args.defer_db_steps
                   else "opt-in; only the masterlist authoring flow needs it")
            log("STEP 3b — registry classify [SKIPPED]")
            print(f"[skip] {doc_id}: registry seed + classify — {why}. Run afterward:\n"
                  f"    python3 findociq/pipeline/run_doc.py --db-steps-only --db {db}")
        else:
            step3b_registry(db)

        ingest_status.mark(str(db), source_file, "verify", "running")
        try:
            verify_with_reextract(db, doc_id, pdf, toc_json, batch=args.batch, shim=shim,
                                  family=family, source_file=source_file)
        except (SystemExit, Exception) as e:
            # verify_with_reextract already writes the detailed ingest_status
            # record itself for a "still failing after max_rounds" verify
            # failure (richer than a bare error string) and sets this flag so we
            # don't clobber it here. Any OTHER exception out of that function
            # (e.g. a re-extract subprocess crash) still gets the generic mark.
            if not getattr(e, "ingest_status_recorded", False):
                ingest_status.mark(str(db), source_file, "verify", "failed", error=e)
            raise
        ingest_status.mark(str(db), source_file, "verify", "ok")

        # fact_metric + ratios are built AFTER verify: verify_with_reextract can
        # re-extract failing sections and reload units, and build_fact_metric does a
        # full DROP/CREATE from the concept tables — so the canonical table and its
        # derived ratios must be rebuilt from post-verification data, not before it.
        # (whole-DB steps, not tracked per-doc in ingest_status — a crash here is a
        # DB-wide event, not this document's alone.)
        ingest_status.mark(str(db), source_file, "xlsx", "running")
        try:
            step6_xlsx(db)
        except (SystemExit, Exception) as e:
            ingest_status.mark(str(db), source_file, "xlsx", "failed", error=e)
            raise
        ingest_status.mark(str(db), source_file, "xlsx", "ok")

        if args.defer_db_steps:
            log("STEP 7 — sync_bq [DEFERRED]")
            print(f"[defer] {doc_id}: sync_bq SKIPPED — whole-DB step deferred; "
                  f"ingest_status stage 'sync_bq' left UNMARKED. Run "
                  f"--db-steps-only (above) after the sweep to sync.")
        elif not args.no_sync_bq:
            ingest_status.mark(str(db), source_file, "sync_bq", "running")
            rc = step7_sync_bq(db)
            if rc != 0:
                ingest_status.mark(str(db), source_file, "sync_bq", "failed",
                                   error=f"sync_bq exited rc={rc}")
            else:
                ingest_status.mark(str(db), source_file, "sync_bq", "ok")

    # ---- STAGE 3 — serve ---------------------------------------------------
    if 3 in stages:
        step8_serve(db, Path(args.compiled_v2).resolve())

    ingest_status.mark(str(db), source_file, "done", "ok")

    print("\n" + "#" * 60)
    print(f"# DONE  {doc_id}")
    print(f"#   period   : {period}")
    if stages != DEFAULT_STAGES:
        print(f"#   stages   : {sorted(stages)} (of [1, 2, 3])")
    # The counts and the verify verdict are STAGE 2's results. With stage 2 off
    # this document may not be in the DB at all, and db_counts would either
    # raise or — worse — report a PREVIOUS run's numbers as if they were this
    # one's. Report what this run actually did instead.
    if 2 in stages:
        c = db_counts(db, doc_id)
        print(f"#   sections : {c['sections']}")
        print(f"#   tables   : {c['tables']}")
        print(f"#   rows     : {c['rows']}")
        print(f"#   cells    : {c['cells']}")
        print(f"#   verify   : PASS (0 fail)")
    else:
        print(f"#   db       : NOT written (stage 2 skipped)")
    if 3 in stages:
        print(f"#   serving  : {_display_path(Path(args.compiled_v2).resolve())}")
    print(f"#   cost     : {cost_note_for(audit_root)}")
    print(f"#   elapsed  : {time.time() - t0:.1f}s")
    print("#" * 60)
    return 0


# ===========================================================================
# MODE: --verify-only (load-from-artifacts + verify, $0)
# ===========================================================================
def run_verify_only(args) -> int:
    t0 = time.time()
    pdf = Path(args.pdf).resolve()
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}")
    doc_id = doc_id_for(pdf)
    db = Path(args.db).resolve()
    period = infer_period(doc_id, args.doc_period)
    print(f"VERIFY-ONLY  doc_id={doc_id}  period={period}  db={_display_path(db)}")

    audit_root = find_audit_root(doc_id)
    if audit_root is None:
        sys.exit(f"no audit dir for {doc_id} — nothing to load")
    if not document_exists(db, doc_id):
        # sections missing -> seed them from the cached TOC (no Gemini, no scan)
        toc_json = TOC_DIR / f"{doc_id}_toc.json"
        if not toc_json.exists():
            sys.exit(f"no document row and no cached TOC ({toc_json}); run full pipeline")
        run_cmd([PYTHON, "findociq/pipeline/stage1_extract/toc/toc_to_db.py",
                 "--toc", str(toc_json), "--db", str(db), "--doc-period", period],
                cwd=REPO, env=subprocess_env(shim=False))

    log("load-from-artifacts")
    load_doc(db, doc_id, audit_root)
    log("verify")
    report = verify_doc_report(db, doc_id, pdf)
    failing = failing_table_ids(report)
    c = db_counts(db, doc_id)
    print(f"\nVERIFY-ONLY DONE  {doc_id}: tables={c['tables']} rows={c['rows']} "
          f"cells={c['cells']}  fail={len(failing)}  elapsed={time.time()-t0:.1f}s")
    if failing:
        for tid in failing:
            print(f"   ✗ {tid}")
        return 1
    return 0


# ===========================================================================
# MODE: --rebuild-db (whole-db from schema_v7 + every cached TOC w/ audit)
# ===========================================================================
def _rebuildable_docs(only: list[str] | None = None) -> list[tuple[str, Path, Path]]:
    """(doc_id, toc_json, audit_root) for every cached TOC that has a matching
    audit dir HOLDING AT LEAST ONE LOADABLE UNIT. Sorted by doc_id for a stable
    rebuild order.

    The loadable-unit test is not cosmetic: load_doc raises SystemExit on an
    empty audit dir, so a single stale/emptied dir used to abort the whole
    rebuild and take every other doc with it. Such a doc is skipped LOUDLY —
    it contributes nothing to a rebuild either way.

    `only` keeps just the doc_ids containing one of the given substrings
    (case-insensitive), e.g. ["4Q25", "1Q26"] to rebuild a maintained subset."""
    out, empty = [], []
    for toc_json in sorted(TOC_DIR.glob("*_toc.json")):
        doc_id = toc_json.name[: -len("_toc.json")]
        if only and not any(o.lower() in doc_id.lower() for o in only):
            continue
        audit_root = find_audit_root(doc_id)
        if audit_root is None:
            continue
        if not build_units_from_audit(audit_root):
            empty.append(doc_id)
            continue
        out.append((doc_id, toc_json, audit_root))
    for doc_id in empty:
        print(f"[rebuild] skip {doc_id} — audit dir has no loadable unit")
    return out


def run_rebuild(args) -> int:
    t0 = time.time()
    db = Path(args.db).resolve()
    shim = args.ipv4_shim

    log(f"REBUILD-DB — fresh schema_v7 -> {_display_path(db)}")
    if db.exists():
        db.unlink()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.executescript(SCHEMA_V7.read_text())
        con.commit()
    finally:
        con.close()
    print(f"[rebuild] executescript {SCHEMA_V7.relative_to(REPO)}")

    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
    if only:
        print(f"[rebuild] --only {only} — rebuilding a maintained subset")
    docs = _rebuildable_docs(only)
    if not docs:
        sys.exit("no cached TOC has a matching audit dir — nothing to rebuild")
    print(f"[rebuild] {len(docs)} doc(s): {[d for d, _, _ in docs]}")

    doc_pdfs: dict[str, Path] = {}
    for doc_id, toc_json, audit_root in docs:
        period = infer_period(doc_id)
        payload = json.loads(toc_json.read_text())
        src = payload["document"].get("source_pdf")
        pdf = (REPO / src).resolve() if src else None
        if pdf is not None and not pdf.exists():
            pdf = _find_source_pdf(Path(src).name, doc_id)
        if pdf is None and src is None:
            pdf = _find_source_pdf(None, doc_id)
        if pdf is not None:
            doc_pdfs[doc_id] = pdf
        log(f"REBUILD {doc_id}  period={period}")
        run_cmd([PYTHON, "findociq/pipeline/stage1_extract/toc/toc_to_db.py",
                 "--toc", str(toc_json), "--db", str(db), "--doc-period", period],
                cwd=REPO, env=subprocess_env(shim=False))
        load_doc(db, doc_id, audit_root)

    log("REBUILD verify (all docs)")
    any_fail = False
    n_verified = n_skipped = 0
    for doc_id, _, _ in docs:
        pdf = doc_pdfs.get(doc_id)
        if pdf is None or not pdf.exists():
            print(f"   ⚠ {doc_id}: no source pdf resolvable — skipping verify")
            n_skipped += 1
            continue
        n_verified += 1
        report = verify_doc_report(db, doc_id, pdf)
        failing = failing_table_ids(report)
        if failing:
            any_fail = True
            print(f"   ✗ {doc_id}: {len(failing)} table(s) failed: {failing}")

    step6_xlsx(db)

    if not args.no_sync_bq:
        step7_sync_bq(db)

    c = db_counts(db)
    print("\n" + "#" * 60)
    print(f"# REBUILD-DB DONE")
    print(f"#   docs     : {len(docs)}")
    print(f"#   sections : {c['sections']}")
    print(f"#   tables   : {c['tables']}")
    print(f"#   rows     : {c['rows']}")
    print(f"#   cells    : {c['cells']}")
    # NEVER report a bare "PASS" for a run that verified nothing. Without the
    # source PDFs every doc is skipped above, and this line used to print
    # "PASS (0 fail)" over 25 skipped documents — a green light that had
    # checked precisely nothing. Say what was actually covered.
    if n_verified == 0:
        verdict = f"NOT RUN — all {n_skipped} doc(s) skipped, no source PDF"
    elif any_fail:
        verdict = f"FAIL ({n_verified} verified, {n_skipped} skipped)"
    else:
        verdict = f"PASS (0 fail, {n_verified} verified" + (
            f", {n_skipped} SKIPPED — no source PDF)" if n_skipped else ")")
    print(f"#   verify   : {verdict}")
    print(f"#   elapsed  : {time.time() - t0:.1f}s")
    print("#" * 60)
    return 1 if any_fail else 0


# ===========================================================================
# MODE: --db-steps-only (whole-DB steps only: concepts -> fact_metric ->
# ratios -> sync_bq; no document required). Companion to --defer-db-steps.
# ===========================================================================
def _display_path(p: Path) -> str:
    """Repo-relative when possible, else absolute. `Path.relative_to` RAISES for
    a path outside the repo, which crashed --db-steps-only on a scratch DB in
    /tmp before it ran a single step."""
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def run_db_steps_only(args) -> int:
    """Run ONLY the whole-DB steps against --db, in the SAME order run_one
    uses: registry seed/classify -> sync_bq. This is what an operator runs ONCE
    after a batch sweep of --defer-db-steps documents, instead of repeating
    these O(whole-DB) steps once per document. (The concept stages that used to
    sit between them — 4a/4b/4c — were retired 2026-08-12.)"""
    t0 = time.time()
    db = Path(args.db).resolve()
    shim = args.ipv4_shim
    log(f"DB-STEPS-ONLY — {_display_path(db)}")

    step3b_registry(db)

    if not args.no_sync_bq:
        step7_sync_bq(db)

    c = db_counts(db)
    print("\n" + "#" * 60)
    print("# DB-STEPS-ONLY DONE")
    print(f"#   sections : {c['sections']}")
    print(f"#   tables   : {c['tables']}")
    print(f"#   rows     : {c['rows']}")
    print(f"#   cells    : {c['cells']}")
    print(f"#   elapsed  : {time.time() - t0:.1f}s")
    print("#" * 60)
    return 0


# ===========================================================================
# CLI
# ===========================================================================
FS_ROOT = FINDOCIQ / "data" / "sources" / "financial_statements"
PILLAR3_ROOT = FINDOCIQ / "data" / "sources" / "pillar3"
# every source root --all sweeps; family (fs vs pillar3) is still decided per-doc
# by the content classifier (classify_family), not by which of these it's under.
SOURCE_ROOTS = [FS_ROOT, PILLAR3_ROOT]


def _loaded_doc_ids(db: Path) -> set[str]:
    if not db.exists():
        return set()
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute(
            "SELECT DISTINCT doc_id FROM table_t")}
    finally:
        con.close()


def run_all(args) -> int:
    """--all: sweep every PDF under SOURCE_ROOTS (financial_statements/ +
    pillar3/) that is not yet extracted+loaded (present in table_t). Per-doc
    failures don't stop the sweep; the summary names them and the exit code
    reflects them. The final BigQuery sync runs ONCE after the whole sweep
    (each per-doc run_one call is forced --no-sync-bq) rather than once per
    doc — a full-table sync per doc doesn't scale to a batch."""
    db = Path(args.db).resolve()
    done = _loaded_doc_ids(db)
    # GCS is the source of truth: materialize every source into the local cache
    # (data/sources/, gitignored) before the disk sweep the rest of --all uses.
    for key in source_store.list_sources():
        source_store.materialize(key)
    pdfs = sorted({p for root in SOURCE_ROOTS if root.exists() for p in root.rglob("*.pdf")})
    bank = args.bank.strip().upper() if getattr(args, "bank", None) else None
    if bank:
        # institution is content-derived (filename OR placement dir — see
        # stage1_extract.route.family.institution_from_path), never a path-string hack.
        pdfs = [p for p in pdfs if _classify_row(p).get("institution") == bank]
    todo, skipped = [], []
    for pdf in pdfs:
        (skipped if doc_id_for(pdf) in done and not args.force else todo).append(pdf)
    roots_disp = " + ".join(str(r.relative_to(REPO)) for r in SOURCE_ROOTS)
    bank_disp = f" bank={bank}" if bank else ""
    print(f"--all{bank_disp}: {len(pdfs)} PDFs under {roots_disp}; "
          f"{len(skipped)} already loaded (skip), {len(todo)} to run")
    if args.dry_run:
        for pdf in todo:
            print(f"   [todo]    {pdf.relative_to(REPO)}")
        for pdf in skipped:
            print(f"   [skip]    {pdf.relative_to(REPO)}")
        return 0
    results: list[tuple[str, int]] = []
    for i, pdf in enumerate(todo, 1):
        print(f"\n{'='*70}\n[{i}/{len(todo)}] {pdf.relative_to(REPO)}\n{'='*70}")
        sub = argparse.Namespace(**{**vars(args), "pdf": str(pdf),
                                    "doc_period": None, "all": False,
                                    "no_sync_bq": True})
        try:
            rc = run_one(sub)
        except SystemExit as e:                    # run_one sys.exit()s on hard errors
            print(f"  FAILED: {e}")
            rc = 1
        except Exception as e:                      # noqa: BLE001 — a bug in ONE
            # doc's extraction/load (e.g. an un-merged continuation fragment)
            # must not abort the other N-1 docs' worth of already-paid-for work.
            print(f"  FAILED ({type(e).__name__}): {e}")
            rc = 1
        results.append((doc_id_for(pdf), rc))

    # --defer-db-steps: concepts/fact_metric/ratios were skipped for every doc
    # above, so a sync now would push a DB missing this whole batch's derived
    # data — leave it to the operator's separate --db-steps-only pass instead.
    if args.defer_db_steps:
        print("\n[defer] sweep-level sync_bq SKIPPED — run "
              f"`--db-steps-only --db {db}` to build concepts/fact_metric/"
              "ratios and sync.")
    elif not args.no_sync_bq and any(rc == 0 for _, rc in results):
        step7_sync_bq(db)

    print("\n" + "#" * 60 + "\n# SWEEP SUMMARY")
    for doc_id, rc in results:
        print(f"#   {'OK  ' if rc == 0 else 'FAIL'}  {doc_id}")
    print(f"#   skipped (already loaded): {len(skipped)}")
    print("#" * 60)
    return 1 if any(rc for _, rc in results) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", help="source FS PDF (required unless --rebuild-db/--all/"
                    "--db-steps-only)")
    ap.add_argument("--all", action="store_true",
                    help="sweep every PDF under data/sources/{financial_statements,"
                         "pillar3} not yet in the DB (per-doc failures don't stop "
                         "the sweep)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --all: print the todo/skip plan; run nothing, spend $0")
    ap.add_argument("--bank", default=None,
                    help="with --all: restrict the sweep to DBS|OCBC|UOB")
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help=f"schema_v7 sqlite DB (default {DEFAULT_DB.relative_to(REPO)})")
    ap.add_argument("--stage1", action="store_true",
                    help="STAGE 1 extract: scan, TOC, PASS2, geometry -> "
                         "outputs/fs/<bank>_<period>/ (xlsx + audit artifacts)")
    ap.add_argument("--stage2", action="store_true",
                    help="STAGE 2 load: load_units, verify, db_check_xlsx, sync_bq "
                         "-> --db (compiled_fs.db). Row identity (canonical_leaf_id "
                         "+ table_type_id) is stamped HERE, by load_v7.")
    ap.add_argument("--stage3", action="store_true",
                    help="STAGE 3 serve: build_compiled_v2 -> --compiled-v2 "
                         "(compiled_v2.db, the DB the app reads). CARRIES the stamps "
                         "stage 2 resolved; it does not re-stamp. DELETES its target "
                         "and rebuilds it. Never runs unless asked.")
    ap.add_argument("--compiled-v2", dest="compiled_v2",
                    default=str(FINDOCIQ / "db" / "compiled_v2.db"),
                    help="STAGE 3 target (default findociq/db/compiled_v2.db)")
    ap.add_argument("--doc-period", default=None,
                    help="override the inferred 'as at' date (YYYY-MM-DD)")
    ap.add_argument("--batch", action="store_true",
                    help="extraction via Gemini Batch API (async, 50%% cost)")
    ap.add_argument("--force", action="store_true",
                    help="re-extract units even when an audit parsed.json exists")
    ap.add_argument("--seed-registry", action="store_true",
                    help="run STEP 3b (seed table_registry + classify corpus). OFF by default; needed only by the masterlist authoring flow.")
    # STEP 7 IS OFF BY DEFAULT since GCP was retired (2026-08-14). There is no
    # project or dataset for it to reach, so leaving it on meant every run
    # attempted a sync that could only fail — noise in the log and a 'failed'
    # row in ingest_status for a step nobody wants. The flag keeps its dest so
    # the six `args.no_sync_bq` call sites are unchanged; --sync-bq opts back in
    # for anyone who restores a dataset.
    ap.add_argument("--sync-bq", dest="no_sync_bq", action="store_false",
                    default=True,
                    help="run the BigQuery sync (STEP 7). OFF by default — GCP "
                         "is retired and the step cannot succeed without it.")
    ap.add_argument("--no-sync-bq", dest="no_sync_bq", action="store_true",
                    help="(default) skip the BigQuery sync step")
    ap.add_argument("--ipv4-shim", dest="ipv4_shim", action="store_true", default=True,
                    help="prepend an AF_INET getaddrinfo shim (default ON)")
    ap.add_argument("--no-ipv4-shim", dest="ipv4_shim", action="store_false",
                    help="disable the IPv4 shim")
    ap.add_argument("--rebuild-db", action="store_true",
                    help="rebuild the WHOLE db from schema_v7 + every cached TOC "
                         "with a matching audit dir (ignores --pdf)")
    ap.add_argument("--only", default=None,
                    help="--rebuild-db: comma-separated substrings; keep only the "
                         "doc_ids matching one of them (e.g. '4Q25,1Q26,2Q26') so "
                         "the rebuild covers just the maintained corpus")
    ap.add_argument("--verify-only", action="store_true",
                    help="load-from-artifacts + verify only, no extraction ($0)")
    ap.add_argument("--defer-db-steps", action="store_true",
                    help="skip the WHOLE-DB steps for this document — concepts "
                         "(STEP 4a), fact_metric (STEP 4b), ratios (STEP 4c), and "
                         "sync_bq (STEP 7) — and print what to run afterward. For "
                         "batch sweeps of many documents, where each of these steps "
                         "is O(whole DB) and today gets re-run once per document; "
                         "pair with --db-steps-only run ONCE after the sweep.")
    ap.add_argument("--db-steps-only", action="store_true",
                    help="run ONLY the whole-DB steps — concepts -> fact_metric -> "
                         "ratios -> sync_bq (in that order) — against --db; no --pdf/ "
                         "document required. Companion to --defer-db-steps: the "
                         "operator runs this once after a batch sweep. Respects "
                         "--no-llm and --no-sync-bq. Ignores --pdf.")
    args = ap.parse_args(argv)

    if args.db_steps_only:
        return run_db_steps_only(args)
    if args.rebuild_db:
        return run_rebuild(args)
    if args.all:
        return run_all(args)
    # STAGE 3 is whole-DB: it reads --db and writes --compiled-v2, and needs no
    # document. `--stage3` on its own is therefore a valid, doc-free invocation
    # ("just stamp"). Only when it is the ONLY stage asked for — combined with
    # 1 or 2 it is the tail of a per-document run and still needs the --pdf.
    if args.stage3 and not args.stage1 and not args.stage2 and not args.pdf:
        step8_serve(Path(args.db).resolve(), Path(args.compiled_v2).resolve())
        return 0
    if not args.pdf:
        ap.error("--pdf is required (or use --rebuild-db / --all / --db-steps-only "
                 "/ a bare --stage3)")
    if args.verify_only:
        return run_verify_only(args)
    return run_one(args)


if __name__ == "__main__":
    raise SystemExit(main())
