# PIPELINE_DECISIONS — control-flow audit, document-in to registry-match

Date: 2026-07-31 · **Read-only audit. No code, map, or DB changes.**
Scope: `run_doc.py` STEP 0–6 plus the mapping layer.

**Headline: the 0-table documents are located.** They are not a PaddleOCR
confidence problem — there is no confidence gate anywhere in the pipeline. They
are D9 + D11 + D14, three independent fail-open gates in a row. See §C.

---

## A. Stage diagram

```
PDF
 │
 ├─ STEP 0  scan            candidates.py   PP-DocLayout-L on 200-DPI PNG
 │                          ├─ D1  label=='table' / 'paragraph_title'   [NO SCORE GATE]
 │                          ├─ D2  typographic fallback (1.3x body size)
 │                          ├─ D3  running-header filter (>=3 pages)
 │                          ├─ D4  twin merge (difflib >= 0.85)
 │                          └─ D5  nested-wrapper dedup (containment >= 0.90)
 │                                 -> candidates.csv, regions.csv  (MAY BE EMPTY)
 │
 ├─ STEP 1  TOC             classify.family -> 'pillar3' | 'fs' | 'slides'
 │                          ├─ D6  family route (pillar3 = deterministic, fs = Gemini)
 │                          └─ D7  classifier exception -> defaults to 'fs'
 │                                 -> section rows
 │
 ├─ (route/scan.py)         page classifier — pdfplumber, NOT OCR
 │                          ├─ D8  COV_HI/COV_LO/B_MIN -> BORDERED/BORDERLESS/MIXED_REVIEW
 │
 ├─ STEP 2  extraction      PASS2_v2 -> pass2/extract.py -> Gemini
 │                          ├─ D9   _reasonable(ext)          [FAILS OPEN ON EMPTY]
 │                          ├─ D10  transport-error retry (3x backoff)
 │                          ├─ D11  page_has_table_structure  [>=5 ruling lines]
 │                          ├─ D12  group exception -> {} and continue
 │                          └─ D13  batch vs sync path
 │
 ├─ (PASS2 distribution)    ├─ D14  `if not tables: continue`  [SILENT DROP]
 │                          └─ D15  duplicate-table warnings (never fatal)
 │
 ├─ STEP 2b geometry        pass2/geometry.py  (side-car, never fails a run)
 │                          ├─ D16  line clustering  0.55 x modal char size
 │                          ├─ D17  superscript test (size -0.5 AND top -0.15)
 │                          ├─ D18  indent cluster   0.5 x body char size
 │                          └─ D19  all-or-nothing gate: all_rows_matched
 │
 ├─ STEP 3  load            pass2/load_v7.py
 │                          ├─ D20  period grammar (regex vocabulary)
 │                          ├─ D21  axis-exclusivity (unambiguous_count >= 2)
 │                          ├─ D22  row_parents_by_position (skip_terminal)
 │                          └─ D23  cell_state classification ('zero'/'nm'/...)
 │
 ├─ STEP 4  concepts        concept/run.py + build_fact_metric
 │                          ├─ D24  alias lookup, scoped beats wildcard
 │                          ├─ D25  row_hierarchy >= 1 candidate filter
 │                          └─ D26  conflict resolution across candidates
 │
 ├─ STEP 5  verify          verify_cells.py + re-extract loop
 │                          ├─ D27  norm_token parsing
 │                          ├─ D28  anchor overlap >= 0.6
 │                          └─ D29  <=2 re-extract rounds, then exit(1)
 │
 └─ mapping layer           registry match + anchor match
                            ├─ D30  composite -> section -> title lookup
                            ├─ D31  safe_clean subsequence guard
                            └─ D32  parent-qualified anchor match
```

---

## B. Decision inventory

| id | stage | criterion (verbatim, file:line) | class | failure behavior | what breaks it |
|---|---|---|---|---|---|
| **D1** | scan | `[b for b in blocks if b["label"] == "table"]` — `candidates.py:417`. **No score is read anywhere in the file** (grep for `score`/`thresh`/`confidence` returns nothing). | MODEL-CONFIDENCE | **SILENT** — zero tables → empty `regions.csv` with header only; `emit_candidates` returns a summary, rc=0 | any page the layout model doesn't label `table` |
| D2 | scan | `_TYPO_FACTOR = 1.3` x body size, `_TYPO_MAX_LEN = 90` — `candidates.py:91-92` | HEURISTIC | SILENT | headings at <1.3x body size |
| D3 | scan | `_RUNHDR_MIN_PAGES = 3` — `candidates.py:94` | HEURISTIC | SILENT | a real heading repeated on ≥3 pages is dropped |
| D4 | scan | `difflib ratio >= _TWIN_RATIO_MIN = 0.85`, `_TWIN_Y_TOL = 3.0` — `candidates.py:97-98` | HEURISTIC | SILENT | two *distinct* headings ≥85% similar merge |
| D5 | scan | `dedup_nested_regions(containment = 0.90)` — `candidates.py:323` | HEURISTIC | SILENT | genuinely nested tables collapse |
| D6 | STEP 1 | `family == "pillar3"` → deterministic `pass1_toc`; else Gemini `toc_stage` — `run_doc.py:389` | DETERMINISTIC | LOUD (`sys.exit`) | — |
| **D7** | STEP 1 | `except Exception … return {}` then `.get("family") or "fs"` — `run_doc.py:373-382` | DETERMINISTIC | **SILENT** — a crashed classifier silently routes to the Gemini FS path | classifier import/runtime error |
| D8 | route | `COV_HI=0.80`, `COV_LO=0.50`, `B_MIN=MIN_DATA_ROWS=3`, `B_EDGE_FALLBACK_MIN=8`, `ALIGN_TOL=3.0` — `route/scan.py:48-55` | HEURISTIC | FALLBACK → `MIXED_REVIEW` | the 0.50–0.80 dead zone |
| **D9** | STEP 2 | `if not ext.tables: return True` — `pass2/extract.py:400-401` | HEURISTIC | **SILENT, FAILS OPEN** | **an empty extraction is declared "reasonable"** |
| D10 | STEP 2 | `for attempt in range(3)` … `wait = 15 * (2**attempt) + uniform(0,5)` — `extract.py:638-647` | DETERMINISTIC | LOUD (re-raises `last_err`) | non-transport errors bypass retry entirely |
| **D11** | STEP 2 | `min_h_edges: int = 5`, edges `> page_w * 0.10` — `pass2/render.py:99-115` | HEURISTIC | **SILENT** — False suppresses the image retry | **borderless tables (0 ruling lines)** |
| D12 | STEP 2 | `except Exception … group_results[gnum] = {}` — `PASS2_v2.py:460-462` | — | **SILENT** — a whole failed group becomes an empty dict, run continues | any per-group exception |
| D13 | STEP 2 | batch vs sync; `chunk_size` default 2 pages — `PASS2_v2.py:440` | DETERMINISTIC | LOUD on batch failure | — |
| **D14** | STEP 2 | `if not tables: print("· no tables, no tab"); idx = [...]; continue` — `PASS2_v2.py:497-500` **and again 505-508** | DETERMINISTIC | **SILENT** — section dropped from the index, `·` at info level, **exit code 0** | any section with no tables |
| D15 | STEP 2 | `validate_exactly_once` / `flag_duplicate_tables` → `⚠` prints — `PASS2_v2.py:464-472` | DETERMINISTIC | **SILENT** (warn-only, never fatal) | duplicate tables load anyway |
| D16 | geometry | `LINE_TOL_FACTOR = 0.55` x modal char size — `geometry.py:71` | HEURISTIC | FALLBACK → model levels | mixed font sizes in one table |
| D17 | geometry | `SUPERSCRIPT_SIZE_DELTA = 0.5` **AND** `SUPERSCRIPT_TOP_DELTA = 0.15` — `geometry.py:72-73` | HEURISTIC | FALLBACK | footnote markers at normal size |
| D18 | geometry | `INDENT_CLUSTER_FACTOR = 0.5` x body size — `geometry.py:74` | HEURISTIC | FALLBACK | shallow indentation |
| D19 | geometry | all-or-nothing: `all_rows_matched` + row-count agreement + every indent present | DETERMINISTIC | FALLBACK → model levels, per table | one miss on a repeated label strands the rest (measured: OCBC 4/41) |
| D20 | load | period grammar regexes; `span = "1H" if mon <= 6 else "2H"` — `load_v7.py:200` | HEURISTIC | FALLBACK → doc period; warning emitted | an unseen header form → NULL `col_period` |
| D21 | load | `candidates = [a for a in mm if unambiguous_count[a] >= 2]` — `load_v7.py:584` | HEURISTIC | FALLBACK + warning `ambiguous axis label` | a label appearing on <2 unambiguous rows |
| D22 | load | `skip_terminal` — totals/notes skipped as parent candidates — `load_v7.py:595-625` | DETERMINISTIC | — | disabled on the geometry branch by design |
| D23 | load | `'0' -> ('zero', 0.0)`; `except ValueError` — `load_v7.py:443-462` | DETERMINISTIC | FALLBACK to a state label | a currency-prefixed value (`S$1.63`) |
| D24 | concepts | scoped alias beats wildcard `'*'` — `resolve_deterministic.py:43-46` | DETERMINISTIC | SILENT (no match → NULL) | wildcard alias over-reach |
| **D25** | concepts | global `row_hierarchy >= 1` candidate filter in `build_fact_metric` | HEURISTIC | **SILENT** — hierarchy-0 rows never enter `fact_metric` | top-level ratio rows (UOB/OCBC ROA/ROE) |
| D26 | concepts | conflict resolution across candidates sharing a key | HEURISTIC | SILENT — picks one | Group vs Bank columns (pre-legal-entity) |
| D27 | verify | `norm_token` — commas, footnotes, `%`, parens | DETERMINISTIC | LOUD (`fail`) | **currency prefixes** (`S$2.1b`) — 29 cells |
| D28 | verify | `overlap_ok = matched / len(first6) >= 0.6` — `verify_cells.py:210` | HEURISTIC | FALLBACK → `page` tier | reworded row labels |
| D29 | verify | `max_rounds: int = 2` then `sys.exit(1)` — `run_doc.py:536,562` | DETERMINISTIC | **LOUD** — the one hard gate in the pipeline | aborts before xlsx/sync_bq |
| D30 | mapping | composite → section → title; miss → UNCLASSIFIED | DETERMINISTIC | LOUD-by-design (queued, never guessed) | unseeded alias |
| D31 | mapping | `safe_clean` subsequence guard — `normalize.py` | DETERMINISTIC | FALLBACK → verbatim label | — |
| D32 | mapping | exact `(bank, table_type_id, row_label_norm, parent_label_norm)` | DETERMINISTIC | LOUD-by-design | wrong parent chain |

---

## C. Fragility ranking — and where the 0-table documents live

Ranked by (silent failure × likelihood on our corpus).

### 1. **D9 + D11 + D14 — THE 0-TABLE CAUSE.** Silent, certain, compounding.

Three fail-open gates in series, each individually defensible, jointly invisible:

```
D9   extract.py:400   if not ext.tables: return True     # empty == "reasonable"
D11  render.py:104    min_h_edges = 5                    # borderless -> no retry
D14  PASS2_v2.py:497  if not tables: … continue          # silent drop, exit 0
```

**Empirically confirmed.** `page_has_table_structure` needs ≥5 horizontal rules
longer than 10% of page width. Measured h-edge counts:

| document | p1 | p2 | p3 | p4 | p5 | p6 | p7 |
|---|---|---|---|---|---|---|---|
| DBS_1Q26_trading_update | 2 ✗ | 0 ✗ | 0 ✗ | 0 ✗ | 0 ✗ | 12 ✓ | 10 ✓ |
| 1Q23_trading_update | 2 ✗ | 0 ✗ | 0 ✗ | 0 ✗ | 14 ✓ | — | — |
| UOB_4Q25_condensed | 2 ✗ | 28 ✓ | 0 ✗ | 1 ✗ | 26 ✓ | 8 ✓ | 11 ✓ |

DBS trading updates are **borderless** — the exact layout `route/scan.py` builds
its whole `bscore` machinery for. On a borderless page D11 returns False, so the
image retry that would rescue a thin extraction is *suppressed*. If Gemini
returns `tables: []`, D9 calls it reasonable, D14 drops the section with a `·`,
and PASS2 exits 0. `run_doc` STEP 2 checks only `rc != 0`.

**Corroborating evidence that this is extraction, not scan:** DBS_1Q22 and
DBS_3Q22 both have `regions.csv` present and **sections loaded** (6 and 11
respectively) — TOC succeeded — yet 0 tables. And 1Q23 has the *same* 1 region
as 1Q22/3Q22 but produced 4 tables. Region count is not the discriminator.

### 2. D12 — a whole extraction group can fail and become `{}`. Silent, moderate likelihood.

### 3. D25 — `row_hierarchy >= 1` silently excludes every top-level row from `fact_metric`. Silent, **already biting** (UOB/OCBC ROA/ROE absent).

### 4. D1 — no confidence gate at all. Currently *safer* than a badly-tuned one, but it means region quality is entirely unmeasured, and there is no signal to tune.

### 5. D19 — geometry all-or-nothing. FALLBACK (not silent — `hierarchy_source` records it), but measured coverage is DBS 4/4, UOB 28/44, OCBC 4/41, and it is the root of 46% of key-field misses (RC1).

### 6. D20 / D23 — period grammar and value parsing. FALLBACK + warning; the `S$` prefix case is already documented (29 cells).

### 7. D7 — a crashed family classifier silently routes Pillar 3 documents down the Gemini FS path.

---

## D. Answers to your specific questions

**What does PaddleOCR feed the section mapper, and what on zero regions?**
`candidates.py` runs **PP-DocLayout-L** (a layout model, not OCR) on a 200-DPI
PNG and emits two CSVs: `candidates.csv` (heading candidates) and `regions.csv`
(table bboxes). Zero regions → a header-only `regions.csv`; `run_doc.step0_scan`
tests only `regions.exists()` (`run_doc.py:345,356`), so an empty file passes as
success. Zero on all pages behaves identically — no assertion anywhere.

**What confidence threshold gates a detected region, who chose it, what's the
score distribution?** **There is none.** `candidates.py:411-418` reads only
`b["label"]` and `b["coordinate"]`; the model's score field is never read. So no
score distribution exists to compare 1Q/3Q against 2H/FY — **your hypothesis
that interim docs cluster under a cutoff cannot be true, because there is no
cutoff.** What *does* differ sharply is region count: interim trading updates
yield 1–2 regions, performance summaries 30–39. That is a real layout
difference, not a scoring artifact.

**How is the section map built? What breaks on a slimmer interim layout?**
Headings from the layout model's `paragraph_title` boxes (D1), plus a
typographic fallback at 1.3× body size (D2), minus running headers (D3), minus
difflib-0.85 twins (D4). Text always comes from the **native pdfplumber layer**,
never from OCR. A slimmer interim layout has fewer/no `paragraph_title` boxes,
so the section map leans harder on D2's font-size heuristic.

**OCR text vs native text layer — where chosen?** Nowhere; there is no choice
point. Layout comes from the image, **all text comes from the native PDF layer**
(`words_from_chars(page)`, `text_in(...)`, and pdfplumber throughout
`route/scan.py`, `geometry.py`, `verify_cells.py`). We are not OCRing documents
that have text layers, and we are not relying on text layers that don't exist —
but there is also **no fallback if a text layer is ever absent**, which would
fail silently as empty text.

**Any decision made on VALUES rather than structure?** Three, all flagged:
- **D9** — the "reasonableness" of an extraction is judged on value presence
  (`any(r.values for r in t.rows)`), not on structure.
- **D28** — verify anchors on token overlap ≥ 0.6 of the first 6 tokens.
- **D26** — conflict resolution picks among candidate *values*.
D9 is the one that matters: it is the value-matching antipattern at the
extraction gate, and it is exactly what lets an empty result pass.

**Which decision points would the footnote / raw-vs-normalized schema change
touch?** D17 (superscript detection moves from geometry inference to extracted
metadata), D23 (value parse becomes validate-not-sanitise), D31 (`safe_clean`
becomes unnecessary once `raw` and `label` are separate fields), D32 (anchors
match `label`, so footnote drift stops breaking them — this is RC5), and D9
(a schema with explicit `tables: []` plus a reason is what makes empty
distinguishable from failed).

---

## E. Recommendations, ranked

| # | decision | recommendation |
|---|---|---|
| **R1** | **D9** | Make empty non-reasonable: `if not ext.tables: return False`. An empty extraction on a page the router called BORDERLESS/BORDERED must retry with the image. This one line is the highest-value change in the audit. |
| **R2** | **D14** | Count dropped sections; if a document ends with **0 tables across all sections**, raise. Emitting a doc with no tables and rc=0 is the failure mode that produced DBS_1Q22/3Q22. |
| **R3** | **D11** | Don't gate the retry on ruling lines alone. `route/scan.py` already computes `bscore` for exactly this — a borderless table scores high there and zero on `min_h_edges`. Feed the router's classification in, or drop the gate and accept the retry cost. |
| **R4** | **D1** | Read and persist the layout model's score per region even if nothing gates on it yet. Without it there is no data to tune any future threshold, and no way to answer "was this page detected well?". |
| R5 | D25 | Remove or justify the `row_hierarchy >= 1` filter — it silently excludes top-level ratio rows and is already costing UOB/OCBC ROA/ROE. |
| R6 | D12 | A group exception should mark the document degraded, not silently yield `{}`. |
| R7 | D19 | Scan re-anchoring (already queued) — 46% of key-field misses trace here. |
| R8 | D23/D27 | Add currency-prefix handling to `norm_token`, and make value parse failures explicit rather than state-labelled. |
| R9 | D7 | A classifier crash should be loud; defaulting Pillar 3 into the FS path is worse than stopping. |
| R10 | D15 | Duplicate-table warnings should gate the load, or be recorded in `ingest_status`, not just printed. |

**R1–R3 together close the 0-table hole.** R1 alone would likely have prevented
it; R2 makes it impossible to ship silently; R3 removes the borderless blind
spot that suppressed the rescue path.

---

## F. Audit coverage caveat

Fully read: `run_doc.py`, `candidates.py`, `route/scan.py`, `PASS2_v2.py`,
`pass2/extract.py`, `pass2/render.py`, `pass2/geometry.py` (constants),
`mapping/*`. Read by targeted grep only — decision points may be incomplete:
`pass2/load_v7.py` (1,633 lines — D20–D23 sampled), `pass2/transforms.py`
(1,267), `discover/pass1_toc.py` (812), `toc/toc_stage.py` (770),
`concept/build_fact_metric.py` (D25/D26 from prior sessions' evidence, not a
full read). D24–D26 in particular deserve a dedicated pass.
