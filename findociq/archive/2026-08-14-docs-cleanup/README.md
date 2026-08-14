# 2026-08-14 — docs cleanup

## What moved here

`reg-fold-collision-report.md` (86 KB) — a point-in-time collision report with
**zero inbound references** from any live document, spec or code path. Archived
on its own merits, not as a duplicate.

## What was DELETED rather than archived, and why that was safe

39 files were removed from `findociq/docs/` in the same commit and are **not**
copied here, because they were already sitting in
`findociq/archive/2026-08-12-docs-cleanup/` — byte-identical, verified with
`cmp` file by file before removal:

- 27 markdown documents (the m2/m3 OCBC reports, `ingest-inventory.md`,
  `six-bug-diagnosis.md`, `runbook-execution-2026-08-04.md`, the 2026-07-31
  tagging and key-field notes, `2026-07-29-tag-workbook-design.md`, and the
  `findings/`, `plans/` and `superpowers/` trees)
- 12 diagram assets under `diagrams/` (PNG, SVG, MMD)

They existed twice because the 2026-08-12 cleanup MOVED them here, and the later
"rescue 49 files that existed only on unmerged branches" commit restored them to
`docs/` without noticing they were already archived. Copying them a second time
would have re-created the same problem one directory over.

## The redirect

`findociq/docs/README.md` carries the pointer: any `docs/<name>.md` reference
that no longer resolves is in `2026-08-12-docs-cleanup/`. `DECISIONS.md` and
`PROGRESS.md` still cite the old paths on purpose — they are logs, and rewriting
old entries to chase a file move would destroy the record they exist to keep.

## Also in this commit

`findociq/docs/Techreport/` moved to `Techreport/` at the repo root, beside
`README.md`. It is the deliverable; it should not be three directories down.
`README.md`'s pointer was updated to match.
