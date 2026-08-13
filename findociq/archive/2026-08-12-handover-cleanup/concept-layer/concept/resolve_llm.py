"""concept.resolve_llm — classify the deterministic RESIDUE into the fixed
dictionary vocabulary with an enum-constrained Gemini call. The LLM can only
return a concept_key that already exists in the dictionary, or 'none' — it can
never invent a concept, so it cannot corrupt the vocabulary.

Efficiency: the residue is de-duplicated by normalised label, then batched (up to
BATCH_SIZE distinct labels per call), each label carrying its table_type and
parent-label context. The response schema is a constrained ARRAY of
{label_id, concept_key(enum), confidence}.

Self-reinforcing: an accepted classification (confidence >= FLOOR) is stamped on
EVERY row sharing that normalised label, logged (method='llm'), and APPENDED to
concept_map as a wildcard alias — so the next run matches it deterministically
and the LLM fires less each time. Below the floor or 'none' -> left NULL and
surfaced for review.

Model is env-swappable (GEMINI_MODEL); default is Gemini flash. Client is
Vertex AI/ADC (gemini_client.py) — no API key. temperature 0, thinking_budget 0
(classification needs neither).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
import cost  # noqa: E402  (pipeline/cost.py — pricing helper; pipeline/ is on path)
from concept.load_dictionary import _now_iso, load_concepts  # noqa: E402
from concept.normalize import norm  # noqa: E402
from gemini_client import build_client  # noqa: E402  (pipeline/gemini_client.py)

_REPO = Path(__file__).resolve().parents[3]
_PROMPT_FILE = _REPO / "findociq" / "pipeline" / "prompts" / "concept_classify.txt"

BATCH_SIZE = 20
CONFIDENCE_FLOOR = 0.8
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")


def _dedupe_residue(residue: list[dict]) -> tuple[list[dict], dict[str, set]]:
    """Collapse residue to distinct (norm_label, table_type) CONTEXTS — NOT bare
    labels. A bare label like 'Total' means different things under different table
    types; classifying and stamping it per-context (rather than corpus-wide)
    prevents one context's answer leaking onto every same-worded row elsewhere.
    Each context keeps a representative parent and its OWN rows.

    Also returns norm_label -> {table_type,...}: a label present under exactly one
    table type is UNAMBIGUOUS in this corpus and may safely become a wildcard
    concept_map alias; a label spanning multiple table types is context-dependent
    and must NOT mint a wildcard (it would over-stamp future runs)."""
    by_ctx: dict[tuple[str, str], dict] = {}
    types_by_label: dict[str, set] = {}
    for r in residue:
        types_by_label.setdefault(r["norm_label"], set()).add(r["table_type"])
        key = (r["norm_label"], r["table_type"])
        d = by_ctx.setdefault(key, dict(
            norm_label=r["norm_label"], label=r["label"],
            table_type=r["table_type"], parent=r.get("parent"), rows=[]))
        d["rows"].append((r["doc_id"], r["table_id"], r["row_id"], r["label"]))
    return list(by_ctx.values()), types_by_label


def _response_schema(keys: list[str]):
    from google.genai import types
    return types.Schema(
        type=types.Type.ARRAY,
        items=types.Schema(
            type=types.Type.OBJECT,
            required=["label_id", "concept_key", "confidence"],
            properties={
                "label_id": types.Schema(type=types.Type.INTEGER),
                "concept_key": types.Schema(type=types.Type.STRING, enum=keys + ["none"]),
                "confidence": types.Schema(type=types.Type.NUMBER),
            },
        ),
    )


def _classify_batch(client, model, prompt_tmpl, candidates_block, keys, batch):
    """One Gemini call over a batch of distinct labels. Returns
    (answers_by_label_id, usage_dict)."""
    from google.genai import types
    payload = [dict(label_id=i, line_item=b["label"],
                    table_type=b["table_type"], parent=b.get("parent"))
               for i, b in enumerate(batch)]
    prompt = (prompt_tmpl
              .replace("{CANDIDATES}", candidates_block)
              .replace("{LABELS}", json.dumps(payload, ensure_ascii=False)))
    schema = _response_schema(keys)
    last = None
    for attempt in range(4):
        try:
            resp = client.models.generate_content(
                model=model, contents=[prompt],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=schema,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            data = json.loads(resp.text)
            um = getattr(resp, "usage_metadata", None)
            usage = dict(
                prompt_tokens=getattr(um, "prompt_token_count", 0) or 0,
                output_tokens=getattr(um, "candidates_token_count", 0) or 0,
                thinking_tokens=getattr(um, "thoughts_token_count", 0) or 0)
            return {int(a["label_id"]): a for a in data}, usage
        except Exception as exc:  # noqa: BLE001 — retry transient failures
            last = exc
            wait = 2 * (2 ** attempt)
            print(f"[concept.llm] attempt {attempt+1} failed: {type(exc).__name__}: "
                  f"{str(exc)[:120]} (retry {wait}s)", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"gemini classify: all attempts failed: {last}")


def resolve_llm(con, residue: list[dict], *, model: str | None = None) -> dict:
    """Classify the residue and stamp accepted answers. `residue` is the list
    returned by resolve_deterministic. Returns a report dict."""
    model = model or DEFAULT_MODEL
    concepts = load_concepts()
    keys = [c["key"] for c in concepts]
    candidates_block = "\n".join(f"  {c['key']}: {c['name']}" for c in concepts)
    prompt_tmpl = _PROMPT_FILE.read_text()

    distinct, types_by_label = _dedupe_residue(residue)
    report = dict(residue_rows=len(residue), distinct_contexts=len(distinct),
                  distinct_labels=len(types_by_label),
                  calls=0, prompt_tokens=0, output_tokens=0, thinking_tokens=0,
                  accepted=0, rejected_low_conf=0, none=0, rows_stamped=0,
                  aliases_appended=[], aliases_skipped_ambiguous=[],
                  cost_usd=0.0, review=[])
    if not distinct:
        return report

    client = build_client()
    cur = con.cursor()
    for start in range(0, len(distinct), BATCH_SIZE):
        batch = distinct[start:start + BATCH_SIZE]
        answers, usage = _classify_batch(
            client, model, prompt_tmpl, candidates_block, keys, batch)
        report["calls"] += 1
        report["prompt_tokens"] += usage["prompt_tokens"]
        report["output_tokens"] += usage["output_tokens"]
        report["thinking_tokens"] += usage["thinking_tokens"]

        for i, b in enumerate(batch):
            a = answers.get(i)
            key = (a or {}).get("concept_key", "none")
            conf = float((a or {}).get("confidence", 0.0) or 0.0)
            if key == "none" or key not in keys:
                report["none"] += 1
                report["review"].append(dict(label=b["label"], reason="none",
                                              table_type=b["table_type"]))
                continue
            if conf < CONFIDENCE_FLOOR:
                report["rejected_low_conf"] += 1
                report["review"].append(dict(label=b["label"], reason=f"low_conf={conf:.2f}",
                                              concept_key=key, table_type=b["table_type"]))
                continue
            # ACCEPTED: stamp only THIS context's rows (never corpus-wide), log.
            report["accepted"] += 1
            for doc_id, table_id, row_id, lbl in b["rows"]:
                cur.execute(
                    "UPDATE row_dim SET concept_key=:k WHERE doc_id=:d AND "
                    "table_id=:t AND row_id=:r AND concept_key IS NULL",
                    dict(k=key, d=doc_id, t=table_id, r=row_id))
                cur.execute(
                    "INSERT INTO concept_resolution_log(doc_id,table_id,row_id,label,"
                    "norm_label,concept_key,method,confidence,ts) "
                    "VALUES (:d,:t,:r,:l,:n,:k,'llm',:c,:ts)",
                    dict(d=doc_id, t=table_id, r=row_id, l=lbl, n=b["norm_label"],
                         k=key, c=conf, ts=_now_iso()))
                report["rows_stamped"] += 1
            # Append a WILDCARD alias only when the label is UNAMBIGUOUS in the
            # corpus (present under a single table type). An ambiguous label
            # ('Total', 'Gross') that spans table types is context-dependent — a
            # wildcard alias would over-stamp every same-worded row on the next
            # deterministic run, so it is deliberately NOT promoted (it stays a
            # per-context LLM decision; cheap to re-ask).
            if len(types_by_label.get(b["norm_label"], set())) == 1:
                cur.execute(
                    "INSERT OR IGNORE INTO concept_map"
                    "(table_type,label_norm,concept_key,table_type_norm) VALUES ('*',?,?,'*')",
                    (b["norm_label"], key))
                report["aliases_appended"].append(
                    dict(label_norm=b["norm_label"], concept_key=key))
            else:
                report["aliases_skipped_ambiguous"].append(
                    dict(label_norm=b["norm_label"], concept_key=key,
                         table_types=sorted(types_by_label[b["norm_label"]])[:4]))
        con.commit()

    report["cost_usd"] = cost.dollars(report["prompt_tokens"],
                                      report["output_tokens"],
                                      report["thinking_tokens"])
    return report
