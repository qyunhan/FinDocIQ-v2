# slide_ingest — decks in, Excel + audit + `compiled_slides.db` out

```bash
python3 findociq/tools/slide_ingest/run_slides.py <deck.pdf> --pages 1-30
python3 findociq/tools/slide_ingest/run_slides.py <deck.pdf> --pages 3 --dry-run
```

One orchestrated command. Renders each page, runs OBSERVE → EXTRACT, writes the
workbook, the audit trail and its own database.

## Not `run_doc.py`, on purpose

| | `run_doc.py` (statements) | `run_slides.py` (decks) |
| --- | --- | --- |
| Unit planning | TOC → sections → units | **pages** — a slide IS the unit |
| Passes | one, over PDF bytes | **two** — observe, then extract |
| Input form | PDF | page rendered at 2.5× |
| Model output | `GTable` JSON | HTML, one `<table>` per element |
| Database | `compiled_fs.db` (`schema_v7`) | **`compiled_slides.db`** |

The split is not convenience. A deck has no contents page, so STEP 1 has nothing
to plan from — which is why the retired `auto_extract.py` also took `--pages`.
And chart values have to be *looked at*: the observe pass exists to force the
spatial discipline (trace the pointer, check the sum, read fill colour not
label) that a single pass skips.

**The database is separate because the evidence is different.** A figure read
off a donut is not the same kind of fact as one read off a filed statement.
Putting both in one `cell_fact` would make them indistinguishable to anything
querying it.

## What you get

```
outputs/slides/<tag>/
  <doc>_slides.xlsx          one sheet per element: p<N>_<idx>_<type>
  <doc>_slides.index.json    elements per page + token usage
  logs/cost_summary.json
  audit/<doc>/p<N>/
      page.png               EXACTLY what the model saw
      observe_prompt.txt     what it was asked to observe
      observe.txt            the observation it produced
      extract_prompt.txt     observation injected + the shared table rules
      response.html          raw output, before parsing
      parsed.json            normalised elements
```

`page.png` → `observe.txt` → `response.html` → `parsed.json` is the full chain
behind any number, readable without running anything.

## Database

`db/compiled_slides.db`, three tables, created on first write:

    slide_doc      doc_id, source_file, n_pages, ingested_at
    slide_element  doc_id, page, element_idx, element_type, element_title
    slide_cell     doc_id, page, element_idx, row_idx, row_label, col_label,
                   value_raw, data_kind, data_sign

`data_kind` / `data_sign` carry the waterfall semantics — `total` vs `bridge`,
and the sign the prompt derives from **fill colour** rather than from what the
label means financially. Values are stored `value_raw`, verbatim: `3,483` keeps
its comma, a printed dash stays `-`, brackets stay brackets. Nothing is parsed
to a number here — that is a decision for whoever consumes it.

Loading is **doc-scoped**, like the statement loader: a re-run deletes that
`doc_id` and re-inserts, so a deck cannot be doubled. Verified in testing —
loading the same deck twice leaves 2 elements / 6 cells, not 4 / 12.

## Flags

| Flag | Effect |
| --- | --- |
| `--pages` | `3`, `1-30`, `2,5,9-11`; duplicates and overlaps collapse |
| `--tag` | output dir under `outputs/slides/` (default: the PDF stem) |
| `--db` | slides DB path (default `db/compiled_slides.db`) |
| `--no-db` | Excel + audit only |
| `--dry-run` | render pages and assemble prompts, **no API call** |

`FINDOCIQ_SLIDES_MODEL` overrides the model (default `gemini-2.5-pro`).

## Prompts

`prompts/` holds the three this tool uses, restored from
`archive/2026-08-12-handover-cleanup/dead-prompts/`:

* `slide_observe.txt` — 352 lines. Element inventory, then per-type discipline
  for text tables, waterfalls, stacked bars, donuts, KPI grids, and a procedure
  for chart types it has never seen. Opens with the known failure modes:
  proximity is not membership, never assume ordering, verify with a sum.
* `slide_extract_html.txt` — emit one `<table data-element data-title>` per
  element; waterfall sign comes from fill colour.
* `stage2_core.txt` — the shared table rules, appended to the extract prompt.

They live here rather than in `pipeline/prompts/` because this tool is not on
the `run_doc.py` path. If deck ingestion is ever promoted into the pipeline,
they should move there with it, per CLAUDE.md.

## Status

The two pure halves are tested and green: `test_html_tables.py` (12 assertions)
covers the parser, and the Excel writer and DB sink were exercised end to end
with synthetic elements. `--dry-run` was verified against a real PDF —
pages render, prompts assemble, no API call.

**Not yet run against a real deck with live API calls.** The observe/extract
chain is restored verbatim from the retired `auto_extract.py`, but its output
quality on a current deck is unmeasured.
