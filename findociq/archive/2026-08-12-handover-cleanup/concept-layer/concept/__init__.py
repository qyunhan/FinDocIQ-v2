"""concept — the CONCEPT RESOLUTION LAYER.

Deterministic-first, LLM-assisted-residue pipeline that stamps
row_dim.concept_key from the curated concept_dictionary.yaml. Cells inherit the
concept through v_cell / v_cell_flat (COALESCE(row, cell)).

Modules (run in order by run.py):
  normalize            norm(label) — footnote/punctuation/whitespace normalisation
  load_dictionary      YAML -> concept_map wildcard rows (+ schema migration)
  resolve_deterministic  exact norm match -> stamp + audit log
  resolve_llm          residue only -> Gemini enum-constrained classifier
  validate             formula / uniqueness / sums_to reconciliation gate
  run                  orchestrator (2->3->4->5), coverage summary
"""
