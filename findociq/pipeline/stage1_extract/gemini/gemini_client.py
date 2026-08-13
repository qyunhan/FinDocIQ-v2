"""gemini_client.py — the ONE Gemini client builder for the whole pipeline.

Auth is Vertex AI via ADC/IAM (Cloud Shell identity locally, a Cloud Run
service account in prod) — no API key, no Secret Manager read, no plaintext
credential ever enters the process env. Previously five call sites
(extract_run.py, toc/toc_stage.py, discover/section/sections_from_gemini.py,
concept/resolve_llm.py, app/spec.py — only toc_stage and resolve_llm survive;
the other three are archived) each duplicated a findociq/.env
GEMINI_API_KEY regex-parse; that entire path is retired in favour of this
one constructor, per the decision to route every instance through IAM
rather than a distributed API key.
"""
from __future__ import annotations

from google import genai

PROJECT = "igc2026-team08-6311"
# gemini-3.5-flash (the pipeline's pinned model) is not published as a Vertex
# publisher model in us-central1/us-east*/europe-west1 for this project —
# confirmed 404 on all of those. asia-southeast1 (Singapore) works and matches
# the rest of the project's infra (BigQuery dataset `findociq` also lives here).
LOCATION = "asia-southeast1"


def build_client(**kwargs) -> genai.Client:
    """The pipeline's sole Gemini client. kwargs pass through (e.g. http_options)."""
    return genai.Client(vertexai=True, project=PROJECT, location=LOCATION, **kwargs)


# ===========================================================================
# LLM response parsing
# ===========================================================================
# Moved here 2026-08-12 from app/spec.py. It lived in the APP tree, which forced
# pipeline/toc/toc_stage.py -- a live STEP 1 module -- to put findociq/app on
# sys.path just to parse a Gemini response. That made the pipeline depend on the
# Streamlit app; the dependency now runs the other way (nothing) and app/ holds
# only what the Findociq-Dashboard deploy ships (see app/DEPLOY.md).
import json as _json
import re as _re


class LLMResponseError(ValueError):
    """Raised when an LLM response carries no usable JSON object."""


def parse_llm_json(text: str) -> dict:
    """Strip ``` fences / leading-trailing prose, parse the first {...} block.

    Robust to realistic LLM chatter: leading prose containing a stray brace,
    trailing prose containing a brace, or a second JSON-ish aside after the
    real object. Tries a balanced decode (json.JSONDecoder.raw_decode) at
    each "{" position in the text and returns the first one that succeeds
    and yields a dict — this is what "first {...} block" means in practice,
    as opposed to a naive first-"{"-to-last-"}" span.
    """
    stripped = text.strip()
    fence_match = _re.search(r"```(?:json)?\s*(.*?)\s*```", stripped,
                             _re.DOTALL | _re.IGNORECASE)
    if fence_match:
        stripped = fence_match.group(1).strip()

    decoder = _json.JSONDecoder()
    for i, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(stripped, i)
        except _json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj

    raise LLMResponseError(f"No JSON object found in LLM response: {text!r}")
