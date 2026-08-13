# GCS Source Migration — Design Spec

**Date:** 2026-07-29
**Status:** Approved (design), pending spec review
**Author:** orchestrator session (Fable) + deep-reasoner (Opus) invariant analysis

## Goal

Make **Google Cloud Storage the sole persistent source of truth** for raw source
PDFs. Local disk holds at most an ephemeral, gitignored materialization cache.
The extraction pipeline sources every raw PDF from GCS; the manifest/tracker
records each document's GCS location; the scraper writes downloads straight to
GCS. This is the read/write substrate for the end-state pipeline
(**user selects → system web-scrapes → PDFs land in GCS → orchestrator runs
ingestion, no human in the loop**).

Non-goal (explicitly out of scope): the *unattended service-account* execution of
that orchestrator (Phase C). That is gated by an IAM/admin wall this migration
deliberately does not depend on — see §7.

## Context / current state

- **Bucket already exists and already holds the corpus:** `gs://findociq-sources-igc2026-team08-6311`
  (ASIA-SOUTHEAST1), flat layout `data/sources/<folder>/<file>.pdf` where
  `folder ∈ {financial_statements, pillar3}`, plus `data/sources/NAMING.md`,
  `download_report.csv`, and `db/compiled_fs.db`. Verified live.
- **Local `data/sources/` holds only `manifest.csv`** — the PDFs are
  `.gitignore`/`.gcloudignore`/`.dockerignore`-excluded and already pulled from
  GCS at runtime by `retry_worker.py`. So this is *finishing* the wiring, not a
  from-scratch move.
- **A working GCS pull/push pattern already exists** in `pipeline/retry_worker.py`
  (`pull_from_gcs`/`push_to_gcs`), modeled here into a reusable module.
- **The ingest tracker is a DB table**, `ingest_status` (schema_v7.sql), keyed by
  `source_file`, marked per-PDF by `run_doc.py`. The CSV the user referenced
  (`2026-07-29T03-38_export.csv`) is a *grouped view* of that table
  (`doc_ids` comma-joined per `(bank, year, quarter, family, doc_type)`).

## Access / IAM reality (verified live)

- Active identity `yunhan@uobdmoedm.com` has **`roles/editor`** on the project.
  Editor includes full Cloud Storage object + bucket control — verified by a
  live write+delete probe in the bucket. **This entire migration needs no admin
  access.**
- The admin/IAM wall (`resourcemanager.projects.setIamPolicy`,
  service-account admin) only blocks: (a) making the dashboard public, and
  (b) **Phase C** — running the orchestrator as an unattended service account.
  Neither is required here.

## The core invariant (backbone of the design)

There is exactly **one canonical source key** per document:

```
K = "<folder>/<file>.pdf"          folder ∈ {financial_statements, pillar3}
                                   e.g. "financial_statements/DBS_1Q25_trading_update.pdf"
```

with constants `SOURCES_ROOT = <repo>/findociq/data/sources`,
`BUCKET = findociq-sources-igc2026-team08-6311`, and **four derived quantities
that must all be pure functions of `K`:**

| Quantity | Formula |
|---|---|
| GCS blob URI | `gcs_uri(K)   = f"gs://{BUCKET}/data/sources/{K}"` |
| Local path | `local_path(K) = SOURCES_ROOT / K` |
| `ingest_status.source_file` (PK) | `source_file(K) = K` (bare key — no `findociq/`, no `data/sources/`) |
| `doc_id` | `doc_id(K) = Path(K).stem.replace(" ", "_")` |

`gcs_uri` is **always derived from `K`, never stored as an independent free
string.** That derivation *is* the invariant.

### Why this needs fixing (mismatches found today)

The deep-reasoner analysis found the four quantities are **not** consistent today:

1. **`findociq/` prefix mismatch.** `run_doc.py:509` and `retry_worker.py:107`
   compute `source_file = os.path.relpath(pdf, REPO)` →
   `findociq/data/sources/financial_statements/DBS_1Q25_trading_update.pdf`
   (has `findociq/`), but the GCS blob is stored under prefix `data/sources/`
   (no `findociq/`). Same file, two different key strings.
2. **Scraper layout diverges.** `scrape_bank_ir.py:_dest_path (:219-224)` writes
   **nested** `<out_root>/<bank>/<year>/<quarter>/<file>` and uses a single
   `out_root` (default `financial_statements`) for *both* families — so pillar3
   files would land under `financial_statements/...` and never under `pillar3/`.
   The bucket, the manifest `folder` column, and `SOURCE_ROOTS`
   (`run_doc.py:755-759`) all expect **flat** `<folder>/<file>`. A locally-scraped
   nested file therefore gets a *different* `source_file` PK than the same file
   pulled flat from the bucket — the live break.
3. **`doc_id = stem` is not namespaced.** Uniqueness rests entirely on the
   `<BANK>_<PERIOD>_` filename convention (NAMING.md). Two PDFs sharing a stem
   across folders would collide in `document`/`table_t` and the flat blob
   namespace. We keep/enforce the naming convention as the guard (see §5).

### Multi-document rows (OCBC/UOB) — handled for free

`ingest_status` PK is `source_file` (`ON CONFLICT(source_file)`), **one row per
PDF**. OCBC's `condensed_interim` period maps to two distinct PDFs
(`OCBC_4Q25_Condensed_Financial_Statements.pdf` **and**
`OCBC_4Q25_Media_Release_and_Financial_Highlights.pdf`); UOB similarly
(`condensed-financial-statements` + `news-release`). Distinct stems → distinct
`K` → distinct rows → distinct `gcs_uri`. **Both are recorded**, no collision.
The grouped export comma-joins them under one `(bank, quarter, family, doc_type)`
line, exactly as the reference CSV shows.

## Architecture

### 1. New module — `pipeline/source_store.py` (single choke point)

The only place that talks to the sources bucket. Env-overridable constants
(`GCS_BUCKET`, reusing the existing pattern); no new config framework.

```
list_sources() -> list[str]            # list .pdf blobs under data/sources/, return keys K
materialize(K) -> Path                 # if local_path(K) absent, download gcs_uri(K) -> local_path(K); return it. idempotent, size-verified.
upload(local_path, K) -> str           # upload to gcs_uri(K); return the URI. scraper write path.
uri(K) -> str                          # gcs_uri(K) — the manifest value
exists(K) -> bool
key_for(local_path) -> str             # relpath(local_path, SOURCES_ROOT) -> K
```

Materialize writes to the **canonical local path** (already gitignored), so
`doc_id`, relpaths, and scan-tag matching stay byte-identical to today — the
~20 downstream `pdfplumber`/`pypdfium2` open sites need **zero changes**. The
only thing that changes is *how the file arrives*: GCS download vs pre-existing
local file.

### 2. Read-side wiring (extraction sources from GCS)

- `run_doc.py --pdf <K | gs://uri | localpath>`: resolve to `K`, `materialize(K)`,
  then existing flow. A real local path still works (back-compat).
- `run_doc.py --all`: `source_store.list_sources()` → `materialize` per doc →
  existing per-doc loop (replaces the disk `rglob` at `:782`).
- `ingest_quarter.py:92`: after scrape, materialize the period's docs from GCS
  instead of `glob`-ing disk.
- `retry_worker.py`: refactor its inline `pull_from_gcs` to call
  `list_sources()`/`materialize()` — dedupes the two GCS code paths into one.

### 3. Write-side wiring (scrape → GCS)

- `scrape_bank_ir.py`: **flatten `_dest_path`** to `<folder>/<file>` and route
  family → its root (`fs → financial_statements`, `pillar3 → pillar3`) as a
  *general rule*, not per-bank. Replace the local `shutil.copy2 (:344)` with
  `source_store.upload(tmp, K)`. The temp download is discarded — nothing
  persists locally.

### 4. Manifest / tracker points to GCS

- `gcs_uri` is exposed on the ingest tracker as a **derived** value
  (`source_store.uri(source_file)`), so it can never drift from the file's real
  location. The grouped export gains a `gcs_uris` column **aligned to `doc_ids`**
  (comma-joined in the same order), so every recorded document — including both
  OCBC/UOB FS docs — carries its GCS link.
- If a materialized DB column is later wanted for BQ/dashboard, it is a generated
  column computed as `gcs_uri(source_file)` — still derived, never free text.

### 5. Correctness guards

- **Enforce the `<BANK>_<PERIOD>_` naming convention** (per `data/sources/NAMING.md`)
  as the `doc_id` uniqueness guard, since flat layout + `doc_id = stem` depend on
  it. Scraper normalizes filenames to canonical form before upload.
- `materialize` verifies object size (and optionally md5) after download; a
  mismatch is a hard error, not a silent partial file.

## Data migration (one-time)

Existing `ingest_status.source_file` rows store the old
`findociq/data/sources/<...possibly nested...>` form. A one-time rekey strips the
`findociq/data/sources/` prefix and any `bank/year/quarter` segment → bare `K`,
so post-change lookups match (otherwise every doc reads as never-attempted).
Idempotent, run once, verified by row-count parity before/after.

## Code sites that must change (from invariant analysis)

1. `run_doc.py:509` — `relpath(pdf, REPO)` → `relpath(pdf, SOURCES_ROOT)` (== `K`).
2. `retry_worker.py:107` — identical change; base must match (1).
3. `scrape_bank_ir.py:219-224` `_dest_path` + `:342` — flatten + family routing.
4. `retry_worker.py:50,73-79` — blob prefix stays `data/sources/`; confirm
   materialize target is `SOURCES_ROOT/rel` (it is).
5. One-time `ingest_status` rekey migration (above).
6. `gcs_uri` surfaced only via `source_store.uri(K)` — never stored free.
7. Filename-convention guard for `doc_id` uniqueness.
8. New `pipeline/source_store.py`; `run_doc.py --all`, `--pdf`, and
   `ingest_quarter.py` routed through it.

## Testing

- **Unit (`source_store`):** `key_for`/`uri`/`local_path`/`doc_id` round-trip;
  `materialize` no-ops when local file present; downloads when absent; size-verify
  failure raises. Mock the GCS client.
- **Invariant test:** for a sample of real blob keys, assert
  `key_for(local_path(K)) == K` and `uri(K)` matches the live blob path.
- **Scraper layout test:** `_dest_path` returns flat `<folder>/<file>` and routes
  pillar3 to `pillar3/` for all three banks (no per-bank branching).
- **Migration test:** old nested/prefixed `source_file` values rekey to `K`;
  idempotent on second run; row count preserved.
- **End-to-end smoke:** `run_doc.py --pdf financial_statements/DBS_1Q25_trading_update.pdf`
  on an empty local `data/sources/` materializes from GCS and completes STEP 0→verify
  (uses the existing paddle-scan skip path so it stays cheap).

## Rollout

1. Land `source_store.py` + unit tests (no behavior change yet).
2. Rewire read side (`run_doc`, `ingest_quarter`, `retry_worker`) + invariant test.
3. Flatten scraper write side + upload.
4. Run the `ingest_status` rekey migration.
5. Add `gcs_uris` to the export/tracker view.
6. E2E smoke on one doc from an empty local `data/sources/`; then a period sweep.

Every step keeps GCS authoritative and runs under current `roles/editor` creds.
