#!/usr/bin/env bash
# setup_paddle_venv.sh — (re)build the ephemeral .venv-paddle used for STEP 0
# (PaddleOCR layout scan via pipeline/discover/section/candidates.py).
#
# This venv MUST live outside $HOME: on Cloud Shell only $HOME persists across
# sessions (~5GB quota), and paddlepaddle+paddleocr+paddlex+model weights would
# burn a big chunk of that. So it's built under /tmp (wiped every session —
# that's expected; just re-run this script, ~1-2 min, no size cost to $HOME).
#
# Usage:  bash tools/setup_paddle_venv.sh
# Then invoke paddle-dependent scripts with:
#   PYTHONPATH=/tmp/paddle-scratch HOME=/tmp/paddle-scratch/paddlehome \
#     .venv-paddle/bin/python3 findociq/pipeline/discover/section/candidates.py ...
# (or ingest_quarter.py ... --no-ipv4-shim, which needs PYTHONPATH set the same
# way so its own sitecustomize doesn't shadow this one — see the ingest handoff doc)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH=/tmp/paddle-scratch

mkdir -p "$SCRATCH/paddlehome"
export PIP_CACHE_DIR="$SCRATCH/pipcache"

if [ ! -x "$SCRATCH/venv/bin/python3" ]; then
  echo "▶ Creating venv at $SCRATCH/venv …"
  python3 -m venv "$SCRATCH/venv"
fi

ln -sfn "$SCRATCH/venv" "$REPO_ROOT/.venv-paddle"

echo "▶ Installing pinned paddle stack (paddlepaddle/paddleocr/paddlex + deps) …"
"$SCRATCH/venv/bin/pip" install --quiet --upgrade pip
"$SCRATCH/venv/bin/pip" install --quiet -r "$REPO_ROOT/findociq/requirements-paddle.txt"

cat > "$SCRATCH/sitecustomize.py" << 'EOF'
from paddlex.inference.models.runners.paddle_static.config import pp_option as _pp
_pp.is_mkldnn_available = lambda: False
EOF

"$SCRATCH/venv/bin/python3" -c "
import paddle, paddleocr, paddlex
print('paddle', paddle.__version__)
print('paddleocr', paddleocr.__version__)
print('paddlex', paddlex.__version__)
"

echo "✅ .venv-paddle ready -> $SCRATCH/venv (PYTHONPATH=$SCRATCH HOME=$SCRATCH/paddlehome required at run time)"
