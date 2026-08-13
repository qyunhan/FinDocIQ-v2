# findociq/pipeline/Dockerfile — single unified image for the unattended
# retry worker (Phase B of the ingest-status plan).
#
# Unlike local Cloud Shell (tools/setup_paddle_venv.sh, $HOME quota forces a
# separate .venv-paddle under /tmp), a container has no such constraint:
# requirements.txt + requirements-paddle.txt install together into ONE
# interpreter — no venv split needed in prod.
#
# Build context MUST be findociq/ (not the repo root), e.g.:
#   docker build -f findociq/pipeline/Dockerfile findociq/ -t findociq-retry-worker
#
# That places this findociq/ subtree at /app/findociq inside the image, so
# run_doc.py's own REPO = Path(__file__).resolve().parents[2] (pipeline ->
# findociq -> repo) resolves to /app — matching every path it hardcodes
# (findociq/data/..., findociq/db/..., findociq/schema/...) with no changes
# to run_doc.py itself.
FROM python:3.12-slim

# libgomp1: PaddleOCR (STEP 0, pipeline/stage1_extract/toc/candidates.py) needs
# libomp/libgomp at runtime on Debian slim. libgl1/libglib2.0-0: paddlex pulls
# in opencv, which dlopen's libGL at import time -- without it, importing
# paddlex fails, which silently breaks our sitecustomize.py mkldnn patch (a
# sitecustomize ImportError is swallowed by Python at startup, not fatal, so
# this failure mode is easy to miss until PaddleOCR crashes downstream).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python deps: unified install (see header note) ---
COPY requirements.txt requirements-paddle.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-paddle.txt

# --- paddle/mkldnn CPU-crash workaround ---
# paddlepaddle 3.3.1 crashes on CPU via the oneDNN/PIR path unless patched
# (findociq/tools/setup_paddle_venv.sh writes the identical sitecustomize.py
# into .venv-paddle locally). Per the paddle-mkldnn-workaround lesson, this
# MUST be picked up via PYTHONPATH pointing at the directory containing it —
# patching site-packages directly does not stick across rebuilds.
RUN mkdir -p /opt/paddle-fix \
    && printf '%s\n' \
        "from paddlex.inference.models.runners.paddle_static.config import pp_option as _pp" \
        "_pp.is_mkldnn_available = lambda: False" \
        > /opt/paddle-fix/sitecustomize.py
ENV PYTHONPATH=/opt/paddle-fix

# run_doc.py's STEP 0 hard-codes invoking REPO/.venv-paddle/bin/python3 as a
# separate interpreter (the Cloud-Shell split-venv layout). This image has no
# split venv — everything, paddle included, is one interpreter — so symlink
# that expected path onto the system python3 rather than editing run_doc.py.
RUN mkdir -p /app/.venv-paddle/bin \
    && ln -s "$(command -v python3)" /app/.venv-paddle/bin/python3

# --- app code ---
COPY . findociq/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "findociq/pipeline/common/retry_worker.py"]
