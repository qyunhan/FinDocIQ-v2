# Spike: MinerU + pdfplumber vs Gemini for Stage-2 HTML → schema_v5

**Date:** 2026-06-29
**Status:** Design — pending user review
**Owner:** yunhan
**Related:** `DELIVERABLE/FinDocIQ_Plan_9.docx` (Stage 2 + Appendix A), `DELIVERABLE/schema_v5.sql`

## 1. Goal & motivation

FinDocIQ Plan 9 defines a 5-stage pipeline where **only Stage 2 calls the AI**: Gemini
reads a PDF page and returns an **HTML table** (headers + grid + colspan + shading +
dashes + row level); Stages 3–5 (enrich / reconcile / load) are deterministic and load
that HTML into the `schema_v5` star schema.

Stage 2 is the only token cost. This spike answers one question, **per table class**:

> Can **MinerU (+ a pdfplumber overlay)** produce the Stage-2 HTML deterministically
> (zero tokens), well enough to **drop / reduce / keep** Gemini — without breaking the
> universal pipeline?

The output is a **decision + scorecard**, plus a reusable `html_to_cells` loader. It is
**not** a production pipeline.

### Why HTML (not JSON)
HTML maps 1:1 to `schema_v5` and is more compact than nested JSON (real token savings):
native `colspan` → `cell_fact.colspan`; `style="background-color:#D3D3D3;"` → `is_shade`;
dash/0/blank text → `cell_state`; `data-level` → row hierarchy. Confirmed against a real
Gemini call (§4).

### Why MinerU + pdfplumber (complementary)
- **MinerU** (table-recognition model) is strong on grid + `colspan`, weak/absent on
  shading and indentation level.
- **pdfplumber** (verified) reads grey **rect fills** (→ `is_shade`) and **x-indentation
  clusters** (→ row level) — exactly the two fields MinerU drops.
- **Gemini's** unique edge is *semantic* hierarchy on ragged tables.

## 2. The pinned HTML contract (Stage-2 output, both backends)

Finalized from Plan 9 Appendix A **and** a real `gemini-2.5-flash` sample on
`OCBC_NSFR.pdf` (saved at `scratchpad/gemini_ocbc_nsfr.html`).

```
<table border="1">
  <thead>
    <tr><th colspan="N">context row (Date / Currency / Scope)</th></tr>   ← 0+ full-width
    <tr> group-header row: <th colspan="K">group</th>, empty <th></th> over ungrouped </tr>
    <tr> leaf-header row: one <th> per leaf column </tr>                   ← FLAT, no rowspan
  </thead>
  <tbody>
    <tr data-level="L" [data-kind="total"]>
      <td>value | 0 | - | (empty)</td> ...                               ← one <td> per column
      <td colspan="K">…</td>                                             ← merges only
      <td style="background-color:#D3D3D3;">…</td>                       ← shaded cells
    </tr>
  </tbody>
</table>
```

Field mapping → `schema_v5`:

| HTML construct | schema_v5 |
|---|---|
| `<table>` + context rows | `table_t` (table_type, period, page_range) |
| group `<th colspan>` / leaf `<th>` | `col_dim.leaf_label`, `col_hierarchy`, `col_parent`, `col_period` |
| `<tr data-level=L>` | `row_dim.row_hierarchy = L` |
| leading numeric `<td>` (see §3 wrinkle 2) | `row_dim.line_no` |
| `<td colspan=N>` | `cell_fact.col_id` (position), `colspan` |
| `<td>` text `value / 0 / - / blank` | `cell_fact.value_raw`, `value_num`, `cell_state ∈ {reported,zero,null,empty,suppressed}` |
| `style="background-color:#D3D3D3;"` (grey only) | `cell_fact.is_shade = 1` |

**Derived, NOT in the HTML** (Plan 9 "only things that need eyes"): `row_parent` (nearest
`data-level − 1` above), `concept_key`, `geo_key` — computed in the enricher.

## 3. `html_to_cells` loader — defensive rules (from the real sample)

The loader parses the contract into `schema_v5` rows/cells. Two wrinkles appeared in the
real Gemini output and MUST be handled:

1. **`rowspan` in headers.** The contract says "no rowspan", but `gemini-2.5-flash` emitted
   `<th rowspan="2">ASF Item</th>` / `Weighted value`. The loader must tolerate header
   `rowspan` (expand into the flat grid) rather than assume it is absent. (3.5-flash may
   comply; the loader must not depend on it.)
2. **Unheadered leading line-number column.** Data rows had **7** `<td>` (`line_no, label,
   4 buckets, weighted`) while the header declared **6** columns — so `Σcolspan ==
   header-cols` *fails*. The loader detects a leading all-numeric first column with no
   matching header and maps it to `row_dim.line_no`, excluding it from the data-column
   count before validating spans.

Plus the Plan 9 §5.5 safety nets: `is_shade=1` only for grey `#D3D3D3`/near-grey (other
colours dropped as decorative); drop reference-letter header rows (all single/bracketed
letters); `4a/5a` line-number suffix as a hierarchy cue; `data-kind="total"` is style-driven,
so gate aggregation on unit/`concept_key`, not `data-kind` alone.

**Validation** (Plan 9): each data row's `Σ(td colspan)` (excluding line-no col) == leaf
column count; else route to review.

## 4. Components

1. **`html_to_cells(html) -> {table_t, col_dim[], row_dim[], cell_fact[]}`** — the
   deterministic loader above. Re-seeds the lost HTML→DB parser; reusable beyond the spike.
2. **`GeminiExtractor`** — one API call per table (`gemini-3.5-flash`, `temperature=0`,
   PDF as `application/pdf` Part, `response_mime_type="text/plain"`, Appendix A Core +
   framing **verbatim**). Auth via `genai.Client()` reading `DELIVERABLE/pillar3/.env`.
   Mirrors `pass2/extract.py` exactly except output is HTML, not JSON.
3. **`MinerUExtractor`** — MinerU → HTML table(s), remapped to the contract; then a
   **pdfplumber overlay** stamps `style="background-color:#D3D3D3;"` (grey rect-fill ∩ cell
   bbox) and `data-level` (x0 indent cluster) onto MinerU's cells.
4. **Comparison harness** — load both backends' HTML via the *same* `html_to_cells` into a
   scratch `schema_v5` sqlite; diff on the 5 hard fields (headers, colspan, shading, dashes,
   row level); spot-check hard cells against the PDF by eye (old `parsed.json` is the wrong-
   schema baseline and not trusted).
5. **Scorecard** — table-class × field × {MinerU-alone, MinerU+pdfplumber, Gemini} →
   drop/reduce/keep recommendation + token deltas.

## 5. Test set

| Table | Input | Why |
|---|---|---|
| `ocbc_nsfr` | `DELIVERABLE/OCBC_NSFR.pdf` (3pp) | clean NSFR grid + grey shading + 3-level hierarchy; real Gemini sample already captured |
| `9.3` | `…/ocbc_4Q25/audit/OCBC_4Q25_Pillar 3/9_3_p29-33/pages.pdf` (pre-sliced, 5pp) | was quarantined once → ragged/hard |
| `12.9` | slice `UOB_4Q25_Pillar 3.pdf` pp.38–41 | 14 asset-class blocks × 2 periods — the multi-table / chunking stress case Plan 9 calls out |

## 6. Out of scope (YAGNI)
Full-document orchestration (Stage 1 discovery / Stage 5 stitching), all table types, the
concept review loop, schema changes, production wiring. Just the 3-table eval + the loader.

## 7. Success criteria
A written scorecard + per-table-class recommendation (e.g. "MinerU+pdfplumber sufficient for
NSFR/KM grid tables; keep Gemini for ragged IRBA/12.9"), backed by token deltas vs the
Gemini baseline (§4 yardstick: ~5k output tokens for NSFR×2 periods).

## 8. Risks
- MinerU install is heavy (torch + model weights, CPU inference on this Mac, first-run
  download). Mitigation: install once, cache models.
- MinerU page-numbering / continued-table stitching quirks on multi-page units (9.3, 12.9).
- `gemini-3.5-flash` 503 saturation (seen during the probe); fall back / retry for the
  Gemini baseline, but the *quality* comparison must use 3.5-flash, not 2.5.
- "Truth" is defined by eye for 3 tables — acceptable for a spike, not for production.

## 9. Build order
1. `html_to_cells` + the defensive rules, tested against the saved Gemini samples
   (`samples/gemini_ocbc_nsfr.html`, `samples/gemini35_ocbc_nsfr.html`) → schema_v5 cells. **DONE.**
2. `GeminiExtractor` (3.5-flash) for all three tables → HTML baselines.
3. Install MinerU; `MinerUExtractor` + pdfplumber overlay.
4. Harness + scorecard → the decision.

## 10. Step-1 findings (2026-06-29) — empirical, from real API calls

Captured two real `OCBC_NSFR.pdf` extractions (Appendix A Core + SPANNING, verbatim).
`html_to_cells` + `test_html_to_cells.py` pass 18/18 against **both**.

**The same prompt yields divergent HTML across models** — the parser must absorb this
(deterministic-code-over-prompt, per Plan 9). Defensive rules now in `html_to_cells`:

| Aspect | gemini-2.5-flash | gemini-3.5-flash | parser handles |
|---|---|---|---|
| `data-level` | on `<tr>` (per contract) | on the **first `<td>`** | read from either |
| line number | separate first `<td>` column | **prefixed into label text** (`"2 Regulatory capital"`) | both (column or prefix) |
| header merges | `rowspan` (contract violation) | flat `<th></th>` (correct) | tolerate rowspan |
| section band ("RSF Item") | `<td>` row | **`<th colspan>` row inside tbody** | both |
| shading (`#D3D3D3`) | **2 cells** (missed most) | **34 markers / 16 parsed cells** | grey-only safety net (`#F5F5F5` band excluded) |
| value quality | duplicated row 2 → row 3 | row 3 correct (`-`) | n/a (model choice) |

**Token cost (NSFR × 2 periods), the MinerU yardstick:**
- 2.5-flash: 1,523 in / 5,068 out / 8,190 think / 14,781 total
- 3.5-flash: 2,345 in / 10,580 out / 13,317 think / 26,242 total

**Implication:** 3.5-flash is materially better on the two hard fields (shading, header
flatness) and on value accuracy — the *quality* baseline must be 3.5, not 2.5. The prompt
could optionally pin `data-level` placement and line-number-as-column to cut divergence,
but the parser already absorbs both, so this is optional.
