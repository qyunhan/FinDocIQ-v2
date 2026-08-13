"""sections_from_gemini.py — Stage 2 (Gemini branch, no-TOC) heading VALIDATOR of
section->table tagging.

See findociq/docs/specs/2026-07-09-section-table-tagging-design.md, AMENDMENT
2026-07-09 PM: the one-shot design (Gemini emits TOC + all table assignments in
one call) is RETIRED — evidence from live runs showed it drops assignments and
lumps subsection tables to their parent note, while a DETERMINISTIC
"deepest-heading-above" sweep provably gets the leaf right. LLMs must not do
positional/table assignment.

New shape: Gemini only VALIDATES/ARRANGES headings. It receives the ordered,
INDEXED candidate list (candidates.py) with NO tables block, and returns which
candidate lines are real section-heading instances, how they group (dedupe
spaced/glued/"(continued)" repeats into one section) and nest (id/level/
parent_id). This module turns that into the shared `boundaries` contract:
    [{section_id, section_title, level, page, y0, continued}]  (reading order)
and hands it to `assign_tables.assign(boundaries, regions)` — the ONE
deterministic assigner shared with the TOC branch (toc_match.py) — to produce
section_tags.csv. Gemini never sees table regions and never emits a position or
a table assignment.

Base python + google-genai (lazy import, only inside the real transport) — this
module itself has no heavy dependency and can be imported/tested without
paddlex or google-genai installed. The transport/client plumbing (gemini_llm,
_with_backoff, parse_llm_json) is REUSED, not duplicated: retry/backoff and JSON
hardening come from findociq/app/spec.py (already the shared helper used by the
TOC-branch-adjacent tooling); the Gemini client construction itself lives here
since prompts/call shape differ from spec.py's system_prompt/user_text split.

RUNS (real API call):
  python3 findociq/pipeline/discover/section/sections_from_gemini.py <tag> \
      [--out findociq/experiments/2026-07-07_paddleocr_eval/outputs]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from typing import Callable

HERE = os.path.dirname(os.path.abspath(__file__))

# -> findociq/ (parent of the `app` package, for `from app.spec import ...`)
_FINDOCIQ_ROOT = os.path.join(HERE, "..", "..", "..")
if _FINDOCIQ_ROOT not in sys.path:
    sys.path.insert(0, _FINDOCIQ_ROOT)
from app.spec import parse_llm_json, _with_backoff  # noqa: E402  (reuse JSON hardening + retry)

if HERE not in sys.path:
    sys.path.insert(0, HERE)
from assign_tables import assign  # noqa: E402  (the ONE deterministic assigner, shared with toc_match)

_PIPELINE_DIR = os.path.join(_FINDOCIQ_ROOT, "pipeline")
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)
from gemini_client import build_client  # noqa: E402  (pipeline/gemini_client.py)

_DEFAULT_OUT = os.path.join(
    HERE, "..", "..", "..", "experiments", "2026-07-07_paddleocr_eval", "outputs")

_PROMPT_PATH = os.path.join(HERE, "..", "..", "prompts", "section_arrange.txt")


class SectionArrangeError(ValueError):
    """Raised for a malformed/invalid Gemini response (fail-loudly, spec convention)."""


class GeminiTransportError(RuntimeError):
    """Raised when the Gemini transport fails loud: chunk_size=0 (one-shot only)
    stalls with no fallback available, or chunked mode exhausts retries on one or
    more chunks (their caches persist on disk; a re-run resumes from cache)."""


def _read_csv(path: str) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def build_prompt(candidates: list[dict]) -> str:
    """Base instruction text (section_arrange.txt) + a CANDIDATES: block, one line
    per candidate as `idx | page | y | size | bold | align | text`, idx = 0-based
    position in the given list (candidates.csv is already in reading order)."""
    with open(_PROMPT_PATH) as fh:
        base = fh.read().rstrip("\n")

    cand_lines = [
        f"{i} | {r['page']} | {r['y0']} | {r['font_size']} | {r['bold']} | "
        f"{r['alignment']} | {r['text']}"
        for i, r in enumerate(candidates)
    ]

    return f"{base}\n\nCANDIDATES:\n" + "\n".join(cand_lines) + "\n"


_IPV4_FORCED = False


def _force_ipv4() -> None:
    """Filter AF_INET6 out of socket.getaddrinfo so every outbound connection
    uses IPv4. Needed on hosts whose IPv6 route to Google blackholes: the
    google-genai SDK (httpx) tries an AAAA address first and hangs indefinitely
    (observed 2026-07-09: IPv4 connect 0.02s, IPv6 connect times out), curl
    dodges it via Happy Eyeballs but the SDK does not. Idempotent; process-wide."""
    global _IPV4_FORCED
    if _IPV4_FORCED:
        return
    import socket
    _orig = socket.getaddrinfo

    def _ipv4_only(host, port, family=0, *a, **kw):
        res = _orig(host, port, family, *a, **kw)
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return v4 or res            # fall back to original if no v4 (never strand)

    socket.getaddrinfo = _ipv4_only
    _IPV4_FORCED = True


def gemini_llm(prompt: str) -> str:
    """Real transport: google-genai client (Vertex AI/ADC — gemini_client.py),
    gemini-3.5-flash, temperature 0. Transient 5xx errors retried up to 5
    attempts with exponential backoff via the shared `_with_backoff` helper.
    google-genai is imported lazily so the rest of this module works without
    it installed. This arranger sends one self-contained prompt (instructions +
    data appended by build_prompt), so there's no separate system_instruction.

    Model policy: single model (gemini-3.5-flash), no baked-in fallback. A
    same-night capacity workaround chained a gemini-2.5-flash fallback on 503s;
    that was a user-approved ad hoc call for one arrangement task, not a pipeline
    decision, so it is NOT ported here. An operator who needs a different model
    can pass `llm=` to attribute_from_gemini with their own transport callable.
    """
    from google.genai import errors as genai_errors
    from google.genai import types

    _force_ipv4()          # this host's IPv6 route to Google blackholes (2026-07-09)

    # Per-request timeout so a silently-stalled endpoint FAILS FAST instead of
    # hanging forever (observed 2026-07-09: the endpoint accepted the socket but
    # returned nothing — a 19-min hang with no timeout). http timeout is in ms.
    client = build_client(http_options=types.HttpOptions(timeout=120_000))

    def _call():
        return client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )

    def _is_retryable(e: Exception) -> bool:
        if isinstance(e, genai_errors.ServerError):
            return True
        if isinstance(e, genai_errors.APIError):
            return (getattr(e, "code", None) or 0) >= 500
        # a connection timeout / stalled socket is retryable (transient network)
        name = type(e).__name__.lower()
        return "timeout" in name or "connection" in name or "deadline" in name

    response = _with_backoff(_call, attempts=5, base_delay=2, is_retryable=_is_retryable)
    return response.text


# Stamped onto chunk caches (parsed["_model"]) so a human can see which model
# answered a given chunk without reading code. Fixed by model policy above; a
# custom `llm=` transport can set its own `.MODEL` attribute to be stamped
# instead (falls back to the callable's __name__ if absent).
gemini_llm.MODEL = "gemini-3.5-flash"


def _derive_level(section_id: str) -> int:
    """Fallback level when Gemini omits it: numbering depth (dots+1), else 1."""
    sid = (section_id or "").strip()
    if not sid:
        return 1
    parts = sid.split(".")
    if all(p.strip().isdigit() for p in parts if p.strip()):
        return max(1, len([p for p in parts if p.strip()]))
    return 1


def _normalize_text(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def boundaries_from_response(parsed: dict, candidates: list[dict]) -> list[dict]:
    """Validate parsed Gemini JSON against the candidate list, then expand each
    section's candidate_idxs into one boundary per instance, in reading order.

    Validation (raises SectionArrangeError):
      - top-level shape has a "sections" list;
      - every candidate_idx is an int in range [0, len(candidates));
      - no candidate_idx is claimed by more than one section;
      - every section has >=1 candidate_idx and a non-empty id;
      - section ids are unique.

    Each boundary: {section_id, section_title, level, page, y0, continued}.
    `continued` is True iff the instance's own text (normalized) ends with
    "(continued)", OR it is not the section's first instance in reading order
    (reading order = candidates.csv order, which is page/y0 ascending).
    """
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sections"), list):
        raise SectionArrangeError(
            f"Malformed Gemini response: expected key 'sections' (list), got {parsed!r}")

    sections = parsed["sections"]
    n = len(candidates)
    seen_idx: dict = {}
    seen_ids: set = set()
    boundaries = []

    for s in sections:
        sid = str(s.get("id", "")).strip()
        if not sid:
            raise SectionArrangeError(f"section missing non-empty 'id': {s!r}")
        if sid in seen_ids:
            raise SectionArrangeError(f"duplicate section id {sid!r}")
        seen_ids.add(sid)

        title = str(s.get("title", "")).strip() or sid
        level = int(s["level"]) if s.get("level") not in (None, "") else _derive_level(sid)

        idxs = s.get("candidate_idxs") or []
        if not idxs:
            raise SectionArrangeError(f"section {sid!r} has no candidate_idxs")

        resolved = []
        for raw in idxs:
            if not isinstance(raw, int):
                raise SectionArrangeError(
                    f"section {sid!r} candidate_idx {raw!r} is not an int")
            if raw < 0 or raw >= n:
                raise SectionArrangeError(
                    f"section {sid!r} candidate_idx {raw} out of range [0,{n})")
            if raw in seen_idx:
                raise SectionArrangeError(
                    f"candidate_idx {raw} claimed by both section "
                    f"{seen_idx[raw]!r} and {sid!r}")
            seen_idx[raw] = sid
            resolved.append(raw)

        # reading order = ascending idx (candidates.csv is already reading-order).
        resolved.sort()
        for pos, idx in enumerate(resolved):
            c = candidates[idx]
            text_norm = _normalize_text(c.get("text", ""))
            continued = pos > 0 or text_norm.endswith("(continued)")
            boundaries.append(dict(
                section_id=sid,
                section_title=title,
                level=level,
                page=int(c["page"]),
                y0=float(c["y0"]),
                continued=continued,
            ))

    boundaries.sort(key=lambda b: (b["page"], b["y0"]))
    return boundaries


# --- adaptive/chunked transport -------------------------------------------
#
# See findociq/docs/specs/2026-07-09-section-table-tagging-design.md AMENDMENT
# 2026-07-10: this host's network intermittently DROPS large (>~6KB) HTTPS
# uploads to generativelanguage.googleapis.com — a silent stall, no error, no
# timeout raised by the SDK. Small requests (a handful of candidates) pass.
# A stalled socket cannot be cancelled, so the only reliable defense is a
# daemon-thread call bounded by `.join(cap)`: if the thread is still alive
# after `cap` seconds we treat the call as failed and move on, leaving the
# thread to die with the process. Proven in a scratch runner before being
# ported here (findociq/experiments/2026-07-07_paddleocr_eval).

_CHUNK_SIZE = 25          # candidates per call in chunked mode
_CHUNK_CALL_TIMEOUT = 90  # seconds, bounded wrapper per chunk attempt
_CHUNK_RETRIES = 3        # attempts per chunk before it is skipped this pass


def _retry_wait(attempt: int) -> float:
    """Backoff before retry `attempt` (1-based): 5s, 10s, 15s. A module-level
    function (not a constant) so tests can monkeypatch it to 0 and run fast."""
    return 5 * attempt


def _call_bounded(transport: Callable[[str], str], prompt: str, cap: float):
    """Run `transport(prompt)` on a daemon thread, joined with a `cap`-second
    timeout. A stalled socket cannot be cancelled, so a still-alive thread
    after the join is treated as a stall: we return (None, "stall") and let
    the caller move on; the thread is abandoned (daemon, dies with process).

    Returns (raw_text, None) on success, or (None, err_str) on stall/exception.
    """
    box: dict = {}

    def run():
        try:
            box["raw"] = transport(prompt)
        except Exception as e:  # noqa: BLE001 (transport failures are data, not bugs)
            box["err"] = f"{type(e).__name__}: {e}"[:200]

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(cap)
    if th.is_alive():
        return None, "stall"
    if "raw" in box:
        return box["raw"], None
    return None, box.get("err", "unknown transport error")


def _model_stamp(transport: Callable[[str], str]) -> str:
    return getattr(transport, "MODEL", None) or getattr(transport, "__name__", "unknown")


def _finish(parsed: dict, candidates: list[dict], regions: list[dict],
            out_dir: str, tag: str) -> str:
    """Shared tail for both transport paths: validate/expand `parsed` into
    `boundaries`, hand off to the shared deterministic assigner, write
    section_tags.csv. Returns the section_tags.csv path."""
    boundaries = boundaries_from_response(parsed, candidates)
    rows = assign(boundaries, regions)

    tags_path = os.path.join(out_dir, "section_tags.csv")
    with open(tags_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "page", "table_idx", "section_id", "section_title", "source"])
        w.writeheader()
        for r in rows:
            w.writerow(dict(
                page=r["page"], table_idx=r["table_idx"],
                section_id=r["section_id"], section_title=r.get("section_title", ""),
                source="gemini"))

    print(f"[{tag}] gemini arranger: {len(rows)} region(s) tagged -> {tags_path}")
    return tags_path


def _attribute_oneshot(tag: str, out_dir: str, candidates: list[dict],
                        transport: Callable[[str], str], timeout: float,
                        fail_loud: bool) -> dict | None:
    """One call, all candidates. Persists gemini_sections_raw.txt + parsed
    gemini_sections.json (unchanged from the pre-adaptive behavior) on success.

    Returns the parsed dict on success. On stall/error: raises
    GeminiTransportError if `fail_loud`, else returns None (adaptive caller
    falls back to chunked transport).
    """
    prompt = build_prompt(candidates)
    raw, err = _call_bounded(transport, prompt, timeout)
    if raw is None:
        if fail_loud:
            raise GeminiTransportError(
                f"[{tag}] one-shot Gemini call failed ({err}) after {timeout}s; "
                f"chunk_size=0 forces one-shot-only, no fallback")
        return None

    raw_path = os.path.join(out_dir, "gemini_sections_raw.txt")
    with open(raw_path, "w") as fh:
        fh.write(raw)

    parsed = parse_llm_json(raw)

    json_path = os.path.join(out_dir, "gemini_sections.json")
    with open(json_path, "w") as fh:
        json.dump(parsed, fh, indent=2)

    return parsed


def _attribute_chunked(tag: str, out_dir: str, candidates: list[dict],
                        transport: Callable[[str], str], chunk_size: int) -> dict:
    """Candidates sliced into groups of `chunk_size`; one small bounded call
    per chunk, cached to disk so a re-run resumes from where it left off.

    Merge: sections keyed by id; candidate_idxs shifted by the chunk offset
    (lo = chunk_index * chunk_size) and unioned across chunks; first
    occurrence of a given id wins title/level/parent_id (later chunks only
    contribute more candidate_idxs to that id, which does not happen in
    practice since a chunk's own idxs are local to it, but is handled for
    robustness). Writes the merged gemini_sections.json. Raises
    GeminiTransportError if any chunk is missing after retries (its cache
    files, if any, remain on disk — a re-run resumes).
    """
    cache_dir = os.path.join(out_dir, "gemini_chunks")
    os.makedirs(cache_dir, exist_ok=True)

    n = len(candidates)
    nchunks = (n + chunk_size - 1) // chunk_size if n else 0
    merged: dict = {}
    order: list = []
    missing: list = []

    for ci in range(nchunks):
        cache_path = os.path.join(cache_dir, f"{ci:02d}.json")
        lo = ci * chunk_size

        if os.path.exists(cache_path):
            with open(cache_path) as fh:
                parsed = json.load(fh)
        else:
            prompt = build_prompt(candidates[lo:lo + chunk_size])
            raw, err = None, None
            for attempt in range(1, _CHUNK_RETRIES + 1):
                raw, err = _call_bounded(transport, prompt, _CHUNK_CALL_TIMEOUT)
                if raw is not None:
                    break
                wait = _retry_wait(attempt)
                print(f"[{tag}] chunk {ci + 1}/{nchunks} attempt {attempt}: "
                      f"{err}; waiting {wait}s")
                time.sleep(wait)

            if raw is None:
                print(f"[{tag}] chunk {ci + 1}/{nchunks}: FAILED after "
                      f"{_CHUNK_RETRIES} attempt(s) — skipping this pass "
                      f"(other chunks continue; progress persists, a re-run "
                      f"resumes)")
                missing.append(ci)
                continue

            parsed = parse_llm_json(raw)
            parsed["_model"] = _model_stamp(transport)
            with open(cache_path, "w") as fh:
                json.dump(parsed, fh)
            print(f"[{tag}] chunk {ci + 1}/{nchunks}: ok "
                  f"({len(parsed.get('sections', []))} section(s))")

        for s in parsed.get("sections", []):
            sid = s["id"]
            idxs = [i + lo for i in s.get("candidate_idxs", [])]
            if sid in merged:
                merged[sid]["candidate_idxs"] = list(merged[sid]["candidate_idxs"]) + idxs
            else:
                merged[sid] = dict(s, candidate_idxs=idxs)
                order.append(sid)

    if missing:
        raise GeminiTransportError(
            f"[{tag}] chunked transport: {len(missing)}/{nchunks} chunk(s) "
            f"missing after retries (0-based indices {missing}) — chunk "
            f"caches for completed chunks persist under {cache_dir}; re-run "
            f"to resume")

    full = {"sections": [merged[sid] for sid in order]}
    json_path = os.path.join(out_dir, "gemini_sections.json")
    with open(json_path, "w") as fh:
        json.dump(full, fh, indent=2)
    return full


def attribute_from_gemini(
    tag: str,
    out_root: str = _DEFAULT_OUT,
    llm: Callable[[str], str] | None = None,
    chunk_size: int | None = None,
    oneshot_timeout: float = 120,
) -> str:
    """No-TOC branch: candidates.csv + regions.csv -> section_tags.csv.

    Gemini validates/arranges HEADINGS ONLY (no tables block, no positional
    assignment). This function derives `boundaries` from its response and hands
    them to the shared deterministic `assign_tables.assign` (same assigner the
    TOC branch's toc_match.py effectively implements) to attribute every region.

    Transport is ADAPTIVE by default (chunk_size=None): a stalled/dropped large
    upload cannot be distinguished from a slow model up front, so we always try
    the cheap path first. First try ONE call with all candidates, bounded by
    `oneshot_timeout` seconds (a stalled socket can't be cancelled, so this is a
    daemon-thread join, not a real cancel). If it returns, proceed exactly as
    before this feature existed. If it stalls, log one line and fall back to
    CHUNKED transport (candidates split into groups of 25, one small call per
    group, each response cached to <out_dir>/gemini_chunks/NN.json so a re-run
    resumes instead of re-paying for completed chunks).

    `chunk_size` overrides the adaptive choice: an int forces chunked mode at
    that group size; 0 forces one-shot-only and fails loud (GeminiTransportError)
    on stall instead of falling back.

    Writes <out_root>/<tag>/gemini_sections_raw.txt (one-shot path only, raw LLM
    text), <out_root>/<tag>/gemini_sections.json (parsed JSON — merged across
    chunks in chunked mode), and <out_root>/<tag>/section_tags.csv
    (page,table_idx,section_id,section_title,source), one row per region in
    regions.csv. Returns the section_tags.csv path.

    `llm` is an injectable prompt->str callable (tests mock it); defaults to the
    real Gemini transport (gemini_llm). See gemini_llm's docstring for model
    policy (single model, no baked-in fallback; pass a custom `llm=` for that).
    """
    out_dir = os.path.join(out_root, tag)
    cand_path = os.path.join(out_dir, "candidates.csv")
    regions_path = os.path.join(out_dir, "regions.csv")

    candidates = _read_csv(cand_path)
    regions = _read_csv(regions_path)

    transport = llm if llm is not None else gemini_llm

    if chunk_size == 0:
        parsed = _attribute_oneshot(tag, out_dir, candidates, transport,
                                     oneshot_timeout, fail_loud=True)
    elif chunk_size:
        parsed = _attribute_chunked(tag, out_dir, candidates, transport, chunk_size)
    else:
        parsed = _attribute_oneshot(tag, out_dir, candidates, transport,
                                     oneshot_timeout, fail_loud=False)
        if parsed is None:
            print(f"[{tag}] one-shot stalled after {oneshot_timeout}s — "
                  f"falling back to chunked transport")
            parsed = _attribute_chunked(tag, out_dir, candidates, transport, _CHUNK_SIZE)

    return _finish(parsed, candidates, regions, out_dir, tag)


def main():
    ap = argparse.ArgumentParser(
        description="Stage 2/3 (no-TOC branch): Gemini heading validator + "
                     "shared deterministic table assigner")
    ap.add_argument("tag")
    ap.add_argument("--out", default=_DEFAULT_OUT,
                     help="output root (default: findociq/experiments/"
                          "2026-07-07_paddleocr_eval/outputs)")
    args = ap.parse_args()
    attribute_from_gemini(args.tag, args.out)


if __name__ == "__main__":
    main()
