# Masterlist v3 — reference-set authority + banner rule

**Date:** 2026-08-06 · **Branch:** `mapping/masterlist-registry`
**Output:** `data/derived/masterlist_proposed_v3/` (v2 and the original untouched)
**Source:** `db/compiled_v2.db`, read-only. No curation applied.

---

## Headline

| metric | v2 | v3 |
|---|---|---|
| **Row tagging coverage** (value-bearing rows given a canonical leaf in place) | — | **1,705 / 1,710 = 99.7%** |
| Ghost-ancestor leaves (id segment absent from its own hierarchy) | 330 | **41** |
| Date-segment leaves (a date used as identity) | 56 | **0** |
| DBS `markets_trading_income::` phantom prefix | 15 | **2** (both legitimate) |
| DBS `FS_INCOME_SELECTED` | 21 | **21** — no regression |
| Leaves | 1,613 | 1,246 |

**5 untagged rows corpus-wide** (OCBC 1, UOB 4), each named in `untagged_rows.csv`.

`total_income::of_which::net_interest_income` — v2 carried this as
`markets_trading_income::total_income::of_which::net_interest_income`.

---

## Pre-flight findings

### P1 — the equity col_axis is inverted, and not fixable as predicted

Equity components **are** columns in all three banks, both GROUP and COMPANY;
the rows are movement line items (`Purchase of treasury shares`, `Net profit`,
`Balance at 31 December 2024`). The seed declares `col_axis=period`,
`row_dim_axis=equity component` — exactly backwards.

But the correction is **not** `period+equity_component`: `col_period` is NULL on
every column and the period comes from `table_t.period` (2025-12-31/FY, derived
from the title). The right declaration is `col_axis=equity_component`,
`row_dim_axis=movement`.

Per your decision, v3 derives the axis from extraction and emits `col_members`.
**16 seed col_axis mismatches** are logged in `col_axis_mismatch.csv` for the
follow-up curated seed edit:

| bank | table | declared | extracted (hard axis) |
|---|---|---|---|
| DBS | FS_EQUITY_CHANGES_GROUP | `period` | non_controlling_interests, other_equity_instruments, … |
| DBS | FS_VOLUME_RATE | `period+measure` | rate, volume |
| DBS | FS_BALANCE_SELECTED[panel_1] | `period` | hong_kong, rest_of_greater_china, … |
| OCBC | FS_NPA_COVERAGE[panel_1] | `period+geo` | doubtful, loss, substandard, npls, npl_ratio |
| OCBC | FS_ASSET_QUALITY | `period` | qoq, yoy |

**Also found, flagged as blocking for the STAMPING pass** (not for v3): DBS p32
Group prints comparative panels `(2024)` and `(2025)` and **both carry
`period=2025-12-31`**. v3 emits identities and both panels have identical leaf
sets, so the registry is correct regardless. But the moment facts are stamped
(§3.4 era), two panels both claiming 2025-12-31 collide on the same
`(leaf, period)` key — duplicate or overwritten facts. p34/p35 (Company) got the
period right, so this is Group-panel-specific.

### P2 — reference sets derive cleanly from `doc_kind`

    DBS   performance_summary                -> DBS_4Q25_performance_summary
    OCBC  condensed_financial_statements     -> OCBC_4Q25_Condensed_Financial_Statements
    OCBC  media_release_financial_highlights -> OCBC_4Q25_Media_Release_and_Financial_Highlights
    UOB   condensed_financial_statements     -> UOB_4Q25_condensed-financial-statements

OCBC's two-document reference set fixes the `Q4_DOCS` bug directly.

**Scope-key substitution (noted, not silently done):** the spec asked for
`(bank, section_canonical, normalized_caption)`. The extracted `section` table
carries document sections, not the seed's canonical names. `section_canonical`
partitions cleanly by `doc_kind` within each bank (verified — no section spans
two doc_kinds), so `doc_kind` scoping is a strict, derivable coarsening that
gives the same media-release-vs-condensed-FS disambiguation.

---

## What the build does

**Hierarchy anchors on the captured chain.** `row_dim.row_parent` — already
carrying the printed-parent precedence consumed since 01151d1 — is the base
hierarchy. The banner rule is a **repair**, supplying a parent only where the
chain leaves a row orphaned. An earlier iteration re-derived ancestry from
scratch and was wrong in both directions (DBS `FS_PERF_BY_SEGMENT` 45 rows → 9
leaves; OCBC NPA 18 → 43); anchoring fixed it.

**Structure guard.** Reference tables are grouped by **column signature**
(breakdown discriminator + hard-axis column vocabulary) and each group is built
independently. Rows never cross a signature boundary, so one registry entry can
never take items from a differently-shaped table — the failure mode that let v2's
`FS_BALANCE_SELECTED` absorb PERFORMANCE BY GEOGRAPHY rows. 19 blocks carry a
`sub_table` key.

**Sub-tables become banner ancestors.** `NON-PERFORMING ASSETS (continued) —
NPLs by Industry` resolves to `FS_NPA_COVERAGE` with `by_industry` injected as
the outermost banner, so OCBC's separately-captioned sub-tables and DBS's
banner-row form produce the same id shape.

**Periods are never identity.** Two classes carry period and contribute no id
segment: `PERIOD_BANNER` (valueless date row) and `PERIOD_ROW` (a *valued* date
row — UOB/OCBC print `Dec-25` as a leaf under a geography). This is what took
date-segment leaves to 0.

### Normalisation — three rules, none moonlighting

One forced change, and it is the root cause of two v2 defects:

> `_TRAILING_FOOTNOTE` gained a `(?<!\d)` lookbehind. Without it the 1-2 digit
> cap was no protection — the engine matched the *tail* of a longer run, so
> `2024` lost `24`, then `20`. That one rule was doing all three jobs: it gave
> `balance_at_1_january` (right answer, wrong reason) and fused
> `31 Dec 2021/2022/2024/2025` into a single `31_dec` identity (wrong answer),
> and it destroyed every date before classification could see it.

Now separate: **rule 1** footnote markers; **rule 2** trailing year only (day and
month are printed identity — opening vs closing balance); **rule 3**
`is_period_label`, classification only, which deliberately does not call rule 1.

19 tests in `pipeline/mapping/test_masterlist_derive.py`, all green.

---

## Gates — 70 pass / 23 fail

Gates are **reported, never silent, and a failing table still emits its leaves**
stamped `gate_status: FAIL` in the YAML. Withholding them would lose the correct
leaves along with the incorrect ones.

| outcome | n |
|---|---|
| PASS | 70 |
| NO_REFERENCE_TABLE | 15 |
| G1 ghost-ancestors | 4 |
| G4 suffix-collapsible | 4 |
| G3 untagged rows | 4 |
| G2 date segments | **0** |

G3 was reframed to your definition — **a failure is a value-bearing row that
could not be tagged with a canonical leaf in its place**, not a leaf-count delta
against the printed row count (that proxy fires on healthy tables whose panels
legitimately share ids).

The 15 `NO_REFERENCE_TABLE` are seed/caption gaps, not derivation failures —
e.g. DBS prints `Fair Value Hierarchy` where the seed names `Fair Value
Measurement`. Per your rule these gate-fail and are **never** fed to the HY
fallback, so no partial document mints a canonical id.

---

## v2 → v3 diff

| bank | v2 | v3 | gone | new | kept |
|---|---|---|---|---|---|
| DBS | 644 | 414 | 308 | 78 | 336 |
| OCBC | 603 | 483 | 231 | 111 | 372 |
| UOB | 366 | 349 | 49 | 32 | 317 |

The drop is the point: v2 unioned every period as an equal source, so each
vintage's mis-parented variant became its own leaf. v3 mints only from reference
documents; other periods land as sorted aliases (alias ordering is now
deterministic — the v2 nondeterminism is fixed) or in the review queue.

**Review queue: 425 entries**, each with an ordinal-position suggestion
(`review_queue.csv`).

**Curated aliases: all 5 targets still exist in v3, 0 need re-pointing.** The 2
surviving `markets_trading_income::` leaves are exactly the ones they point at.

---

## Deliverables

    data/derived/masterlist_proposed_v3/
      masterlist_registry_{DBS,OCBC,UOB}.yaml   78 blocks, 1,246 leaves,
                                                20 with col_members, 19 sub_table
      gate_report.csv          per table: G1-G5 with numbers + coverage %
      untagged_rows.csv        the 5 rows that could not be tagged
      review_queue.csv         425 non-reference rows + suggestions
      unresolved_captions.csv  60 reference tables matching no seed caption
      col_axis_mismatch.csv    16 seed col_axis vs extraction mismatches

    pipeline/mapping/masterlist_derive.py       shared module (seeder + resolver)
    pipeline/mapping/test_masterlist_derive.py  19 tests

**Caveat on Phase-2 zero-unresolved:** seeder and resolver share
`masterlist_derive`, so a zero proves registry coverage and scoping, **not**
resolver correctness. The real test is an unseen document (DBS 2Q26).

## Not done in this pass

1. The 15 `NO_REFERENCE_TABLE` seed/caption gaps — need seed captions or aliases.
2. 41 residual ghost-ancestors and 4 suffix-collapsible tables.
3. The seed `col_axis` correction (16 rows) — a curated seed edit, separate commit.
4. The DBS Group panel period bug — **blocks the stamping pass**.
5. Sub-tables as first-class seed vocabulary.
