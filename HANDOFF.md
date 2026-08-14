# FinDocIQ — Handoff / Current State

> **HISTORICAL — do not follow this document.** Written 2026-07-16, when the
> project ran on GCP (Cloud Workstation, GCS source bucket, BigQuery, Cloud
> Run). **GCP was retired in August 2026.** The architecture description below
> is still broadly accurate, but every cloud instruction in it is dead. Read
> `README.md` for how the project actually works today.

Bridges the original build context into the repo. Last updated 2026-07-16 and
not maintained since.

## What this is

A pipeline that turns Singapore-bank disclosure PDFs (DBS/OCBC/UOB financial
statements + Pillar 3) into a **verified, queryable database**, then answers
cross-bank analytical questions with **numbers that trace to the source page**.
End goal: a dashboard + chat tool over the 3 banks' ROE drivers, no human in the loop.

## Architecture (three layers)

```
PDF ─► EXTRACT ────────────► VERIFIED DB (schema_v7) ─► QUERY
       PaddleOCR detects        star schema: cell_fact +      concept router →
       tables (has_tables);     dims (concept/segment/geo/    SQL → analyst.
       Gemini reads them into   period). Every value          Numbers from SQL,
       the GTable/GRow JSON;    verified vs its page          never the LLM.
       load_v7 → schema_v7      (verify_cells, 0-fail).
```

**Golden rule everywhere:** the LLM proposes/reads/narrates; deterministic code and
SQL supply and verify every number. Accuracy comes from gates (`verify_cells` page
parity, `sums_to` arithmetic), not from trusting the model.

## Current state (what works)

- **Extraction pipeline: DONE & proven.** 7 FS docs loaded, `verify_cells` 0-fail.
  One command: `python3 findociq/pipeline/run_doc.py --pdf <file> --batch`
  (`--all` sweeps the corpus; `--rebuild-db` reconstructs; `--verify-only` is $0).
- **Semantic layers stamped & verified:** concepts (dictionary, ~37 keys),
  segments (SEG_RETAIL/WHOLESALE/…), geography (geo_dim hierarchy), periods with
  spans (1Q..4Q/1H/2H/9M/FY/as_at) + intervals, `sums_to`/`sums_sign` (arithmetic-
  verified totals, decomposition vs waterfall). cell_fact is self-describing.
- **Query layer:** `concept/query_db.py` (parameterized pull), `discover_cuts.py`
  (dimension-cut catalog). The 2-step router→analyst pattern is demoed and works.
- **DB:** `findociq/db/compiled_fs.db` (SQLite, schema_v7). Regenerable from
  audit JSON via `run_doc.py --rebuild-db`. 7 docs / 172 tables / 11,907 cells.

## GCP (project `igc2026-team08-6311`, account yunhan@uobdmoedm.com)

Live and tested:
- **BigQuery** dataset `findociq` — 9 tables loaded (v_cell_flat, document, section,
  table_t, dim_segment, dim_geo, concept_map, concept_dim, concept_cuts).
- **Secret Manager** — `GEMINI_API_KEY` (the pipeline's AI Studio key).
- **Document AI** — Form Parser processor, bake-off tested (see decisions).
- **Vertex AI** — enabled, Gemini reachable via ADC (no key). Preferred for on-GCP code.

## Next steps (prioritized — handover is the deadline: user leaves Aug 2026)

1. **`fact_metric` clean table.** BigQuery holds RAW `v_cell_flat` — the same concept
   returns MULTIPLE values per bank (sign variants, rounding twins, %-cells, cross-table
   duplicates, a few mis-stamps). Charts CANNOT read raw v_cell_flat. Build `fact_metric`:
   one canonical row per (institution, concept, period, span, segment, geo) applying the
   dedup/sign/conflict logic already in `query_db.py`. It also emits the data-quality
   punch-list (conflicts to fix). **This is the gate before any dashboard.**
2. **Phase C — derived ratios** (ROE, NIM, CIR) = the "6 ROE drivers." Formulas already
   written in concept_dictionary.yaml; not computed yet. Needed for the Comparison tab.
3. **Streamlit app** (Browse / Comparison / Chat tabs) → Cloud Run + IAP. Reads BigQuery
   `fact_metric`. Chat = the 2-step router→analyst (use Vertex, key-free).
4. **Auto-ingestion:** Drive/GCS → Cloud Run job → BigQuery, per new quarter.
5. **Handover kit:** Terraform (whole stack), this doc, tests in Cloud Build CI.

## Key decisions & findings

- **Vertex AI works** (tested): `genai.Client(vertexai=True, project=..., location="us-central1")`
  — no API key, IAM auth. Use for on-GCP code; keep the Secret Manager key for local dev.
  (Verify `gemini-3.5-flash` availability/id on Vertex before switching the pipeline.)
- **Document AI bake-off:** reads cells VERY well — OCBC borderless NSFR 189/189 (100%),
  UOB 12.9 variable-column tables correctly segmented (10 sub-tables). BUT it returns flat
  cells: no row roles / hierarchy / sign / stitching / verification. Verdict: viable as an
  OCR front-end feeding the GTable contract; NOT a drop-in for the Gemini structuring.
  Positional (row×col) accuracy still untested. Keep PaddleOCR+Gemini as the proven path.
- **Cost (gemini-3.5-flash, per doc):** TOC ~5c, full extraction ~$0.5–1 (batch = 50%).
- **Compact-output was tried and REVERTED** (accuracy > 35% cost cut; user chose Batch API).

## Deploy boundary
`.gcloudignore` ships ONLY the live pipeline (~7MB); `_legacy/`, `experiments/`, `app/`,
`tools/`, `reports/` stay in git but never upload.

## Working on this (new developer — no Mac needed)

You can do ~all development entirely in the browser on GCP. Open **Cloud Shell**
(or Cloud Shell **Editor**, which is VS Code in the browser) and:

```bash
npm install -g @anthropic-ai/claude-code   # optional: Claude Code, same as local
git clone https://github.com/qyunhan/FinDocIQ
cd FinDocIQ
```

In Cloud Shell, `gcloud` is already authenticated as you, so **Vertex AI, BigQuery, and
Secret Manager work with NO API key** — code uses your identity (ADC). This is the
key-free path the pipeline's Vertex mode targets.

**Split of where work runs — this is the important part:**

| Task | Runs where | Fast in Cloud Shell? |
|---|---|---|
| Edit code, git, deploy | Cloud Shell / Editor | ✅ yes |
| Query BigQuery, build `fact_metric` / ratios | Cloud Shell | ✅ yes |
| Run the dashboard / chat (query layer) | Cloud Shell | ✅ yes |
| **Extract a NEW pdf (PaddleOCR table detection)** | **Cloud Run job (real compute)** | offloaded — don't run on the shell |

PaddleOCR (`PP-DocLayout-L`) is a CV model and is CPU-heavy; the free Cloud Shell VM is
small, so raw-PDF extraction is slow there. **By design it is not a developer step** — it
runs as the auto-ingestion **Cloud Run job** (Next steps #4) when a new quarterly PDF
lands. A developer never waits on it. If you ever need interactive heavy compute, use a
**Cloud Workstation** or a Compute Engine VM instead of Cloud Shell.

Bottom line: develop, query, and deploy in Cloud Shell; let extraction run as a job.

### Persistent dev VM (so Claude Code + progress survive between sessions)

Cloud Shell is ephemeral — its VM resets and only `$HOME` persists (and only for
120 days). For durable, always-there Claude Code, use a Compute Engine VM driven by
two helper scripts in `tools/`:

```bash
# ONE-TIME in Cloud Shell: get the launcher into your persistent $HOME
curl -O https://raw.githubusercontent.com/qyunhan/FinDocIQ/main/tools/vm_up.sh

# EVERY TIME you open GCP: bring up the VM and drop into it
bash vm_up.sh            # creates+bootstraps the first run, else just starts + SSHes in
#   on the VM, first run only:  source ~/.bashrc && claude login   (then: cd ~/FinDocIQ && claude)

# WHEN DONE (stops the hourly charge; disk + work persist):
bash ~/FinDocIQ/tools/vm_down.sh
```

`vm_up.sh` is idempotent: it creates the VM (`claude-dev`, `e2-small`, Singapore,
persistent 30GB disk, `--scopes=cloud-platform` so Vertex/BQ work key-free via ADC),
installs Claude Code into `$HOME` with PATH set, clones this repo, and connects you —
or just starts + connects on later runs. Override defaults via env (`GCP_ZONE`,
`VM_MACHINE`, …). **The durable state is git**: `git push` before `vm_down.sh`; the
repo on GitHub is the source of truth, the VM is disposable compute.

Note: this runs the *pipeline's* Gemini on Vertex key-free (ADC). Claude Code's own
auth is separate — `claude login` (subscription), or set `CLAUDE_CODE_USE_VERTEX=1` +
a Claude-enabled Vertex region if you want the assistant itself on Vertex too.

## Where to read more
- `findociq/PIPELINE.md` — the step map + folder guide.
- `findociq/docs/specs/` — 15 design records (the "why"). Start with
  `2026-07-13-fs-branch-pipeline.md` and `2026-07-13-gtable-schema-v7-loader-design.md`.
- `findociq/PROGRESS.md` — session-by-session build log.

## Known debt
- View DDL is duplicated in `schema_v7.sql` + `concept/load_dictionary.py` (must hand-sync).
- `compiled_fs.db` (14MB) committed on every rebuild → 242MB `.git`. Consider gitignoring
  the DB (it's regenerable) to slim history.
- Concept coverage: ~37 keys; a long tail of line items unstamped (reachable via
  `query_db --fallback`). Grow the dictionary as needed.
