# Key-field extractability audit — baseline (pre-sweep)

Date: 2026-07-31 · DB snapshot: `compiled_fs.pre-sweep.db`
Harness: `pipeline/mapping/audit_coverage.py` (read-only)
Full matrix: `docs/2026-07-31-key-field-coverage.md`

**This is the BASELINE against current extractions. No re-extraction has run.**
It was produced first, deliberately: it identifies which documents actually need
paid re-extraction and gives the before-half of the snapshot+diff.

Key-field set: the 89 `human_confirmed` anchors (28 concepts) from step 5.
A HIT requires the **structural path to resolve** — value-matching never
confirms one.

---

## 1. Coverage summary (bank × quarter)

`%hit` excludes N/A-BY-DISCLOSURE from the denominator.

| bank | quarter | HIT | MISS-STRUCT | MISS-ABSENT | CONTAM | N/A | %hit |
|---|---|---:|---:|---:|---:|---:|---:|
| DBS | 2022-03-31 | 0 | 0 | 0 | 0 | 35 | — (nt=0 doc) |
| DBS | 2022-06-30 | 6 | 4 | 13 | 0 | 12 | 26% |
| DBS | 2022-09-30 | 0 | 0 | 0 | 0 | 35 | — (nt=0 doc) |
| DBS | 2022-12-31 | 0 | 0 | 0 | 0 | 35 | — (no exhibit classified) |
| DBS | 2023-03-31 | 8 | 15 | 11 | 0 | 1 | 24% |
| DBS | 2025-03-31 | 12 | 17 | 5 | 0 | 1 | 35% |
| DBS | 2025-06-30 | 11 | 18 | 1 | 0 | 5 | 37% |
| DBS | 2025-09-30 | 18 | 12 | 4 | 0 | 1 | 53% |
| DBS | 2025-12-31 | 11 | 19 | 0 | 0 | 5 | 37% |
| **DBS** | **2026-03-31** | **29** | **5** | **0** | **0** | **1** | **85%** |
| OCBC | 2025-03-31 | 13 | 6 | 3 | 0 | 4 | 59% |
| OCBC | 2025-06-30 | 0 | 2 | 7 | 0 | 17 | 0% |
| OCBC | 2025-09-30 | 0 | 2 | 7 | 0 | 17 | 0% |
| OCBC | 2025-12-31 | 20 | 0 | 0 | 6 | 0 | 77% |
| UOB | 2025-03-31 | 3 | 19 | 2 | 0 | 4 | 12% |
| UOB | 2025-09-30 | 3 | 18 | 3 | 0 | 4 | 12% |
| UOB | 2025-12-31 | 16 | 0 | 0 | 12 | 0 | 57% |

### The single most important row in that table

**DBS 2026-03-31 scores 85%. Every other DBS quarter scores 24–53% on the same
exhibits, with the same anchors.** The only difference is that 1Q26 is the one
document on the **geometry** branch; all others are `model`.

This is the controlled experiment the whole design rested on, and it came out
in favour of the design: the anchors are right, the documents' hierarchies are
wrong, and geometry is what fixes them. It also means the audit is measuring
hierarchy quality far more than it is measuring anything else.

---

## 2. Defect log — 211 non-hits by root cause

| # | root cause | count | % | layer | owner |
|---|---|---:|---:|---|---|
| RC1 | phantom section-header parent (model branch) | 97 | 46.0% | document | sweep/geometry |
| RC3 | row absent from extraction | 56 | 26.5% | document / disclosure | needs split |
| **RC2** | **anchor parent wrong** | **35** | **16.6%** | **map** | **YOURS — see §3** |
| RC4 | unit / value contamination | 18 | 8.5% | document | sweep + schema |
| RC5 | bank renamed the header | 5 | 2.4% | map (alias) | **YOURS — see §3** |

**RC1 + RC2 = 132 of 211 (62.5%) are parent-chain failures.** Nothing else comes
close. Fixing the hierarchy is the whole game.

### RC1 — phantom section-header parent (97, document defect)
The model branch emits the printed section header as a *row*, which then becomes
the parent of everything under it. Geometry deletes the phantom and lifts the
rows to top level.

```
model  (4Q25): 'Total income'  parent='selected_income_statement_items'   ← phantom
geometry(1Q26):'Total income'  parent=''                                   ← correct
```
Sub-classes: `selected_income_statement_items` ×58, `key_financial_ratios_2_3`
×14, `per_basic_and_diluted_share` ×4, plus the specific DBS_4Q25 mis-nesting of
`of_which_net_interest_income` under `markets_trading_income` (×1) — the exact
row that carries group NII.

**Fixed by re-extraction + geometry. No map change.**

### RC3 — row absent (56)
Concentrated in the older DBS filings (2Q22 ×13, 1Q23 ×11) and OCBC's interim
press releases (1H25 ×7, 3Q25 ×7). **This bucket is NOT yet split between
genuine disclosure cadence and extraction failure** — the harness can only say
"no row with this label in this exhibit". Splitting it needs the PDFs, and for
2Q22/1Q23 the row genuinely may not be printed (DBS's overview exhibit changed
shape between 2022 and 2025). Treat the count as an upper bound on extraction
defects here.

### RC4 — unit / value contamination (18)
Two distinct sub-classes, both real:

- **12 × unit mismatch**: a `percent` concept whose cell carries `S$m`
  (OCBC ×6, UOB ×6 — NIM, CIR, ROE, ROA, NPL, CET1). The row-level unit on the
  parent (`Key financial ratios (%)`) never propagates to its leaves.
- **6 × label carries value fragments** (UOB only): `Total income` →
  `Total income 1`, `Customer deposits` → `Customer deposits 4 25`,
  `Total assets` → `Total assets 5 72`, plus gross loans, shareholders' equity,
  NAV. The geometry stage's clean label ADDS tokens from the first numeric
  column. This is the `352,180` class you flagged.

### RC5 — bank renamed the header (5, map-side)
- DBS 1Q23 prints **`Treasury Markets total income`** where 2025+ prints
  `Markets trading income` (2 anchors affected).
- OCBC 1Q25 prints **`Earnings per share 2/`** → normalizes to
  `earnings_per_share_2`, vs `earnings_per_share` elsewhere (3 anchors).
  Note this is a *footnote* that survived normalization — see §5.

---

## 3. STOPPED — map-side decisions that are yours

Per your constraint I have not touched any `human_confirmed` anchor.

### 3a. RC2 (35) — I authored 5 DBS balance-sheet anchors from the wrong branch
The cleanest possible evidence: DBS_1Q26 is the only trustworthy document, and
**all 5 of its non-hits are this one class**:

| anchor | I authored | geometry says |
|---|---|---|
| `customer_loans` | parent `selected_balance_sheet_items` | parent `''` |
| `customer_deposits` | parent `selected_balance_sheet_items` | parent `''` |
| `total_assets` | parent `selected_balance_sheet_items` | parent `''` |
| `total_liabilities` | parent `selected_balance_sheet_items` | parent `''` |
| `shareholders_funds` | parent `selected_balance_sheet_items` | parent `''` |

I authored the DBS **income** rows from geometry (parent `''`) but the DBS
**balance-sheet** rows from model-branch 4Q25 (parent `selected_balance_sheet_items`).
Inconsistent sourcing on my part — a map bug, not a document bug. The same
inconsistency accounts for the OCBC `FS_INCOME_SELECTED` anchors I gave a
`selected_income_statement_items` parent.

**Proposed fix (needs your approval): re-author those 5 DBS anchors to
parent `''`.** That alone should move DBS_1Q26 from 85% to 100%.

### 3b. RC5 (5) — alias decisions
- Does `Treasury Markets total income` (DBS 1Q23) mean the same as
  `Markets trading income` (DBS 2025+)? If yes it is a map-side alias.
- OCBC `earnings_per_share_2` — this is a *footnote leak*, arguably an
  extraction fix rather than an alias. Your call which layer owns it.

---

## 4. Does anything need the column axis? (the NIM $m-vs-% class)

Yes — **one confirmed case, and the audit found no others among the 89.**

`DBS FS_NII_ANALYSIS / net_interest_income_margin` is the sole surviving
CONFLICT in `bank_line_map`: the corpus stamps `ratio.nim(3)` and
`pnl.nii.net(1)` on the same anchor. The row prints NII in `$m` and NIM in `%`
in *different columns of the same row*. That is two facts in one row, so no row
anchor can resolve it — it needs the concept to vary by column.

It is not a dashboard key field, so it does not appear in the matrix. Flagged
because you asked specifically whether anything else falls in this class. The
RC4 unit mismatches (18) are a *different* problem — one fact, wrong unit
stamped — and are fixed by unit propagation, not by a column axis.

---

## 5. Proposed extraction-schema pivot (your mid-turn instruction)

**PIPELINE PIVOT — not yet implemented, needs go-ahead.** Per CLAUDE.md this
changes the Gemini extraction schema and prompt, so it must be recorded in the
routing spec and be visible in the route manifest before it lands.

Per row, emit three fields instead of one string:

| field | rule |
|---|---|
| `raw_text` | verbatim cell. **Never** used for matching or values — provenance only, so a defect is diagnosable without reopening the PDF. |
| `label` | normalized: footnote markers, superscripts and period tokens stripped. This is what the parent-chain anchor matches. Extends decision #1's table-level rule to rows: `Return on equity⁴,⁵` and `Return on equity` must produce the same path. |
| `footnote_refs[]` | markers captured as structured metadata, never discarded. Footnote bodies extracted once per table and linked by ref. |

**Why `footnote_refs` earns its place** (this is the strongest part of the
proposal and it is already evidenced in this audit):
- DBS footnote 2 *is* the definition of the underlying basis (excl. Citi/CSR) —
  exactly what `bank_line_map.basis` encodes. Today that linkage is implicit.
- DBS footnote 8 documents the retrospective bonus-share adjustment to EPS — a
  restatement flag that is otherwise invisible.
- Footnote appearance/disappearance across quarters is a change-detection signal
  the corpus already contains and currently throws away.
- **RC5 above is a footnote leak**: OCBC's `Earnings per share 2/` breaks the
  anchor purely because the marker rode into the label. Structured refs make
  that class impossible rather than regex-dependent.

**Value cells: validation, not sanitation.** A value must parse as a clean
numeric with its unit from the column axis. Residual non-numeric characters →
`CONTAMINATED`, failing loud for geometry or a human. **No regex pass ever
"fixes" a value cell** — the cell-level twin of the value-matching rule:
extraction may propose a parse, only structure confirms it. This directly
prevents RC4's 6 UOB label-contamination cases from silently recurring, and
FS highlights tables are dense with footnotes so the exposure is broad.

---

## 6. Recommended sequence (no spend yet)

1. **Free, first:** approve the RC2 anchor re-authoring (§3a) and re-run the
   audit. Expected: DBS_1Q26 → 100%, and the RC2 bucket (35) clears corpus-wide.
2. **Free, second:** the geometry **scan re-anchoring** fix already queued in
   PROGRESS.md. Geometry currently matches DBS 4/4 but UOB 28/44 and OCBC 4/41,
   because one miss on a repeated label strands every following row. Raising
   geometry coverage *before* paying for re-extraction is what makes the sweep
   worth its cost — RC1 is 46% of all defects and geometry is its only fix.
3. **Then spend:** the re-extraction sweep, with the §5 schema pivot in the
   prompt. Doing it before steps 1–2 would re-derive JSON that still lands on a
   weak hierarchy and would need re-running.

Estimated post-fix ceiling: RC1 (97) + RC2 (35) = **132 of 211 non-hits (62.5%)
addressed by hierarchy work alone**, most of it free.
