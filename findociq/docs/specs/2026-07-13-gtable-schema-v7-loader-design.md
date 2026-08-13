# GTable → schema_v7 loader — design (2026-07-13)

> **Concept resolution** (stamping `row_dim.concept_key` from the curated
> dictionary, deterministic-first + LLM-classifier residue) is a sibling layer,
> specified separately in `2026-07-14-concept-resolution.md`.

**Status:** approved draft (Option A, user-approved 2026-07-13: pass2 keeps its
GTable JSON contract; a new loader maps it into schema_v7 — NOT switching FS
extraction to the HTML/html_to_cells path). Drafted by deep-reasoner agent,
open questions resolved with the recommended defaults (§4).

**Scope:** `doc_family='financial_stmt'`. Input per doc = per-unit GTable JSON
(pass2 audit artifact `parsed.json`, shape `Extraction`) + `doc_id`, resolved
`section_id`, page provenance. Pure python + sqlite3. `document` and `section`
rows are OWNED UPSTREAM (TOC stage insert, `toc_stage.py` → `toc_to_db.py`);
this loader asserts they exist and never authors them. Lineage registries
(`row_lineage`/`col_lineage`) are GLOBAL and get-or-create — never doc-deleted.

## 1. Field-by-field mapping

### GTable → table_t
| target | source / rule |
|---|---|
| doc_id | arg |
| table_id | `f"{section_id}_{table_type}_{period_iso}"`. Period is IN the id: same template + different date ⇒ separate table_t row (periods never merged). |
| table_title | `GTable.title` verbatim |
| table_type | router/unit-supplied; fallback `slug(title)`. No per-bank branch. |
| section_id / section_no | the UNIT's section, always (router knowledge is authoritative); `GTable.section_id` is the model's echo — advisory cross-check only, mismatch → warning. MUST exist in `section` → else FAIL LOUD. section_no denormalised from the section row. **Leak guard:** an echo that RESOLVES to a different existing section (by id or section_no) that is NOT an ancestor of the unit's section = the model asserting the table belongs elsewhere (boundary leak on a shared page) → the table is SKIPPED with a warning; that section's own unit owns it, and the verify/coverage gates catch a double miss. Echoes resolving to an ancestor are consistent (printed parent note number) and load normally. |
| period | resolved table-level period (§2); NULL **only if** every leaf col carries col_period |
| geo_key | NULL unless whole table is one geography (rare in FS) |
| page_range | unit page span `'p-q'` from merged `continued_from_previous` fragments |

### GColumn → col_dim (+ col_lineage registry)
- Leaf columns: `col_id = 1..N` left-to-right (what cell_fact anchors), `col_hierarchy=1`.
- `group` (span banner): one hierarchy-0 row per distinct group, out-of-band
  `col_id 100,101,…`; leaf's `col_parent` = that id. cell_fact NEVER references
  col_id ≥ 100.
- **Period-axis exclusion (extended 2026-07-14 to GROUP banners + period
  EXPRESSIONS):** a leaf OR a group banner whose text IS a date/period (predicate
  `is_period_text`, the general grammar below — not only `DD-Month-YYYY`) is the
  period axis: the ISO end date goes to `col_dim.col_period` and the axis text is
  DROPPED from lineage. When a **group banner** is a period (`'1st Half 2025'` /
  `'1st Half 2024'` / `'2nd Half 2024'` over `Average balance ($m)` / `Interest
  ($m)` / `Average rate (%)`), the banner is excluded and every leaf under it
  inherits that col_period, so the metric leaves converge to ONE col_lineage_id
  per metric across the three period groups — distinguished only by col_period. A
  leaf's OWN explicit date/period takes precedence over its group banner. If
  exclusion empties the lineage, fall back to the (non-period) group banner, else
  the canonical token `'value'`. GUARD: the axis text must BE the header once
  boilerplate leading words are removed — a COMPARISON/change banner
  `'1st Half 2025 vs 1st Half 2024'` is NOT a single period (residual `'vs …'`),
  so it stays in the lineage and its leaves get no col_period.
- col_lineage: group→lvl1, leaf→lvl2 (or lvl1 only); get-or-create in
  `col_lineage`; stamp `col_dim.col_lineage_id`.

### GRow → row_dim (+ row_lineage registry)
- `row_id` (INTEGER) = 1-based enumeration; printed `GRow.row_id` string →
  `line_no` (display only).
- `row_hierarchy` = `GRow.level`. `row_parent` resolved by POSITION (nearest
  preceding row one level up — html_to_cells pattern), cross-checked against
  `GRow.parent`. **Terminal-total/note parent rule:** `total` and `note` rows are
  terminal and NEVER parent a data row — they are SKIPPED as parent candidates
  while scanning up. A total in between (a subtotal) is passed over to the next
  eligible ancestor; a data row whose only one-level-up candidates are totals/notes
  gets `row_parent` NULL (top-level). A note may still parent another note (the
  'Notes:' block's own display nesting is preserved). This fixes 'Due within/after
  1 year' mis-parenting to the preceding Total in DEBTS ISSUED.
- **`sums_to` / `sums_sign` (SIGN-AWARE verified relation — 2026-07-13):** for each
  `total` row T, walk UP the contiguous run above it (stop at section_header /
  sub_header / table start; notes skipped, not a stop). The PREVIOUS total, if the
  run reaches one, is captured as a **carry-in member** (a total can be prior-subtotal
  ± new lines) and bounds the run. Data members are the SHALLOWEST level in the run
  (deeper 'of which' rows excluded to avoid double-count). Resolution is two-phase:
  1. **Fast additive path** (byte-identical to the pre-2026-07-13 rule, so every
     already-passing block is untouched): all `+1` over the DATA members ONLY. If
     `sum(members) == T` within tol `1.0*len(data_members)` for every non-NULL column
     → assign `sums_to=T.row_id`, `sums_sign=+1`. Carry-in is never materialised here.
  2. **Sign search** (only when the fast path fails): candidate set = data members +
     carry-in. Find `s_i ∈ {+1,-1}` with `Σ s_i·v == T` within tol `1.0*len(members)`
     for EVERY non-NULL column simultaneously (member NULL → 0; column where T is NULL
     skipped). Zero-across-all-active-columns members are fixed `+1` and excluded from
     enumeration (never widen the solution count). >14 non-zero members → NULL +
     `'block too large'`. Exactly ONE solution → assign `sums_to`/`sums_sign`; none →
     `'no solution'`; more than one → `'ambiguous, k solutions'`. All non-solving
     cases leave the block NULL + a load-summary warning (never a load failure).
  Totals and notes themselves have `sums_to`/`sums_sign` NULL.
  - **%-COLUMN GATING (2026-07-13 follow-up, implemented):** columns whose parsed
    unit is `'%'` (via `parse_unit`, Feature A) are EXCLUDED from the arithmetic
    check in both phases above — a percent-change column is non-additive by nature
    (the % change of a total is not the sum of the % changes of its parts, schema
    NOTE D#4/#5), so a printed `'+/(-) %'` column must never be allowed to fail an
    otherwise-reconciling S$m block (or spuriously "verify" one by luck). Guard: at
    least one non-`'%'` value column must remain checkable for the block to resolve
    at all; a block where every leaf column is `'%'` stays NULL with a dedicated
    warning `'only %-columns, not arithmetically verifiable'` rather than silently
    skipping the check. Measured effect on the DBS 2Q25 corpus: 53 → 70 verified
    totals (+17), recovering exactly the income-statement/comprehensive-income
    subtraction chains identified as the KNOWN GAP in the prior draft of this spec.
- **`unit` (materialised per-cell, FULL precedence chain — Feature A, extended
  2026-07-14):** deterministic token grammar, no per-bank rule. The EFFECTIVE unit is
  now **materialised onto `cell_fact.unit` at load** (an additive `TEXT` column); the
  `v_cell`/`v_cell_flat` views READ that column directly (the old view-time `CASE` is
  gone) so every consumer — view, xlsx check, NL layer — sees one identical value, and
  a cell can be self-labelled by its own printed token (which no view CASE over the
  dim units could ever express). The SOURCE units stay populated for inspection:
  `table_t.unit` = `parse_unit(label_header)` then title (table DEFAULT);
  `col_dim.unit` = `parse_unit(group+leaf)`, explicit marker only, pure-date leaves
  excluded; `row_dim.unit` = `parse_row_label_unit(row label)`, explicit only.
  - **COUPON-IN-NAME guard (`parse_row_label_unit`, 2026-07-15):** a `'%'` printed
    INSIDE a row NAME as a coupon/interest rate immediately followed by descriptive
    text (regex `\d+(\.\d+)?\s*%(?=\s*[A-Za-z])`: `'3.58% non-cumulative
    non-convertible perpetual capital securities'`, `'3.0% perpetual capital
    securities'`) is a rate-in-a-name, NOT the row's unit — those cells are S$m
    capital/dividend amounts, so the `'%'` is DROPPED and the unit resolves via the
    normal col/table chain (UOB `share_capital` perpetual rows 749/150/599/400/850 →
    S$m; OCBC `dividends_distributions` coupon rows likewise). A STANDALONE/TERMINAL
    `'%'` marker is still a real row unit — a parenthesised `'(%)'` (`'Net interest
    margin (%)'`, `"Capital Adequacy Ratio ('CAR') (%)"`) or a ratio name ending in
    `%`. The guard fires ONLY when the sole `'%'` is a coupon-in-name (every coupon
    occurrence stripped, then `parse_unit` re-run so a legit marker elsewhere still
    wins); non-`'%'` tokens are unaffected. The value-token path
    (`unit_from_value('137%')→'%'`) and the col/table-unit paths are UNCHANGED, so
    the `Loss Allowance Coverage` `'137%'`/`'236%'` cells stay `'%'`. Blast radius:
    ~10 cells corpus-wide (all the coupon-in-name pattern) move `'%'` → `S$m`.
  - **`parse_unit()` (mid-chain, dim units):** `%`/`(%)`/`% change` → `'%'`;
    `($m)`/`S$m`/`In $ millions` → `'S$m'`; `('000)`/`thousands` → `"'000"`;
    `per share`/`cents` → `'per_share'`; `number of`/`no. of` → `'count'`;
    `times`/`(x)` → `'x'`; first match wins, else NULL.
  - **NEW top of chain — CELL VALUE TOKEN `unit_from_value(value_raw)`:** a cell whose
    verbatim value is self-labelled carries its OWN unit and must not inherit the
    block's: `value_raw` ending `'%'` → `'%'`; `value_raw` matching `r'\d\s*(x|times)$'`
    (case-insens) → `'x'`; else None. **Rationale:** the `Loss Allowance Coverage`
    ratio rows print `'137%'` / `'236%'` as the cell text (the '%' survives into
    `value_raw`), inside a table whose currency lines are S$m — the token beats the
    column/row/table unit so those 6 cells resolve to `'%'` while the allowance rows
    stay `S$m`. No view CASE could do this; it is per-CELL.
  - **NEW bottom of chain — DOCUMENT DEFAULT:** after every table of a load is mapped,
    the MODAL non-NULL `table_t.unit` across the doc's tables is the last-resort unit
    for any cell still unresolved by the chain above (a strict tie → NO default). It is
    stored NOWHERE (derivable; the warning IS its provenance). **Rationale:** the
    consolidated `Statement of Changes in Equity` prints its `In $ millions` banner
    only in prose / a merged caption Gemini does not transcribe into `label_header`,
    so `table_t.unit` is NULL and its 140 cells would otherwise be unit-less — the
    modal `'S$m'` (31/33 non-NULL table units in DBS 2Q25) is unambiguously correct.
    Every table that consumed the doc default for ≥1 cell gets ONE warning
    `f"{table_id}: unit from document default {unit!r} (modal across N/M tables)"`.
  - **Full per-cell resolution order (implemented in python at load, NOT a view
    CASE):** (1) cell value token; (2) row unit **if `'%'`** (a ratio row beats a
    currency column); (3) col unit (derived `% chg` columns cut across currency rows);
    (4) row unit (any other explicit row marker); (5) table unit; (6) document default;
    else NULL — genuinely unknowable — with ONE per-table warning
    `f"{table_id}: N cells with unresolvable unit"`. Loader still warns when a row and
    column unit both fire, differ, and neither is `'%'`.
  - **Measured on DBS 2Q25 (22 units → 38 tables, 2374 cells):** every cell resolves,
    NULL = 0. By source: cell-token 6, row-`%` 6, col 400, row 4, table 1807, doc
    default 149. `cell_fact.unit` breakdown: S$m 2034, % 316, count 24.
- `row_type`: `section_header`/`sub_header` → row_dim rows (parent anchors,
  lvl1 in v_cell_flat), ZERO cell_fact. `data`/`total` → value-bearing leaves.
  `note` → row, no cells (loaded — default Q4).
- row_lineage: root→row chain, depth 1..5, **overflow = HARD FAIL**.
- `concept_key`/`col_key` left NULL — stamped separately by stamp.py.

### GCell → cell_fact
- One GCell per leaf column (positional). `colspan=1` always.
- `value_raw` verbatim; `value_num`: `(1,234)` → -1234; `-`/`–`/`—`/`''`/text → NULL.
- **cell_state is value-token driven** (schema NOTE A): `{-,–,—}`→null,
  `#`→suppressed, `0`→zero, `''`→empty, parseable→reported. `GCell.cell_state`
  used ONLY for `is_shade` (`grey`→1) + reconciliation cross-check; on
  disagreement the value token wins + a warning row is emitted (default Q5) —
  never a load failure.
- `period` resolved per-cell (§2), NEVER NULL.
- **`unit` resolved per-cell (Feature A full chain above), materialised onto
  `cell_fact.unit`.** NULL only when genuinely unknowable (chain 1–6 all miss),
  with a warning naming the table. The `v_cell`/`v_cell_flat` views read this column.
- **`geo_key` / `segment_key` MATERIALISED onto `cell_fact` at load (2026-07-16):**
  effective geo = `COALESCE(row, col, table_t, 'GLOBAL')`, effective segment =
  `COALESCE(row, col, 'SEG_TOTAL')`, precedence matching the old view COALESCE so
  the fact equals the view cell-for-cell. Self-describing (mirror of `unit` /
  `period_span`); `v_cell`/`v_cell_flat` read `f.geo_key`/`f.segment_key` directly.
  See §3b / §3c.

## 2. Period derivation (GTable has no period field)
Most-specific wins: (1) column-date → `col_period` (leaf OR inherited from a
period GROUP banner); (1b) ROW-date → `row_period` (see row-axis mirror below);
(2) title date/period → `table_t.period`; (3) unit/stitch-
supplied period-instance date; (4) `doc_period` last resort. FAIL LOUD if 1–3
unresolved AND doc_period NULL. ISO-normalize via one shared parser (extract_run
`_resolve_period` pattern).
- **GENERAL PERIOD-EXPRESSION grammar (`parse_period_expr` /
  `parse_period_span`, extended 2026-07-14):** every period axis and title is
  parsed by ONE deterministic grammar — no per-bank rules. `parse_period_expr`
  returns the ISO period **END** date only (title accessor, kept string-returning
  for back-compat); its sibling `parse_period_span(text, *, column)` returns
  `(iso_end, span, iso_start)` and is what the loader uses to populate
  `col_period`/`period_span`/`period_start` (col groups, leaves, titles).
  **CALENDAR-FISCAL ASSUMPTION:** every issuer in this corpus (DBS/OCBC/UOB, all
  Singapore) closes 31 December, so fiscal == calendar; a non-calendar-fiscal
  issuer would need its year-end wired in (single shared calendar rule, not a
  per-bank branch). Forms (END date, `span`):
    * `DD-Month-YYYY` explicit date → that date, span `as_at` UNLESS a duration
      prefix upgrades it: `'Year ended 31 December 2024'` → FY, `'Half year ended
      30 June 2025'` → 1H (by end month), `'Quarter ended 31 March 2025'` → 1Q,
      `'Nine months ended 30 September 2025'` → 9M;
    * halves `'1st Half 2025'`/`'First Half 2025'`/`'1H25'`/`'1H 2025'` → **30
      Jun**, span `1H`; `'2nd Half 2024'`/`'2H24'` → **31 Dec**, span `2H`;
    * quarters `'2Q25'`/`'2Q 2025'`/`'Second Quarter 2025'` → last day of month
      `3*Q` (1Q 31 Mar, 2Q 30 Jun, 3Q 30 Sep, 4Q 31 Dec), span `nQ` (printed
      convention: `'3Q25'` is THE third quarter — span `3Q`, **not** nine-months);
    * nine-months `'9M25'`/`'9M 2025'`/`'YTD25'` → **30 Sep**, span `9M`
      (cumulative YTD, SG-bank Q3 convention);
    * full-year `'FY2024'`/`'FY24'`/`'Full Year 2024'` → **31 Dec**, span `FY`;
    * **COLUMN context only** (`column=True`): a **bare 4-digit year** `'2025'` →
      **31 Dec**, span `FY`, and a **month-year** `'Dec-25'`/`'Dec 2025'` →
      month-end, span `as_at`. The column axis is unambiguously periodic; a bare
      year there IS a period (fixes defect a — bare-year group banners `'2025'`/
      `'2024'` were refused, so their leaves kept `'2025 > $m'` lineage and their
      cells fell to the doc default, mis-stamping the FY2024 column with the wrong
      period).
  GUARD: outside column context (titles/prose, `column=False`) a **bare year
  alone** is NOT a period (ambiguous start vs end) → None. Two call modes:
  `parse_period_expr`/`parse_period_span(column=False)` are LOOSE (find a period
  trailing a descriptive **title** — fixes the geography period-instance tables
  `'Selected income statement items — 1st Half 2025'`); `is_period_text(text, *,
  column)` is the residual-GUARDED axis predicate used for column leaf/group
  headers (§1 period-axis exclusion), and takes the same `column` flag so a
  bare-year banner is recognised while `'Note 3 2025'` (residual `'note 3'`) is
  not.
- **COLUMN residual: footnote + unit strip (2026-07-15, defects c/d):** in COLUMN
  context ONLY, before the `is_period_text` residual whitelist runs, footnote
  markers are stripped ANYWHERE (superscripts INSIDE the text `'2H25¹ $m'`, not
  just trailing, plus `'(1)'` indices `'2H 2025 (1)'`) and unit tokens
  (`'$m'`/`'S$m'`/`'%'`/`"'000"` …) are removed from the residual — so a combined
  period+unit header (`'2025 $m'` → residual `'$m'` → `''`) and a footnoted period
  column parse as period axis (col_period + span) while a descriptive header keeps
  its words (`'Net loans $m'` → `'net loans'` → not a period). The unit is STILL
  PARSED separately via `parse_unit` for `col_dim.unit` (`'2025 $m'` yields
  col_period FY2025 AND unit `'S$m'`). Titles (`column=False`) are unchanged. The
  canonical span-token set is verbatim `{1Q,2Q,3Q,4Q,1H,2H,9M,FY,as_at}`.
- **SPAN + START columns (additive, 2026-07-14):** `col_dim.period_span` /
  `table_t.period_span` (TEXT, vocabulary `{as_at,1Q,2Q,3Q,4Q,1H,2H,9M,FY}`) is
  the human-readable duration qualifier; `period` stays the END date so all joins
  and sorting on `period` are unchanged. This DISTINGUISHES flows that collide on
  the end date: `'2H25'` and `'FY2025'` both end **31 Dec 2025** but differ by
  span 2H vs FY (fixes defect b — half-year and full-year flows were
  indistinguishable). Alongside it, `col_dim.period_start` / `table_t.period_start`
  (DATE, NULL for `as_at`) is the calendar-fiscal period START (FY/1H/9M → **Jan
  01**, 2H → **Jul 01**, quarter n → first day of that quarter). The
  `[period_start, period]` interval is the MACHINE semantic of a flow — downstream
  derivations (`3Q = 9M − 1H`, `2H = FY − 1H`) become date arithmetic rather than
  enum decoding; the span token is provenance. Both are populated wherever a
  period resolves (col groups, leaves, titles) and exposed effective (col else
  table) in `v_cell`/`v_cell_flat` as `period_span`/`period_start`.
- **`period_span` MATERIALISED onto `cell_fact` (2026-07-15):** the effective span is denormalised onto `cell_fact.period_span` at load, PAIRED with the per-cell `period` (span of whichever axis won the period: col span for a col period, row span for a row period, else table span; doc_period fallback → NULL) so the base fact row is self-describing (FY vs 2H disambiguated without a join, same rationale as `cell_fact.unit`); `v_cell`/`v_cell_flat` now read `f.period_span` directly. `period_start` stays on the dims only (optional sugar).
- **ROW-AXIS PERIOD MIRROR (2026-07-15):** some tables put the period in the ROW axis (UOB NPL: row labels `'Dec-25'`/`'Jun-25'`/`'Dec-24'`, geographies in COLUMNS). `row_dim.row_period`/`period_span`/`period_start` mirror `col_dim` EXACTLY: each row label is parsed with the SAME column-context grammar + residual guard (footnote/unit strip, bare-year allowed) — a pure-period row yields the triple; a descriptive row carrying an incidental date is refused (`'Balance at 1 January 2025'` → residual `'balance at'` fails the whitelist → not a period). A period row ALSO applies PERIOD-AXIS EXCLUSION to `row_lineage` exactly like `col_lineage` (a `'Dec-25'` row does not mint lineage `'dec-25'`; falls back to `'value'`), so reporting dates converge instead of minting fresh ids; the `row_leaf_label` stays VERBATIM. Per-cell precedence becomes **col_period > effective_row_period > table > doc**; if BOTH col and (effective) row carry a period and they DIFFER, col wins and a warning is emitted (`f"{table_id} r{row}c{col}: period on both axes (col {x} vs row {y}) — col wins"`). The paired span rule above picks up the row span automatically.
- **ROW-PERIOD INHERITANCE + bare-year year-headers (2026-07-15):** a bare-year ROW label (`'2025'`/`'2024'`, a section-header with line items nested under it in performance-by-segment tables) parses as **FY** of that year — same as the column bare-year rule, same guards. `effective_row_period(row)` = the row's OWN parse, else the OWN parse of its NEAREST ANCESTOR (walk `row_parent`, nearest first), else None — so a real line item under a `'2025'` header inherits FY2025 while its OWN parse wins if it itself is a period (rare). Sibling year blocks are isolated by the parent chain (`'2024'` children walk to `'2024'`, never `'2025'`). **MATERIALIZATION:** `row_dim.row_period`/`period_span`/`period_start` store the OWN parse ONLY (inheriting children keep NULL — they are line items, not period rows), and lineage exclusion likewise acts ONLY on own-parse period rows (the year header is dropped from a child's lineage so both year blocks' identical line items converge; the child keeps its real lineage). Inheritance is a CELL-resolution concept only. after a table's periods resolve, if `doc_period`
  is neither among the table's `col_period`s nor equal to `table_t.period`, emit a
  load-summary warning (`doc_period D not among table periods [...]`). Advisory only —
  never fails; a prior-period comparative side-table (e.g. a 31 Dec 2024 balance-sheet
  strip inside a 30 Jun 2025 report) legitimately trips it.

## 3. Idempotency & failure semantics
- Doc-scoped reload: DELETE cell_fact/row_dim/col_dim/table_t WHERE doc_id
  (FK order). Never delete section/document (upstream-owned) or the global
  lineage registries (orphans harmless).
- Registries: `INSERT OR IGNORE` on lineage_key then SELECT (get-or-create).
- Fail loud: missing parsed.json → FileNotFoundError; 0 tables / 0-byte
  artifact → RuntimeError; unmerged `continued_from_previous=True` at loader →
  FAIL (upstream contract violation); depth>5 → FAIL; unknown section_id → FAIL.

## 3b. Geography stamping (added 2026-07-14 — deterministic, zero API)
The geo axis can sit in EITHER rows (OCBC/UOB print Singapore/Malaysia/… as ROWS)
or columns (DBS performance-by-geography prints them as leaf COLUMNS), so the
loader stamps BOTH `row_dim.geo_key` and `col_dim.geo_key`.

- **Match rule — EXACT normalised full-label equality, never substring.** Each row
  label and each column leaf/group label is normalised by `geo_norm` = `_clean_label`
  then `.lower()` (footnote markers stripped, whitespace collapsed, lowercased — the
  SAME normalisation a single-level `lineage_key` uses), then looked up in `geo_map`
  by exact key. `'Singapore Government securities'` → `'singapore government
  securities'` ≠ key `'singapore'`, so it does NOT mis-stamp. `geo_map` is loaded
  ONCE per `load_units` call from the target DB.
- **Effective per-cell geography** is `COALESCE(row.geo_key, col.geo_key,
  table_t.geo_key, 'GLOBAL')` (most specific axis wins, else the default member),
  exposed as `geo_key` in `v_cell` / `v_cell_flat`. **MATERIALISED onto
  `cell_fact.geo_key` at load (2026-07-16):** the effective value is denormalised
  onto the fact so the base row is self-describing (same rationale as
  `cell_fact.unit` / `period_span`); `v_cell`/`v_cell_flat` now read `f.geo_key`
  DIRECTLY (no COALESCE). The precedence order at load MATCHES the old view COALESCE
  exactly (row > col > table > `'GLOBAL'`), so `cell_fact` == view cell-for-cell
  (parity-preserving). Reuses the per-row/per-col stamps already computed during
  stamping — no re-query. The row/col stamps remain the source of truth.
- **Rollup honesty (judgment calls, encoded in `geo_dim` seeds):**
  - `GC_EX_HK` ('Greater China ex-HK', bucket, parent `GREATER_CHINA`) for DBS's
    'Rest of Greater China' — it EXCLUDES Hong Kong (printed as its own line), so
    mapping it to `GREATER_CHINA` would double-count HK on rollup. `HK + GC_EX_HK
    = GREATER_CHINA`, no overlap.
  - `SSEA` ('South and Southeast Asia', bucket, parent `ASIA`) for DBS's combined
    line (three printed spellings aliased); attached at `ASIA` because it spans
    ASEAN + South Asia.
  - `OTH_APAC` ('Other Asia Pacific', bucket, parent `ASIA`) for OCBC's residual
    Asia-Pacific line.
  - `'Greater China 4'` (UOB footnote OCR'd as a trailing space+digit — not a
    superscript, so `_clean_label` cannot strip it) is aliased to `GREATER_CHINA`.
- **Geo drift signal (mirrors the concept drift queue).** A table is
  geography-context when ≥2 rows OR ≥2 columns are stamped. In such a table, any
  remaining data row (total/note/section_header/sub_header excluded) at the SAME
  hierarchy level as a stamped row, but with no `geo_key`, emits one load-summary
  warning `"{table_id}: possible unmapped geography label {label!r} — extend
  geo_map if it is one"` so a new bank's novel bucket surfaces loudly instead of
  silently staying NULL. General + deterministic; no per-bank rule.

## 3c. Segment (business-line) stamping and default-member views (added 2026-07-14 — deterministic, zero API)
Mirrors §3b geography exactly: `segment_dim(segment_key, label, seg_level, parent_seg)`
+ `segment_map(label_norm, segment_key)`, seeded once (schema_v7.sql), loaded once
per `load_units` call. The segment axis sits overwhelmingly in COLUMNS in this
corpus (all 3 banks print business lines as leaf columns; DBS additionally uses a
`Markets` / `Commercial Book` SPAN BANNER over its leaves), but rows are stamped
too for generality (mechanically identical to geo).

- **Match rule** — identical to geo: `seg_norm` = `geo_norm` (`_clean_label` then
  `.lower()`), EXACT full-label equality, never substring (`'Trading income'` ≠
  key `'trading'`). `segment_map` loaded once per `load_units` from the target DB.
- **Members (`segment_dim` seeds):** `SEG_RETAIL` (consumer/wealth/private),
  `SEG_WHOLESALE` (institutional/corporate), `SEG_MARKETS` (treasury/trading/global
  markets), `SEG_INSURANCE`, `SEG_OTHER` (residual bucket) — all `seg_level='line'`
  or `'bucket'`, `parent_seg='SEG_TOTAL'`. `SEG_TOTAL` (`seg_level='total'`,
  `parent_seg=NULL`) is the DEFAULT member — inserted first (its own parent-of-all
  role is checked immediately under `PRAGMA foreign_keys=ON`).
- **Harvested aliases (`segment_map` — see loader-design task report for the full
  harvest table):** DBS `'Consumer Banking/ Wealth Management'`→`SEG_RETAIL`,
  `'Institutional Banking'`→`SEG_WHOLESALE`, `'Trading'` + span banner
  `'Markets'`→`SEG_MARKETS`; OCBC `'Global Consumer/ Private Banking'`→`SEG_RETAIL`,
  `'Global Wholesale Banking'`→`SEG_WHOLESALE`, `'Global Markets'`→`SEG_MARKETS`,
  `'Insurance'`→`SEG_INSURANCE`; UOB abbreviations `'GR'`→`SEG_RETAIL`,
  `'GWB'`→`SEG_WHOLESALE`, `'GM'`→`SEG_MARKETS` (+ full-name aliases `'Group
  Retail'`/`'Group Wholesale Banking'`). `'Others'`→`SEG_OTHER` (shared spelling
  with geo `OTH` — accepted ambiguity, same as geo's own `'Others'` collision;
  context and the reconciliation gate keep each dimension internally consistent).
  **USER-APPROVED plain aliases:** `'Total'` and `'Group'` → `SEG_TOTAL` — a
  whole-bank/Group column IS the default-member slice, so stamping it `SEG_TOTAL`
  is consistent, not contradictory (this is what lets the column-sum
  reconciliation gate below locate the total column deterministically).
  **Deliberately NOT mapped:** DBS `'Commercial Book'` — a supra-segment span
  banner (Consumer+Institutional+Others, i.e. everything except Markets), not one
  of the canonical members; its leaves carry the real member keys.
- **Effective per-cell segment** is `COALESCE(row.segment_key, col.segment_key,
  'SEG_TOTAL')` — the **default-member trick** (dimension_model.md): a base-table
  NULL means "no explicit slice", which IS the default member, so a whole-bank
  fact is a plain filter (`segment_key='SEG_TOTAL'`) rather than a special case.
  **MATERIALISED onto `cell_fact.segment_key` at load (2026-07-16),** exactly as
  geo above (row > col > `'SEG_TOTAL'`, matching the old view COALESCE so it is
  parity-preserving); `v_cell`/`v_cell_flat` read `f.segment_key` DIRECTLY. Both
  effective dims are now stored on the fact (self-describing, mirror of
  `cell_fact.unit`/`period_span`); the row/col stamps stay populated for
  inspection and remain the source of truth for the materialisation.
- **Segment drift signal** — mirrors the geo drift warning, SAME majority-gate
  rule (≥2 rows OR ≥2 columns stamped a segment MEMBER — `SEG_TOTAL` excluded from
  the count so an incidental `'Total'`/`'Group'` column never trips it), warning
  text `"{table_id}: possible unmapped segment label/column {label!r} — extend
  segment_map if it is one"`.
- **Column-sum reconciliation gate + RECORD (2026-07-15 — warning-only, never a
  load failure; now also STORES the verified relation):** a dimension (segment or
  geo) only genuinely PARTITIONS a table's columns when `>=2` leaf columns carry
  that dimension's MEMBER keys (`!= SEG_TOTAL` / `!= GLOBAL`) **and** `>=1` leaf
  column carries its DEFAULT member (`SEG_TOTAL` or `GLOBAL` — THE total column).
  `%`-unit columns are excluded from both sets (non-additive, mirroring
  `verified_sums_to`'s %-gating). Only then, per value-bearing row
  (section/sub-header/note excluded; rows with any NULL member cell skipped):
  `Σ sign·member cells ≈ total cell` within tolerance `1.0 * n_members`
  (`sign = −1` for a detectable `'less: eliminations'` member column, else `+1`).
  - **Reconciles across EVERY checkable row** → the relation is the COLUMN mirror
    of `row_dim.sums_to`: each MEMBER column gets `col_dim.sums_to = the total
    col_id` and `col_dim.sums_sign` (`+1`/`−1`); the total column keeps `sums_to`
    NULL (like a row total). These are additive `col_dim` columns (comments mirror
    `row_dim`); no `v_cell`/`v_cell_flat` change (col property; views untouched so
    `concept/load_dictionary.py`'s duplicate view DDL stays in sync).
  - **Does NOT reconcile** → `sums_to` left NULL + ONE warning per mismatching row
    `f"{table_id} row {label!r}: {dim} members sum {s} != total {t}"` (unchanged).
  All other tables (period-column tables, tables with no default-member column,
  single-axis tables) are SILENTLY skipped — the gate never fires and nothing is
  recorded. This COMPLEMENTS the default-member labelling (total-vs-member); the
  `sums_to` link adds the verified ARITHMETIC. Measured on the real DBS/OCBC/UOB
  corpus: the gate enters the checkable state on every business/geographical-segment
  table that carries both member and total columns, and every row reconciles
  exactly (0 mismatch warnings) — e.g. DBS 'Net interest income' 1H2025: 3099
  (Consumer) + 3155 (Institutional) + 1090 (Others) − 15 (Trading) = 7329 = the
  printed Total.

## 4. Resolved defaults (accepted 2026-07-13)
1. Pure-date column lineage fallback token = `'value'` (group banner first).
2. `table_type` supplied by router/unit; loader never guesses beyond slug(title).
3. `GTable.label_header` dropped (display-only).
4. `note` rows loaded (verbatim fidelity, no cells).
5. cell_state conflicts: value token wins + warning, never fail.

## 5. Pipeline pivot — swapped title/label_header repair (2026-08-04)

Fixes a table-classification gap surfaced while getting the dashboard's 27
spine metrics automation-ready: DBS's per-share exhibit had
`title='DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES'` (the page masthead) and
`label_header='Per share data ($)3,8'` (the real caption) — swapped. Since
`table_type` and `table_id` both slug from `title`, the exhibit classified
`table_type_id IS NULL`, which blocks `stamp_human_anchors` (requires a
resolved `table_type_id`) from projecting the table's three already-loaded
human-confirmed anchors (`bs.nav_per_share`, `pnl.eps.basic`,
`pnl.eps.diluted`).

**This partially revisits §4 item 3** ("`GTable.label_header` dropped,
display-only"): `label_header` is now READ, before that drop, by
`repair_swapped_captions()` (`load_v7.py`) — a two-signal check (title fully
explained by the filer's own name + corporate boilerplate; label_header has
real caption content once unit parentheticals/footnote digits are stripped)
that swaps the two fields in place when both fire, so no verbatim text is
invented or lost. Verified against the two other corpus tables sharing the
masthead-title shape that a one-signal rule would have corrupted (one has no
real caption in `label_header` to recover; one is a genuine long caption
that merely mentions the filer, not a masthead) — the repair fired exactly
once in the whole corpus.

**Pivot, called out per CLAUDE.md**: this is a loader rule, not a one-off
patch — it will fire on any future filing where the same extractor slip
recurs, routing that exhibit to its real registry type instead of
UNCLASSIFIED, with zero new registry aliases. Observable in the load
warnings (`swapped-caption repair: …`) and in `seed_registry.py`'s
classified/unclassified counts. Tests: `pass2/test_swapped_caption.py`.

## Upstream contract (for reference)
TOC stage (`toc_stage.py`): slim Gemini heading call → coordinate-window
finalize (candidates → text-search → page-top anchors) → `has_tables` from
region attribution → toc_v3.json → `toc_to_db.py` inserts document + ALL
section rows (prose included; has_tables stays in the manifest — DB truth for
"has tables" is `LEFT JOIN table_t` after extraction). pass2 extracts only
`has_tables=true` sections, 4 prompt routes decided from page spans.
