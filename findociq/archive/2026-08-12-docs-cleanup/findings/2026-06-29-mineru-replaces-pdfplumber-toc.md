# Finding: MinerU is the universal Stage-1 discovery engine (replaces pdfplumber TOC + Gemini table-count)

**Date:** 2026-06-29

## The question
Current discovery = pdfplumber parses the *contents page* → section→page map, then **Gemini**
counts tables per page. It breaks on documents with no contents page (financial statements).
Can MinerU give the section tree directly — well enough to replace pdfplumber on Pillar 3 too,
and work on *all* documents?

## Head-to-head

| Capability | pdfplumber `PASS1_TOC` | Gemini table-count | **MinerU** |
|---|---|---|---|
| Section tree on Pillar 3 | ✅ 64 sections | — | ✅ **~64/64** (parts + dotted numbering + page) |
| Captures `PART A/B/C` + `12.2.5` numbering | ✅ | — | ✅ verbatim printed headings |
| Multi-page section continuations | ⚠️ inferred | — | ✅ flags `(continued)` |
| Works with **NO contents page** (financial stmts) | ❌ returns nothing | — | ✅ builds tree from detected headings |
| **Per-page table detection** | ❌ | ✅ (costs tokens) | ✅ zero-token |
| Retains cell **hierarchy** on extraction | n/a | n/a | ❌ flattens (no row-levels/shading) |
| LLM tokens | 0 | per-page | **0** |

## Evidence
- **DBS 4Q25 Pillar 3 (92 pp):** MinerU section tree matched all 64 `PASS1_TOC` sections
  (e.g. `p41 12.2.5 SA(CR) – Credit Risk…`, `p42–44 12.2.6 …`, `p58 13.2.3 …`), correct pages,
  PART structure intact; detected **92 tables across 69 pages** — also replacing the Gemini
  table-count. (The "3 misses" in a first pass were an em-dash matching artifact, not real.)
- **DBS financial statement (32 pp, no TOC):** 29/29 table-bearing pages, 177 leveled headings —
  where `PASS1_TOC` returns nothing.

## Decision
- **MinerU = the single Stage-1 discovery engine** for every document type: section tree +
  per-page table detection, fully offline (zero LLM tokens).
- **pdfplumber** demoted to a fast fallback / sanity cross-check.
- **Gemini table-count is removed** from Stage 1 (MinerU does it).
- **Gemini stays for Stage-2 extraction only** — MinerU flattens hierarchy (no `data-level`,
  no shading; see [2026-06-29-mineru-detection-on-financial-statements]), which `schema_v5`
  needs, so precise extraction remains on the Gemini-HTML path.

## Remaining work (reconcile, not detection)
Title from nearest heading (filter footnote-like headings); stitch cross-page continuations;
MinerU runs in isolated `.venv-mineru` (base env has a numpy/TF ABI conflict).
