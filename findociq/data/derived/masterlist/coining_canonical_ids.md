# Coining canonical_leaf_id and canonical_col_id

**Audience:** For authoring masterlists. Assumes you have read the spec (`2026-08-09-column-axis-identity.md`, "the spec" hereafter) and can find `<bank>_masterlist.csv` / `<bank>_masterlist_cols.csv` in `data/derived/masterlist`.

**What this doc is:** the rulebook you consult every time you sit down to name a new leaf or column. Section 1 states the invariants. Section 2 walks the row-id rules. Section 3 walks the col-id rules. Section 4 is the per-bank cheat sheet — where each bank's conventions differ, and every non-obvious decision that has already been made and should not be re-litigated.

**What this doc is not:** a spec rewrite. When this doc and the spec conflict, the spec wins. When you disagree with either, raise it before authoring — do not silently invent.

---

## 1. Two invariants that survive every rewrite

**(A) A date is period data, never identity.**
Period never contributes to `canonical_leaf_id` or `canonical_col_id`, either axis. If your id contains `1h26`, `fy25`, `at_31_december_2025`, `q4`, or any date fragment, you have a bug. The one narrow exception is opening/closing balances in movement statements (see Rule 3 below) — the date there is genuinely the identity, not a period.

**(B) Column decisions are stamped at load, filtered at query — never reasoned in the app.**
Ids and col_role live on the col_dim row from the moment the loader writes it. The dashboard reads them with `AND c.col_role IS NULL` and never inspects labels. This is why the col vocabulary matters so much: a missed Note/Ref/derived label leaks into anchor selection and the app cannot recover.

Everything below is downstream of these two.

---

## 2. `canonical_leaf_id` — the row identity

### 2.1 Definition and scope

A `::`-joined normalized hierarchy path derived from the printed label and its ancestor labels. Snake_case, casefold, ASCII-safe.

```
non_performing_loans::manufacturing
total_income::of_which::net_interest_income
attributable_to::shareholders
balance_at_1_january
commercial_book_total_income
```

The id is stable across periods and across documents from the same bank. Two rows from different quarters with the same id are the same concept.

**Scope: table-scoped, not global.** The id is not a globally-unique locator. Full cell identity is the tuple:

```
(bank, table_type_id, canonical_leaf_id, canonical_col_id, period_end_date)
```

`table_type_id` (`FS_BALANCE_STATUTORY`) already carries "this is a balance sheet row". So `::` inside the leaf id composes hierarchy **within the table**, not global context that another field of the tuple already supplies. This is what determines which labels compose and which drop:

- **Subtotals and of-which memos compose** (`total_income::net_interest_income`) — they are real rows with values in a genuine parent-child relationship inside the table.
- **Section banners drop** (`ASSETS`, `LIABILITIES`, `EQUITY`) — `FS_BALANCE_STATUTORY` already tells the query which section family the row is in; prefixing `assets::` inside the id repeats what the type_id says.

Banner labels are also less stable across quarters (`ASSETS` vs `Assets` vs `Total Assets`) than subtotal labels, which are pinned to a value. If you want the full as-printed hierarchy including the banner, read `full_path`, not `canonical_leaf_id`. Rule of thumb: `canonical_leaf_id` is normalized identity for the query layer; `full_path` is the audit view of what was printed on the page.

### 2.2 Normalization pipeline

Applied to every ancestor label AND the leaf label before joining:

1. **Casefold.** `Net Interest Income` → `net interest income`.
2. **Strip footnote markers.** `Return on equity4,5` → `return on equity`. Both digit suffixes and superscript/comma-separated groups (`2,3`) go. Applied per Rule of trailing footnote digits (spec §3, resolver rule).
3. **Strip unit suffixes.** `Total assets (S$m)` → `total assets`. Same for `(bps)`, `(%)`, `(x)`.
4. **Strip `Less:` / `Add:` prefixes.** `Less: Impairment` → `impairment`. Sign is a masterlist property, not an id property.
5. **Section-headers dropped from the id.** Per §2.1, ALL-CAPS structural banners (`ASSETS`, `LIABILITIES`, `EQUITY`, `OFF-BALANCE SHEET ITEMS`, `INCOME`) live in `full_path` prefix only. `assets::cash_and_cash_equivalents` in `full_path` collapses to `cash_and_cash_equivalents` in `canonical_leaf_id`.
6. **Spaces → underscores; punctuation dropped.** Ampersands become `_and_`. Slashes become `_`.

### 2.3 Composition rules (resolver rules 1-3, plus 3b)

These are the rules that decide what goes into the `::` path vs what collapses.

**Rule 1 — Subtotal collapse.** A subtotal keys with a single-segment id; its children key off it as parent.

```
Total income                       → commercial_book_total_income        (subtotal, one segment)
  Net interest income              → commercial_book_total_income::net_interest_income
  Non-interest income              → commercial_book_total_income::non_interest_income
```

**Rule 2 — "Of which:" memo-parent.** An "Of which: X" line attaches under the nearest preceding non-memo row.

```
Total income                       → total_income
Net interest income                → total_income::net_interest_income
  Of which: SGD                    → total_income::net_interest_income::of_which::sgd
```

The memo does not become the new parent for what follows — the next non-memo sibling keys back to the original parent.

**Rule 3 — Opening/closing balance dates preserved.** In movement statements (SCE, Level 3 roll-forwards, allowance roll-forwards), the opening and closing balance rows keep the date-word as identity because the date IS the thing being labelled. `balance_at_1_january` and `balance_at_31_december` stay as two distinct leaves. The year is stripped; the day+month is kept.

**Rule 3b — Period-banner ancestor labels dropped from ids.** Ancestor labels like `At <date>`, `Half year ended <date>` are period banners when they head a valueless span. They must not leak into descendant ids as `at_31_december_2025::...` prefixes. The recognizer lives in `masterlist_derive.py:125` (`is_period_banner_label`), applied on the valueless branch only. Same wording carries opposite meaning depending on row state: valueless heading → PERIOD_BANNER (Rule 3b, drop), valued opening balance → DATA (Rule 3, keep).

Rules 3 and 3b are the disambiguated version of an earlier "does ancestor contain a year" heuristic that was wrong in both directions.

### 2.4 What NEVER goes into a `canonical_leaf_id`

- **Period tokens** (invariant A). Not `1h26`, not `at_31_december_2025`, not `fy25`. Rule 3 openings/closings are the ONLY carve-out.
- **Section banners** as separate segments. `ASSETS`, `LIABILITIES`, `EQUITY` live in `full_path` not the id (§2.1).
- **Dim members from a hard-axis column vocabulary** — with one exception below (§2.5).
- **Sign words** — `Less:`, `Add:` are stripped.
- **Footnote refs** — digits and comma-groups stripped from the id (they stay verbatim in `label` and `full_path`).

### 2.5 Row-side `::` composition (spec §5.1) — the deferred-dim shortcut

**When authoring hits a table with many breakdown rows** (loans by industry, deposits by currency, NPA by geography), you have two options:

1. **Hard-axis col vocabulary.** Stamp `industry_dim` / `geo_dim` / `currency_dim` cols and let the compiled_db aggregate cross-bank via dim_key.
2. **Row-side `::` composition.** Compose the dim member into the leaf id: `non_performing_loans::manufacturing`, `customer_deposits::sgd`, `gross_customer_loans::greater_china`.

Yunhan's preference (documented as spec §5.1 addition 2026-08-10) is row-side composition where cross-bank aggregation isn't immediately valuable. It saves authoring overhead. The retrofit path is a load-time script that stamps `<dim>_key` on `canonical_leaf_id LIKE '%::<value>'` when cross-bank aggregation becomes valuable — no re-authoring needed.

**Storage shape example** — DBS Manufacturing NPA at 31 Dec 2025 = 255:
```
bank             = 'DBS'
canonical_leaf_id = 'non_performing_loans::manufacturing'
canonical_col_id  = 'npa'
period_end_date   = '2025-12-31'
period_type       = 'instant'
value             = 255
```
Cell address = 4-tuple `(bank, canonical_leaf_id, canonical_col_id, period_end_date)`.

### 2.6 Cross-bank id reuse — follow labels, not concepts

Two banks that print the same label get the same id. Two banks that print different labels for the same underlying concept get different ids, and the dashboard anchor CSV wires them together via composite anchors.

- `total_assets` → reused across all three banks (all print "Total assets").
- `customer_deposits` (DBS/OCBC) vs `deposits_of_non_bank_customers` (UOB) → stay distinct. The Highlights anchor CSV names each bank's id under one concept.

Do not force id alignment across banks. Alignment happens at the anchor CSV layer.

### 2.7 Anti-patterns that have already burned time

- **Inventing structural splits.** `FS_PER_SHARE` outside DBS was invented twice (once for UOB, once for OCBC) and had to be reverted both times. UOB and OCBC print per-share inline in `FS_INCOME_CONSOLIDATED` / `FS_RATIOS_KEY`. Author what is printed. If it is printed inline, keep it inline.
- **Truncated concept-digits.** `tier`, `cet` should be `tier_1`, `cet_1`. The `_1` is not a footnote marker.
- **Silent parent invention.** If Gemini stamped a row as child of the wrong parent, fix the ancestry, don't paper over it in the leaf id.
- **`SEG_TOTAL` as no-match sink.** DBS NII 1Q23 became −113 because rows silently fell through to `SEG_TOTAL`. Retired as explicit axis value. Reported-total leaves anchor with `segment_key=NULL`.

---

## 3. `canonical_col_id` — the column identity

### 3.1 When you need a canonical_col_id at all

Spec §3 three-way dispatch, tested in order:

**period** → `period_label`, `period_end_date`, `period_type`, `period_start_date` populated; no `canonical_col_id`. This is the majority of columns.

**derived** → `col_role='derived_skip'`, no `canonical_col_id`, dashboard allowlist filters it out. Deterministic vocabulary from spec §3.1: `%chg`, `+/(-)%`, `YoY%`, `QoQ%`, `Δ`, `Change`, `Variance`, any col under a range banner. Volume/Rate deltas also derived_skip (between-period decomposition, not measurements).

**hard** → `canonical_col_id` from registry, `col_role=NULL`, dashboard reads it. This is where the naming work happens.

**Also non-measurement:** `note_reference` — the OCBC Note/Notes/Ref columns get `col_role='note_reference'` at load. Add plurals to the recognizer (the OCBC Note bug was plural-missing on a similar recognizer earlier).

### 3.2 Typed attribute vs canonical_col_id (spec §3.2)

Two shapes of "hard" that need different treatment:

- **Typed attribute** — for closed enumerations that never appear on rows. `legal_entity` (3 values: CONSOLIDATED, PARENT_COMPANY / BANK_ENTITY, plain) is the canonical case. Stamped as an attribute column on the fact row.
- **canonical_col_id** — for open dims that could appear on either axis. `geo`, `segment`, `industry`, `measure`, `level`, `equity_component`. These get a col_id entry in the masterlist_cols file.

Rule of thumb: if the dim members might show up as row labels in a different table, it's canonical_col_id. If they only ever appear as column headers everywhere, it's an attribute.

### 3.3 Naming — hierarchical path snake_case (matches leaf convention)

**canonical_col_id values follow the same snake_case path convention as canonical_leaf_id.**

Not `SEG_RETAIL` (that's the `dim_key`, a separate compiled_db normalization field). The `canonical_col_id` is the printed label, normalized the same way a row would be:

```
Markets Trading                    → markets_trading
Singapore                          → singapore
Greater China ex Hong Kong         → greater_china_ex_hong_kong
Level 3                            → level_3
NPA ($m)                           → npa
Total                              → global   (for geography — total-across-geo is GLOBAL)
```

**dim_key** (`SEG_MARKETS`, `SG`, `LEVEL_3`, `NPA`, `GLOBAL`) is the cross-bank alignment key, stamped separately in the masterlist_cols file. This split was clarified 2026-08-11 during the breakdown anchors delivery — the anchor CSVs carry `canonical_col_id` in path form; `dim_key` lives on the col_dim row for the compiled_db layer.

### 3.4 Dim vocabularies currently registered

Keep these lists in sync with the masterlist_cols files. Additions get proposed here, not silently in a curate script.

| Dim | Members | Notes |
|---|---|---|
| `geo_dim` (20) | SG, MY, TH, ID, GREATER_CHINA, HK, RGC, GC_EX_HK, OTHER_ASIA_PACIFIC, ROW, OTH, GLOBAL, … | GC_EX_HK is DBS-specific (DBS splits HK from RGC). GLOBAL = total-across-geo. |
| `segment_dim` (6+) | SEG_RETAIL, SEG_WHOLESALE, SEG_MARKETS, SEG_INSURANCE, SEG_OTHER, SEG_TOTAL | OCBC adds GLOBAL_CONSUMER_PRIVATE_BANKING, GLOBAL_WHOLESALE_BANKING, GLOBAL_MARKETS, INSURANCE — bank-specific vocabulary that hasn't collapsed cross-bank yet. |
| `industry_dim` (10) | Manufacturing, Building_construction, Housing_loans, etc. | Cross-bank labels align reasonably; watch for OCBC "Agriculture Mining and Quarrying" going into "Others". |
| `legal_entity_dim` (3) | CONSOLIDATED, PARENT_COMPANY, BANK_ENTITY | Typed attribute, not canonical_col_id. Naming split: DBS uses PARENT_COMPANY, OCBC uses BANK_ENTITY. Unification open. |
| `level_dim` (4) | LEVEL_1, LEVEL_2, LEVEL_3, LEVEL_TOTAL | Standard fair-value hierarchy. |
| `measure_dim` (open) | NPA, NPL_PCT, SP, NPA_TOTAL, SUBSTANDARD, DOUBTFUL, LOSS, NPL_TOTAL, NPL_AMT, LOAN, ALLOWANCE, PCT | Multi-bank NPA table structures diverge — DBS has 3 measures, OCBC has 6 on BY_GEOGRAPHY and 2 on BY_INDUSTRY. |
| `equity_component_dim` (open) | Bank-specific. OCBC GROUP has 8, OCBC BANK has 5, DBS GROUP has 7, DBS COMPANY has 5. | Different bank structures don't collapse. See §4 per-bank notes. |
| `currency_dim` (open) | SGD, USD, MYR, THB, IDR, HKD, CNY, TWD, … | Deposit/loan currency splits. Composite "Asia" per bank varies. |

### 3.5 Anti-patterns for cols

- **Concatenating dim members into a `canonical_leaf_id`** when the table has a hard-axis col. `sg_net_interest_income` is wrong — it should be `net_interest_income` in the leaf and `singapore` in the col. (§5.1 permits row-side composition ONLY where cross-bank dim aggregation is deferred; do not mix.)
- **Splitting `SEG_MARKETS` off as its own type_id.** DBS "Markets Trading" is a 4th flat col alongside the 3 Commercial Book sub-cols, not a separate table. `SEG_COMMERCIAL_BOOK` composition happens at the anchor layer.
- **Same-structure legal entity duplication.** `FS_BALANCE_STATUTORY_GROUP` + `FS_BALANCE_STATUTORY_BANK` as two type_ids violates the "same structure different legal entity → ONE type_id, legal_entity col disambiguates" discipline. See DBS FS_BALANCE_CONSOLIDATED (2 legal_entity col_members).
- **Different-structure legal entity collapse.** `FS_EQUITY_CHANGES_GROUP` and `FS_EQUITY_CHANGES_COMPANY` genuinely have different equity_component col sets (GROUP has NCI, COMPANY doesn't). Keep as two type_ids. The "same structure" test is what actually differs, not the label similarity.
- **Missing plurals in deterministic recognizers.** Merge continuation bug stamped `note_` singular and missed `notes_`. Every recognizer covers both.

---

## 4. Per-bank cheat sheet

Everything in this section is a **decision that has been made** and should be followed, not re-litigated. Sources are the masterlist files at `/mnt/user-data/outputs/` and the concept-mapping decisions logged in the FinDocIQ project memory.

### 4.1 DBS

**Table_type_id conventions.**
- Audited balance sheet: `FS_BALANCE_STATUTORY` (matches UOB, differs from OCBC's `FS_BALANCE_CONSOLIDATED`).
- Per-share: `FS_PER_SHARE` **exists** as a standalone type_id on DBS (Overview page). Do not create this type_id for the other two banks.
- Equity changes: two type_ids — `FS_EQUITY_CHANGES_GROUP` (7 equity_component cols including NCI) and `FS_EQUITY_CHANGES_COMPANY` (5 cols, no NCI). Kept split because column structures genuinely differ.
- NPA family: `FS_NPA_COVERAGE` (3 measure cols NPA/NPL_PCT/SP), `FS_NPA_BY_INDUSTRY` (2 measures NPA + SP, no per-industry ratio), `FS_NPA_BY_COLLATERAL`, `FS_NPA_BY_LOAN_GRADING`, `FS_NPA_BY_PERIOD_OVERDUE` (1 measure NPA only).

**Legal entity coverage.**
- `FS_BALANCE_CONSOLIDATED` prints Group + Company side-by-side → 2 legal_entity col_members (CONSOLIDATED, PARENT_COMPANY).
- `FS_INCOME_CONSOLIDATED`, `FS_CASHFLOW`, `FS_COMPREHENSIVE_INCOME` are Group-only. No legal_entity cols.

**Geography vocabulary.**
- DBS splits Hong Kong from Rest of Greater China → `GC_EX_HK` and `HK` are distinct dim members. `GREATER_CHINA` (single member) is the UOB/OCBC form.

**Segment vocabulary.**
- 5 flat segment cols: Consumer Banking/WM (`SEG_RETAIL`), Institutional Banking (`SEG_WHOLESALE`), Others (`SEG_OTHER`), Markets Trading (`SEG_MARKETS`), Total (`SEG_TOTAL`). The 3 Commercial Book sub-cols are Consumer + Institutional + Others; Commercial Book itself is a composite via anchor, not a col.

**Curated state.**
- `DBS_masterlist_curated.csv` (389 rows, 31 type_ids, sections 1-22 non-Overview)
- `DBS_masterlist.csv` (46 rows, Overview p4-8 — legacy 9-col shape missing `source_family`)
- `DBS_masterlist_cols_curated.csv` (50 rows, 13 hard-axis type_ids)

### 4.2 OCBC

**Table_type_id conventions.**
- Audited balance sheet: `FS_BALANCE_CONSOLIDATED` (differs from DBS/UOB `FS_BALANCE_STATUTORY`). Intentional — do not rename.
- Per-share: **no** `FS_PER_SHARE`. EPS lives inline in `FS_INCOME_CONSOLIDATED` (ordinals 27-29) and `FS_RATIOS_KEY`.
- Equity changes: `FS_EQUITY_CHANGES_GROUP` (8 equity_component cols) and `FS_EQUITY_CHANGES_COMPANY` (5 cols). Kept split. Note earlier draft called the second `FS_EQUITY_CHANGES_BANK` — renamed to `_COMPANY` for cross-bank alignment.
- Note-suffixed types: `FS_NII_NOTE`, `FS_FEE_INCOME_NOTE`, etc. exist alongside media-side `FS_NII_DETAIL` because loader key is (bank, table_type_id, canonical_leaf_id) — collision would happen if type_ids repeated.
- Media-vs-consolidated PBT split: `FS_PBT_BY_SEGMENT_SELECTED` (6-row Media PBT view) vs `FS_PERF_BY_SEGMENT` (11-row Condensed full P&L). Renamed to disambiguate.

**Legal entity coverage.**
- `FS_BALANCE_CONSOLIDATED` prints Group + Bank → 2 legal_entity col_members (CONSOLIDATED, BANK_ENTITY).
- Note the naming split: OCBC uses BANK_ENTITY, DBS uses PARENT_COMPANY. Unification decision still open — either both use one label or a cross-bank alias table maps them.

**Segment vocabulary — bank-specific additions.**
- `GLOBAL_CONSUMER_PRIVATE_BANKING`, `GLOBAL_WHOLESALE_BANKING`, `GLOBAL_MARKETS`, `INSURANCE` are OCBC's printed segment names. Do not force-map to DBS/UOB SEG_RETAIL/SEG_WHOLESALE/SEG_MARKETS at the id layer; the anchor CSV wires the cross-bank comparison.

**NPA vocabulary — bank-specific.**
- `FS_NPA_BY_GEOGRAPHY` carries 6 measure cols: NPA_TOTAL, SUBSTANDARD, DOUBTFUL, LOSS, NPL_TOTAL, NPL_PCT. This is broader than DBS's 3-measure grid.
- `FS_NPA_BY_INDUSTRY` — 2 measures: NPL_AMT + NPL_PCT.
- `FS_NPA_RESTRUCTURED` — 2 measures: LOAN_AMT + ALLOWANCE_AMT (across 4 loan grades).

**Equity component vocabulary — OCBC-specific additions.**
- `SHARE_CAPITAL_AND_OTHER_EQUITY`, `CAPITAL_RESERVES`, `FAIR_VALUE_RESERVES`, `OTHER_EQUITY_INSTRUMENTS_ISSUED_BY_SUBSIDIARY`. Not a cross-bank vocabulary — OCBC's equity structure differs from DBS's.

**Curated state.**
- `OCBC_masterlist.csv` (114 rows Overview + prior Condensed; **BS not authored properly** per Yunhan — flagged for re-author).
- `OCBC_masterlist_media_detail.csv` (169 rows, 13 tables, sections 2-10 Media Release).
- `OCBC_masterlist_condensed_fs.csv` (305 rows, 20 tables) — should REPLACE the 72 Condensed rows in the legacy `OCBC_masterlist.csv`.
- `OCBC_masterlist_cols_curated.csv` (63 rows).
- Row masterlist renames pending (see project state): `FS_EQUITY_CHANGES_BANK` → `_COMPANY`, `FS_BS_BY_SEGMENT` → `FS_BALANCE_BY_SEGMENT`, `FS_PERF_BY_SEGMENT_CONSOL` → `FS_PERF_BY_SEGMENT`, `FS_PERF_BY_GEOGRAPHY_CONSOL` → `FS_PERF_BY_GEOGRAPHY`.

**Known geometry defect (not a naming issue) — do not try to fix in the id.**
15 rows on OCBC SCE captured as CHILDREN of the opening-balance row rather than SIBLINGS under the span banner. Yields `at_1_january::profit_for_the_year` shapes. Fix is at the parser layer, not the masterlist layer. Backlog item.

### 4.3 UOB

**Table_type_id conventions.**
- Audited balance sheet: `FS_BALANCE_STATUTORY` (matches DBS).
- Per-share: **no** `FS_PER_SHARE`. Merged into `FS_INCOME_CONSOLIDATED` and `FS_RATIOS_KEY` as printed.
- Highlights + geography table already authored — `FS_INCOME_SELECTED` (12), `FS_BALANCE_SELECTED` (4), `FS_RATIOS_KEY` (27), `FS_PERF_BY_GEOGRAPHY` (11 rows + 7 geo col_members).
- Full statutory balance sheet + income statement authoring still open — `Total liabilities` and `Total equity` not authored yet (need `FS_BALANCE_STATUTORY` masterlist).

**Geography vocabulary.**
- 7 members: `SG`, `MY`, `TH`, `ID`, `GREATER_CHINA` (single, no HK split), `OTH`, `GLOBAL`. `Total` → `GLOBAL` was the only geo_map addition needed at pilot time.

**Segment vocabulary.**
- Best-guessed at breakdown-anchors time: `group_retail`, `group_wholesale_banking`, `global_markets`, `others`. Every UOB row in the breakdown_of_gross_nb_loans anchors needs re-verification once UOB row masterlist covers `FS_CUSTOMER_LOANS` and `FS_PERF_BY_SEGMENT`.

**Row-side `::` composition adopted by default for breakdowns.**
UOB has the smallest authored surface right now; §5.1 preference means most UOB breakdowns will be composed row-side rather than needing new hard-axis col vocabulary. `Over 3 years` = `over_3_but_within_5` + `over_5` is a UOB-only maturity composite.

**Curated state.**
- `UOB_masterlist.csv` (43 rows, Financial Highlights + geography).
- `UOB_masterlist_cols.csv` (7 rows, FS_PERF_BY_GEOGRAPHY only).
- Everything else pending — this is the next bank to work through with the same script pattern as DBS and OCBC.

---

## 5. Adding a new table — the checklist

Every new `table_type_id` runs this checklist before you commit rows or cols to a masterlist. In order:

1. **Confirm the type_id doesn't already exist under a different label.** Same structure across periods → ONE type_id with aliases. Same structure different legal entity → ONE type_id with `legal_entity` col disambiguating. Different structure that just happens to share a name → distinct type_ids.
2. **Draft the `table_registry_seed.csv` row first** — one row per (table_type_id, bank, doc), with structural signature + col_axis. This is done chat-side, not in CC.
3. **Author rows.** Apply §2 rules 1-3b. Every ancestor gets normalized. Every leaf gets checked against the "never" list in §2.4.
4. **Sweep cols before authoring col_members.** For every col in the printed table, classify as period / derived / hard / note_reference / unresolved. Extend the deterministic vocabulary if you find a new label shape (Note plurals, restated markers, currency-equivalent). This is the 30-second step that prevents Note-shaped surprises.
5. **Author col_members if hard-axis.** Snake_case path for `canonical_col_id`. `dim_key` from §3.4 vocabulary; propose a new dim member here (§3.4) before adding it.
6. **Cross-bank sanity check.** Does this table exist on the other two banks? Do their labels align? If yes, id-align now. If no, note the divergence — anchor CSV will handle it.
7. **Anti-pattern sweep.** Run through §2.7 and §3.5. Any `::` composition of a period or dim member? Any silent SEG_TOTAL? Any invented structural split?
8. **Commit.** Row and col files together, with the seed row and any dim vocabulary additions.

Halt conditions — CC does not proceed, escalates to chat:

- Two-candidate label matches on an existing leaf.
- Novel concept not in printed masterlist.
- Mixed-concept table (two tables printed as one).
- `col_axis` classification ambiguous.

---

## 6. Where each artifact lives

| File | Purpose |
|---|---|
| `<bank>_masterlist.csv` | Row masterlist. `canonical_leaf_id`, `full_path`, `line_ordinal`, `table_type_id`, `source_family`, `label`, `sign`. |
| `<bank>_masterlist_cols.csv` | Col masterlist. Sparse — one row per (bank, table_type_id, canonical_col_id, dim_key, dim, member_label). Only for hard-axis tables. |
| `<bank>_masterlist_<doc_slug>.csv` | Per-doc scratch during authoring. Merged into combined per-bank file. |
| `table_registry_seed.csv` | Pre-declared type_ids with structural signature + col_axis. L1 seed — resolver reads, never regenerates. |
| `highlights_dashboard_anchors.csv` | Single-member concept → (bank, canonical_leaf_id, canonical_col_id, filter_by, sign). |
| `highlights_dashboard_formulaanchors.csv` | Composite concept → ordered list of (canonical_leaf_id, sign) rows. |
| `breakdown_of_gross_nb_loans_anchors.csv` / `_formulaanchors.csv` | Same pattern for the loans dashboard. |
| `2026-08-09-column-axis-identity.md` | The spec. When this doc and the spec conflict, the spec wins. |
