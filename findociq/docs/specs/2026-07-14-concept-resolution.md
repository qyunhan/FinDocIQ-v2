# Concept resolution layer — design (2026-07-14)

Sibling to the schema-v7 loader (`2026-07-13-gtable-schema-v7-loader-design.md`).
Package: `findociq/pipeline/concept/`. Binding vocabulary:
`findociq/pipeline/concept/concept_dictionary.yaml` (tracked copy of the user's
curated dictionary). Target DB: `findociq/db/compiled_fs.db`.

## Why (the shape of the problem)

Parsed cells carry no meaning across banks: DBS "Net fee and commission income",
OCBC "Fees and commissions (net)", UOB "Net fee and commission income" are the
same concept under three house names. Cross-bank analysis needs a canonical
`concept_key` on each line item. The layer stamps `row_dim.concept_key`;
cells inherit it through `v_cell` / `v_cell_flat`.

Design = **deterministic-first, LLM-assisted-residue**:

- **Deterministic first.** ~60-70% of concept-bearing rows match on a normalised
  label alone — free, fast, fully auditable. Here: 295 / 1990 rows matched
  deterministically before any API call.
- **LLM as a CLASSIFIER, not an author.** The residue is classified into the
  FIXED dictionary keys (enum-constrained structured output) or `none`. The model
  cannot emit a key outside the dictionary, so it cannot corrupt the vocabulary.
- **Self-reinforcing map.** An accepted classification becomes a new
  `concept_map` alias, so the layer gets *more* deterministic every run.
- **Reconciliation gate.** `sums_to` / dictionary formulas let us PROVE a mapping
  (components reconcile to totals; a ratio equals num/den) rather than trust it.
  A failing check means the MAPPING is suspect — reported loudly, never
  auto-unstamped.
- **Stamp the row, not the cell.** Concept is a property of the line item; ~1k
  labels to resolve, not ~8k cells.

## Schema (additive, `schema_v7.sql`)

- `concept_map.table_type_norm TEXT` — canonical table-type slug or `'*'`
  (wildcard). A type-scoped row wins over a wildcard at resolve time. Dictionary
  aliases seed `'*'` rows; the 19 NSFR template rows stay `table_type='nsfr'`,
  `table_type_norm='nsfr'`, untouched.
- `concept_resolution_log(doc_id, table_id, row_id, label, norm_label,
  concept_key, method, confidence, ts)` — every stamp is auditable.
- `v_cell` / `v_cell_flat` now expose
  `COALESCE(row_dim.concept_key, cell_fact.concept_key)` so a stamped row's
  concept is what cells inherit (the loader never writes `cell_fact.concept_key`).

For an already-built DB, `load_dictionary.ensure_schema` applies the same changes
idempotently (ALTER / CREATE IF NOT EXISTS / view recreate).

## Modules

1. `normalize.norm` — loader-consistent base (`_clean_label` footnote-tail +
   lowercase + whitespace) PLUS glued-digit footnote strip (`EXPENSES1`→
   `expenses`, `CET1` kept via a ≥5-letter guard), `&`→`and`, punctuation→spaces.
2. `load_dictionary` — YAML → wildcard `concept_map` rows for every concept's
   name + aliases (line_item AND derived); `map_table_type_norm`; migration.
3. `resolve_deterministic` — exact-norm lookup (scoped beats wildcard) → stamp +
   log (`deterministic`, conf 1.0). Structural skips: date/period (loader's
   `is_period_text`/`is_date_text`), `note*`, no-alpha. Idempotent.
4. `resolve_llm` — residue only, Gemini flash (env-swappable), temp 0,
   `thinking_budget=0`, enum-constrained. Batches by **(norm_label, table_type)
   CONTEXT**, confidence floor 0.8. **Decision-tree note (see below).**
5. `validate` — additive subtotal identities (sign-robust) + dictionary ratio
   formulas + uniqueness per (doc,table) + `sums_to` component-vs-total.
6. `run` — orchestrate 2→3→4→5, `--dry-run` (throw-away copy) / `--no-llm`,
   coverage matrix (concept × bank × period).

## Decision-tree pivot — LLM residue is CONTEXT-SCOPED, not label-global

Original spec batched residue by bare normalised label and, on acceptance,
stamped every same-worded row corpus-wide + appended a WILDCARD alias. The first
live run exposed the failure mode: the model saw a bare `'Total'` once in an
allowance table, classified it `pnl.provisions.total`, and that leaked onto ~50
unrelated `'Total'` rows (debts issued, fair-value, volume/rate) AND minted a
wildcard alias `'total' → pnl.provisions.total` that would corrupt every future
deterministic run. This is precisely the over-fitting the project forbids.

Fix (general, no per-label hack):
- Residue is de-duplicated by **(norm_label, table_type)** — a bare label is
  classified and stamped per context, never corpus-wide.
- A wildcard alias is appended **only when the label is unambiguous in the corpus**
  (present under a single table_type). A label spanning multiple table types is
  context-dependent and is deliberately NOT promoted (it stays a per-context LLM
  decision; cheap to re-ask). Reported as `aliases NOT promoted — ambiguous`.

Observable in the run report: the deterministic/LLM split, per-alias promotion vs
ambiguous-skip, and the coverage matrix; every stamp is in
`concept_resolution_log`.

## Reconciliation is a gate, not an auto-fix

Validation failures name a suspect mapping and are printed loudly; nothing is
unstamped automatically. Known standing flags on the current corpus (for human /
next-cycle review, NOT bugs in the layer):
- UOB `financial_highlights` non-interest income appears partial (additive +
  noninterest_share both flag it) — a real mis-stamp candidate.
- `ratio.nim` reconciliation is off by ~2× on DBS — half-year NII vs average
  interest-earning assets (annualisation), not a wrong stamp.
- `sums_to` flags where the dictionary aliases a SUBTOTAL and its GRAND TOTAL to
  one key (`Subtotal Assets`/`Total assets`; `NPL`/`NPA`; fee components/fee
  total) — dictionary-granularity items to tighten upstream.
- Uniqueness flags on segment/geo panels — the concept is correct; the repeated
  rows are disambiguated by an axis (segment/geo) not yet stamped per-row.

## Decision-tree pivot — dimensional breakdowns are a NO-WILDCARD scope (2026-08-04)

Fixes pre-flight finding **F2**. A geography/segment/industry decomposition
prints row labels that are character-for-character the spine's ("Total assets",
"Net interest income", "Profit before tax") while its cells mean a *slice* of
the entity, not the entity. A wildcard alias knows only the label text, never
which exhibit the row landed in, so it claimed those rows exactly as eagerly as
a real income statement's — 1,117 `cell_fact` cells quarantined as
`F2_geo_wildcard`, across UOB **and DBS**, plus an untagged equivalent in the
segment panels.

`_TYPE_NORM_RULES` + `scoped_aliases` cannot express the fix. That mechanism has
POSITIVE polarity — it supplies an alternate meaning for a bucket, and a bucket
with no scoped alias for a label still falls through to the wildcard. A
breakdown needs the opposite: a scope in which the wildcard is **never
consulted**.

**New branch.** `load_dictionary.dimensional_scopes(con)` assigns
`dim_geo` / `dim_segment` / `dim_industry` to a table; `build_lookup` gives those
buckets no `'*'` fallback; `resolve_deterministic` clears a stale stamp on such a
row (logging `method='deterministic_dim_scope'`) and never offers it to the LLM
— inference must not re-do by guess what the scope refuses to do by alias. A
human decision (`concept_key_human` / `identity_source='human_anchor'`) is left
untouched.

**Two independent signals, unioned — neither per-bank nor per-document:**
1. *structural*: the table's own COLUMNS carry >= 2 distinct non-total keys on
   one axis (geo != `GLOBAL`, segment != `SEG_TOTAL`, industry != `IND_TOTAL`).
   Columns, not rows: the label collision exists precisely when the ROWS are
   line items and the COLUMNS are the dimension. The >= 2 threshold keeps a
   single-geography statement (a subsidiary's own accounts), whose rows DO mean
   the spine concept, out of the scope.
2. *declared*: `table_registry.dim_hint` for the table's `table_type_id`.

Each covers the other's blind spot: signal 1 catches 6 OCBC "Business segments"
tables the registry leaves UNCLASSIFIED and 4 DBS breakdowns misfiled as
`FS_INCOME_SELECTED`; signal 2 catches a breakdown whose column headers name
regions we cannot yet map. Measured on the corpus: 45 tables scoped, zero false
positives. `_corpus_label_buckets` excludes scoped tables from the ambiguity
gate — counting them would disqualify the wildcard alias of every concept a
breakdown repeats, unstamping the genuine statement rows.

Observable in the run report: `dim-scope suppressed N (of which un-stamped M)`
in `concept/run.py`'s DETERMINISTIC block, and `deterministic_dim_scope` rows in
`concept_resolution_log`.

**Known tension, deliberately left open.** The bullet above ("Uniqueness flags
on segment/geo panels — the concept is correct; the repeated rows are
disambiguated by an axis not yet stamped per-row") records the opposite intent:
that breakdown rows SHOULD carry the concept and be separated by `geo_key` /
`segment_key`. `fact_metric` has those columns and `query_db.pull(dimension=…)`
serves them. Suppression at ROW grain necessarily removes both the harmful
GLOBAL-grain facts AND the useful dimensional ones, because `concept_key` is a
row attribute while the axis lives in the columns. Re-enabling dimensional
facts is a one-row `scoped_aliases: {dim_geo: […]}` addition — but it also
re-admits the breakdown's Total column at the canonical grain, i.e. F2. The
grain-correct resolution is a rule one layer down, in `build_fact_metric`: a
breakdown table may supply dimension MEMBERS but never the canonical
`(GLOBAL, SEG_TOTAL)` slot. Tracked with B6/D2.
