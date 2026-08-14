#!/usr/bin/env bash
# vm_up.sh — bring up the persistent Claude Code dev VM and SSH into it.
#
# Idempotent: creates + bootstraps the VM the first time, just starts it if it
# was stopped, then connects you. Run this from Google Cloud Shell (or any
# terminal with gcloud) every time you open GCP.
#
#   bash vm_up.sh
#
# Override defaults via env, e.g.:  GCP_ZONE=us-central1-a VM_MACHINE=e2-medium bash vm_up.sh
set -euo pipefail

PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${GCP_ZONE:-asia-southeast1-b}"          # Singapore
VM="${VM_NAME:-claude-dev}"
MACHINE="${VM_MACHINE:-e2-small}"
REPO="${REPO_URL:-https://github.com/qyunhan/FinDocIQ.git}"

[ -n "$PROJECT" ] || { echo "✋ No GCP project set. Run: gcloud config set project YOUR_PROJECT_ID"; exit 1; }

status=$(gcloud compute instances describe "$VM" --zone="$ZONE" --project="$PROJECT" \
           --format='value(status)' 2>/dev/null || echo MISSING)

if [ "$status" = MISSING ]; then
  echo "▶ Creating VM '$VM' ($MACHINE) in $ZONE …"
  gcloud compute instances create "$VM" \
    --project="$PROJECT" --zone="$ZONE" --machine-type="$MACHINE" \
    --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=30GB --scopes=cloud-platform
  echo "▶ Waiting for SSH …"; sleep 25

  # Bootstrap is a fully-quoted heredoc (nothing expands here in Cloud Shell);
  # it runs ON the VM and takes project/region/repo as args — no fragile subst.
  BOOT=$(mktemp)
  cat > "$BOOT" <<'BOOTSTRAP'
#!/usr/bin/env bash
set -euo pipefail
PROJECT="$1"; REPO="$2"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3-pip python3-venv curl
curl -fsSL https://claude.ai/install.sh | bash
if ! grep -qF 'claude-dev env' "$HOME/.bashrc" 2>/dev/null; then
  {
    echo '# claude-dev env'
    echo 'export PATH="$HOME/.local/bin:$PATH"'
    echo "export GOOGLE_CLOUD_PROJECT=$PROJECT"
  } >> "$HOME/.bashrc"
fi
[ -d "$HOME/FinDocIQ" ] || git clone "$REPO" "$HOME/FinDocIQ"
echo "✅ bootstrap complete. Next: 'source ~/.bashrc', then 'claude login' (or your"
echo "   Claude auth), then 'cd ~/FinDocIQ && claude'."
BOOTSTRAP

  echo "▶ Bootstrapping VM (git, python, Claude Code) …"
  gcloud compute scp "$BOOT" "$VM":~/bootstrap.sh --zone="$ZONE" --project="$PROJECT" --quiet
  gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" --quiet \
    --command="bash ~/bootstrap.sh '$PROJECT' '$REPO'"
  rm -f "$BOOT"

elif [ "$status" != RUNNING ]; then
  echo "▶ Starting VM '$VM' …"
  gcloud compute instances start "$VM" --zone="$ZONE" --project="$PROJECT"
  sleep 10
else
  echo "▶ VM '$VM' already running."
fi

echo "▶ Connecting … (pulling latest in ~/FinDocIQ)"
exec gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" -- \
  'cd ~/FinDocIQ 2>/dev/null && git pull --ff-only 2>/dev/null; exec bash -l'
