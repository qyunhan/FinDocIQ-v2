# Chat-with-data — NL question → validated query spec → deterministic UOB-branded slide

**Date:** 2026-07-06
**Status:** design approved by user 2026-07-06 (brainstormed: form factor, NL layer, scope,
branding each explicitly chosen). Implementation plan is the next artifact.
**Decisions locked with user:** local Streamlit web app · Gemini flash as NL layer ·
all stamped concepts queryable · UOB-branded exports.

## Purpose

Let the user ask questions of findociq's star schema in natural language
("compare UOB vs DBS required stable funding through 2025") and get back a chart preview
plus downloadable PPTX/PDF slide — without any human-authored SQL, and without the LLM
ever touching a number.

## Core contract — the query spec

The ONLY thing the LLM produces is a small JSON spec, validated by code before anything
runs:

```json
{
  "concepts": ["asf_total"],
  "institutions": ["DBS", "UOB"],
  "period_start": "2023-09-30",
  "period_end": "2025-12-31",
  "column": "weighted",
  "chart": "line",
  "title": null
}
```

- Gemini flash (project key, `findociq/.env`; temperature 0) receives a fixed system
  prompt + the **live registry** — concept keys with their labels, institutions, periods,
  column keys, all queried from `final.db` at app start — plus the user's question, and
  must return the JSON spec only.
- Code-side validation against the same registry: unknown concept/institution → reject
  with closest matches (difflib); > 4 concepts → ask to narrow; period range clamped to
  available periods; `chart ∈ {line, bar, table}`; `column` must be a real col_key
  (default `weighted`).
- One retry loop max: if the response fails parse/validation, re-ask once with the
  validator error appended; then surface the raw response and ask the user to rephrase.
- The UI always displays "interpreted as: …" (the validated spec) so the user sees what
  the LLM decided before trusting the chart. Data path after the spec is 100%
  deterministic: same spec ⇒ same SQL ⇒ same slide.

## Components

1. **`findociq/tools/slide_kit.py`** (refactor) — extract from `nsfr_slide.py`: palette +
   ink tokens, `shorten_institution`, generalized `fetch_series(db, concepts, col_key)`,
   generalized time-series chart builder (percent vs thousands formatting decided by
   concept), bar chart, slide assembly (PPTX + preview PNG + vector PDF, shared
   `fit_chart_layout`), logo extraction/fallback. `nsfr_slide.py` becomes a thin CLI over
   slide_kit with byte-identical current behavior (regression-checked).
2. **`findociq/app/spec.py`** — query-spec dataclass, registry loader, validator, SQL
   builder (`v_cell ⋈ col_dim`), Gemini call + retry loop. Pure code, no Streamlit import,
   fully unit-testable with canned LLM responses.
3. **`findociq/app/chat_report.py`** — Streamlit UI: chat input → spec → "interpreted as"
   → inline chart preview → Download PPTX / Download PDF buttons (UOB template). Slide
   footer carries DB path, row count, and the spec itself for lineage.

## Error handling

- Valid spec, empty result set → explicit "no data for that slice" message; never a
  silently empty chart.
- Gemini unavailable/timeout → error banner, single retry only.
- `final.db` missing/unreadable → fail loudly at startup.
- Validation rejects are conversational (answerable by rephrasing the question).

## Testing

- `spec.py`: unit tests over canned Gemini outputs — valid, malformed JSON, hallucinated
  concept, out-of-range periods, >4 concepts. No live API calls in tests.
- `slide_kit.py`: smoke tests (chart/slide render without exception, output files
  non-empty) + rerun of the `nsfr_slide.py` CLI as a regression gate (same outputs as
  before the refactor).
- NL layer: a 10-phrasing manual eval list (question → expected spec) run once against
  live Gemini before v1 is called done.

## Run / dependencies

`pip install streamlit google-genai` into `.venv-reports`.
Launch: `.venv-reports/bin/streamlit run findociq/app/chat_report.py`.

## Out of scope (v1, deliberate)

Auth, multi-user, saved history, GCP deployment (schema/spec contract ports to Cloud Run
+ BigQuery unchanged if later needed), non-stamped concepts, non-NSFR doc types until
stamped. The one manual step this adds is zero: registry is read live from the DB; new
stamped concepts become queryable with no interface change.
