# MAPPING_LAYER — per-bank tagging template for auto-ingestion

Status: **PROPOSED**. Scope: decisions 1 + 4 only (the pair that blocks the
Key Financial Highlights dashboard). Decisions 2 (un-materialize derived facts)
and 3 (dimension bridge) are settled but deferred — see §7.

Date: 2026-07-31 · Builds on `docs/specs/2026-07-31-schema-gap-analysis.md`
Related: `docs/specs/2026-07-30-cross-bank-identity-mapping.md` (the 26 items)

> **Where the masterlist lives:** `docs/specs/2026-08-04-masterlist.md` is
> authoritative for what the masterlist is, which files/tables store it, and the
> **one-writer-per-level rule**. This spec covers `table_registry` and
> `bank_line_map` as *schema*; it does not define the masterlist. Note in
> particular that `bank_line_map` is NOT the masterlist — it is an additive
> accumulation that enriches it.

---

## 0. The problem in one table

`DBS_1Q26_trading_update`, exhibit `Selected income statement items ($m)`:

| row | depth | label | today's `concept_key` | 1Q26 | 4Q25 | 1Q25 |
|---|---|---|---|---:|---:|---:|
| 1 | 0 | Commercial book total income | `pnl.income.total` | 5,559 | 5,177 | 5,542 |
| 2 | 1 | Net interest income | `pnl.nii.net` | 3,475 | **3,592** | 3,719 |
| 5 | 0 | Markets trading income | `pnl.noninterest.trading` | 389 | 154 | 363 |
| 6 | 1 | Net interest income | `pnl.nii.net` | 19 | **1** | (38) |
| 8 | 0 | Total income | `pnl.income.total` | 5,948 | 5,331 | 5,905 |
| 9 | 1 | Of which: Net interest income | `pnl.nii.net` | 3,494 | **3,593** | 3,681 |

Three rows share one concept; two more share another. Row 9 is the one that
compares to UOB's and OCBC's single NII line. Nothing in the current key
`(table_type, label_norm)` can tell them apart.

The parent can, and `row_lineage` already records it on 100% of rows.

---

## 1. Decision 1 — `table_type` becomes a registry-assigned stable ID

### 1.1 Why the current value cannot be the key

`table_t.table_type` is a slug of the model's table title. Measured on the
375-table corpus:

- **217 distinct values; 158 (73%) appear in exactly one document.**
- The same logical exhibit drifts across quarters:
  `selected_income_statement_items_m` (1Q26, 4Q25) vs
  `selected_income_statement_items_1st_half_2025` (2Q25) vs
  `performance_by_business_segments1_selected_income_statement_items2` (2Q22).
- Stripping period tokens and footnote digits collapses **217 → 175 only (19%)**.
  The rest is genuine title variation: `financial_highlights` (6) /
  `financial_highlights_continued` (11) / `financial_highlights_unaudited` (7)
  are one OCBC exhibit under three titles.

**Therefore normalization alone is insufficient.** The registry needs a seeded
**alias table** — many normalized titles → one stable ID — which is also what
makes a miss flaggable and the map learnable.

### 1.2 What exists today (do not mistake this for a registry)

`pipeline/concept/load_dictionary.py::map_table_type_norm()` — 11 substring
rules → 7 buckets. Measured coverage: **129/375 (34%)**; **246 (66%) fall
through to `'*'`**. A `'*'` row then resolves against *wildcard* aliases, i.e.
the system guesses. That is the behaviour this spec removes.

The MAS-form path (`template_row`/`template_col`/`template_cell`, with
`parent_line_no` + `concept_key`) is the correct pattern already — but it is
scoped to regulatory forms shared across banks, and is not present in
`compiled_fs.db`. This spec generalizes that pattern to per-bank exhibits.

### 1.3 DDL

```sql
-- The controlled vocabulary. ~18-25 rows. Hand-seeded, grows by review.
CREATE TABLE table_registry (
  table_type_id   TEXT PRIMARY KEY,   -- STABLE, registry-assigned. Never derived
                                      -- from a title. e.g. 'FS_INCOME_SELECTED'
  display_name    TEXT NOT NULL,
  statement_class TEXT NOT NULL,      -- income_statement | balance_sheet | ratios
                                      -- | per_share | credit_quality | capital
                                      -- | regulatory | segment | geography
  period_nature   TEXT NOT NULL,      -- duration | instant | mixed
  dim_hint        TEXT,               -- axis this exhibit decomposes along, if any:
                                      -- 'segment' | 'geo' | 'industry' | NULL
  is_regulatory   INTEGER NOT NULL DEFAULT 0,  -- 1 -> MAS template_row path
  notes           TEXT
);

-- Many normalized titles -> one stable ID. THIS is what absorbs title drift.
CREATE TABLE table_registry_alias (
  alias_norm    TEXT NOT NULL,        -- output of normalize_exhibit_title()
  bank          TEXT,                 -- NULL = applies to all banks
  table_type_id TEXT NOT NULL REFERENCES table_registry(table_type_id),
  source        TEXT NOT NULL,        -- 'seed' | 'human_confirmed'
  added_at      TEXT NOT NULL,
  PRIMARY KEY (alias_norm, bank)
);

-- Resolution result, per table, per ingest. Never overwrites table_t.table_type
-- (as-reported is preserved; this is the normalized pointer).
ALTER TABLE table_t ADD COLUMN table_type_id TEXT;  -- NULL = UNCLASSIFIED
```

### 1.4 `normalize_exhibit_title(title) -> alias_norm`

Deterministic, no LLM. In order:

1. lowercase; unicode NFKC; curly quotes → ASCII
2. strip footnote markers — **prefer the geometry stage's typographic decision**
   (superscript size + baseline) when `hierarchy_source='geometry'`; fall back to
   trailing-digit-after-word-char otherwise
3. strip period tokens: `1st/2nd/3rd/4th half|qtr|quarter`, `year YYYY`, `FYnn`,
   `nQnn`, `nH nn`, bare `YYYY`, `dd mmm yyyy`
4. strip unit/currency parentheticals: `($m)`, `(%)`, `(s$)`, `($)`
5. strip assurance/continuation noise: `continued`, `unaudited`, `cont'd`
6. collapse punctuation/whitespace → `_`; trim

**Explicitly NOT stripped** — dimensional qualifiers are part of the exhibit's
identity: `by_geography`, `by_business_segments`, `by_industry`, `by_currency`,
`by_maturity`, `by_collateral_type`, `by_loan_grading`.
`performance_by_geography_selected_income_statement_items` is a *different*
exhibit from `selected_income_statement_items` — it carries a geo axis. Merging
them would silently stamp geography-decomposed values as group totals.

### 1.5 Resolution and the no-guess rule

```
alias_norm = normalize_exhibit_title(table_title)
lookup (alias_norm, bank) -> (alias_norm, NULL) -> MISS
MISS  => table_type_id = NULL, table marked UNCLASSIFIED,
         one review_queue row (reason='table_unclassified'),
         and NO row in that table is mapped or loaded.
```

There is no `'*'` fallback and no fuzzy match at load time. A near-miss is a
flag, not a guess. (AI may *propose* an alias for the queue — §4 of
INGEST_LOOP — but the proposal does not load values.)

### 1.6 Seed scope

Seed from the 375-table corpus, prioritising the exhibits the 26-item dashboard
needs. Minimum viable seed (the four DBS overview sub-tables plus the UOB/OCBC
equivalents):

| `table_type_id` | statement_class | period_nature | covers |
|---|---|---|---|
| `FS_INCOME_SELECTED` | income_statement | duration | DBS `Selected income statement items ($m)`; UOB `Financial Highlights` income block; OCBC `financial_highlights*` income block |
| `FS_BALANCE_SELECTED` | balance_sheet | instant | DBS `Selected balance sheet items ($m)`; UOB/OCBC BS summaries |
| `FS_RATIOS_KEY` | ratios | mixed | DBS `Key financial ratios (%)`; UOB/OCBC ratio blocks |
| `FS_PER_SHARE` | per_share | mixed | DBS `Per share data ($)`; UOB/OCBC EPS + NAV |
| `FS_INCOME_STATUTORY` | income_statement | duration | audited/condensed income statements |
| `FS_BALANCE_STATUTORY` | balance_sheet | instant | audited/condensed balance sheets |

`period_nature='mixed'` means the exhibit contains both instant and duration
rows (a ratio table carrying CET1 as-at alongside ROE for the period); the
row's own `period_type` from the concept dictionary decides, not the table's.

---

## 2. Decision 4 — `bank_line_map` is the source of truth

### 2.1 Why identity has never stuck

Measured: `row_dim.concept_key` is populated on 30.8% of rows, and it is
**re-derived unconditionally** by `concept/run.py` from the alias table on every
run, and **wiped entirely** by a doc-scoped reload (the recorded reload gotcha).
Two consequences already observed:

- the mis-stamp fixes were reverted by pre-existing wildcard aliases, and are
  currently held in place only by running the fixer *again* after
  `concept/run.py` — a workaround, not a fix;
- a reload silently drops the doc out of `fact_metric` until concepts re-run.

Identity lives in a location that is overwritten by design. Moving it to
`bank_line_map` fixes the class, not the instance.

### 2.2 DDL

```sql
CREATE TABLE bank_line_map (
  map_id            INTEGER PRIMARY KEY,
  -- ANCHOR (stable across quarters)
  bank              TEXT NOT NULL,
  table_type_id     TEXT NOT NULL REFERENCES table_registry(table_type_id),
  row_label_norm    TEXT NOT NULL,
  parent_label_norm TEXT NOT NULL DEFAULT '',   -- '' = top-level row

  -- IDENTITY
  concept_key       TEXT,                       -- NULL + is_abstract=1 for headers
  legal_entity      TEXT NOT NULL DEFAULT 'CONSOLIDATED',  -- | PARENT_COMPANY | BANK_SOLO
  segment_key       TEXT,                       -- column-per-axis (decision 3)
  geo_key           TEXT,
  industry_key      TEXT,

  -- INTRINSIC (projected from concept dictionary; denormalized for the gate)
  period_type       TEXT,                       -- instant | duration
  balance           TEXT,                       -- debit | credit
  is_abstract       INTEGER NOT NULL DEFAULT 0,
  negated_label     INTEGER NOT NULL DEFAULT 0,

  -- GOVERNANCE
  map_status        TEXT NOT NULL,              -- ai_proposed | ai_verified
                                                -- | human_confirmed | human_corrected
                                                -- | deprecated
  mapped_by         TEXT NOT NULL,              -- 'gemini-2.5-pro' | 'seed' | user id
  confidence        REAL,
  mapped_at         TEXT NOT NULL,
  superseded_by     INTEGER REFERENCES bank_line_map(map_id),

  UNIQUE (bank, table_type_id, row_label_norm, parent_label_norm)
);

CREATE INDEX ix_blm_anchor ON bank_line_map(bank, table_type_id);
```

### 2.3 Per-column justification — what breaks without it

| column | what breaks without it |
|---|---|
| `bank` | UOB's `Loans to customers` is net; OCBC's same label in a note is a maturity bucket. One global map cannot hold both. |
| `table_type_id` | §1 — without a stable ID the anchor drifts every quarter and the map never converges. |
| `row_label_norm` | the base anchor. |
| **`parent_label_norm`** | **the three-NII collapse.** Rows 2/6/9 above are identical text; only the parent separates 3,592 / 1 / 3,593. Also separates DBS's two `Basic` EPS rows (parent `Earnings` 3.88 vs `Reported earnings` 3.84) — which is why EPS is currently unmapped on purpose. |
| `concept_key` | the identity itself. |
| **`legal_entity`** | **the Group/Company merge.** DBS `Total equity` resolves to 17,643 (Company) with the Group's 68,916 absent from `fact_metric` entirely; UOB `Total assets` returns 485,263 (Bank) instead of 572,061 (Group). `fact_metric`'s PK has segment/geo/industry but no consolidation axis, so these collide as duplicates and the resolver picks one. |
| `segment_key` | `Markets trading income` (1,374) vs `Commercial book total income` (21,526) vs group `Total income` (22,900) are the same element at different members. Without the member they are three rival values for one key. |
| `geo_key`, `industry_key` | same argument for the `by_geography` / `by_industry` exhibits. |
| `period_type` | separates 9M from 3Q and instant from duration. Today `cell_fact` has **no `period_start` column** at all — it is derived in `v_cell_flat`. A 31-Dec stock and an FY flow ending the same day are indistinguishable without it. |
| `balance` | OCBC `Total operating expenses` is stored as **both +5,882 and −5,882**; UOB `Loans to customers` as −16,970. Sign must come from the element, not the page. |
| `is_abstract` | today a header carries `concept_key = NULL`, which is indistinguishable from "unmapped" — visible as the ambiguous `—` column in the tagged-table report. Without the flag, coverage metrics are uninterpretable and headers keep re-entering the review queue forever. |
| `negated_label` | presentation-only sign flips (`Less: Operating expenses`); the other half of `balance`. |
| `map_status` | gates loading. `ai_proposed` never loads a value. |
| `mapped_by`, `confidence`, `mapped_at` | provenance; lets you audit which model proposed what, and re-run proposals when the dictionary changes. |
| `superseded_by` | a correction must not destroy the prior mapping — restatements need the history to explain why last quarter's number moved. |

`period_type`/`balance`/`is_abstract` are **intrinsic to the concept**, denormalized
here so the load gate is one join. The dictionary remains authoritative; a
mismatch between the two is itself a check (see INGEST_LOOP §5).

### 2.4 One-way flow — the rule that makes it durable

```
        bank_line_map  (durable, human-authoritative)
              |
              |  PROJECT  (deterministic, idempotent, read-only on the map)
              v
        row_dim.concept_key / segment_key / geo_key / industry_key / unit
              |
              v
        cell_fact  ->  fact_metric
```

Hard invariants, to be enforced in code and asserted in tests:

1. **The projection never writes to `bank_line_map`.** Only two things write to
   it: the seed loader, and the review-queue resolution step (§4 of INGEST_LOOP).
2. **A doc-scoped reload never touches `bank_line_map`.** Reload deletes
   `row_dim`/`cell_fact` for that doc and re-projects. This *solves* the recorded
   gotcha: identity is restored automatically instead of being wiped.
3. **`concept/run.py` stops re-deriving `row_dim.concept_key` from wildcard
   aliases.** It becomes the projection step. This is the change that makes the
   mis-stamp fixes durable and lets the 3 polluted aliases be purged.
4. **MERGE semantics on ingest**: a novel anchor INSERTs an `ai_proposed` row.
   An existing anchor is left ALONE — `human_confirmed` / `human_corrected`
   survive every re-run, exactly as the table registry protects manual fixes.
5. **Precedence** when several statuses exist for one anchor:
   `human_corrected > human_confirmed > ai_verified > ai_proposed > deprecated`.

### 2.5 What `row_dim` becomes

`row_dim.concept_key` and the three dim columns become a **materialized
projection** — still queried exactly as today (so `v_cell_flat`, `sync_bq`,
`fact_metric` and the app are unaffected), but no longer hand-editable. Direct
writes to them outside the projection step should fail a test.

`cell_fact.concept_key` stays 0% populated and unused; `v_cell_flat` already
does `COALESCE(r.concept_key, f.concept_key)`. No change needed.

---

## 3. Worked example — the three-NII case end to end

### 3.1 Registry

```
normalize_exhibit_title('Selected income statement items ($m)')
  -> 'selected_income_statement_items'
table_registry_alias('selected_income_statement_items', NULL)
  -> table_type_id = 'FS_INCOME_SELECTED'
```

The 2Q25 variant `selected_income_statement_items_1st_half_2025` normalizes to
the **same** `alias_norm` once the period token is stripped — one registry row
absorbs both.

### 3.2 `bank_line_map` rows

| bank | table_type_id | row_label_norm | parent_label_norm | concept_key | segment_key | legal_entity | period_type |
|---|---|---|---|---|---|---|---|
| DBS | FS_INCOME_SELECTED | `commercial book total income` | `` | `pnl.income.total` | `SEG_COMMERCIAL` | CONSOLIDATED | duration |
| DBS | FS_INCOME_SELECTED | `net interest income` | `commercial book total income` | `pnl.nii.net` | `SEG_COMMERCIAL` | CONSOLIDATED | duration |
| DBS | FS_INCOME_SELECTED | `markets trading income` | `` | **`pnl.income.total`** | `SEG_MARKETS` | CONSOLIDATED | duration |
| DBS | FS_INCOME_SELECTED | `net interest income` | `markets trading income` | `pnl.nii.net` | `SEG_MARKETS` | CONSOLIDATED | duration |
| DBS | FS_INCOME_SELECTED | `total income` | `` | `pnl.income.total` | *(NULL → total)* | CONSOLIDATED | duration |
| DBS | FS_INCOME_SELECTED | `of which: net interest income` | `total income` | `pnl.nii.net` | *(NULL → total)* | CONSOLIDATED | duration |

Two element corrections fall out: `markets trading income` moves from
`pnl.noninterest.trading` to `pnl.income.total` (it is NII + non-II, not a
non-interest line), and the `non-interest income` row beneath it keeps
`pnl.noninterest.total` but gains `SEG_MARKETS` so it stops impersonating the
group's 8,400.

`SEG_COMMERCIAL` is a **new intermediate node**, not a rival partition —
`segment_dim` already has a `parent` column, and the business-segments exhibit
proves the tree: CBG 5,257 + IBG 4,400 + Others 1,013 = 10,670 =
`Commercial book total income` (2H25), and its `Trading` column equals the
Markets block exactly (NII 21, total income 593).

```
SEG_TOTAL
├── SEG_COMMERCIAL   (new; parent of the three below)
│   ├── SEG_RETAIL / SEG_WHOLESALE / SEG_OTHER
└── SEG_MARKETS
```

### 3.3 The calc assertion that proves it

```
pnl.nii.net @SEG_COMMERCIAL + pnl.nii.net @SEG_MARKETS = pnl.nii.net @total

1Q26:  3,475 + 19   = 3,494  ✅
4Q25:  3,592 +  1   = 3,593  ✅
1Q25:  3,719 + (38) = 3,681  ✅
```

Holding on 3/3 period columns is what makes the mapping structural rather than
coincidental. Note the 1Q25 markets NII of **−38 is a real negative value**, not
a variance artifact — the artifacts are the adjacent `% chg` columns
(`col_period` NULL, `period_span` NULL, values `(7)` / `NM` / `>100`), which are
currently loaded and must be dropped: **5,576 cells (22.5%) carry NULL span,
and 456 of 2,925 `fact_metric` rows inherit it.**

### 3.4 What the dashboard gets

Row 9 (`of which: net interest income`, no segment member) is the only DBS row
that resolves to group NII — so item 1 of the 26 compares like-for-like against
UOB's 9,355 and OCBC's 9,150. The other two NII rows remain queryable at their
members instead of competing for the headline.

---

## 4. Migration plan (additive, no extraction changes)

1. Create `table_registry`, `table_registry_alias`, `bank_line_map`; add
   `table_t.table_type_id`. All additive — `cell_fact` untouched.
2. Add `normalize_exhibit_title()` + tests.
3. Seed the registry for the six `table_type_id`s in §1.6; resolve aliases for
   the corpus; report coverage and the UNCLASSIFIED list.
4. Backfill `bank_line_map` from today's 2,013 stamped `row_dim` rows, joined to
   `row_lineage` for the parent anchor, at `map_status='ai_proposed'` — nothing
   is promoted to `human_confirmed` without review.
5. Author by hand the ~30 rows the 26-item dashboard needs across three banks,
   at `human_confirmed`.
6. Convert `concept/run.py` into the projection step (invariant §2.4.3).
7. Re-project; diff `fact_metric` before/after; the 26 items must move only in
   the directions this spec predicts.

Steps 1–5 are non-destructive and reversible. Step 6 is the behavioural pivot
and must be called out per CLAUDE.md when it lands.

---

## 5. Open items deliberately NOT in this spec

- `calc_linkbase` (step 2b of the brief) — the assertion in §3.3 is written by
  hand here; the table that generalizes it comes next.
- `metric_definition` + un-materializing the 117 derived `fact_metric` rows
  (decision 2, settled: snapshot → validate formula reproduces the values →
  delete).
- `ingest_review_queue` DDL — specified in INGEST_LOOP.
- `dim_axis`/`dim_member` bridge (decision 3, settled: **column-per-axis for
  this phase**; revisit when grading/collateral/currency exhibits enter scope).

## A title must not carry its POSITION or its PERIOD (2026-08-04)

Pre-flight **A4**. `table_type_id` is stable and registry-assigned precisely so
that a title which drifts between quarters does not fragment the key. Two
drift classes were still reaching `alias_norm`:

- **Hierarchical / lettered note numbering.** Only the flat form
  (`10. Deposits`) was stripped. `13.2 Geographical segments`, `12.3.1 …`,
  `A.3 Overview of key prudential regulatory metrics`, `A.6.1 IRBA RWA flow
  statement` kept their numbering — and note numbers renumber the moment a bank
  inserts a note above, so the same exhibit resolves one quarter and goes
  UNCLASSIFIED the next.
- **Spelled-out period qualifiers.** Only the YEAR was stripped, so OCBC's one
  income summary produced eight different keys — `first_half_performance`,
  `second_quarter_performance`, `nine_months_performance`, `full_performance`
  (a mangled 'Full Year … Performance') — and all eight went UNCLASSIFIED.

Both are fixed in `normalize_exhibit_title`. The numbering rule requires
trailing whitespace so a compact period token opening a title (`1Q25 key
financial indicators`) is left to the period vocabulary, and its optional
letter prefix only fires when digits follow, so an abbreviation cannot match.

### A4 asserts COVERAGE, not match LEVEL

A4 originally asserted `title == 0`, treating a title-level match as a weak
fallback. `registry.py`'s cascade documents the opposite — title IS the identity
for DBS-shaped documents, where the section is a bland page grouping. All 22
title-level matches were verified correct, and driving them to zero would
require aliases that are wrong or unmaintainable:

- `statement_of_changes_in_equity` as a SECTION alias merges the Group and
  Company statements — two different legal entities — into one type. Only the
  title separates them, which is why that key is deliberately unseeded.
- `performance` as a SECTION alias hijacks OCBC's `Allowances` / `Asset Quality`
  tables, which sit under a `<period> Performance` page header and today resolve
  correctly by title. Section is tried FIRST, so a broad section alias always
  beats a precise title.
- The remainder would need composite aliases keyed to drifting per-document
  section headers (`by_currency__loans_to_customers`,
  `2q25_year_on_year_performance__allowances`) — a new row every quarter, i.e.
  the exact fragmentation this layer exists to prevent.

So the level split is reported (a drift toward title is worth seeing) and only
UNCLASSIFIED — a table no tier, `dim_hint` or exclusion rule can reach — fails.

Better classification feeds the concept layer directly: re-seeding took
`table_type_id` coverage from 261/375 to 302/375, which raised the
dimensional-breakdown scope count (`load_dictionary.dimensional_scopes`) from 45
to 49 tables via its DECLARED signal and un-stamped 9 more spurious spine
concepts — the F2 mechanism getting stronger as the registry gets better, which
is the intended coupling.
