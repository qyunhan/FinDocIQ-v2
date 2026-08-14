# FinancialParser — Claude Code instructions

> **Resuming work?** Read `findociq/docs/2026-07-24-ingest-handoff.md` first — it
> has the current 2025/2026-Q1 ingest state, deployments, and the PaddleOCR
> STEP 0 blocker + how to work around it.

## Multi-agent orchestration

The main session (Fable, high effort — set in `.claude/settings.json`) is the **orchestrator**. Its job is planning, decomposition, delegation, and synthesis — not doing every subtask inline.

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

## Persistence — git is the only durable store

**GCP was retired in August 2026.** There is no GCS bucket and no BigQuery
dataset to fall back on: whatever is not pushed to GitHub does not exist. The
machine this runs on may be rebuilt without warning.

- **Commit + push after every major change** (feature, fix, spec/doc) with an
  explicit pathspec — never end a session with meaningful work unpushed, and
  never `git commit -am` blindly (the tree often has parallel WIP). **In the same
  commit, update `findociq/PROGRESS.md`** (newest-on-top session block) so the
  running log always reflects the latest state.
- **Keep a decision log.** For any consequential decision, append to
  `findociq/docs/DECISIONS.md` (newest-on-top): the change, **why**, and anything
  **tried-and-discarded with the evidence** for rejecting it (a command output, a
  `file:line`, a measured fact). This is toward a full project writeup — capture
  rationale and dead ends, not just outcomes.
- **Both databases are committed** (`compiled_fs.db` 31 MB, `compiled_v2.db`
  10 MB). After a rebuild or re-stamp, commit them — that IS the persistence
  step now, in place of the old `gsutil cp`.
- **Publishing the dashboard is a SECOND repo.** `qyunhan/Findociq-Dashboard` is
  what Streamlit Community Cloud builds; pushing here changes nothing on the
  live site. Run its `sync.sh` and push that repo too. See `README.md`.
- **Do not add new GCP dependencies.** `sync_bq.py`, `source_store.py` (GCS) and
  the app's BigQuery backend are retired paths kept only so old code still
  imports; nothing in the working path may rely on them.
