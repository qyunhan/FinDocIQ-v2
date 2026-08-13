# Knowledge-graph projection — design (2026-07-17)

**Status:** design / queued (build AFTER `fact_metric`). GCP-native.

## Question

Can users search the verified DB and retrieve **via a graph** — traverse from a
metric to its drivers, compare banks, ask in natural language — and is
`schema_v7` the right base for it? Yes to both. This spec defines the
`schema_v7` → knowledge-graph **projection** (no re-model — a view over what
exists), where the `concept_dictionary` formulas become semantic edges, and how
to serve it on GCP without breaking the golden rule (numbers come from SQL, never
the LLM).

## Why `schema_v7` is already graph-ready

The hardest part of a knowledge graph — **entity resolution** — is already solved:
- **`concept_map`** unifies "Net interest income" across DBS/OCBC/UOB into one
  `concept_key`. That is one canonical **Concept node** with many bank edges, not
  three look-alike nodes.
- **Provenance** exists end-to-end (`row_lineage`/`col_lineage`, `page_range`,
  `verify_cells`) → every fact node can carry a **cite-able source edge**.
- Hierarchies are already explicit: `section.parent_section`, `geo_dim`,
  `segment_dim`, `sums_to` decomposition.

So this is a **projection**, not a migration.

## Node model (source table → node)

| Node | Source | Key |
|---|---|---|
| `Institution` | `document.institution` (registry) | institution |
| `Concept` | `concept_map` (canonical) | concept_key |
| `Metric` (a value) | **`fact_metric`** | (institution, concept, period, span, segment, geo) |
| `Period` | `table_t.period` + `period_span` | period, span |
| `Segment` | `segment_dim` | segment_key |
| `Geo` | `geo_dim` | geo_key |
| `Document` / `Section` / `Table` | `document` / `section` / `table_t` | ids |
| `SourcePage` | `table_t.page_range`, lineage | doc_id + page |

`Metric` nodes come from **`fact_metric`, not raw `v_cell_flat`** — raw facts have
sign/rounding/duplicate twins that would create ambiguous nodes. This is why the
graph is gated on `fact_metric`.

## Edge model (relationship → edge)

Structural edges (mechanical, from FKs):
- `(Institution)-[:REPORTS]->(Metric)`
- `(Metric)-[:OF_CONCEPT]->(Concept)`, `-[:FOR_PERIOD]->(Period)`,
  `-[:IN_SEGMENT]->(Segment)`, `-[:IN_GEO]->(Geo)`
- `(Metric)-[:SOURCED_FROM]->(SourcePage)` — the trust/citation edge
- `(Geo)-[:WITHIN]->(Geo)`, `(Segment)-[:WITHIN]->(Segment)`,
  `(Section)-[:PARENT_OF]->(Section)`

**Semantic edges (the reason it's a *knowledge* graph, not a table):**
- `(Concept)-[:PART_OF]->(Concept)` — decomposition, from `sums_to` + section/row
  hierarchy (e.g. `net_interest_income` PART_OF `total_income`).
- `(Concept)-[:DERIVES_FROM {formula}]->(Concept…)` — **materialized from
  `concept_dictionary.yaml`**. The ROE/NIM/CIR formulas already written there ARE
  these edges (e.g. `ROE --DERIVES_FROM--> net_profit, ordinary_equity`). Emitting
  them as edges is what lets a user ask "what drives ROE" and have the graph walk
  to the components.

Cross-bank comparison needs **no edge** — because `concept_map` already collapses
banks onto one `Concept`, "compare DBS vs OCBC NIM" is just the `REPORTS` edges
into one `Concept` node. That is the payoff of the existing entity resolution.

## Retrieval flow: natural query → grounded answer

Adopts the GCP-side articulation, harmonized with the golden rule (LLM/graph picks
*which* facts; the database returns *what* the number is). Worked example —
*"Show me the trend of UOB's Net Interest Income compared to its peers over the
last two years."*

1. **Parse intent — Gemini (Vertex).** Gemini maps `"Net Interest Income"` →
   `concept_key` via `concept_map`, `"UOB"` → institution, `"peers"` →
   {DBS, OCBC}, `"last two years"` → the `Period` set. It resolves entities to
   keys; it does not invent numbers. (Peer comparison is *free*: same `Concept`
   node, other `REPORTS` edges — the `concept_map` collapse at work.)
2. **Retrieve — BigQuery SQL *or* Spanner Graph GQL/Cypher.**
   - *BigQuery path:* Gemini generates SQL — but against **`fact_metric`, NOT raw
     `v_cell_flat`**. Raw has sign/rounding/duplicate twins; the LLM would return
     conflicting values. `fact_metric` = one canonical, verified row per key.
   - *Graph path:* Gemini writes GQL/Cypher over Spanner Graph, following
     `OF_CONCEPT` → `DERIVES_FROM`/`PART_OF` to pull the metric **and its context**
     (Operating Expenses, Total Income) — the "follow the edges" advantage over
     isolated rows.
3. **Subgraph + citations.** Retrieval returns a subgraph: the values plus their
   `SOURCED_FROM` parents (document + page, via `page_range`/lineage) — so the
   answer can say *"per OCBC 4Q25 condensed statements, p11 …"*. Provenance is an
   edge, not an afterthought.
4. **Ground + narrate — Gemini.** Composes the answer strictly from the retrieved
   subgraph, with citations. Narrates; never supplies a number not in the rows.

**The one guardrail that makes this safe:** the LLM *writes* the query, the
database *returns* the numbers — but only over the canonical, verified
`fact_metric`. Keep the existing **router→analyst 2-step** (or a query validator)
so a malformed generated query **fails loud** instead of returning a
plausible-but-wrong value. "Numbers from SQL, never the LLM" holds *only if the SQL
targets the clean table* — this is exactly why `fact_metric` is the gate.

The graph replaces "which numbers?" (today's router step); it does **not** replace
"what is the number?" (still SQL, still `fact_metric`).

## Serving on GCP: Spanner Graph vs GraphRAG — build once, serve two ways

They are complementary, not either/or. Build the node/edge **projection once** (in
BigQuery, via Dataform); serve it by purpose:

| Purpose | Tool | Why |
|---|---|---|
| **Deterministic traversal** (Comparison tab, "ROE → drivers → values") | **Spanner Graph** (property graph, GQL) — or BigQuery graph queries if scale is modest | exact, fast multi-hop; extends the existing SQL analyst |
| **Natural-language chat retrieval** | **GraphRAG on Vertex** — graph traversal + **Vertex AI embeddings + Vector Search** over concept descriptions | maps a fuzzy question to the right subgraph, then hands keys to SQL |

**Recommendation:** start with the **BigQuery projection** (cheapest, native,
already where `fact_metric` lives, buildable in Dataform). Add **Spanner Graph**
only if traversal performance demands a native graph engine. Layer **GraphRAG**
(embeddings on `Concept`/description nodes) for the chat tab. In all cases numbers
resolve back to `fact_metric` via SQL.

## Build with GCP-native tools (and record its lineage)

- **Dataform** — define every node/edge table as versioned SQLX with `assertions`;
  its dependency graph *is* the build lineage (see `GCP_TASKS.md` Task 3).
- **BigQuery** — home of the projection tables (+ `fact_metric`).
- **Vertex AI** — embeddings + Vector Search (GraphRAG); Gemini for narration.
- **Spanner Graph** — optional native traversal engine.
- **Dataplex / BigQuery data lineage** — the graph tables appear in the same
  estate lineage as everything else; no separate tracking.

## Prerequisites & ordering

1. **`fact_metric`** (blocks clean `Metric` nodes) — the gate.
2. **Concept coverage** — grow beyond ~37 keys; unstamped line items = orphan nodes.
3. **Materialize `concept_dictionary` formulas** as `DERIVES_FROM` edges.
4. Then: projection (Dataform) → optional Spanner Graph / GraphRAG (Vertex).

## Out of scope / open questions

- Temporal edges (a metric's trend across quarters) — derivable from `Period`
  nodes; defer until the base graph works.
- Whether `PART_OF` should come from `sums_to` (arithmetic-verified) only, or also
  from section nesting (structural) — start with `sums_to` (trustworthy), add
  structural later.
