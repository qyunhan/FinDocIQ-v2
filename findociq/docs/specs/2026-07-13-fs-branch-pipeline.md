# FS branch pipeline — end-to-end summary (2026-07-13)

**Status:** as built and verified on DBS_2Q25_performance_summary (37 tables,
2,374 cells, verify_cells 2,064/2,064 on-page, 70/92 totals arithmetic-proven,
$0.29). This document is the routing-spec record of the 2026-07-13 pipeline
pivot: the FS discovery path collapses to one narrow Gemini heading call plus
deterministic gates, then converges on the pillar-3 extraction machinery.
Code: `experiments/2026-07-12_fs_gemini_toc_spike/contract_v2/` (TOC stage,
to be promoted to `pipeline/`) + `pipeline/pass2/` (extraction + loader).
Companion specs: `2026-07-12-fs-gemini-toc-spike-design.md` (why one raw
call), `2026-07-13-gtable-schema-v7-loader-design.md` (loader mapping).

## The flow

```
PDF ──► 1. TOC call (Gemini, headings only) ──► 2. coordinate anchoring
        3. Paddle region scan ──► 4. section map + has_tables ──► DB sections
        5. pass2 extraction (routed prompts → GRow JSON) ──► 6. load_v7 → schema_v7
        7. verification (page parity + arithmetic + Excel views)
```

1. **TOC first pass — Gemini, deliberately narrow** (`prompt_v3.txt`, one call
   per doc, temperature 0, Files-API upload). Returns headings ONLY: verbatim
   title, page, level, parent ref. No counts, no classification, no end pages —
   config is not the LLM's job. Recall gate: unclaimed heading candidates from
   the deterministic detectors flag Gemini drops at $0.
2. **Coordinate anchoring — deterministic** (`toc_stage.py`). Each title is
   located on its page: candidates.csv match → direct text-line search →
   page-top fallback (98% coordinate-anchored on the pilot). Section OWN-BODY
   window = its anchor → the next anchor. Stable ids derived here: normalized
   OWN title (number prefixes → `section_no`; full ancestor chain →
   `section_path`; hierarchy stays relational).
   **PIVOT (2026-07-16) — `seq` is parent-before-child topological order, not
   raw anchor order.** Anchor `(page, y)` order can place a section BEFORE its
   parent when the parent heading is mis-anchored later (a page-1 title matched
   on a repeated page-2 occurrence; a higher-`y` child on the parent's page).
   That inversion is corrupt by construction — a child span is contained in its
   parent's span, so the child heading must physically follow the parent. After
   region attribution, `build_windows` applies a stable, order-preserving
   topological reorder (`_parent_before_child`) so every parent precedes its
   children; a parent is lifted forward ONLY when a child would else precede it,
   so normal reading order is untouched (identity on inversion-free docs).
   `parent_id` is preserved verbatim — the fault repaired here is a mis-ANCHOR,
   not a mis-NESTING. Invariant `seq(parent) < seq(child)` is asserted at emit
   (fail-loud at the TOC stage, not as a downstream FK error). This is general:
   no bank/doc conditional; keyed only on the `parent_id` graph. It fixed the
   `FOREIGN KEY constraint failed` (toc_to_db self-FK) and `KeyError` on parent
   id (`pass2/extract.load_sections`) that DBS 4Q25 / OCBC 4Q25 4Q25-highlights
   first surfaced.
3. **Table detection — PaddleOCR is the AUTHORITY** (PP-DocLayout-L regions,
   `candidates.py` batch scan, one-time ~2s/page). Zero regions on a page means
   no tables, period. Gemini opinions never overrule geometry.
4. **Section map** — a region belongs to the section whose window contains its
   top edge → `has_tables` Y/N + `n_regions` per section → `toc_v3.json` →
   `toc_to_db.py` inserts `document` + ALL section rows (prose included: "has
   tables" DB truth = LEFT JOIN table_t). Only Y sections proceed.
5. **Extraction — the pillar-3 pass2 machinery, unchanged contract** (`PASS2_v2.py`
   reads the FS TOC natively; no adapter). The router picks 1 of the proven
   prompt routes from page spans alone: SINGLE (one page, one section),
   SPANNING (multi-page; chunked, chunk 2+ gets the CONTINUATION prompt with
   code-injected column signatures), MULTIPLE (sections sharing a page).
   Every prompt carries the boundary rule + table-splitting rules (new header
   set = new table; category label = section_header ROW; different date
   periods = different tables — periods never merged). Output contract =
   `Extraction{tables:[GTable{columns:[GColumn], rows:[GRow{values:[GCell]}]}]}`,
   pydantic-validated at the call boundary; table config (how many tables,
   columns, continuations) is decided BY extraction into that schema, exactly
   as pillar 3 proved.
   *Batch mode (`--batch`)* is a PURE BILLING LEVER, not a behavior change: the
   Gemini Batch API runs the identical requests asynchronously at 50% cost.
   Prompt, config (temperature 0, response_schema, thinking off), and everything
   downstream of the response text (parse → validate → audit artifacts → merge)
   are byte-identical — `pass2/batch.py` reuses the same helpers the sync path
   does (`_ingest_resp`/`_finalize_unit`/`_merge_tables_into`/`_finalize_spanning`),
   never a forked copy. Work is submitted in DEPENDENCY ROUNDS: round 1 carries
   every unit's first call plus every spanning chunk; later rounds exist only for
   the adaptive follow-ups the sync path also makes (image fallback on thin/parse
   errors, MAX_TOKENS half-splitting). In this `PASS2_v2` pass2 path the spanning
   chunks are INDEPENDENT (each gets `build_prompt`, not a continuation prompt),
   so all chunks ride round 1; the round machinery only serializes a genuine
   continuation dependency where one exists. `log_usage` halves the recorded cost
   under batch so ledgers/cost_summary stay truthful (`batch:true`,
   `batch_discount:0.5` stamped per call). Files API is intentionally not used:
   the sync path inlines page-PDF bytes, so batch inlines the same bytes (identical
   model input) rather than a `from_uri` reference.
6. **Load — `load_v7.py`, pure python → schema_v7.** Unit's section is
   authoritative (GTable.section_id = advisory echo; echo resolving to a
   non-ancestor section = boundary leak, table skipped). Lineage registries
   (`row_lineage`/`col_lineage`) are global verbatim-wording identity;
   `concept_map` stays the semantic layer. Period: col date > title date >
   doc_period (consistency gate advisory). Units: table default overridden by
   explicit col/row markers ('%' row beats all). Two row relations, never
   conflated: printed indentation (totals/notes terminal) and `sums_to`/`sums_sign`
   — arithmetic-VERIFIED total membership (±1 sign solve across non-% columns;
   no unique solution → NULL + warning, never a guess). Doc-scoped idempotent
   reload; every anomaly is a warning or a loud failure, never silent.
7. **Verification — independent of extraction:** `verify_cells.py` (zero-token:
   every value_num verbatim on its source page), the sums arithmetic (the one
   structural relation provable by math), and two Excel views from the GRow
   pydantic scheme: the pass2 workbook (what Gemini returned) and
   `db_to_xlsx_check.py` (what the DB actually holds — the loader-inclusive
   check).

## Authority table (who decides what)

| decision | authority | LLM's role |
|---|---|---|
| section headings/titles | Gemini transcribes | verbatim source |
| heading coordinates, windows, ids | deterministic code | none |
| table presence (Y/N) | Paddle regions | none (counts advisory) |
| prompt route | code, from page spans | none |
| table config (count/columns/continuation) | Gemini via GTable schema | decides, then gets verified |
| section attribution | router/unit | echo = advisory + leak guard |
| hierarchy | printed indentation, position-resolved | transcribes levels |
| total membership | arithmetic solver | none |
| values | Gemini transcribes | verified vs page text |
| periods, units | deterministic parsers | none |

## Known limitations (recorded, not hidden)

- Region under-segmentation (stacked tables in one Paddle box) and rare region
  misses: de-merge parked pending `words_from_chars` de-tokenization; the
  extraction-side GTable splitting compensates for merges.
- `table_type` controlled vocabulary (the cross-bank table axis) not yet
  router-supplied — title-slug fallback until then (doubles ids cosmetically).
- Changes-in-equity matrix totals sum ACROSS columns (a different relation than
  `sums_to`); 21 no-solution + 3 ambiguous totals remain NULL by design.
- Sub-page y-attribution promoted into the TOC stage but `n_regions` vs loaded
  logical-table counts still differ where period panels split (by design).
- Parent/child hierarchy is still Gemini's verbatim `parent_id` (indentation
  reading). The 2026-07-16 seq pivot corrects ORDER only, not NESTING; and the
  mis-anchored parent still carries a wrong `page_start` (e.g. DBS `financial_
  results…` records p2, truly p1 — ordering fixed, span accuracy not). A
  dedicated parent-child reconciliation 2-pass (validate Gemini nesting +
  re-anchor parents against the physical heading stack) is the planned home for
  both; deferred, not hidden.
- One-command driver + promotion of the TOC stage out of `experiments/` =
  next phase (after user verification of DBS 2Q25).
