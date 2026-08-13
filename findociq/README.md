# FinDocIQ

Turn financial-disclosure tables (Pillar 3, financial statements, and in future any
table-bearing financial doc) into one queryable star-schema database, then ask it
anything in natural language — for cross-bank, cross-quarter competitor analysis.

## The three phases

1. **Extraction methodology** (`pipeline/`) — the Plan-9 pipeline. Only Stage 2 calls AI:
   Gemini reads a PDF page and returns an **HTML table** (merges + shading + row levels +
   cell states). Everything else is deterministic. Prompts live in
   `pipeline/stage1_extract/gemini/prompts/`.
2. **Database** (`schema/`, `db/`) — the `schema_v5` star schema. One fact table holds every
   cell; dimensions describe rows/cols/tables/docs. **Table templates are stored as rows
   in the DB** (`row_template`/`col_template`), not as files — a matched template injects a
   faster "expected rows" prompt so known tables skip structure-deduction.
3. **NL harness** (`harness/`) — *future*: one-step natural-language query over the DB.

## Layout

```
docs/        plan/ (authoritative FinDocIQ_Plan_9) · specs/ (designs) · findings/ (dated notes)
pipeline/    the 5-stage methodology + prompts/ + html_to_cells.py (HTML → schema_v5 cells)
schema/      schema_v5.sql (the single fixed schema; includes the template tables)
db/          findociq.db (facts + stored templates)
data/        sources/<doctype>/ (input PDFs) · outputs/<doc>/<table>/stage2.html (raw, auditable)
experiments/ dated spikes & dead-ends, self-contained (e.g. 2026-06-29_mineru_eval)
harness/     future NL query layer
_legacy/     old/scattered folders parked here, not deleted
```

## Conventions
- **Outputs you can see:** `data/outputs/<bank>_<period>_<doctype>/<table>/stage2.html`. The DB
  is the store; the HTML is the auditable raw extraction. No intermediate `cells.json`.
- **Prompts:** one lean `stage2_core.txt` (merges + shading + cell states — the only things
  needing eyes) + short framings; known-table modifier generated from DB templates at runtime.
- **Tracking:** `PROGRESS.md` = running session log (Done / In-progress / Bugs / Next).
  `docs/findings/` = the deep technical notes behind the log.

## Status
See **`PROGRESS.md`**. Current focus: validate the Stage-2 HTML methodology and a
deterministic MinerU+pdfplumber alternative (`experiments/2026-06-29_mineru_eval`).
