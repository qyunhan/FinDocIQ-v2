# concept — the concept-resolution layer

Stamps `row_dim.concept_key` from the curated `concept_dictionary.yaml` using a
**deterministic-first, LLM-assisted-residue** pipeline. Cells inherit the concept
through `v_cell` / `v_cell_flat` (`concept_key = COALESCE(row_dim.concept_key,
cell_fact.concept_key)`), so resolution is per **line item** (~1k labels), not per
cell (~8k). The LLM is one bounded, enum-constrained step — never a free stamper.

## Pipeline (run in order by `run.py`)

| step | module | what it does |
|------|--------|--------------|
| — | `load_dictionary.ensure_schema` | additive migration: `concept_map.table_type_norm`, `concept_resolution_log`, views recreated with `COALESCE(row, cell)` concept. Idempotent. |
| 2 | `load_dictionary` | parse YAML; expand every concept's **name + aliases** (line_item AND derived) into wildcard `concept_map` rows `(table_type='*', table_type_norm='*', label_norm=norm(alias), concept_key)`. |
| 3 | `resolve_deterministic` | for each `row_dim` row: `norm(label)` → exact `concept_map` lookup (a **type-scoped** row beats a wildcard). Stamp + audit log (`method='deterministic'`, `confidence=1.0`). Structural rows (date/period, `note*`, no-alpha) are skipped. |
| 4 | `resolve_llm` | **residue only**. De-dupe by normalised label, batch ≤20 per Gemini call with `table_type`+parent context, **enum-constrained** to the dictionary keys + `none`. Accepted (`confidence ≥ 0.8`) → stamp + log (`method='llm'`) + **append a wildcard alias** to `concept_map` (self-reinforcing). Below floor / `none` → left NULL, surfaced for review. |
| 5 | `validate` | reconciliation gate: additive subtotal identities + dictionary ratio formulas, uniqueness per `(doc,table)`, and a `sums_to` component-vs-total cross-check. A failing check means a **mapping** is suspect; nothing is auto-unstamped. |

## One-command entry

```bash
# deterministic-only report on a throw-away copy (no writes, no LLM):
python3 findociq/pipeline/concept/run.py --db findociq/db/compiled_fs.db --dry-run

# full live run (deterministic → LLM residue → validate → coverage):
PYTHONPATH=/Users/Qianyunhan/.claude/jobs/2b32aaed/tmp \
  python3 findociq/pipeline/concept/run.py --db findociq/db/compiled_fs.db

# deterministic + validate, skip the LLM:
python3 findociq/pipeline/concept/run.py --db findociq/db/compiled_fs.db --no-llm
```

`PYTHONPATH=.../sitecustomize.py` activates the IPv4-only `getaddrinfo` shim this
host needs for the google-genai SDK; only the LLM step needs it.

## Idempotency

Re-running re-stamps identically (no new log rows for unchanged mappings) and only
genuinely-new labels reach the LLM — accepted answers became `concept_map`
aliases, so they resolve deterministically next time. The map gets *more*
deterministic each run.

## Normalisation (`normalize.norm`)

Consistent with the loader's `geo_norm`/`_clean_label` base (footnote tails ¹²³ /
`(1)` / `(a)` stripped, lowercase, whitespace collapsed), plus three deterministic
folds: glued-digit footnote (`EXPENSES1`→`expenses`, `CET1` kept), `&`→`and`,
punctuation→spaces. Unit-tested in `test_concept.py`.

## Config

- `GEMINI_MODEL` (default `gemini-3.5-flash`), `GEMINI_PROVIDER` (default `gemini`).
- API key from `findociq/.env` (`GEMINI_API_KEY`), preferred over a stale env var.
- `BATCH_SIZE=20`, `CONFIDENCE_FLOOR=0.8` in `resolve_llm.py`.

## Tests

`python3 findociq/pipeline/concept/test_concept.py` — plain `check()` runner, exit
0/1, no network. Design: `findociq/docs/specs/2026-07-14-concept-resolution.md`.
