# Archived docs — 2026-08-12

Point-in-time documents moved out of `findociq/docs/` so that what remains is
only what is still LOAD-BEARING. Nothing here was deleted; every file is intact
at the path below and in git history.

## What stayed in docs/, and why

    specs/ (34)                    cited from code docstrings 30+ times; CLAUDE.md
                                   requires routing-tree changes recorded here
    DECISIONS.md                   mandated by CLAUDE.md; 35 inbound references
    TO_FIX.md                      the live defect backlog
    Techreport/                    the deliverable
    2026-07-24-ingest-handoff.md   CLAUDE.md: "Resuming work? Read this first"
    2026-07-30-workstation-resume-handoff.md   CLAUDE.md bootstrap
    workstation-persistence.md     CLAUDE.md bootstrap
    workstation-setup.md           8 inbound references

## Why these left

They are finished work: milestone reports (m2/m3), spike findings, superseded
plans, one-off audits and measurement runs, and presentation diagrams. Each
records something that has since been decided — and the DECISION is in
`DECISIONS.md`, which stays. `diagrams/` moved too: the technical report embeds
zero images, so nothing referenced them.

## Dangling links

`DECISIONS.md` and `PROGRESS.md` cite some of these by their old `docs/...`
path. Those texts were deliberately NOT rewritten — a decision log records what
was true when written, and editing it to tidy a move would be the wrong kind of
change. Use the map below to resolve any such link.

## Old path -> new path

    docs/2026-07-29-dashboard-trigger-pending-access.md             -> archive/2026-08-12-docs-cleanup/2026-07-29-dashboard-trigger-pending-access.md
    docs/2026-07-29-tag-workbook-design.md                          -> archive/2026-08-12-docs-cleanup/2026-07-29-tag-workbook-design.md
    docs/2026-07-30-findociq-app-plan.md                            -> archive/2026-08-12-docs-cleanup/2026-07-30-findociq-app-plan.md
    docs/2026-07-31-4q25-tagging-by-bank.md                         -> archive/2026-08-12-docs-cleanup/2026-07-31-4q25-tagging-by-bank.md
    docs/2026-07-31-dbs-4q25-overview-tagged.md                     -> archive/2026-08-12-docs-cleanup/2026-07-31-dbs-4q25-overview-tagged.md
    docs/2026-07-31-key-field-audit.md                              -> archive/2026-08-12-docs-cleanup/2026-07-31-key-field-audit.md
    docs/2026-07-31-key-field-coverage.md                           -> archive/2026-08-12-docs-cleanup/2026-07-31-key-field-coverage.md
    docs/2026-08-06-masterlist-v3-build-report.md                   -> archive/2026-08-12-docs-cleanup/2026-08-06-masterlist-v3-build-report.md
    docs/2026-08-06-printed-parent-rerun-measurement.md             -> archive/2026-08-12-docs-cleanup/2026-08-06-printed-parent-rerun-measurement.md
    docs/diagrams/2026-07-07-pipeline-workflows.md                  -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-07-pipeline-workflows.md
    docs/diagrams/2026-07-08-management-view.md                     -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-management-view.md
    docs/diagrams/2026-07-08-mgmt-0-overview.png                    -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-mgmt-0-overview.png
    docs/diagrams/2026-07-08-mgmt-1-route.png                       -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-mgmt-1-route.png
    docs/diagrams/2026-07-08-mgmt-2-manifest.png                    -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-mgmt-2-manifest.png
    docs/diagrams/2026-07-08-mgmt-3-extract.png                     -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-mgmt-3-extract.png
    docs/diagrams/2026-07-08-mgmt-4-load-verify-stamp.png           -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-mgmt-4-load-verify-stamp.png
    docs/diagrams/2026-07-08-mgmt-5-gaps.png                        -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-mgmt-5-gaps.png
    docs/diagrams/2026-07-08-pipeline-structure.mmd                 -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-pipeline-structure.mmd
    docs/diagrams/2026-07-08-pipeline-structure.png                 -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-08-pipeline-structure.png
    docs/diagrams/2026-07-09-section-tagging.png                    -> archive/2026-08-12-docs-cleanup/diagrams/2026-07-09-section-tagging.png
    docs/diagrams/chat_with_data.svg                                -> archive/2026-08-12-docs-cleanup/diagrams/chat_with_data.svg
    docs/diagrams/paddleocr_spike.svg                               -> archive/2026-08-12-docs-cleanup/diagrams/paddleocr_spike.svg
    docs/diagrams/pipeline_production.svg                           -> archive/2026-08-12-docs-cleanup/diagrams/pipeline_production.svg
    docs/findings/2026-06-29-gemini-2.5-vs-3.5-html.md              -> archive/2026-08-12-docs-cleanup/findings/2026-06-29-gemini-2.5-vs-3.5-html.md
    docs/findings/2026-06-29-mineru-detection-on-financial-statements.md -> archive/2026-08-12-docs-cleanup/findings/2026-06-29-mineru-detection-on-financial-statements.md
    docs/findings/2026-06-29-mineru-replaces-pdfplumber-toc.md      -> archive/2026-08-12-docs-cleanup/findings/2026-06-29-mineru-replaces-pdfplumber-toc.md
    docs/findings/2026-08-03-flow-map.md                            -> archive/2026-08-12-docs-cleanup/findings/2026-08-03-flow-map.md
    docs/followthrough.md                                           -> archive/2026-08-12-docs-cleanup/followthrough.md
    docs/ingest-inventory.md                                        -> archive/2026-08-12-docs-cleanup/ingest-inventory.md
    docs/m2-ocbc-canonical-report.md                                -> archive/2026-08-12-docs-cleanup/m2-ocbc-canonical-report.md
    docs/m2-ocbc-unresolved-rows.md                                 -> archive/2026-08-12-docs-cleanup/m2-ocbc-unresolved-rows.md
    docs/m3-cleanup-report.md                                       -> archive/2026-08-12-docs-cleanup/m3-cleanup-report.md
    docs/m3-ocbc-concept-binding-check.md                           -> archive/2026-08-12-docs-cleanup/m3-ocbc-concept-binding-check.md
    docs/m3-store-relationship.md                                   -> archive/2026-08-12-docs-cleanup/m3-store-relationship.md
    docs/plans/2026-07-06-chat-with-data.md                         -> archive/2026-08-12-docs-cleanup/plans/2026-07-06-chat-with-data.md
    docs/plans/2026-07-07-paddleocr-stage2-spike.md                 -> archive/2026-08-12-docs-cleanup/plans/2026-07-07-paddleocr-stage2-spike.md
    docs/plans/2026-07-08-post-spike-action-map.md                  -> archive/2026-08-12-docs-cleanup/plans/2026-07-08-post-spike-action-map.md
    docs/plans/2026-07-12-document-family-router.md                 -> archive/2026-08-12-docs-cleanup/plans/2026-07-12-document-family-router.md
    docs/runbook-execution-2026-08-04.md                            -> archive/2026-08-12-docs-cleanup/runbook-execution-2026-08-04.md
    docs/six-bug-diagnosis.md                                       -> archive/2026-08-12-docs-cleanup/six-bug-diagnosis.md
    docs/superpowers/plans/2026-07-29-gcs-source-migration.md       -> archive/2026-08-12-docs-cleanup/superpowers/plans/2026-07-29-gcs-source-migration.md
    docs/superpowers/specs/2026-07-29-gcs-source-migration-design.md -> archive/2026-08-12-docs-cleanup/superpowers/specs/2026-07-29-gcs-source-migration-design.md
