# GCS Source Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GCS the sole source of truth for raw source PDFs — the pipeline materializes each PDF from the bucket on demand, the scraper uploads to the bucket, and the ingest tracker records each document's GCS URI.

**Architecture:** A single new module `pipeline/source_store.py` is the only place that talks to the sources bucket. Every raw-PDF read/write routes through it. A single canonical key `K = "<folder>/<file>.pdf"` (folder ∈ {financial_statements, pillar3}, relative to `findociq/data/sources/`) is the one identity from which the GCS URI, the local materialized path, `ingest_status.source_file`, and `doc_id` all deterministically derive. Local `data/sources/` becomes an ephemeral, gitignored materialization cache.

**Tech Stack:** Python 3, `google-cloud-storage` (already a dependency, used by `retry_worker.py`), `pytest`, SQLite (`ingest_status` table), Streamlit (dashboard).

## Global Constraints

- **Canonical key** `K = "<folder>/<file>.pdf"`, folder ∈ {`financial_statements`, `pillar3`}, relative to `SOURCES_ROOT = findociq/data/sources`.
- **Four derived quantities, all pure functions of K:** `gcs_uri(K)=f"gs://{bucket}/data/sources/{K}"`; `local_path(K)=SOURCES_ROOT/K`; `ingest_status.source_file == K` (bare key — no `findociq/`, no `data/sources/`); `doc_id(K)=Path(K).stem.replace(" ","_")`.
- **Bucket:** `findociq-sources-igc2026-team08-6311`, override via env `GCS_BUCKET`. GCS object prefix is `data/sources/` (no `findociq/`).
- **`gcs_uri` is ALWAYS derived from K, never stored as an independent free string.**
- Runs entirely under `roles/editor` (verified). No new IAM needed.
- Follow existing repo patterns: modules derive `REPO = Path(__file__).resolve().parents[2]`, `FINDOCIQ = REPO/"findociq"`. Pure helpers unit-tested with no I/O; GCS calls mocked in tests.
- DRY, YAGNI, TDD, frequent commits.

---

### Task 1: `source_store.py` — the GCS choke point

**Files:**
- Create: `findociq/pipeline/source_store.py`
- Test: `findociq/pipeline/test_source_store.py`

**Interfaces:**
- Produces (pure, no GCS client): `bucket_name() -> str`, `key_for(local_path) -> str`, `local_path(key: str) -> Path`, `uri(key: str) -> str`, `doc_id_for(key_or_path) -> str`, `gcs_uri_for_source(source_file: str) -> str`.
- Produces (GCS-backed): `list_sources() -> list[str]`, `exists(key) -> bool`, `materialize(key) -> Path`, `upload(local_file, key) -> str`, `resolve_to_local(arg: str) -> Path`.
- Constants: `SOURCES_ROOT`, `GCS_SOURCES_PREFIX = "data/sources/"`, `DEFAULT_BUCKET`.

- [ ] **Step 1: Write the failing test for the pure path helpers**

Create `findociq/pipeline/test_source_store.py`:

```python
import os
from pathlib import Path

import source_store as ss


def test_key_for_strips_sources_root():
    p = ss.SOURCES_ROOT / "financial_statements" / "DBS_1Q25_trading_update.pdf"
    assert ss.key_for(p) == "financial_statements/DBS_1Q25_trading_update.pdf"


def test_local_path_roundtrips_key():
    k = "pillar3/OCBC_1Q26_pillar3.pdf"
    assert ss.key_for(ss.local_path(k)) == k


def test_uri_uses_prefix_and_bucket(monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    k = "financial_statements/DBS_1Q25_trading_update.pdf"
    assert ss.uri(k) == (
        "gs://findociq-sources-igc2026-team08-6311/"
        "data/sources/financial_statements/DBS_1Q25_trading_update.pdf")


def test_uri_honors_env_override(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    assert ss.uri("pillar3/x.pdf") == "gs://my-bucket/data/sources/pillar3/x.pdf"


def test_doc_id_for_matches_run_doc_convention():
    assert ss.doc_id_for("financial_statements/OCBC 1H25 FS.pdf") == "OCBC_1H25_FS"


def test_gcs_uri_for_source_is_derived_from_bare_key(monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    # source_file (== K) -> gs:// uri, no double prefixing
    assert ss.gcs_uri_for_source("financial_statements/DBS_1Q25_trading_update.pdf") == (
        "gs://findociq-sources-igc2026-team08-6311/"
        "data/sources/financial_statements/DBS_1Q25_trading_update.pdf")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd findociq/pipeline && python3 -m pytest test_source_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'source_store'`.

- [ ] **Step 3: Write `source_store.py` (pure helpers + lazy GCS ops)**

Create `findociq/pipeline/source_store.py`:

```python
"""source_store.py — the single choke point between the pipeline and the GCS
sources bucket. GCS is the SOLE source of truth for raw PDFs; local
findociq/data/sources/ is an ephemeral, gitignored materialization cache.

Canonical source key:
    K = "<folder>/<file>.pdf"   folder in {financial_statements, pillar3},
                                relative to SOURCES_ROOT.
Everything derives from K:
    uri(K)          = gs://<bucket>/data/sources/<K>
    local_path(K)   = SOURCES_ROOT / K
    ingest_status.source_file == K            (bare key)
    doc_id_for(K)   = Path(K).stem, spaces -> underscores  (matches run_doc)

The pure path helpers do NOT import google.cloud.storage, so callers that only
need a URI (e.g. the dashboard) pay no GCS dependency. The client is imported
lazily inside the GCS-backed functions.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # pipeline -> findociq -> repo
FINDOCIQ = REPO / "findociq"
SOURCES_ROOT = FINDOCIQ / "data" / "sources"
GCS_SOURCES_PREFIX = "data/sources/"
DEFAULT_BUCKET = "findociq-sources-igc2026-team08-6311"


# --- pure path helpers (no GCS client) -------------------------------------
def bucket_name() -> str:
    return os.environ.get("GCS_BUCKET", DEFAULT_BUCKET)


def key_for(local_file) -> str:
    """Local path under SOURCES_ROOT -> canonical key K (posix, forward slashes)."""
    rel = os.path.relpath(Path(local_file).resolve(), SOURCES_ROOT)
    return Path(rel).as_posix()


def local_path(key: str) -> Path:
    return SOURCES_ROOT / key


def uri(key: str) -> str:
    return f"gs://{bucket_name()}/{GCS_SOURCES_PREFIX}{key}"


def gcs_uri_for_source(source_file: str) -> str:
    """ingest_status.source_file (== K after the rekey migration) -> gs:// uri."""
    return uri(source_file)


def doc_id_for(key_or_path) -> str:
    return Path(key_or_path).stem.replace(" ", "_")


# --- GCS-backed ops (lazy client) ------------------------------------------
def _bucket():
    from google.cloud import storage
    return storage.Client().bucket(bucket_name())


def list_sources() -> list[str]:
    """Every .pdf blob under data/sources/, returned as canonical keys K."""
    out = []
    for blob in _bucket().list_blobs(prefix=GCS_SOURCES_PREFIX):
        if blob.name.endswith("/") or not blob.name.endswith(".pdf"):
            continue
        out.append(blob.name[len(GCS_SOURCES_PREFIX):])
    return sorted(out)


def exists(key: str) -> bool:
    return _bucket().blob(GCS_SOURCES_PREFIX + key).exists()


def materialize(key: str) -> Path:
    """Ensure local_path(key) exists (download from GCS if absent or size-stale);
    return the local path. Idempotent; size-verified. Raises FileNotFoundError
    if the source blob does not exist and there is no local cache.

    NOTE: check blob.exists() ONCE before any blob.reload() — the real GCS
    client raises NotFound on reload() of a missing blob, so a cached-local +
    vanished-remote case must not call reload()."""
    dest = local_path(key)
    blob = _bucket().blob(GCS_SOURCES_PREFIX + key)
    remote_exists = blob.exists()
    if dest.exists():
        if not remote_exists:
            return dest  # remote gone/unreadable; trust the local cache
        blob.reload()
        if blob.size is None or dest.stat().st_size == blob.size:
            return dest
        # fall through: cached file size differs from remote -> re-download
    if not remote_exists:
        raise FileNotFoundError(f"no source blob for key {key!r} at {uri(key)}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(dest))
    blob.reload()
    if blob.size is not None and dest.stat().st_size != blob.size:
        raise IOError(
            f"size mismatch after download of {key!r}: "
            f"local {dest.stat().st_size} != remote {blob.size}")
    return dest


def upload(local_file, key: str) -> str:
    """Upload a local PDF to gs://<bucket>/data/sources/<key>; return its uri."""
    _bucket().blob(GCS_SOURCES_PREFIX + key).upload_from_filename(str(local_file))
    return uri(key)


def resolve_to_local(arg: str) -> Path:
    """Turn a run_doc --pdf argument into a local path. Accepts a local path
    (used as-is), a gs:// uri, or a bare key K (materialized from GCS)."""
    if Path(arg).exists():
        return Path(arg).resolve()
    prefix = f"gs://{bucket_name()}/{GCS_SOURCES_PREFIX}"
    key = arg[len(prefix):] if arg.startswith(prefix) else arg
    return materialize(key)
```

- [ ] **Step 4: Run the pure-helper tests to verify they pass**

Run: `cd findociq/pipeline && python3 -m pytest test_source_store.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Add GCS-backed tests with a mocked client**

Append to `findociq/pipeline/test_source_store.py`:

```python
import types
import pytest
import source_store as ss


class _FakeBlob:
    def __init__(self, name, size=None, present=True):
        self.name = name
        self.size = size
        self._present = present
        self.downloaded_to = None
        self.uploaded_from = None

    def exists(self):
        return self._present

    def reload(self):
        pass

    def download_to_filename(self, path):
        self.downloaded_to = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x" * (self.size or 1))

    def upload_from_filename(self, path):
        self.uploaded_from = path


class _FakeBucket:
    def __init__(self, blobs):
        self._blobs = {b.name: b for b in blobs}

    def list_blobs(self, prefix):
        return [b for n, b in self._blobs.items() if n.startswith(prefix)]

    def blob(self, name):
        return self._blobs.get(name) or _FakeBlob(name, present=False)


def test_list_sources_returns_keys(monkeypatch):
    bucket = _FakeBucket([
        _FakeBlob("data/sources/financial_statements/DBS_1Q25_trading_update.pdf"),
        _FakeBlob("data/sources/pillar3/OCBC_1Q26_pillar3.pdf"),
        _FakeBlob("data/sources/NAMING.md"),          # non-pdf, skipped
        _FakeBlob("data/sources/financial_statements/"),  # dir marker, skipped
    ])
    monkeypatch.setattr(ss, "_bucket", lambda: bucket)
    assert ss.list_sources() == [
        "financial_statements/DBS_1Q25_trading_update.pdf",
        "pillar3/OCBC_1Q26_pillar3.pdf",
    ]


def test_materialize_downloads_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SOURCES_ROOT", tmp_path)
    blob = _FakeBlob("data/sources/financial_statements/x.pdf", size=5)
    monkeypatch.setattr(ss, "_bucket", lambda: _FakeBucket([blob]))
    p = ss.materialize("financial_statements/x.pdf")
    assert p == tmp_path / "financial_statements/x.pdf"
    assert p.read_bytes() == b"xxxxx"


def test_materialize_noops_when_present_and_same_size(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SOURCES_ROOT", tmp_path)
    dest = tmp_path / "financial_statements/x.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"xxxxx")
    blob = _FakeBlob("data/sources/financial_statements/x.pdf", size=5)
    monkeypatch.setattr(ss, "_bucket", lambda: _FakeBucket([blob]))
    ss.materialize("financial_statements/x.pdf")
    assert blob.downloaded_to is None   # did not re-download


def test_materialize_missing_blob_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SOURCES_ROOT", tmp_path)
    monkeypatch.setattr(ss, "_bucket", lambda: _FakeBucket([]))
    with pytest.raises(FileNotFoundError):
        ss.materialize("financial_statements/missing.pdf")
```

- [ ] **Step 6: Run the full test file to verify it passes**

Run: `cd findociq/pipeline && python3 -m pytest test_source_store.py -v`
Expected: PASS (10 passed).

- [ ] **Step 7: Commit**

```bash
git add findociq/pipeline/source_store.py findociq/pipeline/test_source_store.py
git commit -m "feat(sources): add source_store.py GCS choke point (canonical key K)"
```

---

### Task 2: Rewire `run_doc.py` to source PDFs from GCS

**Files:**
- Modify: `findociq/pipeline/run_doc.py` (import; `run_one` `:504-509`; `run_all` `:781-782`)
- Test: `findociq/pipeline/test_run_doc.py` (add cases; file already exists per module docstring at `run_doc.py:60`)

**Interfaces:**
- Consumes: `source_store.resolve_to_local`, `source_store.key_for`, `source_store.list_sources`, `source_store.materialize` (Task 1).
- Produces: `run_one`/`run_all` accept a GCS key or gs:// uri as `--pdf`, and `--all` sweeps the bucket. `source_file` written to `ingest_status` is now the bare key K.

- [ ] **Step 1: Write the failing test for key-based source_file**

Add to `findociq/pipeline/test_run_doc.py`:

```python
import source_store as ss
from run_doc import doc_id_for


def test_source_file_is_bare_canonical_key():
    # a materialized PDF's ingest_status key must be the bare canonical key,
    # NOT a findociq/data/sources/... relpath.
    p = ss.SOURCES_ROOT / "financial_statements" / "DBS_1Q25_trading_update.pdf"
    assert ss.key_for(p) == "financial_statements/DBS_1Q25_trading_update.pdf"
    assert doc_id_for(p) == "DBS_1Q25_trading_update"
```

- [ ] **Step 2: Run it to confirm the helper alignment (this passes; it pins the contract)**

Run: `cd findociq/pipeline && python3 -m pytest test_run_doc.py::test_source_file_is_bare_canonical_key -v`
Expected: PASS. (Contract guard — asserts key_for/doc_id agree on the key that Step 3 will start writing.)

- [ ] **Step 3: Import source_store and re-point `run_one`**

In `findociq/pipeline/run_doc.py`, add to the imports near `import ingest_status` (line 46):

```python
import ingest_status
import source_store
```

Replace `run_one`'s head at `run_doc.py:504-509`:

```python
    pdf = Path(args.pdf).resolve()
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}")
    doc_id = doc_id_for(pdf)
    db = Path(args.db).resolve()
    source_file = os.path.relpath(pdf, REPO)
```

with:

```python
    try:
        pdf = source_store.resolve_to_local(args.pdf)
    except FileNotFoundError as e:
        sys.exit(str(e))
    doc_id = doc_id_for(pdf)
    db = Path(args.db).resolve()
    source_file = source_store.key_for(pdf)
```

- [ ] **Step 4: Re-point `run_all` to materialize from the bucket first**

Replace `run_doc.py:782`:

```python
    pdfs = sorted({p for root in SOURCE_ROOTS if root.exists() for p in root.rglob("*.pdf")})
```

with:

```python
    # GCS is the source of truth: materialize every source into the local cache
    # (data/sources/, gitignored) before the disk sweep the rest of --all uses.
    for key in source_store.list_sources():
        source_store.materialize(key)
    pdfs = sorted({p for root in SOURCE_ROOTS if root.exists() for p in root.rglob("*.pdf")})
```

- [ ] **Step 5: Run the run_doc unit tests to verify nothing regressed**

Run: `cd findociq/pipeline && python3 -m pytest test_run_doc.py -v`
Expected: PASS (existing tests + the new one).

- [ ] **Step 6: Commit**

```bash
git add findociq/pipeline/run_doc.py findociq/pipeline/test_run_doc.py
git commit -m "feat(sources): run_doc sources PDFs from GCS; source_file is bare key K"
```

---

### Task 3: Rewire `retry_worker.py` onto `source_store`

**Files:**
- Modify: `findociq/pipeline/retry_worker.py` (import; `pull_from_gcs` sources loop `:72-81`; `eligible_pdfs` `:107`)

**Interfaces:**
- Consumes: `source_store.key_for`, `source_store.list_sources`, `source_store.materialize`.
- Produces: `retry_worker` computes `source_file` as the bare key K (matching `run_doc.py`), removing the `findociq/` prefix discrepancy.

- [ ] **Step 1: Write the failing test — retry_worker's key must equal run_doc's**

Create `findociq/pipeline/test_retry_worker.py`:

```python
import source_store as ss
import retry_worker as rw


def test_retry_worker_source_file_matches_source_store_key():
    p = ss.SOURCES_ROOT / "pillar3" / "OCBC_1Q26_pillar3.pdf"
    # retry_worker must key ingest_status the same way run_doc does.
    assert rw._source_file_for(p) == ss.key_for(p) == "pillar3/OCBC_1Q26_pillar3.pdf"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd findociq/pipeline && python3 -m pytest test_retry_worker.py -v`
Expected: FAIL — `AttributeError: module 'retry_worker' has no attribute '_source_file_for'`.

- [ ] **Step 3: Add the import, a shared key helper, and re-point the sources pull**

In `findociq/pipeline/retry_worker.py`, add to imports after `from google.cloud import storage` (line 39) — but keep it below the `sys.path.insert` block; move the source_store import next to the other pipeline imports at line 52-54:

```python
sys.path.insert(0, str(FINDOCIQ / "pipeline"))
import ingest_status                                          # noqa: E402
import source_store                                           # noqa: E402
from run_doc import SOURCE_ROOTS, doc_id_for, _loaded_doc_ids  # noqa: E402


def _source_file_for(pdf) -> str:
    return source_store.key_for(pdf)
```

Replace the sources-download loop in `pull_from_gcs` at `retry_worker.py:72-81`:

```python
    n = 0
    for blob in bkt.list_blobs(prefix=GCS_SOURCES_PREFIX):
        if blob.name.endswith("/"):
            continue
        rel = blob.name[len(GCS_SOURCES_PREFIX):]
        dest = SOURCES_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(dest))
        n += 1
    print(f"pulled {n} source file(s) -> {SOURCES_ROOT}")
```

with:

```python
    keys = source_store.list_sources()
    for key in keys:
        source_store.materialize(key)
    print(f"pulled {len(keys)} source file(s) -> {SOURCES_ROOT}")
```

- [ ] **Step 4: Re-point `eligible_pdfs` source_file computation**

Replace `retry_worker.py:107`:

```python
        source_file = os.path.relpath(pdf, REPO)
```

with:

```python
        source_file = _source_file_for(pdf)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd findociq/pipeline && python3 -m pytest test_retry_worker.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add findociq/pipeline/retry_worker.py findociq/pipeline/test_retry_worker.py
git commit -m "refactor(sources): retry_worker keys ingest_status by bare key K via source_store"
```

---

### Task 4: One-time `ingest_status` rekey migration

**Files:**
- Create: `findociq/pipeline/migrate_ingest_status_keys.py`
- Test: `findociq/pipeline/test_migrate_ingest_status_keys.py`

**Interfaces:**
- Consumes: nothing from prior tasks (pure SQLite).
- Produces: `rekey(old_source_file: str) -> str`, `migrate(db_path: str) -> int` (returns rows changed).

Rationale: existing `ingest_status.source_file` rows store `findociq/data/sources/<...>` (and any nested `bank/year/quarter`). After Tasks 2-3 the code writes the bare key K. Without a rekey, every already-tracked doc would read as never-attempted.

- [ ] **Step 1: Write the failing test**

Create `findociq/pipeline/test_migrate_ingest_status_keys.py`:

```python
import sqlite3
import migrate_ingest_status_keys as mig


def _db(tmp_path, rows):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ingest_status (source_file TEXT PRIMARY KEY, doc_id TEXT)")
    con.executemany("INSERT INTO ingest_status(source_file, doc_id) VALUES (?, ?)", rows)
    con.commit(); con.close()
    return str(db)


def test_rekey_strips_findociq_data_sources_prefix():
    assert mig.rekey("findociq/data/sources/financial_statements/DBS_1Q25_trading_update.pdf") \
        == "financial_statements/DBS_1Q25_trading_update.pdf"


def test_rekey_collapses_nested_bank_year_quarter():
    assert mig.rekey("findociq/data/sources/financial_statements/DBS/2025/1/x.pdf") \
        == "financial_statements/x.pdf"


def test_rekey_is_idempotent_on_bare_key():
    assert mig.rekey("pillar3/OCBC_1Q26_pillar3.pdf") == "pillar3/OCBC_1Q26_pillar3.pdf"


def test_migrate_updates_rows(tmp_path):
    db = _db(tmp_path, [
        ("findociq/data/sources/financial_statements/DBS_1Q25_trading_update.pdf", "DBS_1Q25_trading_update"),
        ("pillar3/OCBC_1Q26_pillar3.pdf", "OCBC_1Q26_pillar3"),  # already bare
    ])
    changed = mig.migrate(db)
    assert changed == 1
    con = sqlite3.connect(db)
    keys = {r[0] for r in con.execute("SELECT source_file FROM ingest_status")}
    con.close()
    assert keys == {
        "financial_statements/DBS_1Q25_trading_update.pdf",
        "pillar3/OCBC_1Q26_pillar3.pdf",
    }


def test_migrate_is_idempotent_second_run(tmp_path):
    db = _db(tmp_path, [
        ("findociq/data/sources/pillar3/x.pdf", "x"),
    ])
    assert mig.migrate(db) == 1
    assert mig.migrate(db) == 0   # nothing left to change
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd findociq/pipeline && python3 -m pytest test_migrate_ingest_status_keys.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migrate_ingest_status_keys'`.

- [ ] **Step 3: Write the migration**

Create `findociq/pipeline/migrate_ingest_status_keys.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd findociq/pipeline && python3 -m pytest test_migrate_ingest_status_keys.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add findociq/pipeline/migrate_ingest_status_keys.py findociq/pipeline/test_migrate_ingest_status_keys.py
git commit -m "feat(sources): one-time ingest_status source_file rekey migration"
```

---

### Task 5: Surface `gcs_uri` in the dashboard status view / export

**Files:**
- Modify: `findociq/app/dashboard.py` (the `tab_status` block, `:394-438`)
- Test: `findociq/pipeline/test_source_store.py` (the `gcs_uri_for_source` behavior is already covered in Task 1; add a doc_id→uri derivation guard here)

**Interfaces:**
- Consumes: `source_store.gcs_uri_for_source(source_file)` (Task 1).
- Produces: the status table (and its CSV download) gains a `gcs_uri` column derived from `ingest_status.source_file`.

Note: `sync_bq.py:35` already syncs `ingest_status`, so `source_file` is available in both the SQLite and BQ backends. `gcs_uri` is derived as a pure string — no bucket access from the dashboard.

- [ ] **Step 1: Write the failing test — the doc→uri derivation the dashboard will use**

Add to `findociq/pipeline/test_source_store.py`:

```python
def test_dashboard_derivation_from_source_file(monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    # what the dashboard does per ingest_status row:
    source_file = "financial_statements/OCBC_4Q25_Condensed_Financial_Statements.pdf"
    assert ss.gcs_uri_for_source(source_file).endswith(
        "/data/sources/financial_statements/OCBC_4Q25_Condensed_Financial_Statements.pdf")
```

- [ ] **Step 2: Run it to verify it passes (contract guard for the dashboard change)**

Run: `cd findociq/pipeline && python3 -m pytest test_source_store.py::test_dashboard_derivation_from_source_file -v`
Expected: PASS.

- [ ] **Step 3: Read the current status-tab query and column list**

Run: `sed -n '390,440p' findociq/app/dashboard.py`
Expected: shows the `SELECT ... FROM ingest_status WHERE doc_id IS NOT NULL` query (`:400`), the per-doc status aggregation, and `keep = ["bank","year","quarter","family","doc_type","status", ...]` (`:438`). Confirm `source_file` is (or can be) selected alongside `doc_id`.

- [ ] **Step 4: Add `source_file` to the ingest_status query and derive `gcs_uri`**

In `findociq/app/dashboard.py`, add the import near the top with the other pipeline imports (the file already does `from ingest_manifest import _db_coverage` inside `tab_status`; add beside it):

```python
        from ingest_manifest import _db_coverage  # noqa: E402
        import source_store  # noqa: E402
```

Change the `ingest_status` SELECT at `dashboard.py:400` to also fetch `source_file`:

```python
                          FROM {TBL('ingest_status')} WHERE doc_id IS NOT NULL""")
```

becomes (add `source_file` to the selected columns — keep the rest of the SELECT list intact):

```python
                          SELECT source_file, doc_id, bank, period, family, stage, state
                          FROM {TBL('ingest_status')} WHERE doc_id IS NOT NULL""")
```

After the per-row `mf` frame is built and before `keep = [...]` (`:438`), add a derived `gcs_uri` column mapping each row's doc_id(s) to their GCS URI via the ingest_status `source_file`:

```python
        # gcs_uri: derived from ingest_status.source_file (== canonical key K),
        # pure-string, no bucket call. Multiple docs per manifest row -> joined.
        uri_by_doc = {s.doc_id: source_store.gcs_uri_for_source(s.source_file)
                      for s in ist_rows if getattr(s, "source_file", None)}

        def _gcs_uris(row) -> str:
            ids = [d for d in str(row.get("doc_ids", "")).split(",") if d]
            return ", ".join(uri_by_doc[d] for d in ids if d in uri_by_doc)

        mf["gcs_uri"] = mf.apply(_gcs_uris, axis=1)
```

Add `"gcs_uri"` to the `keep` list at `:438`:

```python
        keep = ["bank", "year", "quarter", "family", "doc_type", "status",
                "gcs_uri", ...]   # keep the remaining existing columns unchanged
```

(Where `ist_rows` is the row objects from the `ingest_status` SELECT and `mf["doc_ids"]` is the existing comma-joined doc_ids column the export already produces. If the variable holding those rows is named differently in the file, use that name — confirm in Step 3.)

- [ ] **Step 5: Verify the dashboard imports and renders without error**

Run: `cd findociq && FINDOCIQ_DB_SOURCE=sqlite python3 -c "import ast; ast.parse(open('app/dashboard.py').read()); print('dashboard.py parses OK')"`
Expected: `dashboard.py parses OK`.
Then, if a local DB is present: `cd findociq && FINDOCIQ_DB_SOURCE=sqlite streamlit run app/dashboard.py --server.headless true` and confirm the Ingest Status tab shows a `gcs_uri` column populated with `gs://…` values. Stop the server after visual confirmation.

- [ ] **Step 6: Run the source_store tests once more and commit**

Run: `cd findociq/pipeline && python3 -m pytest test_source_store.py -v`
Expected: PASS.

```bash
git add findociq/app/dashboard.py findociq/pipeline/test_source_store.py
git commit -m "feat(sources): show derived gcs_uri in dashboard ingest-status view/export"
```

---

### Task 6: Flatten the scraper's write side onto GCS

**Files:**
- Modify: `findociq/pipeline/ingest/scrape_bank_ir.py` (`_dest_path` `:219-224`; placement block `:342-346`; imports)
- Test: `findociq/pipeline/ingest/test_scrape_bank_ir.py` (create if absent)

**Interfaces:**
- Consumes: `source_store.upload`, `source_store.uri`.
- Produces: `_dest_key(url: str, family: str) -> str` returning the flat canonical key K; scraper uploads to GCS instead of writing nested local files.

Rationale (from invariant analysis): the scraper's nested `<bank>/<year>/<quarter>/` layout and single-`out_root`-for-both-families bug produce a `source_file` that differs from the same file pulled flat from the bucket. Flattening + family routing makes local layout == bucket layout == K.

- [ ] **Step 1: Write the failing test for the flat family-routed key**

Create `findociq/pipeline/ingest/test_scrape_bank_ir.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # findociq/pipeline
sys.path.insert(0, str(Path(__file__).resolve().parent))       # findociq/pipeline/ingest
import scrape_bank_ir as sc


def test_dest_key_routes_fs_to_financial_statements():
    assert sc._dest_key("https://www.dbs.com/iwov/DBS_1Q25_trading_update.pdf", "fs") \
        == "financial_statements/DBS_1Q25_trading_update.pdf"


def test_dest_key_routes_pillar3_to_pillar3():
    assert sc._dest_key("https://www.uobgroup.com/x/regulatory-disclosures-pillar-3-1q-2025.pdf", "pillar3") \
        == "pillar3/regulatory-disclosures-pillar-3-1q-2025.pdf"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd findociq/pipeline/ingest && python3 -m pytest test_scrape_bank_ir.py -v`
Expected: FAIL — `AttributeError: module 'scrape_bank_ir' has no attribute '_dest_key'`.

- [ ] **Step 3: Add `_dest_key` and route uploads through `source_store`**

In `findociq/pipeline/ingest/scrape_bank_ir.py`, add near the other pipeline imports at the top of the file:

```python
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # findociq/pipeline
import source_store  # noqa: E402
```

Add `_dest_key` next to `_dest_path` (keep `_dest_path` only if other callers use it; the placement block will switch to `_dest_key`):

```python
def _dest_key(url: str, family: str) -> str:
    """Flat canonical key K for a scraped PDF: family -> its source folder,
    filename = the URL basename. No bank/year/quarter nesting (that diverged
    from the flat bucket + SOURCE_ROOTS layout). General rule, not per-bank."""
    folder = "pillar3" if family == "pillar3" else "financial_statements"
    filename = Path(urlparse(url).path).name
    return f"{folder}/{filename}"
```

Replace the placement block at `scrape_bank_ir.py:342-346`:

```python
        dest = _dest_path(out_root, bank, url, row["period"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_path, dest)
        _cleanup_temp(tmp_path)
        print(f"[{bank}] placed -> {dest}")
```

with:

```python
        key = _dest_key(url, family)
        gcs = source_store.upload(tmp_path, key)
        _cleanup_temp(tmp_path)
        print(f"[{bank}] uploaded -> {gcs}")
```

- [ ] **Step 4: Run the scraper test to verify it passes**

Run: `cd findociq/pipeline/ingest && python3 -m pytest test_scrape_bank_ir.py -v`
Expected: PASS.

- [ ] **Step 5: Verify the module still imports (upload path is exercised only on a real scrape)**

Run: `cd findociq/pipeline/ingest && python3 -c "import scrape_bank_ir; print('scrape_bank_ir imports OK')"`
Expected: `scrape_bank_ir imports OK`.

- [ ] **Step 6: Commit**

```bash
git add findociq/pipeline/ingest/scrape_bank_ir.py findociq/pipeline/ingest/test_scrape_bank_ir.py
git commit -m "feat(sources): scraper uploads flat canonical keys to GCS (no local write)"
```

---

### Task 7: End-to-end smoke — materialize + ingest from an empty local cache

**Files:**
- None modified. Manual verification against the live bucket under `roles/editor`.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Run the migration against the real DB (idempotent)**

Run: `cd /Users/Qianyunhan/Desktop/Project_UOB/FinDocIQ && python3 findociq/pipeline/migrate_ingest_status_keys.py --db findociq/db/compiled_fs.db`
Expected: `rekeyed N ingest_status row(s) to canonical key K` (N ≥ 0; a second run prints `rekeyed 0`).

- [ ] **Step 2: Clear the local cache to prove GCS is the source of truth**

Run: `find findociq/data/sources -name '*.pdf' -delete && ls findociq/data/sources`
Expected: no `.pdf` files remain (only `manifest.csv` etc.).

- [ ] **Step 3: List sources from the bucket**

Run: `cd findociq/pipeline && python3 -c "import source_store as ss; print(len(ss.list_sources()), 'keys'); print(ss.list_sources()[:3])"`
Expected: a non-zero count and keys like `financial_statements/DBS_1Q25_trading_update.pdf`.

- [ ] **Step 4: Materialize + ingest one doc end-to-end from GCS**

Run: `cd /Users/Qianyunhan/Desktop/Project_UOB/FinDocIQ && PYTHONPATH=/tmp/paddle-scratch python3 findociq/pipeline/run_doc.py --pdf financial_statements/DBS_1Q25_trading_update.pdf --no-ipv4-shim --no-sync-bq`
Expected: run_doc downloads the PDF into `findociq/data/sources/financial_statements/`, then completes STEP 0→verify with `# DONE DBS_1Q25_trading_update` and `verify: PASS`. (STEP 0 skips paddle when a committed scan tag exists — see the ingest handoff.)

- [ ] **Step 5: Confirm ingest_status keyed by bare K**

Run: `cd findociq && python3 -c "import sqlite3; c=sqlite3.connect('db/compiled_fs.db'); print(c.execute(\"SELECT source_file, stage, state FROM ingest_status WHERE doc_id='DBS_1Q25_trading_update'\").fetchall())"`
Expected: `source_file` == `financial_statements/DBS_1Q25_trading_update.pdf` (bare key), stage `done`, state `ok`.

- [ ] **Step 6: Commit any docs/notes (no code change expected)**

```bash
git commit --allow-empty -m "test(sources): e2e smoke — GCS materialize + ingest under editor role"
```

---

## Self-Review

**Spec coverage:**
- Spec §Architecture 1 (source_store choke point) → Task 1. ✓
- §Architecture 2 (read-side wiring: run_doc --pdf/--all, retry_worker) → Tasks 2, 3. ✓
- §Architecture 3 (scraper flatten + upload) → Task 6. ✓
- §Architecture 4 (manifest/tracker gcs_uri) → Task 5. ✓
- §Data migration (ingest_status rekey) → Task 4. ✓
- §Code sites 1 (run_doc:509) → Task 2 Step 3; 2 (retry_worker:107) → Task 3 Step 4; 3 (scraper flatten) → Task 6; 4 (blob prefix unchanged) → preserved (source_store uses `data/sources/`); 5 (rekey) → Task 4; 6 (gcs_uri derived only) → Tasks 1+5; 8 (new module + entry routing) → Tasks 1-3. ✓
- §Testing (unit, invariant round-trip, scraper layout, migration, e2e) → Tasks 1,2,3,4,6,7. ✓
- §Correctness guard 5 (size-verify) → Task 1 `materialize`. ✓
- ingest_quarter.py:92 (spec §2) — NOTE: `ingest_quarter` calls `run_doc.py --pdf`/`--all` as a subprocess; once Task 2 lands, its docs materialize via run_doc. No separate change needed unless it globs disk directly; confirm during Task 2 review and, if it globs, apply the same `list_sources()`+`materialize` pattern. Flagged, not silently dropped.

**Code-site 7 (doc_id uniqueness guard / `<BANK>_<PERIOD>_` naming):** NOT a code change in this plan — it is a data-convention guard. Scraper filenames come from the URL basename; canonical `<BANK>_<PERIOD>_` renaming is deliberately OUT of scope here (it needs the classifier's doc_type, a separate reasoning task) and is tracked as follow-up. The existing corpus already follows NAMING.md, so no collision today. Documented, not forgotten.

**Placeholder scan:** the only ellipses are in Task 5 Step 4's `keep = [..., ...]` and the SELECT list, where they explicitly mean "preserve the file's existing columns verbatim" — the implementer confirms exact names in Step 3 (`sed` command provided). No TBD/TODO/"handle errors" placeholders.

**Type consistency:** `key`/`source_file` are `str` (canonical key K) throughout; `key_for`→str, `local_path`/`materialize`/`resolve_to_local`→`Path`, `uri`/`gcs_uri_for_source`/`upload`→str; `list_sources`→`list[str]`. `_source_file_for` (Task 3) == `source_store.key_for` (Task 1). `_dest_key` (Task 6) returns the same key form `list_sources` yields. Consistent.
