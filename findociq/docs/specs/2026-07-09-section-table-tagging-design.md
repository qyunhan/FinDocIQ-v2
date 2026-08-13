# Section→Table tagging — routing branch design (2026-07-09)

> **RETIRED 2026-08-12 — HISTORICAL ONLY. Do not implement from this document.**
> 4 of the 5 modules below are archived under
> `archive/2026-08-12-handover-cleanup/`: `toc_match.py`, `gemini_arrange.py`,
> `section_manifest.py` (archived 2026-08-06), `score_sections.py`. Only
> `candidates.py` survives, and it is now a LEAF — `run_doc.py` STEP 0 invokes it
> to write `regions.csv`; nothing consumes an "arranger" stage.
> The live routing is: `classify/family.py` -> `pillar3` = `discover/pass1_toc.py`
> + `toc/pass1_to_v7.py`, anything else = `toc/toc_stage.py`. See PIPELINE.md STEP 1.

**Status:** approved in brainstorming; binding for the implementation plan.
**Pivot class:** decision-tree pivot (new branch keyed on printed-TOC presence). Per
CLAUDE.md this ships with (1) this spec, (2) manifest + route_map visibility, (3) an
explicit "pipeline pivot" call-out.

## Problem

Every detected table must carry the SECTION it belongs to, so downstream extraction can
route the correct Stage-2 prompt deterministically (section → template_type → prompt).
The naive "nearest paragraph_title above the table" rule is brittle: PP-DocLayout
sometimes labels a **date line** (`31 Dec 2024`, `For the Quarter ended 31 December
2024`) as `paragraph_title`, and it can be **typographically identical** to a real
section header (left-margin, bold, same font size — verified on OCBC p92), so neither
geometry nor font can reject it. Semantic judgement is required.

## Core idea — Paddle proposes, the arranger disposes

**Shared substrate (both branches):** Paddle emits ALL header-looking blocks per page in
reading order — it never decides which are real. Each candidate is enriched with cheap
pdfplumber typography (x0/alignment, font size, bold) read at the candidate's box.

The only thing that differs by branch is the **arranger** that turns candidates into a
section structure and attributes tables:

- **Printed-TOC present → deterministic matcher.** Keep only candidates that match a
  printed-TOC section title (normalized fuzzy match ≥ 0.9); everything else — including a
  typographically-identical date line — is auto-rejected because it is not in the section
  list. Zero LLM tokens. This is immune to the date-line failure by construction.
- **No printed TOC → Gemini arranger.** Send the WHOLE document's ordered candidate list
  (texts + page + position + typography) in ONE call. Gemini classifies each candidate
  {section | subheader | caption/date | noise}, builds the hierarchy, and assigns each
  table to a section. It emits **structure-only JSON — never table numbers/content** (same
  discipline as the chat-with-data layer). Cheap: header texts only, one call/doc.

Branch key: `pass1_toc` section-density over the first pages (≥ threshold → TOC branch).
Recorded per-doc in the manifest as `section_source ∈ {printed_toc, gemini}`.

## Coordinate hazard (MUST honor)

Paddle boxes are in rendered PIXELS at 200 DPI; pdfplumber is in POINTS with the page
bbox ORIGIN added (DBS PDFs: origin (−12.64,−12.64); OCBC: (0,0)). Everywhere Paddle px
meets pdfplumber pt: `pt = px*72/DPI + page.bbox[origin]`. (This silently cost DBS all
its titles in the first unit_scan run.)

## Stages & interfaces (pinned — modules build in parallel against these)

1. `discover/section/candidates.py`  (**runs in .venv-paddle**)
   `emit_candidates(pdf_path, tag) -> writes outputs/<tag>/candidates.csv` and
   `regions.csv`.
   - candidates.csv columns: `page, y0, x0, text, font_size, bold, alignment(left|center|
     right), is_dateish(bool)`. One row per PP-DocLayout `paragraph_title` block (plus any
     bold/large pdfplumber line Paddle missed), text read by pdfplumber at the box with
     the origin rule. `is_dateish` = regex date/period phrase (a hint, never a decider).
   - regions.csv columns: `page, table_idx, x0, y0, x1, y1` — one row per `table` block,
     `table_idx` = 0-based top→bottom on the page.

2. `discover/section/toc_match.py`  (base python; TOC branch)
   `attribute_from_toc(tag, toc_json_path) -> section tag per region`.
   Matches candidates to printed-TOC titles; each table region → nearest VALID (matched)
   section boundary above it on the page, else carry the previous page's section.

3. `discover/section/gemini_arrange.py`  (base python + API; no-TOC branch)
   `attribute_from_gemini(tag) -> section tag per region`, using
   `prompts/section_arrange.txt`. LLM returns structure-only JSON:
   `{sections:[{id,title,first_page,last_page}], table_assignments:[{page,table_idx,
   section_id}]}`. Validated against the emitted regions (every region must be assigned;
   unknown page/table_idx = hard error).

4. `discover/section/section_manifest.py`  (base python; both branches)
   Joins regions + section attribution → `section_manifest.csv` and updates the route
   manifest / route_map. Output columns (the downstream extraction contract):
   `doc_id, page, section_id, section_title, table_idx, bbox, template_type, prompt,
   source`. `template_type` via the existing template registry match on section title;
   `prompt` = routed Stage-2 prompt for that type (blank if unknown). `source` =
   printed_toc | gemini.

5. `discover/section/score_sections.py`  (base python)
   Scores section_manifest against the hand-labeled GT CSV on join key
   `(page, table_idx)`: section_id exact / mismatch / missing / extra. Reports per-branch.

## Ground-truth CSV (author by hand; one row per table-region per page)

`doc_id, page, table_idx, section_id, section_title, table_caption, notes`
- `page` 1-indexed; `table_idx` 0-based top→bottom on that page (disambiguates
  multiple tables/page; a table spanning N pages = N rows, same section).
- `table_caption` = a short human snippet (the table's title or its top-left cell) so we
  can confirm our detected region lines up with the GT row.
- `notes` optional (e.g. "date line above is NOT the section").

## Validation targets

- Pillar 3 OCBC 4Q25 (TOC branch): scored vs printed TOC AND the hand GT.
- OCBC financial statement 2025 (Gemini branch): scored vs hand GT only.
Pass bar: zero section MISMATCH on GT; date-line false-headers must not win (regression
of the p38/p92 class).

## AMENDMENT 2026-07-09 PM — two-step arranger (binding; supersedes Stage 3's shape)

Evidence from the first live runs: the one-shot Gemini call (TOC + 141 table
assignments in one output) (a) dropped a table assignment once, and (b) lumped note-2
tables to the parent "2" twice, while the DETERMINISTIC "deepest heading above by
position" rule provably yields the correct leaf (p13 table → 2.8; p30 tables → 2.21.3).
LLMs must not do positional assignment.

**New shape — BOTH branches become: validate headings → shared deterministic assign.**

1. `candidates.py` v2 — absorbs typographic_headings.py (font-outlier fallback +
   running-header filter) AND dedupes spaced/glued twin candidates (same page, |Δy0|≤3pt,
   space-stripped casefolded texts equal/prefix ratio ≥0.85 → keep the better-spaced one,
   OR the is_dateish flags). Schema unchanged.
2. Heading VALIDATORS (branch-specific) — output the shared `boundaries` contract:
   `[{section_id, section_title, level, page, y0, continued}]` in reading order.
   - TOC branch (`toc_match.py` v2): candidates matched against printed toc.json (as
     today) → each matched candidate instance is a boundary. level = dots+1 for
     numbered ids, else 1. continued = title carries "(continued)" or repeat instance.
   - Gemini branch (`sections_from_gemini.py`): prompt v2 sends INDEXED candidates ONLY
     (`idx | page | y | size | bold | align | text`) — NO tables block. Gemini returns
     STRICT JSON `{"sections":[{id,title,level,parent_id,candidate_idxs:[...]}]}`:
     which candidate lines are real headings and how they nest; captions/dates/noise are
     simply not referenced. Validation: idxs in range, no idx claimed twice, every
     section non-empty. Boundaries derive positions from candidates.csv rows by idx —
     Gemini never emits a position or a table assignment.
3. `assign_tables.py` — the ONE deterministic assigner used by both branches. Reading-
   order sweep (page asc, y asc; boundary before region at equal y). Cursor = section of
   the last boundary crossed → each region gets the cursor's section (this IS
   deepest-heading-above, since subsection headings follow their parents). Rules:
   - a `continued` boundary whose section is an ANCESTOR of the cursor's section does
     NOT downgrade the cursor (page-top "(continued)" banners of the parent note must
     not steal a table from the subsection that is still continuing);
   - any non-continued boundary always sets the cursor;
   - regions before any boundary → PREAMBLE; cursor carries across boundary-less pages.
4. `gemini_arrange.py` becomes thin (validator + shared assign); its one-shot prompt,
   completeness clause, and gap-filler are RETIRED (assignment can no longer drop or
   coarsen — it's code).
5. Scorer v2: before title-fallback matching, aggregate OUR sections into a GT section
   by id equality or dotted-prefix (ours "2.8", "2.21.3" roll up to GT "2") — our output
   is now MORE granular than note-level GT and must not be penalized for it.
6. Runner `tag_sections.py`: one entry point; picks the branch (toc.json provided/found
   → deterministic; else Gemini), shells the .venv-paddle emitter, then arranger +
   manifest. Prints which branch fired (route-visibility).

Acceptance for the amendment: leaf-granularity spot checks on real outputs — FS p13
table tagged 2.8 (not 2), p30 tables tagged 2.21.3 (not 2); P3 tables under 6.x/9.x
never tagged bare 6/9; GT scores not worse than the one-shot run on either doc.

## Constraints (inherited)

- Deterministic-first: LLM only in the no-TOC branch, structure-only output, one call.
- Every prompt change is a pipeline change: `prompts/section_arrange.txt` is a committed
  pipeline artifact, router-selected.
- No git commits (owner batches). Never touch final.db. No per-doc conditionals.
- Tests = plain `check()` scripts, no pytest.
