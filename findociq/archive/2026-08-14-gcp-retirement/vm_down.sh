#!/usr/bin/env bash
# vm_down.sh — STOP the dev VM (keeps the disk + all your work, stops the
# hourly compute charge). Bring it back anytime with vm_up.sh. Run from Cloud Shell.
set -euo pipefail

PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${GCP_ZONE:-asia-southeast1-b}"
VM="${VM_NAME:-claude-dev}"

[ -n "$PROJECT" ] || { echo "✋ No GCP project set. Run: gcloud config set project YOUR_PROJECT_ID"; exit 1; }

gcloud compute instances stop "$VM" --zone="$ZONE" --project="$PROJECT"
echo "🛑 '$VM' stopped. Disk + your work persist; bring it back with 'bash vm_up.sh'."
