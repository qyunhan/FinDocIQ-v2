# PaddleOCR Stage-2 Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer, with scored evidence, whether PP-StructureV3 (geometry-based table structure) can replace/reduce Gemini in Stage-2 extraction per table class (ruled/borderless), and whether Paddle-sourced titles can build a document TOC.

**Architecture:** PDF pages → 200-DPI PNG → PP-StructureV3 (isolated `.venv-paddle`) → persisted markdown+JSON → dialect adapter + continuation stitcher (`md_tables.py`) → REUSED `html_to_cells.py` parser → pdfplumber geometry overlay (indent levels + shade fills) → `flatten.py` to `v_cell_flat` shape → three gates scored against user-provided GT CSVs and printed-TOC JSON → `paddle_eval.db` (schema v7) + Excel verification views + `scorecard.md` + finding doc.

**Tech Stack:** paddlepaddle 3.x (CPU, arm64) + paddleocr 3.x in `.venv-paddle`; everything else runs in **base `python3`** (verified to have `lxml`, `pdfplumber`, `openpyxl`); SQLite (schema_v7); plain `check()`-style test scripts.

**Binding spec:** `findociq/docs/specs/2026-07-07-paddleocr-stage2-spike-design.md`. Where this plan adds detail, the spec wins on conflict.

## Global Constraints

- **NO git commits** — the owner batches commits manually. Never run `git commit`. (Overrides any skill default that says to commit.)
- **Never touch `final.db`.** The spike DB is `findociq/experiments/2026-07-07_paddleocr_eval/paddle_eval.db` only.
- **Zero Gemini / LLM tokens anywhere.**
- **No per-bank/per-doc conditionals** in any logic. Doc *metadata* (paths, institution strings) lives in one registry dict; *behavior* must be dialect-general.
- **Tests are plain scripts**: `def check(name, cond, got=None)` printing `✓/✗`, run via `python3 test_x.py`, exit code 0/1. **No pytest.**
- **Fail loudly**: empty PP-Structure output → exception. A page with no detected table where GT expects one is scored as STRUCTURE failure by the scorer (run_paddle warns but does not raise — OCBC p94 is a legitimate prose page inside the NSFR section range).
- All artifacts under `findociq/experiments/2026-07-07_paddleocr_eval/outputs/` — resumable (existing page artifacts skipped unless `--force`), every intermediate inspectable.
- Pinned package versions recorded in `outputs/pins.txt` and quoted in the finding doc.
- All commands run from the repo root `/Users/Qianyunhan/Desktop/FinancialParser`.

## Established facts (already probed — do NOT re-derive, trust these)

- **toc.json ground truth exists for all 12 quarterlies** at `findociq/_legacy/DELIVERABLE/outputs/pillar3/<doc>/toc.json` (spec's "VERIFY at implementation start" — done 2026-07-07). Shape: `{"document":…, "provenance":…, "parts":…, "sections":[{"section_id","part","number","title","page_ref","start_page","end_page"}…], "all_sections":…}`.
- NSFR sections: DBS 4Q23 → `C.1.1 "NSFR Disclosure Template"` pages **75–78**; OCBC 4Q24 → `24 "Net Stable Funding Ratio"` pages **94–96**. (Located via pattern at runtime — never hardcode pages.)
- **DBS layout:** Dec-2023 table spans p75–76 (lines 1–22 / 23–34), Sep-2023 spans p77–78. Every page after the first prints "NSFR Disclosure Template **(continued)**" — including p77 where the *Sep* table starts. So captions canNOT split tables; the split signals are **period text + printed line-number restart**. Fully ruled (35–70 stroke lines/page); grey fills lum ≈ 0.847/0.749 mark shading.
- **OCBC layout:** p94 prose only, p95 = Dec-2024 (all 34 lines), p96 = Sep-2024. Dates printed as full text ("31 December 2024"). **Zero stroke lines** (borderless) but **442 fill rects/page** (lums 0.851 / 0.651 / 0.502 / 1.0) — shading IS detectable from fills on borderless pages too.
- **Indent geometry:** row-label x0 clusters — DBS {74.9, 82.5, 90.1} (steps ~7.6pt), OCBC {104.2–107.1, 115.2–116.0, 131.6–132.8} (jitter ≤ 3pt within a level, gap ≥ 8pt between). Both docs: exactly 3 label indent levels. A 4.0pt cluster-gap threshold splits both cleanly (measured band: ≤3.0 vs ≥7.6).
- **GT CSVs** (repo root): each contains **BOTH quarters** — `GT_dbs_4q23_p3.csv` 288 cells (2023-12-31 + 2023-09-30), `GT_ocbc_4q24_p3.csv` 284 cells (2024-12-31 + 2024-09-30). 34 distinct `line_no` (all non-empty; band rows do NOT appear as GT rows — bands only appear as `row_lvl1` ancestors). 5 leaf columns: group `Unweighted value by residual maturity` → {`No Maturity`, `< 6 months`, `6 months to <1 yr`, `≥ 1yr`} plus `Weighted value` (col_lvl2 = empty, col_depth 1). cell_states: reported/null/empty only. is_shade=1: DBS 12, OCBC 22. colspan>1: DBS 22, OCBC 24. row_depth ∈ {2,3,4} ↔ row_hierarchy ∈ {1,2,3} (depth = hierarchy + 1; the +1 is the band level).
- **GT lineage semantics** (drives flatten): line 1 "Capital:" has `row_lvl1='ASF Item', row_lvl2='Capital:', depth=2, hierarchy=1`. So the cell-less band row ("ASF Item"/"RSF Item") is the lineage ROOT for all following rows; a data row's lineage = [band] + parent-chain(by indent level) + [own label].
- **GT identity strings (verbatim):** institutions `DBS Group Holdings Ltd` / `Oversea-Chinese Banking Corporation Limited`; doc_ids `dbs_4q23_p3` / `ocbc_4q24_p3`; table_ids `nsfr_dec23`, `nsfr_sep23`, `nsfr_dec24`, `nsfr_sep24`; table_title `NSFR Disclosure Template`; GT row_id numbering counts band rows (line 1 → row_id 2 because the ASF band is row_id 1).
- **T4 region-detection facts (added 2026-07-08):** UOB 4Q25 §12.9 = pages **38–41** (`_legacy/DELIVERABLE/outputs/pillar3/uob_4Q25/toc.json`, section_id `12.9`; PDF filename `UOB_4Q25_Pillar 3.pdf` — contains a space); pdfplumber `find_tables()` there = **10/4/10/4 = 28 known-true** (cross-confirmed in `pipeline/route/out/UOB_4Q25_Pillar 3_route.json`, num_cov ≈ 0.97/page). Coverage referee to IMPORT (never reimplement): `findociq/pipeline/route/scan.py` — `NUM` regex (line ~45, anchored full-match numeric test), `_in_bbox(cx, cy, bbox)` (line ~113), `_coverage` (line ~118). The dead MinerU branch = `_mineru_detect` (line ~263) always returns `(None, "pending")`. Route manifests live in `pipeline/route/out/*_route*.json`; page objects carry `route` (class) — NO_TABLE pages: OCBC_4Q24 = {1,4,5,15,16,17,18,91,94,97,98,99,100}, DBS_4Q23 = {1}. No bbox-IoU prior art in the repo — write it fresh in `score_regions.py`.
- **`html_to_cells.parse_html(html) -> list[Table]`** (in `findociq/experiments/2026-06-29_mineru_eval/`, 18/18 tests) expects: optional `<thead>` with leading single-cell context rows (period parsed from them) + header rows honouring colspan/rowspan; `<tbody>` rows; `data-level` attrs optional (level defaults 0); `style="background-color:#…"` → `is_shade`; leading all-numeric column auto-detected as `line_no`. Models: `Table(period, context_rows, cols[Col(col_id,leaf_label,group)], rows[Row(row_idx,level,kind,line_no,label,parent_idx,cells[Cell(col_id,colspan,value_raw,value_num,cell_state,is_shade)])], warnings)`.

## Design decisions (locked; deviations must be reported to the user)

1. **We render pages ourselves** with `pdfplumber page.to_image(resolution=200)` and feed PNGs to PP-StructureV3. Coordinate scale is then exactly `72/200` (image px → PDF pt) — deterministic geometry fusion, no guessing Paddle's internal rasterizer.
2. **Row hierarchy is authored by pdfplumber indent geometry** (overlay), not by Paddle HTML (Paddle emits no `data-level`). This mirrors the spec's own authority split for shading ("geometry authors structure") and the MinerU-era plan ("pdfplumber shading/indent overlay"). Same rule for both docs, adaptive threshold, no per-bank constants.
3. **Stitch rule** (general, template-class-wide): within one section's page sequence, a table fragment starts a NEW table iff its parsed period differs from the current one, OR its first printed line_no does not continue the current table's last line_no. Otherwise it is a continuation: body rows are appended and the repeated header must match the current column signature exactly (hard error otherwise).
4. **Gate-1 comparison columns.** Gated: presence/absence on join key `(period, line_no, col_lvl1, col_lvl2)`, `row_lvl1..5`, `row_depth`, `row_hierarchy`, `col_depth`, `cell_state`, `is_shade`, `colspan`, line_no ordering, `value_raw`/`value_num` (TEXT class). Report-only (assigned identities, not extracted content — never gate): `table_title`, `section_no`, `concept_key`, `geo_key`, `row_header_id`, `col_header_id`, `doc_id`, `table_id`, `row_id`, `col_id`.
5. **Lineage label text rule:** at each lineage position, exact match after normalization (casefold + whitespace collapse) → OK; `difflib.SequenceMatcher.ratio() ≥ 0.9` → TEXT mismatch; else STRUCTURE mismatch. Depth/position differences are always STRUCTURE.
6. **Shade rule is luminance-relative, never a color enum:** within a table's bbox, the modal fill luminance is the base; a cell is shaded iff a grey fill **darker than base** (and not near-black text/rules, lum band 0.3–0.95) covers ≥ 50% of the cell area. Task 4 calibrates the exact band against GT counts and documents the measured values — the rule stays general.

## File structure

```
findociq/experiments/2026-07-07_paddleocr_eval/
  docs_config.py     # doc registry + deterministic NSFR-section lookup from toc.json
  smoke.py           # Task 1: install proof; dumps real dialect facts for Task 3
  run_paddle.py      # Task 2 (.venv-paddle ONLY): render→predict→persist per page
  md_tables.py       # Task 3: raw json/md → adapted HTML + geometry sidecar + stitcher
  overlay.py         # Task 4: pdfplumber indent levels + shade fills onto parsed Tables
  flatten.py         # Task 5: Table → v_cell_flat rows → per-table cells.csv
  score_cells.py     # Task 6: Gate 1 + Gate 2 → outputs/scores/<table_id>.json
  cells_to_xlsx.py   # Task 7: Excel verification views (real merges + fills)
  load_db.py         # Task 8: schema_v7 → paddle_eval.db + v_cell_flat round-trip diff
  score_toc.py       # Task 9: Gate 3 → outputs/scores/toc_<doc_id>.json
  score_regions.py   # Task 11: Gate 4 (T4a/b/c) → outputs/scores/regions.json
  assemble_scorecard.py  # Task 10: scores/*.json → scorecard.md (incl. Gate 4)
  test_md_tables.py / test_overlay.py / test_flatten.py / test_score_cells.py /
  test_load_db.py / test_score_toc.py     # check()-style, no pytest
  outputs/<doc_id>/pages/NNN.{png,json,md}        # T1/T2 captures
  outputs/<doc_id>/pages_full/NNN.{png,json,md}   # T3 captures
  outputs/<doc_id>/tables/<table_id>.html / .geom.json / .cells.csv / .xlsx
  outputs/scores/*.json ; outputs/pins.txt ; paddle_eval.db ; scorecard.md
```

Dependency graph: T1 → T2 → {T3 → T4 → T5 → {T6, T7, T8}} and T2 → T9-runs (launch full-doc runs in the BACKGROUND right after Task 2 — they take ~1–2 h CPU) → T9-scoring → T10. Task 11 (Gate 4): its T4a/T4b scoring needs only Task 2's captures; its T4c scoring additionally needs the finished full-doc captures. Tasks 6, 7, 8, 11 are mutually independent once their inputs exist. Task 10 runs last (consumes all scores).

---

### Task 1: `.venv-paddle` install + smoke run (fail-fast gate)

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/smoke.py`
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/outputs/pins.txt` (generated)

**Interfaces:**
- Consumes: `findociq/data/sources/pillar3/DBS_4Q23_Pillar3.pdf`
- Produces: working `.venv-paddle`; `outputs/smoke/` artifacts (real PP-StructureV3 JSON + markdown for one ruled page) that Task 3 reads to pin the dialect; `outputs/pins.txt`.

- [ ] **Step 1: Create the venv and install pinned packages**

```bash
cd /Users/Qianyunhan/Desktop/FinancialParser
python3.12 -m venv .venv-paddle
.venv-paddle/bin/pip install --upgrade pip
.venv-paddle/bin/pip install "paddlepaddle==3.3.1" "paddleocr==3.7.0" "paddlex==3.7.2" pdfplumber
```

**Pin history (2026-07-08, RESOLVED):** the original pins (`paddlepaddle==3.0.0` +
`paddleocr==3.1.0` + `paddlex==3.1.0`) SIGBUS-crashed 5× in a row loading
`RT-DETR-L_wireless_table_cell_det` weights on this host (macOS arm64) regardless of
memory headroom; the crashing model loaded clean in isolation, so the bump to
`paddlepaddle==3.3.1 / paddleocr==3.7.0 / paddlex==3.7.2` was tried and fixed it —
smoke run green, exit 0, all 6 checks. (paddleocr and paddlex must stay contemporaneous:
mixing paddleocr 3.1.0 with paddlex 3.7.2 raises `TypeError: PaddlePredictorOption...`.)
These ARE the pins; `outputs/pins.txt` reflects them. Dialect facts confirmed on the real
smoke JSON: table entries at `table_res_list[i]` with keys `pred_html` /
`cell_box_list` (flat `[x0,y0,x1,y1]`, image px) / `table_ocr_pred`; pred_html has NO
`<thead>`, HAS `<tbody>` + `colspan=` + `rowspan=`; `save_to_json(save_path=*.json)` and
`save_to_markdown(save_path=*.md)` write to the exact file path given.

- [ ] **Step 2: Record the pins**

```bash
mkdir -p findociq/experiments/2026-07-07_paddleocr_eval/outputs
.venv-paddle/bin/pip freeze | grep -Ei 'paddle|pdfplumber|opencv|numpy' \
  > findociq/experiments/2026-07-07_paddleocr_eval/outputs/pins.txt
cat findociq/experiments/2026-07-07_paddleocr_eval/outputs/pins.txt
```

Expected: lines for `paddleocr`, `paddlepaddle`, `pdfplumber`, `numpy`.

- [ ] **Step 3: Write `smoke.py`**

```python
"""smoke — prove PP-StructureV3 runs on this machine and emits table HTML + cell geometry.

Renders DBS 4Q23 p75 (ruled NSFR page) at 200 DPI, runs PP-StructureV3, persists all
artifacts under outputs/smoke/, and prints the REAL result-JSON shape (keys + the paths
holding table HTML) — Task 3 (md_tables) is written against this captured reality.

Run: .venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/smoke.py
"""
import json, os, sys
import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs", "smoke")
PDF = os.path.join(HERE, "..", "..", "data", "sources", "pillar3", "DBS_4Q23_Pillar3.pdf")


def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)


def find_html_paths(node, path="$"):
    hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and "<table" in v:
                hits.append(path + "." + k)
            hits += find_html_paths(v, path + "." + k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits += find_html_paths(v, f"{path}[{i}]")
    return hits


def main():
    os.makedirs(OUT, exist_ok=True)
    png = os.path.join(OUT, "p75.png")
    with pdfplumber.open(PDF) as pdf:
        pdf.pages[74].to_image(resolution=200).save(png)

    from paddleocr import PPStructureV3
    # Subsystems OFF (2026-07-08): chart/formula segfault on this host and don't apply to
    # financial tables; orientation/unwarping/seal target photographed or scanned docs —
    # ours are digital-native PDFs. Task-scoped capability toggles (documented kwargs),
    # general to the whole document class, and they cut peak model-load memory (the host
    # is swap-constrained). Never disable the table subsystems themselves.
    pipe = PPStructureV3(device="cpu", use_chart_recognition=False,
                         use_formula_recognition=False,
                         use_doc_orientation_classify=False,
                         use_doc_unwarping=False,
                         use_seal_recognition=False)
    results = list(pipe.predict(png))

    ok = check("exactly one page result", len(results) == 1, len(results))
    res = results[0]
    res.save_to_json(save_path=OUT)      # if these treat save_path as a file, adjust to
    res.save_to_markdown(save_path=OUT)  # save_path=os.path.join(OUT, "p75.json") etc.
    jsons = [f for f in os.listdir(OUT) if f.endswith(".json")]
    mds = [f for f in os.listdir(OUT) if f.endswith(".md")]
    ok &= check("json persisted", bool(jsons), os.listdir(OUT))
    ok &= check("markdown persisted", bool(mds), os.listdir(OUT))
    raw = json.load(open(os.path.join(OUT, jsons[0])))
    ok &= check("table html somewhere in json", "<table" in json.dumps(raw))
    md = open(os.path.join(OUT, mds[0])).read()
    ok &= check("markdown embeds <table html", "<table" in md, md[:300])
    ok &= check("cell geometry present", "cell_box" in json.dumps(raw)[:2_000_000]
                or "cell_box_list" in json.dumps(raw))
    print("\n--- top-level json keys:", sorted(raw.keys()) if isinstance(raw, dict) else type(raw))
    print("--- paths holding table html:", find_html_paths(raw)[:10])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the smoke and read its dialect report**

Run: `.venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/smoke.py`
Expected: all `✓`, exit 0, and two printed lines describing the real JSON shape. First run downloads models (~min); subsequent pages are seconds-per-page on CPU.

**Record in your report to the orchestrator:** the top-level keys, the JSON path(s) holding table HTML (expected: something like `…table_res_list[i].pred_html` with sibling `cell_box_list`), whether `save_to_json` wanted a dir or file path, and whether the pred_html contains `<thead>`/`<tbody>` and `colspan=` attributes. Task 3 depends on these five facts.

---

### Task 2: `docs_config.py` + `run_paddle.py`, capture T1+T2 pages

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/docs_config.py`
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/run_paddle.py`

**Interfaces:**
- Consumes: Task 1's venv; toc.json files; the two PDFs.
- Produces: `DOCS: dict[str, dict]` registry (keys `pdf, toc, institution, gt, render`); `nsfr_pages(toc_path) -> list[int]` (1-indexed); per-page artifacts `outputs/<doc_id>/pages/NNN.png|.json|.md` for DBS p75–78 and OCBC p94–96. `DPI = 200` constant importable from `docs_config`.

- [ ] **Step 1: Write `docs_config.py`** (importable from BASE python — no paddle imports here)

```python
"""docs_config — the spike's document registry + deterministic NSFR-section lookup.

Metadata only (paths, verbatim institution strings). All BEHAVIOR elsewhere is
dialect-general; nothing may branch on these keys.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))          # repo root
PDF_DIR = os.path.join(ROOT, "findociq", "data", "sources", "pillar3")
TOC_DIR = os.path.join(ROOT, "findociq", "_legacy", "DELIVERABLE", "outputs", "pillar3")

DPI = 200          # PNG render resolution; px -> pt scale is exactly 72/DPI
PT_PER_PX = 72.0 / DPI

DOCS = {
    "dbs_4q23_p3": dict(
        pdf=os.path.join(PDF_DIR, "DBS_4Q23_Pillar3.pdf"),
        toc=os.path.join(TOC_DIR, "dbs_4Q23", "toc.json"),
        institution="DBS Group Holdings Ltd",
        gt=os.path.join(ROOT, "GT_dbs_4q23_p3.csv"),
        render="ruled",
    ),
    "ocbc_4q24_p3": dict(
        pdf=os.path.join(PDF_DIR, "OCBC_4Q24_Pillar3.pdf"),
        toc=os.path.join(TOC_DIR, "ocbc_4Q24", "toc.json"),
        institution="Oversea-Chinese Banking Corporation Limited",
        gt=os.path.join(ROOT, "GT_ocbc_4q24_p3.csv"),
        render="borderless",
    ),
}

# Region-detection corpus (T4a) — registry-only third doc: no GT cells, no NSFR capture.
EXTRA_DOCS = {
    "uob_4q25_p3": dict(
        pdf=os.path.join(PDF_DIR, "UOB_4Q25_Pillar 3.pdf"),    # filename contains a space
        toc=os.path.join(TOC_DIR, "uob_4Q25", "toc.json"),
        institution="United Overseas Bank Limited",
        gt=None,
        render="ruled",
    ),
}
ALL_DOCS = {**DOCS, **EXTRA_DOCS}

TABLE_TYPE = "nsfr"
TABLE_TITLE = "NSFR Disclosure Template"   # MAS 653 template name (report-only identity)

_NSFR = re.compile(r"nsfr|net stable funding", re.I)


def nsfr_pages(toc_path: str) -> list[int]:
    """1-indexed NSFR-section pages from the printed-TOC output (never hardcoded).
    Deepest (most dotted section_id) match wins; residual ambiguity is a hard error."""
    toc = json.load(open(toc_path))
    hits = [s for s in toc["sections"] if _NSFR.search(s.get("title") or "")]
    if not hits:
        raise ValueError(f"no NSFR section found in {toc_path}")
    depth = lambda s: (s.get("section_id") or "").count(".")
    best = max(depth(s) for s in hits)
    finals = [s for s in hits if depth(s) == best]
    if len(finals) != 1:
        raise ValueError(f"ambiguous NSFR sections in {toc_path}: "
                         f"{[s['section_id'] for s in finals]}")
    s = finals[0]
    return list(range(int(s["start_page"]), int(s["end_page"]) + 1))


def section_pages(toc_path: str, section_id: str) -> list[int]:
    """1-indexed pages of a printed-TOC section, selected by EXACT section_id
    (T4a uses '12.9'). Same deterministic mechanism as nsfr_pages, keyed lookup."""
    toc = json.load(open(toc_path))
    hits = [s for s in toc["sections"] if s.get("section_id") == section_id]
    if len(hits) != 1:
        raise ValueError(f"section_id {section_id!r}: {len(hits)} matches in {toc_path}")
    return list(range(int(hits[0]["start_page"]), int(hits[0]["end_page"]) + 1))
```

- [ ] **Step 2: Sanity-check the lookups from base python**

Run: `python3 -c "import sys; sys.path.insert(0,'findociq/experiments/2026-07-07_paddleocr_eval'); from docs_config import DOCS, EXTRA_DOCS, nsfr_pages, section_pages; print({d: nsfr_pages(c['toc']) for d,c in DOCS.items()}); print(section_pages(EXTRA_DOCS['uob_4q25_p3']['toc'], '12.9'))"`
Expected: `{'dbs_4q23_p3': [75, 76, 77, 78], 'ocbc_4q24_p3': [94, 95, 96]}` then `[38, 39, 40, 41]`

- [ ] **Step 3: Write `run_paddle.py`**

```python
"""run_paddle — PP-StructureV3 over a doc's NSFR pages (default) or the whole doc (--full).

Persists, per page: the 200-DPI PNG the model actually saw, the raw result JSON, and the
markdown. Resumable: pages with an existing .json are skipped unless --force.

RUNS ONLY IN .venv-paddle:
  .venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/run_paddle.py dbs_4q23_p3
  .venv-paddle/bin/python .../run_paddle.py ocbc_4q24_p3 --full     # T3 capture, ~1h CPU
"""
import argparse, os, sys
import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import ALL_DOCS, DPI, nsfr_pages, section_pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id", choices=sorted(ALL_DOCS))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="whole document (T3), not NSFR pages")
    mode.add_argument("--section", help="capture a printed-TOC section by section_id (T4a: 12.9)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    doc = ALL_DOCS[a.doc_id]
    sub = "pages_full" if a.full else (f"pages_sec{a.section}" if a.section else "pages")
    outdir = os.path.join(HERE, "outputs", a.doc_id, sub)
    os.makedirs(outdir, exist_ok=True)

    from paddleocr import PPStructureV3
    # Subsystems OFF (2026-07-08): chart/formula segfault on this host and don't apply to
    # financial tables; orientation/unwarping/seal target photographed or scanned docs —
    # ours are digital-native PDFs. Task-scoped capability toggles (documented kwargs),
    # general to the whole document class, and they cut peak model-load memory (the host
    # is swap-constrained). Never disable the table subsystems themselves.
    pipe = PPStructureV3(device="cpu", use_chart_recognition=False,
                         use_formula_recognition=False,
                         use_doc_orientation_classify=False,
                         use_doc_unwarping=False,
                         use_seal_recognition=False)

    with pdfplumber.open(doc["pdf"]) as pdf:
        pages = (list(range(1, len(pdf.pages) + 1)) if a.full
                 else section_pages(doc["toc"], a.section) if a.section
                 else nsfr_pages(doc["toc"]))
        print(f"[{a.doc_id}] {'FULL' if a.full else 'NSFR'} pages {pages[0]}..{pages[-1]} ({len(pages)})",
              flush=True)
        for pno in pages:
            base = os.path.join(outdir, f"{pno:03d}")
            if os.path.exists(base + ".json") and not a.force:
                print(f"  p{pno}: exists, skip", flush=True); continue
            pdf.pages[pno - 1].to_image(resolution=DPI).save(base + ".png")
            results = list(pipe.predict(base + ".png"))
            if len(results) != 1:
                raise RuntimeError(f"p{pno}: expected 1 page result, got {len(results)}")
            res = results[0]
            # Adjust save calls to the dir/file convention Task 1's smoke run established;
            # the on-disk contract that MUST hold is: <outdir>/NNN.json and <outdir>/NNN.md.
            res.save_to_json(save_path=base + ".json")
            res.save_to_markdown(save_path=base + ".md")
            if not (os.path.exists(base + ".json") and os.path.getsize(base + ".json") > 0):
                raise RuntimeError(f"p{pno}: empty/missing PP-Structure JSON output")
            n = open(base + ".json").read().count("<table")
            print(f"  p{pno}: ok ({n} table html blocks)"
                  + ("" if n or a.full else "  [WARN: no table detected on a targeted page]"),
                  flush=True)
    print("done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Capture T1, T2, and T4a pages (11 pages total)**

Run (sequential, same venv):
```bash
.venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/run_paddle.py dbs_4q23_p3
.venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/run_paddle.py ocbc_4q24_p3
.venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/run_paddle.py uob_4q25_p3 --section 12.9
```
Expected: `ok (≥1 table html blocks)` for p75–78, p95–96, and UOB p38–41; p94 prints the no-table WARN (prose page — correct). Exit 0 all three.

- [ ] **Step 5: Verify artifacts on disk**

Run: `ls findociq/experiments/2026-07-07_paddleocr_eval/outputs/*/pages*/`
Expected: `075..078.{png,json,md}`, `094..096.{png,json,md}`, and under `uob_4q25_p3/pages_sec12.9/` `038..041.{png,json,md}` — all non-empty.

- [ ] **Step 6 (orchestrator, immediately after this task): launch the two T3 full-doc captures in the background** — they only depend on this task and take ~1–2 h CPU total:

```bash
.venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/run_paddle.py dbs_4q23_p3 --full
.venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/run_paddle.py ocbc_4q24_p3 --full
```
(Run sequentially in one background shell — two paddle processes would thrash CPU.)

---

### Task 3: `md_tables.py` — dialect adapter, geometry sidecar, continuation stitcher

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/md_tables.py`
- Test: `findociq/experiments/2026-07-07_paddleocr_eval/test_md_tables.py`

**Interfaces:**
- Consumes: `outputs/<doc_id>/pages/NNN.json|.md` (Task 2); `docs_config.DOCS/DPI/PT_PER_PX`; `html_to_cells.parse_html` from `findociq/experiments/2026-06-29_mineru_eval/` (import via `sys.path`).
- Produces: `build_tables(doc_id: str) -> list[dict]` — writes `outputs/<doc_id>/tables/<table_id>.html` + `<table_id>.geom.json`, returns manifest entries `{"table_id": str, "period": "YYYY-MM-DD", "pages": [int], "html": path, "geom": path}`. Geometry sidecar (all coords in **PDF points**): `{"fragments": [{"page": int, "table_bbox": [x0,y0,x1,y1], "rows": [{"body_index": int, "bbox": […]}], "col_bands": {"1": [x0,x1], …}, "merges": [{"body_index": int, "col_id": int, "colspan": int}]}]}` where `body_index` is the tbody row index in the STITCHED table and `col_id` matches `html_to_cells` grid cols (0 = label col, 1..N = value cols).

**IMPORTANT adapt-point:** the exact JSON paths for table entries (`pred_html`, `cell_box_list`, table bbox, layout blocks) come from Task 1's smoke report + the real captures. The extractor below searches structurally and fails loudly with the actual keys — adjust the two marked constants if the smoke report showed different names, and note the change in your report.

- [ ] **Step 1: Write the extractor + adapter + stitcher**

```python
"""md_tables — PP-StructureV3 page artifacts -> stitched, html_to_cells-tolerant tables.

Three jobs, all dialect-general (no bank/doc conditionals):
 1. EXTRACT per-page table entries (pred_html + cell boxes + bbox) and page context text
    from the raw result JSON. Fail-loud with the real key names if the dialect differs.
 2. ADAPT Paddle's table HTML to the contract html_to_cells tolerates:
      - exactly one <table> with <thead> (context rows + header rows) and <tbody>
      - context rows = single-cell rows carrying the page text ABOVE the table on the same
        page (so _parse_period can resolve the period: '31 December 2024', 'As at 31 Dec 2023')
      - if Paddle emitted no <thead>: leading rows with NO parseable numeric cell are headers
 3. STITCH continuation fragments within the section page-range into period tables:
      new table iff period changes OR printed line_no restarts; otherwise append tbody rows
      (repeated header must equal the current column signature EXACTLY — hard error).

Run: python3 md_tables.py <doc_id>       (base python; writes outputs/<doc_id>/tables/)
"""
from __future__ import annotations
import json, os, re, sys

import lxml.html

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "2026-06-29_mineru_eval"))
from docs_config import DOCS, PT_PER_PX
from html_to_cells import parse_html, _parse_period, _norm, _build_grid

# --- dialect constants (verified against the Task-1 smoke report; adjust there only) ---
TABLE_HTML_KEY = "pred_html"        # key on a table result entry holding the HTML string
CELL_BOXES_KEY = "cell_box_list"    # sibling key holding per-cell boxes (image px)


# ------------------------------------------------------------------ raw json extraction
def _walk(node, want_key):
    """Yield every dict in the tree that has want_key."""
    if isinstance(node, dict):
        if want_key in node:
            yield node
        for v in node.values():
            yield from _walk(v, want_key)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, want_key)


def _bbox_pt(box) -> list[float]:
    """Normalize a Paddle box (flat [x0,y0,x1,y1] or 4-point polygon) -> PDF points."""
    pts = [float(v) for pair in (box if isinstance(box[0], (list, tuple)) else [box])
           for v in (pair if isinstance(pair, (list, tuple)) else [pair])]
    xs, ys = pts[0::2], pts[1::2]
    return [min(xs) * PT_PER_PX, min(ys) * PT_PER_PX, max(xs) * PT_PER_PX, max(ys) * PT_PER_PX]


def page_tables(json_path: str) -> list[dict]:
    """[{html, cell_boxes(list, PDF pts), bbox(PDF pts)}] in reading order for one page."""
    raw = json.load(open(json_path))
    entries = [d for d in _walk(raw, TABLE_HTML_KEY) if "<table" in str(d.get(TABLE_HTML_KEY, ""))]
    if "<table" in json.dumps(raw) and not entries:
        raise KeyError(f"{json_path}: table html present but not under key "
                       f"'{TABLE_HTML_KEY}' — real keys: {sorted(set(k for d in _walk(raw, None) or [] for k in []))} "
                       f"(inspect the file; update TABLE_HTML_KEY)")
    out = []
    for e in entries:
        boxes = e.get(CELL_BOXES_KEY)
        if boxes is None:
            raise KeyError(f"{json_path}: '{CELL_BOXES_KEY}' missing on a table entry "
                           f"(keys: {sorted(e.keys())})")
        cell_boxes = [_bbox_pt(b) for b in boxes]
        allx = [v for b in cell_boxes for v in (b[0], b[2])]
        ally = [v for b in cell_boxes for v in (b[1], b[3])]
        out.append(dict(html=e[TABLE_HTML_KEY], cell_boxes=cell_boxes,
                        bbox=[min(allx), min(ally), max(allx), max(ally)]))
    out.sort(key=lambda t: t["bbox"][1])          # reading order, top to bottom
    return out


def page_context_text(md_path: str) -> list[str]:
    """Non-table markdown text lines (page context: headings, 'As at 31 Dec 2023', …)."""
    md = open(md_path).read()
    md = re.sub(r"<table.*?</table>", " ", md, flags=re.S | re.I)
    md = re.sub(r"<[^>]+>", " ", md)
    lines = [_norm(l.lstrip("#* ")) for l in md.splitlines()]
    return [l for l in lines if l]


# ------------------------------------------------------------------ html adaptation
def _numericish(txt: str) -> bool:
    t = txt.strip().strip("()").replace(",", "")
    try:
        float(t); return True
    except ValueError:
        return t in {"-", "–", "—", "#", ""} and t != ""


def adapt_fragment(paddle_html: str, cell_boxes: list[list[float]], context: list[str]) -> dict:
    """One page-level fragment -> {header_trs(html), body_trs(html), col_sig, first/last line_no,
    body_rows_geom, col_bands, merges, period}."""
    root = lxml.html.fromstring(paddle_html)
    tables = root.xpath("//table")
    if len(tables) != 1:
        raise ValueError(f"fragment must hold exactly 1 <table>, got {len(tables)}")
    tbl = tables[0]
    trs = tbl.xpath(".//tr")
    # zip every th/td (document order) with cell_boxes — count mismatch is a FINDING
    tds = [c for tr in trs for c in tr.xpath("./th|./td")]
    if len(tds) != len(cell_boxes):
        raise ValueError(f"cell count mismatch: html={len(tds)} boxes={len(cell_boxes)} "
                         f"(record this in the finding doc if PP-Structure does it)")
    box_of = {id(c): b for c, b in zip(tds, cell_boxes)}

    # header/body split: explicit thead wins; else leading all-non-numeric rows are headers
    thead = tbl.find(".//thead")
    if thead is not None:
        header_trs = thead.findall(".//tr")
        body_trs = [tr for tr in trs if tr not in header_trs]
    else:
        header_trs, body_trs, in_head = [], [], True
        for tr in trs:
            cells = [_norm(c.text_content()) for c in tr.xpath("./th|./td")]
            if in_head and not any(_numericish(c) for c in cells[1:]):
                header_trs.append(tr)
            else:
                in_head = False
                body_trs.append(tr)
    ncols, _grid = _build_grid(header_trs) if header_trs else (0, [])

    # per-body-row geometry + printed line numbers + merges
    rows_geom, merges, line_nos = [], [], []
    col_x = {}
    for bi, tr in enumerate(body_trs):
        cells = tr.xpath("./th|./td")
        boxes = [box_of[id(c)] for c in cells]
        rows_geom.append([min(b[0] for b in boxes), min(b[1] for b in boxes),
                          max(b[2] for b in boxes), max(b[3] for b in boxes)])
        first = _norm(cells[0].text_content())
        m = re.match(r"(\d{1,2})[a-z]?$", first) or re.match(r"(\d{1,2})[a-z]?\s", first)
        line_nos.append(int(m.group(1)) if m else None)
        gcol = 0
        for c, b in zip(cells, boxes):
            cs = int(c.get("colspan", 1) or 1)
            if cs > 1:
                merges.append(dict(body_index=bi, col_id=gcol, colspan=cs))
            if cs == 1:
                lo, hi = col_x.get(gcol, (b[0], b[2]))
                col_x[gcol] = (min(lo, b[0]), max(hi, b[2]))
            gcol += cs
    nums = [n for n in line_nos if n is not None]
    sig = tuple(_norm(lxml.html.tostring(tr, encoding="unicode")
                      if False else tr.text_content()) for tr in header_trs)
    return dict(header_trs=[lxml.html.tostring(tr, encoding="unicode") for tr in header_trs],
                body_trs=[lxml.html.tostring(tr, encoding="unicode") for tr in body_trs],
                col_sig=sig, ncols=ncols, first_ln=(nums[0] if nums else None),
                last_ln=(nums[-1] if nums else None), rows_geom=rows_geom,
                col_bands={str(k): list(v) for k, v in sorted(col_x.items())},
                merges=merges, period=_parse_period(context))


# ------------------------------------------------------------------ stitcher
def _mon_yy(iso: str) -> str:
    y, m, _ = iso.split("-")
    return ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"][int(m)-1] + y[2:]


def build_tables(doc_id: str) -> list[dict]:
    doc = DOCS[doc_id]
    pages_dir = os.path.join(HERE, "outputs", doc_id, "pages")
    out_dir = os.path.join(HERE, "outputs", doc_id, "tables")
    os.makedirs(out_dir, exist_ok=True)
    page_nos = sorted(int(f[:3]) for f in os.listdir(pages_dir) if f.endswith(".json"))
    if not page_nos:
        raise FileNotFoundError(f"no page captures under {pages_dir} — run run_paddle first")

    tables, cur = [], None
    for pno in page_nos:
        jp = os.path.join(pages_dir, f"{pno:03d}.json")
        ctx = page_context_text(os.path.join(pages_dir, f"{pno:03d}.md"))
        for t in page_tables(jp):
            frag = adapt_fragment(t["html"], t["cell_boxes"], ctx)
            frag_geom = dict(page=pno, table_bbox=t["bbox"],
                             rows=frag["rows_geom"], col_bands=frag["col_bands"],
                             merges=frag["merges"])
            new = (cur is None
                   or (frag["period"] and frag["period"] != cur["period"])
                   or (frag["first_ln"] is not None and cur["last_ln"] is not None
                       and frag["first_ln"] <= cur["last_ln"]))
            if new:
                if cur is not None:
                    tables.append(cur)
                if not frag["period"]:
                    raise ValueError(f"p{pno}: new table but no period parsed from context {ctx[:5]}")
                cur = dict(period=frag["period"], header_trs=frag["header_trs"],
                           col_sig=frag["col_sig"], context=ctx[:3], pages=[pno],
                           body_trs=list(frag["body_trs"]), fragments=[frag_geom],
                           last_ln=frag["last_ln"])
            else:
                if frag["col_sig"] != cur["col_sig"]:
                    raise ValueError(f"p{pno}: continuation header != current signature\n"
                                     f"  cur: {cur['col_sig']}\n  new: {frag['col_sig']}")
                cur["pages"].append(pno)
                base = sum(len(f["rows"]) for f in cur["fragments"])
                for m in frag_geom["merges"]:
                    m["body_index"] += base
                for i, _ in enumerate(frag_geom["rows"]):
                    pass
                frag_geom["body_index_offset"] = base
                cur["fragments"].append(frag_geom)
                cur["body_trs"] += frag["body_trs"]
                cur["last_ln"] = frag["last_ln"] if frag["last_ln"] is not None else cur["last_ln"]
    if cur is not None:
        tables.append(cur)

    manifest = []
    for t in tables:
        table_id = f"nsfr_{_mon_yy(t['period'])}"
        ctx_rows = "".join(f"<tr><th colspan={max(t.get('ncols', 6), 2)}>{c}</th></tr>"
                           for c in t["context"])
        html = ("<table><thead>" + ctx_rows + "".join(t["header_trs"]) + "</thead><tbody>"
                + "".join(t["body_trs"]) + "</tbody></table>")
        hp = os.path.join(out_dir, table_id + ".html")
        gp = os.path.join(out_dir, table_id + ".geom.json")
        open(hp, "w").write(html)
        json.dump(dict(fragments=t["fragments"]), open(gp, "w"), indent=1)
        parsed = parse_html(html)
        if len(parsed) != 1:
            raise ValueError(f"{table_id}: adapted html parsed to {len(parsed)} tables")
        if parsed[0].period != t["period"]:
            raise ValueError(f"{table_id}: period lost in adaptation "
                             f"({parsed[0].period} != {t['period']})")
        manifest.append(dict(table_id=table_id, period=t["period"], pages=t["pages"],
                             html=hp, geom=gp))
    mp = os.path.join(out_dir, "manifest.json")
    json.dump(manifest, open(mp, "w"), indent=1)
    print(f"[{doc_id}] {len(manifest)} tables: "
          + ", ".join(f"{m['table_id']} (p{m['pages']})" for m in manifest))
    return manifest


if __name__ == "__main__":
    build_tables(sys.argv[1])
```

**Note to implementer:** the code above encodes the *contract*; the captured artifacts are
the *reality*. Before running the test, open one page JSON (e.g.
`python3 -c "import json;d=json.load(open('findociq/experiments/2026-07-07_paddleocr_eval/outputs/dbs_4q23_p3/pages/075.json'));print(json.dumps(d,indent=1)[:3000])"`)
and reconcile `TABLE_HTML_KEY` / `CELL_BOXES_KEY` / box format. If Paddle splits one
printed table into >1 `<table>` blocks per page, or merges the two header rows, do NOT
special-case a bank — fix the general rule and record it.

- [ ] **Step 2: Write `test_md_tables.py`** — plumbing facts only (extraction QUALITY belongs to the gates, not these tests):

```python
"""test_md_tables — plumbing checks on the REAL captured pages (T1+T2 must exist).
Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/test_md_tables.py"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "2026-06-29_mineru_eval"))
from md_tables import build_tables
from html_to_cells import parse_html

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)

ok = True
for doc_id, want in [("dbs_4q23_p3", {"nsfr_dec23": [75, 76], "nsfr_sep23": [77, 78]}),
                     ("ocbc_4q24_p3", {"nsfr_dec24": [95], "nsfr_sep24": [96]})]:
    print(f"\n[{doc_id}]")
    man = build_tables(doc_id)
    got = {m["table_id"]: m["pages"] for m in man}
    ok &= check(f"stitched into {sorted(want)}", got == want, got)
    for m in man:
        t = parse_html(open(m["html"]).read())[0]
        ok &= check(f"{m['table_id']}: period {m['period']}", t.period == m["period"], t.period)
        ok &= check(f"{m['table_id']}: 5 value columns", len(t.cols) == 5,
                    [c.leaf_label for c in t.cols])
        ok &= check(f"{m['table_id']}: unweighted group present",
                    any("nweighted" in (c.group or "") for c in t.cols),
                    [(c.leaf_label, c.group) for c in t.cols])
        g = json.load(open(m["geom"]))
        n_geom = sum(len(f["rows"]) for f in g["fragments"])
        ok &= check(f"{m['table_id']}: geom rows == tbody rows", n_geom == len(t.rows),
                    (n_geom, len(t.rows)))
        bands = g["fragments"][0]["col_bands"]
        xs = [bands[k][0] for k in sorted(bands, key=int)]
        ok &= check(f"{m['table_id']}: col bands monotonic", xs == sorted(xs), xs)
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Run the test, iterate on the adapter until green**

Run: `python3 findociq/experiments/2026-07-07_paddleocr_eval/test_md_tables.py`
Expected: all ✓, exit 0. The likely first failures are dialect facts (key names, thead
absence, boxes-per-cell mismatches) — fix them in md_tables.py generally, never per doc.
If a check can only pass with a per-doc hack, STOP and report: that is a finding about
PP-Structure, not something to paper over.

---

### Task 4: `overlay.py` — pdfplumber indent levels + shade fills

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/overlay.py`
- Test: `findociq/experiments/2026-07-07_paddleocr_eval/test_overlay.py`

**Interfaces:**
- Consumes: `html_to_cells.Table` (parsed from Task 3 html), geom sidecar dict, `pdfplumber`.
- Produces: `apply_overlay(table: Table, geom: dict, pdf_path: str) -> Table` — mutates in place: sets `Row.level` (1..K from indent clusters; band rows keep their cluster level) and `Cell.is_shade` (from grey fills), returns the table. Also `cluster_1d(xs: list[float], gap: float = 4.0) -> list[list[float]]` used by tests.

- [ ] **Step 1: Write `overlay.py`**

```python
"""overlay — author row hierarchy + shading from PDF GEOMETRY (pdfplumber).

Paddle's HTML carries no indentation and no fill styling; per the merge-perception
finding, geometry authors structure. Two general rules, no per-doc constants:

INDENT: level = 1 + rank of the row-label x0 cluster. Clusters = 1-D clustering of
  first-word x0 inside each body row's label region, gap threshold 4.0pt (measured
  band across both render styles: intra-level jitter <= 3.0pt, inter-level gap >= 7.6pt).

SHADE: within the table bbox, the MODAL grey-fill luminance is the base (row banding /
  plain background); a cell is shaded iff a grey fill DARKER than base (by > 0.05 lum,
  0.30 <= lum <= 0.95 so text/rules don't count) covers >= 50% of (row band x col band).
"""
from __future__ import annotations
import re
from collections import Counter

import pdfplumber


def cluster_1d(xs: list[float], gap: float = 4.0) -> list[list[float]]:
    xs = sorted(xs)
    out: list[list[float]] = []
    for x in xs:
        if out and x - out[-1][-1] <= gap:
            out[-1].append(x)
        else:
            out.append([x])
    return out


def _lum(color) -> float | None:
    """pdfplumber non_stroking_color -> grey luminance, or None if not greyish."""
    if color is None:
        return None
    if isinstance(color, (int, float)):
        return float(color)
    vals = [float(v) for v in color]
    if len(vals) == 1:
        return vals[0]
    if len(vals) >= 3:
        r, g, b = vals[:3]
        if max(r, g, b) - min(r, g, b) > 0.1:      # coloured, not grey
            return None
        return (r + g + b) / 3
    return None


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def apply_overlay(table, geom: dict, pdf_path: str):
    frags = geom["fragments"]
    # flat per-body-row (page, bbox) in stitched order
    row_pages, row_boxes = [], []
    for f in frags:
        for b in f["rows"]:
            row_pages.append(f["page"]); row_boxes.append(b)
    if len(row_boxes) != len(table.rows):
        raise ValueError(f"geom rows {len(row_boxes)} != parsed rows {len(table.rows)}")

    with pdfplumber.open(pdf_path) as pdf:
        pages = {p: pdf.pages[p - 1] for p in {f["page"] for f in frags}}
        words = {p: pg.extract_words() for p, pg in pages.items()}
        fills = {p: [r for r in pg.rects if r.get("fill")] for p, pg in pages.items()}

        # ---------- INDENT ----------
        label_hi = {}                       # label col x1 per page (from col band 0)
        for f in frags:
            label_hi[f["page"]] = f["col_bands"]["0"][1] if "0" in f["col_bands"] else None
        x0s, row_x0 = [], [None] * len(table.rows)
        for i, (p, bb) in enumerate(zip(row_pages, row_boxes)):
            hi = label_hi[p] or bb[2]
            ws = [w for w in words[p]
                  if bb[1] - 1 <= (w["top"] + w["bottom"]) / 2 <= bb[3] + 1 and w["x0"] < hi]
            # drop a leading standalone line number token; the label starts after it
            ws.sort(key=lambda w: w["x0"])
            if ws and re.fullmatch(r"\d{1,2}[a-z]?", ws[0]["text"]):
                ws = ws[1:]
            if ws:
                row_x0[i] = ws[0]["x0"]; x0s.append(ws[0]["x0"])
        clusters = cluster_1d(x0s)
        lo = [c[0] for c in clusters]
        def level_of(x):
            for k, c in enumerate(clusters):
                if c[0] - 2.0 <= x <= c[-1] + 2.0:
                    return k + 1
            # nearest cluster fallback (never silently level-0)
            return 1 + min(range(len(lo)), key=lambda k: abs(lo[k] - x))
        for i, r in enumerate(table.rows):
            r.level = level_of(row_x0[i]) if row_x0[i] is not None else 1

        # ---------- SHADE ----------
        for f in frags:
            p = f["page"]; bx = f["table_bbox"]
            in_tbl = [r for r in fills[p]
                      if _overlap(r["x0"], r["x1"], bx[0], bx[2]) > 0
                      and _overlap(r["top"], r["bottom"], bx[1], bx[3]) > 0]
            lums = [(_lum(r.get("non_stroking_color")), r) for r in in_tbl]
            greys = [(l, r) for l, r in lums if l is not None and 0.30 <= l <= 0.95]
            if not greys:
                continue
            base = Counter(round(l, 3) for l, _ in greys).most_common(1)[0][0]
            shade_rects = [r for l, r in greys if l < base - 0.05]
            off = f.get("body_index_offset", 0)
            bands = {int(k): v for k, v in f["col_bands"].items()}
            for ri, bb in enumerate(f["rows"]):
                row = table.rows[off + ri]
                for cell in row.cells:
                    xs = [bands[c] for c in range(cell.col_id, cell.col_id + cell.colspan)
                          if c in bands]
                    if not xs:
                        continue
                    cx0, cx1 = min(x[0] for x in xs), max(x[1] for x in xs)
                    area = max(1e-6, (cx1 - cx0) * (bb[3] - bb[1]))
                    cov = sum(_overlap(r["x0"], r["x1"], cx0, cx1)
                              * _overlap(r["top"], r["bottom"], bb[1], bb[3])
                              for r in shade_rects)
                    cell.is_shade = int(cov / area >= 0.5)
    return table
```

- [ ] **Step 2: Write `test_overlay.py`** — expectations computed FROM the GT CSVs, not hardcoded:

```python
"""test_overlay — indent + shade authored from geometry match GT facts.
Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/test_overlay.py"""
import csv, json, os, sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "2026-06-29_mineru_eval"))
from docs_config import DOCS
from html_to_cells import parse_html
from overlay import apply_overlay, cluster_1d

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)

ok = True
ok &= check("cluster_1d splits measured band", 
            [c[0] for c in cluster_1d([74.9, 82.5, 90.1, 75.2, 82.9])] == [74.9, 82.5, 90.1],
            cluster_1d([74.9, 82.5, 90.1, 75.2, 82.9]))
ok &= check("cluster_1d merges jitter",
            len(cluster_1d([104.2, 106.1, 107.1, 115.2, 116.0, 132.1, 132.8])) == 3,
            cluster_1d([104.2, 106.1, 107.1, 115.2, 116.0, 132.1, 132.8]))

for doc_id in DOCS:
    gt = list(csv.DictReader(open(DOCS[doc_id]["gt"])))
    man = json.load(open(os.path.join(HERE, "outputs", doc_id, "tables", "manifest.json")))
    print(f"\n[{doc_id}]")
    for m in man:
        t = parse_html(open(m["html"]).read())[0]
        apply_overlay(t, json.load(open(m["geom"])), DOCS[doc_id]["pdf"])
        gt_t = [r for r in gt if r["period"] == m["period"]]
        if not gt_t:
            continue                       # GT only covers the two quarters it covers
        # GT hierarchy by line_no -> our level must match (hierarchy == indent level)
        gt_h = {r["line_no"]: int(r["row_hierarchy"]) for r in gt_t}
        got_h = {r.line_no: r.level for r in t.rows if r.line_no in gt_h}
        diffs = {k: (gt_h[k], got_h.get(k)) for k in gt_h if got_h.get(k) != gt_h[k]}
        ok &= check(f"{m['table_id']}: indent levels == GT hierarchy (34 lines)",
                    not diffs, dict(list(diffs.items())[:6]))
        gt_shade = sum(1 for r in gt_t if r["is_shade"] == "1")
        got_shade = sum(c.is_shade for r in t.rows for c in r.cells)
        ok &= check(f"{m['table_id']}: shade count == GT ({gt_shade})",
                    got_shade == gt_shade, got_shade)
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Run, calibrate, iterate**

Run: `python3 findociq/experiments/2026-07-07_paddleocr_eval/test_overlay.py`
Expected: all ✓. If shade counts miss: print the per-luminance rect histogram inside the
table bbox for the failing page and adjust ONLY the general luminance-band constants
(document the measured values in code comments). If indent levels miss: print the raw x0
list and check whether Paddle's label col band (`col_bands["0"]`) is truncating labels.
Level differences that trace to Paddle mis-gridding rows are FINDINGS, not test bugs —
if so, record and move on (Gate 1 will quantify them).

---

### Task 5: `flatten.py` — Table → v_cell_flat rows

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/flatten.py`
- Test: `findociq/experiments/2026-07-07_paddleocr_eval/test_flatten.py`

**Interfaces:**
- Consumes: `html_to_cells.Table` (levels/shades already overlaid).
- Produces: `flatten_table(t, institution, doc_id, table_id, section_no="") -> list[dict]` with EXACTLY the GT column set; `write_cells_csv(rows, path)`; CLI `python3 flatten.py <doc_id>` reads the Task-3 manifest, applies the overlay, writes `outputs/<doc_id>/tables/<table_id>.cells.csv`. `GT_COLUMNS` list importable by the scorer.

- [ ] **Step 1: Write `flatten.py`**

```python
"""flatten — parsed+overlaid Table -> v_cell_flat-shaped rows (no DB round-trip).

Lineage semantics (matches GT + schema_v7 registries):
  * a body row with NO cells is a BAND (e.g. 'ASF Item'): it is the lineage ROOT of every
    following data row until the next band, and emits no cell rows itself.
  * data-row lineage = [band] + chain(labels of nearest-earlier rows at level-1, level-2, ...)
    + [own label]; row_depth = len(lineage); row_hierarchy = indent level.
  * parent chains are recomputed here from Row.level (the overlay mutates levels AFTER
    html_to_cells computed parent_idx — do not trust parent_idx).
  * columns: grouped leaf -> col_lvl1=group, col_lvl2=leaf, depth 2; ungrouped ->
    col_lvl1=leaf, col_lvl2='', depth 1.
  * row_id numbering counts band rows (GT: 'ASF Item' band is row_id 1, line 1 is row_id 2).

Run: python3 flatten.py <doc_id>
"""
from __future__ import annotations
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "2026-06-29_mineru_eval"))
from docs_config import DOCS, TABLE_TITLE, TABLE_TYPE

GT_COLUMNS = ["institution", "period", "table_type", "table_title", "section_no",
              "line_no", "row_lvl1", "row_lvl2", "row_lvl3", "row_lvl4", "row_lvl5",
              "row_depth", "col_lvl1", "col_lvl2", "col_depth", "value_num", "value_raw",
              "cell_state", "is_shade", "colspan", "concept_key", "geo_key",
              "row_header_id", "col_header_id", "doc_id", "table_id", "row_id", "col_id",
              "row_hierarchy"]


def flatten_table(t, *, institution: str, doc_id: str, table_id: str,
                  section_no: str = "") -> list[dict]:
    if not t.period:
        raise ValueError(f"{table_id}: table has no period — cells would be unjoinable")
    out = []
    band = None
    stack: list[tuple[int, str]] = []          # (level, label) path of data rows
    for row_id0, r in enumerate(t.rows, start=1):
        if not r.cells:                         # band row: lineage root, no cell rows
            band = r.label
            stack = []
            continue
        while stack and stack[-1][0] >= r.level:
            stack.pop()
        stack.append((r.level, r.label))
        lineage = ([band] if band else []) + [lbl for _, lbl in stack]
        if len(lineage) > 5:
            raise ValueError(f"{table_id} line {r.line_no}: lineage depth {len(lineage)} > 5: {lineage}")
        lv = lineage + [""] * (5 - len(lineage))
        for c in r.cells:
            col = next(cc for cc in t.cols if cc.col_id == c.col_id)
            grouped = bool(col.group)
            out.append(dict(
                institution=institution, period=t.period, table_type=TABLE_TYPE,
                table_title=TABLE_TITLE, section_no=section_no,
                line_no=r.line_no or "",
                row_lvl1=lv[0], row_lvl2=lv[1], row_lvl3=lv[2], row_lvl4=lv[3], row_lvl5=lv[4],
                row_depth=len(lineage),
                col_lvl1=(col.group if grouped else col.leaf_label),
                col_lvl2=(col.leaf_label if grouped else ""),
                col_depth=(2 if grouped else 1),
                value_num=("" if c.value_num is None else c.value_num),
                value_raw=c.value_raw, cell_state=c.cell_state,
                is_shade=c.is_shade, colspan=c.colspan,
                concept_key="", geo_key="",              # assigned identities: report-only
                row_header_id="", col_header_id="",       # stamped by load_db, not here
                doc_id=doc_id, table_id=table_id, row_id=row_id0, col_id=c.col_id,
                row_hierarchy=r.level,
            ))
    if not out:
        raise ValueError(f"{table_id}: flatten produced zero cells")
    return out


def write_cells_csv(rows: list[dict], path: str):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GT_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main(doc_id: str):
    from html_to_cells import parse_html
    from overlay import apply_overlay
    doc = DOCS[doc_id]
    tdir = os.path.join(HERE, "outputs", doc_id, "tables")
    man = json.load(open(os.path.join(tdir, "manifest.json")))
    for m in man:
        t = parse_html(open(m["html"]).read())[0]
        apply_overlay(t, json.load(open(m["geom"])), doc["pdf"])
        rows = flatten_table(t, institution=doc["institution"], doc_id=doc_id,
                             table_id=m["table_id"])
        p = os.path.join(tdir, m["table_id"] + ".cells.csv")
        write_cells_csv(rows, p)
        print(f"  {m['table_id']}: {len(rows)} cells -> {p}")


if __name__ == "__main__":
    main(sys.argv[1])
```

- [ ] **Step 2: Write `test_flatten.py`** — anchored on the KNOWN-GOOD Gemini sample (18/18-tested parse), so flatten's semantics are validated independently of Paddle quality:

```python
"""test_flatten — lineage/shape semantics on the known-good Gemini NSFR sample.
Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/test_flatten.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "2026-06-29_mineru_eval"))
from html_to_cells import parse_html
from flatten import flatten_table, GT_COLUMNS

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)

SAMPLE = os.path.join(HERE, "..", "2026-06-29_mineru_eval", "samples", "gemini35_ocbc_nsfr.html")
tables = parse_html(open(SAMPLE).read())
rows = flatten_table(tables[0], institution="X", doc_id="d", table_id="t")

ok = True
ok &= check("exact GT column set", list(rows[0].keys()) == GT_COLUMNS,
            [k for k in rows[0] if k not in GT_COLUMNS])
ok &= check("34 distinct line_no", len({r["line_no"] for r in rows}) == 34,
            len({r["line_no"] for r in rows}))
ok &= check("no band rows emitted as cells", all(r["line_no"] for r in rows))
combos = {(r["col_lvl1"], r["col_lvl2"]) for r in rows}
ok &= check("5 col combos incl. ungrouped Weighted",
            len(combos) == 5 and ("Weighted value", "") in combos, sorted(combos))
l1 = next(r for r in rows if r["line_no"] == "1")
ok &= check("line 1 lineage = ASF Item > Capital:",
            (l1["row_lvl1"], l1["row_lvl2"], l1["row_depth"], l1["row_hierarchy"])
            == ("ASF Item", "Capital:", 2, 1), l1)
ok &= check("line 1 row_id counts the band (== 2)", l1["row_id"] == 2, l1["row_id"])
l21 = next(r for r in rows if r["line_no"] == "21")
ok &= check("line 21 depth 4 / hierarchy 3",
            (l21["row_depth"], l21["row_hierarchy"]) == (4, 3),
            (l21["row_depth"], l21["row_hierarchy"]))
ok &= check("all periods resolved", all(r["period"] == "2025-12-31" for r in rows))
l2 = next(r for r in rows if r["line_no"] == "2" and r["col_lvl1"] == "Weighted value")
ok &= check("line 2 weighted = 59082 reported",
            (l2["value_num"], l2["cell_state"]) == (59082.0, "reported"),
            (l2["value_num"], l2["cell_state"]))
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Run test, fix until green**

Run: `python3 findociq/experiments/2026-07-07_paddleocr_eval/test_flatten.py`
Expected: all ✓, exit 0. (Note: the Gemini sample has `data-level` levels, so the
band-stack logic gets exercised with level-1 bands and level-1 'Capital:' rows — the
band must still root the lineage because it is CELL-LESS, not because of its level.)

- [ ] **Step 4: Produce the Paddle cells.csv files for both docs**

Run:
```bash
python3 findociq/experiments/2026-07-07_paddleocr_eval/flatten.py dbs_4q23_p3
python3 findociq/experiments/2026-07-07_paddleocr_eval/flatten.py ocbc_4q24_p3
```
Expected: 4 `*.cells.csv` files written, each reporting on the order of ~140±20 cells
(exact counts are Gate-1 business, not this step's).

---

### Task 6: `score_cells.py` — Gate 1 + Gate 2

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/score_cells.py`
- Test: `findociq/experiments/2026-07-07_paddleocr_eval/test_score_cells.py`

**Interfaces:**
- Consumes: `<table_id>.cells.csv` (pred), GT CSVs, `<table_id>.geom.json` (merge set), pdfplumber (TEXT adjudication), `flatten.GT_COLUMNS`.
- Produces: `score_doc(doc_id) -> dict` + CLI writing `outputs/scores/<doc_id>.json`:
  `{ "tables": { "<table_id>": { "gate1": {"structure": [...], "text": [...], "missing": [...], "extra": [...], "pass": bool}, "gate2": {"gt_merges": N, "pred_merges": N, "diff": [...], "pass": bool} } } }`. Each mismatch entry: `{"key": [period,line_no,col_lvl1,col_lvl2], "field": str, "gt": str, "pred": str, "class": "STRUCTURE"|"TEXT", "adjudication": str|None}`.

- [ ] **Step 1: Write `score_cells.py`**

```python
"""score_cells — Gate 1 (cell parity) + Gate 2 (geometry merge set) vs ground truth.

Join key: (period, line_no, col_lvl1, col_lvl2) — flat-to-flat, no hierarchy walking.
Gated fields per Design decision 4; lineage-label text rule per Design decision 5.
TEXT mismatches on value_raw are adjudicated against pdfplumber words on the table's
pages: GT string present verbatim -> 'ocr_error_pdfplumber_fixable', else 'unverified'.

Run: python3 score_cells.py <doc_id>
"""
from __future__ import annotations
import csv, difflib, json, os, re, sys

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import DOCS

KEY = ("period", "line_no", "col_lvl1", "col_lvl2")
STRUCT_FIELDS = ["row_depth", "row_hierarchy", "col_depth", "cell_state", "is_shade", "colspan"]
LINEAGE = ["row_lvl1", "row_lvl2", "row_lvl3", "row_lvl4", "row_lvl5"]
_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub(" ", (s or "")).strip().casefold()


def _load(path):
    return list(csv.DictReader(open(path)))


def _mm(key, field, gt, pred, cls, adj=None):
    return dict(key=list(key), field=field, gt=gt, pred=pred, **{"class": cls},
                adjudication=adj)


def score_table(gt_rows, pred_rows, geom, page_words) -> dict:
    gt = {tuple(r[k] for k in KEY): r for r in gt_rows}
    pred = {tuple(r[k] for k in KEY): r for r in pred_rows}
    if len(gt) != len(gt_rows):
        raise ValueError("GT join key not unique — investigate before scoring")
    structure, text, missing, extra = [], [], [], []

    for k in gt:
        if k not in pred:
            missing.append(_mm(k, "<row>", "present", "MISSING", "STRUCTURE"))
    for k in pred:
        if k not in gt:
            extra.append(_mm(k, "<row>", "ABSENT", "present", "STRUCTURE"))

    for k in sorted(set(gt) & set(pred)):
        g, p = gt[k], pred[k]
        for f in STRUCT_FIELDS:
            if str(g[f]) != str(p[f]):
                structure.append(_mm(k, f, g[f], p[f], "STRUCTURE"))
        for f in LINEAGE:
            if norm(g[f]) == norm(p[f]):
                continue
            if not g[f] or not p[f]:
                structure.append(_mm(k, f, g[f], p[f], "STRUCTURE"))
            elif difflib.SequenceMatcher(None, norm(g[f]), norm(p[f])).ratio() >= 0.9:
                text.append(_mm(k, f, g[f], p[f], "TEXT"))
            else:
                structure.append(_mm(k, f, g[f], p[f], "STRUCTURE"))
        if norm(g["value_raw"]) != norm(p["value_raw"]):
            gnum = g["value_num"] or ""
            pnum = p["value_num"] or ""
            same_num = gnum and pnum and float(gnum) == float(pnum)
            adj = ("ocr_error_pdfplumber_fixable"
                   if any(g["value_raw"].strip() == w for w in page_words) else "unverified")
            text.append(_mm(k, "value_raw", g["value_raw"], p["value_raw"], "TEXT",
                            "numerically_equal" if same_num else adj))

    # line_no ordering: pred line numbers must be strictly increasing
    seen = [int(r["line_no"]) for r in pred_rows
            if r["line_no"].isdigit() and r["col_lvl2"] == "No Maturity"]
    if seen != sorted(seen):
        structure.append(_mm(("<table>",), "line_no_order", "increasing", str(seen), "STRUCTURE"))

    # ---- Gate 2: merge sets (anchor line_no+col_id+colspan), raw geometry vs GT ----
    gt_merges = sorted((r["period"], r["line_no"], r["col_lvl1"], r["col_lvl2"], r["colspan"])
                       for r in gt_rows if r["colspan"] != "1")
    pred_merges = sorted((r["period"], r["line_no"], r["col_lvl1"], r["col_lvl2"], r["colspan"])
                         for r in pred_rows if r["colspan"] != "1")
    diff = [list(x) for x in
            sorted(set(map(tuple, gt_merges)) ^ set(map(tuple, pred_merges)))]
    return dict(
        gate1=dict(structure=structure + missing + extra, text=text,
                   missing=len(missing), extra=len(extra),
                   matched=len(set(gt) & set(pred)), gt_cells=len(gt),
                   **{"pass": not (structure or missing or extra)}),
        gate2=dict(gt_merges=len(gt_merges), pred_merges=len(pred_merges), diff=diff,
                   **{"pass": not diff}),
    )


def score_doc(doc_id: str) -> dict:
    doc = DOCS[doc_id]
    tdir = os.path.join(HERE, "outputs", doc_id, "tables")
    man = json.load(open(os.path.join(tdir, "manifest.json")))
    gt_all = _load(doc["gt"])
    out = {"tables": {}}
    with pdfplumber.open(doc["pdf"]) as pdf:
        for m in man:
            gt_rows = [r for r in gt_all if r["period"] == m["period"]]
            if not gt_rows:
                continue
            pred_rows = _load(os.path.join(tdir, m["table_id"] + ".cells.csv"))
            words = {w["text"] for p in m["pages"] for w in pdf.pages[p - 1].extract_words()}
            geom = json.load(open(m["geom"]))
            out["tables"][m["table_id"]] = score_table(gt_rows, pred_rows, geom, words)
    if not out["tables"]:
        raise ValueError(f"{doc_id}: no table matched any GT period — nothing scored")
    os.makedirs(os.path.join(HERE, "outputs", "scores"), exist_ok=True)
    sp = os.path.join(HERE, "outputs", "scores", doc_id + ".json")
    json.dump(out, open(sp, "w"), indent=1)
    for tid, s in out["tables"].items():
        g1, g2 = s["gate1"], s["gate2"]
        print(f"  {tid}: GATE1 {'PASS' if g1['pass'] else 'FAIL'} "
              f"(structure={len(g1['structure'])}, text={len(g1['text'])}, "
              f"matched {g1['matched']}/{g1['gt_cells']}) | "
              f"GATE2 {'PASS' if g2['pass'] else 'FAIL'} "
              f"({g2['pred_merges']}/{g2['gt_merges']} merges, {len(g2['diff'])} diffs)")
    return out


if __name__ == "__main__":
    score_doc(sys.argv[1])
```

- [ ] **Step 2: Write `test_score_cells.py`** — synthetic fixture with one planted error per class:

```python
"""test_score_cells — classifier correctness on a planted-error fixture.
Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/test_score_cells.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from score_cells import score_table

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)

def row(line, col1, col2, **kw):
    base = dict(period="2023-12-31", line_no=line, col_lvl1=col1, col_lvl2=col2,
                row_lvl1="ASF Item", row_lvl2="Capital:", row_lvl3="", row_lvl4="",
                row_lvl5="", row_depth="2", row_hierarchy="1", col_depth="2",
                cell_state="reported", is_shade="0", colspan="1",
                value_raw="65,326", value_num="65326.0")
    base.update(kw)
    return base

GT = [row("1", "U", "No Maturity"),
      row("2", "U", "No Maturity", row_lvl2="Capita1:", value_raw="1,000", value_num="1000.0"),
      row("3", "U", "No Maturity", colspan="4"),
      row("4", "U", "No Maturity")]
PRED = [row("1", "U", "No Maturity"),                                   # clean
        row("2", "U", "No Maturity", row_lvl2="Capital:",               # lineage TEXT (ratio>=.9)
            value_raw="1,00O", value_num=""),                           # value TEXT (OCR)
        row("3", "U", "No Maturity", colspan="1"),                      # colspan STRUCTURE + gate2
        row("5", "U", "No Maturity")]                                   # extra + line 4 missing

s = score_table(GT, PRED, geom={}, page_words={"1,000"})
g1, g2 = s["gate1"], s["gate2"]
ok = True
ok &= check("gate1 fails", not g1["pass"])
ok &= check("1 missing + 1 extra", (g1["missing"], g1["extra"]) == (1, 1),
            (g1["missing"], g1["extra"]))
fields = sorted(m["field"] for m in g1["structure"])
ok &= check("colspan is STRUCTURE", "colspan" in fields, fields)
ok &= check("lineage typo is TEXT not STRUCTURE",
            any(m["field"] == "row_lvl2" for m in g1["text"])
            and not any(m["field"] == "row_lvl2" for m in g1["structure"]),
            fields)
vr = [m for m in g1["text"] if m["field"] == "value_raw"]
ok &= check("value TEXT adjudicated pdfplumber-fixable",
            vr and vr[0]["adjudication"] == "ocr_error_pdfplumbe" "r_fixable", vr)
ok &= check("gate2 catches the lost merge", not g2["pass"] and g2["gt_merges"] == 1,
            (g2["gt_merges"], g2["pred_merges"]))
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Run fixture test until green**

Run: `python3 findociq/experiments/2026-07-07_paddleocr_eval/test_score_cells.py`
Expected: all ✓, exit 0.

- [ ] **Step 4: Score both docs for real**

Run:
```bash
python3 findociq/experiments/2026-07-07_paddleocr_eval/score_cells.py dbs_4q23_p3
python3 findociq/experiments/2026-07-07_paddleocr_eval/score_cells.py ocbc_4q24_p3
```
Expected: a PASS/FAIL line per table and `outputs/scores/<doc_id>.json` written. **Any
result is a valid outcome** — the numbers are the experiment. Report the per-table
summaries verbatim to the orchestrator; do NOT tune anything to make gates pass.

---

### Task 7: `cells_to_xlsx.py` — Excel verification views

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/cells_to_xlsx.py`
- Test: `findociq/experiments/2026-07-07_paddleocr_eval/test_cells_to_xlsx.py`

**Interfaces:**
- Consumes: `<table_id>.cells.csv` (Task 5).
- Produces: `outputs/<doc_id>/tables/<table_id>.xlsx` — REAL merged cells + grey fills generated FROM the parsed cells (what you see is what scoring/loading sees). CLI: `python3 cells_to_xlsx.py <doc_id>`.

- [ ] **Step 1: Write `cells_to_xlsx.py`**

```python
"""cells_to_xlsx — Excel verification view: one sheet per table, REAL merges + shading,
built from the parsed cells (never from the source PDF). Layout: title row, two header
rows (groups merged over their children), then one row per printed line (band rows as
bold label rows). Run: python3 cells_to_xlsx.py <doc_id>
"""
from __future__ import annotations
import csv, json, os, sys
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
GREY = PatternFill("solid", start_color="D9D9D9")
BOLD = Font(bold=True)


def build_xlsx(cells_csv: str, out_path: str):
    rows = list(csv.DictReader(open(cells_csv)))
    if not rows:
        raise ValueError(f"{cells_csv}: empty")
    cols = list(OrderedDict(((r["col_lvl1"], r["col_lvl2"]), None) for r in
                sorted(rows, key=lambda r: int(r["col_id"]))))
    col_pos = {c: i + 3 for i, c in enumerate(cols)}       # A=line_no, B=label
    wb = Workbook(); ws = wb.active; ws.title = rows[0]["table_id"][:31]
    ws.cell(1, 1, f"{rows[0]['table_title']} — {rows[0]['institution']} — {rows[0]['period']}").font = BOLD
    # header rows 2 (groups) + 3 (leaves)
    for c, (g, leaf) in enumerate(cols, start=3):
        ws.cell(2, c, g if leaf else ""); ws.cell(3, c, leaf or g)
    ws.cell(3, 1, "line"); ws.cell(3, 2, "label")
    start = None
    for c, (g, leaf) in enumerate(cols, start=3):
        if leaf and (start is None or cols[start - 3][0] != g):
            start = c
        if leaf and (c == len(cols) + 2 or c + 1 > len(cols) + 2 or
                     (c - 2 < len(cols) and cols[c - 2][0] != g)):
            if c > start:
                ws.merge_cells(start_row=2, start_column=start, end_row=2, end_column=c)
    # body: group by row_id
    by_row = OrderedDict()
    for r in sorted(rows, key=lambda r: (int(r["row_id"]), int(r["col_id"]))):
        by_row.setdefault(int(r["row_id"]), []).append(r)
    xl_row = 4
    last_band = None
    for rid, cells in by_row.items():
        g0 = cells[0]
        if g0["row_lvl1"] and g0["row_lvl1"] != last_band:      # band label row
            ws.cell(xl_row, 2, g0["row_lvl1"]).font = BOLD
            last_band = g0["row_lvl1"]; xl_row += 1
        label = next(v for v in [g0[f"row_lvl{g0['row_depth']}"], g0["row_lvl2"]] if v)
        ws.cell(xl_row, 1, g0["line_no"])
        ws.cell(xl_row, 2, "  " * (int(g0["row_hierarchy"]) - 1) + label)
        for cell in cells:
            c0 = col_pos[(cell["col_lvl1"], cell["col_lvl2"])]
            v = cell["value_num"]
            ws.cell(xl_row, c0, float(v) if v else cell["value_raw"])
            span = int(cell["colspan"])
            if span > 1:
                ws.merge_cells(start_row=xl_row, start_column=c0,
                               end_row=xl_row, end_column=c0 + span - 1)
            if cell["is_shade"] == "1":
                for cc in range(c0, c0 + span):
                    ws.cell(xl_row, cc).fill = GREY
        xl_row += 1
    ws.column_dimensions["B"].width = 60
    for i in range(3, len(cols) + 3):
        ws.column_dimensions[get_column_letter(i)].width = 14
    wb.save(out_path)


def main(doc_id: str):
    tdir = os.path.join(HERE, "outputs", doc_id, "tables")
    for m in json.load(open(os.path.join(tdir, "manifest.json"))):
        src = os.path.join(tdir, m["table_id"] + ".cells.csv")
        dst = os.path.join(tdir, m["table_id"] + ".xlsx")
        build_xlsx(src, dst)
        print(f"  {m['table_id']}: {dst}")


if __name__ == "__main__":
    main(sys.argv[1])
```

- [ ] **Step 2: Write `test_cells_to_xlsx.py`**

```python
"""test_cells_to_xlsx — the workbook shows exactly what the cells say.
Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/test_cells_to_xlsx.py"""
import csv, json, os, sys
from openpyxl import load_workbook
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import DOCS

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)

ok = True
for doc_id in DOCS:
    tdir = os.path.join(HERE, "outputs", doc_id, "tables")
    for m in json.load(open(os.path.join(tdir, "manifest.json"))):
        rows = list(csv.DictReader(open(os.path.join(tdir, m["table_id"] + ".cells.csv"))))
        wb = load_workbook(os.path.join(tdir, m["table_id"] + ".xlsx"))
        ws = wb.active
        n_merge_gt = sum(1 for r in rows if r["colspan"] != "1")
        body_merges = [rg for rg in ws.merged_cells.ranges if rg.min_row >= 4]
        ok &= check(f"{doc_id}/{m['table_id']}: body merges == colspan>1 cells "
                    f"({n_merge_gt})", len(body_merges) == n_merge_gt, len(body_merges))
        n_shade = sum(int(r["colspan"]) for r in rows if r["is_shade"] == "1")
        n_fill = sum(1 for row in ws.iter_rows(min_row=4)
                     for c in row if c.fill and c.fill.start_color
                     and c.fill.start_color.rgb and str(c.fill.start_color.rgb).endswith("D9D9D9"))
        ok &= check(f"{doc_id}/{m['table_id']}: shaded xl cells == shade span ({n_shade})",
                    n_fill == n_shade, n_fill)
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Generate + verify**

Run:
```bash
python3 findociq/experiments/2026-07-07_paddleocr_eval/cells_to_xlsx.py dbs_4q23_p3
python3 findociq/experiments/2026-07-07_paddleocr_eval/cells_to_xlsx.py ocbc_4q24_p3
python3 findociq/experiments/2026-07-07_paddleocr_eval/test_cells_to_xlsx.py
```
Expected: 4 xlsx files; test all ✓, exit 0.

---

### Task 8: `load_db.py` — schema_v7 load + v_cell_flat round-trip

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/load_db.py`
- Test: `findociq/experiments/2026-07-07_paddleocr_eval/test_load_db.py`

**Interfaces:**
- Consumes: `<table_id>.cells.csv` for both docs; `findociq/schema/schema_v7.sql`.
- Produces: `findociq/experiments/2026-07-07_paddleocr_eval/paddle_eval.db` (fresh each run; NEVER `final.db`). Loader derives `row_header`/`col_header` registries from the lineage columns (lineage_key = casefold + whitespace-collapse + strip trailing `(N)` footnote markers, joined with `' > '`), stamps FK ids on `cell_fact`. Round-trip: `SELECT` from `v_cell_flat` must equal the input CSVs on all non-id columns.

- [ ] **Step 1: Write `load_db.py`**

```python
"""load_db — cells.csv (v_cell_flat shape) -> fresh schema_v7 SQLite, then round-trip.

Proves DB loadability and exercises v7's header-lineage layer: registries derived from
lineage text, FK ids stamped on cell_fact, and SELECT v_cell_flat == input CSV on every
non-id column. Depth overflow and FK violations are hard failures (schema-enforced).

Run: python3 load_db.py            # loads BOTH docs into paddle_eval.db
"""
from __future__ import annotations
import csv, json, os, re, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import DOCS, TABLE_TITLE, TABLE_TYPE

DB = os.path.join(HERE, "paddle_eval.db")
SCHEMA = os.path.join(HERE, "..", "..", "schema", "schema_v7.sql")
_FOOT = re.compile(r"\s*\(\d+\)\s*$")
_WS = re.compile(r"\s+")


def lineage_key(levels: list[str]) -> str:
    return " > ".join(_WS.sub(" ", _FOOT.sub("", l)).strip().casefold()
                      for l in levels if l)


def get_header_id(cur, table: str, levels: list[str], cache: dict) -> int:
    lk = lineage_key(levels)
    if lk in cache:
        return cache[lk]
    depth = len([l for l in levels if l])
    if not 1 <= depth <= 5:
        raise ValueError(f"lineage depth {depth} out of 1..5: {levels}")
    lv = [l or None for l in levels] + [None] * (5 - len(levels))
    cur.execute(f"INSERT INTO {table}(lineage_key,lvl1,lvl2,lvl3,lvl4,lvl5,depth) "
                f"VALUES (?,?,?,?,?,?,?)", (lk, *lv[:5], depth))
    cache[lk] = cur.lastrowid
    return cache[lk]


def load_doc(cur, doc_id: str, rh_cache: dict, ch_cache: dict) -> int:
    doc = DOCS[doc_id]
    tdir = os.path.join(HERE, "outputs", doc_id, "tables")
    man = json.load(open(os.path.join(tdir, "manifest.json")))
    periods = [m["period"] for m in man]
    cur.execute("INSERT INTO document(doc_id,institution,doc_family,source_file,doc_period) "
                "VALUES (?,?,?,?,?)",
                (doc_id, doc["institution"], "pillar3", doc["pdf"], max(periods)))
    n = 0
    for m in man:
        rows = list(csv.DictReader(open(os.path.join(tdir, m["table_id"] + ".cells.csv"))))
        cur.execute("INSERT INTO table_t(doc_id,table_id,table_title,table_type,period,page_range) "
                    "VALUES (?,?,?,?,?,?)",
                    (doc_id, m["table_id"], TABLE_TITLE, TABLE_TYPE, m["period"],
                     f"{m['pages'][0]}-{m['pages'][-1]}"))
        # --- col_dim: groups get out-of-band ids 100+, leaves 1..N ---
        seen_groups, gid = {}, 100
        leaves = {}
        for r in sorted(rows, key=lambda r: int(r["col_id"])):
            leaves[int(r["col_id"])] = (r["col_lvl1"], r["col_lvl2"])
        for cid, (l1, l2) in leaves.items():
            grp = l1 if l2 else None
            if grp and grp not in seen_groups:
                seen_groups[grp] = gid
                cur.execute("INSERT INTO col_dim(doc_id,table_id,col_id,col_hierarchy,"
                            "col_parent,col_leaf_label,unit) VALUES (?,?,?,0,NULL,?,?)",
                            (doc_id, m["table_id"], gid, grp, "S$m"))
                gid += 1
            chid = get_header_id(cur, "col_header", [l1] + ([l2] if l2 else []), ch_cache)
            cur.execute("INSERT INTO col_dim(doc_id,table_id,col_id,col_hierarchy,col_parent,"
                        "col_leaf_label,unit,col_header_id) VALUES (?,?,?,1,?,?,?,?)",
                        (doc_id, m["table_id"], cid, seen_groups.get(grp),
                         l2 or l1, "S$m", chid))
        # --- row_dim (bands included as hierarchy 0) + cell_fact ---
        by_row = {}
        for r in rows:
            by_row.setdefault(int(r["row_id"]), []).append(r)
        emitted_bands = {}
        for rid in sorted(by_row):
            g0 = by_row[rid][0]
            depth = int(g0["row_depth"])
            lv = [g0[f"row_lvl{i}"] for i in range(1, 6)]
            band = lv[0]
            if band and band not in emitted_bands:          # synthesize the band row
                bid = rid - 1 if rid - 1 not in by_row else max(by_row) + len(emitted_bands) + 1
                bh = get_header_id(cur, "row_header", [band], rh_cache)
                cur.execute("INSERT INTO row_dim(doc_id,table_id,row_id,row_hierarchy,"
                            "row_parent,row_leaf_label,line_no,row_header_id) "
                            "VALUES (?,?,?,0,NULL,?,NULL,?)",
                            (doc_id, m["table_id"], bid, band, bh))
                emitted_bands[band] = bid
            rh = get_header_id(cur, "row_header", lv[:depth], rh_cache)
            cur.execute("INSERT INTO row_dim(doc_id,table_id,row_id,row_hierarchy,row_parent,"
                        "row_leaf_label,line_no,row_header_id) VALUES (?,?,?,?,?,?,?,?)",
                        (doc_id, m["table_id"], rid, int(g0["row_hierarchy"]),
                         emitted_bands.get(band), lv[depth - 1], g0["line_no"], rh))
            for r in by_row[rid]:
                chid = get_header_id(cur, "col_header",
                                     [r["col_lvl1"]] + ([r["col_lvl2"]] if r["col_lvl2"] else []),
                                     ch_cache)
                cur.execute("INSERT INTO cell_fact(doc_id,table_id,row_id,col_id,colspan,"
                            "value_raw,value_num,cell_state,is_shade,period,"
                            "row_header_id,col_header_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (doc_id, m["table_id"], rid, int(r["col_id"]), int(r["colspan"]),
                             r["value_raw"], float(r["value_num"]) if r["value_num"] else None,
                             r["cell_state"], int(r["is_shade"]), r["period"], rh, chid))
                n += 1
    return n


def main():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(open(SCHEMA).read())
    con.execute("PRAGMA foreign_keys = ON;")
    cur = con.cursor()
    rh_cache, ch_cache = {}, {}
    total = sum(load_doc(cur, d, rh_cache, ch_cache) for d in DOCS)
    con.commit()
    got = cur.execute("SELECT COUNT(*) FROM v_cell_flat").fetchone()[0]
    print(f"loaded {total} cells; v_cell_flat rows = {got}")
    if got != total:
        raise RuntimeError("v_cell_flat row count != loaded cells")
    con.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write `test_load_db.py`** — the round-trip IS the test:

```python
"""test_load_db — SELECT v_cell_flat == input cells.csv on every non-id column.
Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/test_load_db.py"""
import csv, json, os, sqlite3, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import DOCS

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)

COMPARE = ["institution", "period", "table_type", "table_title", "line_no",
           "row_lvl1", "row_lvl2", "row_lvl3", "row_lvl4", "row_lvl5", "row_depth",
           "col_lvl1", "col_lvl2", "col_depth", "value_num", "value_raw",
           "cell_state", "is_shade", "colspan", "row_hierarchy"]

def canon(d):
    o = []
    for k in COMPARE:
        v = d.get(k)
        if k == "value_num":
            v = "" if v in (None, "") else f"{float(v):g}"
        elif k in ("row_depth", "col_depth", "is_shade", "colspan", "row_hierarchy"):
            v = str(int(v))
        else:
            v = "" if v is None else str(v)
        o.append(v)
    return tuple(o)

con = sqlite3.connect(os.path.join(HERE, "paddle_eval.db"))
con.row_factory = sqlite3.Row
db_rows = [dict(r) for r in con.execute(
    "SELECT *, (SELECT rd.line_no FROM row_dim rd WHERE rd.doc_id=v.doc_id "
    " AND rd.table_id=v.table_id AND rd.row_id=v.row_id) AS line_no2 FROM v_cell_flat v")]
ok = True
db_set = {}
for r in db_rows:
    db_set.setdefault((r["doc_id"], r["table_id"]), []).append(canon(r))
for doc_id in DOCS:
    tdir = os.path.join(HERE, "outputs", doc_id, "tables")
    for m in json.load(open(os.path.join(tdir, "manifest.json"))):
        rows = list(csv.DictReader(open(os.path.join(tdir, m["table_id"] + ".cells.csv"))))
        want = sorted(canon(r) for r in rows)
        got = sorted(db_set.get((doc_id, m["table_id"]), []))
        first_diff = next(((w, g) for w, g in zip(want, got) if w != g), None)
        ok &= check(f"{doc_id}/{m['table_id']}: round-trip identical ({len(want)} cells)",
                    want == got, (len(got), first_diff))
n_reg = con.execute("SELECT (SELECT COUNT(*) FROM row_header), "
                    "(SELECT COUNT(*) FROM col_header)").fetchone()
ok &= check("registries populated and shared across docs", n_reg[0] > 0 and n_reg[1] == 5,
            tuple(n_reg))
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Load + verify**

Run:
```bash
python3 findociq/experiments/2026-07-07_paddleocr_eval/load_db.py
python3 findociq/experiments/2026-07-07_paddleocr_eval/test_load_db.py
```
Expected: `loaded N cells; v_cell_flat rows = N`; test all ✓ exit 0. Note `col_header`
count == 5 — same 5 column lineages shared by both banks (the registry-convergence
property v7 exists for). If the OCBC col labels differ verbatim from DBS ('≥ 1yr'
variants), the count will exceed 5: that is CORRECT registry behavior (verbatim
identity) — relax that check to `n_reg[1] >= 5` and note it.

---

### Task 9: Gate 3 — full-doc TOC runs + `score_toc.py`

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/score_toc.py`
- Test: `findociq/experiments/2026-07-07_paddleocr_eval/test_score_toc.py`

**Interfaces:**
- Consumes: `outputs/<doc_id>/pages_full/NNN.json` (the background runs from Task 2 Step 6 — VERIFY they finished: both dirs must hold one .json per PDF page: DBS 83, OCBC ~100; re-run `run_paddle.py <doc> --full` to resume if not); toc.json (`sections` list = GT).
- Produces: `outputs/scores/toc_<doc_id>.json`: `{"recall": float, "precision": float, "matched": [...], "near_misses": [...], "unmatched_sections": [...], "unmatched_candidates_on_table_pages": [...], "caption_bleed": int}`.

- [ ] **Step 1: Write `score_toc.py`**

```python
"""score_toc — Gate 3: can Paddle-captured titles rebuild the printed TOC?

Candidates: layout blocks whose label contains 'title' (doc/paragraph/table titles) from
every full-doc page JSON. GT: toc.json 'sections' (leaf granularity, printed contents).
Match: deterministic normalization first (casefold, ws-collapse, strip leading section
numbering like 'A.11.2', '24.', '1.1'); then difflib ratio >= 0.9 as reported near-miss.
Page attribution: candidate page must lie in [start_page, end_page].
Caption bleed (MinerU's killer): matched candidate whose normalized text is > 1.5x the
section title length (prose glued onto the heading).

Run: python3 score_toc.py <doc_id>
"""
from __future__ import annotations
import difflib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import DOCS

_WS = re.compile(r"\s+")
_LEADNUM = re.compile(r"^(?:part\s+[a-z]|[a-z])?\.?\s*\d+(?:\.\d+)*\.?\s*", re.I)


def norm_title(s: str) -> str:
    return _WS.sub(" ", _LEADNUM.sub("", (s or "").casefold())).strip()


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def title_candidates(pages_dir: str) -> list[dict]:
    """[{page, label, text}] for every layout block whose label mentions 'title'."""
    out = []
    for f in sorted(os.listdir(pages_dir)):
        if not f.endswith(".json"):
            continue
        pno = int(f[:3])
        raw = json.load(open(os.path.join(pages_dir, f)))
        for d in _walk(raw):
            lbl = str(d.get("label", ""))
            if "title" in lbl.lower():
                txt = d.get("text") or d.get("content") or ""
                if not txt:      # some dialects keep text under a sibling list; skip empties
                    continue
                out.append(dict(page=pno, label=lbl, text=_WS.sub(" ", str(txt)).strip()))
    if not out:
        raise ValueError(f"{pages_dir}: zero title-labeled layout blocks — inspect one "
                         f"page json for the real label/text keys and update title_candidates")
    return out


def score_toc(doc_id: str) -> dict:
    doc = DOCS[doc_id]
    pages_dir = os.path.join(HERE, "outputs", doc_id, "pages_full")
    cands = title_candidates(pages_dir)
    sections = json.load(open(doc["toc"]))["sections"]
    matched, near, bleed = [], [], 0
    unmatched_secs = []
    used = set()
    for s in sections:
        sn = norm_title(s["title"])
        hit = None
        for i, c in enumerate(cands):
            if i in used or not (s["start_page"] <= c["page"] <= s["end_page"]):
                continue
            cn = norm_title(c["text"])
            if cn == sn:
                hit = (i, 1.0); break
            r = difflib.SequenceMatcher(None, sn, cn).ratio()
            if r >= 0.9 and (hit is None or r > hit[1]):
                hit = (i, r)
        if hit is None:
            unmatched_secs.append(dict(section=s["section_id"], title=s["title"],
                                       pages=[s["start_page"], s["end_page"]]))
        else:
            i, r = hit
            used.add(i)
            entry = dict(section=s["section_id"], title=s["title"], page=cands[i]["page"],
                         candidate=cands[i]["text"], ratio=round(r, 3))
            (matched if r == 1.0 else near).append(entry)
            if len(norm_title(cands[i]["text"])) > 1.5 * max(1, len(sn)):
                bleed += 1
    n_sec = len(sections)
    recall = (len(matched) + len(near)) / n_sec if n_sec else 0.0
    leftovers = [c for i, c in enumerate(cands) if i not in used]
    precision = (len(matched) + len(near)) / len(cands) if cands else 0.0
    out = dict(doc_id=doc_id, sections=n_sec, candidates=len(cands),
               recall=round(recall, 3), precision=round(precision, 3),
               matched=matched, near_misses=near, caption_bleed=bleed,
               unmatched_sections=unmatched_secs,
               unmatched_candidates=leftovers[:80])
    os.makedirs(os.path.join(HERE, "outputs", "scores"), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, "outputs", "scores", f"toc_{doc_id}.json"), "w"),
              indent=1)
    print(f"[{doc_id}] TOC: recall {recall:.1%} ({len(matched)} exact + {len(near)} near) "
          f"of {n_sec} sections; precision {precision:.1%} of {len(cands)} candidates; "
          f"caption-bleed {bleed}; unmatched sections {len(unmatched_secs)}")
    return out


if __name__ == "__main__":
    score_toc(sys.argv[1])
```

- [ ] **Step 2: Write `test_score_toc.py`** — synthetic fixture, including a bleed case:

```python
"""test_score_toc — normalization, matching, near-miss, and bleed counting.
Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/test_score_toc.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from score_toc import norm_title
import difflib

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)

ok = True
ok &= check("strips 'A.11.2 ' style numbering",
            norm_title("A.11.2 Credit Risk Mitigation") == "credit risk mitigation",
            norm_title("A.11.2 Credit Risk Mitigation"))
ok &= check("strips '24.' style numbering",
            norm_title("24. NET STABLE FUNDING RATIO") == "net stable funding ratio",
            norm_title("24. NET STABLE FUNDING RATIO"))
ok &= check("strips '1.1 ' numbering",
            norm_title("1.1 NSFR Disclosure Template") == "nsfr disclosure template",
            norm_title("1.1 NSFR Disclosure Template"))
ok &= check("plain title unchanged", norm_title("Attestation Statement") == "attestation statement")
r = difflib.SequenceMatcher(None, norm_title("NSFR Disclosure Template"),
                            norm_title("NSFR Disclosure Ternplate")).ratio()
ok &= check("OCR near-miss lands in [0.9,1)", 0.9 <= r < 1.0, r)
bleedy = norm_title("1.1 NSFR Disclosure Template The Group monitors its funding profile")
ok &= check("bleed detectable by 1.5x length rule",
            len(bleedy) > 1.5 * len(norm_title("NSFR Disclosure Template")), len(bleedy))
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Run fixture test until green**

Run: `python3 findociq/experiments/2026-07-07_paddleocr_eval/test_score_toc.py` → all ✓, exit 0.

- [ ] **Step 4: Verify the full-doc captures completed, then score for real**

```bash
ls findociq/experiments/2026-07-07_paddleocr_eval/outputs/dbs_4q23_p3/pages_full/*.json | wc -l   # expect 83
ls findociq/experiments/2026-07-07_paddleocr_eval/outputs/ocbc_4q24_p3/pages_full/*.json | wc -l  # expect = page count
python3 findociq/experiments/2026-07-07_paddleocr_eval/score_toc.py dbs_4q23_p3
python3 findociq/experiments/2026-07-07_paddleocr_eval/score_toc.py ocbc_4q24_p3
```
Expected: one summary line per doc + `outputs/scores/toc_*.json`. As with Gate 1: any
number is a valid outcome; report verbatim, tune nothing.

---

### Task 10: `assemble_scorecard.py`, scorecard.md, finding doc, PROGRESS

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/assemble_scorecard.py`
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/scorecard.md` (generated)
- Create: `findociq/docs/findings/2026-07-07-paddleocr-stage2-spike.md`
- Modify: `findociq/PROGRESS.md` (new session block on top)
- Check: `findociq/docs/diagrams/2026-07-07-pipeline-workflows.md` §2 — update only if the implemented flow diverged from the spec's diagram (it gained `overlay.py`; reflect that).

**Interfaces:**
- Consumes: `outputs/scores/*.json`, `outputs/pins.txt`.
- Produces: `scorecard.md` with per-gate tables and the spec's decision lines.

- [ ] **Step 1: Write `assemble_scorecard.py`**

```python
"""assemble_scorecard — outputs/scores/*.json -> scorecard.md (verdicts per spec).

Verdict rules (from the spec's gates):
  Gate 1 pass = zero STRUCTURE mismatches (TEXT reported, not failing).
  Gate 2 pass = merge sets identical.
  Per render class: both gates pass -> 'DROP/REDUCE Gemini candidate' (structure trusted;
  TEXT count decides whether pdfplumber fusion is needed); any structure fail -> 'KEEP'.
  Gate 3 verdict is evidence, not a threshold: report P/R + bleed vs MinerU's failure mode.

Run: python3 assemble_scorecard.py
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import DOCS

S = os.path.join(HERE, "outputs", "scores")


def main():
    lines = ["# PaddleOCR Stage-2 spike — scorecard (generated)", "",
             f"Pins: see `outputs/pins.txt`. Spec: `docs/specs/2026-07-07-paddleocr-stage2-spike-design.md`.", ""]
    lines += ["## Gate 1 + Gate 2 (cell parity, geometry)", "",
              "| doc (render) | table | matched/GT | STRUCTURE | TEXT | Gate1 | merges pred/GT | Gate2 |",
              "|---|---|---|---|---|---|---|---|"]
    verdicts = {}
    for doc_id, cfg in DOCS.items():
        p = os.path.join(S, doc_id + ".json")
        if not os.path.exists(p):
            raise FileNotFoundError(p)
        sc = json.load(open(p))
        all_pass = True
        text_total = 0
        for tid, s in sc["tables"].items():
            g1, g2 = s["gate1"], s["gate2"]
            all_pass &= g1["pass"] and g2["pass"]
            text_total += len(g1["text"])
            lines.append(f"| {doc_id} ({cfg['render']}) | {tid} | {g1['matched']}/{g1['gt_cells']} "
                         f"| {len(g1['structure'])} | {len(g1['text'])} "
                         f"| {'PASS' if g1['pass'] else 'FAIL'} "
                         f"| {g2['pred_merges']}/{g2['gt_merges']} "
                         f"| {'PASS' if g2['pass'] else 'FAIL'} |")
        verdicts[cfg["render"]] = (
            ("DROP-Gemini candidate (structure fully geometry-derived; "
             + ("zero TEXT noise)" if text_total == 0 else
                f"{text_total} TEXT cells -> pair with pdfplumber text fusion)"))
             if all_pass else "KEEP Gemini (structural parity not reached)"))
    lines += ["", "## Stage-2 verdict per table class", ""]
    for render, v in verdicts.items():
        lines.append(f"- **{render}**: {v}")
    lines += ["", "## Gate 3 — TOC capability", "",
              "| doc | sections | recall | precision | near-misses | caption-bleed | unmatched |",
              "|---|---|---|---|---|---|---|"]
    for doc_id in DOCS:
        t = json.load(open(os.path.join(S, f"toc_{doc_id}.json")))
        lines.append(f"| {doc_id} | {t['sections']} | {t['recall']:.1%} | {t['precision']:.1%} "
                     f"| {len(t['near_misses'])} | {t['caption_bleed']} "
                     f"| {len(t['unmatched_sections'])} |")
    lines += ["", "_TOC verdict: see finding doc — judged against MinerU's caption-bleed "
              "failure mode, not a bare threshold._", ""]
    r = json.load(open(os.path.join(S, "regions.json")))
    lines += ["", "## Gate 4 — region detection (router region_source candidate)", ""]
    lines.append(f"- T4a ruled parity (UOB §12.9): {'PASS' if r['t4a']['pass'] else 'FAIL'}"
                 f" — {r['t4a']['matched']}/28 matched, mean IoU {r['t4a']['mean_iou']}")
    lines.append("- T4b borderless main-region: "
                 f"{'PASS' if r['t4b']['pass'] else 'FAIL'} — "
                 + ", ".join(f"p{p['page']} cov={p['num_cov']}"
                             for p in r["t4b"]["per_page"] if "num_cov" in p))
    lines.append(f"- T4c NO_TABLE false positives: {'PASS' if r['t4c']['pass'] else 'FAIL'}"
                 f" — {len(r['t4c']['violations'])} violations on "
                 f"{r['t4c']['no_table_pages']} pages")
    lines.append("- **region_source verdict:** "
                 + ("CANDIDATE — qualifies to replace the dead MinerU branch"
                    if all(r[k]["pass"] for k in ("t4a", "t4b", "t4c"))
                    else "NOT qualified (see failing sub-test)"))
    lines.append("")
    out = os.path.join(HERE, "scorecard.md")
    open(out, "w").write("\n".join(lines))
    print(f"wrote {out}")
    print("\n".join(lines[:30]))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate the scorecard**

Run: `python3 findociq/experiments/2026-07-07_paddleocr_eval/assemble_scorecard.py`
Expected: `scorecard.md` written; table rows for 4 tables + 2 TOC rows; verdict bullets present.

- [ ] **Step 3: Write the finding doc** `findociq/docs/findings/2026-07-07-paddleocr-stage2-spike.md`

Contents (write from the actual results — no template numbers): question + link to spec;
pinned versions quoted from `outputs/pins.txt`; per-gate results tables copied from
scorecard.md; the STRUCTURE/TEXT mismatch patterns actually observed (with 2–3 verbatim
examples each); overlay calibration values measured (indent clusters, shade luminance
bands per doc); PP-Structure dialect facts discovered in Tasks 1/3 (JSON keys, thead
behavior, cell-box alignment); Gate-3 verdict vs MinerU's caption-bleed failure;
explicit ANSWER to the spike question per render class (drop/reduce/keep), the
TOC-capability verdict, AND the Gate-4 region_source verdict (T4a/b/c results — does
Paddle qualify to replace the dead `_mineru_detect` branch as the borderless
`region_source`?); recommended next step (e.g. geometry+pdfplumber text fusion
branch, region_source wiring as a routing pivot, or stop). End with the spec's reminder: adoption = a routing branch + spec +
route_map visibility + "pipeline pivot" announcement — NOT part of this spike.

- [ ] **Step 4: Update `findociq/PROGRESS.md`** — new block on top, following the file's
existing style (✅/🐞/⏭️ keys), covering: spike executed per plan, per-gate outcomes,
verdicts, finding-doc path, and any open items (e.g. dialect quirks deferred).

- [ ] **Step 5: Check the workflow diagram** `findociq/docs/diagrams/2026-07-07-pipeline-workflows.md` §2:
the implemented flow adds `overlay.py` (pdfplumber indent+shade) between `html_to_cells`
and `flatten` — update the diagram to match reality if it doesn't already show it.

- [ ] **Step 6: Full re-run of every test as the final gate**

```bash
for t in findociq/experiments/2026-07-07_paddleocr_eval/test_*.py; do echo "== $t"; python3 "$t" || exit 1; done
python3 findociq/experiments/2026-06-29_mineru_eval/test_html_to_cells.py
```
Expected: every suite green (including the untouched 18/18 html_to_cells suite — this
spike must not have modified it), exit 0.

---

### Task 11: Gate 4 — `score_regions.py` (region detection; added 2026-07-08)

**Files:**
- Create: `findociq/experiments/2026-07-07_paddleocr_eval/score_regions.py`
- Test: `findociq/experiments/2026-07-07_paddleocr_eval/test_score_regions.py`

**Interfaces:**
- Consumes: `outputs/uob_4q25_p3/pages_sec12.9/NNN.json` (Task 2), `outputs/ocbc_4q24_p3/pages/NNN.json` (Task 2), `outputs/<doc>/pages_full/NNN.json` (background full-doc runs — T4c only), `docs_config.ALL_DOCS/DOCS/PT_PER_PX/section_pages`, and the ROUTER's referee imported from `findociq/pipeline/route/scan.py`: `NUM`, `_in_bbox`.
- Produces: `outputs/scores/regions.json` with keys `t4a`, `t4b`, `t4c`, each carrying a `pass` bool (consumed by Task 10's scorecard).

**Scheduling note (orchestrator):** T4a/T4b can run any time after Task 2; T4c needs the finished `pages_full` captures — run it when they land (same gate as Task 9 Step 4). Do NOT block Tasks 3–8 on this task.

- [ ] **Step 1: Write `score_regions.py`**

```python
"""score_regions — Gate 4: PaddleOCR as table-REGION detector (router region_source candidate).

T4a ruled parity  : UOB 4Q25 §12.9 pp38-41 — Paddle regions vs pdfplumber find_tables()
                    (known-true 10/4/10/4 = 28). Greedy 1:1 IoU matching; pass = 28/28
                    matched at IoU >= 0.5 and zero unmatched on either side.
T4b borderless    : OCBC NSFR table pages — exactly ONE Paddle region per page holding
                    >= 95% of the page's numeric tokens. Referee = the router's own
                    coverage machinery (scan.NUM + scan._in_bbox), imported, never copied.
T4c false positive: every NO_TABLE page in the route manifests of the two full-capture
                    docs -> zero Paddle table regions (needs pages_full captures).

Run: python3 score_regions.py all      (or: t4a | t4b | t4c)
Results MERGE into outputs/scores/regions.json (t4c can arrive later than t4a/t4b).
"""
from __future__ import annotations
import glob, json, os, sys

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROUTE_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "pipeline", "route"))
sys.path.insert(0, ROUTE_DIR)
from docs_config import ALL_DOCS, DOCS, PT_PER_PX, section_pages
from scan import NUM, _in_bbox          # the router's referee — never reimplement

REGION_LABEL = "table"    # exact layout label for table regions (NOT 'table_title')


def paddle_regions(json_path: str) -> list[list[float]]:
    """Table-region bboxes (PDF points) from a page's raw PP-StructureV3 JSON."""
    raw = json.load(open(json_path))
    out = []

    def walk(node):
        if isinstance(node, dict):
            if str(node.get("label", "")).lower() == REGION_LABEL:
                box = node.get("coordinate") or node.get("box") or node.get("bbox")
                if box is None:
                    raise KeyError(f"{json_path}: table block without a box "
                                   f"(keys: {sorted(node.keys())})")
                pts = [float(v) for v in
                       (box if not isinstance(box[0], (list, tuple))
                        else [c for p in box for c in p])]
                xs, ys = pts[0::2], pts[1::2]
                out.append([min(xs) * PT_PER_PX, min(ys) * PT_PER_PX,
                            max(xs) * PT_PER_PX, max(ys) * PT_PER_PX])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(raw)
    return out


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match_regions(truth: list, pred: list, thr: float = 0.5):
    """Greedy 1:1 matching by descending IoU.
    Returns (pairs [(ti, pi, iou)], unmatched_truth_idx, unmatched_pred_idx)."""
    pairs = sorted(((iou(t, p), ti, pi) for ti, t in enumerate(truth)
                    for pi, p in enumerate(pred)), reverse=True)
    used_t, used_p, out = set(), set(), []
    for s, ti, pi in pairs:
        if s < thr:
            break
        if ti in used_t or pi in used_p:
            continue
        used_t.add(ti); used_p.add(pi); out.append((ti, pi, round(s, 3)))
    return (out, [i for i in range(len(truth)) if i not in used_t],
            [i for i in range(len(pred)) if i not in used_p])


def numeric_coverage(page, bbox) -> tuple[float, int]:
    """Fraction of the page's numeric tokens whose CENTER lies in bbox (router rule)."""
    nums = [w for w in page.extract_words() if NUM.match(w["text"])]
    if not nums:
        return 0.0, 0
    inside = sum(1 for w in nums
                 if _in_bbox((w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2, bbox))
    return inside / len(nums), len(nums)


def t4a() -> dict:
    doc = ALL_DOCS["uob_4q25_p3"]
    pages = section_pages(doc["toc"], "12.9")
    pdir = os.path.join(HERE, "outputs", "uob_4q25_p3", "pages_sec12.9")
    per_page, ious, total_t, total_m, unmatched = [], [], 0, 0, 0
    with pdfplumber.open(doc["pdf"]) as pdf:
        for pno in pages:
            truth = [list(t.bbox) for t in pdf.pages[pno - 1].find_tables()]
            pred = paddle_regions(os.path.join(pdir, f"{pno:03d}.json"))
            pairs, un_t, un_p = match_regions(truth, pred)
            total_t += len(truth); total_m += len(pairs)
            unmatched += len(un_t) + len(un_p)
            ious += [s for _, _, s in pairs]
            per_page.append(dict(page=pno, truth=len(truth), pred=len(pred),
                                 matched=len(pairs), unmatched_truth=len(un_t),
                                 unmatched_pred=len(un_p)))
    return dict(per_page=per_page, truth_total=total_t, matched=total_m,
                mean_iou=round(sum(ious) / len(ious), 3) if ious else 0.0,
                **{"pass": total_t == 28 and total_m == 28 and unmatched == 0})


def t4b() -> dict:
    doc = DOCS["ocbc_4q24_p3"]
    pdir = os.path.join(HERE, "outputs", "ocbc_4q24_p3", "pages")
    per_page, ok = [], True
    with pdfplumber.open(doc["pdf"]) as pdf:
        for f in sorted(os.listdir(pdir)):
            if not f.endswith(".json"):
                continue
            pno = int(f[:3])
            regions = paddle_regions(os.path.join(pdir, f))
            page = pdf.pages[pno - 1]
            n_num = sum(1 for w in page.extract_words() if NUM.match(w["text"]))
            if n_num < 5:               # prose page inside the section range (p94) —
                good = not regions      # mirrors the router's num_tokens<5 NO_TABLE rule
                per_page.append(dict(page=pno, regions=len(regions), prose=True, ok=good))
            else:
                cov, n = (numeric_coverage(page, regions[0])
                          if len(regions) == 1 else (0.0, n_num))
                good = len(regions) == 1 and cov >= 0.95
                per_page.append(dict(page=pno, regions=len(regions),
                                     num_cov=round(cov, 4), num_tokens=n, ok=good))
            ok &= good
    return dict(per_page=per_page, **{"pass": ok})


def t4c() -> dict:
    checks, ok = [], True
    for doc_id, cfg in DOCS.items():
        stem = os.path.splitext(os.path.basename(cfg["pdf"]))[0]
        hits = glob.glob(os.path.join(ROUTE_DIR, "out", stem + "*_route*.json"))
        if len(hits) != 1:
            raise FileNotFoundError(f"{doc_id}: expected 1 route manifest for {stem!r}, got {hits}")
        m = json.load(open(hits[0]))
        page_objs = m["pages"] if isinstance(m, dict) and "pages" in m else m
        for pobj in page_objs:
            if pobj.get("route") != "NO_TABLE":
                continue
            pno = pobj.get("page") or pobj.get("page_no")
            if pno is None:
                raise KeyError(f"{hits[0]}: NO_TABLE page object lacks a page number "
                               f"(keys: {sorted(pobj.keys())})")
            jp = os.path.join(HERE, "outputs", doc_id, "pages_full", f"{int(pno):03d}.json")
            regions = paddle_regions(jp)
            ok &= not regions
            checks.append(dict(doc=doc_id, page=int(pno), regions=len(regions)))
    if not checks:
        raise ValueError("route manifests yielded zero NO_TABLE pages — wrong manifests?")
    return dict(no_table_pages=len(checks),
                violations=[c for c in checks if c["regions"]],
                checks=checks, **{"pass": ok})


def main(which: str):
    os.makedirs(os.path.join(HERE, "outputs", "scores"), exist_ok=True)
    sp = os.path.join(HERE, "outputs", "scores", "regions.json")
    res = json.load(open(sp)) if os.path.exists(sp) else {}
    if which in ("t4a", "all"):
        res["t4a"] = t4a()
        print(f"  T4a ruled parity : {'PASS' if res['t4a']['pass'] else 'FAIL'} "
              f"({res['t4a']['matched']}/28 matched, mean IoU {res['t4a']['mean_iou']})")
    if which in ("t4b", "all"):
        res["t4b"] = t4b()
        print(f"  T4b borderless   : {'PASS' if res['t4b']['pass'] else 'FAIL'} "
              f"{[(p['page'], p.get('num_cov', 'prose')) for p in res['t4b']['per_page']]}")
    if which in ("t4c", "all"):
        res["t4c"] = t4c()
        print(f"  T4c no-table FPs : {'PASS' if res['t4c']['pass'] else 'FAIL'} "
              f"({res['t4c']['no_table_pages']} pages, "
              f"{len(res['t4c']['violations'])} violations)")
    json.dump(res, open(sp, "w"), indent=1)
    print(f"  -> {sp}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
```

**Adapt-point** (same protocol as Task 3): `REGION_LABEL` and the box key are dialect
facts — confirm against a captured page JSON (a UOB §12.9 page has 10 table regions to
eyeball) before trusting the extractor; adjust the constant, not per-doc logic. If Paddle
uses a label like `table_body` or emits regions only inside `table_res_list`, update
`paddle_regions` generally and note it in your report.

- [ ] **Step 2: Write `test_score_regions.py`**

```python
"""test_score_regions — IoU/matcher/coverage fixtures + the pdfplumber 28-region truth.
Run: python3 findociq/experiments/2026-07-07_paddleocr_eval/test_score_regions.py"""
import os, sys
import pdfplumber
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from docs_config import ALL_DOCS, section_pages
from score_regions import iou, match_regions, numeric_coverage

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)

ok = True
ok &= check("iou identical = 1", iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0)
ok &= check("iou disjoint = 0", iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0)
ok &= check("iou half-overlap = 1/3", abs(iou([0, 0, 10, 10], [5, 0, 15, 10]) - 1 / 3) < 1e-9,
            iou([0, 0, 10, 10], [5, 0, 15, 10]))
pairs, un_t, un_p = match_regions([[0, 0, 10, 10], [20, 0, 30, 10]],
                                  [[1, 0, 11, 10], [40, 0, 50, 10]])
ok &= check("greedy matcher: 1 pair + 1 unmatched each side",
            (len(pairs), un_t, un_p) == (1, [1], [1]), (pairs, un_t, un_p))

# REAL truth check (pdfplumber only — no Paddle artifacts needed):
doc = ALL_DOCS["uob_4q25_p3"]
pages = section_pages(doc["toc"], "12.9")
ok &= check("§12.9 pages = 38..41", pages == [38, 39, 40, 41], pages)
with pdfplumber.open(doc["pdf"]) as pdf:
    counts = [len(pdf.pages[p - 1].find_tables()) for p in pages]
    ok &= check("find_tables truth = 10/4/10/4", counts == [10, 4, 10, 4], counts)
    page = pdf.pages[37]
    cov, n = numeric_coverage(page, list(page.bbox))
    ok &= check("full-page numeric coverage = 1.0", cov == 1.0 and n > 0, (cov, n))
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Run the test until green**

Run: `python3 findociq/experiments/2026-07-07_paddleocr_eval/test_score_regions.py`
Expected: all ✓, exit 0. (If find_tables does NOT return 10/4/10/4, STOP — the known-true
baseline itself failed to reproduce; report BLOCKED with the actual counts.)

- [ ] **Step 4: Score T4a + T4b for real**

```bash
python3 findociq/experiments/2026-07-07_paddleocr_eval/score_regions.py t4a
python3 findociq/experiments/2026-07-07_paddleocr_eval/score_regions.py t4b
```
Expected: one PASS/FAIL line each; `outputs/scores/regions.json` written. Any result is
a valid outcome — report verbatim, tune nothing.

- [ ] **Step 5 (deferred until full-doc captures finish): Score T4c**

Run: `python3 findociq/experiments/2026-07-07_paddleocr_eval/score_regions.py t4c`
Expected: `(≥14 pages, N violations)` line; result merged into the same regions.json.

---

## Self-review notes (done at plan time)

- **Spec coverage:** T1/T2 (Tasks 2–8), T3 (Task 9), Gates 1/2 (Task 6), Gate 3 (Task 9), markdown-container architecture + html_to_cells reuse (Task 3), Excel view (Task 7), paddle_eval.db on schema_v7 (Task 8), scorecard + finding (Task 10), pinned env (Task 1), toc.json verification (done — facts section), zero-Gemini + fail-loud + no-per-doc-conditionals (global constraints). Spec's "flatten computes lineage from parent chains" is honored with the overlay supplying levels (Design decision 2 — the one intentional addition; report it to the user).
- **Known adapt-points** (external-library reality): PP-StructureV3 result JSON key names and save-path semantics (Task 1 smoke pins them; Tasks 3/9 have marked constants + loud extractors). These are evidence-gated adjustments with exact inspection commands, not placeholders.
- **Type consistency:** `build_tables` manifest keys (`table_id/period/pages/html/geom`) are consumed identically in Tasks 4, 5, 6, 7, 8; `GT_COLUMNS` defined once (Task 5) and used by the scorer/loader tests; `body_index_offset` written by the stitcher and read by the overlay.
