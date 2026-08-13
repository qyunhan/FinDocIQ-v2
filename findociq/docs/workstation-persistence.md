# Workstation persistence & fresh-boot bootstrap — READ AND FOLLOW

Addressed to the Claude Code session running in the Cloud Workstation.

## What survives what (the persistence truth)
- **Stop / start** (workstation paused, then resumed): **`/home` persists** — the
  repo clone, the `.venv`, `~/paddle-fix`, downloaded PaddleOCR models. Only the
  ephemeral root filesystem (apt-installed system libs) resets.
- **Deleted / recreated / new workstation**: the home disk is wiped → **only what
  is in git (pushed) or GCS survives.**
- **Durable stores:** (1) **git** pushed to `origin` (github.com/qyunhan/FinDocIQ)
  = all code + the git-tracked DB; (2) the **GCS bucket**
  `gs://findociq-sources-igc2026-team08-6311/` = the `db/compiled_fs.db` checkpoint
  + all raw source PDFs.

## Discipline — DO THIS every session
1. **Commit + push after every major change** (a working feature, a bugfix, a
   spec/doc). Small, frequent commits with clear messages:
   `git add <specific files>; git commit -m "..."; git push`.
   **Never end a session with meaningful work uncommitted.** A deleted workstation
   loses anything unpushed.
   - **Also update `findociq/PROGRESS.md`** (newest-on-top; one block per working
     session, status keys ✅/🔄/🐞/⏭️) and include it in the same commit — so the
     running log always reflects the latest state and the next session can orient
     from it.
   - **And log the reasoning in `findociq/docs/DECISIONS.md`** for any consequential
     decision: the change, WHY, and anything tried-and-discarded WITH EVIDENCE
     (command output / file:line / measured fact). PROGRESS = what happened;
     DECISIONS = why, and what we rejected. Both feed the eventual full writeup.
2. **Use explicit pathspecs** on commit — the repo often has parallel WIP in the
   working tree; never `git commit -am` / `git add -A` blindly.
3. **After an ingestion run** that updates `findociq/db/compiled_fs.db`, persist it:
   `gsutil cp findociq/db/compiled_fs.db gs://findociq-sources-igc2026-team08-6311/db/compiled_fs.db`
   (GCS is the durable checkpoint — the only copy that survives a rebuilt
   workstation, since the DB is no longer committed on routine ingest churn.)
   If `gsutil`/`gcloud storage` fail with `ReauthUnattendedError` (stale USER
   creds — ADC itself is fine on the workstation), upload via the storage client,
   which uses ADC:
   `.venv/bin/python3 -c "from google.cloud import storage; storage.Client().bucket('findociq-sources-igc2026-team08-6311').blob('db/compiled_fs.db').upload_from_filename('findociq/db/compiled_fs.db')"` The DB is also git-tracked — commit
   it at milestones, but prefer GCS for routine ingest churn to avoid bloating git
   history with a binary.
4. **Generated artifacts** (tag workbooks, `outputs/…`) are regenerable — don't
   commit them unless they're a milestone; push to GCS if they must persist.

## Fresh-workstation bootstrap — run in order
1. **Code:** `git clone https://github.com/qyunhan/FinDocIQ.git` (or `cd FinDocIQ && git pull`),
   then `git checkout v2-concept-toolkit`.
2. **Environment:** follow `findociq/docs/workstation-setup.md`:
   - step 2 — system libs (Ubuntu 24.04: `libgomp1 libgl1 libglib2.0-0t64`),
   - step 3 — `sudo apt-get install -y python3.12-venv`, then `python3 -m venv .venv` + `pip install -r findociq/requirements.txt -r findociq/requirements-paddle.txt -r findociq/app/requirements.txt`,
   - step 4 — the PaddleOCR mkldnn fix (`~/paddle-fix/sitecustomize.py`) + the `.venv-paddle` symlink.
   - Claude Code itself: `npm config set prefix ~/.npm-global` (+ PATH) then
     `npm install -g @anthropic-ai/claude-code` — system npm can't write `/usr/lib`.
3. **Data:** `gsutil cp gs://findociq-sources-igc2026-team08-6311/db/compiled_fs.db findociq/db/`
   (raw PDFs are pulled on demand by `source_store.materialize`, no bulk sync needed).
4. **Verify:** `gcloud auth application-default print-access-token >/dev/null && echo ADC OK`;
   `python3 -c "import paddleocr"`; launch the app
   `PYTHONPATH="$HOME/paddle-fix" streamlit run findociq/app/findociq_app.py --server.port 8080 --server.address 0.0.0.0 --server.headless true`.
5. **Orient:** read `findociq/docs/2026-07-30-workstation-resume-handoff.md`.

Note: apt system libs (step 2) don't persist across a restart either — re-run step 2,
or bake them into the workstation config image (see workstation-setup.md Option C).
