"""stage1_extract.chunk.extract — Gemini API calls, prompt building, unit grouping, caching."""
from __future__ import annotations
import os, sys, json, re, hashlib, datetime, time, random
from pathlib import Path

from google.genai import types

import stage1_extract.chunk.schema as schema
from .schema import (
    MODEL, AUDIT_DIR, INSTITUTION, DOC_DATE, ENABLE_BOUNDARY_CROP,
    Extraction, GTable,
    _call_log, _run_usage, _call_log_lock, _run_usage_lock,
    INPUT_PRICE_PER_M, OUTPUT_PRICE_PER_M, THINK_PRICE_PER_M,
    USAGE_LOG_PATH,
)
from .render import (
    cut_pdf, page_has_table_structure,
    render_images, render_images_with_page_numbers,
    _pil_image_from_bytes, _pil_image_to_bytes,
    compute_boundary_crop,
)
from .transforms import (
    _normalise_cell_states,
    validate_spans, validate_numbers, validate_labels, validate_letter_leafs,
    validate_column_bands, repair_column_bands,
)


# ===========================================================================
# BATCH MODE (billing lever only — model behavior/prompts/config unchanged)
# ===========================================================================
# The Gemini Batch API bills at 50% of interactive rates. When the batch
# orchestrator (pass2/batch.py) is driving extraction it flips BATCH_MODE on
# so log_usage records the discounted (truthful) cost. It changes NOTHING about
# the prompt, config, or response handling — batch is purely execution/billing.
BATCH_MODE     = False
BATCH_DISCOUNT = 0.5


# ===========================================================================
# GEMINI CONFIG
# ===========================================================================
def build_config(with_thinking: bool) -> types.GenerateContentConfig:
    kwargs = dict(
        response_mime_type="application/json",
        response_schema=Extraction,
        temperature=0.0,
        max_output_tokens=65536,
    )
    budget = 8192 if with_thinking else 0
    try:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
    except Exception:
        print("  ⚠️  ThinkingConfig not available in this SDK build — thinking left at default")
    try:
        return types.GenerateContentConfig(**kwargs)
    except TypeError:
        kwargs.pop("thinking_config", None)
        return types.GenerateContentConfig(**kwargs)


# ===========================================================================
# PROMPTS
# ===========================================================================
_PROMPT = """
═══════════════════════════════════════════════════
TABLE STRUCTURE
═══════════════════════════════════════════════════
For each table return:

title
  The printed table title verbatim. Include the reporting date if shown (e.g. "31 Dec 2025").
  If no explicit table title is printed, use the section heading as the title (e.g. "Key Metrics").

label_header
  The header of the row-label column (e.g. "$m", "ASF Item"). "" if none is printed.

continued_from_previous
  true ONLY when ALL of these hold:
  1. The columns are identical to the previous table (same count, same leaf labels).
  2. NO new bold heading, date-period label, or section header appears at the top of this chunk.
  3. The first substantive row is a data or total row — NOT a section_header or sub_header.
  If a new bold title, date header, or section_header exists before the first data row,
  set continued_from_previous=false and give this table its own title.

  DIFFERENT DATE PERIODS = DIFFERENT TABLES — always. Two blocks belong to different tables if:
  - A date/period label appears as a header between them, OR
  - Each block ends with a dated total row (e.g. "At 31 December 2025" then "At 31 December 2024")
    followed by a visual break before the next block with the same structure, OR
  - The same column structure repeats for a different reporting date.
  Never merge two date-period blocks into one table. Hard rule, not a judgment call.

columns  (left to right — DATA columns only)
  EXCLUDE the row-label column and the row-number column.
  Two-level headers: set group (spanning label) and leaf (sub-column label).
  Single-level headers: group=null, put the text in leaf.
  Scope/currency lines above headers (e.g. "Group – All Currencies") are NOT columns.
  SUB-LABEL ROW: if a row of descriptive labels sits between the column headers and the
  first data row, those ARE the column leaf values — NOT data rows or sub_header rows.
  EXAMPLE: if the PDF shows:
      Group header:  "Gross carrying amount of¹/"  spanning columns (a) and (b)
      Letter row:    (a)          (b)          (c)      ...
      Label row:     Defaulted    Non-defaulted Allowances ...
      First data row: 3,229       337,891      (3,615)  ...
  Then the correct columns are:
      {"group": "Gross carrying amount of¹/", "leaf": "Defaulted exposures"}
      {"group": "Gross carrying amount of¹/", "leaf": "Non-defaulted exposures"}
      {"group": null, "leaf": "Allowances and impairments"}
  The label row ("Defaulted exposures", "Non-defaulted exposures"...) becomes the leaf.
  The letter row ("(a)", "(b)"...) is discarded — it is just a reference label, not a header.
  NEVER emit the label row as sub_header or data rows.

  LETTER-ROW RULE (hard): If a row of single letters or bracketed letters — "(a)", "(b)",
  "(c)" etc. — appears anywhere in the header band, that entire row is a reference index.
  Discard it unconditionally. The descriptive text row immediately below it provides the
  leaf labels. This applies even when the letter row is the only row between the group
  header and the first data row. NEVER emit "(a)", "(b)" etc. as a leaf value.

rows  (EVERY row, top to bottom)
  row_id    Printed line number exactly as shown ("1", "4a"). null for rows with no number.
  row_type  "section_header" — category title, date/period header, shaded block header
                               (e.g. "31 Dec 2025", "CASH OUTFLOWS", "Loans"). No values.
            "data"           — normal line item.
            "total"          — bold total, grand total, or subtotal.
            "sub_header"     — bold divider introducing a sub-group, no values.
            "note"           — footnote or disclaimer line.
            Use "section_header" for date/period headers — NOT "sub_header".
  level     0 = section_header or grand total
            1 = primary line item
            2 = sub-item (indented, "of which", named breakdown)
            3 = sub-sub-item (rare)
  parent    null for level 0 and 1. For level 2+: row_id of nearest ancestor one level up.
            If that ancestor has no printed number, assign it a synthetic id ("h1","h2",…)
            and use the SAME id in both rows.
  label     Row label verbatim, including footnote markers. Do not re-indent or trim.
  values    One GCell per column (see CELL STATE below).
            section_header / sub_header / note rows use an empty list [].

═══════════════════════════════════════════════════
CATEGORY LABELS — NEVER DROP THEM
═══════════════════════════════════════════════════
Every category / portfolio / asset-class block MUST be captured as a row with
row_type="section_header" (level 0). These labels often appear on the same line as the
column headers — in that case the leading words are the CATEGORY LABEL, not column names.
Emit them as a section_header row. NEVER fold a category label into a column name.

═══════════════════════════════════════════════════
VALUE FIDELITY
═══════════════════════════════════════════════════
- Copy each value EXACTLY as printed, including thousands separators and signs:
  "62,195"  "(1,505)"  "17.0%"  "NM"  ">100"  "unchanged"
- If a number is split by a stray render space ("2 64,680"), join it ("264,680").
- Never invent, merge, split, reorder, or omit any row, column, or value.

═══════════════════════════════════════════════════
CELL STATE  (every GCell must carry one of these 5 states)
═══════════════════════════════════════════════════
  "reported" — any printed value: number, %, text, #, <0.5, NM, etc.
  "nil"      — a dash printed: -, –, —  (zero or negligible).  value = "-"
  "zero"     — printed "0" (explicitly zero).                  value = "0"
  "empty"    — cell is truly blank with no visual mark.         value = ""
  "grey"     — cell is visually shaded / greyed out.            value = ""

Rules — apply in this order:
  1. Any dash (-, –, —)        → cell_state="nil",   value="-"
  2. Printed "0"               → cell_state="zero",  value="0"
  3. Visually grey/shaded blank → cell_state="grey",  value=""
  4. Truly blank, no mark      → cell_state="empty", value=""
  5. Anything else printed     → cell_state="reported", value=verbatim

CRITICAL — DASH PRESERVATION (most common error):
  A printed dash "-" must ALWAYS be captured. NEVER replace a printed dash with "".
  When in doubt between "empty" and "nil": if ANYTHING is visually printed in the cell
  (even a faint or small dash), it is "nil" not "empty".
  Use "empty" ONLY for cells with NO mark whatsoever — completely blank white space.
  WRONG: {"value": "",  "cell_state": "empty"}   ← for a printed dash
  RIGHT: {"value": "-", "cell_state": "nil"}     ← for a printed dash

═══════════════════════════════════════════════════
COLUMN ALIGNMENT  (critical — never shift values)
═══════════════════════════════════════════════════
values must contain EXACTLY one GCell per column, in left-to-right order.
Even if a row is sparse, emit a GCell for every column slot — never skip or shift.

Example — 3 columns [A, B, C], only B has a value:
  [{"value":"","cell_state":"empty"},
   {"value":"42","cell_state":"reported"},
   {"value":"","cell_state":"empty"}]

Trailing empty columns must still be emitted — never truncate the list early."""


def _anchor(sect_num: str, sect: str) -> str:
    """Section boundary rule injected into every prompt type."""
    return (
        f"BOUNDARY RULE (mandatory):\n"
        f"1. Scan top-to-bottom until you find the heading '{sect_num}' or '{sect}'.\n"
        f"2. START extracting tables only AFTER that heading — ignore everything before it.\n"
        f"3. STOP immediately when you see any heading for a DIFFERENT section number "
        f"(e.g. any section that is not {sect_num}) — "
        f"tables after that point do not belong here.\n"
        f"4. If the heading is not found, return {{\"tables\": []}}.\n"
        f"5. If only narrative text exists under the heading (no grid), return {{\"tables\": []}}."
    )


_PROMPT_HASH = hashlib.sha1(_PROMPT.encode()).hexdigest()[:8]


def _col_sig(cols) -> tuple:
    """Canonical column signature preserving 2-level headers."""
    return tuple(
        f"{_clean_col(c.group)} > {_clean_col(c.leaf)}" if (c.group or "").strip()
        else _clean_col(c.leaf)
        for c in cols
    )


def _col_sig_leaves(cols) -> tuple:
    """Leaf-only signature — fallback when one side has groups and the other doesn't."""
    return tuple(_clean_col(c.leaf) for c in cols)


def _clean_col(s: str) -> str:
    """Normalize a column label: strip control chars, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", s or "")).strip()


def build_prompt(unit: dict) -> str:
    """Build the extraction prompt for a unit."""
    pages    = unit["pages"]
    pr       = ", ".join(map(str, pages))
    sect     = unit["leaves"][0]["title"]  if unit.get("leaves") else ""
    sect_num = unit["leaves"][0]["number"] if unit.get("leaves") else ""

    sep_note = (
        "TABLE SPLITTING RULES:\n"
        "- Every distinct table is a SEPARATE entry. Never merge two tables into one.\n"
        "- A new bold heading followed by its OWN NEW column headers = NEW table, "
        "even if the column names are identical to the previous table.\n"
        "- A bold category label that shares the same columns as the rows above and below "
        "it (no new column headers appear after it) is a section_header ROW inside the "
        "existing table — NOT a new table. Do NOT split on category labels.\n"
        "- Different date periods = different tables. If two blocks of rows are separated "
        "by a visual break and each ends with a dated total (e.g. 'At 31 December 2025' "
        "then 'At 31 December 2024'), they are TWO tables. Name each table's `title` "
        "with the date it belongs to (e.g. 'Credit Quality of Restructured Exposures — "
        "31 December 2025' and 'Credit Quality of Restructured Exposures — 31 December 2024')."
    )

    if unit["type"] == "multiple":
        sections_desc = "; ".join(
            f'{lf["number"]} "{lf["title"]}"'
            for lf in unit["leaves"]
        )
        lead = (
            f"You are given PDF page {pages[0]} from a bank's regulatory disclosure.\n"
            f"This page contains tables from MULTIPLE sections (in reading order): {sections_desc}.\n\n"
            f"Rules:\n"
            f"- Read top-to-bottom. When you encounter a section heading, all tables that follow "
            f"belong to THAT section — until the next section heading appears.\n"
            f"- Do NOT extract tables that appear before the first listed section heading.\n"
            f"- Set `section_id` on each table to the section NUMBER it belongs to.\n"
            f"- Do NOT merge tables that belong to different sections.\n"
            f"- {sep_note}"
        )
    elif unit["type"] == "spanning":
        lead = (
            f"You are given PDF pages {pr} — section {sect_num} '{sect}' "
            f"of a bank's regulatory disclosure.\n\n"
            f"A table that spans a page break (same columns resume, header not repeated) → "
            f"merge into ONE table, set continued_from_previous=true on the continuation.\n"
            f"Genuinely different tables (different title or columns) → SEPARATE entries.\n"
            f"{sep_note}\n\n"
            f"{_anchor(sect_num, sect)}"
        )
    else:  # single
        lead = (
            f"You are given PDF page {pages[0]} — section {sect_num} '{sect}' "
            f"of a bank's regulatory disclosure.\n\n"
            f"{sep_note}\n\n"
            f"{_anchor(sect_num, sect)}"
        )

    return lead + "\n\n" + _PROMPT


def build_continuation_prompt(unit: dict, chunk_pages: list[int],
                               prev_tables: list) -> str:
    """Prompt for chunk 2+ of a spanning section."""
    pr       = ", ".join(map(str, chunk_pages))
    sect     = unit["leaves"][0]["title"]  if unit.get("leaves") else ""
    sect_num = unit["leaves"][0]["number"] if unit.get("leaves") else ""

    open_tables_desc = "\n".join(
        f'  - "{t.title or "(untitled)"}": columns [{" | ".join(_col_sig(t.columns))}]'
        for t in prev_tables
    )
    context_block = (
        f"═══════════════════════════════════════════════════\n"
        f"CONTEXT FROM PREVIOUS CHUNK\n"
        f"═══════════════════════════════════════════════════\n"
        f"These tables were partially extracted from earlier pages of section {sect_num}.\n"
        f"If this chunk continues any of them (same columns, no new section heading), "
        f"set continued_from_previous=true and emit only the new rows — do NOT repeat headers.\n"
        f"If a genuinely new table starts (different title or columns), create a fresh entry "
        f"with continued_from_previous=false.\n\n"
        f"Open tables:\n{open_tables_desc}"
    )

    lead = (
        f"You are given PDF pages {pr} — continuation of section {sect_num} '{sect}'.\n\n"
        f"{_anchor(sect_num, sect)}\n\n"
        f"{context_block}"
    )
    return lead + "\n\n" + _PROMPT


# ===========================================================================
# USAGE LOGGING
# ===========================================================================
def log_usage(resp, label: str, image_used: bool) -> dict:
    try:
        um        = getattr(resp, "usage_metadata", None)
        prompt_t  = getattr(um, "prompt_token_count", None) or 0
        output_t  = getattr(um, "candidates_token_count", None) or 0
        thought_t = getattr(um, "thoughts_token_count", None) or 0
        total_t   = getattr(um, "total_token_count", None) or 0
        cost = (prompt_t / 1e6 * INPUT_PRICE_PER_M) + (output_t / 1e6 * OUTPUT_PRICE_PER_M) + (thought_t / 1e6 * THINK_PRICE_PER_M)
        if BATCH_MODE:
            cost *= BATCH_DISCOUNT
        rec = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "script": "extract_to_excel", "label": label, "model": MODEL,
            "image_used": image_used,
            "prompt_tokens": prompt_t, "output_tokens": output_t,
            "thinking_tokens": thought_t, "total_tokens": total_t,
            "est_cost_usd": round(cost, 5),
            "batch": BATCH_MODE,
        }
        if BATCH_MODE:
            rec["batch_discount"] = BATCH_DISCOUNT
    except Exception as e:
        prompt_t = output_t = thought_t = 0
        cost = 0.0
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "script": "extract_to_excel", "label": label, "error": f"usage_capture_failed: {e}"}
    with _run_usage_lock:
        _run_usage["calls"]    += 1
        _run_usage["prompt"]   += prompt_t
        _run_usage["output"]   += output_t
        _run_usage["thinking"] += thought_t
        _run_usage["cost"]     += cost
    with _call_log_lock:
        _call_log.append(rec)
    if not schema.USAGE_LOG_PATH:
        if not getattr(log_usage, "_warned_no_path", False):
            print("   ⚠ usage ledger path not set — calls are NOT being recorded")
            log_usage._warned_no_path = True
    else:
        try:
            os.makedirs(os.path.dirname(schema.USAGE_LOG_PATH) or ".", exist_ok=True)
            with open(schema.USAGE_LOG_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception as e:
            print(f"   ⚠ usage ledger write FAILED ({e!r}) — call record lost: {rec.get('label', '?')}")
    return rec


# ===========================================================================
# EXTRACTION HELPERS
# ===========================================================================
def _to_extraction(resp) -> Extraction:
    """Prefer the SDK's parsed pydantic object; fall back to parsing text."""
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, Extraction):
        return _normalise_cell_states(parsed)
    raw = (resp.text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
    data = json.loads(raw)
    return _normalise_cell_states(Extraction(**data))


_TRANSPORT_SIGNALS = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
                      "timed out", "timeout", "TimeoutError", "DeadlineExceeded")


def _is_transport_error(e: Exception) -> bool:
    """True for server-side transport failures (503, 429, timeout).
    These must retry via backoff — never via the image path."""
    msg = f"{e.__class__.__name__} {e}"
    return any(sig in msg for sig in _TRANSPORT_SIGNALS)


def _reasonable(ext: Extraction) -> bool:
    """Sanity check on returned tables."""
    if not ext.tables:
        return True
    for t in ext.tables:
        if not t.columns or not t.rows:
            return False
        if not any(r.values for r in t.rows):
            return False
    return True


# ===========================================================================
# RESPONSE HANDLING  (shared by the sync path and the batch orchestrator)
# ===========================================================================
# These two helpers hold everything DOWNSTREAM of the model response text —
# finish-reason gate, usage logging, audit response.txt, JSON→Extraction parse,
# validators, meta build, parsed.json/meta.json write. extract_unit (sync) and
# pass2/batch.py both call them, so batch is byte-identical from response text
# onward by construction (no forked copy of the parse/validate/audit logic).
def _ingest_resp(resp, unit: dict, attach_image: bool,
                 save_audit: bool, udir: str) -> tuple:
    """From a GenerateContentResponse → (Extraction, usage). Raises on MAX_TOKENS."""
    try:
        fr = resp.candidates[0].finish_reason
        fr_name = fr.name if hasattr(fr, "name") else str(fr)
        if fr_name == "MAX_TOKENS":
            raise RuntimeError(f"MAX_TOKENS: output truncated for {unit['unit_id']}")
    except (AttributeError, IndexError):
        pass
    usage = log_usage(resp, unit["unit_id"], attach_image)
    if save_audit:
        with open(os.path.join(udir, "response.txt"), "w") as f:
            f.write(resp.text or "")
    return _to_extraction(resp), usage


def _finalize_unit(ext: Extraction, usage: dict, unit: dict, pdf_path: str,
                   pages: list[int], image_used: bool,
                   boundary_crops: dict, save_audit: bool, udir: str) -> tuple:
    """Validate a single/multiple unit's Extraction, build meta, write audit.
    Extracted verbatim from extract_unit's tail so the sync and batch paths
    share ONE implementation."""
    sids = tuple(lf["section_id"] for lf in unit.get("leaves", []))
    span_issues        = validate_spans(ext)
    number_issues      = validate_numbers(ext, pdf_path, pages, section_ids=sids, unit=unit)
    label_issues       = validate_labels(ext)
    letter_leaf_issues = validate_letter_leafs(ext)
    column_band_issues = validate_column_bands(ext, pdf_path, pages)
    # Repair runs AFTER detection, never before: detection must still report
    # what WAS wrong (the col-shift lines above are computed on the raw model
    # output), and every repair is appended to the SAME list so both the finding
    # and the correction land in meta.json. See repair_column_bands' docstring
    # for why this is not folded into _apply_transforms.
    column_band_issues += repair_column_bands(ext, pdf_path, pages)

    if span_issues:
        print(f"  ⚠  span violations in {unit['unit_id']}:")
        for s in span_issues: print(s)
    if number_issues:
        print(f"  ⚠  number recall issues in {unit['unit_id']} ({len(number_issues)} discrepancies):")
        for s in number_issues[:5]: print(s)
        if len(number_issues) > 5:
            print(f"     … and {len(number_issues)-5} more (see meta.json)")
    if label_issues:
        print(f"  ⚠  row-shift / duplicate labels in {unit['unit_id']}:")
        for s in label_issues: print(s)
    if letter_leaf_issues:
        print(f"  ⚠  bare letter leafs remain in {unit['unit_id']} (repair gate missed):")
        for s in letter_leaf_issues: print(s)
    if column_band_issues:
        print(f"  ⚠  column-band issues in {unit['unit_id']}:")
        for s in column_band_issues: print(s)

    meta = {
        "document":    os.path.basename(pdf_path),
        "bank":        schema.INSTITUTION,
        "doc_date":    schema.DOC_DATE,
        "model":       schema.MODEL,
        "prompt_hash": _PROMPT_HASH,
        "unit_id":     unit["unit_id"],
        "section_ids": [lf["section_id"] for lf in unit.get("leaves", [])],
        "section_titles": [lf.get("title", "") for lf in unit.get("leaves", [])],
        "pages":       pages,
        "type":        unit.get("type", "single"),
        "image_used":  image_used,
        "n_tables":    len(ext.tables),
        "n_rows":      sum(len(t.rows) for t in ext.tables),
        "usage":       usage,
        "validation":  {
            "span_issues":        span_issues,
            "number_issues":      number_issues,
            "label_issues":       label_issues,
            "letter_leaf_issues": letter_leaf_issues,
            "column_band_issues": column_band_issues,
        },
        "crop": (
            {str(p): {"y_px": v[0], "anchor": v[1]}
             for p, v in boundary_crops.items() if v is not None}
            or None
        ) if ENABLE_BOUNDARY_CROP else None,
    }
    if save_audit:
        with open(os.path.join(udir, "parsed.json"), "w") as f:
            f.write(ext.model_dump_json(indent=2))
        with open(os.path.join(udir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
    return ext, meta


# ===========================================================================
# CACHE HELPERS
# ===========================================================================
def _migrate_legacy_chunk_dirs(audit_dir: str):
    """One-time migration: move legacy {unit_id}_cN sibling dirs into
    {unit_id}/chunks/cN/ so they can no longer shadow unit dirs in lookups."""
    import re as _re, shutil as _shutil
    chunk_pat = _re.compile(r'^(.+)_(c\d+)$')
    p = Path(audit_dir)
    if not p.exists():
        return
    for d in sorted(p.iterdir()):
        if not d.is_dir():
            continue
        m = chunk_pat.match(d.name)
        if not m:
            continue
        base_id, chunk_id = m.group(1), m.group(2)
        base_dir = p / base_id
        dest_parent = base_dir / "chunks"
        dest = dest_parent / chunk_id
        if dest.exists():
            continue
        if not base_dir.exists():
            q = p / "_quarantine" / d.name
            q.parent.mkdir(parents=True, exist_ok=True)
            _shutil.move(str(d), str(q))
            print(f"   📦 quarantined legacy chunk dir '{d.name}' (no base unit dir)")
        else:
            dest_parent.mkdir(parents=True, exist_ok=True)
            _shutil.move(str(d), str(dest))
            print(f"   📦 migrated legacy chunk dir '{d.name}' → '{base_id}/chunks/{chunk_id}'")


def load_cached_unit(unit: dict, args) -> "Extraction | None":
    """Load and validate a unit's parsed.json from the audit cache."""
    udir = os.path.join(schema.AUDIT_DIR, unit["unit_id"])
    pj   = os.path.join(udir, "parsed.json")
    mj   = os.path.join(udir, "meta.json")
    if not os.path.exists(pj):
        return None
    meta = json.load(open(mj)) if os.path.exists(mj) else {}
    uid  = unit["unit_id"]

    if meta.get("partial"):
        print(f"   • {uid} — partial cache, skipping")
        return None
    cached_doc = meta.get("document", "")
    current_doc = os.path.basename(args.pdf)
    if cached_doc and cached_doc != current_doc:
        print(f"   • {uid} — cache mismatch (cached={cached_doc} current={current_doc}), skipping")
        return None
    cached_pages = meta.get("pages")
    if cached_pages is None or cached_pages != unit["pages"]:
        print(f"   • {uid} — cache mismatch (cached pages={cached_pages} current={unit['pages']}), skipping")
        return None
    cached_hash = meta.get("prompt_hash")
    if cached_hash is None or cached_hash != _PROMPT_HASH:
        reason = "legacy cache (no prompt_hash)" if cached_hash is None \
                 else f"prompt changed ({cached_hash} → {_PROMPT_HASH})"
        print(f"   • {uid} — {reason}, skipping")
        return None

    try:
        ext = _normalise_cell_states(Extraction(**json.load(open(pj))))
    except Exception as e:
        print(f"   • {uid} — cache load error ({e}), skipping")
        return None

    return ext


# ===========================================================================
# UNIT EXTRACTION
# ===========================================================================
def extract_unit(client, pdf_path: str, unit: dict, force_image: bool, with_thinking: bool,
                 save_audit: bool = True):
    """Run one Gemini call for a unit, with audit + optional image fallback."""
    pages   = unit["pages"]
    prompt  = build_prompt(unit)
    pdf_bytes = cut_pdf(pdf_path, pages)

    udir = os.path.join(schema.AUDIT_DIR, unit["unit_id"])
    if save_audit:
        os.makedirs(udir, exist_ok=True)
        with open(os.path.join(udir, "prompt.txt"), "w") as f:
            f.write(prompt)
        with open(os.path.join(udir, "pages.pdf"), "wb") as f:
            f.write(pdf_bytes)

    pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    config   = build_config(with_thinking)

    # Boundary crop (off by default — set ENABLE_BOUNDARY_CROP=True to activate)
    boundary_crops: dict[int, tuple[int, str] | None] = {}
    if ENABLE_BOUNDARY_CROP:
        for nl in unit.get("next_leaves", []):
            p = int(nl["start_page"])
            if p in pages:
                sec_num = re.sub(r"^[A-Z]\.", "", nl.get("number", "") or nl.get("section_id", ""))
                anchor = sec_num or nl.get("title", "")
                y = compute_boundary_crop(pdf_path, p, anchor)
                if y is None and anchor != nl.get("title", ""):
                    anchor = nl.get("title", "")
                    y = compute_boundary_crop(pdf_path, p, anchor)
                boundary_crops[p] = (y, anchor) if y is not None else None

    _crop_prompt_suffix = ""
    if ENABLE_BOUNDARY_CROP and any(v is not None for v in boundary_crops.values()):
        _crop_prompt_suffix = (
            "\n\nThis image is a partial page. Extract only tables visible in this image; "
            "do not infer continuation rows beyond it."
        )

    def _call(attach_image: bool):
        parts = [pdf_part]
        if attach_image:
            if ENABLE_BOUNDARY_CROP and boundary_crops:
                for img_bytes, page_no in render_images_with_page_numbers(pdf_path, pages):
                    if page_no in boundary_crops and boundary_crops[page_no] is not None:
                        y_px, _ = boundary_crops[page_no]
                        img = _pil_image_from_bytes(img_bytes)
                        w, h = img.size
                        img_bytes = _pil_image_to_bytes(img.crop((0, 0, w, min(y_px, h))))
                    parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
            else:
                for img in render_images(pdf_path, pages):
                    parts.append(types.Part.from_bytes(data=img, mime_type="image/png"))
        parts.append(prompt + _crop_prompt_suffix)
        last_err = None
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=schema.MODEL, contents=parts, config=config)
                break
            except Exception as e:
                last_err = e
                if _is_transport_error(e):
                    wait = 15 * (2 ** attempt) + random.uniform(0, 5)
                    print(f"      ⏳ {e.__class__.__name__} — waiting {wait:.0f}s before retry {attempt+1}/3")
                    time.sleep(wait)
                else:
                    raise
        else:
            raise last_err
        return _ingest_resp(resp, unit, attach_image, save_audit, udir)

    image_first = force_image
    try:
        ext, usage = _call(attach_image=image_first)
    except Exception as e:
        if _is_transport_error(e):
            raise
        if page_has_table_structure(pdf_path, pages[0]):
            print(f"      ↻ parse error ({e.__class__.__name__}) — retrying {unit['unit_id']} with image")
            ext, usage = _call(attach_image=True)
            image_first = True
        else:
            raise
    image_used = image_first

    if not image_used and not _reasonable(ext) and page_has_table_structure(pdf_path, pages[0]):
        print(f"      ↻ first response looked thin — retrying {unit['unit_id']} with image")
        ext, usage = _call(attach_image=True)
        image_used = True

    return _finalize_unit(ext, usage, unit, pdf_path, pages, image_used,
                          boundary_crops, save_audit, udir)


def extract_unit_chunked(client, pdf_path: str, unit: dict, force_image: bool,
                          with_thinking: bool, save_audit: bool,
                          chunk_size: int = 2) -> tuple:
    """For spanning units longer than chunk_size pages, split into chunks."""
    pages = unit["pages"]
    if unit["type"] != "spanning":
        return extract_unit(client, pdf_path, unit, force_image, with_thinking, save_audit)
    if len(pages) <= chunk_size:
        return extract_unit(client, pdf_path, unit, force_image, with_thinking, save_audit)

    chunks = [pages[i:i + chunk_size] for i in range(0, len(pages), chunk_size)]
    print(f"     ↷ chunking {len(pages)} pages into {len(chunks)} chunks of ≤{chunk_size}")

    all_tables: list = []
    combined_usage: dict = {}

    for ci, chunk_pages in enumerate(chunks):
        chunk_uid  = f"chunks/c{ci+1}"
        chunk_unit = dict(unit, pages=chunk_pages,
                          unit_id=f"{unit['unit_id']}/{chunk_uid}")

        try:
            ext, meta = extract_unit(client, pdf_path, chunk_unit,
                                     force_image, with_thinking, save_audit)
        except RuntimeError as e:
            if "MAX_TOKENS" not in str(e):
                raise
            if len(chunk_pages) <= 1:
                raise RuntimeError(
                    f"output truncated on single page p{chunk_pages[0]} "
                    f"in {unit['unit_id']} — cannot halve further") from e
            mid = len(chunk_pages) // 2
            print(f"      ✂ MAX_TOKENS on chunk {ci+1} (p{'+'.join(map(str,chunk_pages))}) "
                  f"— retrying as two halves")
            ext_a, meta_a = extract_unit(client, pdf_path,
                                         dict(chunk_unit, pages=chunk_pages[:mid],
                                              unit_id=f"{unit['unit_id']}/chunks/c{ci+1}a"),
                                         force_image, with_thinking, save_audit)
            ext_b, meta_b = extract_unit(client, pdf_path,
                                         dict(chunk_unit, pages=chunk_pages[mid:],
                                              unit_id=f"{unit['unit_id']}/chunks/c{ci+1}b"),
                                         force_image, with_thinking, save_audit)
            merged_tables = ext_a.tables + ext_b.tables
            merged_usage  = {k: meta_a.get("usage", {}).get(k, 0)
                               + meta_b.get("usage", {}).get(k, 0)
                             for k in set(meta_a.get("usage", {})) | set(meta_b.get("usage", {}))}
            ext  = Extraction(tables=merged_tables)
            meta = {"usage": merged_usage}

        for k, v in meta.get("usage", {}).items():
            combined_usage[k] = combined_usage.get(k, 0) + (v if isinstance(v, (int, float)) else 0)

        pr = "+".join(map(str, chunk_pages))
        ut = meta.get("usage", {})
        print(f"        chunk {ci+1}/{len(chunks)} p{pr}: {len(ext.tables)} table(s)  "
              f"[{ut.get('prompt_tokens','?')}in/{ut.get('output_tokens','?')}out tok]")

        # DROPPED-PAGE RESCUE. A multi-page chunk can come back having answered
        # for only some of its pages, and the table COUNT cannot detect that —
        # one table legitimately spans a chunk. `pages_with_no_output` judges per
        # page instead: a table-dense page none of whose numbers appear in any
        # returned cell was ignored. Re-ask for that page ALONE, then merge, so
        # a continuation is still rejoined by _merge_tables_into's title +
        # column-signature rule. See docs/specs/2026-08-12-dropped-page-rescue.md.
        if len(chunk_pages) > 1:
            dropped = pages_with_no_output(_page_texts(pdf_path, chunk_pages),
                                           ext.tables)
            for p in dropped:
                print(f"        ⟳ page {p} produced no table — re-extracting alone")
                try:
                    ext_p, meta_p = extract_unit(
                        client, pdf_path,
                        dict(chunk_unit, pages=[p],
                             unit_id=f"{unit['unit_id']}/chunks/c{ci+1}p{p}"),
                        force_image, with_thinking, save_audit)
                except Exception as e:
                    # Never fail the unit over a rescue: the chunk's own tables
                    # are already in hand, and a loud warning beats losing them.
                    print(f"        ⚠ rescue of page {p} FAILED ({e}) — "
                          f"page remains unextracted")
                    continue
                for k, v in meta_p.get("usage", {}).items():
                    combined_usage[k] = combined_usage.get(k, 0) + (
                        v if isinstance(v, (int, float)) else 0)
                print(f"        ⟳ page {p} rescued: {len(ext_p.tables)} table(s)")
                ext = Extraction(tables=list(ext.tables) + list(ext_p.tables))

        _merge_tables_into(all_tables, ext.tables)

        if save_audit and all_tables:
            udir = os.path.join(schema.AUDIT_DIR, unit["unit_id"])
            os.makedirs(udir, exist_ok=True)
            partial_ext = Extraction(tables=all_tables)
            with open(os.path.join(udir, "parsed.partial.json"), "w") as f:
                f.write(partial_ext.model_dump_json(indent=2))
            with open(os.path.join(udir, "meta.json"), "w") as f:
                json.dump({"unit_id": unit["unit_id"], "pages": pages,
                           "partial": True, "chunks_completed": ci + 1}, f)

    return _finalize_spanning(unit, all_tables, combined_usage, len(chunks),
                              pdf_path, save_audit)


_NUM_TOKEN_RX = re.compile(r"\d[\d,]*(?:\.\d+)?")
# A page carrying at least this many distinct numeric tokens is a TABLE page.
# Below it, a page is prose — a notes/commentary page legitimately yields no
# table and must not be re-extracted. Set from the corpus: the thinnest real
# exhibit seen (DBS 'Per share data', 7 rows x 3 cols) carries 21 tokens; the
# fattest prose page (DBS 2Q26 p6 notes block) carries 6.
_MIN_TOKENS_FOR_A_TABLE_PAGE = 12


def _numeric_tokens(text: str) -> set[str]:
    """Distinct numeric tokens in `text`, comma/decimal form preserved.

    Comparison is on the PRINTED form, not a parsed float, because that is what
    a cell holds verbatim ('5,624'). Single digits are dropped: footnote markers,
    list numbering and column ordinals are numeric but say nothing about whether
    a table was extracted."""
    return {t for t in _NUM_TOKEN_RX.findall(text or "")
            if len(t.replace(",", "").replace(".", "")) >= 2}


def _cell_tokens(tables: list) -> set[str]:
    """Every numeric token appearing in any cell of `tables`."""
    out: set[str] = set()
    for t in tables:
        for r in getattr(t, "rows", []):
            for cell in getattr(r, "values", []):
                out |= _numeric_tokens(getattr(cell, "value", ""))
    return out


def pages_with_no_output(page_texts: dict[int, str], tables: list) -> list[int]:
    """Pages that were SENT to the model but are represented by nothing it
    returned. Deterministic, no per-bank or per-document rule.

    A chunk of N pages returning fewer than N tables is NOT the signal — one
    table legitimately spans several pages, which is the whole reason spanning
    units exist. The signal is per page: a page dense enough to be a table page
    (`_MIN_TOKENS_FOR_A_TABLE_PAGE` distinct numeric tokens) whose tokens appear
    in NO returned cell contributed nothing, whatever the table count says.

    WHY THIS EXISTS: DBS's 2Q26 performance summary prints its overview twice —
    half-year basis on pages 4-6, quarter basis on 7-8. `overview_p4-8` chunked
    to [4,5] [6,7] [8]; the [6,7] call returned ONE table, page 6's per-share
    block, and dropped page 7's 'Selected income statement items ($m)' entirely.
    Nothing downstream noticed, so the whole quarter basis was missing from the
    database: 632 cells span 1H against 10 spanning 2Q, and every page-7 figure
    (5,624 / 3,483 / 6,093 ...) was absent.

    Pure — takes text, returns page numbers. No IO, no client."""
    seen = _cell_tokens(tables)
    missing = []
    for page_no, text in sorted(page_texts.items()):
        toks = _numeric_tokens(text)
        if len(toks) < _MIN_TOKENS_FOR_A_TABLE_PAGE:
            continue                      # prose page — nothing owed
        if toks & seen:
            continue                      # represented in some returned table
        missing.append(page_no)
    return missing


def _page_texts(pdf_path: str, pages: list[int]) -> dict[int, str]:
    """{1-based page number -> extracted text} for `pages`. Best-effort: a page
    whose text cannot be read yields '', which reads as a prose page and is
    therefore never re-extracted on a guess."""
    import pypdfium2 as pdfium
    out: dict[int, str] = {}
    try:
        doc = pdfium.PdfDocument(pdf_path)
    except Exception:
        return {p: "" for p in pages}
    try:
        for p in pages:
            try:
                out[p] = doc[p - 1].get_textpage().get_text_range()
            except Exception:
                out[p] = ""
    finally:
        doc.close()
    return out


def _merge_tables_into(all_tables: list, new_tables: list) -> None:
    """Merge a chunk's tables into the running spanning-unit table list.
    Continuation heuristic (title + column-signature match) extracted verbatim
    from extract_unit_chunked so the sync and batch spanning paths agree."""
    for t in new_tables:
        first_sub = next((r for r in t.rows if r.row_type not in ("note",)), None)
        t_norm = _norm_title(t.title)
        t_sig  = _col_sig(t.columns)
        t_sig_leaves = _col_sig_leaves(t.columns)
        has_fresh_header = (first_sub is not None and
                            first_sub.row_type in ("section_header", "sub_header"))

        target_idx = None
        if all_tables:
            for idx in range(len(all_tables) - 1, -1, -1):
                open_t = all_tables[idx]
                o_norm = _norm_title(open_t.title)
                title_matched = bool(t_norm and o_norm and t_norm == o_norm)
                weak_title = (not t.title.strip() or t.continued_from_previous)
                title_ok = weak_title or title_matched
                if not title_ok:
                    continue
                if has_fresh_header and not title_matched:
                    continue
                o_sig = _col_sig(open_t.columns)
                col_ok = (t_sig == o_sig) or (t_sig_leaves == _col_sig_leaves(open_t.columns))
                if col_ok:
                    target_idx = idx
                    break

        if target_idx is not None:
            all_tables[target_idx].rows.extend(t.rows)
        else:
            all_tables.append(t)


def _finalize_spanning(unit: dict, all_tables: list, combined_usage: dict,
                       chunks_n: int, pdf_path: str, save_audit: bool) -> tuple:
    """Validate the merged spanning Extraction, build combined meta, write audit.
    Extracted verbatim from extract_unit_chunked's tail; shared with batch."""
    pages = unit["pages"]
    combined_ext = Extraction(tables=all_tables)

    sids = tuple(lf["section_id"] for lf in unit.get("leaves", []))
    span_issues        = validate_spans(combined_ext)
    number_issues      = validate_numbers(combined_ext, pdf_path, pages, section_ids=sids, unit=unit)
    label_issues       = validate_labels(combined_ext)
    letter_leaf_issues = validate_letter_leafs(combined_ext)
    column_band_issues = validate_column_bands(combined_ext, pdf_path, pages)
    # Detect first, then repair — see _finalize_unit and repair_column_bands.
    column_band_issues += repair_column_bands(combined_ext, pdf_path, pages)
    if span_issues:
        print(f"  ⚠  span violations in {unit['unit_id']} (combined):")
        for s in span_issues: print(s)
    if number_issues:
        print(f"  ⚠  number recall in {unit['unit_id']} (combined, {len(number_issues)} issues):")
        for s in number_issues[:5]: print(s)
        if len(number_issues) > 5:
            print(f"     … and {len(number_issues)-5} more")
    if label_issues:
        print(f"  ⚠  row-shift / duplicate labels in {unit['unit_id']} (combined):")
        for s in label_issues: print(s)
    if letter_leaf_issues:
        print(f"  ⚠  bare letter leafs remain in {unit['unit_id']} (combined, repair gate missed):")
        for s in letter_leaf_issues: print(s)
    if column_band_issues:
        print(f"  ⚠  column-band issues in {unit['unit_id']} (combined):")
        for s in column_band_issues: print(s)

    combined_meta = {
        "unit_id":  unit["unit_id"], "pages": pages, "type": "spanning",
        "image_used": True, "usage": combined_usage,
        "chunks":   chunks_n,
        "document": os.path.basename(pdf_path),
        "bank":     schema.INSTITUTION,
        "doc_date": schema.DOC_DATE,
        "model":    schema.MODEL,
        "prompt_hash": _PROMPT_HASH,
        "section_ids":    [lf["section_id"] for lf in unit.get("leaves", [])],
        "section_titles": [lf.get("title", "") for lf in unit.get("leaves", [])],
        "n_tables": len(all_tables),
        "n_rows":   sum(len(t.rows) for t in all_tables),
        "partial":  False,
        "validation": {
            "span_issues":        span_issues,
            "number_issues":      number_issues,
            "label_issues":       label_issues,
            "letter_leaf_issues": letter_leaf_issues,
            "column_band_issues": column_band_issues,
        },
    }
    if save_audit:
        udir = os.path.join(schema.AUDIT_DIR, unit["unit_id"])
        os.makedirs(udir, exist_ok=True)
        tmp_path = os.path.join(udir, "parsed.json.tmp")
        with open(tmp_path, "w") as f:
            f.write(combined_ext.model_dump_json(indent=2))
        os.replace(tmp_path, os.path.join(udir, "parsed.json"))
        partial_path = os.path.join(udir, "parsed.partial.json")
        if os.path.exists(partial_path):
            os.remove(partial_path)
        with open(os.path.join(udir, "meta.json"), "w") as f:
            json.dump(combined_meta, f, indent=2)
    return combined_ext, combined_meta


# ===========================================================================
# UNIT GROUPING
# ===========================================================================
def _contig(pages: list[int]) -> list[list[int]]:
    """Split a sorted page list into contiguous runs: [3,4,5,8] -> [[3,4,5],[8]]."""
    runs, cur = [], []
    for p in sorted(pages):
        if cur and p == cur[-1] + 1:
            cur.append(p)
        else:
            if cur:
                runs.append(cur)
            cur = [p]
    if cur:
        runs.append(cur)
    return runs


def group_key(section: dict) -> str:
    """Top-level grouping key for a leaf."""
    num0 = str(section["number"]).split(".")[0]
    part = section.get("part")
    return f"{part}.{num0}" if part else num0


def _drop_containing_wrappers(leaves: list[dict]) -> list[dict]:
    """Containment guard: drop any extraction unit that STRICTLY WRAPS another —
    i.e. some other unit falls in its INTERIOR (start < other.start and
    other.end < end, both strict). Such a unit is an aggregating heading (e.g. a
    running-header remnant like 'FINANCIAL HIGHLIGHTS' left spanning p10-22 after
    its per-page children were re-homed); the interior units are the real
    per-page tables and carry the data. Drop the wrapper, keep the inner units.

    STRICT interior is deliberate: it must NOT fire when two sibling sections
    merely share a boundary PAGE (e.g. Pillar 3 A.6.3 p17-18 next to A.6.2 p17,
    or A.12.2.7 p45-49 next to A.12.2.8 p49 — end_page/start_page touch is a TOC
    span-estimate artifact, not containment). Verified inert on the proven
    Pillar 3 pipeline (DBS 4Q25: drops 0) while still catching the FS phantom.
    General deterministic invariant — no title/bank literal, no family branch."""
    def span(s):
        return int(s["start_page"]), int(s["end_page"])
    kept = []
    for a in leaves:
        a0, a1 = span(a)
        wraps = next((b for b in leaves if b is not a
                      and a0 < (b0 := int(b["start_page"])) and (b1 := int(b["end_page"])) < a1),
                     None)
        if wraps is not None:
            print(f"   ⊃ dropping wrapper unit '{a['section_id']}' p{a0}-{a1} "
                  f"(interior unit '{wraps['section_id']}' p{int(wraps['start_page'])}-"
                  f"{int(wraps['end_page'])}) — inner units carry the data")
            continue
        kept.append(a)
    return kept


def build_units(leaves: list[dict]) -> list[dict]:
    """One unit per leaf section (after dropping containing wrappers)."""
    leaves = _drop_containing_wrappers(leaves)
    units: list[dict] = []
    for i, s in enumerate(leaves):
        pages = list(range(int(s["start_page"]), int(s["end_page"]) + 1))
        typ = "single" if len(pages) == 1 else "spanning"
        sid_slug = s["section_id"].replace(".", "_")
        p_str = str(pages[0]) + (f"-{pages[-1]}" if len(pages) > 1 else "")
        uid = f"{sid_slug}_p{p_str}"
        boundary = pages[-1]
        next_leaves = [leaves[j] for j in range(i + 1, len(leaves))
                       if int(leaves[j]["start_page"]) == boundary]
        units.append({
            "type":       typ,
            "pages":      pages,
            "leaves":     [s],
            "unit_id":    uid,
            "group":      group_key(s),
            "next_leaf":  leaves[i + 1] if i + 1 < len(leaves) else None,
            "next_leaves": next_leaves,
        })
    units.sort(key=lambda u: u["pages"][0])
    return units


def load_sections() -> tuple[dict, list[dict]]:
    """Load the TOC. Two formats, sniffed by key shape:
    - legacy pillar3 PASS1 toc.json: sections carry number/start_page/end_page;
    - findociq FS final TOC: sections carry id/page_start/page_end/seq and
      has_tables (deterministic: PaddleOCR regions attributed by header
      coordinate windows). Sections without tables are not extraction units —
      they exist for the DB section table upstream. `number` is set to the
      ROOT ancestor id so group_key() groups a whole top-level section's
      leaves together, same semantics as pillar3 numbers."""
    if not os.path.exists(schema.TOC_PATH):
        sys.exit(f"{schema.TOC_PATH} not found — run:  python build_toc.py <pdf>")
    toc = json.load(open(schema.TOC_PATH))
    raw = toc.get("sections", [])
    if raw and "page_start" in raw[0]:
        roots: dict = {}
        for s in raw:                      # reading order: parents precede kids
            roots[s["id"]] = roots[s["parent_id"]] if s.get("parent_id") else s["id"]
        secs = [{
            "section_id": s["id"],
            "number":     roots[s["id"]],
            "part":       None,
            "title":      s["title"],
            "start_page": int(s["page_start"]),
            "end_page":   int(s["page_end"]),
            "kind":       s.get("kind", ""),
        } for s in raw
            # has_tables (region-attributed) is authoritative when present;
            # older files fall back to Gemini's kind field
            if s.get("has_tables", s.get("kind") != "prose_section")]
        return toc.get("document", {}), secs
    all_parts = sorted({s.get("part") for s in toc.get("sections", []) if s.get("part")})
    _PART_ORD = {None: 0}
    for i, p in enumerate(all_parts):
        _PART_ORD[p] = i
    secs = sorted(toc.get("sections", []),
                  key=lambda s: (int(s["start_page"]),
                                 _PART_ORD.get(s.get("part"), 0),
                                 [int(x) for x in s["number"].split(".")]))
    return toc.get("document", {}), secs


# ===========================================================================
# TABLE ROUTING HELPERS
# ===========================================================================
def _norm_words(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _title_score(table_title: str, leaf_title: str) -> float:
    """Token-overlap fraction of the leaf title covered by the table title."""
    tt, lt = _norm_words(table_title), _norm_words(leaf_title)
    return len(tt & lt) / (len(lt) or 1)


_CONT_STRIP_RE = re.compile(
    r"\(cont(?:inued|'d|'d|d)?\.?\)|cont(?:inued|'d|'d)\.?\b", re.I
)
_SECTION_NUM_RE = re.compile(r"^\d+(?:\.\d+)*\s+")


def _norm_title(s: str) -> str:
    """Canonical table title for continuation matching."""
    s = (s or "").lower()
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s)
    s = _CONT_STRIP_RE.sub("", s)
    s = _SECTION_NUM_RE.sub("", s.lstrip())
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def drop_next_section_tables(tables: list, unit: dict) -> tuple[list, list[dict]]:
    """Drop tables whose title matches the next section's leaf better than this
    unit's own — boundary-page spill from the section that starts on this unit's
    last page.

    Returns (kept_tables, drop_records). drop_records has one dict per dropped
    table: {"title", "anchor_label": None, "anchor_y": None,
            "owning_section": <next section_id>, "dropped": True,
            "reason": "next_section_title"}. Empty when the gate doesn't fire,
    so call sites can merge it into table_drops unconditionally.
    """
    nl = unit.get("next_leaf")
    if not nl or int(nl["start_page"]) != unit["pages"][-1]:
        return tables, []
    own = unit["leaves"][0]["title"]
    kept = []
    records = []
    for t in tables:
        sc_next = _title_score(t.title, nl["title"])
        sc_own  = _title_score(t.title, own)
        if sc_next >= 0.5 and sc_next > sc_own + 0.1:
            print(f"   ✂ dropped '{t.title[:40]}' — belongs to next "
                  f"section {nl['section_id']} (score {sc_next:.2f} vs own {sc_own:.2f})")
            records.append({"title": t.title, "anchor_label": None,
                            "anchor_y": None, "owning_section": nl["section_id"],
                            "dropped": True, "reason": "next_section_title"})
        else:
            kept.append(t)
    return kept, records


def route_tables(tables: list, leaves: list[dict]) -> list[tuple]:
    """Assign each table on a shared page to one subsection leaf."""
    leaves_ord = sorted(leaves, key=lambda lf: [int(x) for x in lf["number"].split(".")])
    leaf_by_num = {lf["number"]: lf for lf in leaves_ord}

    chosen: dict[int, tuple] = {}

    for ti, t in enumerate(tables):
        sid = (t.section_id or "").strip()
        if sid and sid in leaf_by_num:
            chosen[ti] = (leaf_by_num[sid], "section_id", 1.0)

    untagged = [ti for ti in range(len(tables)) if ti not in chosen]
    if untagged:
        taken_leaves = {chosen[ti][0]["section_id"] for ti in chosen}
        pairs = sorted(
            ((_title_score(tables[ti].title, lf["title"]), ti, lf["section_id"], lf)
             for ti in untagged for lf in leaves_ord),
            reverse=True, key=lambda x: x[0],
        )
        taken_title = set()
        for sc, ti, lid, lf in pairs:
            if sc <= 0 or ti in taken_title or (lid in taken_leaves and sc < 0.5):
                continue
            chosen[ti] = (lf, "title", sc)
            taken_title.add(ti)
            taken_leaves.add(lid)

    last_leaf = None
    free = [lf for lf in leaves_ord
            if lf["section_id"] not in {chosen[ti][0]["section_id"] for ti in chosen}]
    fi = 0
    for ti in range(len(tables)):
        if ti in chosen:
            last_leaf = chosen[ti][0]
        else:
            if last_leaf is not None:
                chosen[ti] = (last_leaf, "overflow", 0.0)
            else:
                lf = free[fi] if fi < len(free) else leaves_ord[-1]
                chosen[ti] = (lf, "order", 0.0)
                fi += 1

    out = []
    count_mismatch = len(tables) != len(leaves_ord)
    for ti, t in enumerate(tables):
        lf, method, sc = chosen[ti]
        flagged = method in ("order", "overflow") or sc < 0.34 or count_mismatch
        out.append((t, lf, method, sc, flagged))
    return out
