# FinDocIQ — architecture overview

Reference document. Entry point for a new team member. Describes the data path
from PDF to dashboard cell, the three mappings that path depends on, and what is
built versus not built today.

Written 2026-08-04. Section 4 is a point-in-time snapshot; check it against the
database before relying on it.

---

## 1. Purpose and scope

FinDocIQ extracts structured financial data from Singapore bank regulatory PDFs
(DBS, OCBC, UOB) into a queryable database serving the 26-item Key Performance
Highlights dashboard. Disclosure families in scope: Financial Statements (FS) and
Pillar 3 regulatory disclosures (P3). A dashboard cell is only trustworthy if
three separate mappings each hold; they are defined in section 2, and each is
verified by its own artifact rather than by one system-wide health number.

---

## 2. The three mappings

### M1 — Faithful transcription

**Definition.** The extracted JSON matches the PDF. Owned by Stage 1 (Gemini +
PaddleOCR).

**Truth criterion.** The PDF. M1 has no other referent — no registry, no prior
period, no other bank.

**Failure modes.** Misread values. Wrong column assignment. Merged or lost rows.
Missing cells. Also: one physical table extracted as several `table_t` rows (see
section 7, OCBC `media_release_financial_highlights`).

**Verification artifact.** Post-load verification gate, `outputs/checks/`. See
`../specs/2026-07-06-post-load-verification-gate.md`.

**Current automation state.** Automated, runs per document in `run_doc.py`.

---

### M2 — Per-bank identity persistence

**Definition.** The masterlist declares, for each (bank, table_type), an
enumerated ORDERED set of canonical leaves. Every ingested row must resolve to
exactly one canonical leaf (direct match or via alias) or the ingest gate flags
it as unresolved.

M2 promises:

1. For each (bank, table_type) there is an enumerated, ordered set of canonical
   leaves.
2. Every row resolves to exactly one canonical leaf or fails the gate.
3. Canonical leaves are versioned; disclosure restructures use deprecation +
   bridging aliases.
4. Order is meaningful and preserved end-to-end.

M2 does NOT promise:

- Cross-bank comparability (M3's job).
- That the same table_type across banks contains the same leaves.

**Truth criterion.** The bank's own prior disclosure of the same table. A row is
correct under M2 if it lands on the same canonical leaf it landed on last
quarter, or on a leaf explicitly bridged to it by an alias.

**Failure modes.** A relabelled row silently becoming a new leaf. Two distinct
rows collapsing onto one leaf. Order drift, which breaks any consumer reading by
position. Genuine disclosure restructure mistaken for a matching bug — and the
reverse.

**Verification artifact.** `resolve_address()` in
`../../pipeline/mapping/m2_canonical_leaf.py`, plus the generated reports
`../m2-ocbc-canonical-report.md` and `../m2-ocbc-unresolved-rows.md`.

**Current automation state.** Warn-only, OCBC only. The gate function exists and
is tested. No ingest call site calls it, so nothing is blocked today. See
section 4.

---

### M3 — Cross-bank concept equivalence

**Definition.** `lineage_identity_map` binds concept_key → (bank,
canonical_leaf). Concept bindings inherit stability from M2.

M3 promises:

1. For each concept_key, at most one canonical leaf per bank is bound as source
   of truth.
2. Cross-bank comparability is guaranteed only for values pulled via
   concept_key.
3. Concept definitions are documented — economic meaning, bank-specific caveats,
   normalization if any.

M3 does NOT promise:

- That every canonical leaf has a concept binding.
- That every concept has a binding for every bank.

**Truth criterion.** Economic equivalence across banks, as documented per
concept. Not label similarity.

**Failure modes.** Two banks' rows bound to one concept_key that do not mean the
same thing. A binding pointing at a leaf that M2 no longer resolves — the
binding is then dead, and the dashboard cell is empty or stale. Reported versus
underlying basis mixed within one concept.

**Verification artifact.** `verify_concept_bindings()` in
`../../pipeline/mapping/m2_canonical_leaf.py`, output at
`../m3-ocbc-concept-binding-check.md`.

**Current automation state.** Checked, not enforced. Two stores are involved and
they are not the same thing:

- `data/derived/lineage_identity_map.csv` (94 rows) — the authored map. Records
  per concept_key and bank whether the value is an `anchor` (a real printed row)
  or `derived` (computed from other concepts), with the reasoning.
- `bank_line_map.concept_key` — the live binding the pipeline actually reads.
  240 bindings for OCBC, of which 99 currently resolve to a canonical leaf.

---

## 3. Data path

Provenance chain for any dashboard cell, and the mapping that owns each hop:

```
concept_key
    │  M3 — cross-bank concept equivalence
    ▼
bank
    │  M3
    ▼
canonical_leaf
    │  M2 — per-bank identity persistence
    ▼
address  (table_type_id, parent_label_norm, row_label_norm)
    │  M2
    ▼
PDF page + coordinates
    │  M1 — faithful transcription
    ▼
cell value
```

| Hop | Owner | Store |
|---|---|---|
| concept_key → bank | M3 | `lineage_identity_map.csv`, `bank_line_map.concept_key` |
| bank → canonical_leaf | M3 | `bank_line_map.concept_key` |
| canonical_leaf → address | M2 | `canonical_leaf`, `canonical_leaf_alias` |
| address → PDF page + coordinates | M2 | `row_dim`, `col_dim`, `table_t` |
| PDF page + coordinates → cell value | M1 | `cell_fact` |

A break at any hop presents identically at the dashboard: a wrong number or a
blank cell. Identifying which hop broke is the first step of every such
investigation.

---

## 4. Current storage state (as of this document's creation)

| Bank | Canonical set storage | Status |
|------|----------------------|--------|
| DBS  | Not persisted — computed live in `app/findociq_app.py:370` (`line_item_benchmark_frame`) from `row_dim` @ `BENCHMARK_PERIOD='2025-12-31'`, enriched via `bank_line_map` | Needs M2 gate migration |
| OCBC | `canonical_leaf` + `canonical_leaf_alias` (persisted this session) — 13 table_types, 364 canonical leaves | M2 gate built |
| UOB  | Not persisted — same live-computation pattern as DBS | Needs M2 gate build |

Note the OCBC M2 gate flagged 281/608 unresolved `fact_metric` rows and 141/240
unresolved concept bindings; these are open, not closed. See
`../m2-ocbc-unresolved-rows.md` and `../m3-ocbc-concept-binding-check.md`.

Two clarifications on the DBS and UOB rows. First, the live computation is a
*rendering*, not a gate: it produces the leaf set for display and discards it, so
no row is ever checked against a declared set. Second, `bank_line_map` is not a
substitute — it is period-agnostic and additive, accumulating every address ever
seen since 2022, so it cannot answer "is this row supposed to exist." Measured:
DBS `FS_PER_SHARE` holds 12 accumulated addresses for a table that prints 7 rows.

Where the masterlist is stored, and the one-writer-per-level rule, is specified
in `../specs/2026-08-04-masterlist.md`.

---

## 5. Layer separation rationale

Each mapping is independently verifiable, independently breakable, and
independently repairable. Most "the dashboard is wrong" bugs require identifying
which mapping actually broke: a misread figure (M1), a row that stopped matching
its prior identity (M2), and a concept bound to the wrong row in one bank (M3)
all surface as the same symptom but have nothing in common as repairs. That is
why each mapping has its own verification artifact rather than one system-wide
health metric — an aggregate number tells you something is wrong without telling
you which of three unrelated things to fix, and a repair aimed at the wrong layer
usually adds a compensating error rather than removing one.

---

## 6. Related documents

- `../mappings/M1-transcription.md` (planned)
- `../mappings/M2-bank-identity.md` (planned)
- `../mappings/M3-concept-equivalence.md` (planned)
- `../DECISIONS.md` — decision log, newest on top. (The task specification for
  this document cited `docs/decisions/DECISIONS.md`; that directory does not
  exist. The file is at `docs/DECISIONS.md`.)
- `../m2-ocbc-canonical-report.md`
- `../m2-ocbc-unresolved-rows.md`
- `../m3-ocbc-concept-binding-check.md`
- `../../PIPELINE.md` — end-to-end run instructions. Exists; not planned.
- `../specs/2026-08-04-masterlist.md` — masterlist definition, storage, and the
  one-writer-per-level rule.
- `../specs/MAPPING_LAYER.md` — `table_registry` and `bank_line_map` schema.

---

## 7. Open questions

- DBS and UOB canonical sets are not persisted; live-computation at render time
  means no explicit M2 gate for these banks yet.
- OCBC has 141 broken concept bindings including `bs.nav_per_share` with zero
  resolving bindings. This is a live dashboard risk, not a conditional one:
  `bs.nav_per_share` is in the 26-item KPH set (`app/highlights.yaml:228`) and is
  mapped for all three banks in `pipeline/mapping/dashboard_rows.yaml` (lines 81,
  112, 156). The OCBC binding is the one with no resolving leaf.
- Disclosure family (FS vs P3) is not yet an explicit column on the table_type
  registry. Scope is currently enforced by query filters on
  `document.doc_family`, which is a weaker guarantee than a registry column.
- Alias resolution priority — exact current label > alias table > deprecated leaf
  label > unresolved — is documented for OCBC in `../DECISIONS.md` but the same
  rules must apply for DBS and UOB when their gates are built.
- `table_id` is not unique across the corpus; canonical join key is
  `(doc_id, table_id)`. See `../DECISIONS.md`. This was a live bug, not a
  hypothetical: a `LIMIT 1` lookup on a bare `table_id` quarantined the wrong
  table.
- Extraction root cause for the OCBC `media_release_financial_highlights` split
  remains open; dedup quarantine is a stopgap. 17 clusters, 49 of 375 `table_t`
  rows, isolated to that one doc_kind.
- M2's promise 3 (versioning via deprecation + bridging aliases) is structurally
  present — `canonical_leaf.added_quarter` / `deprecated_quarter` and
  `canonical_leaf_alias` exist — but untested against a real restructure. OCBC's
  build produced 0 auto-aliases, so the bridging path has no live exercise yet.
