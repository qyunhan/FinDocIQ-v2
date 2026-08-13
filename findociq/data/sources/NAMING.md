# Source PDF drop-in convention

Drop bank disclosure PDFs into the two folders here. The pipeline reads the
**bank and period from the filename**, and routes family (fs / pillar3) from the
PDF's *content* — so folder placement is organizational, filenames must be right.

## Folders
- `financial_statements/` — quarterly financial results / highlights / condensed statements (Gemini TOC route)
- `pillar3/` — Pillar 3 regulatory disclosures (deterministic, zero-API route)

## Filename convention
```
<BANK>_<period>_<subtype>.pdf
```
- **BANK** — `DBS` | `OCBC` | `UOB` (must appear as a substring; case-insensitive)
- **period** — `1Q25` / `2Q25` / `3Q25` / `4Q25`. Use the quarter form even for
  full-year (`4Q25` = 2025-12-31). **Do NOT use `FY2025`** — the classifier's
  period regex misreads it as `FY20`. `FY25` is tolerated but `4Q25` is preferred.
- **subtype** — free-form, but keep the stem UNIQUE. `doc_id = filename stem`, so
  two PDFs for the same bank+quarter (e.g. OCBC Q1/Q3) MUST differ here or they
  collide.

### Standard subtype tokens
`condensed_interim`, `unaudited_financial`, `performance_highlights`,
`performance_summary`, `results_highlights`, `press_release`, `media_release`,
`pillar3`

## Multiple PDFs per bank/quarter — load BOTH
Some quarters ship >1 FS PDF (OCBC's Q1/Q3 = a highlights doc + a press release).
Ingest **both** — the pipeline is doc-scoped and `fact_metric` canonicalizes by
`(institution, concept, period, …)`, so complementary numbers merge and
duplicates dedup. Just give them distinct subtypes so the stems don't collide.

## Examples
```
financial_statements/
  DBS_1Q25_performance_highlights.pdf
  DBS_4Q25_condensed_interim.pdf
  OCBC_1Q25_results_highlights.pdf      # OCBC Q1/Q3: two PDFs
  OCBC_1Q25_press_release.pdf           #
  OCBC_2Q25_condensed_interim.pdf
  UOB_1Q25_performance_highlights.pdf
pillar3/
  DBS_1Q25_pillar3.pdf
  OCBC_1Q25_pillar3.pdf
```

## Checklist
`manifest.csv` in this folder lists every target file for 2021–2026 × 4 quarters
× 3 banks (FS + Pillar 3). Fill the `have(y/n)` column as you collect. Rows marked
`not_yet_released` in `availability` are future quarters (not reported yet as of
2026-07-24) — skip until published.
