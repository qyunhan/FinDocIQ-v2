# M3 store relationship — `lineage_identity_map.csv` vs `bank_line_map.concept_key`

Read-only investigation, 2026-08-04. No writes to either store.

**Verdict: two independent stores, drift is a real risk (sync hazard).**

They agree today — measured, all 72 authored anchors reconcile — but nothing
enforces that. Detail in section 6.

---

## 1. `lineage_identity_map.csv` — schema

Path: `findociq/data/derived/lineage_identity_map.csv`. 93 data rows + header.
Hand-authored; nothing writes it programmatically.

**Key: `(bank, concept_key)`.** Not concept_key alone, and banks are not
separate columns — each bank gets its own row. Verified: 32 distinct
concept_keys × 3 banks, 31 rows per bank, zero duplicate `(bank, concept_key)`
pairs. Every row is `period='4Q25'`; period is not part of the key in practice,
it records which filing the anchor was authored against.

| Column | Role |
|---|---|
| `concept_key` | key part 1 — the cross-bank concept |
| `bank` | key part 2 — DBS / OCBC / UOB |
| `canonical_item`, `canonical_section` | display naming for the dashboard |
| `period` | filing the anchor was authored against (`4Q25` for all 93) |
| `resolution` | **the discriminator** — see below |
| `source_doc`, `doc_section`, `table_name` | provenance of the authored anchor |
| `parent_row`, `line_item` | the address, as printed labels (not normalized) |
| `formula` | derivation expression, `resolution='derived'` only |
| `review_flag` | analyst note / open question. Non-empty on 58 of 93 rows |

`resolution` decides what happens to the row:

| `resolution` | Rows | DBS / OCBC / UOB | Meaning |
|---|---|---|---|
| `anchor` | 72 | 21 / 25 / 26 | A real printed row. The only kind that loads into `bank_line_map`. |
| `pending_anchor` | 10 | 0 / 5 / 5 | Not authored yet. Reported, not loaded. |
| `not_disclosed` | 5 | 5 / 0 / 0 | Bank does not publish the item. Absence is a fact; routes to `concept_disclosure`. |
| `pending_extraction` | 3 | 3 / 0 / 0 | Authored, but no resolved row lineage yet. |
| `derived` | 3 | 2 / 1 / 0 | Computed from other concepts. Routes to the derivation layer, never to `bank_line_map`. |

Sample row (`resolution='anchor'`):

```
concept_key        = 'pnl.nii.net'
canonical_item     = 'Net interest income'
canonical_section  = 'Selected income statement items ($m)'
bank               = 'OCBC'
period             = '4Q25'
resolution         = 'anchor'
source_doc         = 'OCBC_Full_Year_2025_Condensed_Financial_Statements.pdf'
doc_section        = 'CONSOLIDATED INCOME STATEMENT'
table_name         = 'CONSOLIDATED INCOME STATEMENT'
parent_row         = ''
line_item          = 'Net interest income'
formula            = ''
review_flag        = 'CONFIRMED by analyst: statutory statement is its own section and table; …'
```

Sample row (`resolution='not_disclosed'`):

```
concept_key='ratio.loan_deposit'  canonical_item='Loan/Deposit ratio'
bank='DBS'  resolution='not_disclosed'
review_flag='bank does not publish this item — absence is a fact, not a gap'
```

---

## 2. `bank_line_map.concept_key` — schema context

`concept_key` is one column of a 21-column table. It is **not** the key. The key
is the physical address:

```
UNIQUE (bank, table_type_id, row_label_norm, parent_label_norm)
```

At most one row can occupy an address. `concept_key` is an attribute stamped
onto that address, nullable — an address with no concept binding is normal.

| Column group | Columns | Role |
|---|---|---|
| identity (the key) | `bank`, `table_type_id`, `row_label_norm`, `parent_label_norm` | the address |
| binding | `concept_key` | M3 — which concept this address supplies |
| dimensions | `legal_entity`, `segment_key`, `geo_key`, `industry_key`, `period_type`, `basis`, `balance` | slice qualifiers |
| flags | `is_abstract`, `negated_label` | structural header; sign convention |
| provenance | `map_status`, `mapped_by`, `confidence`, `mapped_at`, `note` | **who wrote it and how much to trust it** |
| lifecycle | `map_id`, `superseded_by` | surrogate key; deprecation pointer |

`map_status` is the load gate: only `human_confirmed` and `human_corrected` load
a value. `ai_proposed` never does.

Live counts — 2329 rows total, 593 with a non-empty `concept_key`:

| Bank | Rows with `concept_key` |
|---|---|
| DBS | 283 |
| OCBC | **240** |
| UOB | 70 |

Sample rows, one per writer:

```
-- mapped_by='lineage_identity_map.csv'
map_id=1108  bank=OCBC  table_type_id=FS_BALANCE_STATUTORY
row_label_norm='attributable_to_equity_holders_of_the_bank_total'
parent_label_norm='equity'   concept_key='bs.equity.shareholders'
map_status='human_confirmed' confidence=1.0  mapped_at='2026-08-03T08:17:09Z'
note='ANCHOR, not derived: OCBC prints this directly on the statutory Balance Sheet …'

-- mapped_by='dashboard_rows.yaml'
map_id=1151  bank=OCBC  table_type_id=FS_BALANCE_STATUTORY
row_label_norm='total_equity'  parent_label_norm=''  concept_key='bs.equity.total'
map_status='human_confirmed' confidence=1.0  mapped_at='2026-08-03T02:17:56Z'

-- mapped_by='backfill:corpus'
map_id=977   bank=OCBC  table_type_id=FS_ALLOWANCES
row_label_norm='allowances_charge_write_back_for_loans_and_other_assets'
concept_key='pnl.provisions.total'  industry_key='IND_TOTAL'
map_status='ai_proposed'  confidence=0.5
```

---

## 3. How `concept_key` is populated

**Answer: (c) — both, at different times.** Three writers, no shared source.

| Writer | Source | Status written | Rows w/ `concept_key` (DBS/OCBC/UOB) |
|---|---|---|---|
| `pipeline/mapping/backfill_map.py` | the corpus itself — concepts already stamped on cells, loaded only when every occurrence agrees | `ai_proposed` (never loads a value) | 243 / 199 / 42 = **484** |
| `pipeline/mapping/apply_dashboard_rows.py` | `pipeline/mapping/dashboard_rows.yaml` | `human_confirmed` | 40 / 26 / 28 = **94** |
| `pipeline/mapping/load_anchors.py` | **`lineage_identity_map.csv`** | `human_confirmed` | 0 / 15 / 0 = **15** |

The CSV is the smallest of the three inputs. It accounts for **15 of 593**
bindings corpus-wide (2.5%), **15 of OCBC's 240** (6%), and **zero for DBS and
UOB**.

`dashboard_rows.yaml` is a second hand-authored anchor set, independent of the
CSV, and it writes at the same `human_confirmed` status.

---

## 4. Is there an expansion rule? Does 94 → 240 math out?

**No, and no.** The premise of question 4 does not hold: `bank_line_map` is not
a projection of the CSV, so there is no per-row fan-out to compute.

- `load_anchors.py` is **1:1 at most, and lossy**. One CSV row can produce at
  most one `bank_line_map` row, because the target key is unique on the address.
  Of 93 CSV rows, only the 72 `anchor` rows are eligible; of those, only ones
  that resolve PASS through `resolve_anchors.py` load. `derived` routes to the
  derivation layer, `not_disclosed` to `concept_disclosure`, `pending_*` nowhere.
- OCBC's 240 decomposes as **199 `backfill:corpus` + 26 `dashboard_rows.yaml` +
  15 `lineage_identity_map.csv`**. The bulk is machine-seeded from the corpus, not
  authored anywhere.
- The CSV authored 25 OCBC anchors; 15 carry its provenance tag. The other 10
  landed on addresses already written by another writer and took the
  `confirmed_in_place` / label-conflict branches, which do not re-tag `mapped_by`.

So attribution slightly understates the CSV's influence, but not by enough to
change the picture: the two stores have different origins, different grains
(concept-per-bank vs physical address), and different population mechanisms.

---

## 5. Reconciliation path

**There is no check that the two stores agree on bindings.**

What exists, and what it actually covers:

- `pipeline/preflight_invariants.py:42` `spine_concepts()` reads the CSV and
  treats `anchor`/`derived`/`pending_extraction` concepts as "the spine". Check
  **A2** then asserts ≥90% of spine × bank combos have at least one non-null cell
  in `v_cell`. This is a **coverage** check on output values — it passes whether
  or not the value came from the address the CSV anchors.
- Check **A1a** asserts `human_confirmed` count `== 104` (currently true). A
  hard-coded total across all three writers; it detects a count change, not a
  binding disagreement.
- `load_anchors.py:130` refuses to overwrite a `human_confirmed` row whose
  `concept_key` differs, unless the pair is in `KNOWN_LABEL_ONLY_CONFLICTS`
  (one entry: `("UOB", "bs.assets.customer_loans_net")`). It exits rather than
  writing. This is genuine protection — but only in the direction of
  CSV → database, and only when `load_anchors` is actually run.

**The hazard is the reverse direction.** `apply_dashboard_rows.apply()`
(`apply_dashboard_rows.py:88-107`) updates any matching address whose status is
not `human_corrected`. A row written by `load_anchors` sits at `human_confirmed`,
so it is in range: running `apply_dashboard_rows` after `load_anchors` overwrites
`concept_key`, `mapped_by`, and `note` with no comparison against the CSV and no
warning. Last writer wins, and run order is manual.

**No trigger ties any of this together.** `run_doc.py` calls none of the three
writers — confirmed by grep. They are one-shot scripts a human runs by hand, in
whatever order.

### Measured drift today: none

For each of the 72 CSV `anchor` rows, I normalized `line_item` with
`mapping.normalize.normalize_row_label` and checked for a live non-deprecated
`bank_line_map` row with the same `(bank, concept_key)` and that
`row_label_norm`:

```
CSV anchors with a line_item:                72
  address AGREES with a live binding:        72
  address DISAGREES:                          0
  concept with no binding at all:             0
```

The stores are consistent right now. **57 of those 72 are backed only by writers
that never read the CSV** — the agreement is not maintained by any mechanism, it
is the residue of the same analyst authoring both sets against the same 4Q25
filings within a day of each other (`mapped_at` timestamps cluster on
2026-08-03).

### What reconciliation would look like (plan, not implementation)

1. **Pick a direction.** The CSV is authored per `(bank, concept_key)` and
   carries the `resolution` vocabulary and the analyst's reasoning; it is the
   better candidate for source of truth. `dashboard_rows.yaml` is authored per
   physical address, which is what the database needs. They overlap on 46 of 72
   anchors. Either merge them into one authored set, or declare one primary and
   generate the other.
2. **Add a check before merging anything.** A read-only `verify_m3_bindings`
   that, for every CSV `anchor` row, resolves the address and asserts a live
   `human_confirmed` binding at exactly that address with exactly that
   `concept_key` — reporting `MISSING` / `DISAGREES` / `OK`, the same shape as
   the existing M2 reports. This is the check that would have caught drift; it
   would pass 72/72 today, which makes it cheap to add now and a real gate later.
3. **Close the overwrite hole.** Either make `apply_dashboard_rows` refuse to
   overwrite `mapped_by='lineage_identity_map.csv'` rows (matching
   `load_anchors`' own protective behaviour), or introduce an explicit precedence
   order across the three writers and enforce it in all three.
4. **Give it a trigger.** As long as no driver runs these scripts, "consistent"
   is a statement about the last time a human ran them in the right order.

Sequencing note: step 2 before steps 1 and 3. Merging the authored sets without a
verifier means losing the current 72/72 agreement with no way to notice.

---

## 6. Verdict

> **Two independent stores, drift is a real risk (sync hazard).**

Not "authored source + materialized projection": `bank_line_map.concept_key` is
not derived from `lineage_identity_map.csv`. Three writers populate it from three
unrelated sources, the CSV supplies 2.5% of bindings corpus-wide and none for two
of the three banks, and the largest writer (`backfill:corpus`) reads the corpus
rather than any authored file.

Not "unclear": the mechanism is fully readable in the code. Each writer's source,
status, and precedence behaviour is explicit, and the provenance is recorded
per-row in `mapped_by`.

The qualifier that matters for prioritization: **the drift is potential, not
manifest.** All 72 authored anchors currently reconcile against live bindings.
This is a hazard to close before the next authoring pass, not a live data
corruption to repair.
