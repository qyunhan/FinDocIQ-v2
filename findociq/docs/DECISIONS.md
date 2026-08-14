# DECISIONS — why we did what we did (and what we discarded)

Decision log toward a full project writeup. **Newest on top.** One block per
decision. Every block records: the **Decision**, **Why**, and — when something was
tried and dropped — **Discarded** with the **evidence** for rejecting it.
This is deliberately more than a status log (that's `PROGRESS.md`): it captures
rationale and dead ends so the final documentation can explain not just what the
system does but why it is shaped this way.

Format:
```
## YYYY-MM-DD — <short title>
**Decision:** what we chose.
**Why:** the reason.
**Discarded:** <alternative> — why rejected + evidence (a command output, a file:line, a measured fact).
```

---

## 2026-08-14 — the committed DB was double-counting; the rebuild was the fix

**Decision:** promote a fresh `--rebuild-db` replay to be the committed
`compiled_fs.db` / `compiled_v2.db`, reversing the standing assumption that the
committed artifact was the better one.

**Why:** six cells of the live loans dashboard were serving **exactly double**.
OCBC Singapore read 298,172 against a filed 149,086; the geography block summed
to 682,240 against a filed gross of 341,120. The cause was 39 `canonical_leaf_id`
stamps that existed only in the committed artifact, all on DUPLICATE extractions
of one page in `OCBC_4Q25_Media_Release_and_Financial_Highlights`. The anchor
composition sums its members, so a leaf stamped on two copies of one page is
added twice. After the rebuild every breakdown — geography, industry, maturity,
business unit — reconciles to 341,120 exactly.

**Discarded:** *"the replay loses 39 leaves, so the committed DB is better".*
This was TO_FIX §5's framing and it was wrong in the most expensive way: it
treated the artifact's extra stamps as value and would have had us re-apply
them. The stamps were the defect. What settled it was arithmetic no count could
give — the parts of a breakdown must sum to its own declared total.

**Discarded:** *chasing non-determinism in the classifier.* Measured first: two
independent rebuilds are byte-identical on row identity AND table
classification, 0 differences. The arbiter's tie-break is already deterministic
(`-fraction, -matched, table_type_id`). The divergence was artifact-vs-current-
code, not run-to-run, so there was no randomness to fix.

**Evidence the pipeline could not catch it:** counts identical (the duplicate
rows exist in both), `verify_cells` 10/10 PASS (each value IS on its page —
verification is per-cell, double counting is a property of composition), anchor
coverage 166/166 both ways, and 298,172 is not obviously absurd until you add
the column up.

**Method note, recorded because it cost real time:** the first attempt at this
diagnosis ran `--rebuild-db` with no `--db`, which overwrote the shipped DB. Only
a copy taken beforehand saved it. `--db <tmp>` has always been supported — I had
grepped for it, seen only the internal subprocess call sites, and wrongly
reported that no such flag existed. Read the argparse block, not the call sites.

**Still open:** the duplicate tables are still extracted and still carry NULL
`dedup_status`. They are now merely classified consistently enough not to double
count. The durable guard is a sum-to-total invariant per breakdown, which is not
implemented. Full write-up: Appendix E of the technical report.

---

## 2026-08-14 — the Highlights row order, and the formula file nobody was reading

**Decision:** the headline dashboard's formula file is renamed
`highlights_formulaanchors.csv` -> `highlights_dashboard_formulaanchors.csv`;
`available_dashboards` gains `orphan_formula_files()` and the view WARNS on any
stem mismatch; and the per-bank item lists are merged by declared `row_order`
(`merge_dashboard_items`) instead of being appended in bank order.

**Why:** the reported symptom was that the Income statement block did not start
`Net interest income, Net fee and commission income, Other non-interest income`.
Two independent causes, one masking the other.

1. **A dashboard is a PAIR sharing one stem, and this pair did not.**
   `load_dashboard_anchors` builds `<stem>_formulaanchors.csv`. With the
   headline stem `highlights_dashboard` it looked for
   `highlights_dashboard_formulaanchors.csv` and found nothing, so every
   composed line was dropped without a message. DBS lost `Net interest income`
   and `Other non-interest income` entirely (both rollups) — measured:
   `load_dashboard_anchors('DBS', dashboard='highlights_dashboard')` returned
   24 items against 26 after the rename. It stayed invisible because the
   no-argument path globs `*_formulaanchors.csv` and loads the file, so tests
   and any single-pair install behaved perfectly. It only bites once a SECOND
   pair exists and the picker starts passing a stem — which shipped with
   `breakdown_of_gross_nb_loans`.

2. **The union appended.** The grid shows one row list for every bank, built by
   adding each bank's unseen labels to the end. Banks are read alphabetically,
   so DBS set the order and anything only OCBC or UOB declared landed after
   every DBS row — three sections below where it belonged. And because
   `highlights_grid_frame` emits each section header at most once, such a row
   printed with no header at all. With DBS's list truncated by (1), that is
   exactly where `Net interest income` and `Other non-interest income` went.

**Discarded:** *teaching the loader to accept the mismatched name* (fall back to
`highlights_formulaanchors.csv`). Rejected — it entrenches an ambiguity the
whole set-based design exists to remove, and the next pair would inherit it. The
contract is one stem per pair; the fix is to honour it and to make a violation
visible rather than silent.

**Discarded:** *fixing only the rename.* It would have restored the observed
order, because DBS then declares every line and DBS is read first — and left
the append bug latent for the first line a future bank declares alone.

**Verified:** OCBC's grid now reads Net interest income / Net fee and commission
income / Other non-interest income (ᵈ) / Total income, and two formula lines
absent from the page for the whole life of the two-pair install —
`Other non-interest income` and `Shareholders' equity` — are back. 162 app
tests; the live dashboards directory is asserted orphan-free.

---

## 2026-08-14 — a footnote column was a servable figure; `reference_skip` and the col-id regression

**Decision:** new `col_role` value **`reference_skip`** for columns that carry a
cross-reference rather than a measurement, and the col_role vocabulary moves to
`stage3_stamp/resolve/col_roles.py` so the loader and `apply/restamp_columns.py`
share ONE definition. `resolve_canonical_col`'s gate becomes "any non-NULL role"
instead of `== 'derived_skip'`. Separately, `canonical_col_id` was re-stamped
into `compiled_fs.db` and `compiled_v2.db` rebuilt from it.

**Why:** OCBC's consolidated income statement prints `Note | 1H 2026 | 1H 2025`.
The Note column has no period of its own, so the period cascade fell through to
`table_title` and stamped its cells `2026-06-30 / 1H` — the SAME
(leaf, period, span) address as the real figure. With `canonical_col_id`
unpopulated that address is the finest grain the dashboard can match on, so the
dedupe tie-break picked the footnote index. Measured on the shipped DB: net
interest income rendered **3** against a filed 4,486, fee income **4** against
1,414, allowances **7** against −665. Ten anchored addresses, two documents.
After the fix: 0.

**Why `canonical_col_id` was 0 of 1915 at all:** a regression, not missing work.
TO_FIX §5 records that `restamp_columns` had written it (56 -> 197) directly
into the BUILT artifact, and warned "they will drift the moment either changes."
Commit 051c32b rebuilt the DB and the patch was lost, because the stamping lived
in the artifact and not the lineage. Fixed at the source this time:
`compiled_fs.db` restamped, `compiled_v2.db` rebuilt from it (which CARRIES
`canonical_col_id`/`col_role`), so the next rebuild keeps it. 186 columns.

**Discarded:** *`col_period IS NULL` as the predicate.* This was the obvious
rule and it is wrong. A hard-axis table's VALUE columns legitimately have no
period of their own — UOB's 'Performance by Geographical Segment' prints
`Singapore | Malaysia | ...` as banners and takes its period from the title,
which is why the cascade exists at all. Periodlessness cannot separate a
reference column from a geography column; only what the column IS can, which is
what `col_role` encodes.

**Discarded:** *matching `\bnote\b` anywhere in the label.* It would claim
'Notes receivable' and 'Notes and coins', both real line items banks report.
The regex is anchored to the whole label plus an optional short index
('Note (a)', 'Note no.'). Swept the live corpus: it claims exactly the 2 real
Note columns of 1,915 and nothing else.

**Discarded:** *patching `_ANCHOR_SQL` to exclude a label named Note.* That is
the per-source hack the project forbids — the serving query already allowlists
`col_role IS NULL`, so the correct fix is to give the column its role upstream
and let the existing gate do the work. No app change was needed at all.

**Verified:** the rebuild is content-identical to the shipped artifact except
the two intended fields — same row counts on all 9 tables, 0 cells and 0 row
identities differing either way.

---

## 2026-08-14 — the app reads canonical_leaf_id and nothing else; three of four views were dead in public

**Decision:** the Streamlit app's ONLY identity join is `canonical_leaf_id`,
declared by the dashboard anchor CSVs. `table_catalog`, `bank_line_map`,
`row_lineage`, `v_fact_metric_serving` and `v_cell_flat` are removed from the
read path entirely, and the Table Registry is rebuilt on
anchors x `row_dim.canonical_leaf_id`. Four crash/blank causes fixed alongside.

**Why:** `compiled_v2.db` carries nine tables and zero views by design. Every
read of the retired mapping layer therefore returned nothing (`run_opt`) or
raised (`run`). Measured on the shipped DB before this change: only the
Dashboard rendered. Database raised `no such column: row_leaf_label_clean` the
moment any table was picked; Table Registry showed only "run
migrate_add_table_catalog.py"; Ingest raised `ModuleNotFoundError: source_store`
**on page load**, because it is the first radio option and its pipeline import
was unguarded — so a fresh session was a sidebar plus a traceback.

**Why the registry is anchor-keyed:** the Dashboard already resolves figures by
(bank, table_type_id, canonical_leaf_id) against the anchor CSVs. Building the
registry on the same declaration and the same stamped column means the two views
cannot disagree about what an address means, and dropping a new anchor pair into
`data/derived/dashboards/` extends BOTH with no code change. First run: 159
declared anchors, 156 captured, 3 uncaptured (all OCBC), plus 1,189 stamped
addresses no dashboard declares yet. It immediately surfaced a real
near-miss — OCBC `FS_CUSTOMER_LOANS / net_loans` is declared-but-never-captured
while `FS_CUSTOMER_LOANS / allowances::net_loans` is captured 10x.

**Discarded:** *hardcoding the compiled_v2 column list* in the queries that
raised. Rejected — it is the per-source special case CLAUDE.md forbids, and it
inverts on the next schema change. `select_clause` probes `PRAGMA table_info`
and serves absent columns as `NULL AS <name>`, keeping frame shapes fixed, so a
restored column starts serving again with no edit.

**Discarded:** *`run_opt` on the failing queries.* Rejected on evidence — the
failure is `no such column`, which is raised by a query against a table that
very much exists, so a table-level fallback would have blanked working views
(the whole `_raw_frame` reconstruction) to paper over three columns.

**Discarded:** *hiding `canonical_col_id` until it is populated.* It is
`0 of 1915` in BOTH `compiled_v2.db` and `compiled_fs.db` — the column-axis
stamp (spec 2026-08-09) has never run. Rejected because the user asked to see
the per-cell address and an absent half is a fact about the pipeline worth
showing; the view states the coverage instead, and lights up when the stamp
lands.

**Discarded:** *a per-document map for the unresolvable PDFs.* `source_file`
holds two key conventions and 3 of 10 documents use the foldered one. A basename
fallback under `data/sources/` is one general rule that resolves all 10,
including the Pillar 3 PDF that lives in a different subfolder
(`sources/pillar3/`) than its recorded path claims.

---

## 2026-08-13 — period banners: the discriminator is valueless-ness, not span

**Decision:** a cell with no period on either axis inherits from the nearest
preceding **period BANNER** in its table — a new `row_banner` rung between the row
axis and `table_title`. A banner is `row_type in (section_header, sub_header)` with
a parseable period and **no values of its own**.

**Why:** the row rung only walked ANCESTORS. Banks stack period blocks vertically
and the model emits the banner at the same level as its rows, so
`row_parents_by_position` (parents strictly by `level - 1`) gives them one shared
parent and the walk finds nothing. DBS_4Q25 `PERFORMANCE BY BUSINESS SEGMENTS`
parsed all five banners correctly and still stamped all 225 cells `doc_period`,
135 of them wrong. **`verify_cells` passed on every one** — the values match the
PDF; only the dates are wrong. This class of defect is invisible to cell
verification, which is why it survived this long.

**Discarded — a span whitelist (`1H`/`2H`/`FY`).** It was my first design and it is
wrong. Evidence: UOB_4Q25 `Classification of Financial Assets … Dec 24` heads its
block with `Dec 24`, span `as_at`, and a span filter leaves all **105 cells** — a
whole Dec-2024 balance sheet — stamped 2025-12-31. Of the 70 valueless period rows
in the corpus, 28 carry `as_at` and scope real blocks. Valueless-ness is also the
structurally safer signal: `section_header`/`sub_header` is exactly the membership
that guarantees the row emits no `cell_fact`, so "is a banner" and "owns values"
are exclusive **by construction**, not by measurement (87/87 and 121/121 over every
`parsed.json`).

**Discarded — my own detector as the audit tool.** "Rows print more distinct
periods than the cells received" found 21 tables / 1,850 cells but is structurally
blind to the UOB case, where printed and resolved *counts* are both 1 and only the
value is wrong. Audit by comparing each cell to its nearest banner, never by
comparing counts.

**Discarded — restore-on-pop unwinding at a shallower DATA row.** Intuitive, and
caught only because a unit test failed: UOB's banner sits at level 1 while its data
rows sit at level 0, so unwinding on a shallower data row pops the banner before
any row can read it and re-breaks the case the rung exists to fix. The stack
unwinds on a new BANNER only; a deeper block therefore stays live to the end of the
table. Accepted — banners are per-table and no table in the corpus nests them.

**Discarded — folding the banner into the row variable (`rp`).** The both-axes
warning compares a col period against the row's own period; folding an inherited
banner in changes what that warning means and would fire it on every banner table.
0 cells today have both a col period and a live banner, so it would be silent now
and noisy later. Kept in its own variable.

**`period_source='row_banner'`, not reused `'row'`.** `schema_v7.sql` says the
column exists so an inherited period can be told from a printed one; folding 1,910
inherited cells into `'row'` destroys exactly that and makes the reload
unverifiable. No `CHECK` constraint, so no migration.

**Companion fix:** `clamp_bare_year_to_doc_period` was applied to columns and
titles but never to rows, so `'2026'` banners resolved to 2026-12-31 — **141 cells
with a period later than their own `doc_period`**. Threading `doc_period` into the
pre-pass mattered too: clamping only in the row loop still left 7 cells of DBS 2Q26
`Balance at 30 June 2026` at 2026-12-31/FY, found by the acceptance query, not by
review.

**Measured on the live DB after backfilling 4 documents:** 771 cells changed
period, 468 gained a span, `period > doc_period` 141 → 0, verify PASS throughout.
Two OCBC documents re-loaded as a control showed **zero delta**, confirming valued
`as_at` balance rows never scope their movement rows.

---

## 2026-08-13 — `--stage1/--stage2/--stage3`: the default must stay {1,2}, not {1,2,3}

**Decision:** `run_doc` gains three composable stage flags mirroring `pipeline/`'s own
layout. **No flag selects stages {1,2}** — a bare `--pdf` run stays exactly what it has
always been. Stage 3 (`build_compiled_v2` -> compiled_v2.db) is opt-in.

**Why {1,2} and not {1,2,3}:** `run_doc` has never built compiled_v2.db —
`grep -n compiled_v2 run_doc.py` returned one COMMENT and no call site. Adding it to
the default would have been a new behaviour smuggled in under a refactor, and
`build_compiled_v2.py:186` **unlinks its `--dst`** before rebuilding, so a default that
included stage 3 would silently delete the app's serving DB on every document run.

**Why the stage names are half-wrong, and why they were kept anyway.** "Stage 3 =
stamping" does not describe what happens: row identity (`row_dim.canonical_leaf_id`,
`table_type_id`) is written by `load_v7.py:2199` during **stage 2's load**;
`build_compiled_v2` only carries those columns across. Renaming the stages would have
diverged them from the `stage3_stamp/` package they mirror, so the names stand and the
trap is documented at the top of `run_doc.py` instead. Practical consequence, worth
knowing before debugging an unstamped row: **`--stage3` alone can never change what is
stamped** — that needs `--stage2 --stage3`.

**Also not clean, also kept:** stage 1 writes to the DB. STEP 1's `toc_to_db` seeds the
document's `section` rows, so "stage 1 = files, stage 2 = DB" is not literally true.
Pre-existing behaviour; splitting it would have violated the all-else-constant
constraint. Instead, stage 2 run without stage 1 re-seeds sections from the cached TOC
when `document_exists()` is false — the same guard and the same subprocess
`--verify-only` already uses, rather than a second mechanism.

**Verified by running all five combinations**, not by tests alone (OCBC 2Q26 media
release): default = 51/34/572/1978 verify PASS with no `stages` line printed;
`--stage2` = 11.7s vs 77s, identical counts; `--stage1` = xlsx written, `db: NOT
written`; bare `--stage3` (no `--pdf`) = compiled_v2.db, 5,772 rows / 4,053 stamped;
`--stage2 --stage3` = both. 33/33 pipeline tests pass.

**Discarded:** a single `--stages 1,2,3` list argument — rejected because three
`store_true` flags compose without parsing, and argparse then documents each stage's
outputs in `--help` where an operator actually looks.

---

## 2026-08-13 — a refactor's "verified on the real pipeline" proof never touched STEP 1 or STEP 2

**Decision:** fix the two path casualties `5ce26d0` (three-stage split) left, and
record that **`--rebuild-db` is not a proof that extraction works.**

`toc_stage.py:47` still pointed at the pre-move `pipeline/prompts/fs_toc_headings.txt`.
`PASS2_v2.py` imports `stage1_extract.*` as packages but is launched as a script with
cwd `stage1_extract/chunk`, so `sys.path[0]` was `chunk/` — it now inserts `parents[2]`
(`pipeline/`) before those imports, the same idiom as `toc_stage.py:44`.

**Why:** `run_doc --pdf <any new document>` was dead on arrival since `5ce26d0` —
STEP 1 raised `FileNotFoundError`, and with that patched STEP 2 raised
`ModuleNotFoundError: No module named 'stage1_extract'`. Found by RUNNING a document
(OCBC 2Q26 media release), not by a test.

**The real lesson — a coverage hole, not two typos.** `5ce26d0`'s message claims
verification by `--rebuild-db` (25 docs, 865 sections, verify PASS) plus 38 passing
tests. Neither could have caught either bug: `--rebuild-db` rebuilds from **cached TOC
JSON + existing audit artifacts** and never shells out to `toc_stage.py` or
`PASS2_v2.py`. Evidence: `run_doc.py:12` documents STEP 6 as the last replay step, and
the failing invocations are the ones only the `--pdf` path emits —
`run_doc.py:609` (`toc_stage.py`) and `run_doc.py:635` (`PASS2_v2.py`). So every green
signal after the refactor came from the replay path, and the two live Gemini-touching
stages had zero coverage for a day. **`--rebuild-db` proves the DB layer; only
`--pdf` on a document proves extraction.** Treat a full `--pdf` run as the required
post-refactor smoke test.

**Discarded:** setting `PYTHONPATH` in `run_doc`'s subprocess env instead of fixing
PASS2 — rejected because it makes PASS2 runnable only via `run_doc`. The bootstrap in
the file keeps `python3 PASS2_v2.py …` working from any cwd, which is how it was
debugged here (`--help` from `chunk/` is the one-command check).

**Discarded:** moving the prompt back to `pipeline/prompts/` — rejected because the
prompt is Gemini-stage-owned and the refactor's placement is right; the stale
*reference* was the defect. `findociq/README.md:11` carried the same dead path and was
corrected with it.

---

## 2026-08-12 — one period grammar, not two: `--rebuild-db` could never finish

**Decision:** `run_doc.infer_period` now reads the SAME token grammar as
`classify/family.py:period_from_stem` — half-years (`1H`/`2H` -> the period-END
quarter), an optional `-`/`_`/space separator, and 2- OR 4-digit years.
**Why:** found by RUNNING the pipeline, not by a test. `--rebuild-db` walked 25
documents and died partway:

    ValueError: no period token (1Q25/2Q25/3Q25/4Q25/FY2025) in doc_id
    'OCBC_1H25_Media_Release_Financial_Highlights'

`family.py` — which ROUTES the same document — has parsed `1H`/`2H` since it was
written (`_PERIOD_H`, line 55). `run_doc` had `([1-4])Q(\d\d)` only, while its own
comment claimed to be "the ONLY period grammar". Two parsers, one corpus, different
answers: the router accepted a document the driver then refused. So `--rebuild-db`
could not complete on this corpus at all.
Both now agree on `1H25`->2025-06-30/2025-Q2, `2H24`->2024-12-31/2024-Q4,
`1q-2025`->2025-03-31/2025-Q1, `FY2025`->2025-12-31/2025-Q4, and a doc_id with no
token still fails LOUD.
**Verified by the real thing:** `--rebuild-db` on a scratch DB now runs clean —
25 docs, 865 sections, 528 tables, 9,038 rows, 33,671 cells, verify PASS (0 fail).
**Discarded:** special-casing the OCBC filename — that is the per-source hack
CLAUDE.md forbids; the defect was a general grammar gap, and half-year filings are
issuer-agnostic.

**Decision:** three `db.relative_to(REPO)` call sites replaced with the existing
`_display_path` helper.
**Why:** `Path.relative_to` RAISES outside the repo, so `--verify-only --db /tmp/x.db`
crashed before running a single step. `_display_path` was written for exactly this
(its docstring records the same crash in `--db-steps-only`) but three sites never
adopted it.


## 2026-08-12 — the pipeline no longer imports the app; full dead-code sweep

**Decision:** `parse_llm_json` moved from `app/spec.py` into
`pipeline/gemini_client.py` (with its own `LLMResponseError`), and
`toc_stage.py` dropped `sys.path.insert(0, findociq/app)`.
**Why:** `pipeline/toc/toc_stage.py:44,46` — a LIVE STEP 1 module on the main `fs`
route — put the Streamlit app tree on `sys.path` just to parse a Gemini response.
The pipeline depended on the app. It is pure stdlib (`json` + `re`), so the move is
mechanical; its 5 chatter checks came across as `pipeline/test_gemini_client.py`.
`app/` is now exactly what `app/DEPLOY.md` ships (`findociq_app.py`, `Dockerfile`,
`requirements.txt`) — the Findociq-Dashboard deploy needs only those plus
`db/compiled_v2.db` and `data/derived/dashboards/*.csv`.
**Discarded:** deleting `findociq/app` wholesale — it would have broken STEP 1 for
every `fs` document AND the dashboard deploy, which is built FROM this tree.

**Decision:** `app/spec.py` + `app/test_spec.py` retired to
`archive/2026-08-12-handover-cleanup/retired-streamlit/`.
**Why:** once `parse_llm_json` left, spec's remaining halves (`load_registry` /
`fetch_data` / the NL layer) had exactly one caller — `chat_report.py`, the OLD
Streamlit app, archived in the same pass. `findociq_app.py` never imported spec
(AST-verified), and `DEPLOY.md` never copied it.

**Decision:** 114 -> 50 non-test modules under `pipeline/`. Clusters and evidence
are recorded in `archive/2026-08-12-handover-cleanup/README.md`.
**Why:** handover. The load-bearing test was each module's TARGET TABLES against
`schema_v7.sql` AND the built DB — not grep, which matches docstrings, and not
"is it mentioned in a doc", because the generated `repo_audit.md` named every file
in the repo (deleted in this pass for exactly that reason).
**Discarded:** trusting the 2026-08-06 pass's reachability result — it rooted the
graph at imports only, missed the subprocess-invoked entry points, and so archived
`section_manifest.py` out from under `tag_sections.py`, leaving a documented command
that raised `ModuleNotFoundError` for six days.

**NOT actioned — open question.** `test_geo_stamp.py` asserts
`geo_lookup('Total') is None`, but the seeded `geo_map` holds `('total','GLOBAL')`
and `GLOBAL` is a real `geo_dim` member ('Group / Global'). `load_v7.py:646` sets
`_AXIS_SENTINEL['geo'] = None` while segment/industry get explicit `SEG_TOTAL` /
`IND_TOTAL`, so the map and the sentinel double-encode the same idea. This is a
semantic ruling on a LIVE path (`load_v7.py:2026` loads `geo_map` on every load),
not a test to edit — left failing and flagged rather than guessed.
Note `geo_key`/`segment_key`/`industry_key` are NOT retired: the 2026-07-31 decision
above keeps them, and they are what the canonical *identity* ids
(`canonical_leaf_id`/`canonical_col_id`) sit ALONGSIDE, not what those replaced.


## 2026-08-12 — run_doc.py self-bootstraps its venv; Pillar-3 "Branch B" retired

**Decision:** `run_doc.py` is now a true one-liner from a bare clone. A stdlib-only
block above every project import re-execs into `<repo>/.venv`, creating it and
pip-installing `findociq/requirements.txt` first if needed (hash-stamped in
`.venv/.findociq-reqs.sha256`, so run 2 is a no-op). `.venv-paddle` is built ON
DEMAND by STEP 0 only, never up front.
**Why:** handover. The reader has no Claude session and no setup doc in hand; the
one command in PIPELINE.md has to work as written.
**Discarded:** a preflight that only *checks* and prints fix commands — it still
makes the documented one-liner a two-step for anyone starting from a clone.

**Decision:** venv creation tries three strategies: `venv` -> `virtualenv` ->
`venv --without-pip` driven by the parent interpreter's `pip --python <venv_py>`.
**Why:** measured on this box — `python3 -m venv` FAILS ("ensurepip is not
available", Debian ships it in the separate python3-venv package) and
`python3 -m virtualenv` is absent, yet `python3 -m pip` is 24.0 and
`venv --without-pip` works. Strategy 3 installed the full requirements.txt into a
fresh tree with no sudo and no apt. Each attempt wipes the directory first,
because a failed `python -m venv` still leaves `bin/python3` behind — so
`_venv_usable()` probes `pip --version` rather than trusting the file exists.
**Discarded:** exiting with "sudo apt install python3-venv" — it needs root the
handover reader may not have, and strategy 3 removes the need entirely.

**Decision:** STEP 0 now runs PaddleOCR under `paddle_env()` —
`PYTHONPATH=/tmp/paddle-scratch`, `HOME=/tmp/paddle-scratch/paddlehome` — instead
of `subprocess_env(shim=...)`.
**Why:** this is the fix `docs/2026-07-24-ingest-handoff.md` line 96 left open.
Python loads only the FIRST `sitecustomize.py` on PYTHONPATH, so the IPv4 shim and
paddle's mkldnn-disabling shim cannot both apply; paddle's must win or
paddlepaddle 3.3.1 crashes on CPU via the oneDNN/PIR path.

**Decision:** `discover/section/{tag_sections,score_sections}.py` (+ tests) retired
to `archive/2026-08-12-handover-cleanup/`, and PIPELINE.md STEP 1 rewritten.
**Why:** `tag_sections.py` had been DEAD AND BROKEN since 2026-08-06 — that pass
archived `section_manifest.py`, which `tag_sections.py` imports at line 40:

    ModuleNotFoundError: No module named 'section_manifest'

so PIPELINE.md documented a Branch B command that could not start. Nothing was
lost: `run_doc.py:389` routes pillar3 to `pass1_toc.py` -> `pass1_to_v7.py` (the
2026-07-16 pivot). `score_sections.py` scores a `section_manifest.csv` that only
`tag_sections.py` produced. `candidates.py` / `toc_match.py` / `assign_tables.py`
STAY — `run_doc.py` still invokes them.
**Discarded:** restoring `section_manifest.py` — it would revive a second Pillar-3
orchestrator that `run_doc.py` never calls.

**Decision:** the three column-band checks get a committed fixture,
`pipeline/pass2/fixtures/dbs_1Q26_col_shift/`, rather than deletion.
**Why:** they read an absolute scratchpad path on a retired laptop
(`/private/tmp/claude-501/-Users-Qianyunhan-Desktop-...`), so they could not run
anywhere. Repointing at the tracked audit dir is NOT enough: that artifact is the
POST-repair extraction, so `validate_column_bands` correctly returns `[]` and the
positive/repair checks have nothing to fire on. The fixture reintroduces exactly
the one defect the spec names, and reproduces its ground truth verbatim:
`col-shift: 'Constant-currency change' printed bands [3,5] -> extracted slots [2,4]`.
**Discarded:** archiving them as unrecoverable — that would drop live coverage of
`validate_column_bands` / `repair_column_bands` / `validate_numbers` scoping.

**Decision:** `test_load_v7.py` builds its own fixture DB (schema_v7 + `toc_to_db.py`
on the tracked TOC) instead of copying `experiments/.../fs_eval_v7.db`.
**Why:** that spike DB was never tracked (`findociq/.gitignore: db/*.db`) and did
not survive its machine, so 2 of the 178 checks failed on "original spike DB
exists". Both inputs it was built from ARE tracked. Suite is now ALL PASS.


## 2026-08-12 — a bare year is a period in the TITLE'S TRAILING CAPTION, and nowhere else in a title

**Decision:** `_period_match_ctx` gains one TITLE-context branch, tried **last**,
after every printed-token branch has failed: a bare four-digit year that is the
entire trailing caption after a title delimiter (`— – - | :`) resolves to FY of
that year, ending 31 Dec (`_TITLE_TRAILING_YEAR_RX`, anchored to end-of-string).
The general bare-year guard for titles is untouched. At the load site the result
is passed through the existing `clamp_bare_year_to_doc_period`, so a trailing
`— 2026` on an interim filing cannot invent a period ending after the document's
own reporting date.

**Why:** UOB's 4Q25 condensed statements split one geography exhibit five ways by
a printed trailing caption — `— 1H25`, `— 2H24`, `— 2H25`, `— 2024`, `— 2025`.
The first three parsed; the two bare years did not, so both tables fell through
to `doc_period`. Measured before the fix:

    2025-12-31  via doc          n=77  <- Performance by Geographical Segment ¹ — 2024
    2025-12-31  via doc          n=77  <- Performance by Geographical Segment ¹ — 2025
    2024-12-31  via table_title  n=77  <- ...(cont'd) — 2H24

FY2024's 77 cells were stamped `2025-12-31` — a full year wrong, and colliding
with the genuine FY2025 table on the same period key. `— 2025` was right only by
coincidence (`doc_period` agreed). After: both resolve `via table_title`, 2024 to
`2024-12-31`. The caption is the same slot its siblings print a parsed token in,
which is what makes the trailing position — not the year itself — the signal.

**Discarded:** *lifting the bare-year guard for titles generally.* It is a
deliberate, tested invariant (`load_v7.py:309`, `test_period_span.py:74`) — "a
bare year alone is ambiguous start vs end". Lifting it makes every incidental
year in a title a period: `Basel III 2024 framework`, `Note 3 2025`. Both are
now regression cases asserting `None`.

**Discarded:** *adding a SECTION-HEADER rung to the cascade (col → row →
table_title → doc).* Measured on the corpus first: of the 3,203 cells that fall
back to `doc_period` across 87 tables, exactly **one table (6 cells)** sits under
a section whose title carries a date, and that date (`Fourth Quarter 2025
Performance` → 2025-12-31) is identical to the `doc_period` already stamped. A
section rung would change zero values. Only 25 of 296 section titles carry a date
at all, and they restate the document period by construction. Re-open this if a
document appears whose columns, rows and title are all period-free while its
section header carries a date that differs from the document's.

**Also verified, not a defect:** row-parent and column-parent inheritance were
suspected missing and are not. DBS 2Q26 `PERFORMANCE BY GEOGRAPHY — Selected
balance sheet items` carries its three dates as hierarchy-0 ROW banners; all 90
cells resolve `via row` to 2026-06-30 / 2025-12-31 / 2025-06-30 by the ancestor
walk at `load_v7.py:1675`. DBS 2Q26 NPA-by-industry carries them as hierarchy-0
COLUMN banners (`col_id` 100/101/102); leaves inherit through `col_parent`.

## 2026-08-12 — a printing slip is aliased, not normalised

**Decision:** `masterlist_leaf_aliases.yaml` gains
`DBS: FS_BALANCE_BY_GEOGRAPHY: total_assets_before_goodwill_and_intangibles_assets
→ total_assets_before_goodwill_and_intangible_assets`.

**Why:** DBS's 2Q26 performance summary spells the line "intangible assets" in
its 30 Jun 2026 and 31 Dec 2025 blocks and "intangible**s** assets" in the
30 Jun 2025 block of the *same* exhibit. Unmatched, that row loaded with
`canonical_leaf_id` NULL (and no `table_type_id`), so the 1H25 figure — 835,499
group total — was extracted correctly and then unaddressable: a stamped leaf can
be reached by an anchor, an unstamped one cannot. This is the file's own stated
case, the exact same printed line under a changed name, and the alias costs one
line.

**Correction (2026-08-12, same day):** this entry first claimed the gap was
"invisible to the dashboard while its 2Q26 and 4Q25 siblings rendered — a hole at
exactly one period". That was wrong. `total_assets_before_goodwill_and_intangible_assets`
appears in **0 of the 4 anchor files**, so no sibling rendered either and no
dashboard row has ever shown this concept. The fix is still correct — an
unstamped leaf is a real defect and the alias is the right remedy — but it buys
nothing VISIBLE until an anchor addresses that leaf. Checked by grepping the leaf
id across `data/derived/dashboards/*.csv`.

**Discarded:** *a general singular/plural normaliser in
`build_masterlist_proposed.py`.* The alias file reserves normalisation for
footnote markers, `&`/`and`, unit suffixes and section prefixes — forms that are
provably equivalent. Singular/plural is not: it would collapse leaves that are
genuinely distinct elsewhere in the corpus, to fix one typo in one block.

## 2026-08-11 — the registry names EVERY extracted table, and granularity follows the printed axis

**Decision:** `table_registry.yaml` grows from 26 to 51 types so that all 184
tables extracted from the four 4Q25 documents classify — 100%, 0 UNCLASSIFIED
(was 148/184, 80%). Granularity rule, now written into the file header: **one
`table_type_id` per distinct printed exhibit, where distinct means a distinct
decomposition axis or column shape.** Split on WHAT is decomposed — industry,
geography, segment, legal entity, collateral, loan grading, period-overdue.
Never on WHICH period is shown.

**Why:** the masterlist addresses a leaf as `(bank, table_type_id,
canonical_leaf_id)`. When several logical tables share one id their leaves
compete for one namespace and most become unaddressable — measured at
`pipeline/pass2/transforms.py:1573`, where DBS's merged Overview page stranded
two thirds of its correctly-stamped leaves. UOB's four p14-15 NPA tables have
four different column signatures (`Dec-25/Jun-25/Dec-24 $m`; `NPL ratio % +
specific allowance`; `NPL $m + ratio` per period; allowance-coverage
percentages), so one `FS_NPA_COVERAGE` could only ever serve one of them.

Concretely: `FS_NII_DETAIL` split into `FS_NII_DETAIL` /
`FS_NON_INTEREST_INCOME` / `FS_FEE_INCOME` / `FS_OTHER_INCOME`; NPA into 8;
`FS_AVG_BALANCE_SHEET` separated from `FS_VOLUME_RATE` (levels vs
change-attribution); segment and geography each into income-by-axis and
balance-by-axis.

**Why NOT split on period:** UOB prints changes-in-equity twice (2024, 2025) and
net interest margin twice (FY columns, half-year columns); OCBC prints business
segments three times. Those are one exhibit at several periods — the period
lives on the column axis. A period-derived id would mint new ids every quarter
and stop being stable, defeating the point of a registry-assigned key.

**Discarded:** mapping UOB's p8 "Net interest margin" to `FS_AVG_BALANCE_SHEET`,
which the retired seed CSV did
(`archive/2026-08-06-masterlist-retirement/table_registry_seed.csv`, caption
"Net interest margin (average balance sheet)"). Its columns ARE the
average-balance shape, but the section tree is decisive: `section_path =
net_interest_income.net_interest_margin`, a level-2 subsection of Net Interest
Income, nothing to do with a balance sheet. The retired seed read the column
shape and ignored the document.

**Discarded:** renaming the curated masterlists and dashboard anchors to the
YAML's `FS_INCOME_STATUTORY` / `FS_BALANCE_STATUTORY`. The curated file is
authority (2026-08-06 retirement), and 72 OCBC masterlist rows plus 21 anchor
rows already address `_CONSOLIDATED`. The YAML moved to the authored name
instead.

**Discarded:** widening `masterlist_derive.is_period_label` to match the bare
`At <date>` form, which would stop `At 31 December 2025` segment banners leaking
into leaf ids as `at_31_december::segment_assets`. `classify` applies the same
test to VALUED rows as `PERIOD_ROW`, and a valued `At 1 January 2025` is the
opening balance of a changes-in-equity or Level 3 roll-forward — printed
identity, not period (`masterlist_derive.py:125`). Measured: of the 7
`At <date>` labels in the corpus, every valued one is an opening/closing balance
(4-8 values each) and every valueless one is a segment banner (0 values). A
correct fix splits on `has_values` and belongs in the stamper, not in a proposal
generator. The 52 affected rows are flagged `PERIOD IN ID` for curation.

## 2026-08-11 — masterlist proposals are generated, but generated ids are never authority

**Decision:** new `pipeline/mapping/propose_masterlist.py` drafts 1,266
leaves and 42 column identities for the ~93 `(bank, table_type_id)` pairs no
masterlist covers, into `data/derived/masterlist_proposed_2026-08-11/`. It
refuses to write into `data/derived/masterlist/` and skips any pair already
curated.

**Why:** coverage was DBS 4/36, OCBC 3/42, UOB 4/30 table types. Authoring
~1,500 leaves by hand from scratch is not realistic; curating a draft is. The
generator reuses the stamper's own functions — `masterlist_derive.leaf_id`,
`classify`, `build_ancestry`, `resolve_canonical_leaf._printed_chain`,
`doc_source_family` — so a promoted proposal resolves back to the rows it came
from. Verified: 106/106 entries locate a table, 100/106 match every leaf they
declare, and all 6 partials are pre-existing curated entries.

**Why the proposal directory:** the 2026-08-06 retirement happened because a
stamping run matched against generated ids and overwrote curated ones. The rule
it set — generated is a proposal, curated is authority — is enforced here in
code, not convention.

**Discarded:** emitting `leaf_id()`'s own of-which form. It produces three
segments, `total_income::of_which::net_interest_income`; every curated file uses
two, `total_income::of_which_net_interest_income`. That exact disagreement was
one of the two triggers for the 2026-08-06 retirement, so the CURATED form wins
and `masterlist_derive` is left untouched.

**Discarded:** drafting leaves from the richest table of a type. DBS prints its
geography exhibit TWICE on p18-21 — under a `Selected income statement items`
banner (11 leaves, all prefixed) and bare (11 leaves, unprefixed). Both resolve
to `FS_PERF_BY_GEOGRAPHY` and the two leaf sets are DISJOINT, so the richest-only
draft silently lost 11. Leaves are now the UNION across one document.

**Discarded:** unioning across the whole document FAMILY. `locate_tables` scores
a candidate against `MIN_MATCH_FRACTION` (0.5) of everything the entry declares,
so every leaf raises the bar for every vintage: a 2Q26 half-year variant's extra
rows pushed the 4Q25 table under it. Measured — 3 entries stopped resolving
entirely and partial matches went 6 → 49. Scoped to one document instead.

**Discarded:** an unconditional same-document union. Where a document prints the
type as several DISJOINT slices — DBS's changes-in-equity (audited FY + unaudited
half-year × Group 2024/2025) and OCBC's Level 3 roll-forward (financial assets,
liabilities, non-financial) — the union declared 23 and 22 leaves that no single
table could half-match, and both types stopped locating. The union is now
self-checking: it is tested against `MIN_MATCH_ABSOLUTE` / `MIN_MATCH_FRACTION`
and falls back to the richest table when it would not survive them. Final:
106/106 entries locate, 95/106 match every declared leaf, and the 5 proposed
partials are genuine multi-slice types.

**Discarded:** reading column identity at a fixed hierarchy level. Identity sits
at different levels per exhibit — geography puts `$m` under `Singapore`, the
balance sheet puts `Dec-25 $m` under `The Group`, equity puts `Share Capital` at
the leaf, UOB's segments are flat `GR $m`. A "level-0 headers" rule got equity
wrong: DBS spans only columns 1-5 with `Attributable to shareholders of the
Company` and leaves `Non-controlling interests` / `Total equity` at top level, so
level 0 is one span, not the seven components. The generator now walks UP from
each leaf to the nearest ancestor whose label is neither a period nor a bare
unit, which handles all four shapes.

**Discarded:** treating `dim_hint` as "the columns carry this dimension". UOB's
`FS_PERF_BY_GEOGRAPHY` puts geography in columns (14 columns carry `geo_key`);
its `FS_NPA_BY_GEOGRAPHY` puts geography in rows (6 rows carry `geo_key`) and
spends the columns on measures — `NPL/NPA $m`, `NPL ratio %`. Emitting the
latter minted geography ids out of measure headers. The ingest already records
which axis it stamped the key on, so the generator asks the data instead of the
caption.

## 2026-08-11 — a dated balance line scopes nothing, and DBS's masterlist is complete

**Decision:** one condition in `build_ancestry`'s captured-chain filter
(`masterlist_derive.py:519`) — an ancestor whose label is wholly a date/period
contributes no id segment even when it carries VALUES. The `cls` test beside it
already dropped valueless period ancestors; this covers the valued ones.

**Why:** OCBC indents its changes-in-equity movements under the opening balance,
so the loader captured `At 1 January 2024` as their `row_parent` and every
movement derived as `at_1_january::profit_for_the_year`. In the printed statement
they are its SIBLINGS — opening balance, movements, closing balance — which is
exactly how DBS captures the same exhibit. The fix makes OCBC agree with DBS
rather than inventing a per-bank convention.

**What it does NOT do:** remove the row. It stays `DATA`, still derives
`at_1_january` from its own label, still carries its 5 figures; `at_31_december`
likewise. Leaf count on the affected table is 13 before and after — 11 of them
get a shorter address. `full_path` loses the prefix in step with the id, because
`build_ancestry` fills `ancestors` / `ancestor_labels` / `ancestor_labels_raw` in
one loop, so the masterlist and `table_paths()` cannot drift apart. Two
regression tests pin both halves.

**Blast radius:** 24 rows across 4 OCBC tables, nothing elsewhere. Zero rows in
either DB change, because OCBC is not promoted or stamped — `build_ancestry` is
derivation, and the DB only moves when `stamp_tables.py` writes. Streamlit
renders identically: the dashboard anchors address 7 table types and none is an
equity type.

**Also:** `DBS_Master.csv` (the curated pass over the proposal, 389 rows / 31
types) merged into `DBS_masterlist.csv` — 47 → 436 leaves, 4 → 35 of 36 types,
only the narrative `FS_KEY_AUDIT_MATTERS` uncovered. Types were disjoint, so the
union introduced no duplicate leaf ids; `section_ordinal` was renumbered globally
by first printed page (Dividends p1 now precedes Overview p4). 35/35 entries
locate, 34 at full match.

**Discarded (a curation slip worth recording):** two `FS_CUSTOMER_LOANS` rows
were authored with `full_path` = `Less: ECL Stage 3 (SP)`, the ` > ` separator
lost. DBS prints `Less:` as a level-1 banner over the two ECL rows, so the
printed chain is `Less: > ECL Stage <n>` and the flattened form matched nothing —
2 of 30 leaves silently unresolved. The path must mirror the print; the leaf id
need not, and the flat `ecl_stage_3_sp` was kept because `match_variants` accepts
both forms.

## 2026-08-11 — rule 3b: 'At <date>' is a period banner only when the row carries no values

**Decision:** `masterlist_derive` gains `is_period_banner_label` (rule 3b), used
by `classify` for the VALUELESS branch only. It is rule 3 plus the bare
`At <date>` and `<Half|Full> year ended <date>` forms, and — unlike rule 3 — it
strips footnote markers first. Rule 3 (`is_period_label`) is unchanged.

**Why not simply widen rule 3:** `classify` asks the same question of valued and
valueless rows, and the answer must differ.

| | meaning | required class |
|---|---|---|
| valueless `At 31 December 2025` | heads a segment BALANCE block; scopes the rows under it | `PERIOD_BANNER` — no id segment |
| valued `At 1 January 2025` | the OPENING BALANCE of a changes-in-equity or Level 3 roll-forward, 4-8 figures | `DATA` — the date is the identity |

Left as a BANNER the first leaked into ids as
`at_31_december::segment_assets` — 47 leaves. Demoted to `PERIOD_ROW` the second
would collapse opening and closing balances into one leaf and lose the movement,
the distinction `masterlist_derive.py:125` already protects for
'Balance at 1 January'. Measured: of the 7 distinct `At <date>` labels in the
corpus, every valued occurrence is an opening/closing balance (4-8 values) and
every valueless one is a segment banner (0 values). The split is on
`has_values`, not on the wording.

**Blast radius, measured by re-deriving the whole corpus under both classifies:**
4,723 rows before and after, **66 ids changed, 0 rows changed class**, and every
one of the 66 is an `at_<date>::` prefix disappearing. `balance_at_1_january` and
`balance_at_31_december` remain distinct. Four regression tests added covering
both halves of the split.

**Discarded:** fixing it in `propose_masterlist` by stripping the segment from
the proposed id. The proposal must emit exactly what the stamper will produce —
diverging is precisely the generated-vs-curated mismatch that forced the
2026-08-06 retirement.

**Discarded:** flagging period leaks by testing the ancestor LABEL for a year.
Wrong in both directions: it flagged `Balance at 1 January 2024`, whose segment
is `balance_at_1_january` with the year already stripped by
`strip_trailing_date`, and it would miss a period phrase written without one.
The flag now asks rule 3b, the same question `classify` asks.

**Not fixed — 15 rows, all OCBC changes-in-equity.** OCBC captures `Profit for
the year` and the other movements as CHILDREN of the opening-balance row, giving
`at_1_january::profit_for_the_year`; in print they are its SIBLINGS. Not
reachable by reclassification (the row is valued and its date is its identity),
and DBS captures the same statement flat, so this is geometry/loader ancestry.
Flagged `DATE PARENT` in the proposal rather than mislabelled as a period leak.

## 2026-08-11 — the equity column axis gets a dimension, and a bare 'Total' is not total equity

**Decision:** new `equity_component_dim` + `equity_component_map` in
`schema/schema_v7.sql`, mirroring `segment_dim`/`segment_map` exactly, plus
`pipeline/mapping/migrate_add_equity_component_dim.py` to backfill an existing
DB (`--stamp` also writes a new `col_dim.equity_key`). 10 members, 17 aliases;
149 columns stamped, 1 unmatched (`the bank`, correctly the legal-entity axis).

**Why:** a statement of changes in equity decomposes ACROSS the page — its
columns are equity components, not periods. `geo_dim` / `segment_dim` /
`industry_dim` cover the other three axes; this one had nothing, so all 33 such
columns resolved to no key and the axis could not be addressed or rolled up.

**Why hierarchical:** the printed columns are NOT peers.
`EQ_TOTAL = EQ_ATTRIBUTABLE + EQ_NCI (+ EQ_OEI_SUB)` and
`EQ_ATTRIBUTABLE = share capital + reserves + retained earnings`. Summing them
as siblings double-counts. `eq_level` / `parent_eq` are the same guard
`seg_level` / `geo_level` already provide. OCBC splits what DBS and UOB print as
one `Other reserves` into `Capital reserves` + `Fair value reserves`, so those
are CHILDREN of `EQ_OTHER_RESERVES` — summing one level stays correct whichever
bank is in front of you.

**The load-bearing alias:** `total` -> `EQ_ATTRIBUTABLE`, NOT `EQ_TOTAL`. On an
equity statement a bare `Total` column is the subtotal attributable to equity
holders of the parent, printed BEFORE the NCI column; OCBC and UOB print `Total`
where DBS prints `Total Shareholders' funds`. Mapping it to `EQ_TOTAL` would
silently equate the attributable subtotal with total equity.

**Discarded:** `geo_norm` alone for the lookup, the convention the other three
axes use. Those read identity off SPANNING headers, which carry no unit; equity
reads it off the LEAF columns, and UOB prints `Retained earnings $m`. Measured:
21 of 25 columns missed. `eq_norm` = `geo_norm` after
`strip_footnote_markers`, which is the same normalisation `propose_masterlist`
already applies to those labels.

**Discarded:** gating the column proposal on `statement_class == 'equity'`.
`FS_DIVIDENDS` and `FS_SHARE_CAPITAL` share the class but column by period
(`Half year ended 31 Dec`) or legal entity (`The Group`), and produced 4 junk
identities. The gate is now evidence: at least one column must resolve in
`equity_component_map` — the same ask-the-data test used for the row-vs-column
axis discriminator.

## 2026-08-11 — DBS gains a source_family, and trading updates alias into it

**Decision:** `DBS_masterlist.csv` gains `source_family: Performance_summary`
(it was the last masterlist without one, i.e. unconstrained), paired with
`DBS: {trading_update: Performance_summary}` in
`masterlist_source_family_aliases.yaml` — the first entry that file has ever had.

**Why:** an entry with no family can claim a table in any document kind. That is
the documented escape in `locate_tables` (`resolve_canonical_leaf.py:550`) and
it was harmless only while DBS had one filing type. `DBS_1Q26_P3_other_regulatory_disclosures`
is already ingested, so the bar now has something to bar.

**Why the alias:** measured, the column alone drops
`DBS_1Q26_trading_update` from 45 stamped rows to 0 (DBS 141 → 96). A trading
update prints a REDUCED SET of the same Overview exhibits, not different ones —
`FS_INCOME_SELECTED`, `FS_BALANCE_SELECTED`, `FS_RATIOS_KEY` and `FS_PER_SHARE`
all appear in both with the same leaves. That is the "same document type under a
changed name" case the alias file exists for. Verified through the real
`locate_tables`: all four types still resolve against the trading update, and
the Pillar 3 document is now correctly barred.

**Discarded:** extending `_FAMILY_QUALIFIER` instead, which the alias file's own
guidance prefers. It cannot reach this: "trading update" and "performance
summary" are different words for the filing, not presentation or assurance
adjectives like condensed/interim/unaudited.

---

## 2026-08-10 (cont'd) — the dashboard's period axis is FISCAL, and each anchor declares how it joins it
**Decision:** the Key Financial Highlights period control is no longer a
"period basis" radio over `period_span`. There is ONE fiscal axis built from
flow spans, and every anchor declares — in the CSV, column `filter_by` ∈
{`period_label`, `period_end_date`} — which of two ways its facts join it. A
flow matches the whole label (`1H26` income is 1H26 income); a stock matches
the END DATE alone and therefore appears under every window closing that day.
Blank means "derive from `table_type_id`": `FS_BALANCE*` → `period_end_date`,
everything else → `period_label`. Implemented entirely in `app/findociq_app.py`
(`default_filter_by` / `resolve_filter_by` / `fiscal_period_axis` /
`target_period_labels`); no schema, no loader, no `cell_fact` change.

**Why:** the radio asked the reader a question about the DATA'S SHAPE that no
reader of a bank dashboard has any reason to answer, and every answer was
wrong for someone. `default_basis` picks the basis with the most distinct
PERIODS, which is `quarter` — measured on the live `compiled_v2.db`, that
landing view rendered **DBS 25/26 lines, OCBC 6/26, and UOB not at all** (UOB
files half-yearly, so `banks_present` dropped it from the page). Switching to
`half` did not fix OCBC either: its balance sheet is stamped `as_at` and
matched no flow basis, so `half` showed the income and blanked all 7
balance-sheet lines while `as_at` did the exact reverse. **No setting of that
radio could show a complete grid**, because span was being used as a filter
when it is really two different KINDS of fact sharing one column.

**Why anchor-driven, not fact-driven:** the alternative is a `stock_flow` /
`concept_kind` classifier on the fact. Rejected — it is a second identity
system competing with the masterlist, it would need a loader change and a
rebuild to populate, and it puts the decision at the wrong grain: the same
leaf can be a stock in a balance sheet and a flow in a movements table. The
anchor already names `(bank, table_type_id, canonical_leaf_id)`; the join rule
belongs beside the address it qualifies.

**Discarded:** *`filter_by` as an enumerated list of stock table ids.* Rejected
for a prefix test (`FS_BALANCE`) — evidence: three variants already exist in
the anchors (`FS_BALANCE_SELECTED` 9 rows, `_CONSOLIDATED` 9, `_STATUTORY` 3,
counted from the two CSVs), the corpus is mid-authoring, and an enumeration
silently misclassifies the next one as a flow, which fails as a BLANK ROW with
no error. The prefix rule needs no edit for a variant we have never seen.

**Discarded:** *treating an unrecognised `filter_by` value as its own mode.*
It is hand-authored in a CSV; a typo (`period_enddate`) would match nothing
and blank the line silently. `resolve_filter_by` falls back to the default, so
the worst a typo costs is an intentional override — the exact case a reader is
already looking at when they check.

**Measured, before → after (live `db/compiled_v2.db`, landing view):**
DBS 25/26 → 25/26 · OCBC **6/26 → 26/26** · UOB **absent → 24/26**. At 1H26
specifically: DBS 25, OCBC 26, UOB 22 — UOB's 4 gaps are the already-logged
masterlist items, unchanged by this work.

**Defect found and fixed while landing it:** the end-date fan-out DOUBLE-COUNTED
stocks. A balance at 2025-12-31 is filed under several spans (DBS's `Total
assets` arrives stamped `4Q`, `2H` AND `FY`); each matched every column closing
that day and the composition SUMMED them, rendering DBS 4Q25 total assets as
**2,692,464 against a filed 897,488 — exactly 3x**. They are one fact recorded
three times, not three facts. `_collapse_same_date_stocks` keeps one per
(institution, slice, end date) and only under `period_end_date`; flows are
untouched, since 1H and FY income at one year end really are different
measurements.

## 2026-08-10 (cont'd) — display precision is a property of the VALUE, not of the unit
**Decision:** `format_highlight_value` no longer branches on `unit_hint`. One
rule for every unit: thousands-separated, the value's own precision, capped at
2 decimals. `unit_hint` stays in the signature and is ignored.

**Why:** the old branch chose `:,.0f` for `S$m`, so the formatter's
correctness depended on a DIFFERENT column being right — and it isn't. Only
DBS stamps `per_share` on its per-share rows; **OCBC's carry `S$m` and UOB's
carry `%`** (measured across all three banks in `compiled_v2.db`). So OCBC EPS
0.81 rendered **`1`**, EPS 1.63 rendered **`2`** and NAV 13.73 rendered **`14`**.
An integer where the filing printed two decimals is not a rounded figure, it is
a wrong one, and the fix belongs where the dependency was misplaced rather than
in a per-bank unit patch. UOB's per-share rows were `%`-labelled and so
*looked* right at 2dp — the same latent bug, one lucky branch away.

**Discarded:** *fixing the `unit` stamp in the loader instead.* Correct, and
out of this task's scope (no loader changes) — but it would not have made the
formatter safe, only currently-lucky. A display rule that cannot be broken by
an upstream stamp is worth having regardless.

**Cost, stated plainly:** a ratio the source printed as `12.30` now renders
`12.3`, and a value with 3+ decimals is capped (`1.234` → `1.23`). Nothing in
the corpus files a headline figure past 2dp. Two tests in
`test_findociq_app.py` pinned the old padding and were updated with the reason
inline.

## 2026-08-10 (cont'd) — a period column must carry a real share of the grid
**Decision:** `period_axis_order` takes `total_items` and drops any period
carrying fewer than `MIN_COLUMN_DENSITY` (0.20) of the bank's anchor items.
Judged PER BANK and counted on DISTINCT item labels. Anchor rows are never
filtered — only columns.

**Why:** a column holding 2 of 26 lines reads as "we have this period" when
what we have is a fragment of it, and the reader is the one person who cannot
check. Measured: DBS's `4Q24` and `3Q25` were three per-share rows and
whitespace; UOB (half-yearly) was being given `4Q24`/`2Q25`/`4Q25`/`2Q26` to
prove it does not file them.

**Why per bank:** the three banks do not file on one calendar, so a shared
column set gives every half-yearly filer four empty quarter columns. Counted
on distinct labels, not rows, because a multi-member formula line emits one
row per member and would otherwise vote several times for its own period.

**Not hidden:** the pruned periods are named in the caption under the period
multiselect and can be added back from it — the rule tidies the default view,
it does not withhold data.

---

## 2026-08-10 — a merge must re-base every reference that was scoped to the table

**Decision:** `merge_continuation_tables` resolves each `GRow.parent='hN'` of the
continuation to its header ROW while the source table is still the frame of
reference, then re-emits the ordinal against the MERGED row list. The header rule
becomes one shared function, `transforms.header_row_indices`, imported by
`load_v7.resolve_printed_parents`.
**Why:** `hN` is a POSITION ("the Nth header row of this table"), not an id —
`resolve_printed_parents` decodes it that way and `GRow.row_id` is null across the
corpus. Appending re-bases every one of them. UOB p6 numbers `Liquidity coverage
ratios` h1, `Capital adequacy ratios` h2, `Earnings per ordinary share` h3; the
merged table's first three headers are `Credit costs on loans`, the `Notes:` block
and LCR, so each p6 sub-item re-parented one block early. Measured: the merged
27-row table matched **12 of 27** masterlist paths, under `MIN_MATCH_FRACTION`'s
13.5, so `table_type_id` stayed NULL and all 27 rows went unstamped — a merge that
did its job produced a WORSE outcome than no merge (UOB @1H26 dashboard anchors
18/26 recorded pre-merge, 13/26 measured with the un-rebased merge, 22/26 now).
**Discarded:** namespacing the synthetic handles (`h1` -> `c1h1`), which is what
the first cut did. It keys off `GRow.row_id`, and in p6's artifact the handles
DANGLE — the children carry `parent='h1'` while the header row's `row_id` is null,
so the rename dict was empty and the pass was a silent no-op. Verified in
`financial_highlights_cont_d_p6/parsed.json`: three `parent=hN` refs, zero
`row_id`s. Handles are not the mechanism; ordinals are.
**Discarded:** re-basing by a precomputed offset (`+ len(headers_in_prev)`). The
join can PROMOTE the predecessor's last row to a header — p5's `Notes:` block sits
at level 0 and only becomes a header once p6's level-1 rows follow it — so the
shift is 2, not the 1 header p5 printed. Only the merged row list can be counted.
**Discarded:** dropping the references and letting the position walk decide. It
happens to be right here, but `resolve_printed_parents` exists precisely because
position is wrong on DBS 4Q25 `Of which: Net interest income` (the `pnl.nii.net`
defect in `lineage_identity_map.csv`). Preserving the extractor's stated intent
across a merge is the whole contract.
**Evidence of no collateral damage:** all 10 docs reloaded with and without the
change and diffed leaf by leaf — zero leaves lost a stamp, one table gained a
`table_type_id`, UOB 2Q26 51 -> 74 stamped. `--verify-only` fail=0 for both UOB
docs, which also proves the merged table's page union survives STEP 5.

---

## 2026-08-10 — a footnote block is excluded by WHAT IT IS, never by where it sits

**Decision:** `masterlist_derive.classify` matches a footnote block with
`^notes?_` instead of `note_`.
**Why:** a whole footnote block arrives as ONE valueless row —
`'Notes: 1 Relates to … 3 Refers to …'` — and `EXCLUDE_LABELS` only holds the bare
word. The prefix test that was meant to catch the block missed the PLURAL, which
is the form every filing in the corpus prints. It cost nothing for as long as the
block was the LAST row of its table, because the trailing-prose rule ("no DATA row
follows") excluded it anyway — a positional accident standing in for the real
test. `merge_continuation_tables` puts a page of rows beneath it, the block
becomes a mid-table BANNER, and every continuation row inherits it:
`Notes: 1 Relates to … > Liquidity coverage ratios (\"LCR\") > NSFR`. Two spurious
ancestors, and `match_variants` yields only the path minus its OUTERMOST one, so
NSFR / Leverage ratio / NAV / Revalued NAV resolved to nothing while their
siblings one prefix deep resolved fine.
**Why this is safe:** the branch is reached only by rows with NO values at all, so
a real line item ("Notes and coins", "Notes payable") is `DATA` before it gets
here. Pinned as a test.
**Discarded:** teaching `match_variants` to strip more than one leading ancestor.
It would have hidden this and every future ancestry defect — the whole point of a
printed path is that it is the path, and each extra tolerance widens what a wrong
address can still match. Fix the classification, not the comparison.
**Discarded:** carrying `GRow.row_type` through to `row_dim` so the resolver could
read `row_type='note'` instead of re-deriving from the label. Correct in the long
run and a schema change: `row_dim` has no `row_type` column, so it needs a
migration plus a reload of every doc. Logged rather than smuggled into a fix.

---

## 2026-08-09 — the column axis gets an identity, and two invariants get written down

**Decision:** spec `docs/specs/2026-08-09-column-axis-identity.md`. The dashboard
address becomes a tuple `bank > table > line item > column`, backed by
`col_dim.canonical_col_id` (declared at `schema/schema_v7.sql:384`, **0 of 1915
populated**). Columns dispatch three ways at load — `period` (attrs only),
`derived` (`col_role='derived_skip'`), `hard` (`canonical_col_id`). The masterlist
gains a `col_members` block per table type, additive to the row block, never a
row×column cross product. Anchors gain a `canonical_col_id` column; blank means
"the period axis", so all 83 existing addresses stay valid.
**Why:** 799 of 1,915 columns carry no period (42%). Statements of changes in
equity and performance-by-geography tables name several facts per row —
OCBC's `Profit for the period` is five different facts across five equity-
component columns. Settled BEFORE the mass masterlist populate: two axes authored
together cost one pass, a column axis retrofitted into a hundred authored tables
costs a rebuild.
**Two invariants recorded verbatim, because machinery gets rewritten and rules
should not:**
  1. *"A date is period data, never identity."* Carried from
     `2026-08-05-master-registry-next-steps.md:74`. UOB's
     `Performance by Geographical Segment` is the case that tests it — geography
     on the COLUMN axis, period printed as row 1 (`1H26`), sitting exactly where
     a row-identity segment would sit. Unstopped, the ancestry walk prefixes every
     leaf with `1h26::` — a leaf that changes every vintage and matches no
     masterlist entry. The rule therefore binds on BOTH axes: a period is period
     data wherever it is printed.
  2. *Column decisions are STAMPED AT LOAD and FILTERED AT QUERY — never reasoned
     about in the app.* Same principle as `canonical_leaf_id` on the row side.
     The OCBC consolidation-basis fix was already this shape: a typed
     `legal_entity` stamp plus one equality filter (`findociq_app.py:985`), not a
     banner-text test.
**Discarded:** *column identity derived at query time from banner text* — the
`WHERE parent.col_leaf_label = 'GROUP'` shape. Rejected explicitly and named in
the spec as an anti-pattern: it moves a load-time decision into the serving layer
and hardcodes one bank's printed spelling (`The Group` / `GROUP` / `Group`), so it
returns the wrong number the first time a filing re-words a banner. Gate 5 of the
spec is a CI grep for exactly this.
**Discarded:** *a separate column-side dimension vocabulary*. Rejected on
evidence: geography ALREADY appears on both axes in the current DB — OCBC's
`geographical_segments` carries it on rows (`geo_key` `SG`/`MY`/`GREATER_CHINA`),
UOB's `perf-by-geography` carries it on columns with no key at all. A parallel id
space makes Singapore incomparable to Singapore. `geo_map` (24 rows) already
resolves six of UOB's seven geography columns by normalised label; only the
printed `Total` -> `GLOBAL` is missing. One row of curation against a whole
second vocabulary.
**Discarded:** *row × column cross product in the masterlist*. A 35-row × 8-column
table would cost 280 declarations instead of 43, re-declare the row list once per
column, and fail to match rows it does contain on any vintage that reprints fewer
columns. Column members are also kept OUT of `locate_tables`' coverage
denominator (`MIN_MATCH_FRACTION`, `resolve_canonical_leaf.py:514`) for the same
reason.

---

## 2026-08-09 — the Highlights row list DECLARES its own section grouping

**Decision:** `section` is now a column in
`data/derived/dashboards/highlights_dashboard_anchors.csv` and
`highlights_formulaanchors.csv`. `load_dashboard_anchors` reads it;
`attach_sections` is demoted to a fallback for anchor files written before the
column existed.
**Why:** the Dashboard view builds ONE `items` list as the cross-bank union
(`findociq_app.py:1537-1543`) and `attach_sections` derived each label's section
from the FIRST record in the concatenated frame. `Total equity` resolves only for
OCBC (DBS and UOB address `FS_BALANCE_STATUTORY`, which no masterlist covers), so
it took OCBC's caption `UNAUDITED BALANCE SHEETS` while its neighbours took DBS's
`Selected balance sheet items ($m)` — and a bold section-header row was emitted
between `Total liabilities` and `Total equity` in EVERY bank's grid. Grouping is a
property of the row list, not of whichever bank's data arrives first.
**Measured:** all three grids 31 -> 30 rows, four section headers each, identical
grouping across banks; 26 of 26 concepts still render, blanks stay blank so the
coverage gap remains visible.
**Discarded:** *derive per bank instead of from the union* — each grid would show
its own printed captions, but the grids stop agreeing row-for-row and compare mode
still needs a tiebreak. **Discarded:** *suppress the header for no-data rows only*
— kills this instance while leaving the ordering-dependent derivation in place to
bite on the next union-order change.

---

## 2026-08-09 — UOB CET1 was an anchor typo, not a stamping failure

**Decision:** the UOB row-23 anchor address changed from `common_equity_tier_1` to
`capital_adequacy_ratios::common_equity_tier_1`. Nothing stored was touched.
**Why:** the DB had it right — `UOB_4Q25` row 19 (`hierarchy 2`, parent row 18
`Capital adequacy ratios`) is stamped
`capital_adequacy_ratios::common_equity_tier_1`, matching `UOB_masterlist.csv:36`
(`full_path` = `Capital adequacy ratios > Common Equity Tier 1`). The anchor
carried the bare leaf. An anchor address must carry the full parent chain: the
serving join is exact string equality in `_ANCHOR_SQL`, and the outermost-ancestor
tolerance in `match_variants` (`resolve_canonical_leaf.py:136`) runs on the
PRINTED path at stamping time only — it never runs on the serving side, so a bare
anchor yields silence rather than a fallback. Isolated slip, not a pattern: every
other parented anchor already spells the chain (`earnings_per_ordinary_share::basic`,
`revenue_mix_efficiency_ratios::net_interest_margin`,
`commercial_book_total_income::net_interest_income`), and an audit of all 83
addresses found no second case.

---

## 2026-08-07 (cont'd) — the dashboard address is scoped by FILING FAMILY, and aliases are one rule not N facts

**Decision:** `locate_tables()` scopes every masterlist entry to the document's
own `source_family` BEFORE scoring it by content. A table type may only claim a
table inside a document of its own kind.
**Why:** content score alone is not a bar. OCBC's 14-leaf `FS_INCOME_SELECTED`,
authored off the media release, cleared `MIN_MATCH_FRACTION` against the
consolidated income statement *inside the financial statements* and re-stamped
16 rows from `FS_INCOME_CONSOLIDATED` to `FS_INCOME_SELECTED`. The leaves stayed
correct; the ADDRESS changed, and the anchors join on
`(table_type_id, canonical_leaf_id)` — so six OCBC dashboard lines went blank
while `stamp_tables` still reported them "resolved". Family is structural: the
wrong type is never scored at all. OCBC's denominator fell 643 -> 522 and the
resolve rate rose 72% -> 76%.
**Discarded:** per-bank alias entries for the interim/annual naming. The corpus
prints FOUR names for one family — OCBC `Condensed Financial Statements` /
`Unaudited Interim Financial Statements`, UOB `condensed-financial-statements` /
`Condensed Interim Financial Statements`. Two banks doing the same thing is a
convention, so `_FAMILY_QUALIFIER = {condensed, interim, unaudited, audited}` is
dropped in `norm_family()` and all four collapse to `financial_statements` while
`media_release_financial_highlights` stays distinct.
`masterlist_source_family_aliases.yaml` is kept as the escape hatch and is
CURRENTLY EMPTY.
**Discarded:** folding curated leaf aliases into `by_norm`. `by_norm` is also the
`MIN_MATCH_FRACTION` denominator (`resolve_canonical_leaf.py`, `want = set(e["by_norm"])`),
so an alias would have raised the bar for finding a table on every vintage that
prints the CURRENT name. `by_alias` is a separate index, counted in the
numerator only — an alias can help a match, never make one harder.

---

## 2026-08-07 (cont'd) — the highlights dashboard states its consolidation basis

**Decision:** `_ANCHOR_SQL` filters
`COALESCE(c.legal_entity,'CONSOLIDATED') = 'CONSOLIDATED'`.
**Why:** a balance sheet prints the same line twice — OCBC's `GROUP` and `BANK`
column banners each carry a 30 June 2026 child, so `Total assets` arrives as both
729,887 and 477,550. `dedupe_by_latest_document`'s key has no entity and its
`doc_period` tie-break ties, so the survivor was decided by row order: feeding
the same rows reversed yielded the BANK figure for a headline metric. The group
number was correct only by accident of `col_id 1 < 3`.
**Discarded:** stamping a new `col_role='entity_banner'` and self-joining
`col_dim` on `col_parent`. Unnecessary — `col_dim.legal_entity` already exists and
is already populated by the loader against `legal_entity_dim`
(148 CONSOLIDATED / 9 BANK_SOLO / 8 PARENT_COMPANY). It also would have required
a re-LOAD, which `--rebuild-db` cannot currently do (see below).
**Note:** `COALESCE` is load-bearing — 1,444 of 1,609 columns have NULL
`legal_entity` (single-entity tables) and `schema_v7.sql` defines NULL as
consolidated. A bare equality would drop them all.

---

## 2026-08-07 (cont'd) — a continuation flag must not outlive the decision it feeds

**Decision:** `transforms.is_true_continuation()` is the ONE test, and
`resolve_continuations()` clears `continued_from_previous` on every table that
survives as its own. Called from `load_v7.load_units` and both `PASS2_v2`
bucketing sites.
**Why:** `load_v7._load_table` refuses any table still carrying the flag, but
NOTHING on the load path honoured that contract —
`run_doc.build_units_from_audit` reads each unit's `parsed.json` verbatim and
never passes through PASS2_v2's bucketing. Gemini set the flag on UOB 2Q26 p6
(title "Financial Highlights (cont'd)", first row the section header "Key
financial ratios (%) (cont'd)"), PASS2 correctly declined to merge it, and the
stale boolean aborted the ENTIRE document — 47 units, 892 rows. Measured: exactly
1 table of ~700 across all 24 ingested docs carries the flag, so the change is a
provable no-op for everything already loaded.
**Discarded:** relaxing the loader's raise to a warning. The contract is right;
it was being handed a contradiction ("I am a fragment" by a self-describing
table). Fix the lie, not the detector.
**Also:** the two PASS2 copies had DRIFTED — the cached-unit path tested 3
conditions, the live path 5 — so an identical table merged or not depending
purely on whether its unit happened to be cached. Now one implementation.
**Open:** the flag was SEMANTICALLY right, and the right end state is ONE table.
UOB prints "Key financial ratios (%)" across pages 5-6; the TOC split it into two
units (`financial_highlights`, `financial_highlights_cont_d`), so one 27-leaf
masterlist entry is split 8/15 across two tables and only the 15-half clears the
13.5 threshold — NIM / Cost-income / NPL never stamp, in BOTH vintages.
It is NOT a row concat: the pages carry DIFFERENT column sets —
p5 `['1H26','1H25','+/(-)%','2H25','+/(-)%']` vs p6 `['1H26','1H25','2H25']`,
which is exactly why `is_true_continuation`'s equal-column-count test rejects it.
Rejoining means aligning p6's columns onto p5's BY PERIOD (`col_period` +
`period_span` identify each one) and leaving the two variance columns null for
p6's rows. Deterministic, and it generalises: a continuation that drops
derived/variance columns on the carry-over page is a normal print convention.
Threshold tuning would be the wrong fix — it treats the symptom and still leaves
two tables where the filing prints one.

---

## 2026-08-07 — one ingest DB rebuilt clean; concept layer opt-in; three normalisation/period gaps closed
**Decision:** collapse to ONE v7 ingest DB (`compiled_fs.db`, rebuilt clean from
  `schema_v7` via `--rebuild-db --only`) plus `compiled_v2.db` as a generated
  view; make the concept layer opt-in behind `--with-concepts`.
**Why:** the DBs had forked without anyone deciding to. `compiled_fs.db` was
  `run_doc.py:52`'s `DEFAULT_DB` but predated `f48599a`, so it lacked
  `row_dim.canonical_leaf_id` and `_stamp_identity` died on it with
  `no such column`. `compiled_2q26.db` — a per-run copy from the DBS 2Q26
  session — was the only current-schema DB and had silently become the working
  one. A clean rebuild fixes both at once because every doc replays from audit
  units on disk at $0. The concept layer went opt-in because nothing in the
  serving path reads it: `build_compiled_v2.py` drops `fact_metric` by design,
  the app's only reader is `run_opt(SELECT * FROM v_fact_metric_serving)` at
  `findociq_app.py:1434` and that view exists in NEITHER DB, and STEP 3b/4b were
  *crashing* on `table_registry` (D6), aborting runs before verify and xlsx.
**Discarded:** migrating `compiled_fs.db` in place with three `ALTER TABLE ADD
  COLUMN`s — it would have preserved 33.6MB of accumulated concept-layer
  history that `compiled_v2.db` discards anyway. Measured: of the 33.6MB,
  `ix_segment`+`ix_geo`+`ix_concept` alone are 10.8MB and
  `concept_resolution_log` 1.57MB, against 5.70MB of actual `cell_fact`.
**Discarded:** rebuilding `compiled_v2.db` straight from `compiled_fs.db` at the
  time — it would have dropped `DBS_2Q26_performance_summary`, which existed
  only in `compiled_2q26.db` (measured: `in v2 NOT in fs: ['DBS_2Q26_…']`).

## 2026-08-07 — a bare-year column may not resolve past the reporting date
**Decision:** `clamp_bare_year_to_doc_period` (`load_v7.py`) — a bare-year column
  period later than `doc_period` is re-read as `doc_period`, with the cumulative
  span ending there. Prior-year bare columns are untouched.
**Why:** a bare year is the one period form with no month or day, so the column
  grammar resolves it to 31 December. That is right for a year-end filing and
  every OCBC FS in the corpus was 4Q. OCBC 2Q26 is the first HALF-YEAR interim:
  its Level 3 movements tables print `group='2026'` meaning the six months to
  30 June 2026, and the loader stamped `2026-12-31` — a date that had not
  happened. The invariant is universal (no filing reports a period ending after
  its own reporting date), so it holds for any bank and any vintage.
**Discarded:** inferring what the PRIOR-year bare column covers (full year vs
  the comparable half). Genuinely ambiguous per table, and guessing would
  manufacture a period the filing never states.

## 2026-08-07 — the footnote stripper must handle the `N/` marker convention
**Decision:** `_TRAILING_FOOTNOTE` accepts an optional `/` after each marker.
**Why:** OCBC prints footnote references as `8/ 9/`. The old pattern treated `/`
  only as a SEPARATOR between digits (`8/9`), so a trailing slash defeated the
  `$` anchor and nothing stripped — the marker digits survived into the id.
  The masterlist authored off 4Q25 keyed `capital_adequacy_ratios_8_9`; 2Q26
  RENUMBERED its footnotes and keyed `capital_adequacy_ratios_8`. Same printed
  row, different address. Measured: `Key Financial Ratios (%)` matched 8 of 22
  masterlist leaves against a `MIN_MATCH_FRACTION` threshold of 11, so
  `locate_tables` never returned it and all 22 ratio leaves went unstamped.
  After the fix OCBC @1H26 went 17/26 → 23/26 rendered lines, recovering NIM,
  cost/income, ROA, ROE, NPL and CET1. Footnote renumbering between filings is
  normal for every bank, so this is a grammar gap, not a per-document quirk.
**Discarded:** stripping trailing digit-runs from the normalised path
  (`re.sub(r'(_\d+)+$','',seg)`). It reads as a fix — 8/22 → 17/22 in a scratch
  test — but it operates after the meaning is gone and would eat real ordinals
  (`Tier 1`, `Level 3`, `Basel 3`). The correct place is the marker grammar,
  before normalisation.
**Note (latent):** the bare-digit branch already strips MEANINGFUL trailing
  ordinals — `Tier 1` and `Tier 2` both normalise to `tier`. No collision today
  only because OCBC's masterlist carries `Tier 1` without `Tier 2`; adding
  `Tier 2` would silently make one of them unmatchable.

## 2026-08-07 — dashboard anchors are selected by COLUMN, not by filename
**Decision:** `load_dashboard_anchors` globs `*{suffix}`, not `{bank}*{suffix}`.
**Why:** the anchor set became one cross-bank pair
  (`highlights_dashboard_anchors.csv` + `highlights_formulaanchors.csv`). The
  bank-prefixed glob matched nothing, and the failure was SILENT — every bank
  returned 0 items. Selection by bank was always the `bank` column filter inside
  the loop; the filename never carried meaning. Per-bank files still match, so
  both layouts work. Measured after: DBS/OCBC/UOB load 26 display lines each.

---

## 2026-08-06 — audit writes at COLUMN level, not file level; archive (not delete) the spoilt OCBC 4Q25 Condensed artifacts
**Decision:** change the single-writer audit from "how many FILES write this
  table" to "how many files write this COLUMN", and keep a per-table ownership
  map in the report.
**Why:** the file-level count could not separate the design from the bug. Two
  passes writing disjoint columns of one table is correct by construction —
  `load_v7` lays the row down, a stamping pass adds its own. A shared column is
  where one pass silently overwrites another's decision. The coarse check
  reported 12 multi-writer tables and gave no way to act on any of them; the
  column check cleared two of them outright on the first run:
  `quarantine_f2_geo_wildcard` writes exactly one column of `cell_fact`
  (`review_status`, never a value column), and `resolve_canonical_leaf` writes
  exactly one column of `row_dim` (`canonical_leaf_id`, never `row_leaf_label`
  or `row_parent`).
**Discarded:** treating every shared column as a violation — evidence: the
  column check's own blind spot is ROW-disjointness. `fact_metric`'s 20 "shared"
  columns are `build_fact_metric` vs `compute_ratios`, and
  `compute_ratios.py:97` scopes its DELETE to `resolved_by = 'formula'` — a
  disjoint row population, not a clobber. Same shape for `concept_map`. Every
  real safety property in this subsystem is enforced on rows, not columns, so
  the check narrows the field but still needs a human read per hit.

**Related finding — D6 is a missing CREATE, not a schema-version mismatch.**
  `table_registry` has no live creator: the only non-test
  `CREATE TABLE table_registry` lived in
  `archive/.../migrations/migrate_add_mapping_layer.py:38`, archived the same day
  in `b0e039d`; `seed_registry.py:47` only INSERTs. Evidence: `compiled_fs.db`
  has the table and 303/375 typed tables (it was migrated while that code was
  live); every freshly-built DB has neither (`compiled_v2.db`: 25/410, and those
  25 come from `stamp_tables`, not `classify_corpus`). **Discarded:** the claim
  that this blocks a dashboard-visible acceptance bar — evidence: the app reads
  `compiled_v2.db` (`findociq_app.py:41`), which drops the concept layer by
  design and contains no `fact_metric` table at all, and `4d3cd50` un-gated Key
  Financial Highlights from `fact_metric`. The registry is needed for the
  concept/analytics layer (`build_fact_metric.py:316` joins it UNGUARDED), not
  for the leaf-addressed highlights path.

**Decision:** archive the spoilt `OCBC_4Q25_Condensed_Financial_Statements`
  artifacts under `archive/2026-08-06-ocbc-4q25-condensed-spoilt/` rather than
  `rm` them.
**Why:** the 23 cached audit units are what makes a zero-cost replay possible —
  the mechanism used on 2026-08-04 to prove the end-to-end path without Gemini
  spend. Deleting them makes the only route back a paid re-extraction, and
  `--dry-run` is still ignored for a single `--pdf` (D5), so there is no cheap
  safety net. Reversibility was worth 1.1 MB.
**Why they were spoilt:** ingested 2026-07-29 10:25, one day before
  family-aware output paths landed. The router classified the document correctly
  the whole time — re-run today: `family='fs' confidence='high' flags=''` — but
  `pass2/schema.py` hardcoded `_P3_ROOT = outputs/pillar3`, so the family
  decision never reached pass2 and the doc was filed and labelled Pillar 3. This
  is exactly the defect in `docs/specs/2026-07-29-family-aware-output-paths.md`.
  Extracted cell values are unaffected; only location and labelling are wrong.
**Discarded:** the first hypothesis, that `detect_family`'s
  `if "pillar 3" in head` substring rule (`classify/family.py:122`, highest
  precedence) had fired — evidence: pdfplumber over pages 1–2 of the PDF returns
  `'pillar 3' in head -> False`, and running the real `classify()` returns
  `family='fs'`. The rule never fired; the routing was never wrong.
**Left open, deliberately:** `run_doc.py:389` branches the TOC framework on
  family (`pillar3` → deterministic `pass1_toc`, else Gemini `toc_stage`).
  Whether that branch existed on 2026-07-29 was NOT verified. If it did not, the
  archived artifacts are wrong in substance, not just misfiled. The presence of
  `_toc_raw.json` suggests it did — recorded as inference, not as a check.

**Decision-tree pivot: no** — no routing change; the router's decision was
  correct throughout and only the downstream filing was wrong.

---

## 2026-08-04 — DBS FS_PER_SHARE: a 4Q25 geometry mis-parent + a real layout restructure; fix DEFERRED past the demo window
**Decision:** do **not** fix in the demo window. Three fix options are recorded
  below, none chosen. This entry exists so the blank DBS NAV cell is a known,
  diagnosed defect rather than a mystery during the demo.

**The defect:** DBS 4Q25 `FS_PER_SHARE` has a geometry mis-parenting defect —
  `net_book_value` is stamped `row_parent='reported_earnings'` instead of `''`
  (top-level). **Document-specific, not systematic**: 1Q26 parses the same
  table correctly. Evidence, `row_dim` for the two documents that share the
  new layout:
```
  4Q25 (2025-12-31): ('net_book_value', 'reported_earnings')   <- mis-parented
  1Q26 (2026-03-31): ('net_book_value', '')                    <- correct
```
  Caveat on "document-specific": the new layout exists in exactly TWO documents
  corpus-wide, so this is 1-of-2, not a broad survey. If a third new-layout doc
  lands mis-parented, re-classify as systematic.
**Consequence:** the `human_confirmed` address for `bs.nav_per_share`
  (`net_book_value`, parent `''`) resolves against 1Q26 but not 4Q25 —
  contributing to the blank DBS NAV cell.

**Not a defect — a real disclosure restructure.** DBS restructured this table
  between 3Q25 and 4Q25. Both layouts coexist in `bank_line_map` as
  period-agnostic addresses:
```
  2022-06-30 .. 2025-09-30   header 'per_basic_and_diluted_share'
                             -> earnings / reported_earnings / net_book_value
                                (+ earnings_excluding_one_time_items in 2Q22)
  2025-12-31, 2026-03-31     headers 'earnings' / 'reported_earnings'
                             -> basic / diluted;  net_book_value top-level
```
  7 `ai_proposed` old-layout (`mapped_by='backfill:corpus'`, 09:40:31) +
  5 `human_confirmed` new-layout (`mapped_by='dashboard_rows.yaml'`, 02:17:56)
  = **12 accumulated addresses for a table that currently prints 7 rows.** This
  is `bank_line_map` behaving as designed (additive, period-agnostic), and it is
  the concrete case behind the masterlist spec's "12 addresses for a 7-row
  table" example — see `docs/specs/2026-08-04-masterlist.md` §1.

**Blast radius:** 3 KPH cells for DBS 4Q25 — `bs.nav_per_share`,
  `pnl.eps.basic`, `pnl.eps.diluted`.

**CORRECTION to the initial read — the alternate path covers ONE of the three,
  not all three.** `row_dim.concept_key` was NOT wiped by the 2026-08-04
  `bank_line_map` cleanup, so it is a live alternate resolution path (not used
  by the dashboard today). But it only helps NAV. Measured:
```
  DBS 4Q25, row_dim rows carrying any of the 3 concepts:  1
    FS_PER_SHARE row 7 'Net book value5' -> bs.nav_per_share
  DBS, ANY period, pnl.eps.basic / pnl.eps.diluted in row_dim:  0 rows
```
  4Q25 `FS_PER_SHARE` rows 2 and 5 ('Basic') and rows 3 and 6 ('Diluted9') all
  carry `concept_key = NULL`. **EPS basic/diluted have no `row_dim` fallback for
  any DBS period.** Any recovery plan that assumes "row_dim still has them" is
  wrong for 2 of the 3 cells.

**Deferred fix options (none chosen):**
  1. Surgical `row_dim` UPDATE for the mis-parented 4Q25 row. Narrowest; fixes
     NAV only, fixes nothing for EPS, and edits extracted data rather than the
     stage that produced it.
  2. Extend `load_anchors` to support multi-address aliases so one concept can
     carry layout history across a restructure. This is the M2 alias mechanism
     (`canonical_leaf_alias`) applied to M3 — the restructure above is exactly
     the case `canonical_leaf.deprecated_quarter` + bridging aliases were
     designed for, and it has never been exercised (OCBC's build produced 0
     auto-aliases).
  3. Fix the geometry stage so the mis-parent cannot recur. Only option that
     prevents recurrence; largest blast radius; needs the 1-of-2 caveat resolved
     first.
**Why deferred:** all three touch either extracted data or a stage the demo
  depends on. A blank cell with a written diagnosis is safer in the demo window
  than a rushed change to geometry or the anchor loader.
**Related:** the DBS `pending_extraction` rows in `lineage_identity_map.csv` for
  these same 3 concepts (`docs/m3-cleanup-report.md`) — this parse ambiguity is
  a plausible reason they were never promoted to `resolution='anchor'`.

**2026-08-04 CORRECTION — the blast-radius claim above ("contributes to the
  blank DBS NAV cell") is wrong.** Runbook execution
  (`docs/runbook-execution-2026-08-04.md`) verified the DBS NAV cell reads
  **24.29 for FY25 and 2H25**, sourced from `DBS_4Q25_performance_summary` /
  `'Net book value5'`. `backfill_map.py`'s corpus-stamped `ai_proposed` row at
  the mis-parented address (`'net_book_value'`, `'reported_earnings'`) carries
  the `concept_key` from `row_dim`, so the value flows through despite the
  address mismatch against the `human_confirmed` anchor (`'net_book_value'`,
  `''`). **The geometry defect is real; its consequences are absorbed.** Full
  mechanism: the three-writer M3 layer plus the `row_dim.concept_key` stamp both
  cover for it.
  Two further corrections to the entry above, from the same run:
  - **EPS was never in the blast radius at all.** `dashboard_rows.yaml` authors
    `pnl.eps.basic` and `pnl.eps.diluted` under *both* parent variants
    (`'earnings'` and `'reported_earnings'`), so the 3Q25→4Q25 restructure is
    already bridged for them. Verified values: basic 3.88 FY / 3.71 2H, diluted
    3.86 FY / 3.69 2H.
  - The "alternate path via `row_dim.concept_key`" is not merely available — it
    is **the path actually in use** for NAV today, via `backfill_map`. The entry
    above described it as unused by the dashboard; that was wrong.
  What remains true: the mis-parent itself (4Q25 stamps
  `row_parent='reported_earnings'`, 1Q26 stamps `''`), the layout restructure,
  the 12-addresses-for-7-rows accumulation, and the deferral decision. The three
  fix options stand — but their justification is now hygiene and future-proofing,
  **not** a broken dashboard cell. Priority should drop accordingly.

---

## 2026-08-04 — `load_anchors.py` branch selection has no test coverage
**Decision:** record the gap now, add the test later. The `concept_key` guard fix
  that landed today (added `old_ck is not None` at `load_anchors.py:131` — line
  130 pre-fix) is **unprotected by any regression test.**
**Why it matters:** `test_mapping.py` covers `apply_dashboard_rows`' retirement
  logic but does not touch `load_anchors` at all — no test exercises its branch
  selection. Nothing would have caught the original bug, and nothing now stops
  someone "simplifying" the guard back out. The added inline comment is the only
  protection, and comments do not fail CI.
**Future task:** add a regression test asserting that a `human_confirmed` row
  with a NULL `concept_key` gets **superseded in place, not raised** — i.e. that
  `load(con)` returns `superseded_placeholder >= 1` and does not `SystemExit`.
  The fixture is small: one `bank_line_map` row at `human_confirmed` with
  `concept_key=NULL` at an address the CSV anchors.
**Cheap task, high leverage — this class of bug is invisible until a data state
  triggers it.** Today's trigger was the M3 cleanup wiping `concept_key` while
  leaving `map_status`, a state that had never existed before; the script had
  been correct for every prior run and failed the first time the assumption was
  violated. Evidence it was a real stoppage, not a theoretical one: the abort
  message and the 15 affected rows are in
  `docs/runbook-execution-2026-08-04.md` "Root cause".

---

## 2026-08-04 — The masterlist is a pipeline component with ONE writer per level
**Decision:** `docs/specs/2026-08-04-masterlist.md` is the single authoritative
  description of the masterlist. It splits it into **L1 (table level —
  declared/hand-authored)** and **L2 (line item level — derived by code)**, and
  imposes: **one writer script per level.** A new consumer READS the stores; a
  new *source* of masterlist state is a change to the existing writer, not a
  new script beside it. Script docstrings link to the spec rather than
  re-describing the masterlist.
**Why:** the user's instruction — "this is part of our pipeline, don't have
  multiple scripts to store the masterlist." The description had fragmented
  across 3 script docstrings, `MAPPING_LAYER.md`, and 4 PROGRESS blocks, each
  partially right; the drift was already visible as a live confusion about
  whether `bank_line_map` IS the masterlist (it is not — it's a period-agnostic
  additive union, measured: DBS `FS_PER_SHARE` has 12 accumulated addresses for
  a table that only ever prints 7).
**Discarded:** *materializing the rendered masterlist to a table/CSV so the app
  reads one place* — rejected: `table_masterlist_frame()` and
  `line_item_benchmark_frame()` are pure joins over `table_catalog` × live
  `table_t` and over `row_dim` @ benchmark period; persisting their output
  creates a THIRD copy that goes stale on every ingest, which is the exact
  failure the one-writer rule exists to prevent. Renderings stay computed.
**Debt recorded, not fixed:** L1 has TWO writers today —
  `migrate_add_table_catalog.py` (from `data/derived/table_registry_seed.csv`)
  and `seed_registry.py` (from `pipeline/mapping/table_registry.yaml`). This
  violates the rule the spec just set. Evidence it's historical rather than
  designed: the seed CSV's `table_type_id` vocabulary renames or folds **11 of
  the YAML's original 26 ids** (`migrate_add_table_catalog.py:58-62` lists the
  renames, e.g. `FS_NPA -> FS_NPA_COVERAGE`), i.e. the CSV was authored later,
  against the real 4Q25 documents, over a vocabulary that already existed.
  Tiebreak stated in the spec so the split cannot silently become a
  disagreement: **the seed CSV wins.** Convergence deferred — it touches two
  idempotent migrations plus the app's catalog query.

---

## 2026-08-04 — M2 gate built for OCBC: canonical_leaf + canonical_leaf_alias, 3 explicit decisions
**Decision (order source):** canonical `position` comes from **(c)/(a) hybrid
  — the real printed row order within the bank's `4Q25` benchmark document
  instance**, falling back to the most recent available period if a
  table_type_id has no `4Q25` capture for this bank (flagged in
  `canonical_leaf.notes` when that happens, not silent). NOT a hand-provided
  ordered list (would require manual curation per table_type, and the
  document already IS the authoritative order) and NOT a bare `row_id` sort
  across the whole canonical table (rejected explicitly -- see Discarded).
  Reuses the exact selection logic `app/findociq_app.py`'s
  `line_item_benchmark_frame` already built and shipped this session for
  the same reason, reimplemented standalone in
  `pipeline/mapping/m2_canonical_leaf.py` (pipeline code must not import a
  Streamlit module) rather than refactoring the shared logic out --
  flagged as real DRY debt (3 near-identical copies of the title-like-
  parent-collapse rule now exist: `stamp_human_anchors`, the app's
  `_ordered_row_addresses`, this one), not fixed under this task's budget.
**Decision (alias resolution priority):** accepted the task's proposed
  default as-is: **exact current label > alias table > deprecated leaf
  label > unresolved** (`resolve_address`'s exact branch order). Verified
  with a dedicated test (`test_resolve_address_deprecated_leaf_lower_
  priority_than_alias`) that an alias wins over a deprecated leaf's own
  direct address, not just over nothing.
**Decision (block vs. warn):** accepted the task's proposed default:
  **warn-only for now.** The gate function (`resolve_address`) exists and is
  tested, but nothing in Stage 1 extraction or `run_doc.py` calls it --
  wiring it into ingest as a hard block is future work, explicitly out of
  this task's scope ("No changes to extraction pipeline"). "Block once
  verified" isn't reachable yet because there is no ingest call site to
  block from.
**Why:** per-bank identity persistence (M2) needs an actual enumerated,
  ordered leaf set to gate against -- `bank_line_map` alone can't serve
  this because it's an unbounded, additive UNION of every address ever
  seen (footnote variants, mis-parented defects, addresses from document
  forms that no longer exist), exactly the problem the 4Q25-benchmark
  line-item view was already built to solve for display; M2 needed the
  same fix at the schema/gate level, not just the UI level.
**Findings, not silently absorbed:**
  - Built for all 13 table_type_ids currently backing an OCBC `fact_metric`
    row (post-dedup): 364 canonical leaves, 0 auto-aliases (OCBC's specific
    drift isn't the footnote-suffix pattern the alias heuristic targets --
    it's genuinely different table layouts across periods, confirmed by
    inspection, not a matching gap).
  - `verify_fact_metric`: 327/608 OCBC `fact_metric` rows resolve; 281 don't.
    Traced one concretely (`FS_ALLOWANCES` / "Non-performing loan (NPL)
    ratio") to real cross-period drift: the 4Q25 benchmark table for that
    type is an allowances-breakdown table with no NPL-ratio row at all --
    an older period's `FS_ALLOWANCES` table was a genuinely different
    physical table. This is the M2 gate doing its job, not a bug.
  - `verify_concept_bindings` (M3): 99/240 OCBC bindings resolve. Of the 4
    shifted concepts: `pnl.eps.basic`/`pnl.eps.diluted` each have >=1
    resolving binding (their primary `human_confirmed` route via
    `FS_INCOME_STATUTORY` is untouched by the dedup fix). `reg.capital.
    cet1_ratio` has several resolving routes. **`bs.nav_per_share` has
    ZERO resolving bindings** -- both its bank_line_map addresses
    (`FS_BALANCE_STATUTORY`, `FS_RATIOS_KEY`) point at rows that don't
    exist in either table's 4Q25 benchmark structure. Flagged prominently
    in `docs/m3-ocbc-concept-binding-check.md`'s summary table, not fixed
    (fixing it would mean editing a `bank_line_map` address, explicitly out
    of scope: "Any modification to bank_line_map addresses themselves").
**Discarded:** row_id sort within the canonical table as a bare, standalone
  option (distinct from the benchmark-instance version actually chosen) --
  `row_id` is scoped per `table_id`, and OCBC genuinely has more than one
  physical table sharing a single `table_type_id` in the same document
  (confirmed: `FS_CAPITAL_ADEQUACY` has both a page-12 summary table and a
  page-20 detailed capital-components table); a bare row_id sort across
  all of a table_type's rows without table_id as the primary sort key would
  silently interleave two unrelated tables (this exact failure mode was
  caught live while building `_ordered_row_addresses` for the earlier
  benchmark-view feature, fixed there by sorting on `(table_id, row_id)` --
  reused here for the same reason).

### Bug found and fixed while building this: `quarantine_duplicate_page_tables.py`'s canonical pick could tag the wrong document
**Decision:** fixed `pick_canonical`/`quarantine()` to carry `(doc_id,
  table_id)` pairs through end-to-end instead of re-deriving `doc_id` from a
  bare `table_id` via `SELECT doc_id FROM table_t WHERE table_id=? LIMIT 1`.
**Why:** `table_id` is unique only WITHIN one `doc_id`, not across the whole
  corpus. Confirmed live: `loans_to_customers_loans_to_customers_2025-12-31`
  exists under two different OCBC documents --
  `OCBC_4Q25_Condensed_Financial_Statements` (a completely unrelated,
  legitimate table, not a duplicate of anything) and
  `OCBC_4Q25_Media_Release_and_Financial_Highlights` (the actual 5-member
  duplicate cluster on page 16, alongside `by_currency`/`by_geography`/
  `by_industry`/`by_maturity`). The old `LIMIT 1` query, with no `ORDER BY`,
  non-deterministically resolved to the WRONG document for this specific
  table_id: it tagged the Condensed Financial Statements table (never a
  duplicate) as `dedup_status='duplicate_page_split'`, excluding it from
  `fact_metric`/masterlist candidacy for no reason, while the REAL Media
  Release duplicate kept `dedup_status=NULL` and stayed uncounted as a
  duplicate. Found via an M2-gate side effect: `FS_CUSTOMER_LOANS`'s
  canonical-position sequence had large gaps (max position 78 for 42
  leaves), which led to tracing `select_benchmark_rows`'s output back to
  this exact table_id collision.
**Discarded:** nothing -- this was a pure bug fix, not a design choice with
  an alternative. Re-ran the full downstream cascade after fixing it
  (`quarantine_f2_geo_wildcard.py` still 0, `stamp_human_anchors` still 303,
  `fact_metric` rebuilt) to confirm no other invariant broke.
  Regression test added (`test_quarantine_duplicate_page_tables.py`)
  constructing exactly this shape (one table_id shared across two
  unrelated documents) -- would have caught this before it shipped.

---

## 2026-08-04 — Quarantined 17 duplicate-extraction table clusters (OCBC media-release doc_kind, 49/375 table_t rows); dedup wired into masterlist + fact_metric
**Decision:** new `pipeline/mapping/quarantine_duplicate_page_tables.py`
  (same tag-don't-delete pattern as `quarantine_f2_geo_wildcard.py`): finds
  every (doc_id, page_range) where 2+ `table_t` rows share a byte-identical
  sorted `cell_fact.value_raw` tuple, picks ONE canonical member per cluster
  (shallowest max `row_lineage.depth`, tie-broken alphabetically by
  `table_id`), and tags the rest `table_t.dedup_status='duplicate_page_split'`
  (new nullable column, additive). Wired the exclusion into: the Table
  Registry masterlist's live-occurrence query and its line-item benchmark
  query (`app/findociq_app.py`), and `build_fact_metric.py`'s `_fetch_levels`
  query (column-existence-checked, so a DB predating this migration, or a
  synthetic test DB, still works unfiltered).
**Why:** surfaced while building OCBC's per-share line-item masterlist (user
  request) -- OCBC's 4Q25 media-release page 12 ("FINANCIAL HIGHLIGHTS
  (continued)") turned out to be extracted 8 SEPARATE times as 8 different
  `table_t` rows, one per section-header on the page
  (`capital_adequacy_ratios_8_9`, `earnings_per_share_s_2`,
  `leverage_ratio_5_8_9`, `liquidity_coverage_ratios_6_8`,
  `net_asset_value_per_share_s`, `net_stable_funding_ratio_7_8`,
  `performance_ratios`, `revenue_mix_efficiency_ratios`), each with
  byte-identical 136-cell values -- confirmed the table-detection stage is
  creating one table per section-header instead of recognizing one
  continuous table. Corpus-wide: 17 clusters, 49/375 `table_t` rows (13%),
  ALL isolated to OCBC's `media_release_financial_highlights` doc_kind
  across every period it appears in (1Q25/1H25/3Q25/4Q25) -- not DBS, not
  UOB, not OCBC's `condensed_financial_statements` doc_kind. Every
  duplicate's row_dim rows were separately backfilling `bank_line_map`
  (inflating the masterlist) and separately entering
  `build_fact_metric.py`'s conflict-resolution candidate pool (inflating
  apparent conflicts) for what is really ONE real value.
  Root cause (WHY the table-detection stage splits by section-header) is
  explicitly NOT investigated here -- user chose "quick stopgap now" over
  "investigate root cause first" when asked directly (3-way tradeoff
  question), given the size of a real table-detection fix vs. the size of
  today's ask.
**Discarded:** leaving the duplicates unaddressed and building the OCBC
  per-share masterlist directly off whichever table happened to classify
  `FS_PER_SHARE` -- would have shown a MISLEADING, incomplete view (2 of the
  8 duplicates, `earnings_per_share_s_2` and `net_asset_value_per_share_s`,
  classified `FS_PER_SHARE`; the canonical winner after dedup classified
  `FS_CAPITAL_ADEQUACY` instead, since classification depends on each
  duplicate's OWN section-title match, not a stable property of the
  underlying page) and would have kept inflating `fact_metric`'s conflict
  count for `bs.nav_per_share`/`pnl.eps.basic`/`pnl.eps.diluted`/
  `reg.capital.cet1_ratio` (confirmed: these 4 concepts' corpus-wide SUM
  dropped by exactly the duplicate contribution after this fix, and ONLY
  these 4 changed -- verified against a pre-dedup snapshot rebuild).
  Also discarded (a real bug caught while building this, not the original
  plan): sorting benchmark rows by `row_id` alone in
  `_ordered_row_addresses` -- `row_id` restarts at 1 per `table_id`, and
  OCBC genuinely has TWO different physical tables both classified
  `FS_CAPITAL_ADEQUACY` in the same document (page 12's combined summary +
  page 20's detailed capital-components breakdown) -- sorting by `row_id`
  alone interleaved their rows by coincidental row_id collision instead of
  keeping each table's rows contiguous. Fixed to sort by
  `(table_id, row_id)`; caught via direct verification against the live DB
  before shipping, not via a test that happened to catch it.
  Coverage side-effect flagged, not silently absorbed: after dedup, the
  Table Registry masterlist's "times_captured" count for
  `REG_LEVERAGE`/`REG_LCR`/`REG_NSFR`/`FS_RATIOS_KEY` will show FEWER OCBC
  occurrences on this exact page than before (the content is still there,
  now correctly attributed to one physical table's classification instead
  of counted once per duplicate) -- a known, expected consequence of the
  fix, not a new coverage gap.

---

## 2026-08-04 — Consolidated 6 duplicate table_type_id pairs; corpus re-stamp `migrate_add_table_catalog.py` had explicitly deferred
**Decision:** ran the corpus re-stamp `migrate_add_table_catalog.py`'s own
  docstring called "a separate, explicitly out-of-scope follow-up" — for the
  6 of its 11 documented `RENAMED` pairs that are a clean 1:1 rename with
  zero `bank_line_map` address collisions (verified by direct query first):
  `FS_NPA`→`FS_NPA_COVERAGE`, `FS_SEGMENT_INCOME`→`FS_PERF_BY_SEGMENT`,
  `FS_GEO_INCOME`→`FS_PERF_BY_GEOGRAPHY`, `FS_NII_ANALYSIS`→`FS_NII_DETAIL`,
  `FS_OPEX`→`FS_EXPENSES_DETAIL`, `FS_CAPITAL`→`FS_CAPITAL_ADEQUACY`. New
  script `pipeline/mapping/migrate_consolidate_table_type_ids.py`: (1) copies
  the OLD id's real `table_registry` metadata (`statement_class`,
  `period_nature`, `dim_hint`, ...) onto the NEW id's row — the NEW ids had
  been auto-inserted by `migrate_add_table_catalog.py` with a placeholder
  (`statement_class='unclassified'`, `dim_hint=NULL`); (2) renames
  `table_t.table_type_id`, `bank_line_map.table_type_id`,
  `table_registry_alias.table_type_id` old→new together, so the
  `stamp_human_anchors` join stamp stays identical. Also updated
  `pipeline/mapping/table_registry.yaml` (the 6 `id:` fields) and
  `quarantine_f2_geo_wildcard.py`'s hardcoded `_AFFECTED_TABLE_TYPE` — both
  had to move with the rename or the next `seed_registry.py`/ingest run
  would have silently reverted it (see Discarded).
**Why:** user noticed "multiple duplicated registry for tables" while
  looking at the new Table Registry masterlist tab. Root cause: real
  captured data sat under the OLD id (e.g. DBS's NPA table: 36 `table_t`
  rows, 181 `bank_line_map` rows under `FS_NPA`) while the masterlist
  (`table_catalog`, from `table_registry_seed.csv`) looks for the NEW id
  (`FS_NPA_COVERAGE`) and saw 0 live occurrences — a duplicate-vocabulary
  bug, not a genuine coverage gap. Confirmed after the fix: all 6 renamed
  types now show real counts across all 3 banks in the masterlist (e.g. DBS
  `FS_NPA_COVERAGE` 15 captures / 4 docs; previously 0).
**Discarded:** doing only the `table_t`/`bank_line_map`/`table_registry_alias`
  UPDATE without touching `table_registry.yaml` — tried first, looked
  correct, but re-running `seed_registry.py` (part of the normal ingest
  driver, `c51efc8`) reseeds `table_registry_alias` from the yaml and
  re-runs `classify_corpus`, which silently REVERTED the rename back to old
  ids (confirmed: `table_t` old-id count went 0 → 124 after one
  `seed_registry.py` run). The yaml itself is the actual source of truth for
  future ingests, not just the DB snapshot.
  Also discarded: renaming the remaining 5 `RENAMED` entries in the same
  pass. `REG_LCR`/`REG_LEVERAGE`/`REG_NSFR`/`REG_KEY_METRICS` all fold into
  `FS_RATIOS_KEY`, which already has 143 of its own `bank_line_map`
  addresses — 40 of the folded addresses collide with an EXISTING
  `FS_RATIOS_KEY` address (measured by direct query). A blind rename would
  violate `bank_line_map`'s UNIQUE constraint or silently overwrite a
  reviewed row; needs a per-collision look at the data, not a script.
  `FS_ALLOWANCES` was excluded because `migrate_add_table_catalog.py`'s own
  docstring already flags it "context-dependent" (OCBC splits it into
  `FS_ASSET_QUALITY` vs `FS_ALLOWANCES_DETAIL` depending on which physical
  table; DBS/UOB fold cleanly) — same reason it wasn't auto-renamed there.
  Verification before declaring done: `stamp_human_anchors` restamped the
  same 303 `row_dim.concept_key_human` rows (was 303 before, 303 after);
  `quarantine_f2_geo_wildcard.py` (which hardcodes the affected
  `table_type_id`, now updated) still tags 0 cells both before and after,
  consistent; rebuilt `fact_metric` and diffed concept-level sums against
  both the live DB and a fresh rebuild from the pre-migration snapshot
  (`db/snapshots/pre_registry_consolidation_2026-08-04.db`) — the 11
  concepts that changed (`pnl.nii.net`, `ratio.cir`, etc.) changed by the
  IDENTICAL amount in both, proving the shift was pre-existing `fact_metric`
  staleness from an earlier session's resolver fix, not caused by this
  migration.

---

## 2026-08-04 — E2 fixed: not an accounting decision (the brief's framing) but a check taxonomy bug hiding two real data bugs
**Decision:** delegated investigation (read-only) to the same agent, then
  made the accept/reject call myself rather than let it decide unilaterally —
  this item was framed by the task brief as genuine concept-level accounting
  judgment ("gross/net basis; derived-equity formula"), the kind of decision
  CLAUDE.md's orchestration model keeps with the orchestrator. Investigation
  found **none of the 12 `failed_resolve` slots needed an accounting
  decision** — all 6 (bank,concept) combos already resolve to a correct,
  anchored CONSOLIDATED value; they failed only because E2 counted a slot
  filled solely by spans `('FY','2H')`, and every failing concept is
  `nature='stock'` (a balance, an INSTANT, not a duration) whose natural
  span is `as_at`. Proof: `bs.equity.total` is reported by ALL THREE banks
  and NONE of them print it in any period-columned exhibit — it was never
  going to have an FY/2H row by the nature of the concept, not by disclosure
  gap. Same shape as B6 and A4 this session: the CHECK's own signature was
  wrong, not the underlying data — but the investigation also surfaced two
  real, previously-invisible data bugs that had to be fixed FIRST, before
  relaxing the check, or E2 would have started "passing" on wrong numbers:
  1. **OCBC `bs.liabilities.total`/`bs.assets.total`** served a SUBTOTAL
     (502,719 / 566,079) instead of the GRAND TOTAL (612,118 / 675,688) —
     OCBC's statutory balance sheet has both `Total liabilities` (row_depth
     1) and `LIABILITIES / Subtotal Liabilities` (row_depth 2) aliasing to
     one concept, and the old tie-break (smallest magnitude) picks the
     subtotal by construction. Fixed by inserting `min_row_depth` into the
     conflict tie-break key (`tier, min_row_depth, -support, |value|`),
     ranked above support/magnitude. Corpus-wide blast radius checked
     (13 candidate groups, not just OCBC) — DBS `bs.equity.shareholders`
     (was serving 5,212/10,770/11,289 sub-lines, now 68,867/68,786),
     `bs.liabilities.deposits_casa` (was single savings sub-lines, now the
     full CASA total), `bs.assets.npa` (was serving NPL at depth 2 under an
     NPA concept), `bs.credit.allowances_*` — all corrected in the same
     direction (deeper sub-line → shallower total), zero cases where the
     deeper row was legitimately correct.
  2. **DBS `bs.assets.customer_loans_net`** was serving -23,317 (a cash-flow
     statement MOVEMENT row — the net lending during the period, per IAS-7)
     for a `nature='stock'` concept that needs the closing balance (445,011).
     This slot wasn't even in E2's original 12 (it was silently "passing"
     with a garbage value) — found because it's the same concept as the UOB
     item under investigation. Fixed generally: `_is_stock_from_cash_flow`
     excludes a stock concept from resolving off a cash-flow-statement row
     (detected via registry `statement_class='cash_flow'` or the raw
     `cash_flow`/`cashflow` slug — same two-signal pattern as F2's
     dimensional scope), scoped to `nature='stock'` only so a genuine flow
     concept can still be reported there. DBS FY 445,011 / FY24 430,594 /
     1H24 424,837; 4 garbage slots (two loan-movement rows, two `ratio.ldr`
     rows built on them, one at -3,888%) removed rather than served.
  3. **E2's span taxonomy**, now that the data underneath is correct: a
     `nature='stock'` slot is satisfied by an `as_at` row at the matching
     period-end date (2025-12-31) — an `as_at` from a different date does
     NOT qualify. Independent verification the fix is right, not just that
     the check passes: **the balance-sheet identity now holds exactly for
     all three banks** — DBS 828,572+68,916=897,488; OCBC
     612,118+63,570=675,688 (previously mismatched by 109,609 with the old
     subtotal value); UOB 520,568+51,493=572,061.
  **Follow-on fix, caught before committing**: E1's own bucket counts
  (`value + not_disclosed + pending_anchor + failed_resolve`) stopped
  summing to `slots` (150 vs 162) after the E2 fix — the `as_at`-excused
  combos were skipped out of `failed_resolve` but never added to `value`,
  so 12 slots fell into an uncounted gap and the headline coverage number
  stayed frozen at 146/162 despite E2 going from 12 failures to 0, directly
  contradicting the task brief's explicit expectation that this pass raises
  the headline number. Fixed with a precise `+=1`-per-span increment inside
  the existing FY/2H loop (not a `+=2`-per-combo shortcut, which would
  double-count a combo whose FY came from `have_slots` and whose 2H is
  excused) — verified empirically: 6 stock combos, all 6 fully excused with
  0 mixed cases, contributing exactly 12. E1's `record()` call was also
  upgraded from an unconditional `True` (a check that could never fail,
  which is why this drift went unnoticed for even one report) to actually
  asserting the bucket-sum invariant.
**Why:** verifying the check's premise before accepting the brief's framing
  is what this session has done three times now (B6, A4, E2) and each time
  the evidence, not the brief's language, decided it. The ordering
  constraint (fix the data bugs before relaxing the check) mattered because
  E2 relaxed alone would have served OCBC's `bs.liabilities.total` as a
  GREEN check next to a WRONG number — worse than the honest
  `failed_resolve` it replaced.
**Discarded:** accepting the row_depth tie-break as OCBC-specific — rejected,
  checked all 13 corpus-wide candidate groups before accepting the rule
  generally; a per-bank scope would have missed the DBS cases entirely.
  Excluding cash-flow-statement rows as a blanket exhibit rule — rejected,
  scoped narrowly to `nature='stock'` so a legitimate flow concept (e.g. an
  operating-cash-flow line) is unaffected.
**Left alone, deliberately, per this session's own no-scope-creep
  discipline**: UOB `bs.liabilities.total`'s spurious `span=NULL` 487,707
  row (a mis-dated FY24 comparative) — already-logged D2 residue, explicitly
  deferred to a future session in the D2 commit; doesn't block this
  resolution since the correct `as_at` value is a separate row. D2's other
  174 residual conflicts — not expanded into.
**Verified before committing**: `preflight_invariants.py` re-run
  independently at each stage (data fixes, then check fix, then E1
  accounting fix) — final state: **19 PASS / 2 FAIL (A4, D2, both
  pre-existing named residuals)**, both hard gates pass, headline coverage
  **146/162 -> 158/162**, buckets sum to 162 exactly. Dashboard-facing
  balance-sheet identity independently recomputed and holds exactly for all
  three banks. `pipeline/concept/` 38/38, `pipeline/pass2/` 56/56, new
  `test_grain_resolution.py` 6/6, all four session-added standalone test
  files still green.
**Decision-tree pivot: no.** Resolution/serving-layer refinements and a
  read-only invariant-report counting fix — no routing branch changes, no
  document takes a different path.

---

## 2026-08-04 — D1 fixed: unit KIND vs unit STRING split into two vocabularies; the brief's own risk assessment was inverted
**Decision:** delegated D1 to the same agent. It found the task brief's
  characterization of all 8 concepts was backwards on both halves:
  - **The 4 ratio concepts (`ratio.cir/nim/npl/roe`), brief called "pure
    string-spelling variants"**: actually a genuine 100x scale bug. `%` rows
    are percentage points (CIR 37.4-45.8); `percent` rows are FRACTIONS
    (CIR 0.21-0.72, NPL 0.005-0.016 vs the correct 0.9-1.6). Relabeling
    `percent`->`%` without rescaling — the "just spelling" read — would have
    made a real 100x discrepancy invisible, the worst possible outcome for a
    dashboard-facing ratio.
  - **`bs.nav_per_share`/`pnl.eps.basic`/`pnl.eps.diluted`, brief flagged as
    "more concerning, a genuine wrong-unit candidate"**: NOT a bug. Every
    `S$m`-labeled row is per-share-scale (0.8-29.4, never millions),
    correctly labeled `'Net asset value per ordinary share ($)'` /
    `'Basic'` / `'Diluted'` — it inherited the table's `($m)` caption as a
    unit default. Concept and value are both correct; only the unit STRING
    is wrong.
  - `pnl.noninterest.other` (S$m/currency): confirmed genuine spelling
    variant, both S$m-scale.
  **Root cause (single, unifying)**: `concept_dictionary.yaml`'s `unit:`
  field is a unit KIND (`currency`/`percent`/`per_share`/`bps`); `fact_metric.
  unit` needs a concrete STRING. `build_fact_metric` wrote the printed string
  (`%`/`S$m`); `compute_ratios` wrote the raw kind verbatim
  (`r.get("unit","percent")`). Compounding it, a formula computes the
  MATHEMATICAL ratio (0.404) while `%` means percentage points (40.4), and
  only `ratio.credit_cost_bps` carried a literal `* 10000` in its formula
  string — every other percent-kind formula silently emitted a fraction.
  Also found and fixed, neither in D1's original list: 152 `unit IS NULL`
  rows (`COUNT(DISTINCT unit)` skips NULLs, so D1's own check missed them —
  now `COALESCE`d in both the gate and the report) and `ratio.credit_cost_
  bps` itself serving both `%` and `bps`.
  **Fix**: `load_dictionary._UNIT_KINDS` — kind -> (canonical string, scale).
  Concept-owned kinds (percent/bps/per_share) are a property of the CONCEPT
  (true for every bank), so the dictionary is authoritative and overrides a
  table's inherited default; currency is a property of the DOCUMENT, so
  as-loaded wins (canonical string `None`). Scale is declared ONCE here, not
  per formula, so a future ratio added with `unit: percent` is correctly
  scaled without the author remembering — removed the literal `* 10000` from
  `credit_cost_bps`'s formula, now driven by `unit: bps` alone.
  `build_fact_metric._resolve_cell_unit` generalized from percent-only to any
  concept-owned kind (kept the percent branch's existing DROP guard exactly —
  an `Interest ($m)` cell on a NIM row must stay excluded); added
  `_infer_missing_currency_units`, filling a NULL unit from the same bank's
  rows else the concept corpus-wide, ONLY when unanimous (a genuine
  multi-currency disagreement is left for the gate, not papered over).
  `compute_ratios` now writes `value * unit_scale(kind)` and
  `canonical_unit(kind)`, with currency metrics inheriting their unit from
  the formula's actual inputs. New `validate.assert_single_unit_per_concept`,
  wired into the same hard-gate `main()` as the legal_entity assertion added
  earlier this session (NULL counts as a distinct unit).
**Why:** the brief's own framing needed correcting on the exact axis the
  session has already learned to distrust twice (B6, A4) — a stated
  characterization of "which finding is cosmetic vs genuine" turned out
  backwards. Verified by reading actual row values before accepting either
  half, not by trusting the brief's language ("pure string-spelling
  variants" / "more concerning").
**Discarded:** relabeling `percent`->`%` without a scale correction — would
  have been a silent, dashboard-facing 100x corruption disguised as a
  cosmetic fix; treating the per-share concepts as a resolution bug (wrong
  concept_key) — rejected, the concept and value are both already correct,
  only the unit string needs canonicalizing.
**Verified before committing**: dashboard-facing reference values checked
  against figures already confirmed earlier this session — UOB `ratio.roe`
  FY=9.6/2H=7.6/1H25=11.7/FY24=13.3/2H24=13.5 and OCBC 4Q25=11.6%, all
  EXACT matches, `resolved_by` shifted `conflict`->`twin_collapse` on some
  (an improvement, not a change in value). `preflight_invariants.py` re-run
  independently: D1 PASS (0), both hard gates pass (`unit_uniqueness_
  serving`: 49 concepts, 0 violations; `legal_entity_uniqueness_serving`:
  2,207 groups, 0 violations), A4/D2/E2 byte-identical, 3 FAILED of 21 (down
  from 4). All 6 `validate()` suspicion-check counts byte-identical to the
  pre-change baseline (confirms the `unit_scale` substitution is
  mathematically equivalent to the removed literal, not just
  coincidentally similar). New `test_unit_canonical.py` 6/6, `pipeline/
  concept/` 32/32, `pipeline/pass2/` 56/56.
**Residual, named not chased**: with scales now consistent, the FORMULA rows
  expose bad INPUTS previously camouflaged as fractions under a different
  unit string — `ratio.nim` max 468, `ratio.roe` max 193, `ratio.ldr` -3888
  to 9718, `credit_cost_bps` up to 106000. These are D2's already-logged
  class-A/B residue (measure-axis columns, alias over-claiming) feeding the
  formulas, not a new unit problem — the fix made them visible, which is the
  point of a correctness fix, not a regression to chase down in this pass.
**Decision-tree pivot: no.** No routing branch changes; serving-layer
  canonicalization plus a hard gate only.

---

## 2026-08-04 — findociq_app.py's Dashboard: same legal_entity bug as dashboard.py, stale docstring, 2 EPS concepts un-nulled
**Decision:** user asked to see the dashboard; investigation surfaced that
  `app/findociq_app.py` (the cobalt-themed, sidebar-nav "website-style" app,
  distinct from `app/dashboard.py`) has a stale module docstring claiming
  three of its four nav views (Database, Table Registry, Dashboard) render
  a "Coming soon" card — false. All four are fully built (~1,800 lines
  total); the docstring predates work that was never reflected back into
  it. Corrected the docstring.
  Found the Dashboard view had the SAME bug already fixed in `dashboard.py`
  earlier this session: it read raw `fact_metric` instead of
  `v_fact_metric_serving`, resurfacing Bank/Company duplicate rows. Fixed
  identically (one-line swap, same comment as `dashboard.py`'s fix).
  Separately, `app/highlights.yaml` (the Key-Financial-Highlights item
  list this view reads) had `concept: null` for "Basic earnings per
  share"/"Diluted earnings per share" for DBS, with a 2026-07-30 note
  explaining why: the deterministic (leaf-label + table_type only)
  resolver couldn't disambiguate a bare "Basic" row appearing under two
  different parent blocks ("Earnings" vs "Reported earnings"). **This
  session's Step 2 fix (the DBS per-share table's swapped title/
  label_header) unblocked exactly this** — `pnl.eps.basic`/
  `pnl.eps.diluted` now resolve via the parent-qualified anchor mechanism
  (`bank_line_map` keyed on `parent_label_norm`), not the row-scoped
  deterministic resolver the note's caveat was about. Un-nulled both,
  updated the note to explain the unblock and name the ONE nuance carried
  forward, not resolved: DBS still prints both blocks, the anchor stamps
  BOTH as the same concept, and `build_fact_metric`'s twin-collapse serves
  ONE value (3.88/3.86, the underlying basis, matching `bank_line_map`'s
  own note) — the same underlying-vs-reported split
  `pnl.profit.net_attributable` already carries a `basis` column for; EPS
  doesn't have that treatment yet. Named, not fixed — a concept-level
  decision, not a highlights-config one.
**Why:** the docstring correction prevents a future session (or a future
  me) from re-deriving "which views are built" from a stale comment instead
  of reading the code; the `v_fact_metric_serving` fix prevents this view
  from silently showing wrong Bank-vs-Group figures once someone actually
  uses it; the EPS un-null makes this view reflect what Step 2 already
  fixed and verified, rather than leaving a config file stuck describing a
  constraint that no longer holds.
**Verified before committing**: smoke-tested the actual functions the
  Dashboard view calls (`load_highlights_config`, `highlights_frame`,
  `highlights_grid_frame`, `compare_frame`) directly against live data —
  zero warnings, 26/26 items loaded, DBS EPS FY25=3.88/3.86 (basic/
  diluted) present and correct, no exceptions. `app/test_findociq_app.py`
  68/68 pass (run from `app/`, its own established invocation directory).
  Launched the app itself (`--server.port 8599`, its documented port),
  confirmed clean startup (health=ok, no exceptions in server log) — could
  not visually screenshot (no browser tooling in this environment), so the
  function-level smoke test is the verification of record, stated
  explicitly rather than claimed as a full UI confirmation.
**Flagged, not fixed (pre-existing, unrelated to this change)**: the
  Dashboard's default period-basis selector (`default_basis()`) currently
  picks `as_at` as the initial view for the combined highlights table —
  under which the entire "Per share" section (EPS/NAV, which are FY/half
  concepts, never `as_at`) renders blank until the user manually switches
  the basis radio button. Pre-existing heuristic behavior, not introduced
  by this change (confirmed by tracing `default_basis`'s logic); out of
  scope for a metrics-wiring pass.
**Decision-tree pivot: no** — serving-layer read fix + a config un-null,
  no routing changes.

---

## 2026-08-04 — findociq_app.py Dashboard: 3-bank comparison toggle + click-to-chart
**Decision:** user asked for two things on the Dashboard view: (1) bold
  section headers where no values are extracted, (2) a "3 bank comparison
  toggle" showing all 3 banks together with click-into-item charting.
  On (1): the per-bank Key Financial Highlights tables already bold
  section-header rows (`highlights_grid_frame`'s `_section_header` flag +
  a pandas Styler applied in the render loop) — confirmed this by reading
  the code, not assumed. Carried the identical convention into the new
  combined view (below), rather than inventing a second styling mechanism.
  On (2): added `st.toggle("Compare 3 banks side by side")`. When on,
  renders ONE combined table (new `highlights_compare_grid_frame`, item
  rows x bank columns, same section-header-bold convention, plus a
  `_chartable` flag excluding section-header and coverage-gap rows) for a
  single selected period, via `st.dataframe(..., on_select="rerun",
  selection_mode="single-row")` — Streamlit 1.60 (confirmed installed
  version) supports native row-click selection, so this reuses the
  EXISTING "Item over time" chart section below rather than building a
  second chart path: a click writes the clicked label into
  `st.session_state["hl_item"]` before that selectbox is instantiated.
  Guarded against a real UX bug caught before shipping: Streamlit's table
  selection is STICKY across reruns (a clicked row stays "selected" until
  clicked again), so a naive "if a row is selected, override the
  selectbox" would silently stomp a user's MANUAL selectbox change back to
  the stale table click on the very next rerun. Fixed by tracking
  `_hl_last_click` and only overriding on a genuinely NEW click.
  Toggle defaults OFF, preserving the existing per-bank-tables behavior
  unchanged when not in compare mode.
**Why:** reuses every existing building block (section-header bolding,
  the chart, `format_highlight_value`, the period-basis filter) rather
  than duplicating rendering logic for a second table shape — the new
  function is the transpose of the existing `highlights_grid_frame`
  (items x periods per bank) with the axes swapped (items x banks per
  period), same conventions throughout.
**Verified before committing**: 4 new tests for
  `highlights_compare_grid_frame` (rows-are-items/columns-are-banks
  shape, single-period filtering, missing-bank cells stay blank not
  hidden, `_chartable` correctly excludes section headers AND
  coverage-gap/null-concept rows) — `app/test_findociq_app.py` 72/72 (was
  68/68). Smoke-tested the full render path against live data (real
  values, correct formatting, `NO EXCEPTIONS`). Relaunched the app,
  confirmed clean startup (health=ok) and no errors/tracebacks in the
  server log through several real reruns.
**Found, flagged, NOT fixed — a genuine design tension, not a bug for me
  to unilaterally resolve**: building the combined comparison table
  exposed that several balance-sheet stock concepts (`bs.equity.total`,
  `bs.liabilities.total` for some banks, `bs.assets.customer_loans_net`
  for UOB) render BLANK under the "Full year"/"Half-year" period-basis
  choices — not a data gap (the values are correct and served, verified
  earlier this session in the E2 fix) but a rendering-layer consequence
  of `period_label()`'s own EXPLICIT, DOCUMENTED design choice: "an as-at
  STOCK and an FY FLOW... MUST render as different tokens ('31-Dec-25' vs
  'FY25'), never the same one" — directly the OPPOSITE treatment from
  this session's E2 pipeline-layer fix, which treats a stock concept's
  `as_at` value as satisfying its FY/2H slot (same instant, same number).
  Did not merge them: `period_label`'s reasoning has real merit (avoiding
  a column that silently mixes a balance and a period movement) and
  overriding a deliberate, already-documented design decision from
  elsewhere in this same file is a product/UX call, not a bug fix — flagged
  for the user to decide, not resolved unilaterally. Workaround today:
  select the "Point in time (as at)" basis to see these specific items.
**Decision-tree pivot: no** — new UI interaction on the serving layer, no
  routing changes.

---

## 2026-08-04 — Step 4: proven end-to-end, found the ONE missing wire-in (registry classify never ran automatically), fixed it
**Decision:** proved the "tomorrow's DBS release, zero human touch" claim by
  actually running it, not reasoning about it: deleted `DBS_4Q25_
  performance_summary`'s own doc data on a scratch copy (kept `bank_line_
  map`/`table_registry*`/`concept_dictionary.yaml` — the durable,
  cross-quarter state — untouched), replayed it through the real production
  driver from cached `parsed.json` artifacts (zero API cost, zero network),
  and checked whether classification/anchor-projection/segment-split/
  serving all fired with nothing hand-run in between. **First run: 0/45
  tables classified, 0 anchors projected, the Markets/Commercial split
  never formed.** Root cause, found precisely: `classify_corpus()` (writes
  `table_t.table_type_id`) is called from exactly one place in the whole
  codebase, `mapping/seed_registry.py:93` — `run_doc.py` never invokes it.
  Every other link in the chain (`stamp_human_anchors`, `build_fact_metric`,
  `compute_ratios`+`segment_rollup`) already runs automatically; this one
  registry-classify step was the sole hop requiring a human to remember a
  command.
  Fixed by adding `run_doc.step3b_registry(db)` — calls `mapping/seed_
  registry.py` (not `classify_corpus` directly: `seed_registry.main()` does
  `seed()` — UPSERT the YAML's types/aliases — THEN `classify_corpus()`, so
  a registry type/alias added between quarters actually reaches the DB
  before classification runs; calling `classify_corpus` alone would
  silently ignore new aliases, the exact A4-class failure this session
  fixed twice already) — wired into BOTH `run_one()` (the per-document
  driver) and `run_db_steps_only()` (the deferred-batch-sweep driver),
  placed before STEP 4a since the concept layer's `ensure_schema` →
  `stamp_human_anchors` reads `table_type_id`. Treated as a whole-DB step
  (like 4a/4b/4c), not per-document, since `classify_corpus` is O(corpus) —
  deferred/re-run together with the others in a batch sweep, no behavior
  change there.
  Re-ran the exact same dry-run with the fix in place, one command, nothing
  hand-run: 38/45 classified, 4 anchors projected, the Commercial/Markets
  split formed correctly (row3→SEG_COMMERCIAL, row7→SEG_MARKETS, disjoint
  by parent from an identical leaf label), group NII served
  FY 14,500/2H 7,171 via the existing tier fallback (segment_rollup
  correctly stood down), and the re-ingest was **bit-identical** to live
  (NEW=0, GONE=0, CHANGED=0 vs the corpus's real DBS_4Q25 data) — proving
  idempotency, not just a fresh-load success.
  Confirmed re-seeding an ALREADY-classified corpus is a no-op (ran twice
  against a copy of live: 0/375 `table_type_id` values changed, alias rows
  byte-identical, `table_t.table_type` as-reported untouched) — so invoking
  this on a document that's re-extracted/reloaded mid-corpus reclassifies
  and destroys nothing.
  Also fixed in passing (found blocking a clean verification of this exact
  fix, both tiny): `--db-steps-only`'s crash on a scratch DB outside the
  repo tree (`Path.relative_to` raises; new `_display_path()` falls back to
  the absolute path).
**Caught in my own independent verification, not the delegate's**: running
  the full test suite mutated the LIVE `db/compiled_fs.db`'s
  `table_registry_alias` table — traced to `pipeline/test_run_doc.py`'s
  pre-existing `test_run_db_steps_only_order`, which monkeypatches every
  whole-DB step it exercises EXCEPT the new `step3b_registry`, so it called
  the REAL `seed_registry.py` subprocess against `run_doc.DEFAULT_DB` (the
  live path) as a side effect of testing step ORDER. Fixed by adding the
  missing mock (and asserting the new step's position: `registry, concepts,
  fact_metric, ratios[, sync_bq]`). This is exactly why "run the test suite,
  then independently re-hash the live DB" is standing procedure this
  session, not a formality — it caught a real, if harmless (idempotent
  UPSERT, no substantive content change beyond an `added_at` timestamp),
  test-isolation gap the delegate's own test run didn't surface.
**Deferred, deliberately, not fixed**: `--verify-only`'s hard requirement
  that the PDF exist locally (vs. resolving via `source_store.py`, this
  project's own canonical GCS-backed path, which `run_one` already uses)
  — is one line, but converts a fail-fast, $0, offline mode into a silent
  network fetch, breaking the exact property this whole verification
  relied on. Needs its own flag (e.g. `--allow-fetch`) and its own
  verification pass; not a drive-by edit on the final piece of an
  already-large task.
**Why:** "prove it, don't argue it" — the whole point of Step 4 was
  verifying the automation claim against the REAL driver, not asserting it
  from reading code. Fixing the one gap found (rather than only naming it)
  matches the task's own stated goal ("prove the pipeline runs end to end
  ... zero human touch"), and the fix itself is exactly the class of thing
  this session has repeatedly found safe to ship (idempotent, additive,
  verified non-destructive on already-good data before being trusted).
**Verified before committing**: `pipeline/test_run_doc.py`+`pipeline/
  concept/`+`pipeline/pass2/` 115/115, `pipeline/mapping/test_mapping.py`
  all pass, live DB hash re-confirmed identical to HEAD after the full
  suite (post test-isolation fix), full `preflight_invariants.py` on live
  unaffected (162/162, same 2 pre-existing FAILs) since this is a
  driver-behavior change with no retroactive effect on already-loaded data.
**Decision-tree pivot: yes**, called out per CLAUDE.md — this changes what
  the automatic ingest driver does for every future document (registry
  classification now runs unconditionally, whole-DB, before concept
  resolution). Recorded in `step3b_registry`'s own docstring; no separate
  `docs/specs/` entry judged necessary since it doesn't add a new routing
  BRANCH (no new page-class/prompt/table-type decision), it makes an
  already-existing, already-manually-invoked step automatic.

---

## 2026-08-04 — Step 3: DBS nii.net segment-sum fallback shipped as a per-bank partition, not a formula; OCBC noninterest.other needed no work at all
**Decision:** two-part step. First, corrected my own earlier automation-
  readiness audit before doing any work: it flagged OCBC `pnl.noninterest.
  other` as "no anchor, Link 2 missing" by checking only `bank_line_map`.
  Checked live: OCBC already serves this concept correctly via the EXISTING
  generic dictionary formula (`pnl.noninterest.total - pnl.noninterest.
  fee_commission`), `resolved_by='formula'` for every period, verified
  exact (FY25: 5,464-2,411=3,053; 2H25: 2,890-1,285=1,605).
  `lineage_identity_map.csv` already marks this row `resolution=derived`
  for OCBC with a note describing the same formula — the CSV was already
  right; the audit simply hadn't checked whether Link 3 covered what Link
  2 didn't. **No CSV edit, no `bank_line_map` edit — touching either would
  have been unnecessary, possibly conflicting, work.**
  Second, `pnl.nii.net` for DBS genuinely had no formula anywhere — shipped
  a **segment-partition roll-up**, declared once per bank
  (`concept_dictionary.yaml`'s new `segment_partitions:` block: `DBS,
  parent=SEG_TOTAL, members=[SEG_COMMERCIAL, SEG_MARKETS]`), applying
  generically to EVERY additive concept measured at those segments — nothing
  in the declaration or the code names `pnl.nii.net` specifically.
  Implemented as a **new code path** (`compute_ratios.segment_rollup`), not
  a `formula:` string extension: every existing formula combines DIFFERENT
  concepts at the SAME grain (columns of a pivot); a partition sum is the
  SAME concept summed across ROWS of the segment axis — the opposite shape,
  and overloading the string syntax would conflate two operations behind
  one parser. Checked whether `segment_dim`/`segment_map` could express
  partition-completeness first — they can't: both are corpus-global (one
  `parent` per segment, no bank dimension), so neither can say WHICH
  members are exhaustive FOR A GIVEN BANK (DBS: Commercial+Markets; OCBC:
  Retail/Wholesale/Markets/Insurance/Other — different partitions of the
  same `SEG_TOTAL`).
  Three guards, each independently necessary: (1) ADDITIVE ONLY — keyed off
  the concept's declared unit KIND, so a ratio/per-share concept can never
  be summed across segments, holds for any future ratio without naming it;
  (2) GRAIN MATCH — members must agree on institution/period/period_span/
  geo_key/industry_key/legal_entity/unit, only `segment_key` differs,
  enforced structurally (the grain tuple is the dict key, so a mismatched
  member simply never completes a group — tested independently on all 6
  grain axes); (3) FILL ONLY — runs LAST, after tier resolution and after
  the ratio/derived-metric formula pass, skips any parent slot that already
  carries a value, `resolved_by='segment_rollup'` tags a summed value so it
  is never mistaken for a reported one.
**Why:** DBS's group NII already renders correctly today only by
  coincidence (a tier fallback happens to pick a clean statutory row that
  happens to equal the segment sum) — this ships the fallback the
  `lineage_identity_map.csv` note always intended, as a REAL executable
  rule, without disturbing the value that already works.
**Discarded:** a `segment_partition: true` flag on the concept — rejected,
  puts a BANK-level fact (which segments partition the bank) on the wrong
  object (the concept), and would need repeating on every concept measured
  at those segments instead of being declared once. Authoring an OCBC
  anchor for `pnl.noninterest.other` — rejected, the concept already serves
  correctly via Link 3; the original "gap" was a hole in the audit's
  checking, not in the data.
**Verified before committing**: DBS `pnl.nii.net` FY25=14,500/2H25=7,171
  UNCHANGED, still `resolved_by='prefer_table'` — the roll-up did not touch
  the value that already works. Corpus-wide `fact_metric` diff: **+2 rows
  only** (2024-12-31/4Q=3,728 and 2024-03-31/1Q=3,505, both previously
  `NULL` — quarters with no printed group row), 0 removed, 0 changed.
  `preflight_invariants.py` re-run independently: byte-identical output to
  pre-Step-3 (same 19 PASS / 2 FAIL, same A4/D2 numbers, coverage still
  162/162 — this step is additive to historical periods, not the current
  headline). New `test_segment_rollup.py` 8/8 (fires when parent empty,
  does NOT fire when a direct value exists, does not fire on a partial
  partition, does not sum non-additive concepts, never sums grain-
  mismatched members across all 6 axes independently, rolls up each grain
  independently, surfaces an unmatched declaration, validates the shipped
  declaration's own shape). `pipeline/concept/` 46/46, `pipeline/pass2/`
  61/61.
**Flagged, not fixed**: `SEG_COMMERCIAL` appears in `fact_metric` but is in
  NEITHER `segment_dim` nor `segment_map` — it arrives purely via
  `bank_line_map.segment_key`. A pre-existing gap in the segment
  dimension's own bookkeeping, surfaced while checking whether that table
  could express partitions; not part of this step's scope.
**Decision-tree pivot: no** — no routing branch changes, no document takes
  a different path. This IS a new pipeline STAGE (`compute_ratios` now has
  a third pass), visible in its own stdout summary and the dictionary
  block's comment; judged not to need a `docs/specs/` routing entry since
  nothing about which prompt/page-class/branch fires for any document
  changes.

---

## 2026-08-04 — Step 2 (dashboard-live task): repaired a swapped title/label_header field, closing all 3 DBS per-share gaps
**Decision:** found via direct inspection of the cached `parsed.json` artifact
  (`outputs/pillar3/dbs_4Q25/.../overview_p4-8/parsed.json`, table index 3)
  that DBS's per-share exhibit's `title` and `label_header` fields were
  swapped by the extractor: `title='DBS GROUP HOLDINGS LTD AND ITS
  SUBSIDIARIES'` (the page masthead), `label_header='Per share data
  ($)3,8'` (the real caption) — both ALREADY correctly captured, just in the
  wrong fields. Confirmed this needs **zero re-extraction**: the fix is a
  loader-time field repair, reprocessing the same cached artifact
  ($0, no API call).
  Before designing the fix, checked the blast radius of the obvious rule
  ("swap whenever title looks like a masthead") and found it UNSAFE:
  `DBS_2Q25_performance_summary`'s NPA table has the identical masthead
  title but `label_header='($m)'` (a bare unit banner, nothing to recover —
  swapping would set its title to `"($m)"`); `DBS_4Q25`'s
  `key_audit_matters` table has a title that merely MENTIONS the filer as
  part of genuine boilerplate ("INDEPENDENT AUDITOR'S REPORT TO THE MEMBERS
  OF DBS GROUP HOLDINGS LTD (continued) - Key audit matter") with a
  correctly-placed `label_header='Key audit matter'` — not a masthead swap
  at all. A blanket rule would have corrupted both.
  Implemented `pass2.load_v7.repair_swapped_captions()`: fires only when
  BOTH signals hold — (a) `title`, once the document's own institution name
  and corporate boilerplate (and/its/group/holdings/ltd/plc/...) are
  removed, has nothing left; (b) `label_header` has real alphabetic content
  surviving unit-parenthetical and footnote-digit stripping. Fired exactly
  once across the whole corpus scan. **Persisted by swapping the two fields
  in place** (not a new column, not `table_title_clean`) — checked first
  that `table_title_clean`'s existing contract (`mapping.normalize.
  safe_clean()`) requires `clean` to be a footnote-stripped SUBSEQUENCE of
  `verbatim`; "Per share data" is not a subsequence of the masthead, so
  using that field would have violated its own documented invariant.
  Swapping in place means `table_type`/`table_id`/registry
  resolution/unit-detection all work through the existing, unmodified path
  (`table_type`/`table_id` both slug from `title`, so a new column
  wouldn't have helped without touching those too). This partially revisits
  the loader spec's 2026-07-13 "resolved default" #3 (`label_header`
  dropped, display-only) — see `docs/specs/2026-07-13-gtable-schema-v7-
  loader-design.md`'s new pivot section.
  Applied via a doc-scoped reload of `DBS_4Q25_performance_summary` from
  the cached `parsed.json` (`run_doc.load_doc`, not a file copy, not
  re-extraction).
**Why:** `bank_line_map` keys on `(bank, table_type_id, row_label_norm,
  parent_label_norm)` — no `table_id` — so the changed `table_id` from this
  repair (`overview_dbs_group_holdings...` → `overview_per_share_data_3_8...`)
  does not disturb any anchor; checked this before applying the fix, not
  after.
**Discarded:** a blanket masthead-detection rule with only signal (a) —
  proven to corrupt the DBS_2Q25 NPA table's title to `"($m)"`. Persisting
  the recovered caption in a new dedicated column — rejected, `table_type`/
  `table_id` still slug from the (uncorrected) `title`, so classification
  would still fail; would have required touching every downstream consumer
  instead of the one field.
**Verified before committing**: preflight diff is exactly 3 lines — A4
  `unclassified` 13→12 (title 22→23), E1 `value` 158→162 / `pending_anchor`
  4→0, headline coverage 158/162→**162/162** — spine coverage is now
  complete. Corpus-wide `fact_metric` diff: 10 rows added (DBS `eps.basic`/
  `eps.diluted` across FY25/2H25/1H25/FY24/2H24), 0 removed, 0 changed. The
  other 3 tables in the same extracted chunk (income statement/balance
  sheet/ratios highlights) are cell-identical, same `table_type_id`, before
  and after. Served values: DBS `pnl.eps.basic` FY=3.88/2H=3.71,
  `pnl.eps.diluted` FY=3.86/2H=3.69 — FY figures match `bank_line_map`'s own
  note ("3.88/3.86 underlying") exactly; `bs.nav_per_share` unchanged at
  24.29 (was already being served by coincidence via the old wildcard path
  before this fix — now served correctly via the anchor). New
  `test_swapped_caption.py` 5/5 (both signals independently, all 3 corpus
  cases pinned as regression guards, idempotency, unit-neutrality).
  `pass2/`+`concept/` 99/99, `mapping/test_mapping.py` +
  `test_normalize_numbering.py` all pass.
**Decision-tree pivot: yes**, called out per CLAUDE.md and recorded in
  `docs/specs/2026-07-13-gtable-schema-v7-loader-design.md` §5 — this is a
  general loader rule, not a one-off patch; it will fire on any future
  filing with the same extractor slip, routing that exhibit to its real
  registry type with zero new aliases.

---

## 2026-08-04 — A4 fixed: normalize_exhibit_title numbering/period gaps + registry alias data; A4's own acceptance bar (title==0) rejected as wrong
**Decision:** delegated A4 to the same agent that closed F2/B6/D2 (full codebase
  context retained). It found the task brief's framing needed two corrections,
  verified before I accepted them:
  1. **"Apply the resolution corpus-wide" would have changed nothing** — A4's
     check does not read a stored `table_type_id`; it RE-RESOLVES live via
     `resolve_table_type` on every run. The mechanism was already being
     invoked corpus-wide; it was failing on genuine gaps (below), not on being
     skipped.
  2. **The 22 title-level matches are not a defect — same class of premise
     error as B6.** Inspected all 22 individually: `registry.py`'s own
     resolution cascade documents title as the CORRECT identity for
     DBS-shaped documents (the section is a bland page grouping, e.g.
     "Overview"; the title, "Selected income statement items", is what
     actually identifies the exhibit). Driving title to 0 would require
     section-level aliases that are actively harmful: `statement_of_changes_
     in_equity` as a section alias would MERGE DBS's Group and Company
     statements (two different legal entities — only the title separates
     them); `performance` as a section alias would HIJACK OCBC's Allowances/
     Asset-Quality tables (section is tried before title, so a broad section
     alias always wins over a precise one). Redefined A4 to assert COVERAGE
     (`unclassified == 0`), not match level; the level split is still
     reported for visibility.
  **The genuine defects, both in `normalize_exhibit_title` (`pipeline/mapping/
  normalize.py`), both general title-position/period stripping gaps, not
  per-bank rules:**
  1. `_LEAD_NUM_RE` stripped only flat numbering (`10. `); hierarchical and
     lettered forms survived (`13.2 Geographical segments`, `A.6.1 IRBA RWA
     flow statement`). Note numbers RENUMBER when a bank inserts a note above
     — the same exhibit resolved one quarter and went unclassified the next.
     Fixed with a trailing-whitespace requirement (so `1Q25 key financial
     indicators` isn't eaten as numbering) and a letter-prefix-only-before-
     digits guard (so `e.g. …` can't match).
  2. The period vocabulary stripped only the year, leaving the spelled-out
     qualifier — OCBC's ONE income summary fragmented into 8 keys
     (`first_half_performance`, `full_performance` from a mangled "Full Year
     … Performance", etc.), all 8 unclassified. Fixed by adding spelled-out
     ordinals/month-counts/`full year`/`year-on-year` to the period pattern
     list, ordered so `Full Year 2025` is consumed whole.
  Plus 15 `table_registry_alias` rows + 2 new type blocks (`FS_FINANCIAL_
  CLASSIFICATION`, `FS_FAIR_VALUE_HIERARCHY` — existed in the DB seed CSV but
  had no YAML entry, so were unseedable) — data, not code, matching "the
  mechanism already exists" per the task brief.
  **Unexpected, intended coupling**: better `table_type_id` coverage
  (261→302/375) strengthened F2 — `dimensional_scopes`'s DECLARED (`dim_hint`)
  signal went from 45 to 49 scoped tables, un-stamping 9 more spurious spine
  concepts. Quarantine count still 0.
**Why:** the task brief's own acceptance criterion ("0 spine tables on
  raw-title fallback") was itself the defect, in the same shape as B6's proxy
  — treating a correct resolution PATH as if it were a weak fallback, purely
  because of which registry cascade step it matched at, not because of
  anything wrong with the result. Verified by reading, not asserted:
  inspected the contents of all 22 title-matched tables before accepting the
  premise correction.
**Discarded:** seeding `statement_of_changes_in_equity` / `performance` as
  broad section-level aliases to force title-fallback to 0 — rejected, proven
  actively harmful (legal-entity merge; OCBC table hijack), not merely
  unnecessary. Composite aliases keyed to per-document drifting section
  headers for the remaining title matches — rejected, "a new row every
  quarter forever" is exactly the key-fragmentation failure mode the registry
  exists to prevent.
**Residual, honestly reported against the acceptance bar, not silently
  dropped**: spine unclassified 33→13, corpus-wide `table_type_id` NULL
  114→73. The brief's acceptance assumed residuals would be "genuinely
  narrative/out-of-scope" — **none of the 13 are**. All are real exhibits
  blocked by two further, distinct causes: (a) DBS per-share data + 3
  statement-of-changes-in-equity tables — the running PAGE HEADER was
  captured as the table's title upstream (an extraction defect, not a
  registry gap; aliasing the company name would mis-tag every exhibit under
  that header); (b) all 7 remaining OCBC `<period> Performance` tables — the
  section header IS the title with no caption of its own, and seeding
  `performance` as an alias would hijack the Allowances tables under the same
  header (the same harm named above). Both need a non-alias mechanism (a
  caption-extraction fix; a content-based or composite disambiguation rule)
  — named, not fixed, out of scope for this pass; a future session's job.
**Verified before committing**: `preflight_invariants.py` re-run
  independently — A4 now `{composite:2, section:118, title:22,
  unclassified:13}` (down from 33), B6/F2 still PASS/0, D2/E2 byte-identical
  (179/293, 12), 4 FAILED of 21 (same set: A4, D1, D2, E2). `pipeline/
  mapping/` (`test_mapping.py`, new `test_normalize_numbering.py`) all pass,
  `pipeline/concept/` 26/26, `pipeline/pass2/` 56/56. Pre-existing pytest
  collection error on `pipeline/mapping/` (sys.path issue under pytest, not
  this change) re-confirmed identical with the diff stashed.
**Decision-tree pivot: yes, narrowly, called out per CLAUDE.md.**
  `normalize_exhibit_title` decides which registry branch a table takes, so
  this changes routing for every future document (41 tables moved from
  UNCLASSIFIED to a typed branch this run). Recorded in
  `docs/specs/MAPPING_LAYER.md`'s 2026-08-04 section; observable in
  `seed_registry.py`'s classified/unclassified counts and A4's level split.

---

## 2026-08-04 — B6/D2 re-checked post-F2: PARTIALLY the same root cause, B6 fixed, D2 partially fixed
**Decision:** re-ran B6/D2 after committing F2, per the task brief's explicit
  instruction ("confirm before treating as separate"). Both had improved
  (B6 148→115, D2 367/517→209/330) but neither was zero, so per the brief's
  own rule ("whatever remains... fix that separately") this became its own
  root-cause pass, delegated to the same agent that fixed F2 (it already had
  full context of this codebase area). Verified independently before
  committing, same discipline as F2: re-ran `preflight_invariants.py` myself
  (not trusted from the report), inspected the actual diff, ran the full test
  suites myself.
  **Finding 1 — B6's own signature was measuring the wrong thing.** It used a
  table-level proxy ("this table has >=2 distinct `col_period`") as a stand-in
  for "was a period available for this cell?" — wrong in both directions: it
  flagged 127 legitimately-periodless comparison-delta columns (`% chg`,
  `YoY (%)`, `Volume`) that merely sit beside real period columns (false
  positive), and it MISSED a column whose label failed to parse unless its
  table happened to have >=2 other parsed periods (false negative). Proof the
  proxy was wrong, not just imprecise: fixing the real defect made the PROXY
  count go UP (63→73) while the true signature went to 0 — adding real column
  periods makes more tables "multi-period" and drags in more innocent `% chg`
  columns. Redefined B6 to assert `load_v7`'s own GATE A2 post-hoc (a spine
  cell whose own column label IS period-shaped by the loader's gate yet
  carries no `col_period`), calling the loader's actual functions so the gate
  and the invariant cannot drift apart.
  **Finding 2 — the real defect: a grammar fix that never got its delivery
  mechanism.** The 2026-08-03 period-grammar improvement (digit ordinals,
  `Qtr`/`Q`, `Year YYYY`) only reaches RE-LOADED rows, and that fix's own
  writeup said it ships as "STEP-3 reload of all docs" — which never
  happened. 39 columns across the three `*_trading_update` documents kept
  `col_period IS NULL` on labels the current grammar parses fine
  (`4th Qtr 2024`, `1st Qtr 2022`, `9 Mths 2025`), so every cell in them fell
  through to the DOC period and six quarters collapsed onto one grain slot —
  exactly what this file's own 2026-08-03 entry predicted. Fixed with new
  `pass2/backfill_col_period.py`: since `col_period` is a pure function of the
  column's stored label, it can be re-derived in place (no re-extraction, no
  API cost) by REPLAYING the loader's own `is_period_text`/`parse_period_span`
  functions (not a second grammar that could drift). Only re-stamps cells
  whose `period_source` is a fallback bucket; a `row`- or `col`-sourced period
  still outranks it; verified 0 disagreements between stored and re-derived
  values corpus-wide, so purely additive. Idempotent — re-run after any future
  grammar change.
  **D2's other two mechanisms are NOT period-related, confirmed by replaying
  the grouping logic on all 209 pre-fix conflicts**: 67 are a MEASURE-axis
  collision (`average_balance_sheet` tables — one row × `Average balance ($m)`
  / `Interest ($m)` columns, same shape as F2 but the axis is measure, not
  dimension); 92 are dictionary alias over-claiming (`reg.capital.cet1_ratio`
  claims 3 non-equivalent labels); 20 are a cross-exhibit basis clash within
  one document. **Not fixed here, deliberately** — each is its own design
  decision (a measure-axis grain rule; dictionary re-granulation; tier
  precedence in `_resolve_group`) and bundling them into a period fix would
  repeat exactly what this session has been correcting elsewhere (see the
  legal_entity-thread commit).
**Why:** the task brief's own acceptance rule — re-check first, only treat
  as separate if non-zero — and once non-zero, root-cause rather than
  re-tune the check to pass. Verifying the proxy was WRONG (not just that the
  fix worked) mattered because a check that measures the wrong signal will
  silently regress again the next time the corpus changes shape, exactly as
  it did here (63→73 on the very fix that resolved the real defect).
**Discarded:** treating the reported "63" as ground truth and fixing toward
  it — rejected; it was proven to be bit-identical before and after F2 (F2
  touched none of it) and, more importantly, provably measuring the wrong
  cells. Folding D2's measure-axis/alias/tier-precedence mechanisms into this
  pass — rejected, out of scope, each needs its own predicate and is a
  separate design decision, not a period-fallback artifact.
**Verified before committing**: `preflight_invariants.py` re-run
  independently — B6 PASS (0), D2 209/330→179/293, A4/D1/E2 byte-identical
  (confirmed D1's 8-concept list is pre-existing, not introduced by this
  pass), F2 still 0, 4 FAILED of 21. `pass2/` 56/56, `concept/` 26/26, new
  `test_backfill_col_period.py` 5/5. No decision-tree pivot — neither change
  alters which prompt/page-class/branch fires for any document; B6 is a
  verification-invariant redefinition and the backfill re-derives existing
  data through the loader's own function, so CLAUDE.md's route-manifest
  requirement doesn't apply.

---

## 2026-08-04 — F2 fixed: dimensional-breakdown tables are a NO-WILDCARD concept_map scope
**Decision:** root-caused and fixed F2 (see `docs/specs/2026-07-14-concept-resolution.md`'s
  2026-08-04 pivot section for the full design). The regression was bigger than
  first scoped: `FS_GEO_INCOME`-shaped tables exist for **UOB and DBS** (DBS =
  942 of the 1,117 tagged cells), and an identical, untagged defect existed in
  the segment panels (790 cells) the quarantine script never looked at; DBS's
  own geography breakdown is titled "Selected income statement items", which
  the raw-title `map_table_type_norm` reads as a genuine income statement — so
  the raw-title axis is actively wrong, not just under-specific. New
  `load_dictionary.dimensional_scopes()` assigns `dim_geo`/`dim_segment`/
  `dim_industry` via two independent signals (structural: table's own columns
  carry >=2 distinct non-total keys on one axis; declared:
  `table_registry.dim_hint`) — a table in either bucket gets NO wildcard
  fallback in `resolve_deterministic.build_lookup`. Verified on a scratch copy
  first (per this session's standing discipline), then applied to live:
  `F2_geo_wildcard` tags 1,117 -> 0 (confirmed idempotent — a second run of
  `quarantine_f2_geo_wildcard.py` also tags 0), UOB `bs.assets.total` FY25
  CONSOLIDATED unchanged at 572,061, `row_dim` diff across the WHOLE corpus is
  452 rows changed, 100% inside the newly-scoped tables, 100% key->NULL, zero
  NULL->key elsewhere. `preflight_invariants.py` re-run independently by the
  orchestrator (not just trusted from the delegate's report): F2 PASS,
  6 failures -> 5 (A4/B6/D1/D2/E2 remain, exactly the rest of this session's
  list), zero other checks regressed. 26/26 `pipeline/concept/` tests green,
  new `test_dim_scope.py` 5/5.
**Why:** the task brief's own instruction — scope the wildcard so it "must NOT
  match across geo-scoped rows" — and CLAUDE.md's standing no-overfitting rule:
  the fix had to generalize to a bank/exhibit never seen before, not special-
  case `table_type_id=='FS_GEO_INCOME'`. Verified this empirically, not by
  argument: signal 1 alone catches 6 OCBC "Business segments" tables the
  registry leaves UNCLASSIFIED and 4 DBS breakdowns misfiled as
  `FS_INCOME_SELECTED` — exactly the case a `table_type_id`-keyed rule would
  have missed. 45 tables scoped corpus-wide, zero false positives (all 45
  inspected).
**Discarded:** extending `scoped_aliases` (the existing ECL-Stage-3-style
  mechanism) — rejected, it is POSITIVE polarity (supplies an alternate
  meaning for a bucket) and still falls through to the wildcard when no scoped
  alias exists; a breakdown needs the opposite, a scope where the wildcard is
  never consulted. Keying the scope off `table_type_id` alone — rejected,
  would miss the OCBC/DBS cases above (proof, not hypothesis).
**Known tension, deliberately left open (tracked, not fixed here):** row-level
  suppression removed 1,482 `fact_metric` slots, 1,418 of them genuinely
  dimensional (`geo_key`/`segment_key` != default) — this contradicts this same
  spec's earlier standing note that segment/geo panel rows are correct and
  merely need per-row axis disambiguation. `concept.query_db.pull(dimension=
  'geo'|'segment')` now returns 0 rows for these tables. **Verified this has
  zero live callers**: grepped the whole repo for `query_db` imports — only
  `build_fact_metric.py` (a comment, not a call) and `query_db.py`'s own CLI;
  `app/dashboard.py` never imports `query_db` at all, reads `fact_metric`/
  `v_fact_metric_serving`/`v_cell_flat` directly. So this is a real regression
  in a standalone, manually-invoked analyst CLI tool, not a live dashboard
  break — corrected from the delegate's initial "empties the dashboard's
  geo/segment selector" framing, which was checked and found inaccurate. The
  grain-correct resolution (a breakdown table may supply dimension MEMBERS but
  never the canonical `(GLOBAL, SEG_TOTAL)` slot — one layer down, in
  `build_fact_metric`) is B6/D2 territory, next on this session's list, not
  done here. Also: 64 canonical GLOBAL slots changed source and 35 canonical
  values changed as a side effect of the tie-break no longer seeing a
  breakdown's coincidental duplicate vote — 34 are improvements (e.g. DBS 1H25
  `pnl.noninterest.total` 1,866 -> 4,308, now correct); one, OCBC
  `bs.liabilities.total` FY25 612,118 -> 502,719, is a pre-existing E2
  tie-break weakness exposed, not caused (that slot was already in the E2
  failed_resolve list before and after — see item 5 on this session's list).

---

## 2026-08-04 — Committed the orphaned legal_entity-axis code that had already built `e8e78f1`'s DB
**Decision:** `e8e78f1` shipped a `db/compiled_fs.db` whose `fact_metric` (3,558 rows,
  `legal_entity`/`unit_source` columns, Group vs Bank/Company now separated) could only
  have been produced by a `build_fact_metric.py` with `legal_entity` in the grouping key
  and a unit-promotion branch — but the committed `build_fact_metric.py` had neither
  string anywhere in it. The code that actually built the shipped DB was sitting
  uncommitted in the working tree (`build_fact_metric.py`, `compute_ratios.py`,
  `validate.py` + new `assert_single_legal_entity_per_group` hard gate,
  `test_fact_metric.py`, new `test_validate.py`), orphaned from the commit that shipped
  its output — HEAD was self-inconsistent (a clean checkout of `e8e78f1` could not
  reproduce its own DB). Verified before committing, not assumed: copied
  `pre_reresolve_2026-08-03.db` to scratch, ran the uncommitted code's
  `concept/run.py --no-llm` → `build_fact_metric.py` → `compute_ratios.py` against it,
  and diffed the result against the live/committed DB table-by-table.
  `fact_metric` and `row_dim` hash byte-identical (3,558 / 6,531 rows); the only
  differing table, `concept_resolution_log`, differs solely on its `ts` timestamp
  column (expected per-run variance, not content). 62/62 (`test_fact_metric.py`) and
  4/4 (`test_validate.py`) pass. Committed the 5 files as their own continuation
  commit, separate from the anchor/geometry thread (different, unrelated set of
  uncommitted files — classified separately, see PROGRESS.md).
**Why:** an unreproducible HEAD means every fix in the pending 6-item pre-flight list
  would be built on a base nobody could regenerate from git alone — the discipline this
  session is explicitly operating under (scratch-copy-first, snapshot-before-mutate)
  is meaningless if the commit itself can't be reproduced. The reproduction test is the
  gate: byte-identical `fact_metric`/`row_dim` from a documented, hashed starting point
  is proof the orphaned code is what built the shipped state, not a guess from
  matching column names.
**Discarded:** proceeding uncommitted (perpetuates the same silent-state problem that
  caused this); reverting the 5 files and re-deriving from committed code only (would
  discard working, tested code — 62+4 tests green — to rebuild something already
  correct, and would produce a genuinely different, less-correct `fact_metric` since
  the committed `build_fact_metric.py` predates the `legal_entity`/unit-promotion work
  entirely).

---

## 2026-08-03 — Pre-flight pass: retained the re-resolved DB, quarantined a self-inflicted regression (F2), blocked the dashboard test on 6 open items
**Decision:** the pre-flight assertion pass (21 checks, categories A–F) ran the
  task's own mandated re-resolve (`concept/run.py --no-llm` →
  `build_fact_metric.py` → `compute_ratios.py`, no re-extraction/reload) and
  found 6 genuine failures, the most consequential being self-inflicted: the
  re-resolve itself caused UOB's `FS_GEO_INCOME` geography table (confirmed
  spine-free by the earlier same-day "UOB title-context bare-year gap"
  decision) to pick up 6 spine concepts via unscoped `concept_map` wildcard
  matching on generic row labels ("Total assets", "Net interest income")
  identical across dozens of legitimate table types. Confirmed via the
  `pre_reresolve_2026-08-03.db` snapshot: these row_dim rows were unmatched
  residue (`concept_key IS NULL`) immediately before the re-resolve ran.
  Chose to KEEP the re-resolved DB (863 newly-stamped, genuinely-correct
  `concept_key` values; refreshed `fact_metric`/ratios — real, retained
  progress) rather than revert to the pristine snapshot, and to CONTAIN the
  regression rather than fix its root cause in this pass: added
  `pipeline/mapping/quarantine_f2_geo_wildcard.py`, an idempotent migration
  that tags (`cell_fact.review_status='F2_geo_wildcard'`, 1,117 cells) every
  affected cell without deleting it — the value, geo_key, and table lineage
  are exactly the evidence needed to (a) scope the real `concept_map` alias
  fix and (b) confirm it worked (tagged count → 0 once fixed). The canonical
  dashboard figure is unaffected throughout: UOB `bs.assets.total` FY25
  CONSOLIDATED still reads 572,061 in `fact_metric`, which the quarantine
  migration does not touch. Also: made both DB states durable, in-repo, not
  scratch-dependent — `db/snapshots/{pre,post}_reresolve_2026-08-03.db` (added
  `db/snapshots/` to `.gitignore`, matching the existing policy of not
  git-tracking extra DB copies; hashes recorded here and in `PROGRESS.md` are
  the durable reference). Added `pipeline/preflight_invariants.py` as a
  permanent, re-runnable gate — not a one-off script — since this is exactly
  the check that needs to run before every future dashboard build and
  quarterly ingest. Full findings, root causes, and next-session ordering
  written to `PROGRESS.md` (2026-08-03 session block) rather than duplicated
  here.
**Why:** "keep the good, record the bad, fix next session from a documented
  baseline" — reverting would discard 863 legitimate stamps to undo one
  narrow regression, and fixing the `concept_map` scoping now would
  scope-expand an assert-and-report pass into a fix pass mid-session with an
  already-large diff. Quarantining (tag, don't delete) preserves the evidence
  trail the real fix needs, rather than either leaving the corruption
  silently live in `fact_metric`/dashboard queries or destructively cleaning
  it before the root cause is even scoped. The two durable snapshots exist so
  the next session (or an audit) can diff exactly what the re-resolve changed
  without depending on a session-scoped scratchpad that may be cleaned.
**Discarded:** reverting `db/compiled_fs.db` to the pre-re-resolve snapshot —
  rejected, throws away real progress (863 correct stamps) to undo a single,
  now-identified, now-quarantined regression; the next session can re-derive
  everything from either snapshot regardless. Fixing all 6 failures in this
  same pass — rejected, this is explicitly an assert-and-report pass per the
  task's own instruction ("if any assertion fails, STOP and report — do not
  paper over"); F2 in particular needs a scoped `concept_map` alias fix
  analogous to the existing corpus-ambiguity gate, which is real design work,
  not a mechanical patch, and deserves its own pass. Deleting the spurious
  `FS_GEO_INCOME` cells outright instead of tagging — rejected, deletion
  destroys the evidence (which labels, which aliases, which geo_keys) needed
  to scope and verify the real fix.

## 2026-08-03 — Third (and fourth) view-clobbering copy found during pre-flight; root-caused to duplicated DDL, not duplicated migrations
**Decision:** the migration merge earlier today fixed two scripts that owned
  independent copies of `v_cell`/`v_cell_leaf`/`v_cell_sumsafe`/`v_cell_flat`.
  The pre-flight assertion pass's first step — a dry-run of `concept/run.py`,
  the literal "re-resolve" entry point the pass asked for — surfaced a THIRD
  copy: `concept.load_dictionary.ensure_schema()` carried its own independent
  pre-merge DDL (`COALESCE(r.concept_key, f.concept_key)` only; no
  `concept_key_human`, `identity_source`, `period_source`, `period_end`,
  `period_label`) and rebuilt it **unconditionally on every call**.
  `ensure_schema()` is the first thing `concept/run.py` does, and
  `concept/run.py` is **STEP 4a of `run_doc.py`** — the standard, automatic,
  production document-ingest path. Nothing downstream in STEP 0–7 re-runs
  `migrate_serving_views.py`. So every real document ingest was silently
  reverting the merged views back to the pre-anchor, pre-period-label schema
  — not a latent hazard like the first two, a **guaranteed regression on the
  next production ingest**. A repo-wide sweep for `CREATE VIEW v_cell` found
  a fourth: `pipeline/migrate_add_industry_dim.py` (a standalone, re-runnable,
  manually-invoked migration, not on the automatic path but carrying the
  identical unconditional-clobber bug).
  Root cause, corrected: the earlier merge fixed a *migration-script*
  duplication (two scripts calling `DROP`/`CREATE VIEW` in an order-dependent
  way) but left a *DDL* duplication in place — the view SQL itself still
  lived in three independent places (`migrate_serving_views.py`,
  `load_dictionary.py`, `migrate_add_industry_dim.py`), each editable without
  the others knowing. An idempotent, single, well-ordered migration doesn't
  help if a completely different code path holds its own stale copy of what
  it's building. Fix: `mapping.migrate_serving_views` is now the **only**
  place the view DDL is defined (`VIEWS` dict + `ensure_columns` +
  `stamp_human_anchors` + `ensure_concept_period_kind` + `rebuild_views`, all
  exposed as the top-level `migrate()`). `load_dictionary.ensure_schema()`
  and `migrate_add_industry_dim.py` both now import and call
  `migrate_serving_views.migrate()` instead of holding their own DDL — so
  STEP 4a *rebuilds* the full merged views (including a fresh human-anchor
  stamp over anything loaded since) rather than reverting them, and there is
  nothing left downstream to "restore."
  A second, real bug found applying this: `stamp_human_anchors()` hard-required
  `table_t.table_type_id` (added by `migrate_add_mapping_layer.py`, not part
  of `schema_v7.sql`, not populated by the standard load path) and
  `bank_line_map` (created by the same migration) to exist, and crashed
  (`sqlite3.OperationalError: no such column: tt.table_type_id`) on any DB
  that predates that migration — caught immediately by
  `pipeline/concept/test_concept.py`'s synthetic schema-only fixture once
  `ensure_schema()` started calling it. Fixed: `stamp_human_anchors()` now
  checks for the column/table first and is a no-op (0 stamped) rather than a
  crash on a DB that hasn't had the mapping layer applied yet — STEP 4a must
  be safe to run on ANY DB state, not just the fully-mapped production one.
  Added two tests: `pipeline/concept/test_ensure_schema_views.py` runs
  `ensure_schema()` (the actual STEP 4a entry point, not the migration script
  directly) against a scratch copy of the real DB, twice, and asserts all
  four views carry `identity_source`/`period_source`/`period_end`/
  `period_label` and are byte-identical after the second run — this is the
  test that would have caught the original bug, since it exercises the real
  call site. Verified `migrate_add_industry_dim.py`'s fix directly against a
  scratch DB copy too. Full existing suite re-run after both fixes:
  `test_concept.py`'s one remaining failure is pre-existing (confirmed via
  `git stash` against the prior commit, identical `RuntimeError` on an
  unrelated synthetic period-mismatch fixture) — not caused by this change.
  Live `db/compiled_fs.db` confirmed untouched throughout this discovery
  (hash-identical to the pre-preflight snapshot); only scratch copies were
  mutated while diagnosing and fixing.
**Why:** flagged mid-pre-flight-pass rather than papered over, per the pass's
  own instruction ("if a reload is somehow required to re-resolve, STOP and
  flag — that means the re-resolve path isn't independent of extraction,
  which is its own finding"). The re-resolve path (`concept/run.py`) was not
  independent of the anchor/period schema: running it would have destroyed
  the exact columns the pre-flight pass exists to verify. User's explicit
  direction on being asked how to proceed: fix the root cause now, as one
  shared view definition, not a third corrected copy — "the failure mode is
  two copies of the view DDL drifting apart... make there be one copy that
  both call." Deferring this (workaround: re-run the migration after
  `concept/run.py` every time) was rejected as re-introducing the exact
  clobber-then-reorder anti-pattern the first merge existed to eliminate, and
  stopping without fixing was rejected because STEP 4a runs on every future
  quarterly ingest — the regression is not hypothetical, it is scheduled.
**Discarded:** giving `ensure_schema()` and `migrate_add_industry_dim.py`
  their own corrected copies of the merged DDL (matching column set, kept
  independently) — rejected; this reproduces the exact failure mode (multiple
  copies of the same SQL, one edit away from drifting apart again) with
  better column coverage but the same structural flaw. Rejected in favor of
  one importable definition, called from every site that needs to build
  these views.

## 2026-08-03 — Merged the two clobbering view-owning migration scripts into one idempotent migration
**Decision:** replaced `pipeline/mapping/migrate_add_human_anchor_projection.py`
  and `pipeline/pass2/migrate_add_period_label.py` — both of which `DROP`/
  `CREATE` the SAME four views (`v_cell`, `v_cell_leaf`, `v_cell_sumsafe`,
  `v_cell_flat`), the second script's version being a strict superset (adds
  `period_source`/`period_end`/`period_label` on top of the first's identity
  columns) — with a single script,
  `pipeline/mapping/migrate_serving_views.py`, that owns all of: the
  `row_dim` column ALTERs (`concept_key_human`/`segment_key_human`/
  `identity_source`), the human-anchor `stamp()` projection (title-like-
  parent-collapse + raw-label-preference fixes intact, unchanged logic —
  only relocated), the `concept_period_kind` reference table/seed, and ONE
  final view definition carrying every column both scripts used to
  contribute, built in a single internally-ordered `migrate()` call
  (columns → stamp → concept_period_kind → views). The two old scripts are
  deleted, not deprecated-in-place — there is nothing left to run out of
  order. Added `pipeline/mapping/test_migrate_serving_views.py`: copies the
  live DB to a scratch file, runs `migrate()` twice, and asserts each view's
  column list, row count, and full-content hash are byte-identical after the
  second run, plus that the second run adds zero columns and re-stamps the
  same `row_dim` count as the first. Ran against a scratch copy AND the real
  `db/compiled_fs.db`: `ALL PASS` both times, plus post-migration invariant
  checks all held — `bank_line_map` `human_confirmed` count unchanged (104,
  the MERGE invariant), `cell_fact.legal_entity` still 100% populated
  (24,788/24,788), all 27 spine concepts still resolve in `v_cell`, and a
  DBS `bs.assets.total` spot-check still shows the correct `human_anchor`
  identity_source with `period_label`/`period_end` intact.
**Why:** this was flagged as an F1-class hazard, not a cosmetic duplication —
  a one-way `DROP VIEW` that silently drops the other script's columns
  depending on run order, hit and manually worked around by ordering at
  least three times already (most recently logged in the 2026-08-03
  "uniform column-label rule" entry above: "hit again this pass — ran
  anchor-projection before period-label, in the order the clobbering
  requires, exactly as logged last time"). For a pipeline whose stated
  purpose is *unattended* quarterly automation, an ordering-dependent
  migration pair is a standing landmine: a scheduled run, a concurrent
  session, or a fresh clone has no way to know the required order and will
  silently lose `period_label`/`period_end` or the human-anchor columns
  depending on which script runs last. Consolidating into one script with an
  internal, fixed execution order removes the hazard structurally — there is
  no longer a "wrong order" to hit, because there is only one call.
**Discarded:** keeping both scripts and just documenting "always run X before
  Y" — rejected outright; this is precisely the failure mode already logged
  as repeated, and documentation has never once prevented it recurring.
  Also discarded: a thin wrapper script that calls both originals in a fixed
  order — rejected because the two scripts' `VIEWS` dicts would still be two
  separately-maintained near-duplicates (drift risk every time either view
  changes) and the wrapper would be exactly as easy to skip/bypass as
  documentation; the acceptance bar was "the manual-ordering workaround is
  gone, not just documented," which requires one script, one `VIEWS` dict,
  not a coordination layer over two.

## 2026-08-03 — UOB title-context bare-year gap: scoped, confirmed out of the spine, deferred
**Decision:** verified rather than assumed. The gap is exactly one table shape:
  `performance_by_geographical_segment_1 — 2024` (`table_id
  performance_by_geographical_segment_1_performance_by_geographical_segment_1_2024_2025-12-31`,
  `table_type_id=FS_GEO_INCOME`) — its columns are geography (Singapore/
  Malaysia/Thailand/...), not period, and the title's bare "— 2024" (no half/
  quarter marker) is refused by the deliberate title-context bare-year guard,
  so `table_t.period` for this one table stamps 2025-12-31 instead of
  2024-12-31. The sibling `"(cont'd) — 2H24"`/`"— 1H25"` tables parse
  correctly (they carry a half marker); only the bare "— 2024"/"— 2025"
  titles are affected, and "— 2025" happens to be right anyway since it
  matches `doc_period`.
  Ran the exact scope query against `v_cell`: zero of the 27 spine concepts
  (`bs.*`/`pnl.*`/`ratio.*`/`reg.capital.cet1_ratio` — the full distinct
  `concept_key` set across all `anchor`/`derived`/`pending_extraction` rows
  in `lineage_identity_map.csv`) read any row from this table. Separately
  confirmed zero `human_confirmed` `bank_line_map` entries ever target
  `table_type_id=FS_GEO_INCOME` for UOB — the geography breakdown was never
  wired into the anchor/spine system by design, not by oversight. A broader
  sweep for spine concepts with `period_source IN ('table_title','doc')`
  found 18 hits, but every one is UOB's `'+/(-)\n%'` comparison-delta column
  in the MAIN highlights table (a different, already-covered table) — the
  same legitimate periodless category (`% chg`/`QoQ`/`YoY`) excluded
  throughout every prior pass's period checks, not a new defect.
**Why:** "deferrable only if it does not touch a spine concept" was a
  testable claim, not a judgment call — ran the test instead of trusting the
  earlier "found but not fixed" note. A geography-axis table feeding no
  anchored concept is a genuinely different risk class than a bare-year gap
  on a headline figure would have been.
**Discarded:** fixing the title-context bare-year grammar now anyway (it's
  a small, evidenced pattern, same class as the earlier "fair value at"
  fix) — rejected because it's confirmed unnecessary for anything the
  dashboard reads, and the title-context bare-year guard exists specifically
  to prevent false positives elsewhere; touching it without a spine-driven
  reason reintroduces exactly the regression risk logged when it was first
  deferred. Left for whoever next needs UOB's geography breakdown, not
  before.

## 2026-08-03 — Period logic: uniform column-label rule replaces the value_kind branch
**Decision:** removed the `concept_period_kind` branch from `v_cell`/`v_cell_flat`'s
  `period_label` CASE entirely. **Every cell now carries its own column label
  verbatim as `period_label` ('FY25'/'2H25'/'4Q25'/a date for `as_at` columns) —
  no per-concept override, no stock/flow branch at stamp time.** The read-only
  audit that preceded this (same date) had found the branch only covered 6 ratio
  concepts and silently fell through to the column label for every OTHER stock
  concept (`bs.assets.total`, `bs.equity.*`, deposits, loans, ...) — confirmed
  live: `bs.assets.total` and `pnl.income.total` both showed `period_label='4Q25'`
  at the same date, identical, because the branch never fired for stocks. The fix
  is not "add more concepts to the list" — it's that stamp-time shouldn't have
  been deciding this at all. `period_end` (unchanged, already correct) is what
  actually separates a stock from a flow: DBS's `Total assets` under three
  columns (`Year 2025`/`2nd Half 2025`/`1st Half 2025`) now shows THREE labeled
  points but only TWO distinct `period_end`s (897,488 @ 2025-12-31 appears under
  BOTH the FY25 and 2H25 labels, 841,896 @ 2025-06-30 under 1H25) — the collapse
  a downstream query needs is available by grouping on `period_end`, without any
  concept flag. `pnl.income.total` over the same three columns shows three
  genuinely different values, correctly not collapsing. `concept_period_kind`
  (the 6-row table) is kept, not dropped — unused by stamping now, available if
  a later display layer wants a ratio's OWN point-in-time-vs-annualised flag for
  *rendering*, which is a different question from what gets stored.
  Re-running the anchor projection (needed regardless, since the last reload
  wiped `concept_key_human`) surfaced two further real bugs, found and fixed:
  `migrate_add_human_anchor_projection.py`'s address computation had never
  received the two fixes made to `resolve_anchors.py` in the row-level anchor
  pass — (1) no title-like-parent collapse, so DBS's table-title-constant parent
  computed as the literal title text instead of `''`, missing every
  `bank_line_map` entry keyed on the collapsed empty-parent address (DBS
  `pnl.income.total` stayed unstamped even after a fresh projection run); (2) no
  preference for `row_dim.row_leaf_label` (raw) over the row_lineage
  footnote-resolved display form, so UOB's `'Total assets 5 72'` never matched
  the map's clean `'total_assets'` key. Both scripts must compute the identical
  address or a reload silently regresses coverage the map already knows how to
  answer — ported both fixes.
**Why:** matches the explicit direction that superseded the stock=date/flow=label
  design: two cells are the same period iff they share `period_end`; nothing
  else needs deciding at stamp time. The address-computation fixes are the same
  class of bug as everything else this multi-pass effort keeps finding —
  two writers (the anchor loader and the anchor projector) computing "the same"
  key with silently different logic, verified only by checking the DATA, not by
  trusting that a re-run "should" work.
**Discarded — verified, not a bug:** the Step 3 integrity assertion (per
  concept/year, FY should ≈ 1H+2H for a flow or FY=2H≠1H for a stock) initially
  flagged 24 "failures" — every one was a ratio concept (ROE, ROA, NIM, CIR, NPL,
  CET1, EPS). A ratio is neither a simple sum (flow) nor a static repeat
  (stock) — it's genuinely its own period-specific computed rate, a third
  category the binary rule can't classify, not a stamping defect. Excluding
  ratios/EPS and widening tolerance to 5% (for ordinary $m-rounding, e.g. UOB's
  amortisation 31 vs 16+14=30) leaves **zero unexplained failures** across 38
  flow/stock concept-year triples (27 flow, 11 stock). Also discarded: merging
  the two view-owning migration scripts (`migrate_add_human_anchor_projection.py`
  and `migrate_add_period_label.py` both `DROP`/`CREATE` the same 4 views, a
  known clobbering trap hit again this pass) — logged, not fixed, per explicit
  direction; and the UOB title-context bare-year grammar gap
  (`performance_by_geographical_segment_1 — 2024`) — still deferred, unchanged.

## 2026-08-03 — Feature-stamping pass: legal_entity loader ownership, armed period gates, period_label/period_end
**Decision:** (G5, must land first) moved `legal_entity` from an out-of-band migration
  (erased by every `_delete_doc` reload) into the loader itself —
  `pass2/load_v7.py` now derives it column-label -> parent-banner -> NULL via
  `legal_entity_map`, on both `col_dim` and `cell_fact`. Verified on a live
  reload before touching anything else: DBS's AUDITED BALANCE SHEETS
  Group/Company split reproduced exactly (68,867 / 17,643) with the migration
  NOT re-run. `schema_v7.sql` (the authoritative DDL, previously silent on this
  whole axis) updated to match — a fresh DB build from it was broken before
  this pass, not just the live one.
  (G4) Gate A2 (period-shaped column, no period) and Gate A3 (doc_period not
  among table periods) armed from advisory-only to hard-failing the load, with
  one deliberate carve-out on A3: a table whose OWN title names an explicit,
  different reporting date (a genuine comparative exhibit — DBS's prior-year
  Statement of Changes in Equity tables) stays advisory, since arming it
  unconditionally would have broken that legitimate, observed shape. Added
  `cell_fact.period_source` ('col'/'row'/'table_title'/'doc'), the real
  provenance the gates' own evidence was previously computing and discarding.
  (G11) Reloaded the 4Q25 anchor-resolution set (DBS, UOB, OCBC x2) plus
  DBS_4Q22 (the only other doc carrying the same stale `Year 20xx` defect) from
  existing artifacts, $0 replay. Zero `Year 20xx` columns with `col_period
  NULL` corpus-wide afterward (was 32).
  Added `period_label`/`period_end` as VIEW-level columns (no new cell_fact
  storage — both derive from the already-correct `period`/`period_span`),
  plus `concept_period_kind` (6 rows) for the one case a column header can't
  decide alone: UOB/OCBC's ratio block prints CET1 and ROE under an identical
  '2025' header, but CET1 is point-in-time (label = the date) and ROE is
  annualised (label = 'FY25') — a per-concept flag, defaulting every
  unlisted ratio to annualised per spec.
**Why:** G5 before G4/G11 is load-bearing, not a preference — reloading with
  `legal_entity` still out-of-band would have wiped the Group/Company/Bank
  axis corpus-wide (24,788 cells) the moment `_delete_doc` ran. Confirmed via
  exhaustive check, not sampling: 632 spine cells across all 3 banks' FY/2H/1H
  columns checked against their own column's stamped period, zero mismatches.
  Ratio-table footnote-value contamination checked two ways (344 cells scoped
  to `FS_RATIOS_KEY`, 1185 broadened to every `%`-unit cell in the reloaded
  docs) — zero contaminated in both.
**Discarded / found-and-logged, not fixed this pass:**
  - A **genuine remaining title-grammar gap**: UOB's `performance_by_geographical_segment_1
    — 2024` table has no column-axis period signal (its columns are
    geography, not period) and title-context deliberately keeps a bare-year
    guard (`parse_period_span("... — 2024", column=False)` returns `None` by
    design, to avoid false-positives elsewhere) — so this table's data is
    still silently stamped to `doc_period` (2025-12-31) instead of 2024-12-31.
    Neither armed gate catches it: Gate A2 doesn't apply (no column carries
    the year), and Gate A3's `table_period` already silently absorbed
    `doc_period` before any disagreement became visible. User-directed to
    defer (`just do 4q25 other banks`) rather than extend the bare-year guard
    under time pressure with the regression risk that entails.
  - **One narrow grammar extension made, not deferred**: OCBC's Fair Value
    Hierarchy note prints `"Fair value at 31 Dec 2025"` as a column header —
    `parse_period_span` could already parse the embedded date, but
    `is_period_text`'s prefix whitelist rejected the surrounding phrase,
    so Gate A2 (correctly) hard-failed the OCBC condensed-statements reload.
    Added `"fair value at"` to `_PERIOD_PREFIXES` — narrow, evidenced (exactly
    two real column label variants, both this pattern), and this table shape
    recurs across all three banks' Pillar 3 / fair-value disclosures, so it's
    a general fix, not one document's patch.
  - The naive "cells where `period == doc_period`" proxy metric was tried as
    a before/after headline number and rejected as misleading: it conflates
    genuinely mis-stamped cells with legitimately doc-period cells (comparison
    columns like `+/(-)%`/`QoQ`/`YoY`, single-period tables) that were never
    defects. The metric that actually validates correctness — a value-bearing
    cell in a genuinely multi-period table whose `period_source` fell to
    `table_title`/`doc` — is zero for every reloaded document, checked
    directly, not inferred from the proxy.
  - Full corpus reload (~20 docs / pillar3 filings / other quarters) was
    **not** attempted — scoped to the 4Q25 anchor set + the one other affected
    doc found (`DBS_4Q22_performance_summary`), per direction mid-pass. The
    corpus-wide "cells inheriting doc_period" count barely moves (8,859 ->
    8,200) as a result — an honest reflection of reload scope, not a failed
    fix; within the 5 reloaded docs the rigorous defect check is clean.

## 2026-08-03 — Steps 4-6: concept_home, derivation layer scope, cadence coverage catalog fix
**Decision:** (Step 4) new `concept_home(concept_key, bank, table_type_id)` records that
  UOB's EPS lives inside the combined highlights table and OCBC prints EPS in two
  legitimate places (statutory foot + media-release ratios) -- neither is an error.
  (Step 5) of the 3 named derived concepts, 2 were already implemented and correct in
  `concept_dictionary.yaml` (`pnl.noninterest.other`, verified for all 3 banks in its
  own comment); the 3rd (OCBC shareholders' equity) needed no formula -- OCBC prints
  "Attributable to equity holders of the Bank (Total)" directly on its statutory
  Balance Sheet, so it became a plain anchor instead of a subtraction formula built
  from untracked concepts (NCI, other equity instruments). The DBS NII group-total
  derivation that entered scope mid-pass (see the row_dim.row_parent bug, previous
  entry) was reconciled manually (14,494+6=14,500) rather than wired into
  `compute_ratios.py`, whose segment-in-the-row-index data model can't express a
  cross-segment sum without a real extension. (Step 6) new `v_anchor_coverage` view;
  fixed one real `table_catalog` defect it surfaced (UOB's income/balance/ratios
  seeded as 3 separate expected types, but physically one combined table -- corrected
  to `FS_HIGHLIGHTS_COMBINED`, matching every UOB anchor already resolved this pass).
**Why:** don't build a formula for something already printed directly (Step 5); don't
  force-fit an aggregation the existing engine's data model can't express under time
  pressure (better a flagged manual reconciliation than a wrong automated one); a
  coverage view is only as trustworthy as the catalog it reads, so a real mismatch
  it surfaces is worth fixing when the evidence is solid (UOB) and worth reporting,
  not guessing, when it isn't (see Discarded).
**Discarded — reported, not fixed, insufficient evidence budget:** two more
  `table_catalog` nuances the coverage view surfaced: OCBC's Q1 vs Q3 press releases
  genuinely differ in whether the highlights page splits into sub-tables (real
  cross-quarter structural variance, not obviously a bug); the media release's
  narrative "Full Year/Fourth Quarter 2025 Performance" blocks are seeded as
  `table_type_id=FS_INCOME_STATUTORY, is_narrative=False` but this session's own
  earlier 1Q25/3Q25 survey found them unclassified/narrative in the actual corpus --
  a question for whoever owns `table_registry_seed.csv`, not resolved here since no
  anchor depends on it. Also discarded: running `build_fact_metric.py` to verify Step
  5 -- it rebuilds `fact_metric` wholesale from `row_dim.concept_key` (not the new
  `_human` columns) and touches files with unrelated pre-existing WIP; run once by
  mistake, reverted immediately (fact_metric restored to 3,130 rows via table-level
  diff against the git-committed baseline, `fact_metric_conflicts.csv` restored via
  git checkout) once the side effect was noticed.

## 2026-08-03 — Row matcher hardening + row_dim.concept_key_human projection (Step 3)
**Decision:** three resolver bugs found and fixed while building row-level matching,
  in order of discovery: (1) leaf position assumed constant at `lvl2` -- wrong for
  UOB's combined-highlights table, where depth=1 IS the leaf; fixed to depth-relative
  (`lvl{depth}`/`lvl{depth-1}`). (2) empty `parent_row` in the map was treated as "skip
  the parent check" rather than "assert uniqueness" -- silently matched DBS's
  ambiguous "Net interest income" (present under both Commercial and Markets books)
  to whichever row the query happened to return first. Fixed to: empty parent_row
  requires the leaf to be unique across the whole search scope, raising `Ambiguous`
  (a hard stop, never a guess) if it isn't. A structural "is this parent title-like"
  heuristic was tried first and discarded -- it broke OCBC's ASSETS/LIABILITIES-
  sectioned balance sheet (falsely required a parent) and UOB's genuinely-unique
  "Common Equity Tier 1" (same false requirement) in opposite ways; uniqueness is the
  actual invariant an empty parent_row asserts. (3) the returned leaf/parent used
  `row_lineage`'s footnote-RESOLVED display label ("Total assets 5 72"), not the
  footnote-clean raw label (`row_dim.row_leaf_label`) -- matching worked (both forms
  were tried) but the WRONG one got persisted as `row_label_norm`, minting a second,
  footnote-suffixed address for a row the corpus already had a clean entry for (5
  UOB rows affected, cleaned up). Added `row_dim.concept_key_human` /
  `segment_key_human` / `identity_source` (never overwritten by `resolve_deterministic`,
  unlike `row_dim.concept_key`) and inverted the `v_cell`/`v_cell_flat` COALESCE to
  prefer them. Verified: DBS's Net Interest Income now separates into 3 distinct
  `(concept_key, segment_key)` rows (14,494/SEG_COMMERCIAL, 6/SEG_MARKETS,
  14,500/SEG_TOTAL) instead of one collapsed `pnl.nii.net`/NULL row.
**Why:** correctness of the stable-address invariant this whole pass exists to
  establish -- an anchor address that silently picks the wrong candidate, or mints a
  spurious duplicate address for data the corpus already has, defeats the point.
**Discarded — real upstream bug found, logged not fixed:** `row_dim.row_parent`
  genuinely (not just in the lineage reconstruction) parents DBS's "Of which: Net
  interest income" group-total row (14,500) under row_id 6, "Markets trading Income"
  -- confirmed by querying `row_dim.row_parent` directly, not just `row_lineage`. A
  pre-existing `bank_line_map` entry (map_id 523) had been hand-authored with the
  semantically correct parent ("Total income") instead, but that address matches no
  real row and was therefore never reachable by any automatic projection -- it just
  looked correct. Deprecated 523 in favor of the address that actually matches the
  data (map_id 522, same value, note merged over). The real fix is upstream, in
  pass2/geometry's hierarchy assignment for this table shape -- not attempted here.

## 2026-08-03 — G13 row-level anchor resolution: 4 parent corrections, 21 concept_key renames, 72 anchors loaded human_confirmed
**Decision:** resolved all 72 `anchor` rows (+3 `pending_extraction`, +5 `not_disclosed`) to
`(doc_id, table_type_id, row_lineage)` and loaded them into `bank_line_map` keyed on
`(bank, table_type_id, parent_label_norm, row_label_norm)`. Two categories of map
correction, both applied directly to `lineage_identity_map.csv` (not an alias layer —
the address/label distinction doesn't apply the way the document-filename case did):
  1. **4 parent_row corrections** — the stated parent didn't match the real row
     hierarchy: OCBC `ratio.roa`/`ratio.roe` parent → `"Performance ratios"`; OCBC
     `reg.capital.cet1_ratio` parent → `"Capital Adequacy Ratios"`; UOB
     `bs.nav_per_share` → top-level, no parent. All 4 verified against real
     `row_lineage` before correcting — not guessed.
  2. **21 concept_key renames** (crosswalk, new → existing dictionary vocabulary):
     `pnl.fee.net`→`pnl.noninterest.fee_commission`, `pnl.profit.ppop`→`pnl.profit.operating`,
     `pnl.amortisation.intangibles`→`pnl.opex.amortisation_intangibles`,
     `pnl.associates.share`→`pnl.associates`, `eps.basic`→`pnl.eps.basic`,
     `eps.diluted`→`pnl.eps.diluted`, `nav.per_share`→`bs.nav_per_share` (each applied
     to all 3 banks' instances of that key for cross-bank consistency). Reversible by
     this log: revert with the reverse mapping if the new vocabulary should win instead.
  Load result: 4 addresses newly anchored, 54 confirmed an already-correct
  `human_confirmed` row in place (no-op), 13 upgraded an `ai_proposed`/placeholder row
  to `human_confirmed` in place, 1 flagged (below). MERGE invariant verified by hash:
  the 106 `human_confirmed` rows are byte-identical before and after re-running
  `backfill_map.py`.
**Why:** "resolve to the address, load the value, flag the label" (explicit instruction)
 — a wrong name on a correctly-located number is a review item, not a blocker; only a
  failed address resolution is a hard stop. The crosswalk direction (new→existing) was
  chosen because the existing names are already `human_confirmed` and load-bearing
  elsewhere in the corpus; renaming the map costs nothing live, renaming the corpus
  would.
**Discarded:** inserting a second `bank_line_map` row for `bs.assets.customer_loans_net`
  (UOB) — rejected, not by choice but by schema: `UNIQUE(bank, table_type_id,
  row_label_norm, parent_label_norm)` allows exactly one row per address. The existing
  row (map_id 2315, `bs.assets.customer_loans_gross`, human_confirmed) is correctly
  labeled and was NOT overwritten; instead its `note` was annotated with the label
  conflict. **Open decision, not yet resolved**: the map's `bs.assets.customer_loans_net`
  concept has no dedicated anchor — UOB's highlights page only prints gross. A genuine
  net figure would need its own derivation or a different source table, tracked here as
  an open item, not silently dropped or forced.

## 2026-08-03 — G13 anchor scope resolution: 4 document aliases, 2 table aliases, DBS per-share data outstanding
**Decision:** resolved document → section → table for all 12 anchor tables across
DBS/UOB/OCBC 4Q25 via a new `document_alias` table (4 rows, map filename → doc_id,
map left unmutated) and 2 bank-scoped `table_registry_alias` rows fixing real
misroutes (OCBC `Key Financial Ratios` was losing to UOB's `'*'`-scoped
`financial_highlights` wildcard; UOB `Balance Sheets (Audited)` — mislabeled under
`Financial Highlights` in the map — now routes to the real statutory table, verified
via `cell_fact`/`row_lineage` that Total liabilities/equity aren't printed on UOB's
highlights pages at all). 11/12 resolve; DBS `Per share data` does not — traced to a
title-selection bug in extraction (`table.title` got the page masthead,
`table.label_header` correctly held `"Per share data ($)3,8"`), not a missing table.
The 3 dependent row anchors are marked `resolution=pending_extraction`, so the other
89 anchors proceed unblocked. See `docs/specs/2026-08-03-anchor-scope-resolution.md`.
**Why:** row-level resolution keys into `(doc_id, section, table_type_id)`; getting
scope wrong before row-mapping silently lands one bank's figure on another's concept
(the original G13 risk). Migration confirmed idempotent (re-run, zero errors, same
row counts) before landing.
**Discarded:** aliasing DBS's mistitled table by its masthead string
(`"dbs_group_holdings_ltd_and_its_subsidiaries"`) to force a `FS_PER_SHARE` match —
rejected as a per-document hack keyed on page furniture, not an exhibit identity; the
real fix belongs in extraction's `title` vs `label_header` selection.

## 2026-07-31 — `bank_line_map` becomes the source of truth for identity; `row_dim` becomes a projection
**Decision:** Identity (concept + dims + legal_entity + period_type + sign) moves to a
new durable `bank_line_map`, anchored on `(bank, table_type_id, row_label_norm,
parent_label_norm)`. `row_dim.concept_key` and the dim columns become a materialized
one-way projection of it. `concept/run.py` stops re-deriving keys from wildcard aliases
and becomes the projection step. Spec: `docs/specs/MAPPING_LAYER.md`.
**Why:** identity currently lives where it is overwritten by design — measured:
`row_dim.concept_key` is re-derived unconditionally on every `concept/run.py` run and
wiped entirely by a doc-scoped reload. That is why the mis-stamp fixes reverted and are
held only by running the fixer a second time *after* `run.py` (a workaround, recorded
2026-07-30). Moving the durable copy out of the overwrite path fixes the class.
A reload now *restores* identity by re-projecting instead of destroying it.
**Discarded:** parent-qualified aliases inside `concept_map` alone — rejected because
`concept_map`'s PK is `(table_type, label_norm)` and `table_type` is not stable
(below), so the anchor would still drift; and because it leaves identity in the
re-derived location, which is the actual defect.

## 2026-07-31 — `table_type` becomes a registry-assigned stable ID with a seeded alias table
**Decision:** a controlled vocabulary (`table_registry`, ~18–25 rows) plus a
many-to-one `table_registry_alias` table. `normalize_exhibit_title()` strips period
tokens, footnote markers and unit parentheticals but PRESERVES dimensional qualifiers
(`by_geography`, `by_business_segments`, …). A miss is UNCLASSIFIED → review queue;
no `'*'` wildcard fallback, no fuzzy match at load time.
**Why:** measured on the 375-table corpus — `table_type` has 217 distinct values and
**158 (73%) appear in exactly one document**; the same exhibit appears as
`selected_income_statement_items_m`, `selected_income_statement_items_1st_half_2025`
and `performance_by_business_segments1_selected_income_statement_items2`. Keying the
map on it would miss on most new quarters and the map would never converge.
**Discarded:** (a) *normalization alone, no alias table* — rejected by measurement:
stripping period/footnote noise collapses only **217 → 175 (19%)**; the residue is
genuine title variation (`financial_highlights` 6 / `_continued` 11 / `_unaudited` 7
are one OCBC exhibit). (b) *reusing `map_table_type_norm()` as the registry* — it is
11 substring rules → 7 buckets with **34% coverage, 66% falling through to `'*'`**
(`pipeline/concept/load_dictionary.py:36-65`), and a `'*'` row resolves against
wildcard aliases, i.e. it guesses. It is a concept-scoping helper, not a registry.
(c) *stripping `by_geography`-style prefixes as noise* — rejected: it would merge the
geography-decomposed exhibit into the group exhibit and stamp geo cuts as group totals.

## 2026-07-31 — Dimensions stay column-per-axis this phase; the `fact_dim_member` bridge is deferred
**Decision:** keep the existing `geo_key`/`segment_key`/`industry_key` columns. Revisit
a generic `dim_axis`/`dim_member` bridge when grading/collateral/currency exhibits
enter scope.
**Why:** the 26-item highlights dashboard needs only those three axes, all of which
already exist and are already in `fact_metric`'s PK. A bridge rewrites every fact
query plus `v_cell_flat` and `sync_bq` — expensive mid-sprint, cheap to defer.
**Discarded:** building the bridge now — rejected on sequencing, not on merit; the
open-ended axes come from tables not yet ingested.

## 2026-07-31 — Derived metrics will be un-materialized from `fact_metric` (snapshot first)
**Decision:** when `metric_definition` lands, the derived rows currently stored in
`fact_metric` are removed — but snapshotted first, and the new formula resolution must
reproduce their values before deletion.
**Why:** storing derived values as facts is a double-resolution trap: the formula and
the stale stored row would eventually disagree. Measured: 117 `pnl.noninterest.other`
rows carry `source_doc_id='derived'`, `source_table_id='formula'`. The reproduce-then-
delete order turns the removal into a free correctness check on `metric_definition`.

## 2026-07-30 — One canonical period label; an as-at STOCK never renders like a flow
**Decision:** New pure `period_label(period, period_span)` in the app composes the display token from the STORED machine fields, never from the printed header text: flow spans → `FY25` / `2H25` / `1H25` / `1Q26` / `9M25`; `as_at` (and NULL/blank/unrecognised span) → the date form `31-Dec-25`. Applied to the Table Registry period column, the Database numeric-pivot headers, and the Dashboard chart axes. NOT applied to the raw "original PDF shape" view (verbatim headers stay, so it lines up with the PDF panel beside it), and the CSV/Excel export KEEPS its ISO `period` and merely gains `period_label` alongside.
**Why:** the three banks print the same period every possible way — `1st Qtr 2026`, `1Q25`, `1st Half 2025¹`, `2024 $m`, `2H 2025 (1)`, bare `2025` — so column headers were not comparable across banks or even across tables of one document. Composing from `col_period` + `period_span` makes the bare-year and short-month forms (`2024`, `Mar 2024`) resolve correctly too, which no amount of header-text parsing would.
**Discarded:** (a) the app's previous `_fm_period_label`, DELETED — it rendered `as_at` as a DERIVED CALENDAR QUARTER, so a balance-sheet stock at 2025-12-31 displayed `4Q25`, indistinguishable on the axis from the FY flow ending the same day. That collision is the entire reason `period_span` exists; collapsing it in the display threw away the distinction the loader works to preserve. (b) replacing the ISO date in the CSV/Excel export (user decision, "dont replace") — an export is machine input; `FY25` would break any downstream date parsing. Additive column instead. (c) preserving a unit token in the header (`2024 $m` → `FY24 $m`) — user chose period-only; the unit is stored on col_dim/cell_fact and shown elsewhere, so nothing is lost from the data.
**Known divergence (not fixed):** `pipeline/dashboard.py` still carries its own copy of the OLD helper and now disagrees with the app. Left alone deliberately — separate surface, out of scope; reconcile if that dashboard is still in use.

## 2026-07-30 — Geometry stage SHIPPED: all-or-nothing per table, total-skip disabled, clean labels stored not substituted
**Decision:** The accepted geometry design is now wired end to end. (1) New pipeline stage **STEP 2b** in `run_doc` (in-process, $0, after every extraction, before every load — both load sites, incl. the verify re-extract loop) writes the side-car; `"geometry"` added to `ingest_status.STAGES` and the app stepper. (2) `transforms.apply_geometry` is **ALL-OR-NOTHING PER TABLE**: it fires only when `all_rows_matched` AND the row counts agree AND every row has an indent; any shortfall falls back WHOLLY to model levels. (3) On the geometry branch `row_parents_by_position` runs with `skip_terminal=False`. (4) Clean labels are **stored in new nullable `*_clean` columns, never substituted for the verbatim label**, and are preferred (COALESCE) only on IDENTITY paths: `row_lineage`, the row-axis geo/segment/industry lookups, and `concept.resolve_deterministic._fetch_rows`. (5) `table_t.hierarchy_source` ('geometry'|'model') is the visible record of which branch fired.
**Why:** (2) a partial override would mix two incompatible depth scales inside one parent walk, so a bad indent threshold on an unseen bank degrades to today's behaviour instead of corrupting structure. (3) the total-skip rule exists to survive WRONG levels (the DEBTS ISSUED defect); on GEOMETRIC depths it is harmful — DBS prints `Of which: Net interest income` indented directly under `Total income` and it genuinely IS its child. (4) the verbatim label is the evidence the $0 verifier eyeballs against the PDF, so byte-fidelity must survive; but registry identity must NOT fork on footnote numbering that renumbers each quarter. (5) project rule: a decision-tree pivot must be observable without reading code.
**Evidence (DBS_1Q26 reloaded on the live DB):** 4/4 tables matched, 49/49 rows, all four `hierarchy_source='geometry'`; 49→47 rows (2 phantom twins merged) with cells UNCHANGED at 201 — nothing lost. `Of which: Net interest income`→parent `Total income`; per-share `Net book value5` now top-level (was under `Reported earnings`); ratios lineage key `Return on equity` (was `Return on equity4, 5`); `table_title_clean` `Key financial ratios (%)`. fact_metric: 23 concepts × 3 periods from this doc, `bs.assets.total` 935,365@1Q26 agreeing exactly with the independent P3 doc. pass2 suite 33→51 green.
**Discarded:** (a) feeding `col_leaf_label_clean` into `col_lineage`/column-axis lookups — column identity already has its own footnote handling (`_COL_FOOTNOTE`) tuned around combined period+unit headers ('2H25¹ $m'), and the defect this stage exists to fix is entirely row-axis; stored but unconsumed pending column-side evidence. Observed cost of NOT doing it: `col_leaf_label_clean` is NULL on every DBS_1Q26 column (`_find_clean_match` is best-effort). (b) keeping the printed-parent cross-check warning under geometry — `GRow.parent` echoes the model's OWN level scheme, so on a corrected table it disagrees by construction and would emit one warning per reparented row; suppressed on that branch only. (c) deleting the 3 footnote-polluted `concept_map` aliases NOW — evidence: 318 of 322 tables are still on the `model` branch with NULL clean labels, so those rows still key on the polluted verbatim label and would LOSE their concept stamp; sequenced after the batch sweep.
**Measured non-result (recorded honestly):** the DB-wide concept gates barely moved — `uniqueness_per_table` 283/283 failed before and after, `sums_to_component_vs_total` 39→40, `nature_as_at_magnitude` 207→204 failures. They are dominated by the 318 un-reloaded tables and will not move until the sweep. Also: the "INV-4/INV-6" labels in the previous PROGRESS entry exist nowhere in the repo — the real gate names are `additive_identity` / `ratio_formula` / `uniqueness_per_table` / `sums_to_component_vs_total` / `nature_flow_as_at` / `nature_as_at_magnitude` in `concept/validate.py`.

## 2026-07-30 — Industry becomes a first-class axis; ambiguous labels stamp only the table-dominant axis
**Decision:** New `industry_dim` (10 members incl. sentinel `IND_TOTAL`) + nullable `industry_key` on row_dim/col_dim/cell_fact/fact_metric, stamped at load exactly like geo/segment via a shared MAS-category alias map (all 3 banks' industry tables use near-identical categories — verified from the corpus inventory; only '&'/'and'/plural variants differ). Migration `migrate_add_industry_dim.py` backfills `IND_TOTAL`. Plus the AXIS-EXCLUSIVITY rule: a label matching >1 axis map (the 'Others' problem — SEG_OTHER + geo OTH double-stamps, 682 cells) stamps ONLY the axis with ≥2 unambiguous member matches on that table axis; no dominant axis → no stamp + drift warning.
**Why:** industry breakdowns (NPL/loans by industry) were unqueryable — members were bare row labels; and independent per-axis lookups let one ambiguous label contaminate a second axis.
**Discarded:** encoding industry in row labels/concepts only — evidence: 'Manufacturing' etc. carried geo=GLOBAL/seg=SEG_TOTAL/concept=NULL, so no industry time series was expressible. The 682 legacy double-stamps clear on the batch reload (exclusivity applies at load time).

## 2026-07-30 — Highlights view: null mappings over guessed ones; derived values fill-only with visible provenance
**Decision:** The Dashboard "Key Financial Highlights" view is config-driven (`app/highlights.yaml`, the user's 4-section item list verbatim). Labels with no corpus concept stay `concept: null` and render as visibly-empty rows (8 of 24: Other non-interest income, Operating profit, Amortisation, Share of associates, ROA, Basic/Diluted EPS, NAV/share). Derived metrics (new `metric_kind: metric` entries in the concept dictionary) are computed by the generalized `compute_ratios.py` engine with FILL-ONLY-MISSING semantics — a reported row is never overwritten — and carry the formula verbatim in `source_row_label`, shown on chart hover / grid expander.
**Why:** an empty cell sends the tagging team to the right gap; a guessed mapping silently lies. Verified example of why guessing fails: UOB's `pnl.nii.net`(9,355) + `pnl.noninterest.total`(4,453) = `pnl.income.total`(13,808) exactly — so mapping 'Other non-interest income' to `pnl.noninterest.total` would double-count against the fee/commission row.
**Discarded:** (a) mapping 'Operating profit' to `total_income − opex` — its meaning varies bank-to-bank around provisions placement (per-bank special case, prohibited); (b) using keys from the draft `concept_dictionary_additions_2026-07-27.yaml` — explicitly not loaded by the pipeline, cells would be empty for the wrong reason. Caveat recorded in the dictionary: DBS's treasury-customer vs markets-trading split shares one concept today (alias collision), so the non-interest derivation approximates from fee+trading+investment until tagging splits them.

## 2026-07-30 — Row hierarchy: PDF geometry becomes authoritative over model-emitted levels
**Decision:** (design accepted, implementation queued) A new deterministic PASS2 geometry stage (`pass2/geometry.py`) derives per-row indent depth from the label's first-ink x-position (pdfplumber chars; leading-space aware), detects superscripts by size+baseline (footnote strip with NO digit regex), and assigns printed-line identity (merges the phantom `section_header` + identical-label data twin). When geometry matches all rows of a table it overrides the model's `level`, the parent walk runs on corrected depths, and the loader's total-skip parent rule is disabled; otherwise the table falls back wholly to today's behavior. Output is a side-car in `parsed.json` (never new `GRow` fields — those would change the Gemini response schema). Labels stay verbatim; cleaned labels land in new nullable `*_clean` columns.
**Why:** the model's `level` field conflates "data row" with "visually indented" and wobbles between tables (`Expenses` level 1 vs equally-flush `Reported net profit` level 0; per-share `Net book value5` leveled like the indented `Basic`/`Diluted`). The loader derives `row_parent` purely from levels (`extract_run.py:294`), so every level error becomes a parentage error. Geometry is the only bank-agnostic ground truth. **Key evidence that this is typography- not code-dependent: Pillar 3 uses the SAME prompt and loader and its hierarchy is correct** — P3 is a fixed regulatory template whose valueless section headers exactly match the model's level scheme (per-doc phantom-dup counts: DBS 1Q26 FS 2-in-4-tables vs P3 1-in-8-tables); freeform FS typography (bold rows WITH values, space-indents, mid-table totals) is what breaks it. Arithmetic cross-check confirms the geometric structure: 5,559 + 389 = 5,948 (Commercial book + Markets trading = Total income, siblings).
**Discarded:** (a) prompt tuning — the extraction prompt is inline at `extract.py:65`, shared by fs AND pillar3 with no router branch; editing it flips `_PROMPT_HASH`, invalidating ~324 cached units (≈$5.50 re-extraction + model nondeterminism re-roll). Logged as prompt-location debt instead. (b) trusting/patching model levels case-by-case — that is per-document overfitting by definition. Caveat recorded: indent-cluster threshold (0.5×body size) validated on DBS only; check UOB/OCBC pages before default-on.

## 2026-07-30 — Per-column periods: a grammar gap in the loader, fixed at load time (reload-only)
**Decision:** Extend the period grammar in `pass2/load_v7.py` — `_QTR_ORD_RX` accepts digit ordinals + 'Qtr'/'Q' abbreviations, halves symmetrically, `_FULLYEAR_RX` accepts 'Year YYYY' → FY — ordered BEFORE the bare-year fallback; `is_period_text`'s residual guard stays untouched; add a load-time gate warning ("period-looking column yielded no period"); `build_fact_metric` unchanged (groups off `v_cell_flat.period` and already excludes change-columns). Fix ships as STEP-3 reload of all docs, zero API cost.
**Why:** `_QTR_ORD_RX` (`load_v7.py:127`) only matched spelled-out ordinals + 'quarter', so '1st Qtr 2026' fell through to the bare-year fallback (would stamp FY-2026!) and was saved only by the residual guard → `col_period` NULL → `table_period = doc_period` → every cell stamped with the doc period. Evidence: 16/20 docs have `col_period` populated; the 4 zero docs are exactly the `*_trading_update` docs (the only 'Nth Qtr YYYY' users); `fact_metric` for DBS_1Q26 = 22 concepts × 1 period with cross-period values (`bs.assets.total` 840,823 = the 1Q25 figure stamped 2026-03-31; true 1Q26 = 935,365).
**Discarded:** (a) re-extraction — unnecessary, the extracted column labels are correct, only the loader failed to parse them; (b) relaxing the `is_period_text` residual guard — it is the only protection against silent wrong-FY stamps today; (c) touching `build_fact_metric` — verified its grouping needs nothing once `cell_fact.period` is right.

## 2026-07-30 — Phantom col_dim rows: normalize echo-groups at load, don't re-extract
**Decision:** Pure normalizer `drop_echo_groups` in `pass2/transforms.py`, called from `load_v7._load_table`: a column whose `group` equals its `leaf` (the model's 'echo' of a single-level header) gets `group=None`, so no 100+ phantom `col_dim` rows are minted.
**Why:** verified model nondeterminism — `key_financial_ratios_2_3_p6` emits `group==leaf` while the income statement on the SAME page emits `group=None` against the same prompt instruction.
**Discarded:** re-extraction or prompt edit — same `_PROMPT_HASH` cache-invalidation cost as above for a defect the loader can neutralize deterministically.

## 2026-07-30 — Naming is standardized at the DISPLAY layer only; stored titles never mutated
**Decision:** New pure helper `display_name()` applied everywhere a table/section title is shown: underscores → spaces, whitespace collapsed, and ALL-CAPS strings (≥2 alpha chars) → sentence case ("NET FEE AND COMMISSION INCOME" → "Net fee and commission income"). `table_t.table_title`/`section` rows in SQLite/BQ keep the source document's exact casing.
**Why:** the stored title is evidence (it must match the PDF for verification/eyeballing); presentation is an app concern. One helper = one consistent rule for Database, Registry, and future Dashboard labels.
**Discarded:** normalizing titles at load time — would bake a display preference into the data and break byte-fidelity with the source document. Known cosmetic limit (accepted): acronyms inside ALL-CAPS titles get sentence-cased too ("DBS GROUP HOLDINGS…" → "Dbs group holdings…"); an acronym whitelist was rejected for now as a hardcoded list.

## 2026-07-30 — Database drill-down: full-exhibit View default, sub-tables as drill-down; PDF order from section.seq
**Decision:** The Database view's table selector became a **View** selector: option 1 = "Full view — all N tables (PDF order)" (the whole exhibit stacked, e.g. DBS's Key financial tables pp.6–7), then each sub-table by its human `table_title` (+ page, deduped) as the drill-down (rows identity, raw table, CSV, pivot, PDF page). Reading order comes from `section.seq` via a LEFT JOIN (`ORDER BY COALESCE(s.seq, 999999), t.section_no, t.table_id`).
**Why:** matches how the filings read — one visual exhibit made of sub-tables — while staying fully general (any doc, any bank; no per-doc grouping config). The schema has no parent "exhibit" section (all `section_level`=1, flat), so document-level full view IS the exhibit for these filings.
**Discarded:** (a) ordering by `table_t.section_no` — it is NULL for FS docs, so ORDER BY silently degraded to alphabetical (ratios before income statement; verified against p.6); (b) hardcoding a "key financial tables" group for trading updates — per-doc special case, violates the no-overfitting rule; contiguous-section exhibit inference can come later if a real need appears.

## 2026-07-30 — Raw table view RECONSTRUCTS from the schema; phantom columns hidden, not deleted
**Decision:** The Database view's "raw table — original PDF shape" is reconstructed live from schema_v7 (`row_id`/`col_id` order, `row_hierarchy` indent depth, `cell_fact.value_raw` original text, duplicate headers kept as separate columns via zero-width-space suffixes for Arrow). Full-document mode stacks all tables in `section_no` (PDF) order. Zero-cell `col_dim` rows are hidden in the display with a visible "N unused column definition(s) hidden" caption — the DB rows stay untouched.
**Why:** the schema already round-trips the original shape, so a stored rendering would be a second source of truth; a column no cell references cannot exist in the PDF render, but silently deleting the rows would erase the evidence of the loader bug.
**Discarded:** (a) keeping the numeric `pivot_table` as the primary view — evidence: it collapses the PDF's duplicate '% chg' columns into one (pandas pivot aggregates same-name columns) and drops non-numeric cells ('NM', '>100', '-'); demoted to an expander. (b) fixing the phantom columns in-DB this session — root cause is the loader (`col_id` 100+ rows with 0 cells on 2/4 DBS_1Q26 tables, e.g. `key_financial_ratios`: col_ids 100-102 duplicate 1-3 with 0 cells each) — queued under extraction quality.

## 2026-07-30 — Database view shows the ORIGINAL PDF as server-rendered page images
**Decision:** The app's Database drill-down gets an "Original document" panel: pages rendered server-side to PNG with `pypdfium2` (cached per page), defaulting to the selected table's `table_t.page_range`, with a page picker for the rest of the doc. PDFs resolve local-first, then `source_store.materialize` from the GCS bucket; pre-migration docs without a blob degrade to an info message.
**Why:** the point is eyeballing extraction vs source — that requires landing ON the table's page automatically, and `page_range` is already stamped per table. pypdfium2 is already a pipeline dependency.
**Discarded:** embedding the raw PDF (`st.pdf` / base64 iframe) — no page-targeting (cannot auto-open at the selected table's pages), ships multi-MB files to the browser per rerun, and iframe PDF viewers are inconsistent inside proxied web previews. Evidence: `table_t.page_range` gives exact pages (e.g. all 4 DBS_1Q26 tables sit on pages 6–7 of a 7-page PDF), so 1–2 cached PNGs replace a whole-file embed.

## 2026-07-30 — Pipeline subprocesses run under `sys.executable`, not bare `python3`
**Decision:** `run_doc.py`, `ingest_quarter.py`, `ingest_manifest.py` now launch every subprocess step with `sys.executable` (module constant `PYTHON`) instead of the literal `"python3"`.
**Why:** the orchestrator must work however it is invoked — `no human in the loop` includes no human remembering to activate a venv. Whatever interpreter runs the orchestrator is by definition the one with the pipeline deps.
**Discarded:** keeping `"python3"` + documenting "activate the venv first" — evidence: invoking `.venv/bin/python3 run_doc.py --pdf financial_statements/DBS_1Q26_trading_update.pdf` on the workstation failed at STEP 1 with `ModuleNotFoundError: No module named 'pdfplumber'` because the `toc_stage.py` subprocess resolved `python3` to the system interpreter (task output 2026-07-30). A docs-only fix leaves the same trap for Cloud Run jobs, cron, and the app's trigger path.

## 2026-07-30 — Workstation smoke test on a known-good doc, not an `nt=0` repair doc
**Decision:** validated the fresh workstation env by re-running `DBS_1Q26_trading_update` (previously verify-PASS, in GCS) end-to-end, rather than starting with one of the 3 `nt=0` FS docs from the roadmap.
**Why:** a known-good doc isolates the variable under test (the environment); an `nt=0` doc conflates env failures with extraction failures. Result: verify PASS, 4 tables / 201 cells, $0.10, 339 s — env proven.
**Discarded:** the handoff's named doc `financial_statements/DBS_4Q25_performance_summary.pdf` — evidence: `source_store.list_sources()` has no such key (it was ingested pre-GCS-migration from local disk; only `pillar3/DBS_4Q25_pillar3.pdf` exists for that period).

## 2026-07-30 — Tagging workbook: shared concept template per family, not per bank
**Decision:** One shared **concept template per table-family** (the dictionary the 3 banks map into); each bank keeps its own extracted table + its own `label→concept` map (which inherits to future periods). The tag workbook stacks 3 banks in one sheet.
**Why:** Cross-bank equivalence (e.g. OCBC "non-impaired" = DBS "Stage 1 and 2") can only be judged with all three in view.
**Discarded:** one template *per bank* — would produce three internally-consistent, mutually-incompatible vocabularies (the user's structural argument). Evidence: the 3 banks' income statements are genuinely different shapes/rows (DBS 22, OCBC 33, UOB 30 rows for the same family — `table_t`/`row_dim` counts), so there is no single shared table shape; only the concept space can be shared.

## 2026-07-30 — Identity is a tuple (row × column), not a single tag
**Decision:** A cell's identity = row-identity × column-identity; both axes get tagged. Date tables: row=(concept, agg_role, group), col=period. Dimension tables (NPA): row=concept, col=(axis, member).
**Why:** Dimension tables lose their axis if only the row carries a concept.
**Discarded:** the current single `row_dim.concept_key` as sufficient — evidence: NPA/staging tables have their breakdown on columns (`col_dim`), which a row-only tag cannot express.

## 2026-07-30 — Live progress reads SQLite, not BigQuery
**Decision:** The app's live ingest-progress view polls `ingest_status` in the local SQLite `compiled_fs.db`; BigQuery is for the analytics dashboard only.
**Why:** the pipeline writes `ingest_status` live at every stage.
**Discarded:** reading BQ for live progress — evidence: `sync_bq` runs only at STEP 7 (end) or once per sweep, so BQ only ever shows the final state, never the stage-by-stage progression.

## 2026-07-30 — Dashboard app hosted via Streamlit on the workstation, not public Cloud Run
**Decision:** Prototype/run the app as Streamlit on the Cloud Workstation (or Streamlit Community Cloud for a public link) rather than public Cloud Run.
**Why:** avoids the org policy wall and needs no key on the workstation (ADC).
**Discarded:** public Cloud Run URL — evidence: org policy `constraints/iam.allowedPolicyMemberDomains` is enforced (`allowedValues: [C00u5vr45]`), so `allUsers` cannot be granted even by a project Owner; and `run.services.setIamPolicy` returned `PERMISSION_DENIED` for the editor user (verified live 2026-07-29).

## 2026-07-30 — Column-shift handled by post-extraction geometry repair (keep), not re-extraction
**Decision:** Keep the deterministic `validate_column_bands` + `repair_column_bands` (pdfplumber x-position geometry), already wired in `pass2/extract.py:446,452`.
**Why:** it works and is tested (`test_column_repair.py` passes on real DBS 1Q26 fixtures) and repairs shifts caused by sparse rows (dashes) packing values leftward.
**Discarded (for now):** geometry-anchored extraction upfront (assign to bands at extract time) — cleaner long-term but a bigger change; deferred. Note: Pillar 3 rarely triggers it (dense grids) vs FS (sparse multi-period columns) — that's why FS surfaced the bug.

## 2026-07-30 — GCS is the source of truth for raw PDFs (canonical key K)
**Decision:** `source_store.py` — raw PDFs live in `gs://findociq-sources-…`; `run_doc --pdf <key>` materializes on demand; canonical key `K = "<folder>/<file>.pdf"` drives gcs_uri / local path / `ingest_status.source_file` / doc_id.
**Why:** removes local-disk dependence; enables the unattended flow; runs under editor (no admin).
**Discarded:** (a) streaming `gs://` into pdf libs — too invasive, touches ~20 call sites, pypdfium/pdfplumber differ; (b) whole-corpus sync before every run — wasteful for single-doc runs. Evidence: the invariant analysis found ~20 open sites all taking local paths, and `retry_worker` already pulled the entire corpus each run.

## 2026-07-29 — `--out-root` must redirect every family; sheet suffix conditional
**Decision:** `--out-root` now repoints `_OUTPUTS_ROOT` too (all families), and `Table N` sheet suffix appears only when a section has >1 table.
**Why:** fs output was silently written into the repo working tree; single-table tabs were over-truncated.
**Discarded:** pillar3-only override (the pre-existing behaviour) — evidence: an fs run wrote `findociq/outputs/fs/dbs_1Q26/…` into the working tree unintentionally.

