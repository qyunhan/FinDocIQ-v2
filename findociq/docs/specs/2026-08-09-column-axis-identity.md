# Column-axis identity — `canonical_col_id`, and the rules that keep it honest

**Status:** spec delta v2, 2026-08-09. Implements the unbuilt leg of
`2026-08-05-master-registry-next-steps.md:85` ("Column dispatch per the seed's
`col_axis`") against the slot already declared at `schema/schema_v7.sql:384`.
v2 folds in period-capture rules, row-axis dim symmetry, NULL semantics, and
gate enforcement — all raised while stress-testing against the OCBC Statement
of Changes in Equity and the mixed-vocabulary dashboards (`1_Highlights.xlsx`
in fiscal spans, `2_Breakdown_loans_and_deposits.xlsx` in absolute dates).

**Why now:** the masterlist is about to be populated across every table in each
bank's documents. Two axes authored together cost one pass; a column axis
retrofitted into a hundred already-authored tables costs a rebuild. This has to
be settled first.

---

## 0. Definitions

Four terms used throughout, defined once so re-readers don't have to infer.

- **banner** — a row or column at hierarchy 0 with no value, carrying a label
  that scopes the leaves beneath it. Row banner: `Attributable to equity
  holders of the Bank`. Column banner: `GROUP`.
- **leaf** — a row or column at hierarchy ≥ 1 that carries values (or, for
  abstract header rows, contributes structure). A canonical id is stamped on
  a leaf, never on a banner.
- **span banner** — a banner whose label contributes a segment to the
  `canonical_leaf_id` or `canonical_col_id` of every leaf beneath it. Not
  every banner is a span banner: `GROUP` / `BANK` are entity banners handled
  by `legal_entity`; `Attributable to equity holders of the Bank` IS a span
  banner in the equity table because it prefixes its child columns'
  canonical ids.
- **member** — the canonical id itself (`SG`, `revenue_reserves`,
  `net_interest_income`). Draws from a **dim** (a typed vocabulary: `geo_dim`,
  `segment_dim`, `industry_dim`, `legal_entity_dim`) or from a
  minted-for-this-axis constant.

`::` is the segment separator for composed ids on both axes
(`attributable_to_equity_holders::revenue_reserves`,
`total_income::net_interest_income`). Same syntax across row and column sides.

---

## 1. The two invariants

Everything below is machinery. These two lines are the spec. If the machinery
is ever rewritten, these survive.

> ### **A date is period data, never identity.**

Carried verbatim from `2026-08-05-master-registry-next-steps.md:74`. A period
never contributes a segment to `canonical_leaf_id` and never contributes a
`canonical_col_id`. Two panels over the same rows — `31 Dec 2025` and
`30 Jun 2025` — are ONE leaf and two facts differing by period. The dashboard
shows both dates by pivoting period, never by carrying two leaves.

**The case that tests it — UOB `Performance by Geographical Segment`.** Here
the period is not on the column axis at all; it is printed as a row:

```
UOB_2Q26 · performance_by_geographical_segment_1_..._1h26_2026-06-30
  col 100-106 (h0)   Singapore | Malaysia | Thailand | Indonesia |
                     Greater China | Others | Total          <- geography, hard axis
  col 1-7   (h1)     '$m' under each                         <- unit, not identity
  row 1              '1H26'                                  <- PERIOD, on the row axis
  row 2..n           Net interest income / Non-interest income / ...
```

`1H26` sits exactly where a row-identity segment would sit, and the ancestry
walk will happily prefix every leaf beneath it with `1h26::` unless the
classifier stops it. That is the failure this line exists to prevent, and it
is why the rule is stated on BOTH axes rather than only on columns: a period
is period data wherever it is printed. The correct outcome for that table is
`net_interest_income` × `SG`, with `1H26` landing in `row_period_label` /
`row_period_end_date`.

> ### **Column decisions are STAMPED AT LOAD and FILTERED AT QUERY — never reasoned about in the app.**

Identical in kind to the row-side invariant that `canonical_leaf_id` is copied
verbatim from the masterlist and never invented. A column's axis, its
identity, and its role are decided once, by the loader, and written to
`col_dim`. The serving layer may only compare those stamped values to
constants.

This is already how the two live legs behave, and `_ANCHOR_SQL` is the proof —
both of its column predicates are equality against a stamped column, with the
reasoning in the loader and a comment, not in the SQL:

```sql
-- findociq_app.py:968
AND (c.col_role IS NULL OR c.col_role <> 'derived_skip')
-- findociq_app.py:985
AND COALESCE(c.legal_entity, 'CONSOLIDATED') = 'CONSOLIDATED'
```

That second line is the OCBC consolidation-basis fix, and it was already
heading here. OCBC 2Q26 prints `GROUP` and `BANK` banners over the same balance
sheet, so `Total assets` arrives as both 729,887 and 477,550; the group figure
was surviving only because `col_id` 1 sorts before `col_id` 3. The fix was a
typed attribute stamped at load (`legal_entity`) plus one equality filter —
NOT a banner-text test at query time.

**The anti-pattern, stated so nobody reinvents it.** The next person who hits
an ambiguous column will be tempted to write:

```sql
-- NEVER. Text vocabulary in the query layer.
AND parent.col_leaf_label = 'GROUP'
```

That is banned. It re-derives at serving time a decision the loader already
made, it hardcodes one bank's printed spelling (`The Group` / `GROUP` /
`Group`) into the serving layer, and it silently returns the wrong number the
first time a filing re-words a banner. If an anchor cannot address what it
needs, the missing piece is a stamp, not a `WHERE` clause. Add the stamp.

---

## 2. What exists, measured

| | state |
|---|---|
| `col_dim.canonical_col_id` | declared at `schema/schema_v7.sql:384`, **0 of 1915 populated** |
| `col_dim.col_role` | live — `'derived_skip'` stamped at load, filtered at `findociq_app.py:968` |
| `col_dim.legal_entity` | live — stamped from `legal_entity_map`, filtered at `findociq_app.py:985` |
| `col_dim.col_period` / `period_span` | live — 1,116 of 1,915 columns dated; to be renamed/split to `period_label` + `period_end_date` per §4.3 |
| non-period columns | **799 of 1,915** — 154 at `col_hierarchy` 0 (banners), 645 at 1 (leaves) |
| dimension vocabularies | `geo_dim` 20 · `segment_dim` 6 · `industry_dim` 10 · `legal_entity_dim` 3 |
| row-side dim keys in use | `row_dim.geo_key` 219 · `segment_key` 142 · `industry_key` 376 |
| row-side period fields | **already live** — `row_dim.row_period` / `period_span` / `period_start`, populated on 187 rows. §4.3 is a RENAME-AND-EXTEND of these, not a new declaration. |
| tables needing a column component | **109 of 342 (32%)** carry a value-bearing column that is non-period, non-derived and not the entity axis. The other 233 are addressed by row id alone. |

**Invariant A is already honoured where it has been tested.** UOB's
perf-by-geography — the §1 case — already stamps correctly today:

```
row 1  '1H26'   row_period=2026-06-30  period_span='1H'  period_start=2026-01-01
                canonical_leaf_id = NULL        <- no `1h26::` prefix exists
row 2  'Net interest income'   row_period=NULL
```

The period row contributes no identity. What is missing is §4.4 inheritance:
rows 2..n carry no period at all, which Gate 8 would fail as the spec stands.
That gap, not the invariant, is the work.

42% of columns are not a period axis. The reason this has not yet broken the
dashboard is that every table the highlights view touches is period-columned,
so all 83 current anchors get by on a row-only address. That is a property of
the current row list, not of the corpus.

---

## 3. `col_axis` — the three-way dispatch

Per table type, every column resolves to exactly one of three treatments. The
classifier is deterministic and runs at load, for every document, with no
per-bank or per-document branch.

| axis | test | what is stamped | registry entry |
|---|---|---|---|
| `period` | the column (or its banner) parses to a period token per §4 | `period_label`, `period_end_date`, `period_type`, `period_start_date` | none — **a date is period data, never identity** |
| `derived` | the column restates other columns (see §3.1) | `col_role='derived_skip'` | none — never ingested as a fact |
| `hard` | everything else that carries values: geography, segment, industry, legal entity, equity component, fair-value level, measure | `canonical_col_id` (+ the typed dim key where one exists) | `col_members` on the table type |

A hierarchy-0 span banner is not itself a column member; it contributes its
segment to the members beneath it, exactly as a row banner contributes to
`canonical_leaf_id`.

**Order matters.** `period` is tested first and wins, on both axes. This is
the mechanical expression of invariant A: a column headed `1H26` under a
geography banner is period data, and a ROW labelled `1H26` above a block of
line items is period data. Neither becomes identity.

### 3.1 What counts as `derived`

Stamped `col_role = 'derived_skip'` when the column header matches any of:

- Change-percentage forms: `% chg`, `+/(-)%`, `YoY %`, `QoQ %`,
  `Dec25 vs Jun25 (% chg)`, `Dec25 vs Dec24 (% chg)`
- Explicit signalled deltas: `Δ`, `Change`, `Variance`

Deterministic list, not fuzzy match. Extend by editing this vocabulary, not by
adding heuristics at load.

**What's NOT derived — `Volume` and `Rate` columns in NII bridge tables.**
These are the analytical decomposition of a change into volume-driven and
rate-driven components — facts of interest, not restatements of other
columns. Where an NII bridge table prints `Change | Volume | Rate`, `Change`
is `derived_skip` (it's the sum, recoverable from Volume + Rate), but
`Volume` and `Rate` are ingested as facts under their own `canonical_col_id`.
If a dashboard needs the total, it sums `Volume + Rate`; if it needs the
decomposition, it queries them independently.

### 3.2 Typed attribute vs `canonical_col_id` — the fourth-bucket rule

Two axes today use typed attributes (`legal_entity` on columns, and — in v2
— `row_period_*` on rows)
rather than `canonical_col_id`. State the rule so a third axis doesn't quietly
appear:

**Typed attribute** when the axis has a small closed enumeration that never
appears on the row axis in any bank's filings. `legal_entity` (CONSOLIDATED /
PARENT_COMPANY / BOTH) qualifies — every bank prints it as column banners
only, and the enumeration is 3.

**`canonical_col_id`** when the axis is an open dimension that can appear on
either row or column axis depending on the filing. Geography, segment,
industry, equity component, fair-value level, measure — all belong here.

Applied test: if the same value could plausibly be a `row_dim.<dim>_key` in
one bank's filing and a column banner in another's, it's a `canonical_col_id`.
Never a typed attribute. This is what keeps cross-bank Singapore-to-Singapore
comparison possible when OCBC prints geo on rows and UOB on columns.

---

## 4. Period capture — the full rules

Every period expression the corpus produces normalises to a
`(period_label, period_end_date, period_type, period_start_date)` tuple at
load. The recognizer is deterministic. Display variance never survives the
loader.

**The stamp is what the dashboard renders.** Two dashboards may render the
same underlying date differently — Highlights as `1H25`, Breakdown as
`30 Jun 2025` — but the loader picks one canonical form per fact based on
print convention: durations get fiscal spans, instants get dates. The
dashboard's period selector translates its display columns to the underlying
stamps via `period_end_date` (§4.5).

### 4.1 The canonical vocabulary

The full set of forms `period_label` can take:

- **Quarters:** `1Q<yy>`, `2Q<yy>`, `3Q<yy>`, `4Q<yy>`
- **Halves:** `1H<yy>`, `2H<yy>`
- **Nine-month YTD:** `9M<yy>`
- **Full year:** `FY<yy>`
- **Absolute date:** `DD MMM YYYY` (e.g. `31 Dec 2025`, `30 Jun 2025`)

Nothing else. If a printed form doesn't map cleanly to one of these — a
trailing-twelve-months label, a non-fiscal calendar range — that's a corpus
extension request, not a load-time invention.

### 4.2 Every printed form, its normalized target

| printed form | example | period_label | period_end_date | period_type | period_start_date |
|---|---|---|---|---|---|
| Absolute date, day named | `31 Dec 2025` | `31 Dec 2025` | `2025-12-31` | instant | — |
| Absolute date, day named | `30 Jun 2025` | `30 Jun 2025` | `2025-06-30` | instant | — |
| Abbreviated month-year | `Dec-25`, `Dec 25`, `Dec.25` | `31 Dec 2025` | `2025-12-31` | instant | — |
| Excel-mangled datetime | `2025-12-01 00:00:00` | `31 Dec 2025` | `2025-12-31` | instant | — |
| Quarter span | `1Q25`, `4Q25`, `Q1 2025` | `1Q25` | `2025-03-31` | duration | `2025-01-01` |
| Half-year span | `1H25`, `2H24`, `H1 2025` | `1H25` | `2025-06-30` | duration | `2025-01-01` |
| Nine-month YTD | `9M25` | `9M25` | `2025-09-30` | duration | `2025-01-01` |
| Full year | `FY25`, `Year 2025` | `FY25` | `2025-12-31` | duration | `2025-01-01` |
| Duration title | `For the quarter ended 31 March 2025` | `1Q25` | `2025-03-31` | duration | `2025-01-01` |
| Duration title | `For the half year ended 30 June 2025` | `1H25` | `2025-06-30` | duration | `2025-01-01` |
| Duration title | `For the nine months ended 30 September 2025` | `9M25` | `2025-09-30` | duration | `2025-01-01` |
| Duration title | `For the financial year ended 31 December 2025` | `FY25` | `2025-12-31` | duration | `2025-01-01` |
| Balance reference — opening | `Balance at 1 Jan 2025`, `At 1 January 2025` | `31 Dec 2024` | `2024-12-31` | instant | — |
| Balance reference — closing | `Balance at 30 Jun 2025`, `At 30 June 2025` | `30 Jun 2025` | `2025-06-30` | instant | — |
| Range / delta label | `1H26 vs 1H25`, `Dec25 vs Jun25 (% chg)` | *not stamped — `derived_skip`* | — | — | — |

Four rules embedded in the table worth naming.

**Durations get fiscal spans; instants get dates.** A balance sheet at
`30 Jun 2025` stamps `period_label = '30 Jun 2025'`, not `1H25 end`. An income
statement over 1H25 stamps `period_label = '1H25'`, not `30 Jun 2025 duration`.
This matches print convention on both dashboards — Highlights columns for BS
items still render their underlying date via the `period_end_date` join
(§4.5).

**Abbreviated month-year means end-of-month, not first-of-month.** `Dec-25` is
`31 Dec 2025`. Excel's `2025-12-01 00:00:00` export of the same underlying
label normalises identically — Excel picks first-of-month; the recognizer
overrides. Never trust Excel's day of month, only its month and year.

**Opening balances normalise to prior period-end.** `Balance at 1 Jan 2025`
is the same fact as `Balance at 31 Dec 2024` — one is printed in a 1H25
filing, the other in a FY24 filing. Both stamp to
`period_label = '31 Dec 2024'`, `period_end_date = '2024-12-31'`. Same fact,
one row in the DB — the alternative is silent duplication.

**3M / 6M / 12M forms collapse to Q / H / FY.** If a bank prints `3M25` (YTD
to March), it's the same period as `1Q25` — stamp as `1Q25`. Same for `6M25`
→ `1H25`, `12M25` → `FY25`. Preserve display fidelity for the eight canonical
forms in §4.1; anything else collapses to its nearest canonical form.

### 4.3 Symmetric row/column period fields

Spec v1 had column-side period fields only. v2 adds symmetric row-side
fields; both sides use the same names:

- `col_dim.period_label`, `col_dim.period_end_date`,
  `col_dim.period_type`, `col_dim.period_start_date` — column-side stamp
- `row_dim.row_period_label`, `row_dim.row_period_end_date`,
  `row_dim.row_period_type`, `row_dim.row_period_start_date` — row-side stamp

Populated when a row or column label / banner carries a period token per §4.2.

The existing `col_dim.col_period` (date, 1,116 populated) and
`col_dim.period_span` (span, e.g. `'1H'`) fields are subsumed by this pair —
`period_end_date` takes the date value from the existing `col_period`;
`period_label` is derived from `period_span` + year for durations, or
formatted from the date for instants. Migration is straightforward; the 1,116
dated columns re-stamp deterministically. Rename in place or add-and-drop, at
the schema author's discretion.

### 4.4 Row inheritance

For rows without an explicit period in the label — Changes in Equity's
`Profit for the financial period`, `Fair value gains for the financial
period` — inherit the table's title-derived period.
`row_period_type = 'duration'`, `row_period_end_date` and
`row_period_start_date` from the title (`For the half year ended 30 June
2025` → `2025-06-30` / `2025-01-01`), `row_period_label = '1H25'`.

The distinction between "own period" and "inherited period" is not recorded —
from the query's point of view they behave identically.

### 4.5 Query patterns — same fact, two dashboards

The two live dashboards render differently and both work against the same
stamps:

```
Highlights dashboard (fiscal spans)
  IS filter:  c.period_label = '1H25'
  BS filter:  c.period_end_date = '2025-06-30'
              (Highlights' period selector maps '1H25' -> '2025-06-30' for BS items)
  renders:    "1H25" (one column header, IS and BS items both land under it)

Breakdown dashboard (absolute dates)
  BS filter:  c.period_end_date = '2025-06-30'
              (equivalent to c.period_label = '30 Jun 2025' — either works)
  renders:    "Jun-25" or "30 Jun 2025", per dashboard preference
```

**`period_end_date` is the join key across both dashboards.**
`period_label` is the display form. A dashboard column that shows one label
but pulls facts stamped with a different label (BS items under `1H25` are
stamped `30 Jun 2025`) queries by `period_end_date`, not by `period_label` —
the dashboard's period selector owns the label-to-date mapping.

**Durations addressable by multiple spans query by `period_label`, not
`period_end_date`.** `4Q25` and `2H25` and `FY25` all end on `2025-12-31`, but
describe different flows. A dashboard column labelled `4Q25` filters
`period_label = '4Q25'` — otherwise Q4 income, half-year income, and full-year
income sum together into an obvious nonsense.

### 4.6 Recognizer discipline — the vocabulary IS the test spec

The 15 rows in §4.2 are the complete test spec for the recognizer. Every
printed form must round-trip: printed → parsed → stamped → matches §4.2's
normalized target. Any form outside §4.1's canonical set aborts load with a
diagnostic naming the offending column or row — silent guessing is banned,
same discipline as the continuation-flag fix from `2026-08-05`.

The current implementation stamps `col_dim.col_period` on 1,116 of 1,915
columns (§2). Under the §3 dispatch, every column classified `period` needs
a resolved `period_label` and `period_end_date` — the 799 unstamped
non-period columns fall into `hard` or `derived`, not into "period but
unrecognised." The gap between "stamped today" and "should be stamped" is
what the migration closes; a corpus sample exercising every §4.2 form is
the acceptance test.

Edge cases the recognizer must handle even though they haven't all been
printed yet in the current corpus:

- A footnote-qualified period (`FY25 (restated)`, `1H26 (1)`) — footnote is
  stripped from the label; the underlying token normalises to §4.1.
- A caption that redundantly names both the duration and the span
  (`For the half year ended 30 June 2025 (1H25)`) — the recognizer extracts
  from either; both must give the same normalised target.
- A period token embedded in a wider title (`Selected data for 1H25`) — the
  token is extracted; surrounding text is discarded.
- A range label that isn't a `derived_skip` column but a real period slice
  (`For the six months ended 30 September 2025` — a non-standard reporting
  period). Not currently in the corpus; if it appears, aborts load with a
  diagnostic so a §4.1 extension can be decided rather than guessed at.

Every one of these either normalises deterministically to a §4.1 canonical
form OR aborts. Never silently drop the row, never invent a form.

---

## 5. Dimension vocabulary — reuse, never mint in parallel

**Rule: where an axis already has a dim table, both `row_dim.<dim>_key` AND
`canonical_col_id` resolve INTO that vocabulary. Fresh ids may only be minted
for axes that have none.**

- reuse: `geo_dim`, `segment_dim`, `industry_dim`, `legal_entity_dim`
- mint: equity component, fair-value level, measure — no dim table exists

The evidence that this is load-bearing, from the current DB. The same
dimension already appears on both axes, in different banks' filings:

```
OCBC geographical_segments   (ROW axis)        UOB perf-by-geography   (COLUMN axis)
  'Singapore'      -> geo_key 'SG'               'Singapore'      -> canonical_col_id 'SG'
  'Malaysia'       -> geo_key 'MY'               'Malaysia'       -> canonical_col_id 'MY'
  'Greater China'  -> 'GREATER_CHINA'            'Greater China'  -> 'GREATER_CHINA'
  'Rest of the World' -> 'ROW'                   'Others'         -> 'OTH'
```

Same vocabulary, either axis. The anchor row records the per-bank choice
(§7); the dim vocabulary does not fork.

And the reuse is nearly free today. `geo_map` (24 rows) already resolves six
of UOB's seven geography columns by normalised label, without a single new
entry:

```
'singapore' -> SG    'malaysia' -> MY    'thailand' -> TH
'indonesia' -> ID    'greater china' -> GREATER_CHINA    'others' -> OTH
```

The only gap is the printed `Total` column, which is the base slice —
`GLOBAL`, already a `geo_dim` member, just not yet a `geo_map` label. One row
of curation against a whole parallel vocabulary. Extending a dim table or its
map is the correct move; minting a second id space is not.

**Corollary — dim members never concatenate into `canonical_leaf_id`.** When a
row carries `row_dim.geo_key = 'SG'`, the `canonical_leaf_id` is the concept
without the geo segment (`total_income`, not `total_income::sg`). Parent-chain
composition still applies for measure banners and other span banners; dim
members are typed attributes, addressed via the anchor's `row_dim_key` /
`canonical_col_id` fields, not through the id. This keeps a leaf's identity
bank-agnostic; per-bank axis choice lives in the anchor.

---

## 6. Masterlist shape — two axes, additive

The masterlist gains a column block per table type, alongside the row block.
It does **not** gain row × column pairs.

```
rows      35 declarations
columns    8 declarations
          --
          43   NOT 280
```

`col_members` per (bank, table_type_id): `col_ordinal`, `canonical_col_id`,
`label`, `full_path`, and `dim_key` where the axis has a dim table. Same
provenance rule as the row side — the id written to `col_dim` is copied
verbatim from the masterlist, never computed at load.

Where a dim has a `_map` (label → key), `canonical_col_id` and `dim_key`
resolve to the same target. Gate 4 checks consistency.

The row block keeps its existing role as the coverage denominator for
`locate_tables` (`MIN_MATCH_FRACTION`, `resolve_canonical_leaf.py:514`).
Column members do **not** enter that score: a table is identified by its
printed line items, and adding a second denominator would make a table harder
to find on any vintage that reprints fewer columns.

---

## 7. Anchor address — a tuple, back-compatible

The dashboard address becomes `bank > table > line item > (row-dim key or
column)`:

```
concept,row_order,section,bank,table_type_id,canonical_leaf_id,
canonical_col_id,row_dim_key,sign
```

- `canonical_col_id` blank means **the period axis** on columns — address the
  row, pivot the period, which is what every highlights anchor does today.
- `row_dim_key` blank means **no row-dim filter** — the row_dim entry doesn't
  slice on a dim member. `row_dim_key` is the typed key
  (`SG`, `TRANSPORT`, `WEALTH_MANAGEMENT`, etc.) — the anchor doesn't need to
  say which dim, because that's fixed by the `table_type_id`.
- All 83 existing addresses stay valid and unedited;
  `load_dashboard_anchors` (`findociq_app.py:747`) reads the new columns the
  same way it reads `section`, with absence meaning "not declared".

`_ANCHOR_SQL` gains two predicates in the same shape as the two already there:

```sql
AND (:canonical_col_id IS NULL OR c.canonical_col_id = :canonical_col_id)
AND (:row_dim_key      IS NULL OR r.<the_dim>_key       = :row_dim_key)
```

Where `<the_dim>_key` is chosen from the seed's declared row dim for that
table type — one of `geo_key`, `segment_key`, `industry_key`. One dim per
table.

**NULL semantics for unresolved stamps.** When a filing prints a column or
row the masterlist doesn't cover, the stamp is NULL. To keep unresolved
stamps out of anchor query results, `col_role` gets a fourth value
`'unresolved'` (in addition to `'derived_skip'`) written whenever
`canonical_col_id` fails to resolve; row side gets `row_status = 'unresolved'`
the same way.

**`col_role` becomes an enum, so its serving predicate becomes an ALLOWLIST.**
The live predicate at `findociq_app.py:968` is a single-value denylist:

```sql
AND (c.col_role IS NULL OR c.col_role <> 'derived_skip')     -- WRONG under v2
```

`'unresolved'` satisfies `<> 'derived_skip'`, so an unresolved column passes
it. Worse, the guard below only fires when `:canonical_col_id` is non-NULL,
which is NONE of the 83 current anchors — so unresolved columns would serve
silently on exactly the period-axis addresses that are the whole dashboard
today. Every future `col_role` value would inherit the same hole. The predicate
must name what is ALLOWED, not what is excluded:

```sql
AND c.col_role IS NULL                       -- only unroled columns carry facts
AND (:canonical_col_id IS NULL OR c.canonical_col_id = :canonical_col_id)
```

Adding a `col_role` value must never widen what serves. This is the same
failure shape as the anchor-address typo: a predicate that is silent rather
than wrong is the expensive kind.

Coverage gaps surface as visible blanks on the dashboard, per the
long-standing rule. Silent aggregation of unresolved rows is banned.

---

## 8. Worked examples

**UOB `Performance by Geographical Segment` — hard axis on cols, period on rows**

```
address   UOB / FS_PERF_BY_GEOGRAPHY / net_interest_income
                                     / canonical_col_id=SG / row_dim_key=NULL
stamped   col 100 canonical_col_id='SG'      geo_key='SG'
          col 106 canonical_col_id='GLOBAL'  (the printed 'Total')
          row 1  '1H26' -> row_period_label='1H26', row_period_end_date=2026-06-30,
                           row_period_start_date=2026-01-01, row_period_type=duration;
                           contributes NO segment
```

**OCBC `Geographical Segments` — hard axis on rows, period on cols**

Same conceptual cell, different tuple. Same `geo_dim` vocabulary.

```
address   OCBC / FS_SEGMENT_TOTAL_INCOME_BY_GEO / total_income
                                                 / canonical_col_id=NULL / row_dim_key=SG
stamped   row 1..6 row_dim.geo_key = 'SG'|'MY'|'ID'|'GREATER_CHINA'|...
          col 1    period_label='1H25', period_end_date=2025-06-30,
                   period_type=duration, period_start_date=2025-01-01
          row 0    banner 'Total income' — contributes canonical_leaf_id='total_income'
```

The row's `canonical_leaf_id` is `total_income`, NOT `total_income::sg`. Geo
is the typed key, not a segment of the id. Rendering-side per-bank pathing
lives in the anchor CSV.

**OCBC `Statement of Changes in Equity` — hard axis on cols, mixed-period rows**

```
address   OCBC / FS_EQUITY_CHANGES_GROUP / profit_for_the_financial_period
                                         / canonical_col_id=attributable_to_equity_holders::revenue_reserves
stamped   col 100 (h0) 'Attributable to equity holders of the Bank' -> span banner
          col 4        canonical_col_id='attributable_to_equity_holders::revenue_reserves'
          col 8        canonical_col_id='total_equity'
          row 1 'Balance at 1 Jan 2025':
                row_period_label='31 Dec 2024', row_period_end_date=2024-12-31, instant
                (opening balance normalised to prior period-end)
                canonical_leaf_id='balance_at_start_of_period'
          row 2 'Profit for the financial period':
                row_period_label='1H25', row_period_end_date=2025-06-30,
                row_period_start_date=2025-01-01, duration
                (inherited from table title)
                canonical_leaf_id='profit_for_the_financial_period'
          row N 'Balance at 30 Jun 2025':
                row_period_label='30 Jun 2025', row_period_end_date=2025-06-30, instant
                canonical_leaf_id='balance_at_end_of_period'
```

The same masterlist entries (`balance_at_start_of_period`,
`balance_at_end_of_period`, `profit_for_the_financial_period`) work across
every quarter's filing; the period comes from the stamp, not the leaf name.

**OCBC balance sheet — no hard axis, entity handled by the live leg**

```
address   OCBC / FS_BALANCE_CONSOLIDATED / total_assets
                                        / canonical_col_id=NULL / row_dim_key=NULL
stamped   legal_entity CONSOLIDATED | PARENT_COMPANY, filtered at findociq_app.py:985
```

Entity is a typed attribute per §3.2, not a `canonical_col_id`. Do not stamp
it twice.

---

## 9. Out of scope for v1

Layered / mixed axes — a table with period on rows AND hard-axis columns AND
period-labeled sub-columns under a hard-axis banner (segment × geography ×
period breakdowns in some annual-report notes) — deferred. The KPH corpus
does not produce them; the Breakdown corpus does not produce them. Revisit
when a dashboard specifically requires this shape. The current
`row_period_*` / `period_*` model can be extended, but the extension isn't authored yet.

Similarly deferred: multi-dim row keys (a row that carries both `geo_key`
AND `segment_key`). Not seen in the current corpus. If it appears, the anchor
would gain multiple typed key fields — mechanical, but not needed today.

---

## 10. Tried and discarded

- **Row × column cross product in the masterlist.** Multiplies authoring by
  the column count and re-declares the row list once per column; a
  reprinted-with-fewer-columns vintage then fails to match rows it does
  contain.
- **Column identity derived at query time from banner text.** The
  `parent.col_leaf_label = 'GROUP'` shape in §1. Banned: it moves a load-time
  decision into the serving layer and hardcodes one bank's spelling.
- **A separate column dimension vocabulary.** Breaks cross-bank comparison
  the moment a dimension appears on the row axis for one bank and the column
  axis for another — which it already does, today, for geography (§5).
- **Letting the period ride along as an identity segment when it is printed
  as a row** (UOB geography, row 1 `1H26`). Produces
  `1h26::net_interest_income`, a leaf that changes every vintage and matches
  no masterlist entry.
- **Concatenating dim keys into `canonical_leaf_id`** (e.g.
  `total_income::sg` for OCBC's geography-on-rows). Encodes geography twice
  — once in leaf_id, once in the dim key — and makes cross-bank comparison
  depend on both staying in sync forever. Kept out per §5's corollary.
- **`period_type` transitions triggering caption-split.** Considered for
  OCBC's segment table (three duration measure blocks + one instant Total
  Assets block). Discarded: measure banners are just row parents, and cells
  are naturally sparse where measure and period_type don't compose. One
  table, sparse cells, correct addressing.
- **Stamping a fiscal `period_span` on every instant date.** Discarded in v1;
  now moot under v2's model. Instants stamp `period_label` as a date form
  (`31 Dec 2025`), not a fiscal span. Dashboards that render in fiscal frames
  (Highlights) join by `period_end_date`.

---

## 11. Gates

Before any hard-axis table's members are emitted:

1. `canonical_col_id` is NULL on every column where `period_end_date IS NOT NULL`
   — invariant A, mechanically checkable. (Names the field that SURVIVES §4.3's
   migration; the pre-migration `col_period` is the same test on the old name,
   and the gate must be moved with the rename or it silently stops firing.)
2. No `canonical_col_id` on a `col_role='derived_skip'` column.
3. Every `canonical_col_id` on an axis with a dim table resolves to a member
   of that dim table.
4. Where both `canonical_col_id` and `dim_key` are declared in the masterlist,
   they resolve to the same dim member.
5. Every `canonical_col_id` written to `col_dim` appears verbatim in the
   masterlist's `col_members` for that (bank, table_type_id).
6. No `col_leaf_label` / `col_parent` text comparison anywhere under `app/`.
7. `canonical_leaf_id` never contains a segment that matches a
   `<dim>_dim` member (`::sg`, `::consumer_banking`, `::housing_loans`) — dim
   membership is a typed key, not a leaf segment.
8. Every column classified `period` has a non-NULL `period_end_date` AND a
   non-NULL `period_label`; every row with a period token in its label has
   non-NULL `row_period_end_date` AND `row_period_label`. No cell_fact row
   exists without a resolved `period_end_date` on both axes.

**Enforcement.** Gates 1–5 and 7–8 fail the load. `load_v7` aborts, no partial
state is committed, `ingest_status` records the failing gate and the offending
`(table_key, col_key or row_key)`. Same discipline as the continuation-flag
fix from `2026-08-05`: don't let ambiguous claims propagate; refuse at the
boundary where the information is still authoritative.

Gate 6 is a CI grep — it rots quietly otherwise. It belongs in the pre-commit
hook and in the docs/specs review checklist.