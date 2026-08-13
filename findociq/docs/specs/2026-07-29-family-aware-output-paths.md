# Spec — family-aware output paths and document labelling

Status: proposed
Date: 2026-07-29

## Problem

The routing `family` (`pillar3` | `fs` | `slides` | `other`, decided in
`classify/family.py` and consumed by `run_doc.py:358`) **never reaches pass2**.
`grep family` across `pipeline/pass2/` hits nothing but a comment. Every
document is therefore labelled and filed as Pillar 3:

| site | current value |
|---|---|
| `pass2/schema.py:28` | `_P3_ROOT = .../outputs/pillar3` |
| `pass2/schema.py:40` | `self.xlsx = run_dir / f"{doc_stem}_pillar3.xlsx"` |
| `pass2/schema.py:42` | `..._pillar3.index.json` |
| `pass2/schema.py:70` | `DOC_TITLE = "Pillar 3 Disclosures"` |
| `pass2/workbook.py:452` | Contents banner ← `DOC_TITLE` |
| `pass2/workbook.py:374` | every sheet's source line ← `DOC_TITLE` |

Observed: `DBS_1Q26_trading_update` — a `family=fs` document — produced
`1Q26_trading_update_pillar3.xlsx`, banner "Pillar 3 Disclosures", and a source
line on every sheet claiming Pillar 3 provenance. The workbook misrepresents
what it is.

`PASS2_v2.py:133-142` already reassigns `INSTITUTION`, `BRAND_COLOUR` and
`DOC_DATE` at runtime — `DOC_TITLE` was simply never added.

## THE TRAP — do not move the existing root

Naively repointing `_P3_ROOT` at `outputs/{family}/` breaks four things:

1. **640 tracked files** live under `findociq/outputs/pillar3/**/audit/`. These
   are the committed `parsed.json`/`meta.json` replay evidence — PIPELINE.md
   calls them "the $0 DB-replay source".
2. **`.gitignore:60-63`** ignores `findociq/outputs/**` and whitelists exactly
   `!findociq/outputs/pillar3/**/audit/**/parsed.json|meta.json`. A new root is
   not whitelisted, so its audit evidence would be silently ignored.
3. **`run_doc.py:54,123,557`** — `P3_ROOT` and `find_audit_root` glob only
   `outputs/pillar3/*/audit/*`. STEP 3 would fail to find any new-root audit dir.
4. **`pass2/test_load_v7.py:29,639`** hardcode `outputs/pillar3/dbs_2Q25/...`.

## Design — parameterize with `pillar3` as default

No migration. No file moves. Existing Pillar 3 behaviour must stay
**byte-identical**; only non-pillar3 families get new paths.

1. **Family reaches pass2 explicitly.** Add `--family` to `PASS2_v2.py`.
   `run_doc.py:413` passes the family it already classified, so the router stays
   authoritative and the decision is visible in the command line. When the flag
   is absent (standalone runs), PASS2 self-classifies via `classify/family.py`
   rather than assuming pillar3.
2. **Single family→label map** in `schema.py`:
   `{"pillar3": "Pillar 3 Disclosures", "fs": "Financial Statements"}`.
   Unknown family → fall back to `pillar3` behaviour AND print a visible note.
3. **Paths become** `outputs/{family}/{bank}_{period}/{doc_stem}_{family}.xlsx`.
   For `family="pillar3"` this is character-for-character what it is today.
4. **`DOC_TITLE` set at runtime** in `PASS2_v2.py` alongside `INSTITUTION` /
   `BRAND_COLOUR` / `DOC_DATE`, which already work this way.
5. **`find_audit_root`** globs every family root, not just pillar3.
6. **`.gitignore`** gains the parallel whitelist for the `fs` root so FS audit
   evidence is tracked on the same terms as Pillar 3's.

## Companion — sheet-name truncation

`sheet_name` (`workbook.py:385-397`) and `table_sheet_name` (`:400-412`)
truncate to Excel's 31-char limit with a bare `[:31]` **after** normalizing
whitespace but **without** `.rstrip()`. Where the cut lands depends purely on
section-id length, producing the observed inconsistency:

- `selected_income_statement_items` — exactly 31, clean
- `selected_balance_sheet_items_m ` — cut at the space before the title
- `key_financial_ratios_2_3 Table ` — amputates the `1` of `Table 1`
- `per_share_data_3 Table 1` — 24 chars, intact

Fix: `.rstrip()` after truncation, and truncate the base *before* appending a
`Table N` suffix so the suffix is never amputated.

## Regression requirement (binding)

A test must prove that for `family="pillar3"` the run dir, xlsx name, index
name, audit dir, `DOC_TITLE`, banner and source line are **identical to
pre-change**. The 640 committed artifacts and `test_load_v7.py` must be
untouched and still pass.

## Non-goals

- No migration of existing `outputs/pillar3/` content.
- No change to extraction, prompts, or the validators.
- `slides`/`other` never reach pass2 (`run_doc.py:523` exits early); they are
  out of scope beyond the unknown-family fallback.
