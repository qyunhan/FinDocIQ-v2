> **START HERE:** read `HANDOFF.md` (repo root) for current state, GCP setup, and prioritized next steps before doing anything.

# FinancialParser — Codex instructions

## Multi-agent orchestration

The main session (Fable, high effort — set in `.Codex/settings.json`) is the **orchestrator**. Its job is planning, decomposition, delegation, and synthesis — not doing every subtask inline.

For any non-trivial task:

1. **Plan** — break the task into phases; classify each phase as *reasoning-heavy* or *mechanical*.
2. **Delegate** via the Agent tool:
   - `deep-reasoner` (Opus) — reasoning-heavy phases only: subtle-bug root cause, algorithm/design decisions, correctness review, ambiguous table/spec semantics. It returns a concise conclusion for you to act on.
   - `fast-worker` (Sonnet) — mechanical execution: boilerplate, repetitive edits, scaffolding, writing/running tests against a decided spec, renames.
3. **Parallelize** — launch independent delegations in a single message so they run concurrently; keep dependent phases sequential.
4. **Synthesize** — integrating results, resolving conflicts between agent outputs, and final judgment stay in the orchestrator. Do not delegate synthesis.

Keep in the orchestrator (don't delegate): trivial one-file edits, decisions requiring full conversation context, anything faster to do than to specify.

Effort note: `effortLevel` in settings.json persists up to `xhigh` (the schema's maximum); use `/effort` in-session if a higher level is available on your build.

## Project purpose — humans OUT of the loop

The end state of this project is a pipeline that runs with **no human in the loop**. Every change must move toward that, never away from it:

- **No overfitting.** Never fix a document-specific symptom with a document-specific hack. If one bank renders a table differently, the fix is a general, deterministic signal or rule in the router (e.g. the coverage-gated classifier) that would work for a bank we've never seen — not a per-bank/per-document conditional. Per-source special cases are a smell.
- **Template authoring is the ONLY manual step currently tolerated** (official notice PDF → registry seed → aliases). Treat it as debt: design toward automating it (notice → seed generation), don't entrench it. Everything else — routing, framing, extraction, verification, alignment, stamping, drift queueing — must be code-decided.
- **Every prompt change is a PIPELINE change.** Prompts live in `findociq/pipeline/prompts/` and are selected by the router. Never hand-tune a prompt for one run or paste one-off instructions into an extraction call. If a case needs different prompting, that is a routing branch: add the branch AND the prompt file, and the router must pick it deterministically for every future document that matches.
- **Decision-tree pivots must be visible.** Any change to the routing decision tree — a prompt split, a new page class, a new branch/command — must be (1) recorded in the routing spec under `findociq/docs/specs/`, (2) observable in the route manifest / `route_map.html` output so a human can SEE which branch fired for which page without reading code, and (3) called out explicitly to the user when it happens ("pipeline pivot: …"), not buried in a diff.
