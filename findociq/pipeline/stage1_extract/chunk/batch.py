"""stage1_extract.chunk.batch — Gemini BATCH API execution mode for pass2 extraction.

Batch mode is a PURE billing/execution lever: the Gemini Batch API runs the
exact same requests asynchronously and bills at 50% of interactive rates. The
prompt, config (temperature 0, response_schema, thinking off), and every step
downstream of the response text (parse → validate → audit artifacts → merge)
are BYTE-IDENTICAL to the synchronous path — this module reuses the same
helpers (`build_prompt`, `build_config`, `_ingest_resp`, `_finalize_unit`,
`_merge_tables_into`, `_finalize_spanning`) rather than forking them.

Rounds
------
The orchestrator submits work in DEPENDENCY ROUNDS. In THIS codebase the
spanning-unit chunk calls are INDEPENDENT (each chunk gets `build_prompt`, not
a continuation prompt that injects prior column signatures — `build_continuation
_prompt` is dead code here), so ROUND 1 carries every unit's first call AND
every spanning chunk at once. Later rounds exist only for adaptive follow-ups
that the sync path also performs: image fallback (thin/parse-error responses)
and MAX_TOKENS half-splitting. Rounds terminate when nothing is pending.

Attachments
-----------
The sync path inlines the unit's page PDF via `Part.from_bytes` (inline base64
blob), NOT a Files API reference. To keep model input byte-identical we inline
the same bytes in the batch request parts (inline batch requests carry
inline_data blobs fine for single-page PDFs). The Files API is therefore
unnecessary here; using `from_uri` would change the content structure and risk
model-behavior drift — the opposite of what batch mode is for.
"""
from __future__ import annotations
import os, time, random

from google.genai import types

import stage1_extract.chunk.schema as schema
from . import extract
from .extract import (
    build_prompt, build_config,
    _ingest_resp, _finalize_unit, _merge_tables_into, _finalize_spanning,
    _page_texts, _reasonable, cut_pdf, extract_unit, page_has_table_structure,
    pages_with_no_output, render_images,
)


# ===========================================================================
# ROUND / REQUEST PLANNING  (pure — no API, unit-testable)
# ===========================================================================
def plan_unit_requests(unit: dict, chunk_size: int, force_image: bool) -> list[dict]:
    """Expand one unit into its ROUND-1 request records — no API calls.

    Mirrors extract_unit_chunked's chunk splitting exactly: a non-spanning unit
    (or a spanning unit within chunk_size) is a single request; a longer
    spanning unit splits into contiguous chunks of ≤chunk_size pages, each a
    request against a chunk_unit whose unit_id is '{uid}/chunks/c{n}'.
    """
    pages = unit["pages"]
    if unit["type"] != "spanning" or chunk_size <= 0 or len(pages) <= chunk_size:
        return [{"unit": unit, "chunk_unit": unit, "attach_image": force_image,
                 "kind": "first", "ci": 0.0}]
    chunks = [pages[i:i + chunk_size] for i in range(0, len(pages), chunk_size)]
    reqs = []
    for ci, cp in enumerate(chunks):
        chunk_unit = dict(unit, pages=cp,
                          unit_id=f"{unit['unit_id']}/chunks/c{ci+1}")
        reqs.append({"unit": unit, "chunk_unit": chunk_unit,
                     "attach_image": force_image, "kind": "first", "ci": float(ci)})
    return reqs


def _build_inlined_request(pdf_path: str, req: dict, with_thinking: bool,
                           save_audit: bool) -> tuple:
    """Build the InlinedRequest for one request record + write prompt/pages audit.
    The parts list is identical to extract_unit's non-crop `_call`."""
    cu     = req["chunk_unit"]
    prompt = build_prompt(cu)
    pdf_bytes = cut_pdf(pdf_path, cu["pages"])
    udir = os.path.join(schema.AUDIT_DIR, cu["unit_id"])
    if save_audit:
        os.makedirs(udir, exist_ok=True)
        with open(os.path.join(udir, "prompt.txt"), "w") as f:
            f.write(prompt)
        with open(os.path.join(udir, "pages.pdf"), "wb") as f:
            f.write(pdf_bytes)

    parts = [types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")]
    if req["attach_image"]:
        for img in render_images(pdf_path, cu["pages"]):
            parts.append(types.Part.from_bytes(data=img, mime_type="image/png"))
    parts.append(prompt)

    ir = types.InlinedRequest(contents=parts, config=build_config(with_thinking))
    return ir, udir


# ===========================================================================
# SUBMIT + POLL
# ===========================================================================
def _job_state_name(job) -> str:
    st = getattr(job, "state", None)
    return st.name if hasattr(st, "name") else str(st)


_TERMINAL = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED",
             "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}


def submit_and_poll(client, model: str, inlined: list, *,
                    timeout_s: int = 1800, poll_first: int = 15,
                    poll_max: int = 60) -> list:
    """Submit one inline batch job, poll with backoff, return responses aligned
    to `inlined` order. Raises TimeoutError if the job does not reach a terminal
    state within timeout_s. Uses per-request metadata idx to reorder defensively.
    """
    for i, ir in enumerate(inlined):
        ir.metadata = {"idx": str(i)}

    job = client.batches.create(model=model, src=inlined)
    print(f"      ↑ submitted batch {job.name}  ({len(inlined)} req)")

    t0 = time.time()
    wait = poll_first
    while True:
        st = _job_state_name(job)
        if st in _TERMINAL:
            break
        if time.time() - t0 > timeout_s:
            raise TimeoutError(
                f"batch {job.name} still {st} after {timeout_s}s — aborting poll")
        time.sleep(wait + random.uniform(0, 2))
        wait = min(wait * 2, poll_max)
        job = client.batches.get(name=job.name)

    st = _job_state_name(job)
    dt = time.time() - t0
    print(f"      ↓ batch {st} after {dt:.0f}s")
    if st != "JOB_STATE_SUCCEEDED":
        raise RuntimeError(f"batch {job.name} terminal state {st}: {getattr(job,'error',None)}")

    resps = (job.dest.inlined_responses if job.dest else None) or []
    # Reorder by metadata idx when present; else trust positional order.
    out = [None] * len(inlined)
    positional = True
    for pos, r in enumerate(resps):
        idx = None
        md = getattr(r, "metadata", None)
        if md and "idx" in md:
            try:
                idx = int(md["idx"]); positional = False
            except (TypeError, ValueError):
                idx = None
        if idx is None:
            idx = pos
        if 0 <= idx < len(out):
            out[idx] = r
    if positional and len(resps) == len(inlined):
        out = list(resps)
    return out


# ===========================================================================
# ORCHESTRATION
# ===========================================================================
def run_batch(client, pdf_path: str, units: list, *, force_image: bool,
              with_thinking: bool, save_audit: bool, chunk_size: int,
              timeout_s: int = 1800) -> dict:
    """Extract `units` via the Batch API. Returns {unit_id: (Extraction, meta)}.

    Flips extract.BATCH_MODE on so log_usage records the 50% discounted cost.
    Units that error in batch are omitted from the result (the caller then falls
    back to the sync path for them — transparent degradation).
    """
    extract.BATCH_MODE = True
    try:
        results: dict = {}
        span: dict = {}   # parent unit_id -> accumulator for spanning units
        pending: list = []

        for u in units:
            reqs = plan_unit_requests(u, chunk_size, force_image)
            if len(reqs) > 1:
                span[u["unit_id"]] = {"unit": u, "chunks": {}, "usage": {},
                                      "n": len(reqs), "failed": False}
            pending.extend(reqs)

        round_no = 0
        while pending:
            round_no += 1
            print(f"   🧺 batch round {round_no}: {len(pending)} request(s)")
            inlined = []
            for r in pending:
                ir, udir = _build_inlined_request(pdf_path, r, with_thinking, save_audit)
                r["udir"] = udir
                inlined.append(ir)

            responses = submit_and_poll(client, schema.MODEL, inlined, timeout_s=timeout_s)
            next_pending: list = []

            for r, rw in zip(pending, responses):
                cu, u = r["chunk_unit"], r["unit"]
                is_span = u["unit_id"] in span

                if rw is None or getattr(rw, "error", None) is not None or rw.response is None:
                    print(f"      ❌ {cu['unit_id']} — batch error: {getattr(rw,'error',None)}")
                    if is_span:
                        span[u["unit_id"]]["failed"] = True
                    continue

                resp = rw.response
                try:
                    ext, usage = _ingest_resp(resp, cu, r["attach_image"], save_audit, r["udir"])
                except RuntimeError as e:
                    # MAX_TOKENS — half-split multi-page chunks, mirroring sync
                    if "MAX_TOKENS" in str(e) and len(cu["pages"]) > 1:
                        mid = len(cu["pages"]) // 2
                        print(f"      ✂ MAX_TOKENS {cu['unit_id']} — splitting into halves")
                        base_ci = r["ci"]
                        for half_i, half_pages in enumerate((cu["pages"][:mid], cu["pages"][mid:])):
                            half_cu = dict(cu, pages=half_pages,
                                           unit_id=f"{cu['unit_id']}{'ab'[half_i]}")
                            next_pending.append({"unit": u, "chunk_unit": half_cu,
                                                 "attach_image": r["attach_image"],
                                                 "kind": "half",
                                                 "ci": base_ci + 0.25 * (half_i + 1)})
                        if is_span:
                            span[u["unit_id"]]["n"] += 1  # one chunk became two
                        continue
                    print(f"      ❌ {cu['unit_id']} — {e}")
                    if is_span:
                        span[u["unit_id"]]["failed"] = True
                    continue
                except Exception as e:
                    # parse error — image fallback if the page has table structure
                    if not r["attach_image"] and page_has_table_structure(pdf_path, cu["pages"][0]):
                        print(f"      ↻ parse error {cu['unit_id']} — image retry (round {round_no+1})")
                        next_pending.append({**r, "attach_image": True, "kind": "image_retry"})
                    else:
                        print(f"      ❌ parse fail {cu['unit_id']}: {e}")
                        if is_span:
                            span[u["unit_id"]]["failed"] = True
                    continue

                # thin-response image fallback (mirrors extract_unit)
                if (not r["attach_image"] and not _reasonable(ext)
                        and page_has_table_structure(pdf_path, cu["pages"][0])):
                    print(f"      ↻ thin response {cu['unit_id']} — image retry (round {round_no+1})")
                    next_pending.append({**r, "attach_image": True, "kind": "image_retry"})
                    continue

                if is_span:
                    # write the chunk's own audit (sync does this per chunk), collect
                    _finalize_unit(ext, usage, cu, pdf_path, cu["pages"],
                                   r["attach_image"], {}, save_audit, r["udir"])
                    S = span[u["unit_id"]]
                    S["chunks"][r["ci"]] = ext
                    # the chunk's OWN pages, needed by the dropped-page rescue
                    # in the assembly below (a chunk knows its pages here; the
                    # assembly loop otherwise only sees the merged tables).
                    S.setdefault("chunk_pages", {})[r["ci"]] = list(cu["pages"])
                    for k, v in usage.items():
                        if isinstance(v, (int, float)):
                            S["usage"][k] = S["usage"].get(k, 0) + v
                else:
                    ext_f, meta = _finalize_unit(ext, usage, u, pdf_path, u["pages"],
                                                 r["attach_image"], {}, save_audit, r["udir"])
                    results[u["unit_id"]] = (ext_f, meta)

            pending = next_pending

        # assemble spanning units from collected chunks
        for uid, S in span.items():
            if S["failed"] or not S["chunks"]:
                print(f"      ⚠ spanning {uid} incomplete in batch — left to sync fallback")
                continue
            all_tables: list = []
            for ci in sorted(S["chunks"]):
                ext_ci = S["chunks"][ci]
                # DROPPED-PAGE RESCUE — the batch twin of the sync guard in
                # extract_unit_chunked. This loop assembles spanning units
                # itself, so it does NOT go through that function and would
                # otherwise keep the defect the sync path just lost: a chunk
                # that answers for only some of its pages, undetected.
                # The rescue is SYNC even here — one page, and batch turnaround
                # is minutes. See docs/specs/2026-08-12-dropped-page-rescue.md.
                ch_pages = S.get("chunk_pages", {}).get(ci) or []
                if len(ch_pages) > 1:
                    for p in pages_with_no_output(
                            _page_texts(pdf_path, ch_pages), ext_ci.tables):
                        print(f"      ⟳ batch {uid} chunk {ci}: page {p} produced "
                              f"no table — re-extracting alone (sync)")
                        try:
                            ext_p, _m = extract_unit(
                                client, pdf_path,
                                dict(S["unit"], pages=[p],
                                     unit_id=f"{uid}/chunks/c{ci}p{p}"),
                                force_image, with_thinking, save_audit)
                        except Exception as e:
                            print(f"      ⚠ rescue of page {p} FAILED ({e}) — "
                                  f"page remains unextracted")
                            continue
                        print(f"      ⟳ page {p} rescued: {len(ext_p.tables)} table(s)")
                        _merge_tables_into(all_tables, ext_p.tables)
                _merge_tables_into(all_tables, ext_ci.tables)
            ext_f, meta = _finalize_spanning(S["unit"], all_tables, S["usage"],
                                             S["n"], pdf_path, save_audit)
            results[uid] = (ext_f, meta)

        return results
    finally:
        extract.BATCH_MODE = False
