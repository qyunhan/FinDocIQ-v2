# findociq/docs — what is here, and where the history went

## Live documents

| Path | What it is |
|---|---|
| `DECISIONS.md` | Decision log, newest on top. Every consequential choice, **why**, and what was tried and discarded with the evidence. Append here, never rewrite. |
| `TO_FIX.md` | Known open defects and accepted shortcuts. |
| `specs/` | The 36 design specs the pipeline is built against. Referenced directly from code — treat as live. |
| `architecture/00-overview.md` | Entry point for a new team member: PDF → dashboard cell. Written 2026-08-04; §4 is a point-in-time snapshot. |
| `2026-07-24-ingest-handoff.md` | Ingest state and the PaddleOCR STEP 0 workaround. Named in `CLAUDE.md`. |
| `2026-07-30-workstation-resume-handoff.md` | Resuming on a rebuilt workstation. Named in `CLAUDE.md`. |
| `workstation-persistence.md` | What survives a workstation restart, and the bootstrap order. Named in `CLAUDE.md`. |
| `workstation-setup.md` | First-time environment setup. |
| `plan/FinDocIQ_Plan_9.docx` | Original planning deck. Not maintained. |

The **technical report** moved to `Techreport/` at the REPO ROOT (2026-08-14),
next to `README.md`, because it is the deliverable a reader should find first
rather than three directories down. Appendix D there is the prioritised
masterlist worklist.

## Where the history went

`docs/` had accumulated **39 files that were byte-identical copies of documents
already archived** on 2026-08-12 — the 2026-08-12 cleanup moved them, and a
later branch-rescue commit restored them alongside their own archive copies. The
duplicates were removed on 2026-08-14; nothing was lost.

**Any reference of the form `docs/<name>.md` that no longer resolves is in
`findociq/archive/2026-08-12-docs-cleanup/`.** That covers the m2/m3 OCBC
reports, `ingest-inventory.md`, `six-bug-diagnosis.md`,
`runbook-execution-2026-08-04.md`, `2026-07-29-tag-workbook-design.md`, the
2026-07-31 tagging/key-field notes, `findings/`, `plans/`, `superpowers/` and
the `diagrams/` assets.

`DECISIONS.md` and `PROGRESS.md` still cite those paths as they stood when the
entries were written. That is deliberate: a log records what was true at the
time, and rewriting old entries to chase a move would destroy the one property
that makes a log worth keeping. Use this section as the redirect.

One document was archived on its own merits rather than as a duplicate:
`reg-fold-collision-report.md` (86 KB, zero inbound references) is in
`findociq/archive/2026-08-14-docs-cleanup/`.

## Adding to this directory

A doc earns a place here if it is **live** — something a reader needs to act on
today. A point-in-time report of work already finished belongs in `PROGRESS.md`
as a summary, with the report itself under `findociq/archive/<date>-<topic>/`.
Keeping finished reports in `docs/` is how this directory reached 1.7 MB with a
third of it duplicated.
