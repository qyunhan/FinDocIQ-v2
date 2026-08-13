# FS Gemini TOC Spike — design (2026-07-12)

**Status:** approved (produce-and-eyeball spike; not a production wiring). Exploratory — informs the FS discovery path that the [2026-07-12 document-family-router spec](2026-07-12-document-family-router-design.md) defers to "next cycle."

## Question

Can a **single raw Gemini pass over a whole FS PDF** — with **no** PaddleOCR /
`PP-DocLayout` candidate scaffolding — reproduce a usable section TOC (section
headers to the finest granularity + the PDF page each starts on)? If yes, the FS
discovery path collapses to something far simpler than the candidate-fed arranger
built for the 07-10 run (`pipeline/discover/section/`).

This is **not** the retired approach. On 2026-07-09 we retired Gemini doing *table
assignment* (positional) because it dropped/lumped tables. This spike extracts
**headings + page numbers only, no table assignment** — exactly what the
document-family-router spec says the FS pipeline's TOC step should do ("a single
narrow Gemini call, titles + page numbers only").

## Corpus

The readable FS documents under `findociq/data/sources/financial_statements/`.
Of 14 files, **4 are empty-byte stubs** (DBS 1Q, OCBC 1Q, OCBC Media Release ×2)
— the script probes readability and records them as `unreadable` without wasting
an upload. The remaining **10**:

- **6 substantial** (printed contents page present): DBS 2Q & 4Q
  `performance_summary`, OCBC 2Q `Unaudited_Interim` & 4Q `Condensed`, UOB 2Q &
  4Q `condensed`.
- **4 short** (no contents page): DBS 3Q `trading_update`, OCBC 3Q
  `Results_Press_Release`, UOB 1Q & 3Q `Performance Highlights`. Included on
  purpose — they reveal whether Gemini hallucinates structure when there is no
  real TOC, or correctly returns little/none.

The corpus is discovered by walking the FS tree (sorted), never hardcoded — a new
bank/quarter/file is picked up automatically (no-overfitting rule).

## Input

**Native PDF upload** via `google-genai` Files API
(`client.files.upload(file=path)`), then one `generate_content(model, contents=[
uploaded_file, prompt])` referencing the file. One doc = one upload = one call.
Chosen because the resumable upload is chunked at the HTTP layer and the generate
call is a small URI reference, so it is the mode most likely to slip past this
environment's known **>6KB single-request blackhole to the Gemini host** (the
07-10 FS run only completed via chunked transport). Gemini reads the *rendered*
pages, so it sees font-size / bold / layout cues that mark section titles — the
best shot at "finest granularity."

## Prompt

Single, spike-scoped, lives in the experiment dir (**not** production
`findociq/pipeline/prompts/`, since this is a spike, not a routed pipeline branch).
Headings-only, finest granularity, page numbers, JSON out; explicitly forbids
table assignment (the retired failure mode):

> Return every section / sub-section heading in reading order, to the finest
> granularity you can identify (numbered sub-notes like `13.1` / `13.1.2` and
> unnumbered sub-headings), each with the 1-based PDF page it starts on. JSON
> only: `{"sections":[{"title":..., "page":..., "level":...}]}`. Headings only —
> do not assign tables, do not summarize, do not invent sections that are not
> printed.

## Output

`findociq/experiments/2026-07-12_fs_gemini_toc_spike/outputs/`:
- `<doc_id>.json` = `{doc_id, source_pdf, model, status, sections:[{title, page,
  level}], contents_page_number, contents_page_text}`. `status ∈ {ok, unreadable,
  error}`. `contents_page_*` is a lightweight eyeball aid (the printed contents
  page, if one is detected) shown beside Gemini's output — NOT a scorer.
- `index.md` — all docs at a glance: per doc, Gemini's section list next to the
  detected printed contents page for side-by-side reading.

**No scorer, no ground-truth authoring** — judgment is produce-and-eyeball.

## Model

`gemini-3.5-flash` (pipeline default, `GEMINI_API_KEY` from env). The "Gemini 3.5
wrong" finding was about *table HTML structure*, a different task; heading reading
is within flash's range. `--model gemini-3.5-pro` retries a single doc if flash
underperforms.

## Error handling (repo convention: fail loudly, never silently drop)

- Unreadable PDF (open/probe raises) → `status="unreadable"`, no upload, still a
  row in `index.md`.
- Gemini call fails after retries (backoff reused from `app/spec.py` pattern) →
  `status="error"` with the exception summary; the run continues to the next doc
  (skip-and-continue). Every corpus file appears in `index.md`.

## Out of scope

Router wiring, scoring, GT authoring, Paddle-vs-raw comparison code (compared by
eye afterward), table assignment, production prompt placement.

## Constraints (inherited)

- NO git commits (owner batches).
- No per-bank behavioral branches — corpus discovered by walking, prompt is
  general, unreadable handling is generic.
- Plain script, run directly; outputs are the artifact.

## Deliverables

`findociq/experiments/2026-07-12_fs_gemini_toc_spike/`: `run_spike.py`,
`prompt.txt`, `outputs/` (generated). This design doc.
