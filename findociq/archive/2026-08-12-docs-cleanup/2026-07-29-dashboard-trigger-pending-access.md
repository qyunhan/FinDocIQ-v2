# DEFERRED — Streamlit dashboard + Cloud Workflows trigger (pending IAM access)

**Status:** BLOCKED on IAM grant. Do NOT start until the user says access has landed.
**Trigger phrase:** when the user says *"I gained access"* (or similar), run STEP 0's
verification, then execute STEPS 1–6 below.
**Created:** 2026-07-29. Project `igc2026-team08-6311`, region `asia-southeast1`.

---

## Why this is deferred

The end-state front-end (user selects → scrape → GCS → ingest) triggers backend
jobs. Two independent walls (both verified live 2026-07-29):

1. **Backend can trigger jobs** — solvable, but doing it *safely* needs a dedicated
   **minimal** service account (SA), and granting roles to a SA needs
   `setIamPolicy`, which `roles/editor` lacks. → the access request below.
2. **Public front-end URL on Cloud Run** — blocked by an **org-level** policy
   `constraints/iam.allowedPolicyMemberDomains` (allowedValues `C00u5vr45`), which
   forbids `allUsers` even for a project Owner. Only an **org admin** can except it.
   → We sidestep by hosting the **dashboard on Streamlit Community Cloud** (public,
   free, no org-admin), and keep GCP for the private backend.

## Decision (approved by user)

- **Dashboard → Streamlit Community Cloud** (public link, no org-admin needed).
- **Pipeline/orchestrator → GCP** (Cloud Run + Workflows + GCS), private, fine.
- Streamlit runs *outside* GCP, so it needs a **SA key** to (a) read BigQuery for
  the dashboard and (b) trigger the ingest Workflow. Key creation is allowed here
  (org policy `iam.disableServiceAccountKeyCreation` is NOT enforced).

## Access requested (user submitted 2026-07-29 — mark done when granted)

**To the user's account `yunhan@uobdmoedm.com`:**
- [ ] `roles/resourcemanager.projectIamAdmin` — **the one that matters**; lets the
      user grant roles to the SA (also permanently unblocks Phase C retry-worker SA).
- [ ] `roles/iam.serviceAccountAdmin` — *redundant* (editor already creates SAs),
      harmless.
- [ ] `roles/iam.serviceAccountKeyAdmin` — *redundant* (editor already creates keys),
      harmless.

> Verified: `editor` already allows SA + key creation. The functional unlock is
> `projectIamAdmin` only. Once it lands, the user is self-sufficient — no more
> admin round-trips.

## Verified facts (don't re-diagnose)

- User = `roles/editor`; **cannot** `setIamPolicy` (tested: `add-iam-policy-binding`
  → PERMISSION_DENIED). Can create/delete SAs and keys.
- Default compute SA `663571970232-compute@developer.gserviceaccount.com` **has
  `roles/editor`** → can invoke Workflows / enqueue Tasks today (over-privileged;
  do NOT export its key long-term).
- Org policies: `iam.allowedPolicyMemberDomains` enforced (blocks `allUsers`);
  `run.allowedIngress` = ALLOW; `iam.disableServiceAccountKeyCreation` NOT enforced.
- Existing workflow to trigger: `pipeline/workflows/retry_worker_workflow.yaml`
  (Cloud Workflows → `findociq-retry-worker` Cloud Run Job).
- Dashboard code: `app/dashboard.py`, env `FINDOCIQ_DB_SOURCE=sqlite|bq`,
  `FINDOCIQ_BQ_PROJECT`, `FINDOCIQ_BQ_DATASET`; already deployed to Cloud Run
  `findociq-dashboard` (private).

---

## RUN WHEN ACCESS LANDS

### STEP 0 — verify the grant
```bash
P=igc2026-team08-6311
gcloud projects get-iam-policy $P --flatten="bindings[].members" \
  --filter="bindings.members:yunhan@uobdmoedm.com" --format="value(bindings.role)"
# expect roles/resourcemanager.projectIamAdmin present
```
If not present, STOP — access not actually granted yet.

### STEP 1 — create the dedicated minimal SA
```bash
gcloud iam service-accounts create findociq-streamlit \
  --project $P --display-name "FinDocIQ Streamlit front-end (minimal)"
SA=findociq-streamlit@$P.iam.gserviceaccount.com
```

### STEP 2 — grant it exactly three narrow roles
```bash
for R in roles/workflows.invoker roles/bigquery.dataViewer roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding $P \
    --member="serviceAccount:$SA" --role="$R"
done
```
> `workflows.invoker` = trigger jobs. `bigquery.dataViewer` + `bigquery.jobUser`
> = read live BQ for the dashboard. NOTHING else. (Skip the BQ roles only if the
> Streamlit app reads the committed SQLite DB instead of live BQ.)

### STEP 3 — generate a key (store OUTSIDE the repo; never commit)
```bash
gcloud iam service-accounts keys create ~/findociq-streamlit-key.json \
  --iam-account $SA
```
Paste its JSON into Streamlit → app → Settings → **Secrets** as `[gcp_service_account]`.
Confirm `~/findociq-streamlit-key.json` is `.gitignore`d / deleted after upload.

### STEP 4 — Streamlit trigger code (scaffold; wire the key)
Add a "Run ingest" action that fires the Workflow non-blocking:
```python
from google.cloud import workflows_v1
from google.cloud.workflows import executions_v1
from google.oauth2 import service_account
import streamlit as st

creds = service_account.Credentials.from_service_account_info(
    st.secrets["gcp_service_account"])
parent = executions_v1.ExecutionsClient(credentials=creds).workflow_path(
    "igc2026-team08-6311", "asia-southeast1", "<WORKFLOW_NAME>")
executions_v1.ExecutionsClient(credentials=creds).create_execution(parent=parent)
# returns immediately; job runs detached — user can close the tab
```
(Confirm the exact workflow name from `retry_worker_workflow.yaml` deployment.)

### STEP 5 — dashboard BQ read via the same creds
Point `app/dashboard.py` BQ client at `st.secrets["gcp_service_account"]` when
running on Streamlit (keep ambient ADC path for Cloud Run).

### STEP 6 — deploy to Streamlit Community Cloud
Connect the GitHub repo at share.streamlit.io (user click-through), set the app
entrypoint to `app/dashboard.py`, add the secret, deploy → public URL.

---

## Optional later (needs ORG admin, not projectIamAdmin)

If a public URL on **Cloud Run** is ever wanted instead of Streamlit: org admin
must add a project exception to `constraints/iam.allowedPolicyMemberDomains` for
`allUsers`, then bind `allUsers → roles/run.invoker` (or grant the user
`roles/run.admin`). Not needed while the dashboard lives on Streamlit.
