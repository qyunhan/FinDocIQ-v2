# Document-Family Router — design (2026-07-12)

**Status:** approved design, pre-implementation. **Binding for the implementation plan.**

## Question

When a user drops a bank disclosure PDF into the system, which of the three
processing families does it belong to — **slides**, **pillar3**, or
**financial_statements (fs)** — and what institution / period / FS-subtype is it,
so the correct downstream pipeline can pick it up without a human sorting it into
a folder?

## Motivation

Today, family sorting is manual: files live under `data/sources/{pillar3,
presentations,financial_statements,regulatory}/` because a human put them there.
The end state (per CLAUDE.md — humans OUT of the loop) is drop-and-forget: a
person names a file with the obvious convention and the tool routes it. Folder
placement and filename become *hints*, never the routing authority — a misnamed
or misfiled document must not silently route to the wrong pipeline.

FS naming is especially noisy for the same underlying quarterly financials:
`trading_update`, `performance_summary`, `Performance Highlights`,
`performance-highlights`, `Condensed Financial Statements`, `Condensed Interim
Financial Statement`, `Unaudited_Interim_Financial_Statements`,
`Results_Press_Release`, `Media_Release_and_Financial_Highlights`. All are the
**fs** family; they differ only in subtype.

## Decision this component produces

Per document: a single manifest row `{path, family, institution, period,
fs_subtype, has_contents_page, confidence, flags}`. The router **classifies and
emits a manifest — it never moves files and never invokes a pipeline.** The
per-family pipelines consume the manifest. This keeps the router a thin, testable
unit and gives a human an inspectable checkpoint (`manifest.csv`) before any
extraction runs — which also satisfies the CLAUDE.md rule that decision-tree
pivots must be *visible*: the manifest shows which family/subtype fired for each
file without reading code.

## Scope

**In scope (this spec):**
- `classify_doc(pdf_path) -> dict` — one manifest row.
- `build_manifest(paths) -> list[dict]` — many rows.
- CLI writing `findociq/pipeline/classify/out/manifest.csv`.
- The three-family fingerprint + hybrid field derivation + cross-check flags.

**Out of scope (next cycle, separate specs):**
- The FS extraction pipeline.
- The FS discovery path — a GENERALIZED contents-page parser handling all three
  printed formats (`Title <pagenum>`, `<pagenum> Title`, and Pillar 3's
  `Title …dots… PageRef`) that arranges the printed contents page into a section
  map deterministically (zero LLM). Corpus validation (below) shows every
  substantial FS has such a page, so this deterministic path is the FS majority;
  the Gemini heading branch is only the fallback for the short highlights with no
  contents page. `pass1_toc`'s current parser is dot-leader-only and does NOT
  cover the FS formats — it must be **generalized**, not merely "tested."
- Any PaddleOCR wiring.
- Auto-dispatch (router invoking pipelines). Deliberately excluded — the manifest
  is the hand-off contract.

## Placement & interfaces

New module `findociq/pipeline/classify/family.py` (document-level classification —
distinct from `pipeline/route/scan.py`, which routes *pages within* a document).

```python
def classify_doc(pdf_path: str) -> dict:
    """One manifest row. Reads the PDF; never writes/moves files."""

def build_manifest(paths: list[str]) -> list[dict]:
    """Map classify_doc over paths (unreadable files → ERROR rows, never dropped)."""
```

CLI (base python, no heavy deps beyond `pypdfium2`/`pdfplumber` already in use):
```
python3 findociq/pipeline/classify/family.py <path-or-dir> [--out out/manifest.csv]
```
Given a directory, it walks it recursively for `*.pdf`.

## Manifest schema

`manifest.csv`, one row per file, columns in this order:

| column | type | source | notes |
|---|---|---|---|
| `path` | str | — | absolute path to the PDF |
| `family` | str | **content** | one of `pillar3` \| `slides` \| `fs` \| `ERROR` |
| `institution` | str | filename | canonical institution string (registry lookup) |
| `period` | str | filename | ISO-ish `YYYY-QN` (e.g. `2025-Q2`) |
| `fs_subtype` | str | **content** | `full` \| `highlights` \| `""` (non-fs) |
| `has_contents_page` | int | **content** | `1`/`0`; `""` if not evaluated (non-fs/non-pillar3) |
| `contents_page_number` | int | **content** | 1-based page index of the detected contents page; `""` if none. Points the FS pipeline's Gemini TOC call at the right page. |
| `confidence` | str | — | `high` (strong content signal) \| `low` (fell to default/weak) |
| `flags` | str | — | `;`-joined; empty when clean (e.g. `family_mismatch`) |

## Per-field derivation

### institution + period — from the filename (registry + regex)

Metadata only; a registry dict, not behavior (allowed under the no-per-bank-hacks
rule — behavior downstream must never branch on these).

```python
INSTITUTIONS = {
    "DBS":  "DBS Group Holdings Ltd",
    "OCBC": "Oversea-Chinese Banking Corporation Limited",
    "UOB":  "United Overseas Bank Limited",
}
# institution: first INSTITUTIONS key found as a case-insensitive SUBSTRING of
#   the filename stem (substring, not token-split — handles "DBS4Q25_CFO..." where
#   the bank name has no separator). Keys are mutually non-overlapping (DBS/OCBC/UOB).
# period: re.search(r"([1-4])Q(\d{2})", stem) -> f"20{yy}-Q{q}" (also matches an
#   embedded "4Q25" inside "DBS4Q25").
#   Fallback for annual/full-year naming (e.g. "FY25", "4Q" already covers Q4):
#   r"FY(\d{2})" -> f"20{yy}-Q4"; if neither matches -> period="" + flag "no_period".
```
If no `INSTITUTIONS` token is present → `institution=""` + flag `no_institution`.

### family — from CONTENT (page-1 text + page geometry)

Read page 1 (and page 2 as fallback) text via `pdfplumber`, plus page dimensions.
Decide in this precedence order (first match wins):

1. **pillar3** — page-1 text (casefolded) contains `"pillar 3"`. Strongest, most
   specific signal. `confidence=high`.
2. **slides** — page geometry: landscape (`width > height`) AND low text density
   (page-1 word count below `SLIDE_MAX_WORDS = 120`). Slide decks are wide with
   sparse large text; statements/pillar3 are portrait and text-dense.
   `confidence=high` if both hold; if only one holds, still `slides` but
   `confidence=low` + flag `weak_slide_signal`.
3. **fs** — default for any remaining financial document. Positive corroboration
   (any of, casefolded): `"financial statement"`, `"condensed"`, `"interim"`,
   `"unaudited"`, `"results"`, `"highlights"`, `"performance"`, `"trading
   update"`, `"press release"`, `"media release"`, `"income statement"`,
   `"balance sheet"`. If corroborated → `confidence=high`; if it fell here with
   no corroboration → `confidence=low` + flag `weak_fs_signal`.

No `if OCBC`-style per-bank branches anywhere — every rule is a general content or
geometry signal that would work for a bank we have never seen.

### fs_subtype — from CONTENT (fs only; else `""`) — ADVISORY, not load-bearing

Casefold page-1 text: **full** if it contains any of `"financial statement"`,
`"condensed"`, `"interim"`, `"unaudited"`; else **highlights**.

This is a human-readable hint ONLY. It is KNOWN to mislabel DBS — its 46-page
`performance_summary` is a full disclosure named without any of those keywords, so
it tags `highlights`. **The FS pipeline MUST NOT branch on `fs_subtype`.** It
branches on `has_contents_page`, which correctly treats DBS's `performance_summary`
as contents-bearing (it has a printed contents page on p3) regardless of its name.
`fs_subtype` is retained purely for at-a-glance manifest readability.

### has_contents_page — from CONTENT (fs + pillar3; else `""`)

**GENERAL rule — keyed on what a contents page IS, not on the two formats this
corpus happens to use.** Scan the first `CONTENTS_SCAN_PAGES = 12` pages; a page
qualifies iff BOTH hold:

1. **Contents label present** — casefolded text contains `"contents"`,
   `"table of contents"`, or `"index"`. This is the near-universal header for a
   TOC across banks and document types — a general linguistic signal, not a
   corpus artifact.
2. **≥ `MIN_TOC_ENTRIES = 4` entry lines.** An entry line, after collapsing
   dot-leaders (`re.sub(r"[.·…]{2,}", " ", line)`), matches EITHER order with a
   plausible page reference (`<pageref>` = integer in `1..n`, or letter label like
   `A-2`):
   - `<pageref> Title`  — `^\s*([A-E]-?\d{1,3}|\d{1,4})\s+\D.*\S\s*$`
   - `Title <pageref>`  — `^\D.*?\S\s+([A-E]-?\d{1,3}|\d{1,4})\s*$`
   This single pair accepts leading-number (UOB), trailing-number (OCBC/DBS), and
   dot-leader (Pillar 3) TOCs — **no per-bank / per-format branch.**

`1` if such a page exists, else `0`. **Why both conditions (rejected alternative):**
a purely structural "cluster of ascending page-refs" test with NO label guard
false-positives on short docs whose data pages carry incidental small ascending
integers — validated: it wrongly flagged the 4–9pg highlights (a "contents page"
on p7 of a 7pg doc). The label guard + format-agnostic entries together catch every
substantial FS and every Pillar 3 contents page while correctly rejecting the
label-less short docs.

This is a **hint for the FS pipeline (next cycle), not a hard branch.** The FS
pipeline gets its section TOC from a single narrow Gemini call (titles + page
numbers only — NOT table assignment, which stays deterministic per the 2026-07-09
legacy lesson). `has_contents_page` + `contents_page_number` tell that call where
to look: `1` → point Gemini at that page (one cheap page, and its section count /
page numbers can be verified against Gemini's output to catch a dropped section);
`0` → Gemini infers the TOC from headings across the doc. Letting Gemini read the
contents page (any layout) is deliberately chosen over a deterministic multi-format
parser — it is format-agnostic and avoids the per-format tuning that would be a mild
overfitting risk. Kept separate from the name-based `fs_subtype` (advisory).

### confidence + flags — cross-check

Derive a **filename family hint** (casefold the stem): `"pillar 3"`/`"pillar3"` →
pillar3; `"presentation"`/`"cfo"`/`"deck"` → slides; `"financial"`/`"results"`/
`"highlights"`/`"performance"`/`"trading"`/`"press"`/`"media"`/`"condensed"`/
`"interim"` → fs; else none.

If a filename hint exists and disagrees with the content-decided `family` →
append flag `family_mismatch`. The row is still emitted (content wins); the flag
makes the disagreement visible in the manifest for a human to adjudicate — never a
silent misroute.

## Error handling (fail loudly, repo convention)

- Unreadable / corrupt PDF (open or page-1 extract raises) → row with
  `family="ERROR"`, `flags="unreadable: <exception summary>"`, other fields best
  effort/blank. The file is **never silently dropped** from the manifest.
- `build_manifest` emits one row per input path unconditionally (including ERROR
  rows), so the manifest row-count always equals the input file-count.

## Testing (plain `check()` scripts, no pytest)

`findociq/pipeline/classify/test_family.py`, run as `python3 test_family.py`,
exit 0/1, using the **real corpus as the oracle**:
- Every file under `data/sources/pillar3/` → `family="pillar3"`.
- Every file under `data/sources/presentations/` → `family="slides"`.
- Every file under `data/sources/financial_statements/**` → `family="fs"`, with
  `fs_subtype="highlights"` for the trading-update / performance / highlights /
  press / media files and `fs_subtype="full"` for the condensed / interim /
  unaudited statements.
- `institution` and `period` parse correctly across all filename variants
  (spaces, hyphens, underscores, mixed case), including
  `UOB_2Q25_Condensed Interim Financial Statement.pdf` (spaces) and
  `UOB_4Q25_condensed-financial-statements.pdf` (hyphens).
- `has_contents_page=1` for the 6 substantial FS docs (DBS/OCBC/UOB condensed and
  DBS performance_summary) and `=0` for the 3 short docs (trading_update, results
  press release, performance highlights) — the load-bearing FS signal, and it must
  catch BOTH the `Title <pagenum>` (DBS/OCBC) and `<pagenum> Title` (UOB) formats.
- The 4 unreadable files (empty-content cloud stubs) → `family="ERROR"` rows,
  never dropped (manifest row-count == input file-count).
- At least one deliberately mis-hinted case asserts `family_mismatch` is flagged
  rather than mis-routed (e.g. a pillar3 PDF temporarily hinted as fs by name).

## Generalization (no overfitting — CLAUDE.md)

The corpus is the **test oracle, not the source of the rules.** Every rule keys on a
general property of the document class, so it holds for a bank / quarter / format we
have never seen:

- **family=pillar3** — the regulatory document's own name (`"pillar 3"`), universal
  to that disclosure type; not a bank string.
- **family=slides** — page geometry (aspect ratio + word density), a property of
  presentation decks in general.
- **family=fs** — the DEFAULT for financial documents; corroboration keywords only
  raise `confidence`, they are never required, so an unseen FS naming still lands in
  `fs`.
- **has_contents_page** — a general contents-*label* + *format-agnostic* entry lines
  (leading-#, trailing-#, or dot-leader), NOT the specific formats this corpus used.
- **fs_subtype** — advisory only; its keyword list is an extensible seed and the
  pipeline never branches on it.
- **institution** — a metadata REGISTRY dict; onboarding a new bank is a one-line
  entry, never a code branch (permitted under the no-per-bank-*behavior* rule).

No rule contains a per-document or `if OCBC/DBS/UOB` behavioral branch. The keyword
lists (fs corroboration, contents labels) are general-language seeds, extended by
adding words — not by adding document-specific conditionals.

## Corpus validation (2026-07-12)

Validated against the real 13-file FS corpus before finalizing:

- **All 6 substantial FS docs have a printed contents page** — DBS p3, OCBC p3
  (`Title <pagenum>`), UOB p4 (`<pagenum> Title`). **None use dot leaders** (unlike
  Pillar 3). So `has_contents_page` is a clean signal and the deterministic
  section-map path applies to the FS majority — not the Gemini-heavy path an
  earlier reading assumed.
- **The 3 short docs** (DBS trading_update 7pg, OCBC Results Press Release 9pg,
  UOB Performance Highlights 4pg) have no contents page → Gemini heading branch.
- **DBS naming caveat:** `DBS_4Q25_performance_summary` (46pg, full financials)
  has a contents page on p3 but no "financial statements"/"condensed" keyword →
  `fs_subtype=highlights` (wrong) yet `has_contents_page=1` (right). Confirms
  `fs_subtype` must stay advisory and the pipeline must branch on
  `has_contents_page`.
- **4 files are unreadable** (nonzero size, empty bytes — cloud-sync placeholders
  or truncated downloads): DBS 1Q trading_update, OCBC 1Q Results Press Release,
  OCBC Media Release ×2. Both pdfminer AND pypdfium2 fail → genuine ERROR rows.
  One OCBC file is also misfiled in the wrong quarter folder — content/filename
  routing is unaffected, which validates folder-distrust.

## Constraints (inherited, non-negotiable)

- NO git commits (owner batches manually).
- No per-bank / per-doc conditionals in *behavior*; institution/period is a
  metadata registry only.
- Tests = plain `check(name, cond, got)` scripts, no pytest.
- Fail loudly: unreadable input → ERROR row, never a skip.
- The manifest is the visible decision-tree artifact (CLAUDE.md visibility rule).

## Deliverables

`findociq/pipeline/classify/`: `family.py`, `test_family.py`, `out/manifest.csv`
(generated). This design doc under `findociq/docs/specs/`.

## Extension (2026-07-16): `fs_preferred` batch tie-break

**Pivot: new manifest column `fs_preferred`, derived as a `build_manifest`
BATCH post-process (not a `classify_doc` per-file rule).**

### Motivation

Multiple `family="fs"` documents legitimately exist for the same
institution+period — e.g. OCBC issues both a "Results Highlights" and a
"Results Press Release" for the same quarter, and both correctly classify as
`fs` under the existing per-field rules (both are financial documents; neither
is wrong). Nothing in the original spec says which of the two a downstream
consumer that wants "the one FS doc per quarter" should pick. Per CLAUDE.md,
that decision must be a general, content-based rule — not a per-bank
conditional — and it must fail loud (empty + flagged) rather than guess when
the signal is not decisive.

### Rule

After `classify_doc` has been mapped over every path (`build_manifest`'s
existing step), group all rows with `family="fs"` by `(institution, period)`:

- **Group size < 2** (zero would never occur — a row only enters a group by
  being `fs`; "1" is the common case) → `fs_preferred=""` on that row, no flag.
  There is no tie to break.
- **Group size ≥ 2** — for each row, casefold `Path(row["path"]).stem`, collapse
  `_`/`-` separators to spaces (real filenames spell multi-word signals with
  underscores, e.g. `Results_Press_Release`, not literal spaces), and test for
  any of: `press release`, `media release`, `financial highlights`,
  `performance summary`, `trading update`, `performance highlights`,
  `condensed`, `interim`, `unaudited`.
  - **Exactly one** row in the group matches → that row gets `fs_preferred=1`,
    every other row in the group gets `fs_preferred=0`.
  - **Zero or more than one** row matches → `fs_preferred=""` on every row in
    the group + flag `fs_tiebreak_ambiguous` on every row in the group. Never
    guess; a human adjudicates via the manifest, same fail-loud philosophy as
    `family_mismatch` and the `ERROR` rows.

### Placement

Implemented in `findociq/pipeline/classify/family.py` as
`_apply_fs_preferred_tiebreak(rows)`, called from `build_manifest` after the
`classify_doc` map — never inside `classify_doc` itself, since the decision is
inherently cross-document (a single file's row cannot know about its siblings
until the whole batch has been classified). `fs_preferred` is added to the
manifest schema as a new column between `confidence` and `flags`.

### Generalization

The preferred-token list is a general "this is the canonical announcement of
results" vocabulary (press/media release, official highlights/summary naming,
condensed/interim/unaudited statement naming) — not a bank name, and not tied
to DBS/OCBC/UOB specifically. A bank we have never seen that ships two fs
documents per quarter, one named with one of these tokens and one without,
resolves the same way. If neither or both filenames in a future bank's pair
carry one of these tokens, the rule correctly refuses to guess and flags
`fs_tiebreak_ambiguous` instead of picking arbitrarily.

### Testing

`test_family.py`'s `synthetic_fixture_tests` covers:
- a resolvable pair (`OCBC_..._Results_Press_Release.pdf` vs
  `OCBC_..._Results_Highlights.pdf`, same institution+period) →
  `fs_preferred=1`/`0`, no ambiguous flag;
- an ambiguous pair (`UOB_..._Condensed_Financial_Statements.pdf` vs
  `UOB_..._Unaudited_Financial_Highlights.pdf`, both match a preferred token) →
  `fs_preferred=""` on both + `fs_tiebreak_ambiguous`;
- two solo fs docs (only one `fs` row for their institution+period) →
  `fs_preferred=""`, no flag (no tie to break).

---

## 2026-07-24 — shipped: keep-all ingest, `other` family, period-parser reach

Pivots made while wiring the IR scraper (`ingest/scrape_bank_ir.py`) to feed the
router. All are general (no per-bank branch); called out to the user as pipeline
pivots.

- **No fs tie-break (keep-all).** The `fs_preferred`/`build_manifest` design
  above is NOT implemented. `build_manifest` classifies a batch and returns rows
  unchanged; every fs-family doc a bank publishes in a quarter is retained.
  Overlapping figures are reconciled downstream by `build_fact_metric`'s conflict
  resolution (`resolved_by` = single/twin_collapse/prefer_table/conflict), not by
  dropping documents at ingest. `classify_doc` is an alias for `classify`.

- **New family `other`.** `detect_family` now REQUIRES a financial-statement
  keyword on page 1 for `family=fs`. A portrait, text-dense doc with no FS
  vocabulary (bond pricing, offer letter, M&A media release, redemption notice)
  is `other` + flag `no_fs_signal`, not the old `fs`/`weak_fs_signal`. Ingest
  discards `slides`/`other`/`ERROR`. Observable in the scraper's per-bank summary
  (`discarded (other)`). Residual: M&A "media release" notices still corroborate
  as `fs` (the keyword is legitimate for real results releases) but carry no
  period, so the downstream period-gate excludes them.

- **Period parser reach.** `period_from_stem` now also parses half-year
  (`1H25`→Q2, `2H25`→Q4) and hyphenated/full-year forms (`-1q-2025`, UOB naming),
  in addition to `4Q25`/embedded/`FY25`.

- **Scraper `--periods` scope.** `scrape_bank_ir.py --periods 2025,2026-Q1`
  prunes URLs before download by period token → year-in-path → keep-if-unknown,
  turning a full-archive crawl into a targeted fetch.

Not solved: OCBC Pillar 3 is not discoverable from OCBC's IR landing pages
(0 kept); needs a separate source.

## 2026-07-26 — shipped: title-line `transcript` signal, routed to `other`

**Bug:** earnings-call transcripts (`*_analyst_transcript.pdf`,
`*_media_transcript.pdf`) were mis-classified `family=fs`. Their Q&A body
quotes management repeating "results", "performance", "highlights" — the
existing `fs` corroboration vocabulary (step 3 of the precedence order above)
— so they passed corroboration and reached the Gemini TOC stage as if they
were a real statement, burning an API call on a document with zero tables to
extract. Worse, `run_one()` did not check family at all before `step0_scan`;
only the scraper's keep/discard filter looked at family, so a transcript
already sitting on disk (e.g. dropped in manually, or kept by an earlier
scraper run before this fix) would silently spend Gemini calls every time the
pipeline was pointed at it.

**Signal:** a new precedence step, inserted between pillar3 and slides:

2. **transcript → `other`** — the first non-blank line of page-1 text
   (casefolded) contains the word `"transcript"` (`_TRANSCRIPT_TITLE =
   re.compile(r"\btranscript\b", re.IGNORECASE)`). Earnings-call transcripts
   universally self-identify in their own title line (e.g. "Edited Transcript
   of DBS First-Quarter ... Analyst Call"), regardless of issuer — this is a
   content/document-type signal, not a per-bank hack, so it holds for a bank
   we have never seen. `confidence=high`, flag `transcript`. Renumbers the old
   steps 2 (slides) and 3 (fs) to 3 and 4.

The check must fire **before** the slides and fs checks, precisely because a
transcript would otherwise fall through to `fs` on corroboration alone (it is
portrait and text-dense, so it never gets a chance at the `slides` geometry
check either) — title-line intent beats body-vocabulary corroboration.

**`run_doc.py` wiring:** `run_one()` now calls `classify_family()` and, if the
result is `"other"` or `"slides"`, prints a `[skip]` line and returns 0
**before** `step0_scan` — the earliest point in the driver, so no Gemini call
of any kind is made for a document with nothing to extract. Previously the
only family-aware gate was in the scraper's keep/discard step, which does not
protect a document that reaches `run_doc.py` by any other path (manual drop,
re-run, backfill).

### Testing

`test_family.py::test_family_transcript_routed_to_other_despite_fs_vocabulary`
asserts a transcript-titled fixture routes to `family="other"` even when its
body is seeded with `fs` corroboration vocabulary, confirming the title-line
check's precedence over corroboration.
