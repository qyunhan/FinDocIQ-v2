# TO FIX — known defects, not yet fixed in the shipped artifacts

Known problems that are **diagnosed and measured but deliberately not acted on**.
Each entry states the evidence, the blast radius, and what would make it worth
doing. Newest on top. When one is fixed, delete the entry and record the fix in
`PROGRESS.md` (and the reasoning in `DECISIONS.md` if a trade-off was made).

The rule this file exists to enforce: **a defect being real is not the same as it
being worth fixing now.** Several entries below are invisible to every dashboard,
and rebuilding to fix them would change numbers nobody reads.

---

## 0. DBS 2Q26 page 7 was silently dropped — the QUARTER-basis income statement

**Status:** real data loss, and the only entry in this file that is VISIBLE on a
dashboard. Reported by Yunhan 2026-08-12.

DBS prints its overview twice: half-year basis on pages 4-6, quarter basis on
pages 7-8. The extraction unit `overview_p4-8` chunked those five pages into
three calls:

    chunks/c1  pages [4, 5]  -> 1 table  'OVERVIEW'                     (45 rows)
    chunks/c2  pages [6, 7]  -> 1 table  'DBS GROUP HOLDINGS LTD ...'   ( 8 rows)
    chunks/c3  pages [8]     -> 1 table  'Per share data ($)3'          ( 7 rows)

`c2` covered TWO pages and returned ONE table. It captured page 6's
`Per share data ($)3` (1H26 / 1H25 / 2H25) and dropped page 7 entirely — the
`Selected income statement items ($m)` printed on the quarter basis
(2Q26 / 2Q25 / %chg / 1Q26 / %chg). The page-7 text is present in
`chunks/c2/pages.pdf`; nothing downstream noticed it produced no table.

**Measured cost** — `DBS_2Q26_performance_summary`:

    FS_INCOME_SELECTED spans present : 1H (20 cells), 2H (20), 1H26 (20), NULL (40)
    FS_INCOME_SELECTED spans MISSING : 2Q, 1Q  — the entire quarter basis
    whole document                   : 632 cells span 1H, only 10 span 2Q

The Key Financial Highlights axis carries a `2Q26` column, so DBS's quarterly
income lines have nothing to render.

**The general defect, not the instance:** a chunk spanning N pages that returns
fewer tables than the pages carry captions is not detected. Fixing only this
document would be the overfitting CLAUDE.md forbids — the guard belongs in the
chunker/verifier, e.g. compare captions found in the chunk's own text against
tables returned, and fail or re-chunk on a shortfall.

**Also check:** whether other multi-page chunks in the corpus lost a page the
same way. `c2` is the only one inspected so far.

---

## 0b. Output folders are keyed on the cover date, not the reporting period

**Status:** cosmetic but confusing; no data loss.

`pass2/render.py:derive_period` maps quarter-end months (Mar/Jun/Sep/Dec) to
`4Q25`-style slugs and EVERY other month to `Feb26`-style ones. The date comes
from `detect_bank`, which scrapes the first two pages — and an OCBC media
release prints its PUBLICATION date, while the statements print the period end.
So one reporting period lands in two differently-named folders:

    outputs/fs/ocbc_Feb26/  OCBC_4Q25_Media_Release_and_Financial_Highlights
    outputs/fs/ocbc_4Q25/   OCBC_4Q25_Condensed_Financial_Statements
    outputs/fs/ocbc_Aug26/  OCBC_2Q26_Media_Release_and_Financial_Highlights
    outputs/fs/ocbc_2Q26/   OCBC_2Q26_Unaudited_Interim_Financial_Statements

These are four DISTINCT documents, not duplicates — OCBC files two per period.
The fix is to key the path on `document.doc_period` (already resolved by then)
rather than the scraped cover date.

---

## 1. Two committed fixes are not in the shipped DB, and need not be

**Status:** fixed in code, absent from `findociq/db/compiled_v2.db`. **No action.**

| Fix | Lives in | Reaches the DB via |
| --- | --- | --- |
| UOB FY2024 bare-year title period | `pipeline/pass2/load_v7.py` | a re-LOAD only |
| DBS 1H25 `intangibles` leaf alias | `data/derived/masterlist/masterlist_leaf_aliases.yaml` | stamping |

**Why it can wait — measured 2026-08-12:**

* The period bug mis-stamps 77 cells in `Performance by Geographical Segment ¹ — 2024`
  (stamped `2025-12-31`, should be `2024-12-31`). Of the 63 stamped cells in that
  table, **0 are addressed by any anchor** in `data/derived/dashboards/*.csv`.
* `total_assets_before_goodwill_and_intangible_assets` appears in **0 of the 4
  anchor files**. No dashboard row has ever shown that concept.

Both apply automatically the next time those documents are ingested. A rebuild
today would cost ~240 re-shaped `row_dim` rows (see §5) to change nothing visible.

**Revisit when:** an anchor addresses either leaf, or a real re-ingest happens.

---

## 2. Re-stamping loses 78 valid leaves — 61 in one exhibit

**Status:** real regression, blocks a clean rebuild.

Clearing `canonical_leaf_id` on a copy of `compiled_fs.db` and re-running
`stamp_tables.py` gives **3,753** stamped rows against the shipped **4,064**.
Of the 312 shipped-only stamps, 78 carry an id that IS in the current masterlist
for its `(bank, table_type_id)` — so the id is legitimate and the matcher simply
no longer finds the row:

    OCBC  FS_INCOME_SELECTED         x61   'Net interest income', 'Non-interest income'
    DBS   FS_EQUITY_CHANGES_COMPANY   x6   'Balance at 1 January 2026'
    UOB   FS_PERF_BY_GEOGRAPHY        x6   'Total income', 'Total expenses'
    OCBC  FS_EQUITY_CHANGES_GROUP     x3
    DBS   FS_EQUITY_CHANGES_GROUP     x2

61 of the 78 are one exhibit: OCBC 2Q26 `FINANCIAL HIGHLIGHTS`, a single physical
table carrying both income and balance-sheet rows. `table_t.table_type_id` is
`FS_BALANCE_SELECTED` while rows inside stamp `FS_INCOME_SELECTED`. Shipped
stamps 17 rows in it; a re-stamp gets 6.

**Fix this before any rebuild** — otherwise a rebuild trades 234 bad stamps (§3)
for 78 good ones.

---

## 3. 234 stamps in the shipped DB violate provenance

**Status:** latent correctness bug; a rebuild REMOVES it.

Of those same 312 shipped-only stamps, **234 carry a `canonical_leaf_id` that is
NOT in the current masterlist** for its `(bank, table_type_id)` — residue from
earlier masterlist vintages, since `stamp_tables.py` only ever adds or overwrites
and never clears. Almost entirely OCBC:

    FS_L3_MOVEMENTS x56   FS_EQUITY_CHANGES_GROUP x55   FS_L3_VALUATION x40
    FS_PERF_BY_GEOGRAPHY x39   FS_BALANCE_BY_GEOGRAPHY x13   + 8 more types

This breaks the invariant the design rests on: *every stamped id is copied
verbatim from the masterlist*. They are unreachable by anchors (no masterlist
entry to address them), so they are inert rather than wrong-on-screen — but they
should not exist.

**Resolves automatically** with §2, since a clean re-stamp drops them.

---

## 4. OCBC has no segment columns anywhere

**Status:** missing data, not a naming problem.

`FS_BALANCE_BY_SEGMENT` has **zero columns for OCBC** in every database checked,
and OCBC carries no `SEG_*`-equivalent stamped column under any table type. So
the 5 OCBC members of the **By Business Unit** section in
`breakdown_of_gross_nb_loans_anchors.csv` cannot resolve whatever column
vocabulary is chosen, and those rows render blank.

UOB's equivalents resolve and reconcile exactly:

    UOB @30-Jun-26   gross loans 361,411   sum(BU) 361,411   diff 0
    DBS @30-Jun-26   gross loans 475,238   sum(BU) 475,238   diff 0

**Next step:** find whether OCBC's segment exhibit is classified as a different
`table_type_id`, or is not being extracted at all.

---

## 5. `compiled_v2.db` was patched, not rebuilt

**Status:** RESOLVED 2026-08-14 — and the warning below came true first.

The predicted drift happened: commit 051c32b rebuilt the DB and the
artifact-level patch was lost, leaving `canonical_col_id` at **0 of 1915**. It
is now fixed in the LINEAGE instead — `compiled_fs.db` was re-stamped with
`stage3_stamp/apply/restamp_columns.py` and `compiled_v2.db` rebuilt from it
(`build_compiled_v2` carries the field), so a rebuild keeps it. 210 columns
stamped. The tool's path in the note below is also stale: it lives at
`findociq/pipeline/stage3_stamp/apply/restamp_columns.py`, not `tools/`.

Original entry, kept for the record:

**Status:** accepted shortcut, documented so it is not mistaken for lineage.

`tools/restamp_columns.py` wrote `col_dim.canonical_col_id` (56 → 197) directly
into the built `findociq/db/compiled_v2.db`, because a full re-load would have
traded one gap for a bigger one. The live column masterlists reproduce that
stamping exactly — verified: re-running from the pre-patch snapshot with the
default glob yields an IDENTICAL
`(doc_id, table_id, col_id, canonical_col_id)` set — so the artifact and the
lineage agree **today**. They will drift the moment either changes.

**RESOLVED 2026-08-14, and the conclusion INVERTED.** The 39 `canonical_leaf_id`
stamps the replay "lost" were a DOUBLE COUNT, not an asset: they sat on
duplicate extractions of one OCBC page, and the anchor composition summed each
figure twice. Six cells of the live loans dashboard were serving exactly double
(Singapore 298,172 against a filed 149,086). The rebuilt figures reconcile to
the filed gross loans, 341,120, on every breakdown. The replay was promoted to
be the committed artifact.

The rebuild is also **deterministic** — two independent runs are byte-identical
— so this was never run-to-run randomness. Full write-up: **Appendix E** of the
technical report. Rebuild to a scratch path (`--rebuild-db --db <tmp>`, always
supported) and diff on IDENTITY, never on counts.

**Also note:** `findociq/db/compiled_fs.db` and a replay of it are NOT
interchangeable. Measured: `table_t` 342 vs 343, `row_dim` 5,772 vs 5,770 but
~240 keys differ on each side, `col_dim` 1,915 vs 1,884, `cell_fact` 21,581 vs
21,592. Never promote a replay output on row counts alone.

---

## 6. Five loan leaf addresses are unstamped

**Status:** RESOLVED 2026-08-14 — 0 uncaptured anchors of 166. Every one was an
anchor-side ADDRESS error, not missing extraction: OCBC `net_loans` ->
`allowances::net_loans`; OCBC's segment balance sheet re-keyed from
`FS_BALANCE_BY_SEGMENT` (a type OCBC produces zero tables of) to
`FS_BS_BY_SEGMENT` in both the anchors and the column masterlist; the UOB
allowance leaves resolve under `less_general_allowance::*`. Original entry below.

**Status:** anchors are probably right; the leaves are not stamped.

From `breakdown_of_gross_nb_loans_*.csv`, unresolved against `compiled_v2.db`:

    UOB   FS_CUSTOMER_LOANS      specific_allowance
    UOB   FS_CUSTOMER_LOANS      general_allowance
    OCBC  FS_CUSTOMER_LOANS      net_loans
    OCBC  FS_BALANCE_BY_SEGMENT  other_information::gross_non_bank_loans
    UOB   FS_PERF_BY_SEGMENT     selected_balance_sheet_items::other_information::gross_customer_loans

The last two sit in the segment table types that §2/§3 also implicate, so they
may resolve once stamping is corrected rather than needing anchor edits.

---

## 7. OCBC "Gross Customer Loans" shows the NET figure

**Status:** open. **The intended figure is GROSS — confirmed 2026-08-14.**

The Highlights anchor addresses
`OCBC / FS_BALANCE_CONSOLIDATED / loans_to_customers`, which is the balance
sheet's line and is reported NET of allowances. It renders **336,692**, exactly
equal to the loans breakdown's `Net loans`. True gross is **341,120**
(`FS_CUSTOMER_LOANS / gross_loans`, stamped 10x, and the figure every breakdown
block sums to).

A row labelled "Gross" is showing net, on the live dashboard, today.

**The fix** is one line in `data/derived/dashboards/highlights_dashboard_anchors.csv`
— repoint OCBC at `FS_CUSTOMER_LOANS / gross_loans`. The periods line up
(341,120 at 31-Dec-25, 364,493 at 30-Jun-26). Peers already do the right thing:
DBS uses `customer_loans`, UOB `gross_customer_loans`.

Not applied yet only because it changes a published figure. Verify against the
filing before shipping, then re-run the coverage check (must stay 166/166).

---

## 8. Duplicate page extraction — the root cause of the double count

**Status:** open, and the highest-value remaining defect.

One physical page is extracted as several `table_t` rows with identical row
content, and `dedup_status` is **NULL on all of them**, so
`quarantine_duplicate_page_tables.py` never marks the cluster. Confirmed on two
documents: OCBC 2Q26 p.9 FINANCIAL HIGHLIGHTS (two copies, stamping split
across them) and OCBC 4Q25 media release (three copies).

This is what produced the **double count** fixed on 2026-08-14: the anchor
composition SUMS its members, so a leaf stamped on two copies of a page is added
twice, and six cells of the live loans dashboard served exactly 2x the filed
figure. See Appendix E of the technical report.

The current state is a truce, not a fix: the duplicates are still extracted, and
are merely classified consistently enough that no leaf lands on two copies. A
new filing with the same page structure can reintroduce it silently.

---

## 9. No breakdown sums to its own total

**Status:** open. This is the guard that would have caught §8.

Nothing in the pipeline compares the parts of a breakdown against the total
printed in the same exhibit. Geography, industry, maturity and business unit
each declare one, and each must reconcile — OCBC 4Q25 gross loans is 341,120 on
all four. The double count in §8 survived identical row counts, `verify_cells`
10/10 PASS and 166/166 anchor coverage, because verification is **per cell**
while a double count is a property of **composition**.

Cheap, general, needs no masterlist knowledge. Implement it at the anchor-
composition layer where the sum already happens.

---

## 10. The public dashboard is set to private

**Status:** open, and it blocks external sharing.

An anonymous request to the Streamlit Community Cloud URL returns HTTP 303 to
`share.streamlit.io/-/auth/app` — the login wall. The owner must set the app to
"anyone with the link" in **Settings -> Sharing**; it cannot be changed from the
repository. Re-test in a private window before sending the link to anyone.

---

## 11. Masterlist coverage — Tiers 2 to 4

**Status:** open, prioritised, and not a code problem.

`canonical_leaf_id` covers 3,804 of 5,771 rows (69% excluding Pillar 3). Roughly
half of what is unstamped is label-only headers that should stay unstamped. The
ranked worklist is **Appendix D** of the technical report — Tier 1 is clear,
Tier 2 is 255 figure-carrying rows in tables already classified, Tier 3 is 13
column blocks, Tier 4 is 63 unclassified tables. Regenerate Appendix D after any
masterlist edit; its numbers are queries, not prose.

