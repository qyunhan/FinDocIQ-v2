# Anchor scope resolution — document → section → table (G13)

Resolves the top three levels of the anchor address (`source_doc → doc_section →
table_name → table_type_id`) for the 12 tables backing `lineage_identity_map.csv`'s
93 row-level anchors, across all three banks' 4Q25/FY2025 releases. Row-level
resolution, `concept_key`, the dashboard, and `fact_metric` are out of scope for this
pass — see `pipeline/mapping/migrate_add_document_alias.py` for the code and
`pipeline/mapping/registry.py` for the resolution mechanism (`resolve_table_type`,
pre-existing).

Prerequisite confirmed: one `claude` process on the DB before this pass started.

## Deliverables

1. `document_alias` table created and populated — 4 rows (§ Level 1).
2. `table_registry_alias` — 2 new rows added, both bank-scoped composite aliases that
   fix real misclassifications surfaced while resolving these 12 targets (§ Level 3).
3. This resolution report — 12 rows, PASS/FAIL per level, verified against physical
   `table_t` rows, not just alias-lookup success (§ Result table).
4. `lineage_identity_map.csv` — **already correctly named** on disk (no `(1)` suffix
   found); no rename was needed. The 3 row-anchors dependent on the one table-level
   FAIL (`eps.basic`, `eps.diluted`, `nav.per_share`) are marked
   `resolution=pending_extraction` (§ Level 3) so they don't block the other 89.

## Level 1 — document

`document_alias (alias_filename TEXT PRIMARY KEY, doc_id TEXT NOT NULL)`. The map is
**not** mutated — it keeps the analyst's filename; the alias table carries the join.

| alias_filename (as written in the map) | doc_id |
|---|---|
| `DBS_4Q25_performance_summary.pdf` | `DBS_4Q25_performance_summary` |
| `UOB_4Q25_condensed-financial-statements.pdf` | `UOB_4Q25_condensed-financial-statements` |
| `OCBC_Full_Year_2025_Condensed_Financial_Statements.pdf` | `OCBC_4Q25_Condensed_Financial_Statements` |
| `OCBC_4Q25_Media_Release_and_Financial_Highlights.pdf` | `OCBC_4Q25_Media_Release_and_Financial_Highlights` |

All 4 distinct source_docs in the 12 targets resolve. `OCBC_Full_Year_2025_...` and
`OCBC_4Q25_Condensed_Financial_Statements` are confirmed to be the same pack (OCBC
titles its Q4 release "Full Year"). `OCBC_4Q25_Media_Release_and_Financial_Highlights`
is confirmed to be a **separate**, distinct `doc_id` from the condensed statements —
the ratios anchor sources from it; the income/balance-sheet anchors source from the
condensed statements. DBS and UOB source_docs resolve 1:1 to their doc_ids (verified
against `document`, not assumed from the prior audit).

## Level 2 — section

One genuine hazard found and handled: **DBS's `Overview` title is not unique inside
its own document.** Besides the top-level financial-highlights section
(`section_id='overview'`, level 1, the one with the exhibit tables), the same
document has a *second*, unrelated section also titled `Overview`
(`section_id='overview_2'`, level 3, nested under `Report on the Audit → Our Audit
Approach → Overview` — audit-methodology prose, no financial tables). An exact
case-sensitive match on the map's literal casing (`Overview`) actually misses the
real section (`OVERVIEW`, all-caps) and would silently land on the audit-report decoy
if matching were case-sensitive; a naive case-insensitive match without
disambiguation would be ambiguous between the two. Resolution rule applied: match
case-insensitively, then prefer the top-level (`section_level=1`) hit — consistent
with the task's own two-shape taxonomy (highlights sections are enclosing top-level
containers; a level-3 subsection under an unrelated report is never an anchor
target). No other section in any of the 4 docs collides case-insensitively.

All 4 sections used by the 12 targets resolve (DBS `Overview`, DBS
`AUDITED BALANCE SHEETS`, UOB `Financial Highlights`, OCBC `CONSOLIDATED INCOME
STATEMENT`, OCBC `BALANCE SHEETS`, OCBC `FINANCIAL HIGHLIGHTS`) within their
respective documents.

## Level 3 — table

10 of 12 targets already resolved correctly through **pre-existing** `'*'`-scoped
aliases in `table_registry_alias` (added in an earlier session) — no new rows needed
for those. Two real classification bugs were found and fixed with bank-scoped
composite aliases (most-specific level, per `registry.py`'s documented priority:
composite beats section beats title, and a bank-specific alias beats `'*'` at the same
level):

- **OCBC media release, `Key Financial Ratios`**: the generic `'*'`-scoped
  `financial_highlights` section alias (seeded for UOB's combined-table shape) was
  winning the section-level lookup for *every* bank's `Financial Highlights`/
  `FINANCIAL HIGHLIGHTS` section, including OCBC's — misrouting the ratios anchor to
  `FS_HIGHLIGHTS_COMBINED` (UOB's bucket) instead of `FS_RATIOS_KEY`, even though
  OCBC's actual ratio rows live in real, already-correctly-typed `FS_RATIOS_KEY`
  tables (`performance_ratios`, `revenue_mix_efficiency_ratios`, three levels deep
  under `financial_highlights_continued`). Added
  `('financial_highlights__key_financial_ratios', 'OCBC', 'FS_RATIOS_KEY')` —
  outranks the wildcard for exactly this bank+combo, changes nothing for UOB or any
  other document that legitimately relies on the wildcard.
- **UOB, `Balance Sheets (Audited)`**: the map lists this table under the
  `Financial Highlights` section, but `Total liabilities` / `Total equity` are **not
  printed on UOB's highlights pages at all** — confirmed by querying
  `cell_fact`/`row_lineage` for both `Financial Highlights` table rows and finding no
  such line items (only `Shareholders' equity` inside a ratio, and `Common Equity
  Tier 1`). The real statutory Balance Sheet lives in its own section elsewhere in
  the same `doc_id` and already carries `FS_BALANCE_STATUTORY`, confirmed to contain
  exactly `Total equity`, `Total liabilities`, `Total equity and liabilities`. Added
  `('financial_highlights__balance_sheets', 'UOB', 'FS_BALANCE_STATUTORY')` — routes
  this specific (section, title) pair straight to the real table instead of falling
  through to the generic highlights bucket.

**One target does not resolve at the table level — traced to root cause, not left as
an open question.** DBS `Per share data` (Overview section): the generic `'*'`-scoped
`per_share_data` alias *does* resolve to `FS_PER_SHARE`, but no `table_t` row of that
type exists for `DBS_4Q25_performance_summary` — the Overview section's 4th table
(`table_id=overview_dbs_group_holdings_ltd_and_its_subsidiaries_2025-12-31`) sits
unclassified (`table_type_id IS NULL`). Traced to root cause by reading the raw
extraction output, `outputs/pillar3/dbs_4Q25/audit/DBS_4Q25_performance_summary/
overview_p4-8/parsed.json`:

- **The table exists and the data is correct.** It's page 6, inside the same p4-8
  Overview chunk as the other 3 exhibits. Its rows are `Earnings → Basic/Diluted` and
  `Reported earnings → Basic/Diluted, Net book value`, with values 3.84 / 3.82 /
  24.29 — an exact match to the figures the map's own `review_flag` column already
  cites for `eps.basic` (3.84), `eps.diluted` (3.82) (reported, not underlying — the
  map's own basis note).
- **It's mistitled, not missing.** `table.title` is `"DBS GROUP HOLDINGS LTD AND ITS
  SUBSIDIARIES"` — the running page masthead that repeats across pages 4-8, not an
  exhibit caption. The real caption *was* captured, just in the wrong field:
  `table.label_header == "Per share data ($)3,8"`. `table_registry_alias` has nothing
  to normalize a company-name string against (correctly — that string is not an
  exhibit identity and should never become an alias target), so the table never
  classifies.

This is a title-selection bug in extraction (`pipeline/pass2/extract.py` /
`pipeline/pass2/schema.py`'s `label_header` vs `title`), not a missing page and not an
alias-layer gap. **Not fixed in this pass** — out of scope for an alias-table-only
pass, and a one-off alias keyed on a company masthead string would be exactly the
per-document hack this project's routing rules forbid. The 3 dependent row anchors
(`eps.basic`, `eps.diluted`, `nav.per_share`) are marked `resolution=pending_extraction`
in the map (distinct from the existing `pending_anchor` status: the anchor path is
known and correct — `DBS_4Q25_performance_summary / Overview / Per share data` — only
the table's stored title/type needs the loader fix before it can classify).

## Result table

| bank | source_doc → doc_id | section → section_id | table_name → table_type_id | L1 | L2 | L3 |
|---|---|---|---|---|---|---|
| DBS | `DBS_4Q25_performance_summary.pdf` → `DBS_4Q25_performance_summary` | `Overview` → `overview` | `Selected income statement items ($m)` → `FS_INCOME_SELECTED` | PASS | PASS | PASS |
| DBS | ″ | ″ | `Selected balance sheet items ($m)` → `FS_BALANCE_SELECTED` | PASS | PASS | PASS |
| DBS | ″ | ″ | `Key financial ratios (%)` → `FS_RATIOS_KEY` | PASS | PASS | PASS |
| DBS | ″ | ″ | `Per share data` → **UNRESOLVED (`pending_extraction`)** | PASS | PASS | **FAIL — real table exists (page 6, `label_header="Per share data ($)3,8"`) but `table.title` was set to the page masthead; unclassified, not missing** |
| DBS | ″ | `AUDITED BALANCE SHEETS` → `audited_balance_sheets` | `AUDITED BALANCE SHEETS` → `FS_BALANCE_STATUTORY` (collapsed: section = table) | PASS | PASS | PASS |
| UOB | `UOB_4Q25_condensed-financial-statements.pdf` → `UOB_4Q25_condensed-financial-statements` | `Financial Highlights` → `financial_highlights` | `Selected income statement` → `FS_HIGHLIGHTS_COMBINED` | PASS | PASS | PASS |
| UOB | ″ | ″ | `Selected balance sheet items ($m)` → `FS_HIGHLIGHTS_COMBINED` | PASS | PASS | PASS |
| UOB | ″ | ″ | `Balance Sheets (Audited)` → `FS_BALANCE_STATUTORY` (new alias) | PASS | PASS | PASS |
| UOB | ″ | ″ | `Key financial ratios` → `FS_HIGHLIGHTS_COMBINED` | PASS | PASS | PASS |
| OCBC | `OCBC_Full_Year_2025_Condensed_Financial_Statements.pdf` → `OCBC_4Q25_Condensed_Financial_Statements` | `CONSOLIDATED INCOME STATEMENT` → `consolidated_income_statement` | `CONSOLIDATED INCOME STATEMENT` → `FS_INCOME_STATUTORY` (collapsed) | PASS | PASS | PASS |
| OCBC | ″ | `BALANCE SHEETS` → `balance_sheets` | `BALANCE SHEETS` → `FS_BALANCE_STATUTORY` (collapsed) | PASS | PASS | PASS |
| OCBC | `OCBC_4Q25_Media_Release_and_Financial_Highlights.pdf` → `OCBC_4Q25_Media_Release_and_Financial_Highlights` | `FINANCIAL HIGHLIGHTS` → `financial_highlights` | `Key Financial Ratios` → `FS_RATIOS_KEY` (new alias) | PASS | PASS | PASS |

**11 / 12 PASS. 1 FAIL (table level), root-caused, not left as a guess:**
`DBS_4Q25_performance_summary / Overview / "Per share data"` — table exists with
correct data, mistitled at extraction (see § Level 3). Fix is a loader/extraction
change (`title` vs `label_header` selection), not an alias. Does not block the other
11: those proceed to row-level resolution. The 3 dependent anchors are marked
`pending_extraction` in the map and rejoin once the table classifies correctly.

Zero anchors keyed on raw `table_title` — every PASS above routes through
`table_registry_alias` → `table_type_id`. Cross-bank non-collision holds by
construction: `table_registry_alias.bank` scopes every alias added or relied on here
(the two new composite rows are bank-specific; the pre-existing wildcards that cover
the other 10 targets were verified against each doc's actual `table_t` rows, not just
alias-lookup success).

## What this pass did not do

Did not re-run `classify_corpus()` / touch `table_t.table_type_id`. The two new
aliases only change how the *map's stated (section, title) pair* resolves during
anchor lookup — they do not reclassify any physical table (those were already
correctly typed via each row's own real section/title). Whether the two fixes should
also be applied by re-running `classify_corpus()` for other UOB/OCBC documents with
the same shape (e.g. `OCBC_1H25_Media_Release_Financial_Highlights`) is a natural
follow-up, not attempted here to keep this pass scoped to alias tables only, per
instruction.
