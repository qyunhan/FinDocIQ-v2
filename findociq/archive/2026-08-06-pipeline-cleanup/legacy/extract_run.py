"""extract_run — reconciled extraction driver.

Routing signals from findociq/pipeline/route/scan.py (BORDERED_* vs
BORDERLESS_MAIN class, num_cov, bscore, structure_authority) choose the
FRAMING per unit; the proven legacy pass2 machinery
(findociq/_legacy/DELIVERABLE/pillar3/pass2/{extract,render,schema}.py)
supplies unit-prompt construction (section-boundary anchor rule, runtime
continuation column-signature injection) and the per-call usage ledger.
scan.py's signals ENHANCE pass2's framing decision — they do not replace
pass2's prompt/unit logic (reconciliation decision #1).

Output is Gemini HTML (the schema_v5-proven contract — same as
findociq/pipeline/universal/auto_extract.py and the OCBC NSFR spike), loaded
via findociq/experiments/2026-06-29_mineru_eval/html_to_cells.py and a
doc-scoped idempotent loader ported from that dir's load_to_db.py (cited at
each port site below), then stamped separately via
findociq/pipeline/templates/stamp.py.

Model is PINNED to gemini-3.5-flash (legacy pass2/schema.py:9 MODEL — also
MODELS[0] in auto_extract.py, and the family findociq/pipeline/cost.py prices).
NO cross-family fallback: a failed call retries the SAME model up to 3x
(legacy extract.py:507-536 backoff pattern, reused via _is_transport_error)
then the unit is FLAGGED and skipped — never silently swapped to another
model family (reconciliation decision #1 / spec item 5).
thinking_budget=0 always (findociq/pipeline/cost.py:4 — validated for
extraction; reconciliation decision #3).

Client: findociq/pipeline/gemini_client.py — Vertex AI via ADC/IAM, no API
key (superseded the legacy DELIVERABLE .env GEMINI_API_KEY convention).

Usage:
    python3 extract_run.py --manifest <path> --only nsfr \
        --db findociq/db/final.db [--doc <doc_id>] [--section <selector>] \
        [--dry-run]

Manifest shape (see findociq/pipeline/route/out/nsfr_manifest.json, or the
smoke-test fixture built alongside this driver):
    {"template": "nsfr", "docs": [
        {"doc_id", "pdf", "bank", "quarter",
         "section": {"sec_no", "title", "pages": [...]},
         "units": [{"pages": [<ints>, ...], "period_hint", "route", "num_cov",
                     "bscore", "template", "structure_authority", "spanning"},
                    ...]}
    ]}
A unit's "pages" is a reading-order list. len(pages) == 1 is a normal single
call. len(pages) > 1 is a SPANNING unit (the routing signal, not necessarily
carrying "spanning": true/false explicitly — both are tolerated): the first
page gets the ordinary prompt; each subsequent page gets a continuation call
with the OPEN table's real column signatures injected at runtime (ported
legacy build_continuation_prompt mechanism, decision #2); the chunks are
merged into ONE logical table (one table_id per period unit) before
html_to_cells/load — never one row per page. The legacy singular "page": int
key is still accepted for old fixtures (see _unit_pages).
"""
from __future__ import annotations
import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))  # FinancialParser/

LEGACY_PASS2_PARENT = os.path.join(
    ROOT, "findociq", "_legacy", "DELIVERABLE", "pillar3"
)
MINERU_EVAL_DIR = os.path.join(
    ROOT, "findociq", "experiments", "2026-06-29_mineru_eval"
)
PROMPTS_DIR = os.path.join(HERE, "prompts")
TEMPLATES_DIR = os.path.join(HERE, "templates")

for p in (LEGACY_PASS2_PARENT, MINERU_EVAL_DIR, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pass2.schema as legacy_schema          # noqa: E402
import pass2.render as legacy_render          # noqa: E402
import pass2.extract as legacy_extract        # noqa: E402
from pass2.extract import _is_transport_error  # noqa: E402

from html_to_cells import parse_html          # noqa: E402  (mineru_eval dir)

import cost as cost_mod                       # noqa: E402  (pipeline/cost.py)
import verify_cells                           # noqa: E402  (pipeline/verify_cells.py)
from gemini_client import build_client        # noqa: E402  (pipeline/gemini_client.py)

# ===========================================================================
# MODEL PIN (reconciliation decision — see module docstring)
# ===========================================================================
MODEL = "gemini-3.5-flash"          # legacy pass2/schema.py:9; auto_extract.py MODELS[0]
THINKING_BUDGET = 0                  # findociq/pipeline/cost.py:4


def _stage2_core() -> str:
    with open(os.path.join(PROMPTS_DIR, "stage2_core.txt")) as f:
        return f.read()


# ===========================================================================
# PROMPT BUILDING — legacy anchor/boundary lead (ported: extract.py:181-192,
# 265-271) + NEW pipeline HTML output contract (stage2_core.txt), NOT
# legacy's JSON-schema _PROMPT body. Continuation context (decision #2) ports
# extract.py:276-304's runtime column-signature injection, adapted to plain
# column-label strings (HTML flow has no GTable objects).
# ===========================================================================
def build_prompt_html(sec_no: str, sec_title: str, page: int, framing: str) -> str:
    """Single-page unit prompt: legacy anchor rule + HTML contract.
    Ported from pass2/extract.py:265-271 (single-page lead) + :181-192 (_anchor)."""
    anchor = legacy_extract._anchor(sec_no, sec_title)
    framing_note = (
        "This page shows a BORDERLESS table — no ruling lines; infer columns "
        "from aligned numeric right-edges."
        if framing == "borderless" else
        "This page shows a RULED (bordered) table — column boundaries are "
        "drawn; use them as the grid anchor."
    )
    lead = (
        f"You are given PDF page {page} — section {sec_no} '{sec_title}' "
        f"of a bank's regulatory disclosure.\n\n"
        f"{framing_note}\n\n"
        f"{anchor}"
    )
    return lead + "\n\n" + _stage2_core()


def build_continuation_prompt_html(sec_no: str, sec_title: str, page: int,
                                    prev_columns: list[str]) -> str:
    """Continuation-chunk prompt: legacy runtime column-signature injection.
    Ported from pass2/extract.py:276-304 (build_continuation_prompt), adapted
    to plain column-label strings for the HTML output flow. Not exercised in
    the DBS smoke test (both units are independent single-page tables), but
    wired for future multi-page BORDERED_MULTI / spanning units."""
    anchor = legacy_extract._anchor(sec_no, sec_title)
    open_cols_desc = " | ".join(prev_columns) if prev_columns else "(none captured)"
    context_block = (
        "═══════════════════════════════════════════════════\n"
        "CONTEXT FROM PREVIOUS PAGE\n"
        "═══════════════════════════════════════════════════\n"
        f"The previous page's table had these columns (left to right): {open_cols_desc}\n"
        "If this page continues that table (same columns, no new section heading), "
        "emit it as ONE table with the same columns — do NOT repeat the header rows.\n"
        "If a genuinely new table starts (different columns), extract it as its own table."
    )
    lead = (
        f"You are given PDF page {page} — continuation of section {sec_no} "
        f"'{sec_title}'.\n\n{anchor}\n\n{context_block}"
    )
    return lead + "\n\n" + _stage2_core()


def _html_config():
    """Ported from auto_extract.py:41-43 — plain-text HTML output, thinking_budget=0."""
    from google.genai import types
    cfg = types.GenerateContentConfig(
        response_mime_type="text/plain", temperature=0.0, max_output_tokens=65536,
    )
    try:
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=THINKING_BUDGET)
    except Exception:
        pass
    return cfg


def _call_with_retry(client, parts, cfg, label: str):
    """Same-model retry only (3x, legacy backoff pattern extract.py:522-536).
    NO cross-family fallback — non-transport failures raise immediately and
    the unit is flagged by the caller."""
    last_err = None
    for attempt in range(3):
        try:
            return client.models.generate_content(model=MODEL, contents=parts, config=cfg)
        except Exception as e:
            last_err = e
            if _is_transport_error(e):
                wait = 15 * (2 ** attempt) + random.uniform(0, 5)
                print(f"      ⏳ {e.__class__.__name__} on {label} — "
                      f"retry {attempt + 1}/3 in {wait:.0f}s")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"transport error persisted after 3 retries: {last_err}") from last_err


# ===========================================================================
# MANIFEST / FILTERING
# ===========================================================================
def load_manifest(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _section_matches(section: dict, selector: str) -> bool:
    sec_no = str(section.get("sec_no", ""))
    title = str(section.get("title", ""))
    if selector == sec_no:
        return True
    return selector.lower() in title.lower()


def select_docs(manifest: dict, doc_filter: str | None, section_selector: str | None) -> list[dict]:
    docs = manifest.get("docs", [])
    if doc_filter:
        docs = [d for d in docs if d.get("doc_id") == doc_filter]
    if section_selector:
        matched = [d for d in docs if _section_matches(d.get("section", {}), section_selector)]
        if not matched:
            available = sorted({
                (str(d.get("section", {}).get("sec_no")), d.get("section", {}).get("title", ""))
                for d in manifest.get("docs", [])
            })
            lines = "\n".join(f"  {sec_no}\t{title}" for sec_no, title in available)
            sys.exit(
                f"--section {section_selector!r} matched no section in this manifest.\n"
                f"Available sections:\n{lines}"
            )
        docs = matched
    return docs


def select_units(doc: dict, only: str | None) -> list[dict]:
    units = doc.get("units", [])
    if only:
        units = [u for u in units if str(u.get("template", "")).lower() == only.lower()]
    return units


def framing_for_route(route) -> str:
    """Framing follows the CLASS FAMILY (BORDERLESS*), not one exact class
    name. Spanning units carry a per-page route list — the unit's framing is
    its first page's class."""
    if isinstance(route, list):
        route = route[0] if route else ""
    return "borderless" if str(route).startswith("BORDERLESS") else "ruled"


def _resolve_period(table_period: str | None, period_hint: str | None) -> str | None:
    """The table's own parsed period (html_to_cells._parse_period on the
    context rows Gemini emitted) wins; otherwise normalize the manifest's
    routing-provided period_hint through the SAME deterministic parser so
    every stored period is ISO regardless of how a bank prints dates. An
    unparseable hint is kept raw rather than dropped."""
    if table_period:
        return table_period
    if period_hint:
        from html_to_cells import _parse_period
        return _parse_period([period_hint]) or period_hint
    return None


def _unit_pages(u: dict) -> list[int]:
    """New shape: "pages": [ints] (reading order). Tolerate the legacy
    singular "page": int shape (the original DBS smoke fixture)."""
    if "pages" in u:
        return [int(p) for p in u["pages"]]
    return [int(u["page"])]


def _unit_label(doc_id: str, pages: list[int]) -> str:
    p_str = str(pages[0]) + (f"-{pages[-1]}" if len(pages) > 1 else "")
    return f"{doc_id}_p{p_str}"


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _merge_continuation(base, cont) -> int:
    """Append a continuation chunk's rows onto the still-open base Table
    in-memory (one logical table per period — never a second table_id per
    page). Dedupes rows the model re-printed across the page break:
      1. exact duplicate line_no already present in base, or
      2. a repeated boundary header/label (no line_no) matching the last row
         already in base — the common "double-printed header row" case.
    Returns the number of rows actually appended."""
    seen_line_nos = {r.line_no for r in base.rows if r.line_no}
    boundary_label = _norm_label(base.rows[-1].label) if base.rows else None
    next_idx = len(base.rows)
    idx_map: dict[int, int] = {}
    appended = 0
    for i, r in enumerate(cont.rows):
        if r.line_no and r.line_no in seen_line_nos:
            continue
        if i == 0 and not r.line_no and boundary_label is not None \
                and _norm_label(r.label) == boundary_label:
            continue
        new_idx = next_idx
        idx_map[r.row_idx] = new_idx
        parent_idx = idx_map.get(r.parent_idx) if r.parent_idx is not None else None
        if parent_idx is None and r.level > 0:
            for j in range(len(base.rows) - 1, -1, -1):   # html_to_cells.py:218-222 pattern
                if base.rows[j].level == r.level - 1:
                    parent_idx = base.rows[j].row_idx
                    break
        base.rows.append(type(r)(row_idx=new_idx, level=r.level, kind=r.kind,
                                  line_no=r.line_no, label=r.label,
                                  parent_idx=parent_idx, cells=r.cells))
        next_idx += 1
        appended += 1
        if r.line_no:
            seen_line_nos.add(r.line_no)
    base.warnings.extend(cont.warnings)
    return appended


# ===========================================================================
# DRY-RUN PLANNING
# ===========================================================================
def plan_and_estimate(docs: list[dict], only: str | None) -> float:
    total_lo = total_hi = 0.0
    n_calls = 0
    for doc in docs:
        units = select_units(doc, only)
        sec = doc.get("section", {})
        for u in units:
            pages = _unit_pages(u)
            framing = framing_for_route(u.get("route", ""))
            n_pages_equiv = 2 if framing == "borderless" else 1  # PDF page + rendered image

            label0 = _unit_label(doc["doc_id"], [pages[0]])
            prompt0 = build_prompt_html(sec.get("sec_no", ""), sec.get("title", ""), pages[0], framing)
            in_tok0 = cost_mod.local_input_estimate(prompt0, n_pages=n_pages_equiv)
            print(f"  [plan] {label0}  route={u.get('route')}  framing={framing}  "
                  f"model={MODEL}  thinking_budget={THINKING_BUDGET}")
            print("        " + cost_mod.preflight(in_tok0, label=label0))
            total_lo += cost_mod.dollars(in_tok0, 1500, 0)
            total_hi += cost_mod.dollars(in_tok0, 12000, 0)
            n_calls += 1

            for page in pages[1:]:
                label_n = f"{doc['doc_id']}_p{page}"
                prompt_n = build_continuation_prompt_html(
                    sec.get("sec_no", ""), sec.get("title", ""), page,
                    ["<prior chunk's real columns — resolved at runtime>"],
                )
                in_tok_n = cost_mod.local_input_estimate(prompt_n, n_pages=n_pages_equiv)
                print(f"  [plan] {label_n}  (continuation of {label0})  route={u.get('route')}  "
                      f"framing={framing}")
                print("        " + cost_mod.preflight(in_tok_n, label=label_n))
                total_lo += cost_mod.dollars(in_tok_n, 1500, 0)
                total_hi += cost_mod.dollars(in_tok_n, 12000, 0)
                n_calls += 1
    print(f"\n[dry-run] {n_calls} call(s) planned — est total ${total_lo:.4f}–${total_hi:.4f}  "
          f"(no API calls, no DB writes)")
    return total_hi


# ===========================================================================
# EXTRACTION
# ===========================================================================
def extract_unit(client, doc_id: str, pdf_path: str, sec: dict, u: dict, out_dir: str,
                 from_html: bool = False):
    """Run 1 call for a single-page unit, or a chunked first + continuation
    call sequence for a spanning (len(pages) > 1) unit, merging every
    continuation chunk into the FIRST chunk's open table in-memory. Returns
    (tables: list[Table], usages: list[dict]) — ONE logical table set for
    the whole unit, never one table per page.

    from_html=True replays existing <page>.html artifacts from out_dir —
    zero API calls, PDF never opened; a missing artifact raises
    FileNotFoundError (fails loudly, never FLAGGED)."""
    pages = _unit_pages(u)
    route = u.get("route", "")
    framing = framing_for_route(route)
    sec_no, sec_title = sec.get("sec_no", ""), sec.get("title", "")

    if from_html:
        def _one_call(page: int, prompt: str, label: str):
            html_path = os.path.join(out_dir, f"{page}.html")
            with open(html_path) as f:  # missing artifact → FileNotFoundError
                html = f.read()
            if not html.strip():
                # a 0-byte artifact is missing content (interrupted write /
                # empty response) — same loud contract as a missing file
                raise FileNotFoundError(f"empty extraction artifact: {html_path}")
            print(f"  ▶ {label}  route={route}  framing={framing}  from-html={html_path}")
            usage = {"label": label, "from_html": True, "est_cost_usd": 0.0}
            return html, usage
    else:
        from google.genai import types

        def _one_call(page: int, prompt: str, label: str):
            pdf_bytes = legacy_render.cut_pdf(pdf_path, [page])
            parts = [types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")]
            if framing == "borderless":
                for img in legacy_render.render_images(pdf_path, [page]):
                    parts.append(types.Part.from_bytes(data=img, mime_type="image/png"))
            parts.append(prompt)
            cfg = _html_config()
            print(f"  ▶ {label}  route={route}  framing={framing}  model={MODEL} ...")
            resp = _call_with_retry(client, parts, cfg, label)
            html = resp.text or ""
            html_path = os.path.join(out_dir, f"{page}.html")
            with open(html_path, "w") as f:
                f.write(html)
            usage = legacy_extract.log_usage(resp, label=label, image_used=(framing == "borderless"))
            print(f"    ✓ {label} → {html_path}  "
                  f"[{usage.get('prompt_tokens', '?')}in/{usage.get('output_tokens', '?')}out tok, "
                  f"${usage.get('est_cost_usd', 0):.5f}]")
            return html, usage

    label0 = f"{doc_id}_p{pages[0]}"
    prompt0 = build_prompt_html(sec_no, sec_title, pages[0], framing)
    html0, usage0 = _one_call(pages[0], prompt0, label0)
    tables = parse_html(html0)
    if not tables:
        # a router-selected unit is table-bearing by construction — zero
        # parsed tables is an extraction failure; raise so the caller FLAGs
        # the unit instead of silently loading a partial doc
        raise RuntimeError(
            f"{label0}: 0 tables parsed from HTML ({len(html0)} chars)")
    usages = [usage0]

    if len(pages) > 1 and tables:
        open_table = tables[-1]
        for page in pages[1:]:
            prev_columns = [c.leaf_label for c in open_table.cols]
            label_n = f"{doc_id}_p{page}"
            prompt_n = build_continuation_prompt_html(sec_no, sec_title, page, prev_columns)
            html_n, usage_n = _one_call(page, prompt_n, label_n)
            usages.append(usage_n)
            cont_tables = parse_html(html_n)
            if cont_tables:
                n_appended = _merge_continuation(open_table, cont_tables[0])
                n_deduped = len(cont_tables[0].rows) - n_appended
                print(f"    ↳ merged {n_appended} row(s) from p{page} into the open table "
                      f"from p{pages[0]}  ({n_deduped} deduped as repeated header/boundary rows)")
                tables.extend(cont_tables[1:])  # any further distinct tables on the chunk page
    return tables, usages


def extract_doc(client, doc: dict, only: str | None, out_root: str,
                from_html: bool = False):
    """Run Gemini call(s) for every selected unit (chunked internally for
    spanning units). Returns [(unit, tables: list[Table], usages: list[dict]),
    ...] — one merged table set per unit, never per page."""
    units = select_units(doc, only)
    sec = doc.get("section", {})
    doc_id = doc["doc_id"]
    pdf_path = doc["pdf"]
    out_dir = os.path.join(out_root, doc_id)
    os.makedirs(out_dir, exist_ok=True)

    legacy_schema.USAGE_LOG_PATH = os.path.join(out_dir, "usage_ledger.jsonl")

    results = []
    for u in units:
        pages = _unit_pages(u)
        try:
            tables, usages = extract_unit(client, doc_id, pdf_path, sec, u, out_dir,
                                          from_html=from_html)
        except Exception as e:
            if from_html and isinstance(e, FileNotFoundError):
                raise  # --from-html contract: missing artifact fails loudly
            print(f"  ✗ FLAGGED {_unit_label(doc_id, pages)}: {e}")
            continue
        results.append((u, tables, usages))
    return results


# ===========================================================================
# DB LOAD — doc-scoped idempotent load, ported from
# findociq/experiments/2026-06-29_mineru_eval/load_to_db.py:27-67 (load()).
# Unlike load_to_db.fresh_db(), this NEVER wipes the whole database — it
# deletes only doc_id-scoped rows first (idempotent per-doc re-run), leaving
# every other doc_id (e.g. ocbc_nsfr_2025) untouched.
# ===========================================================================
def _delete_doc(con: sqlite3.Connection, doc_id: str) -> None:
    cur = con.cursor()
    cur.execute("DELETE FROM cell_fact WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM row_dim WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM col_dim WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM table_t WHERE doc_id = ?", (doc_id,))
    # seed fixtures (schema_v5.sql) also populate `section` rows FK'd to
    # document(doc_id) — clear those too or the document delete below violates FK.
    cur.execute("DELETE FROM section WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM document WHERE doc_id = ?", (doc_id,))


def load_doc(con: sqlite3.Connection, doc: dict, template: str,
             results: list[tuple[dict, list, list[dict]]]) -> dict:
    doc_id = doc["doc_id"]
    sec = doc.get("section", {})
    bank = doc.get("bank", "")
    institution = legacy_schema.BANKS.get(bank, {}).get("institution", bank)

    parsed = []  # (unit, Table, resolved_period)
    for u, tables, _usages in results:
        for t in tables:
            period = _resolve_period(t.period, u.get("period_hint"))
            parsed.append((u, t, period))

    _delete_doc(con, doc_id)

    doc_period = max((p for _u, _t, p in parsed if p), default=None)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO document(doc_id,institution,doc_family,source_file,doc_period) "
        "VALUES (?,?,?,?,?)",
        (doc_id, institution, "pillar3", os.path.basename(doc["pdf"]), doc_period),
    )

    n_cells = 0
    n_tables = 0
    for u, t, period in parsed:
        pages = _unit_pages(u)
        page_range = str(pages[0]) + (f"-{pages[-1]}" if len(pages) > 1 else "")
        table_id = f"{template}_{period or f'p{pages[0]}'}"
        title = t.context_rows[0] if t.context_rows else sec.get("title", "")
        cur.execute(
            "INSERT INTO table_t(doc_id,table_id,table_title,table_type,section_id,"
            "section_no,period,geo_key,page_range) VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, table_id, title, template, None, sec.get("sec_no"), period, None, page_range),
        )
        n_tables += 1

        groups, gid = {}, 100
        for c in t.cols:
            if c.group and c.group not in groups:
                groups[c.group] = gid
                cur.execute(
                    "INSERT INTO col_dim(doc_id,table_id,col_id,col_hierarchy,col_parent,"
                    "col_leaf_label,unit) VALUES (?,?,?,?,?,?,?)",
                    (doc_id, table_id, gid, 0, None, c.group, "S$m"),
                )
                gid += 1
        for c in t.cols:
            cur.execute(
                "INSERT INTO col_dim(doc_id,table_id,col_id,col_hierarchy,col_parent,"
                "col_leaf_label,unit) VALUES (?,?,?,?,?,?,?)",
                (doc_id, table_id, c.col_id, 1, groups.get(c.group), c.leaf_label, "S$m"),
            )

        for r in t.rows:
            parent_rowid = t.rows[r.parent_idx].row_idx if r.parent_idx is not None else None
            cur.execute(
                "INSERT INTO row_dim(doc_id,table_id,row_id,row_hierarchy,row_parent,"
                "row_leaf_label,line_no,unit) VALUES (?,?,?,?,?,?,?,?)",
                (doc_id, table_id, r.row_idx, r.level, parent_rowid, r.label, r.line_no, "S$m"),
            )
        for r in t.rows:
            for c in r.cells:
                cur.execute(
                    "INSERT INTO cell_fact(doc_id,table_id,row_id,col_id,colspan,value_raw,"
                    "value_num,cell_state,is_shade,period) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, table_id, r.row_idx, c.col_id, c.colspan, c.value_raw,
                     c.value_num, c.cell_state, c.is_shade, period),
                )
                n_cells += 1

    con.commit()
    return dict(doc_id=doc_id, tables=n_tables, cells=n_cells)


# ===========================================================================
# POST-LOAD VERIFICATION GATE — spec findociq/docs/specs/
# 2026-07-06-post-load-verification-gate.md (binding). Every doc load (BOTH
# the live path and --from-html replay share this single call site in main())
# is not COMPLETE until every table just loaded re-verifies clean against its
# source PDF's text layer. Zero-LLM, pure pdfplumber (verify_cells.verify_doc)
# — this NEVER adds a model call. Default-ON, no disable flag (humans-out-of-
# the-loop rule): a doc with values_missing > 0 on any table is FLAGGED and
# the run must not report it as clean. verify_cells.verify_doc raises/exits
# loudly if the source PDF can't be opened — this function does not catch
# that, so a bad PDF path fails the run instead of silently skipping the gate.
# ===========================================================================
VERIFY_OUT_DIR = os.path.join(HERE, "route", "out", "verify")


def verify_and_report(manifest: dict, con: sqlite3.Connection, doc_id: str,
                       out_dir: str = VERIFY_OUT_DIR) -> tuple[dict, bool]:
    """Run the post-load verification gate for doc_id and persist the report.

    Delegates the actual pdfplumber-facing check to verify_cells.verify_doc
    (same table-level algorithm/thresholds already fleet-validated 2026-07-06
    — untouched here). Writes the report JSON under out_dir using
    verify_cells' own filename convention (<doc_id>_verify.json) and prints
    one line per table so the gate's outcome (verified-clean vs
    FLAGGED-verify, with the missing count) is visible without reading code.

    Returns (report, any_flagged).
    """
    report = verify_cells.verify_doc(manifest, con, doc_id)

    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, f"{doc_id}_verify.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    any_flagged = False
    for t in report["tables"]:
        n_missing = len(t["values_missing"])
        if n_missing:
            any_flagged = True
            print(f"  ✗ FLAGGED-verify {doc_id}::{t['table_id']}: "
                  f"{n_missing} value(s) missing from source PDF "
                  f"(of {t['values_checked']} checked) — report: {report_path}")
        else:
            print(f"  ✓ verified-clean {doc_id}::{t['table_id']} "
                  f"({t['values_checked']} value(s) checked)")
    return report, any_flagged


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--only", default=None, help="process only units whose template matches (e.g. nsfr)")
    ap.add_argument("--section", default=None,
                     help="process only units in this TOC section: exact sec_no (e.g. '12.9') "
                          "or case-insensitive substring of the section title")
    ap.add_argument("--db", default=os.path.join(ROOT, "findociq", "db", "final.db"))
    ap.add_argument("--doc", default=None, help="single doc_id filter")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-html", action="store_true",
                     help="re-load docs from existing route/out/extract/<doc_id>/<page>.html "
                          "artifacts — zero API calls, deterministic; fails loudly if a page's "
                          "HTML is missing")
    args = ap.parse_args()

    manifest = load_manifest(args.manifest)
    template = manifest.get("template", args.only or "")
    docs = select_docs(manifest, args.doc, args.section)
    if not docs:
        sys.exit(f"no docs matched --doc={args.doc!r} in manifest {args.manifest}")

    if args.dry_run:
        plan_and_estimate(docs, args.only)
        return

    if args.from_html:
        client = None
    else:
        client = build_client()

    out_root = os.path.join(HERE, "route", "out", "extract")
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON;")

    any_verify_flag = False
    for doc in docs:
        print(f"\n=== {doc['doc_id']} (section {doc.get('section', {}).get('sec_no')} "
              f"'{doc.get('section', {}).get('title')}') ===")
        results = extract_doc(client, doc, args.only, out_root, from_html=args.from_html)
        if not results:
            print(f"  (no successful extractions for {doc['doc_id']} — skipping DB load)")
            continue
        stats = load_doc(con, doc, template, results)
        print(f"  → loaded {stats['tables']} table(s), {stats['cells']} cell(s) "
              f"into {args.db} (doc_id={stats['doc_id']})")

        # Post-load verification gate (spec 2026-07-06-post-load-verification-
        # gate.md) — mandatory for both the live path and --from-html replay;
        # default-ON, no flag to disable it.
        _, doc_flagged = verify_and_report(manifest, con, doc["doc_id"])
        if doc_flagged:
            any_verify_flag = True
            print(f"  ⚠ {doc['doc_id']}: post-load verification FLAGGED — doc NOT clean")
        else:
            print(f"  ✓ {doc['doc_id']}: post-load verification passed — doc verified clean")

    con.close()
    print(f"\nrun_usage: {legacy_schema._run_usage}")
    if any_verify_flag:
        sys.exit(1)


if __name__ == "__main__":
    main()
