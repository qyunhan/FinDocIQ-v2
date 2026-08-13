# OCBC 4Q25 Condensed Financial Statements — spoilt artifacts

Archived 2026-08-06. **Not deleted** — everything here is intact and restorable
with the commands at the bottom.

## Why these were pulled

`OCBC_4Q25_Condensed_Financial_Statements` was ingested **2026-07-29 10:25**, one
day before family-aware output paths landed. The doc-family router classified it
correctly the whole time — re-checked today:

    classify(OCBC_4Q25_Condensed_Financial_Statements.pdf)
      -> family='fs'  confidence='high'  subtype='full'  flags=''

but `pass2/schema.py` hardcoded `_P3_ROOT = outputs/pillar3`, so the family
decision never reached pass2 and every document was filed and labelled as
Pillar 3. This is the exact defect described in
`docs/specs/2026-07-29-family-aware-output-paths.md`, whose own worked example
(`DBS_1Q26_trading_update`) is the same shape.

Consequence: the artifacts sit under `outputs/pillar3/`, and the workbook carries
a "Pillar 3 Disclosures" banner and per-sheet source lines for what is an OCBC
financial statement. **The extracted cell values themselves are unaffected** —
only the filing location and the labelling are wrong.

The fix has since shipped (`resolve_family()` in `pass2/schema.py`, routing at
`run_doc.py:378-394`), so a re-ingest lands in `outputs/fs/ocbc_4Q25/`.

## Open question, deliberately not answered here

`run_doc.py:389` branches the **TOC framework** on family — `pillar3` uses the
deterministic `pass1_toc`, everything else uses the Gemini `toc_stage`. Whether
that branch existed on 2026-07-29 was never verified. If it did, this doc got the
correct Gemini TOC and only the filing is stale. If it did not, the run also used
the wrong TOC framework and the artifacts differ in substance, not just location.
The presence of `_toc_raw.json` (a Gemini output) suggests the former, but that
was not confirmed. **Check this before trusting a replay from these files.**

## What is here

| path | contents |
|---|---|
| `outputs/pillar3/ocbc_4Q25/audit/OCBC_4Q25_Condensed_Financial_Statements/` | 23 audit units (the Gemini extraction — `parsed.json` per unit) |
| `data/derived/toc/OCBC_4Q25_Condensed_Financial_Statements_toc.json` | cached TOC (STEP 1) |
| `data/derived/toc/OCBC_4Q25_Condensed_Financial_Statements_toc_raw.json` | raw Gemini TOC response |
| `data/derived/paddle_scans/OCBC_4Q25_Condensed_Financial_Statements/` | PaddleOCR scan (STEP 0) |

67 files, 1.1 MB.

The 23 units are what makes a **zero-cost replay** possible — the same mechanism
used on 2026-08-04 to prove the end-to-end path without spending on Gemini
(`docs/DECISIONS.md`, 2026-08-04 entry). Deleting them for real would mean a paid
re-extraction, and `--dry-run` is currently ignored for a single `--pdf` (D5), so
there is no cheap dry-run safety net. That is why this is an archive, not a `rm`.

## Also cleared

`db/compiled_v2.db` — 3,329 rows for this doc removed (2,377 `cell_fact`, 628
`row_dim`, 210 `col_dim`, 72 `section`, 41 `table_t`, 1 `document`); 25 documents
remain. Backup at `db/compiled_v2.db.bak-before-ocbc-purge`. The same data is
still intact in `db/compiled_2q26.db` and `db/compiled_fs.db`.

## Restore

Run from the repo root (`/home/user/FinDocIQ`):

```bash
A=findociq/archive/2026-08-06-ocbc-4q25-condensed-spoilt
DOC=OCBC_4Q25_Condensed_Financial_Statements

git mv "$A/outputs/pillar3/ocbc_4Q25/audit/$DOC" \
       findociq/outputs/pillar3/ocbc_4Q25/audit/$DOC
git mv "$A/data/derived/toc/${DOC}_toc.json"     findociq/data/derived/toc/
git mv "$A/data/derived/toc/${DOC}_toc_raw.json" findociq/data/derived/toc/
git mv "$A/data/derived/paddle_scans/$DOC"       findociq/data/derived/paddle_scans/$DOC
```

Restore the DB rows by rebuilding the serving DB from a source that still has
them:

```bash
findociq/../.venv/bin/python findociq/tools/build_compiled_v2.py \
    --src findociq/db/compiled_2q26.db --dst findociq/db/compiled_v2.db
```

or simply `cp db/compiled_v2.db.bak-before-ocbc-purge db/compiled_v2.db`.
