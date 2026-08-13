# Finding: MinerU solves no-TOC table discovery on financial statements (2026-06-29)

## Context
Financial statements have **no TOC / no bookmarks / no contents page** (DBS FS: 0 bookmarks,
tables from page 1). The existing `PASS1_TOC.py` returns nothing for them, and a deterministic
pdfplumber pre-filter has poor recall because FS tables are **borderless**.

## Test
Ran MinerU 3.4.0 (pipeline backend, isolated venv) on `DBS_financialstatement_2025.pdf` (32 pp).
Inspected `content_list.json` + the visual `layout.pdf` overlay.

## Result — MinerU detection is good enough to carry discovery

| Signal | Deterministic pre-filter | MinerU layout |
|---|---|---|
| Table-bearing pages found | 5 / 32 | **29 / 29** (all of them) |
| Tables detected | ~5, weak | 94 blocks, all with HTML bodies |
| Section headings (de-facto TOC) | none (garbled) | **177 leveled, numbered** (`1.`, `2.1`, …) |
| Borderless tables | missed | caught |

- The 3 table-less pages (4, 5, 7) are **correctly** pure accounting-policy prose — not misses.
- Visual overlay confirmed (user review): cleanly boxes every relevant table per page, including
  **multiple tables sharing one page**.
- Headings form a real hierarchical TOC with page numbers — exactly what FS lack.

## Caveats / remaining work (reconcile, not detection)
- `table_caption` comes back **empty** → titles must be taken from the nearest preceding heading
  (MinerU provides these). Filter footnote-like headings (e.g. `# Amounts under $500,000`).
- **Cross-page continuation**: a table split across a page break is detected as two blocks →
  stitch in reconcile (columns resume, no new heading).
- Validated on one clean digital FS (32 pp); scanned / very long docs still to test.

## Decision
**MinerU is the Stage-1 detector for no-TOC docs** (and the TOC cross-check for Pillar 3).
pdfplumber stays as the zero-dependency fast fallback / pre-filter. Detection recall is
effectively complete; the remaining work is the deterministic **reconcile** step (title from
heading + continuation stitch) → manifest (`table_t` rows). MinerU runs in `.venv-mineru`
(isolated; base env has a numpy/TF ABI conflict).
