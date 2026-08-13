# Master registry — state on 2026-08-05, and what remains

> Companion to `GOAL: rebuild the three masterlist registries with (A) reference-set
authority (worklist §2) and (B) the banner-by-scope hierarchy rule
(Rule 3) plus the period-banner exception, in ONE pass — they interact,
so no partial builds. Emit to data/derived/masterlist_proposed_v3/.
Do not overwrite _v2 or the original; the diff needs all versions.
READ-ONLY against compiled_v2.db. No curation in this pass.

PRE-FLIGHT (do first, report before building):
  P1. Verify FS_EQUITY_CHANGES_{GROUP,COMPANY}'s extracted column
      structure: in the DBS/OCBC/UOB reference docs, are equity
      components (Share capital, Other reserves, Revenue reserves...)
      COLUMNS? If yes, the seed's col_axis must read
      'period+equity_component' — flag any mismatch between seed
      declaration and extracted reality; do not silently proceed on a
      mismatched axis.
  P2. Derive the REFERENCE SET per bank from the seed's doc_kind — not
      hardcoded doc ids. DBS: 4Q25 performance summary. OCBC: BOTH 4Q25
      documents (Media Release + Condensed FS) — this fixes the Q4_DOCS
      one-doc-per-bank bug where Media-Release-only leaves were marked
      historical with fabricated ordinals. UOB: 4Q25 condensed FS.
      Where a table_type_id appears in no reference doc but exists in
      the seed (HY-only tables like FS_AVG_BALANCE_SHEET), the richest
      half-year doc is the reference FOR THAT TABLE — reference is per
      table_type_id, falling back from the full-doc set.

ALGORITHM — per bank, per (section, table) in seed order. ALL state
(banner stack, hN numbering, positional walk) RESETS at each table
boundary; a row can never parent to a row in another table.

  PASS 1 — classify each row of the REFERENCE doc's table:
    has_values(row) = any cell carries a numeric value in any
                      non-derived-skip column in this document
    section_header  = no values AND label normalizes to the seed
                      caption → dropped, contributes no segment
    EXCLUDED        = no values AND label matches note/notes/
                      nm_not_meaningful, OR no values AND no DATA row
                      follows it before table end (trailing text)
    PERIOD_BANNER   = no values AND label matches a date/period
                      pattern: 'DD Mon YYYY', quarter labels
                      ('4th Qtr 2025','1Q25'), half labels, 'Year YYYY'
    BANNER          = no values, none of the above
                      (e.g. 'By currency and product', 'By geography')
    DATA            = has values

  PASS 2 — ancestry, walking document order:
    Banner stack rules:
      - an incoming BANNER/PERIOD_BANNER pops open banners whose
        printed level is >= its own, then pushes (printed level is
        used ONLY banner-to-banner — banners are stable relative to
        each other; children's levels drift across vintages)
      - a DATA row NEVER closes a banner, regardless of its printed
        level. This is the core of Rule 3: 2Q25 prints currency rows
        at level 0 under 'By currency and product'; 4Q25 prints them
        at level 1 — both must land inside the banner.
      - end of table closes all
    Parent of a DATA row — precedence:
      a. printed parent (hN / literal-label), if valid (resolves to a
         strictly shallower row in the SAME table) — wins
      b. else nearest preceding DATA row at shallower printed level
         WITHIN the innermost open banner scope (a DATA parent from
         before the banner opened is not eligible)
      c. else the innermost open BANNER itself
      → both hierarchy styles emerge from one rule: value-carrying
        parents ('Commercial book total income' → its NII child) via
        (b); banner parents ('By business unit' → 'Consumer Banking/
        Wealth Management') via (c).
    PERIOD_BANNER special case: scopes its block exactly like a
    BANNER, but (i) writes row_period on every row in its scope, and
    (ii) contributes NO segment to any canonical_leaf_id. A date is
    period data, never identity. '31 Dec 2025' panel and '30 Jun 2025'
    panel over the same rows → ONE leaf per row, two facts differing
    by period_id. The dashboard shows both dates by pivoting period —
    never by having two leaves.

  PASS 3 — ids and columns:
    canonical_leaf_id = '::'-join of (BANNER ancestors, outermost→in)
      + (DATA ancestors) + self, each segment normalized (casefold,
      whitespace→underscore, footnote markers incl. trailing glued
      digits, unit suffixes); then subtotal collapse (consecutive
      identical segments fold); then of-which memo rule ('of which'
      rows attach under nearest preceding non-memo row:
      total_income::of_which::net_interest_income).
    Column dispatch per the seed's col_axis:
      period                → period_id + period_type attrs on
                              col_dim; NO registry entry
      hard axes (segment / geo / entity / measure / level /
      equity_component)     → registry col_members with
                              canonical_col_id per member
      derived (% chg, volume/rate deltas) → col_role='derived_skip';
                              never ingested
    Emit col_members in the YAML for every hard-axis table (this is
    new vs _v2 — equity changes, segment, geography tables carry
    their column vocabulary in the registry).

  OTHER PERIODS (after the reference build, per table):
    Run the SAME classify+ancestry+normalize logic on each non-
    reference doc's rows. Resolve each resulting path against the
    reference-built leaf set:
      match → record raw path as leaf_id_alias (dedup, SORTED —
              fixes the nondeterministic alias ordering in _v2)
      miss  → review queue entry with ordinal-position suggestion
              (the reference leaf at the same slot between the row's
              resolved neighbors, if any)
    NEVER mint a canonical id from a non-reference doc. A leaf in no
    reference doc is not a registry member — it is queue material
    (retired or defective, per worklist §3.1).

GATES — per table, pass/fail BEFORE emitting that table's leaves;
report failures, do not emit a failing table silently:
  G1. Ghost-ancestors = 0: every canonical_leaf_id segment appears
      (normalized) in the leaf's own full_hierarchy.
  G2. No date/period segment in any canonical_leaf_id.
  G3. Active leaf count ≈ the reference doc's printed data-row count
      for that table (report both numbers).
  G4. No canonical_leaf_id is a proper '::'-suffix of another id in
      the same table.
  G5. Vintage-stability proof: for tables present in both DBS 2Q25
      and 4Q25 (FS_CUSTOMER_DEPOSITS currency rows are the known
      case), the 2Q25 rows must resolve to IDENTICAL ids as 4Q25's —
      the banner rule absorbing the level drift, no alias needed.
  Known targets: DBS FS_INCOME_SELECTED = 21 active (already
  achieved in _v2 — must not regress); the 13 markets_trading_income
  phantom-prefix leaves and 42 date-prefix leaves found in _v2 DBS
  must be gone.

IMPLEMENTATION CONSTRAINTS:
  - Rule 3 + classification + normalization live in ONE shared module
    imported by both the seeder and any resolver — never two copies.
    (Note in the report: this sharing means Phase-2 zero-unresolved
    proves coverage, not resolver correctness — the real correctness
    test is unseen DBS 2Q26, which is the step after this.)
  - Deterministic output: sort alias lists, stable leaf ordering by
    ordinal, so YAML diffs are meaningful.
  - Do not touch _v2/, masterlist_leaf_aliases.yaml (carry the 5
    curated aliases forward into _v3 where their targets still
    exist; flag any whose target id changed), compiled_v2.db, or
    the seed CSV.

DELIVERABLES:
  data/derived/masterlist_proposed_v3/{DBS,OCBC,UOB}.yaml (with
  col_members for hard-axis tables); gate report per table (G1-G5
  with numbers); _v2→_v3 diff summary per bank (leaves gone/added/
  id-changed, alias counts); review queue with suggestions; the P1
  equity-axis finding; one-line note on any normalize change forced
  along the way.docs/specs/2026-08-04-masterlist.md`, which defines WHAT the
> masterlist is and where it is stored. This document is the **build state** and
> the **worklist**. The masterlist spec stays authoritative for definitions.

---

## 0. Where the registry stands

| Level | Artifact | State |
|---|---|---|
| **L1 — table** | `data/derived/table_registry_seed.csv` (102 rows: DBS 31, UOB 28, OCBC 43) | **Authoritative, complete.** Consumed, never regenerated. |
| **L2 — leaf** | `data/derived/masterlist_proposed/masterlist_registry_{DBS,OCBC,UOB}.yaml` | **PROPOSED, pre-curation.** DBS 644 / OCBC 603 / UOB 366 leaves. |
| Curated aliases | `data/derived/masterlist_leaf_aliases.yaml` | 5 entries, human-confirmed. |
| Source DB | `db/compiled_v2.db` | Built from a full pass2 replay through the fixed loader. |

Build path, all reproducible:

    findociq/tools/replay_load.py          # audit artifacts -> compiled_reload.db
    findociq/tools/build_compiled_v2.py    # compiled_reload.db -> compiled_v2.db (clean schema)
    findociq/tools/build_masterlist_proposed.py --db findociq/db/compiled_v2.db

`compiled_reload.db` is git-ignored: it is regenerable and 31 MB.

---

## 1. What the leaf id is, and the four rules that shape it

`canonical_leaf_id` = `'::'`-joined normalised ancestor path, root → leaf. Never
hand-set. Four rules apply, in order:

1. **Subtotal collapse** — consecutive identical segments fold to one.
2. **Of-which memo** — a row whose label starts *"of which"* attaches under the
   nearest preceding non-memo row at equal-or-shallower depth:
   `total_income::of_which::net_interest_income`.
3. **Banner ancestors by scope, not level** — a row with **no data values in any
   column** is a banner and becomes an ancestor of the block it introduces,
   regardless of its printed level. Value-presence is the only signal stable
   across vintages: DBS prints `By currency and product` with `values=0` in both
   2Q25 and 4Q25, but the currency rows below it sit at level 1 in 4Q25 and level
   0 in 2Q25. Keying on level loses the banner in one vintage and keeps it in the
   other, splitting one line into two ids.
4. **Suffix collapse** — within one (bank, table), if id `A` is a proper suffix of
   id `B`, they are the same printed line and `A` folds into `B` with its raw
   paths kept as aliases. Structural, so it fires regardless of *why* the ancestor
   went missing. 133 ids folded on the current build.

**Status** — `active` = printed in the reference (4Q25) document; `historical` =
observed only in earlier vintages.

---

## 2. THE NEXT CHANGE — one full document per bank

**This is the highest-value item and it is not done.**

The registry currently unions every period as an equal source. It should not.
The corpus is one full document per bank plus a tail of partial ones:

| bank | full document | tables | partial documents |
|---|---|---|---|
| DBS | `DBS_4Q25_performance_summary` | 45 | 2Q25 (37), 2Q22 (35), 4Q22 (13), trading updates (**4 each**) |
| OCBC | `OCBC_4Q25_Media_Release` + `OCBC_4Q25_Condensed_FS` | 53 + 41 | 1H25 (30), 1Q25 (12), 3Q25 (6) |
| UOB | `UOB_4Q25_condensed-financial-statements` | 44 | 1Q25 (**3**), 3Q25 (**2**) |

A 4-table trading update currently carries the same authority as a 45-table full
document, and that is where essentially all the noise came from — the 1Q23
trading update produced the `treasury_markets_total_income` family; the 3Q25 one
produced the orphans. UOB is the control: 5 non-4Q25 tables in total, and only 2
historical leaves, against DBS's 217.

**Required change:**

- **Reference set = the full document(s) per bank**, derived from the seed's own
  `doc_kind` values, not hardcoded. That set defines the active leaves AND their
  ordinals.
- **Other periods contribute aliases only.** A vintage rename of a leaf present in
  the full doc folds in as an alias; a leaf present in *no* full doc is not a
  registry member — it goes to the review queue as retired or defective.

**Known bug this must fix:** `Q4_DOCS` in `build_masterlist_proposed.py` maps one
document per bank, but OCBC's full picture spans **two** 4Q25 documents. Every
leaf appearing only in the Media Release is currently marked `historical` and
given a fabricated ordinal, when it is part of the live set.

---

## 3. Worklist

### 3.1 Registry structure
- [ ] Reference set = full document(s) per bank, from the seed's `doc_kind`
      (fixes the OCBC two-document bug above).
- [ ] History becomes annotation, not a first-class source.
- [ ] Split the `historical` leaves into **defect** (orphan / mis-parented — fix
      upstream) vs **genuinely retired or renamed** (human queue). Roughly half of
      DBS's are expected to be defects, on the `FS_INCOME_SELECTED` precedent
      where all six historical leaves were loader artifacts, not history.

### 3.2 Table resolution
- [ ] **88 captions / 1,826 rows unresolved.** Triage:
      - 914 rows Pillar 3 / regulatory — `REG_*` types exist in the YAML vocabulary
        but have **no seed row**, so they are outside L1's declared scope. A scope
        decision, not a defect.
      - 146 rows `financial_highlights` — deliberately unseeded (OCBC's 4Q25 media
        release puts twelve tables under one page header; ambiguous at title level,
        must resolve at section level).
      - 766 rows genuine alias candidates — e.g. OCBC prints *Nine Months / Second
        Quarter / Third Quarter 2025 Performance* while the seed names only
        *Fourth Quarter* and *Full Year*.
- [ ] A caption that matches only a **narrative** seed row currently lands in
      `table_unresolved` instead of being reported as "matched, narrative,
      skipped" — e.g. OCBC *Strong Funding, Liquidity and Capital Position*
      (48 rows).

### 3.3 Data quality feeding the registry
- [ ] **659 true orphans (10.4%)** in `compiled_v2.db` — rows deeper than their
      table's minimum level with no parent. Down from 804, but these need the
      extractor to emit the missing header rows; they cannot be recovered from
      levels alone.
- [ ] **`ingest_status` is empty** in the replayed DB (the original had 4 rows for
      25 documents). It is the only place that distinguishes a skipped table from a
      merged one. Until it is written, table-count reconciliation is guesswork.
- [ ] **`dedup_status` is NULL** in `compiled_v2.db` — page-split dedup is a
      separate pipeline step that has not run against the replay. The replay
      produces far fewer duplicates natively (`FINANCIAL HIGHLIGHTS (continued)`
      10 → 2) but that is not a substitute.
- [ ] Two documents fail the replay with *no loadable audit units*
      (`DBS_1Q22_trading_update`, `DBS_3Q22_trading_update`). Both contribute 0
      tables today, so no data is lost — but the artifacts are missing and should
      either be regenerated or the documents retired.

### 3.4 Then, and only then
- [ ] Promote the curated registry from PROPOSED to authoritative.
- [ ] Stamp `row_dim.canonical_leaf_id` (currently NULL by design in
      `compiled_v2.db`).
- [ ] Build the **anchor / rollup split** on top of stable leaf ids —
      `line_anchor(bank, table_type_id, canonical_leaf_id) -> concept_key` and
      `concept_rollup(bank, table_type_id, concept_key) -> ordered [leaf_id, sign]`.
      The rollup table is the executable form of `lineage_identity_map.formula`,
      which today no code reads: as rows, `[Commercial book] NII + [Markets] NII`
      needs no formula parser. **Prerequisite: stable leaf ids, i.e. everything
      above.**

---

## 4. Verification that must stay green

- Phase 2 reverse-tag of the three 4Q25 documents: **0 unresolved** (DBS 593 /
  OCBC 370 / UOB 446 resolved).
  **Read this with care** — seeding and resolution share one normalisation
  implementation in `build_masterlist_proposed.py`, so the zero proves registry
  coverage and `(bank, table_type_id)` scoping, NOT resolver correctness. A
  seed-vs-resolve divergence only becomes detectable when the production resolver
  is a separate implementation.
- `DBS FS_INCOME_SELECTED` = **21 leaves, all active** (was 55 before the loader
  fixes and the id rules).
- `pytest pass2` 61 · `concept` 46 · `mapping` 16 · `app` 87.

---

## 5. Pre-existing breakage found along the way (not fixed here)

`findociq/pipeline/test_verify_cells.py` and `findociq/app/test_spec.py` call
`sys.exit()` at module scope, which aborts pytest **collection** for any directory
above them. `pytest` from the repo root cannot run because of this. Suites must
currently be invoked per-directory.
