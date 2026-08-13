"""stage1_extract.chunk.transforms — post-extraction transforms and validators."""
from __future__ import annotations
import re
import hashlib
from collections import Counter
from typing import NamedTuple

import pypdfium2 as pdfium
import pdfplumber

from .schema import GCell, GTable, Extraction, _pdfium_lock, ENABLE_REGION_OWNERSHIP


_DASH_CHARS = {"-", "–", "—", "‐", "‑", "‒", "−"}


def is_true_continuation(prev: GTable | None, t: GTable) -> bool:
    """Does `t` genuinely continue `prev` across a page break?

    THE ONE definition of the test, shared by every caller. `GTable`'s own
    field says `continued_from_previous` means "rows continue under the same
    columns, header NOT repeated" — so the model's claim is only believed when
    the table LOOKS like a bare fragment: no title of its own, the same column
    count, and a first substantive row that is data rather than a repeated
    section/sub header.

    A table that fails this is a NEW TABLE whose printed caption merely says
    "(cont'd)". UOB 2Q26 p6 is exactly that — title "Financial Highlights
    (cont'd)", first row the section header "Key financial ratios (%) (cont'd)"
    — and the TOC had already split pages 5 and 6 into separate units, so there
    was no fragment to rejoin in the first place.

    Previously this test existed in two divergent copies in PASS2_v2 (the
    cached-unit path checked 3 conditions, the live path 5), so an identical
    table merged or did not purely by whether its unit happened to be cached.
    """
    first_sub = next((r for r in t.rows if r.row_type not in ("note",)), None)
    return bool(
        t.continued_from_previous and prev is not None
        and len(prev.columns) == len(t.columns)
        and not t.title.strip()
        and first_sub is not None
        and first_sub.row_type not in ("section_header", "sub_header")
    )


_CONTD_RE = re.compile(
    r"[\s,]*[\(\[]?\s*(?:cont(?:inued|'d|’d|d)?\.?)\s*[\)\]]?\s*$", re.I)


def _strip_contd(s: str) -> str:
    """'Key financial ratios (%) (cont'd)' -> 'Key financial ratios (%)'."""
    prev = None
    out = (s or "").strip()
    while prev != out:
        prev = out
        out = _CONTD_RE.sub("", out).strip()
    return out


def _caption_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _strip_contd(s).lower()).strip("_")


# The handle `fill_parents` mints for an unnumbered heading (see its
# `synthetic_counter`). Matched exactly, so a PRINTED row_id that happens to
# look like one ('h1' as a filing's own line label) is never rewritten.
_SYNTH_HANDLE_RX = re.compile(r"h\d+")
_HN_REF_RX = re.compile(r"\s*h(\d+)\s*", re.I)


def header_row_indices(rows) -> list[int]:
    """Indices of the rows an `hN` printed-parent reference counts, in order.

    THE ONE definition of what `hN` means, shared by the two places that have to
    agree: `load_v7.resolve_printed_parents`, which consumes the reference, and
    `merge_continuation_tables`, which must REWRITE it when appending a
    continuation shifts every ordinal. A header row is one immediately followed
    by a row at a strictly greater level; `section_header` rows are not numbered.

    `hN` is POSITIONAL and TABLE-SCOPED — it is an ordinal into this list, not an
    id — which is exactly why merging two tables invalidates it.
    """
    return [i for i, r in enumerate(rows)
            if getattr(r, "row_type", None) != "section_header"
            and i + 1 < len(rows) and (rows[i + 1].level or 0) > (r.level or 0)]


def _col_key(c) -> str:
    return re.sub(r"[^a-z0-9]+", "_",
                  f"{c.group or ''} {c.leaf or ''}".lower()).strip("_")


def _column_remap(prev: GTable, t: GTable) -> list[int] | None:
    """Position in `prev.columns` for each column of `t`, or None if any column
    of `t` has no free counterpart.

    A carry-over page routinely reprints only SOME of the columns — UOB's page 6
    drops both `+/(-)%` variance columns and keeps the three period columns
    (5 -> 3). So the test is not equal width but SUBSET: every column the
    continuation prints must map to a distinct column of the table it continues.
    Matched on the printed header, since at this stage periods are not parsed
    yet; duplicate headers are consumed left-to-right so the two identically
    labelled `+/(-)%` columns cannot both capture one candidate.
    """
    used, out = set(), []
    for c in t.columns:
        k = _col_key(c)
        j = next((i for i, pc in enumerate(prev.columns)
                  if i not in used and _col_key(pc) == k), None)
        if j is None:
            return None
        used.add(j)
        out.append(j)
    return out


def merge_continuation_tables(tables: list[GTable], warn: list[str] | None = None
                              ) -> tuple[list[GTable], list[int]]:
    """Several physical tables holding ONE logical table -> one GTable.

    The MIRROR of `split_caption_tables`, and the same failure it exists to
    prevent, from the other side. Splitting fixes one printed table that holds
    several logical ones; this fixes one logical table that the filing printed
    across a page break.

    UOB prints 'Key financial ratios (%)' over pages 5-6 and the TOC filed the
    halves as two sections, so the 27-leaf FS_RATIOS_KEY entry is split 8/15
    across two tables. `locate_tables` scores each table on its own, needs
    MIN_MATCH_FRACTION (13.5 of 27), and the 8-half never clears it — so
    net_interest_margin / cost_income_ratio / npl_ratio go unstamped in EVERY
    vintage even though the masterlist carries them verbatim.

    THE RULE (general, no per-bank and no per-document condition): a table
    continues its predecessor when

      (a) its first substantive row is a valueless CAPTION that CARRIES AN
          EXPLICIT CONTINUATION MARKER — '(cont'd)', '(continued)' — and whose
          text with that marker removed matches the predecessor's title or the
          predecessor's own opening caption; and
      (b) every one of its columns maps to a distinct column of the predecessor
          by printed header (see `_column_remap`).

    BOTH halves are load-bearing, and (a) is deliberately NOT satisfied by
    `continued_from_previous`. Measured over the whole corpus, trusting that flag
    merged DBS Pillar 3's '1.2 Average SGD LCR' into '1.1 Average All-Currency
    LCR' — two distinct disclosures that merely share column headers — and
    matching a bare repeated caption merged OCBC's 'FINANCIAL HIGHLIGHTS' pages
    into each other. Requiring the printed marker took the corpus from 29 merges
    across 13 documents down to only the genuine carry-overs. The marker is the
    filing TELLING us the exhibit continues; the flag is the model guessing.

    (b) then keeps even a marked table honest: something captioned '... (cont'd)'
    that does not share the predecessor's column headers is left alone. The
    caption row itself is dropped — it is a repeated header, not data — and every
    other row is re-laid onto the predecessor's column positions with `empty`
    cells for the columns the carry-over page did not reprint.

    Returns (tables, groups). `groups[i]` lists the ORIGINAL indices folded into
    `tables[i]`, its first element being the survivor. Callers need the whole
    group, not just the survivor: a positionally-aligned side-car (load_v7's
    geometry list) is realigned from `groups[i][0]`, and the merged table's PAGE
    RANGE must become the union of every source's pages.

    That page union is not cosmetic. STEP 5 verifies a table's numbers against
    the page range of the unit that owns it; a table merged into the p5 unit
    while holding rows printed on p6 fails verification, and run_doc's
    auto-re-extract loop then rewrites the section — observed on UOB 2Q26, where
    it destroyed the very table the merge had just repaired.
    """
    out: list[GTable] = []
    groups: list[list[int]] = []
    for i, t in enumerate(tables):
        prev = out[-1] if out else None
        rows = [r for r in t.rows if r.row_type not in ("note",)]
        head = rows[0] if rows else None
        # The caption must ACTUALLY carry a continuation marker — `_strip_contd`
        # has to change it. A bare repeated caption is not evidence: OCBC prints
        # 'FINANCIAL HIGHLIGHTS' at the top of several genuinely separate pages.
        marked = bool(head is not None and not head.values
                      and _strip_contd(head.label) != head.label.strip())
        cap_echo = bool(
            marked and prev is not None and _caption_key(head.label)
            and _caption_key(head.label) in {_caption_key(prev.title),
                                             _caption_key(prev.rows[0].label)
                                             if prev.rows else ""}
        )
        remap = _column_remap(prev, t) if (prev is not None and cap_echo) else None
        if remap is not None:
            width = len(prev.columns)
            # `hN` PARENT REFERENCES ARE ORDINALS INTO THIS TABLE'S OWN HEADER
            # LIST, so appending re-bases every one of them. Resolve each to the
            # header ROW while the source table is still the frame of reference,
            # then re-emit the ordinal against the merged table below.
            #
            # Left un-rebased, UOB p6's three references land on p5 rows: p6
            # numbers 'Liquidity coverage ratios' h1, 'Capital adequacy ratios'
            # h2 and 'Earnings per ordinary share' h3, while the merged table's
            # first three headers are 'Credit costs on loans', the 'Notes:' block
            # (level 0 followed by p6's level-1 rows — a header only AFTER the
            # merge) and LCR. Every sub-item re-parents one block up:
            # All-currency under Credit costs, Common Equity Tier 1 under Notes,
            # Basic under LCR. Measured on 2026-08-09: the merged 27-row table
            # matched 12 of 27 masterlist paths, under MIN_MATCH_FRACTION's 13.5,
            # so a correctly merged exhibit still went unstamped.
            #
            # That 'Notes:' case is why the ordinals cannot be rebased by a
            # pre-computed offset: the join can PROMOTE the predecessor's last
            # row to a header. Only the merged row list can be counted.
            src_headers = [t.rows[j] for j in header_row_indices(t.rows)]
            pending: list[tuple] = []
            # Synthetic row_ids also restart per table; they carry no parenting
            # (they land in `line_no`), but namespacing keeps them unique.
            handles = {r.row_id: f"c{i}{r.row_id}" for r in t.rows
                       if r.row_id and _SYNTH_HANDLE_RX.fullmatch(r.row_id)}
            for r in t.rows:
                if r is head and cap_echo:
                    continue                      # repeated header, not data
                vals = [GCell(value="", cell_state="empty") for _ in range(width)]
                for ci, cell in enumerate(r.values[:len(remap)]):
                    vals[remap[ci]] = cell
                r.values = vals
                if r.row_id in handles:
                    r.row_id = handles[r.row_id]
                m = _HN_REF_RX.fullmatch(r.parent) if isinstance(r.parent, str) else None
                if m:
                    n = int(m.group(1)) - 1
                    # Dropped either way: a stale ordinal that survives resolves
                    # to the WRONG block, where None falls back to the position
                    # walk. Restored below when it resolved inside the source.
                    r.parent = None
                    if 0 <= n < len(src_headers):
                        pending.append((r, src_headers[n]))
                elif r.parent in handles:
                    r.parent = handles[r.parent]
                prev.rows.append(r)
            ordinal = {id(prev.rows[j]): k + 1
                       for k, j in enumerate(header_row_indices(prev.rows))}
            for child, parent_row in pending:
                if id(parent_row) in ordinal:
                    child.parent = f"h{ordinal[id(parent_row)]}"
            groups[-1].append(i)
            if warn is not None:
                warn.append(
                    f"{t.title!r}: merged into {prev.title!r} as a printed "
                    f"continuation ({len(t.columns)}/{width} columns reprinted)")
            continue
        t.continued_from_previous = False
        out.append(t)
        groups.append([i])
    return out, groups


def resolve_continuations(tables: list[GTable]) -> tuple[list[GTable], list[int]]:
    """Merge genuine page-break fragments within ONE unit; clear the flag on
    every table that survives as its own.

    `load_v7._load_table` refuses any table still carrying
    `continued_from_previous` — its contract is that continuations are merged
    before loading. Nothing on the LOAD path honoured that contract:
    `run_doc.build_units_from_audit` reads each unit's `parsed.json` verbatim
    and never passes through PASS2_v2's bucketing, so a flag the model set
    travelled untouched into the loader and aborted the whole document. UOB
    2Q26 lost all 47 units and 832 verified rows to one stale boolean.

    Clearing is the point as much as merging: the flag must never outlive the
    decision it feeds, or the loader is handed "I am a fragment" by a table
    that plainly is not one.

    NOTE — scope is one unit's table list. A fragment split across two ROUTER
    UNITS cannot be rejoined here (the loader is per-unit and each unit owns
    its section); such a table is correctly kept whole as its own table.

    Returns (tables, kept_indices). `kept_indices[i]` is the ORIGINAL index of
    `tables[i]`, so a caller holding a positionally-aligned side-car can
    realign it. load_v7's geometry list is exactly that, and a merge shortens
    this list — without the mapping every table after a merge would silently
    take its neighbour's geometry.
    """
    out: list[GTable] = []
    kept: list[int] = []
    for i, t in enumerate(tables):
        if is_true_continuation(out[-1] if out else None, t):
            out[-1].rows.extend(t.rows)
            continue
        t.continued_from_previous = False
        out.append(t)
        kept.append(i)
    return out, kept


def _normalise_cell_states(ext: Extraction) -> Extraction:
    """Post-extraction normalisation: fix mis-classified cell states.
    A printed dash must always be nil — schema pressure sometimes causes Gemini
    to emit cell_state='empty' for dashes. Correct it here as defence in depth."""
    for t in ext.tables:
        for row in t.rows:
            for cell in row.values:
                if isinstance(cell, GCell):
                    if cell.value.strip() in _DASH_CHARS:
                        cell.cell_state = "nil"
                        cell.value = "-"
                    elif cell.value.strip() == "0" and cell.cell_state != "zero":
                        cell.cell_state = "zero"
    return ext


_DATE_HEADER_RE = re.compile(
    r'(?:\b\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b'
)
_LETTER_LEAF_RE = re.compile(r'^\(?[a-z]\)?$')


def repair_letter_leafs(t: GTable) -> GTable:
    """Repair tables where the model kept the letter row as column leafs and
    emitted the real descriptive headers as leading valueless rows.

    Gate (ALL must hold, else return t unchanged):
    - every leaf matches r'^\\(?[a-z]\\)?$'
    - the first N substantive rows (skipping row_type=='note') are valueless
      (no values entry with non-empty value) with row_type in
      ('section_header', 'sub_header'), where N == len(t.columns)

    Repair: leaf[i] = label of the i-th such row; delete those N rows.
    Pure and idempotent."""
    if not t.columns:
        return t
    if not all(_LETTER_LEAF_RE.match(c.leaf.strip()) for c in t.columns):
        return t

    n = len(t.columns)
    substantive = [r for r in t.rows if r.row_type != "note"]
    candidates = substantive[:n]

    if len(candidates) < n:
        return t
    if not all(r.row_type in ("section_header", "sub_header") for r in candidates):
        return t
    if not all(not any(v.value.strip() for v in r.values) for r in candidates):
        return t

    new_cols = [
        c.model_copy(update={"leaf": candidates[i].label})
        for i, c in enumerate(t.columns)
    ]
    candidate_set = set(id(r) for r in candidates)
    new_rows = [r for r in t.rows if id(r) not in candidate_set]
    print(f"   🔧 promoted {n} header-label rows to column leafs in '{t.title[:40]}'")
    return t.model_copy(update={"columns": new_cols, "rows": new_rows})


def split_date_blocks(t: GTable) -> list[GTable]:
    """Split a table that has >= 2 date-period section_header rows into one
    GTable per period.  If the condition is not met, returns [t] unchanged.
    Pure function — does not mutate the input GTable."""
    date_header_indices = [
        i for i, r in enumerate(t.rows)
        if r.row_type == "section_header" and _DATE_HEADER_RE.search(r.label)
    ]
    if len(date_header_indices) < 2:
        return [t]

    boundaries = date_header_indices + [len(t.rows)]
    for k, idx in enumerate(date_header_indices):
        block_start = idx + 1
        block_end   = boundaries[k + 1]
        has_data = any(t.rows[j].values for j in range(block_start, block_end))
        if not has_data:
            return [t]

    result: list[GTable] = []
    pre_rows = list(t.rows[: date_header_indices[0]])

    for k, idx in enumerate(date_header_indices):
        header_row  = t.rows[idx]
        block_start = idx + 1
        block_end   = boundaries[k + 1]
        block_rows  = pre_rows + list(t.rows[block_start:block_end])
        pre_rows    = []

        date_text = _DATE_HEADER_RE.search(header_row.label).group(0)
        orig_title = t.title.strip()
        if orig_title and orig_title != date_text:
            new_title = f"{orig_title} — {date_text}"
        else:
            new_title = date_text

        result.append(t.model_copy(update={"title": new_title, "rows": block_rows}))

    return result


def drop_echo_groups(extraction: Extraction) -> Extraction:
    """Normalise GColumn.group where it merely echoes GColumn.leaf (case- and
    whitespace-insensitive compare), setting group = None for those columns.

    Root cause: the model sometimes emits a group banner that is just a copy of
    the leaf label (group='1st Qtr 2026', leaf='1st Qtr 2026'). The loader
    mints a col_dim GROUP row (col_id 100+) for every distinct non-null group,
    so an echoed group mints a phantom col_dim row that no cell_fact ever
    references. A column whose group is already None is left unchanged.

    Pure function — returns a new Extraction with updated tables; does not
    mutate the input."""
    def _norm_group(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip()).casefold()

    new_tables = []
    for t in extraction.tables:
        new_cols = [
            c.model_copy(update={"group": None})
            if c.group is not None and _norm_group(c.group) == _norm_group(c.leaf)
            else c
            for c in t.columns
        ]
        new_tables.append(t.model_copy(update={"columns": new_cols}))
    return extraction.model_copy(update={"tables": new_tables})


class GeometryResult(NamedTuple):
    """Outcome of applying a geometry side-car to one GTable.

    `table` is the (possibly rewritten) table; every other list is aligned to
    IT, not to the input. `applied` is the visible branch marker persisted as
    table_t.hierarchy_source ('geometry' vs 'model')."""
    table: GTable
    applied: bool
    row_labels_clean: list[str | None]   # len == len(table.rows)
    title_clean: str | None
    col_labels_clean: list[str | None]   # len == len(table.columns)
    warnings: list[str]                  # UNPREFIXED; caller adds table_id


def _no_geometry(t: GTable, warnings: list[str] | None = None) -> GeometryResult:
    return GeometryResult(t, False, [None] * len(t.rows), None,
                          [None] * len(t.columns), warnings or [])


def apply_geometry(t: GTable, tg: dict | None) -> GeometryResult:
    """Make the PDF's printed geometry authoritative over the model's `level`.

    `tg` is one entry of the `geometry.tables` side-car written into parsed.json
    by stage1_extract.chunk.geometry (see that module's docstring), i.e.
    {"rows": [{"line_id", "indent", "label_clean"}, ...], "title_clean",
     "col_labels_clean", "all_rows_matched"}.

    Why: GRow.level conflates "data row" with "visually indented" and wobbles
    between tables of the SAME document, and the loader derives row_parent
    purely from levels — so every level error becomes a parentage error. The
    printed page carries the true nesting deterministically, for every bank.

    ALL-OR-NOTHING PER TABLE. The rewrite happens only when the side-car matched
    EVERY row of this table (`all_rows_matched`), the row counts agree, and
    every matched row has an indent. Any shortfall and the table falls back
    WHOLLY to today's model-level behaviour — a partial override would mix two
    incompatible depth scales in one parent walk.

    Two rewrites, in order:
      1. PRINTED-LINE TWIN MERGE. A run of consecutive rows sharing one
         `line_id` is ONE printed line the model emitted twice — the phantom
         valueless `section_header` plus its identical-label data twin. The
         valued row survives; the phantom is dropped. A run with NO valued row
         is a real (valueless) section header printed once: keep the first, drop
         the rest. A run with SEVERAL valued rows is not a twin pattern geometry
         can adjudicate — keep them all and warn.
      2. DEPTH OVERRIDE. level := the geometric indent rank (0 = leftmost ink).

    Labels are never rewritten: GRow.label stays verbatim. The
    superscript-stripped labels ride alongside in `row_labels_clean` /
    `title_clean` / `col_labels_clean` for the loader's `*_clean` columns and
    for identity lookups (lineage, geo/segment/industry, concepts).

    Pure function — returns a new GTable; does not mutate the input."""
    if not tg or not isinstance(tg, dict):
        return _no_geometry(t)

    title_clean = tg.get("title_clean")
    col_clean_raw = list(tg.get("col_labels_clean") or [])
    # Title/column cleans come from an independent best-effort line match, so
    # they are usable even when the ROW alignment fell short.
    col_clean = (col_clean_raw + [None] * len(t.columns))[:len(t.columns)]

    grows = tg.get("rows") or []
    if not tg.get("all_rows_matched"):
        return GeometryResult(t, False, [None] * len(t.rows), title_clean,
                              col_clean, [])
    if len(grows) != len(t.rows):
        return GeometryResult(
            t, False, [None] * len(t.rows), title_clean, col_clean,
            [f"geometry side-car has {len(grows)} rows for a {len(t.rows)}-row "
             f"table — falling back to model levels"])
    if any(g.get("indent") is None or g.get("line_id") is None for g in grows):
        return GeometryResult(
            t, False, [None] * len(t.rows), title_clean, col_clean,
            ["geometry matched all rows but some carry no indent — falling back "
             "to model levels"])

    warnings: list[str] = []
    kept: list[int] = []          # indices into t.rows that survive the merge
    i = 0
    n_merged = 0
    while i < len(t.rows):
        j = i + 1
        while j < len(t.rows) and grows[j]["line_id"] == grows[i]["line_id"]:
            j += 1
        run = list(range(i, j))
        if len(run) == 1:
            kept.append(i)
        else:
            valued = [k for k in run if t.rows[k].values]
            if len(valued) == 1:
                kept.append(valued[0])
                n_merged += len(run) - 1
            elif not valued:
                kept.append(run[0])
                n_merged += len(run) - 1
            else:
                warnings.append(
                    f"printed line {grows[i]['line_id']} carries "
                    f"{len(valued)} valued rows ({t.rows[run[0]].label!r}) — "
                    f"not merged")
                kept.extend(run)
        i = j
    if n_merged:
        warnings.append(f"geometry merged {n_merged} phantom printed-line twin "
                        f"row(s) into their data rows")

    new_rows = []
    row_labels_clean: list[str | None] = []
    for k in kept:
        # Printed line number: the surviving row's own, else the first one the
        # run carried (the phantom header sometimes holds it, the twin not).
        line_no = t.rows[k].row_id
        if line_no is None:
            for m in range(len(t.rows)):
                if grows[m]["line_id"] == grows[k]["line_id"] and t.rows[m].row_id:
                    line_no = t.rows[m].row_id
                    break
        new_rows.append(t.rows[k].model_copy(
            update={"level": grows[k]["indent"], "row_id": line_no}))
        row_labels_clean.append(grows[k].get("label_clean"))

    return GeometryResult(t.model_copy(update={"rows": new_rows}), True,
                          row_labels_clean, title_clean, col_clean, warnings)


def fill_parents(t: GTable) -> GTable:
    """Overwrite GRow.parent for every row in t using deterministic level-walk.

    Rules:
    - Level 0 and 1 rows: parent = None.
    - Level N (N >= 2) rows: parent = row_id of the nearest preceding level N-1 row.
    - When a level-L row is processed, clear the ancestor stack for all levels > L.
    - Note rows are skipped (parent unchanged; they are not anchors either).
    - If the needed ancestor row has no printed row_id, assign it a synthetic id
      ("h1", "h2", ...) and use that as the parent.  Synthetic ids are only
      assigned to rows that are actually referenced as parents, not to every
      unnumbered row.

    Pure function — returns a new GTable with updated rows; does not mutate input."""
    rows        = [r.model_copy() for r in t.rows]
    ancestor_stack: dict[int, tuple[int, str | None]] = {}
    synthetic_counter = 0

    for i, row in enumerate(rows):
        if row.row_type == "note":
            continue

        level = row.level

        for lvl in list(ancestor_stack.keys()):
            if lvl > level:
                del ancestor_stack[lvl]

        if level <= 1:
            row.parent = None
        else:
            parent_level = level - 1
            if parent_level in ancestor_stack:
                parent_idx, parent_row_id = ancestor_stack[parent_level]
                if parent_row_id is None:
                    synthetic_counter += 1
                    parent_row_id = f"h{synthetic_counter}"
                    rows[parent_idx] = rows[parent_idx].model_copy(update={"row_id": parent_row_id})
                    ancestor_stack[parent_level] = (parent_idx, parent_row_id)
                row.parent = parent_row_id
            else:
                row.parent = None

        ancestor_stack[level] = (i, row.row_id)

    return t.model_copy(update={"rows": rows})


def _apply_transforms(tables: list[GTable]) -> list[GTable]:
    """Apply post-extraction transforms in order: repair_letter_leafs →
    split_date_blocks → fill_parents. drop_next_section_tables is called
    separately by the caller because it needs unit context."""
    result: list[GTable] = []
    for t in tables:
        t = repair_letter_leafs(t)
        for s in split_date_blocks(t):
            result.append(fill_parents(s))
    return result


# ===========================================================================
# VALIDATORS  (zero API cost — pure Python on extracted JSON + PDF text layer)
# ===========================================================================
def table_fingerprint(t: GTable) -> str:
    """Stable 12-char hash of (normalized_title, col leaves, first 3 data-row labels)."""
    norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()
    title = norm(t.title or "")
    leaves = tuple(c.leaf for c in t.columns)
    data_rows = [r for r in t.rows if r.row_type in ("data", "total")][:3]
    row_labels = tuple(norm(r.label or "") for r in data_rows)
    return hashlib.sha1(str((title, leaves, row_labels)).encode()).hexdigest()[:12]


def validate_exactly_once(all_tables_by_unit: dict[str, list]) -> list[str]:
    """Return issue strings for any table fingerprint appearing in >=2 units."""
    from collections import defaultdict
    seen: dict[str, list[str]] = defaultdict(list)
    fp_to_title: dict[str, str] = {}
    for uid, tables in all_tables_by_unit.items():
        for t in tables:
            fp = table_fingerprint(t)
            seen[fp].append(uid)
            fp_to_title[fp] = t.title or "(untitled)"
    return [
        f"DUPLICATE TABLE: '{fp_to_title[fp][:50]}' in units {' AND '.join(uids)}"
        for fp, uids in seen.items() if len(uids) >= 2
    ]


def validate_spans(ext: Extraction) -> list[str]:
    """Check len(row.values) == len(columns) for every data row.
    Returns list of violation strings; empty list = all good."""
    issues = []
    for t in ext.tables:
        n = len(t.columns)
        for row in t.rows:
            if not row.values:
                continue
            total = len(row.values)
            if total != n:
                issues.append(
                    f"  column count mismatch [{t.title[:40]}] row {row.row_id or repr(row.label[:30])}: "
                    f"got {total} values != ncols={n}"
                )
    return issues


def validate_labels(ext: Extraction) -> list[str]:
    """Detect row-shift corruption: duplicate stripped labels among data/total rows.
    Returns list of issue strings; empty list = all good."""
    issues = []
    for t in ext.tables:
        counts: dict[str, int] = {}
        for row in t.rows:
            if row.row_type not in ("data", "total"):
                continue
            lbl = row.label.strip()
            if lbl:
                counts[lbl] = counts.get(lbl, 0) + 1
        for lbl, n in counts.items():
            if n > 1:
                issues.append(
                    f"  duplicate row label x{n}: '{lbl[:40]}' [{t.title[:30]}]"
                )
    return issues


def validate_letter_leafs(ext: Extraction) -> list[str]:
    """Flag tables whose column leafs are still bare letters after repair_letter_leafs.
    Post-repair this means the gate didn't match (N mismatch, values present, etc.)
    and the table needs manual review or a re-extraction."""
    issues = []
    for t in ext.tables:
        if t.columns and all(_LETTER_LEAF_RE.match(c.leaf.strip()) for c in t.columns):
            leafs = [c.leaf for c in t.columns]
            issues.append(
                f"  bare letter leafs {leafs} [{t.title[:40]}]"
            )
    return issues


def _page_raw_text(pdf_path: str, pages: list[int]) -> str:
    """Return concatenated raw text from the given pages via pypdfium2."""
    with _pdfium_lock:
        pdf = pdfium.PdfDocument(pdf_path)
        parts = []
        for pg in pages:
            parts.append(pdf[pg - 1].get_textpage().get_text_range())
    return "\n".join(parts)


def _numeric_tokens_from_text(text: str) -> Counter:
    """Shared token-extraction regex: numeric tokens -> Counter of canonical strings."""
    counts: Counter = Counter()
    for tok in re.findall(r'\(?\d[\d,]*(?:\.\d+)?\)?%?', text):
        cleaned = tok.strip("()% \n").replace(",", "")
        if cleaned and any(c.isdigit() for c in cleaned):
            counts[cleaned] += 1
    return counts


def _page_numbers(pdf_path: str, pages: list[int]) -> Counter:
    """Extract numeric tokens from the PDF text layer. Returns Counter of canonical strings."""
    raw = _page_raw_text(pdf_path, pages)
    return _numeric_tokens_from_text(raw)


def _page_numbers_in_region(pdf_path: str, page_num: int,
                             region: tuple[float, float]) -> Counter:
    """Extract numeric tokens from a page, restricted to the y-band [y0, y1).
    Uses pdfplumber's crop() so wrapped/comma-grouped numbers stay intact
    (verified against DBS_1Q26 p6: crop(126.1, 378.7) contains exactly the
    income-statement numbers, none of the balance-sheet/ratio numbers below)."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return Counter()
            page = pdf.pages[page_num - 1]
            y0, y1 = region
            y0 = max(0.0, y0)
            y1 = min(page.height, y1)
            if y1 <= y0:
                return Counter()
            text = page.crop((0, y0, page.width, y1)).extract_text() or ""
    except Exception:
        return Counter()
    return _numeric_tokens_from_text(text)


_YEAR_RE = re.compile(r'^(19|20)\d{2}$')


_HEADING_RE = re.compile(r'^[A-Z]?\d+(\.\d+)*$')
_SECTION_HEADING_RE = re.compile(r'^([A-Z]\.)?\d{2,}(\.\d+)*$|^[A-Z]\.\d+(\.\d+)*$|^\d+\.\d+')
_ONLY_DIGITS_PUNCT_RE = re.compile(r'^[\d\s\.,\-\(\)]+$')


def page_section_regions(pdf_path: str, page_num: int) -> dict[str, tuple[float, float]]:
    """Return {section_id: (y_start, y_end)} from heading positions on a page.
    Uses pdfplumber extract_words() to find section number headings.
    y_end = next heading's y_start, or page height for the last heading.

    Heading qualification: must match _HEADING_RE AND be either:
    - a multi-part number (contains a dot), e.g. "16.2.1"
    - a two-or-more-digit number, e.g. "16"
    - a letter-prefixed number, e.g. "A.12"
    This excludes bare single-digit row numbers ("1", "2", "3") which share the left margin."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return {}
            page = pdf.pages[page_num - 1]
            page_width = page.width
            page_height = page.height
            words = page.extract_words()
    except Exception:
        return {}

    threshold_x = page_width * 0.15
    headings: list[tuple[float, str]] = []
    for w in words:
        text = w.get("text", "").strip()
        x0 = w.get("x0", 9999)
        top = w.get("top", 0)
        if x0 < threshold_x and _HEADING_RE.match(text) and _SECTION_HEADING_RE.match(text):
            headings.append((top, text))

    if not headings:
        return {}

    headings.sort(key=lambda x: x[0])

    regions: dict[str, tuple[float, float]] = {}
    for i, (y_start, sid) in enumerate(headings):
        y_end = headings[i + 1][0] if i + 1 < len(headings) else page_height
        regions[sid] = (y_start, y_end)

    return regions


def _normalize_heading_text(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (s or "").casefold()).strip()


def _find_heading_y(page, text: str, threshold_x: float, tol: float = 3.0) -> float | None:
    """Locate a heading's y (top) on `page` by matching its FULL normalized
    text against a printed line, left-margin gated.

    Matching on the whole line (not just the first word) is required: FS
    section titles on the same page routinely share a first word — e.g. both
    'Selected income statement items ($m)' and 'Selected balance sheet items
    ($m)' start with 'Selected' — so a first-token match is ambiguous and
    would silently pick the wrong heading. Groups words into printed lines
    (same top-tolerance grouping as _group_page_lines) and requires the
    line's normalized text to equal, or be a prefix/superset of, the
    normalized title."""
    title_norm = _normalize_heading_text(text)
    if len(title_norm) < 3:
        return None

    words = sorted(page.extract_words(), key=lambda w: w["top"])
    if not words:
        return None

    lines: list[list[dict]] = []
    current = [words[0]]
    current_top = words[0]["top"]
    for w in words[1:]:
        if abs(w["top"] - current_top) <= tol:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
            current_top = w["top"]
    lines.append(current)

    for line in lines:
        line_sorted = sorted(line, key=lambda w: w["x0"])
        if line_sorted[0].get("x0", 9999) >= threshold_x:
            continue
        line_norm = _normalize_heading_text(" ".join(w["text"] for w in line_sorted))
        if not line_norm:
            continue
        if line_norm == title_norm or line_norm.startswith(title_norm) or title_norm.startswith(line_norm):
            return min(w["top"] for w in line_sorted)
    return None


def section_region_for_unit(pdf_path: str, unit: dict,
                             page_num: int) -> tuple[tuple[float, float] | None, str]:
    """Resolve the y-region on `page_num` owned by `unit`'s own section.

    Two paths, in order (both pure geometry, no bank/document-specific logic):
    1. Numbered headings (`page_section_regions`) — works for Pillar-3-style
       sections numbered like '16.2.1'.
    2. Title-text search — for FS sections that use a slug id
       (e.g. 'selected_income_statement_items_m') with no numbered heading,
       where (1) returns {}. Locates the unit's own printed section title via
       word position (same machinery `validate_column_bands`/`table_anchor_y`
       use), and bounds the region below by the nearest sibling section that
       starts on this same page (`unit['next_leaves']`, already computed by
       build_units) — or the page bottom if no sibling starts here.

    Returns (region, method) where method is one of
    'numbered-heading' | 'title-match' | 'unresolved' | 'no-leaves'.
    region is None iff method in ('unresolved', 'no-leaves') — caller must
    fall back to page-wide scanning and say so, never scan silently-wrong."""
    leaves = unit.get("leaves") or []
    if not leaves:
        return None, "no-leaves"
    own = leaves[0]
    sid = own.get("section_id", "")

    regions = page_section_regions(pdf_path, page_num)
    if regions:
        def _numeric_part(s: str) -> str:
            m = re.match(r'^[A-Za-z]\.(.+)$', s)
            return m.group(1) if m else s
        target = _numeric_part(sid)
        for rsid, span in regions.items():
            if _numeric_part(rsid) == target:
                return span, "numbered-heading"

    title = (own.get("title") or "").strip()
    if not title:
        return None, "unresolved"

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return None, "unresolved"
            page = pdf.pages[page_num - 1]
            threshold_x = page.width * 0.15
            start_y = _find_heading_y(page, title, threshold_x)
            if start_y is None:
                return None, "unresolved"
            end_y = page.height
            for nl in unit.get("next_leaves", []):
                try:
                    if int(nl.get("start_page", -1)) != page_num:
                        continue
                except (TypeError, ValueError):
                    continue
                nl_title = (nl.get("title") or "").strip()
                if not nl_title:
                    continue
                y2 = _find_heading_y(page, nl_title, threshold_x)
                if y2 is not None and y2 > start_y:
                    end_y = min(end_y, y2)
    except Exception:
        return None, "unresolved"

    return (start_y, end_y), "title-match"


def table_anchor_y(table: GTable, pdf_path: str, page_num: int) -> tuple[str | None, float | None]:
    """Find the y-coordinate of a table on the boundary page using its title.

    Strategy: search for the table's title text on the page. The title is more
    distinctive than row labels and less likely to produce false matches.
    Falls back to the longest distinctive row label if title not found.

    Returns (anchor_text, y) or (None, None) if not found on this page."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return (None, None)
            page = pdf.pages[page_num - 1]
            # Use extract_text to get the full page text for substring search
            page_text = (page.extract_text() or "").lower()
            words = page.extract_words()
    except Exception:
        return (None, None)

    # Try title first — most distinctive
    title = (table.title or "").strip()
    if len(title) >= 10:
        # Strip date suffix for matching (titles may differ by date)
        title_base = re.sub(r'\s*[-—]\s*(31|30|At).*$', '', title, flags=re.I).strip()
        if len(title_base) >= 8 and title_base.lower() in page_text:
            # Find y of first word of title on the page
            first_word = title_base.split()[0].lower().rstrip(".,;:")
            hits = [w["top"] for w in words if w["text"].lower().startswith(first_word[:6])]
            if hits:
                return (title_base, min(hits))

    # Fall back: longest distinctive row label (section_header preferred)
    anchor_label: str | None = None
    for row in table.rows:
        if row.row_type == "note":
            continue
        lbl = (row.label or "").strip()
        if not lbl or lbl in ("Total", "Subtotal") or _ONLY_DIGITS_PUNCT_RE.match(lbl):
            continue
        if anchor_label is None or len(lbl) > len(anchor_label):
            anchor_label = lbl

    if not anchor_label or len(anchor_label) < 12:
        return (None, None)

    if anchor_label.lower() not in page_text:
        return (None, None)

    first_word = anchor_label.split()[0].lower().rstrip(".,;:")
    if len(first_word) < 4:
        return (None, None)
    hits = [w["top"] for w in words if w["text"].lower().startswith(first_word[:6])]
    return (anchor_label, min(hits)) if hits else (None, None)


def drop_misowned_tables(tables: list, unit: dict, pdf_path: str,
                          pages: list[int]) -> tuple[list, list[dict]]:
    """Drop tables whose anchor-row y falls in a different section's region.

    Returns (kept_tables, drop_records) where drop_records is a list of dicts:
      {"title": ..., "anchor_label": ..., "anchor_y": ...,
       "owning_section": ..., "dropped": bool}
    One record per table, kept or dropped — for per-table meta.json stamping.

    Keep if:
    - ENABLE_REGION_OWNERSHIP is False
    - table.continued_from_previous is True
    - anchor y is None (not found) — flag but keep
    - anchor y is in no region — flag but keep
    - owning region == extracting unit's section_id — legitimate owner, keep

    Drop if:
    - anchor y is known AND owning region != extracting unit's section_id AND not continued
    """
    if not ENABLE_REGION_OWNERSHIP:
        return tables, []

    section_id = unit["leaves"][0]["section_id"]

    def _numeric_part(sid: str) -> str:
        m = re.match(r'^[A-Z]\.(.+)$', sid)
        return m.group(1) if m else sid

    section_id_numeric = _numeric_part(section_id)

    all_regions: dict[str, tuple[float, float]] = {}
    for pg in pages:
        all_regions.update(page_section_regions(pdf_path, pg))

    if not all_regions:
        return tables, []

    # Only the boundary page (last page of the unit) can have contamination —
    # tables whose anchor is on an interior page are fully within this unit's range.
    boundary_page = pages[-1]
    boundary_regions = page_section_regions(pdf_path, boundary_page)
    if not boundary_regions:
        return tables, []

    kept = []
    records = []
    for t in tables:
        if t.continued_from_previous:
            kept.append(t)
            records.append({"title": t.title, "anchor_label": None,
                            "anchor_y": None, "owning_section": section_id,
                            "dropped": False, "reason": "continuation_exempt"})
            continue

        # Only search the boundary page for the anchor
        label, y = table_anchor_y(t, pdf_path, boundary_page)

        if y is None:
            # Anchor not found on boundary page → table lives on an interior page,
            # cannot be contamination. Keep silently.
            kept.append(t)
            records.append({"title": t.title, "anchor_label": label,
                            "anchor_y": None, "owning_section": None,
                            "dropped": False, "reason": "interior_page"})
            continue

        owning: str | None = None
        for sid, (y_start, y_end) in boundary_regions.items():
            if y_start <= y < y_end:
                owning = sid
                break

        if owning is None:
            kept.append(t)
            records.append({"title": t.title, "anchor_label": label,
                            "anchor_y": round(y, 2), "owning_section": None,
                            "dropped": False, "reason": "y_in_no_region"})
            continue

        if _numeric_part(owning) == section_id_numeric:
            kept.append(t)
            records.append({"title": t.title, "anchor_label": label,
                            "anchor_y": round(y, 2), "owning_section": owning,
                            "dropped": False, "reason": "legitimate_owner"})
        else:
            print(f"   ⚠ dropped '{t.title[:40]}' from {unit['unit_id']}: "
                  f"anchor '{label[:30]}' at y={y:.1f} in region of {owning}")
            records.append({"title": t.title, "anchor_label": label,
                            "anchor_y": round(y, 2), "owning_section": owning,
                            "dropped": True, "reason": "misowned"})

    return kept, records


def flag_duplicate_tables(all_tables_by_unit: dict[str, list]) -> list[str]:
    """Return issue strings for pairs of tables with >=90% row-content similarity.
    Similarity is based on normalized (label, values) pairs — ignores cell_state/level/parent/title."""
    def _norm(s: str) -> str:
        return re.sub(r'[\s,]', '', (s or "").lower())

    def _row_content(t: GTable) -> list[tuple]:
        rows = []
        for r in t.rows:
            if r.row_type not in ("data", "total"):
                continue
            label = _norm(r.label or "")
            vals = tuple(_norm(c.value if isinstance(c, GCell) else str(c)) for c in r.values)
            rows.append((label, vals))
        return rows

    def _similarity(rows_a: list[tuple], rows_b: list[tuple]) -> float:
        if not rows_a or not rows_b:
            return 0.0
        set_a = set(rows_a)
        set_b = set(rows_b)
        intersection = len(set_a & set_b)
        union = max(len(set_a), len(set_b))
        return intersection / union if union else 0.0

    # Pre-compute content for all tables
    unit_table_content: list[tuple[str, int, GTable, list[tuple]]] = []
    for uid, tables in all_tables_by_unit.items():
        for i, t in enumerate(tables):
            rows = _row_content(t)
            if rows:
                unit_table_content.append((uid, i, t, rows))

    issues: list[str] = []
    for ai in range(len(unit_table_content)):
        uid_a, _, ta, rows_a = unit_table_content[ai]
        for bi in range(ai + 1, len(unit_table_content)):
            uid_b, _, tb, rows_b = unit_table_content[bi]
            if uid_a == uid_b:
                continue
            sim = _similarity(rows_a, rows_b)
            if sim >= 0.90:
                issues.append(
                    f"DUPLICATE TABLE: '{(ta.title or '')[:40]}' in {uid_a} AND "
                    f"'{(tb.title or '')[:40]}' in {uid_b} (similarity {sim:.0%})"
                )

    return issues


_NUM_TOKEN_RE = re.compile(r'^\(?-?\d[\d,]*(?:\.\d+)?\)?%?$')
_LABEL_TAIL_RE = re.compile(r'[\s\d¹²³⁴⁵⁶⁷⁸⁹⁰,\*]+$')


def _is_numeric_token(text: str) -> bool:
    """True if a printed word / GCell value reads as a plain number
    (optionally parenthesised-negative, comma-grouped, decimal, %-suffixed).
    Excludes text tokens like 'NM', '>100', '-' — those carry no geometric
    column signal on the printed page."""
    return bool(_NUM_TOKEN_RE.match((text or "").strip()))


def _normalize_band_label(s: str) -> str:
    """casefold, collapse whitespace, strip trailing footnote digits/markers."""
    s = re.sub(r'\s+', ' ', (s or "").strip()).casefold()
    s = _LABEL_TAIL_RE.sub('', s)
    return s.strip()


def _group_page_lines(pdf_path: str, pages: list[int], tol: float = 3.0) -> list[list[dict]]:
    """Group pdfplumber words into printed lines by 'top' (tolerance pt), per page."""
    lines: list[list[dict]] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pg in pages:
                if pg < 1 or pg > len(pdf.pages):
                    continue
                words = pdf.pages[pg - 1].extract_words()
                if not words:
                    continue
                ws = sorted(words, key=lambda w: w["top"])
                current = [ws[0]]
                current_top = ws[0]["top"]
                for w in ws[1:]:
                    if abs(w["top"] - current_top) <= tol:
                        current.append(w)
                    else:
                        lines.append(current)
                        current = [w]
                        current_top = w["top"]
                lines.append(current)
    except Exception:
        return []
    return lines


def _tag_printed_lines(lines: list[list[dict]]) -> list[dict]:
    """[{'numeric': [word...] left-to-right, 'label': normalized label}] per printed line.

    Shared by the detector and the repairer so the two can never drift apart in
    how they read the page."""
    tagged = []
    for line in lines:
        line_sorted = sorted(line, key=lambda w: w["x0"])
        numeric = [w for w in line_sorted if _is_numeric_token(w["text"])]
        label_words = [w["text"] for w in line_sorted if not _is_numeric_token(w["text"])]
        tagged.append({"numeric": numeric, "label": _normalize_band_label(" ".join(label_words))})
    return tagged


def _calibrate_bands(tagged: list[dict], n: int) -> tuple[list[tuple[float, float]] | None, int]:
    """(band_ranges, n_dense) for a table with `n` value columns, or (None, n_dense)
    when calibration fails (< 2 dense lines).

    A *dense* line has exactly `n` numeric tokens, so its i-th token left-to-right
    IS column i. Each band is [min(x0), max(x1)] over its cluster."""
    dense = [ln for ln in tagged if len(ln["numeric"]) == n]
    if len(dense) < 2:
        return None, len(dense)
    bands: list[list[tuple[float, float]]] = [[] for _ in range(n)]
    for ln in dense:
        for i, w in enumerate(ln["numeric"]):
            bands[i].append((w["x0"], w["x1"]))
    return [(min(x0 for x0, _ in b), max(x1 for _, x1 in b)) for b in bands], len(dense)


def _band_of(band_ranges: list[tuple[float, float]], w: dict) -> int | None:
    """First band whose range contains the word's x-centre; None if outside all."""
    xc = (w["x0"] + w["x1"]) / 2
    for bi, (bx0, bx1) in enumerate(band_ranges):
        if bx0 <= xc <= bx1:
            return bi
    return None


def _bands_containing(band_ranges: list[tuple[float, float]], w: dict) -> list[int]:
    """EVERY band whose range contains the word's x-centre. Repair requires
    exactly one — `_band_of` would silently take the first of an ambiguous pair."""
    xc = (w["x0"] + w["x1"]) / 2
    return [bi for bi, (bx0, bx1) in enumerate(band_ranges) if bx0 <= xc <= bx1]


def _printed_lines_by_label(tagged: list[dict]) -> dict[str, list[list[dict]]]:
    """normalized label -> list of that label's printed numeric-token runs, in
    top-to-bottom order (a label can repeat, e.g. two 'Constant-currency change'
    rows). Detector and repairer consume this identically, so both match the
    same extracted row to the same printed line."""
    out: dict[str, list[list[dict]]] = {}
    for ln in tagged:
        out.setdefault(ln["label"], []).append(ln["numeric"])
    return out


def validate_column_bands(ext: Extraction, pdf_path: str, pages: list[int]) -> list[str]:
    """Geometric column-band validator (detection only — phase 1, see
    docs/specs/2026-07-29-column-band-validator.md).

    validate_spans checks cell COUNT only; validate_numbers checks page-wide
    RECALL without column regard. Neither can see a value sitting in the
    wrong column with the right count. This validator can, using pdfplumber
    word x-positions (free, deterministic, already on disk) to calibrate the
    printed column bands per table and compare them against where the
    extracted values actually landed.

    No bank/document/section-specific logic anywhere — pure geometry, works
    on any table with >= 2 'dense' printed lines (a line whose numeric-token
    count equals the table's value-column count). Detection only: never
    mutates, reorders, or repairs a cell."""
    issues: list[str] = []
    lines = _group_page_lines(pdf_path, pages)
    if not lines:
        return issues

    tagged = _tag_printed_lines(lines)

    for t in ext.tables:
        n = len(t.columns)
        if n == 0:
            continue

        # Cluster numeric x-centres into N bands by column position within
        # dense lines (dense lines are, by construction, fully populated —
        # their i-th numeric token left-to-right IS column i).
        band_ranges, n_dense = _calibrate_bands(tagged, n)
        if band_ranges is None:
            issues.append(
                f"  col-bands: uncalibrated ({n_dense} dense lines) [{t.title[:40]}]"
            )
            continue

        # Occupied bands per printed line, keyed by normalized label —
        # a list per label to handle duplicate labels (e.g. two
        # 'Constant-currency change' rows), consumed in top-to-bottom order.
        printed_lines = _printed_lines_by_label(tagged)

        consumed: dict[str, int] = {}
        for row in t.rows:
            if not row.values:
                continue
            norm_label = _normalize_band_label(row.label)
            candidates = printed_lines.get(norm_label)
            if not candidates:
                continue  # unverifiable — not a failure
            idx = consumed.get(norm_label, 0)
            if idx >= len(candidates):
                continue
            consumed[norm_label] = idx + 1
            printed_bands = {
                b for b in (_band_of(band_ranges, w) for w in candidates[idx])
                if b is not None
            }

            extracted_slots = {
                i for i, cell in enumerate(row.values) if _is_numeric_token(cell.value)
            }

            if extracted_slots != printed_bands:
                p_str = ",".join(str(b + 1) for b in sorted(printed_bands))
                e_str = ",".join(str(b + 1) for b in sorted(extracted_slots))
                issues.append(
                    f"  col-shift: '{row.label[:40]}' printed bands [{p_str}] -> "
                    f"extracted slots [{e_str}]"
                )

    return issues


def _repair_key(s: str) -> str:
    """Comparison key for 'is this the same printed value'. Whitespace-insensitive
    ONLY — commas, parentheses, %, sign and digits must match exactly. This key is
    never written anywhere; values are moved verbatim, never reformatted."""
    return re.sub(r'\s+', '', s or '')


def _repair_row(row, tokens: list[dict], band_ranges: list[tuple[float, float]],
                n: int, extracted_slots: set[int]) -> list[str]:
    """Re-slot ONE shifted row's existing cells into the bands the printed
    geometry proves they occupy. Returns exactly one audit string: either the
    repair record or a decline with its reason. Mutates `row` only on success.

    The repair is a pure PERMUTATION of the cells already in the row — every
    GCell object (value AND cell_state) is carried over untouched, and the
    blanks that back-fill the vacated slots are the row's own empty cells. No
    value is ever invented, dropped, merged, split or reformatted."""
    label = row.label[:40]
    decline = lambda why: [f"  col-repair: declined for '{label}' — {why}"]

    # --- GUARD 3: every printed token must resolve to exactly one band -------
    targets: list[int] = []
    for w in tokens:
        hits = _bands_containing(band_ranges, w)
        if len(hits) != 1:
            return decline(f"printed token {w['text']!r} maps to {len(hits)} bands "
                           f"(need exactly 1)")
        targets.append(hits[0])
    if len(set(targets)) != len(targets):
        dup = sorted({b + 1 for b in targets if targets.count(b) > 1})
        return decline(f"{len(tokens)} printed tokens share band(s) "
                       f"[{','.join(str(b) for b in dup)}] — no unambiguous destination")

    # --- GUARD 2: re-slot KNOWN values only ---------------------------------
    # The row's non-empty values must be exactly the multiset of the printed
    # line's numeric tokens. If they differ, the row is not merely misaligned —
    # something was misread — and moving cells would launder a wrong value into
    # a right-looking column.
    filled  = [c for c in row.values if (c.value or "").strip()]
    empties = [c for c in row.values if not (c.value or "").strip()]
    have    = Counter(_repair_key(c.value) for c in filled)
    want    = Counter(_repair_key(w["text"]) for w in tokens)
    if have != want:
        return decline(
            f"extracted values {sorted(have.elements())} != printed tokens "
            f"{sorted(want.elements())}")

    # --- GUARD 4: row width and cell count must survive unchanged -----------
    if len(row.values) != n:
        return decline(f"row has {len(row.values)} cells but the table has {n} "
                       f"columns — width would change")

    new_values: list = [None] * n
    pool = list(filled)
    for w, b in zip(tokens, targets):
        k = _repair_key(w["text"])
        for j, c in enumerate(pool):
            if _repair_key(c.value) == k:
                new_values[b] = pool.pop(j)
                break
    blanks = iter(empties)
    for i in range(n):
        if new_values[i] is None:
            new_values[i] = next(blanks, None)
    # Post-condition — a permutation, nothing lost, nothing added.
    if any(v is None for v in new_values) or len(new_values) != len(row.values):
        return decline("re-slot would not be a 1:1 permutation of the row's cells")
    if Counter(id(c) for c in new_values) != Counter(id(c) for c in row.values):
        return decline("re-slot would not preserve the row's exact cells")

    row.values[:] = new_values
    s_str = ",".join(str(i + 1) for i in sorted(extracted_slots))
    b_str = ",".join(str(b + 1) for b in sorted(targets))
    return [f"  col-repair: '{label}' slots [{s_str}] -> bands [{b_str}]"]


def repair_column_bands(ext: Extraction, pdf_path: str, pages: list[int]) -> list[str]:
    """Phase 2 of docs/specs/2026-07-29-column-band-validator.md — deterministic,
    guarded REPAIR of the shifts `validate_column_bands` detects.

    PLACEMENT (decided here, deliberately NOT in `_apply_transforms`):
      This runs as a SEPARATE PASS AFTER validation, at the same two call sites
      as the detector (`extract._finalize_unit`, `extract._finalize_spanning`),
      appending to the same `column_band_issues` list. Three reasons, in order
      of weight:
        1. Auditability. `_apply_transforms` is called from PASS2_v2.py only
           AFTER `_finalize_unit`/`_finalize_spanning` have already written
           meta.json — a repair made there could never be recorded in the
           unit's `validation.column_band_issues`, i.e. it would be exactly the
           silent correction this design forbids.
        2. Detection must still report what WAS wrong. Repairing before
           validation would erase the `col-shift:` evidence; the run log and
           meta.json would show a clean unit with no trace of the model's
           error, destroying the firing-rate measurement phase 1 exists for.
           Running after means meta.json carries BOTH the col-shift finding and
           the col-repair record for the same row.
        3. Contract. `_apply_transforms(tables)` is a pure, table-local
           transform with no PDF context (no pdf_path, no pages) and no channel
           for issue strings; repair needs page geometry and must return an
           audit trail. Threading both through it would mean editing a call
           site outside this change's blast radius.

    No bank-, document- or section-specific logic: the only inputs are the
    table's own column count and the printed word positions."""
    issues: list[str] = []
    lines = _group_page_lines(pdf_path, pages)
    if not lines:
        return issues
    tagged = _tag_printed_lines(lines)

    for t in ext.tables:
        n = len(t.columns)
        if n == 0:
            continue

        # --- GUARD 1: never repair on uncalibrated geometry ------------------
        band_ranges, n_dense = _calibrate_bands(tagged, n)
        if band_ranges is None:
            issues.append(
                f"  col-repair: declined for table '{t.title[:40]}' — uncalibrated "
                f"({n_dense} dense lines), shifts in this table cannot be checked"
            )
            continue

        printed_lines = _printed_lines_by_label(tagged)
        consumed: dict[str, int] = {}
        for row in t.rows:
            if not row.values:
                continue
            norm_label = _normalize_band_label(row.label)
            candidates = printed_lines.get(norm_label)
            if not candidates:
                continue  # unverifiable — never repaired
            idx = consumed.get(norm_label, 0)
            if idx >= len(candidates):
                continue
            consumed[norm_label] = idx + 1
            tokens = candidates[idx]

            printed_bands = {
                b for b in (_band_of(band_ranges, w) for w in tokens) if b is not None
            }
            extracted_slots = {
                i for i, cell in enumerate(row.values) if _is_numeric_token(cell.value)
            }
            if extracted_slots == printed_bands:
                continue  # already in the right columns — never touched
            issues.extend(_repair_row(row, tokens, band_ranges, n, extracted_slots))

    return issues


def validate_numbers(ext: Extraction, pdf_path: str, pages: list[int],
                     section_ids: tuple = (), unit: dict | None = None) -> list[str]:
    """Calibrated number-recall validator (v2).

    Class A fix — JSON-side: only count pure numeric tokens from GCell values.
      Strips concatenated text (ISINCode:..., Page57to58...) that pdfplumber
      never sees, eliminating phantom issues from text table columns.

    Class B fix — phantom check: before flagging a JSON value as phantom,
      check if it appears anywhere in the page text without spaces/commas.
      Catches kerning-split numbers (4909 → PDF has '4 909') and similar.

    Noise suppression in deficit check:
      - Tokens ≤ 2 chars (row ids, short ints)
      - 4-digit years (appear in headers/footers, not in tables)
      - Section id tokens (e.g. '9.4', '15.1') — correctly not extracted

    Section-scoping (companion fix, docs/specs/2026-07-29-column-band-validator.md):
      extraction units are section-scoped but a page can carry multiple
      sections. Page-wide scanning charges a unit with every OTHER section's
      numbers on the page as "deficits". When `unit` is given and its section
      region resolves on every page in `pages` (via `section_region_for_unit`),
      the deficit scan is restricted to that unit's own y-region. If the
      region cannot be resolved on any page, this FALLS BACK to page-wide
      scanning (old behaviour) and prepends a visible
      '  number-scan: unscoped ...' note — silence must never read as
      "checked and clean".

    Issues sorted by severity: longer numbers and bigger gaps first.
    """
    raw_text     = _page_raw_text(pdf_path, pages)
    text_nospace = re.sub(r'[\s,]', '', raw_text)

    scope_note: str | None = None
    pdf_counts: Counter = Counter()
    scoped = False
    if unit is not None:
        region_by_page: dict[int, tuple[float, float]] = {}
        unresolved_page = None
        for pg in pages:
            region, method = section_region_for_unit(pdf_path, unit, pg)
            if region is None:
                unresolved_page = (pg, method)
                break
            region_by_page[pg] = region
        if unresolved_page is None and region_by_page:
            scoped = True
            for pg, region in region_by_page.items():
                pdf_counts += _page_numbers_in_region(pdf_path, pg, region)
        else:
            pg, method = unresolved_page if unresolved_page else (pages[0] if pages else "?", "no-leaves")
            scope_note = (
                f"  number-scan: unscoped (section region {method} on p{pg}) "
                f"— page-wide fallback, cross-section false positives possible"
            )

    if not scoped:
        pdf_counts = _page_numbers(pdf_path, pages)

    json_counts: Counter = Counter()
    for t in ext.tables:
        for row in t.rows:
            for gcell in row.values:
                raw = gcell.value if isinstance(gcell, GCell) else str(gcell)
                cleaned = re.sub(r'[,()\s%]', '', raw)
                if re.fullmatch(r'\d+(?:\.\d+)?', cleaned):
                    json_counts[cleaned] += 1

    noise = set(section_ids)
    for sid in section_ids:
        m = re.match(r'^[A-Za-z]\.(.+)$', sid)
        numeric_suffix = m.group(1) if m else sid
        parts = numeric_suffix.split('.')
        for i in range(len(parts), 0, -1):
            noise.add('.'.join(parts[:i]))

    json_is_empty = not json_counts

    def _is_text_only(token: str) -> bool:
        escaped = re.escape(token)
        standalone = re.search(r'(?<![A-Za-z0-9])' + escaped + r'(?![A-Za-z0-9])', raw_text)
        return standalone is None and token in text_nospace

    issues = []
    for num, cnt in json_counts.items():
        if pdf_counts[num] >= cnt:
            continue
        if num in text_nospace:
            continue
        issues.append(("phantom", num, pdf_counts[num], cnt))

    for num, cnt in pdf_counts.items():
        if len(num) <= 2:
            continue
        if _YEAR_RE.match(num):
            continue
        if num in noise:
            continue
        if json_is_empty and len(num) >= 7:
            continue
        if json_counts[num] < cnt:
            issues.append(("deficit", num, cnt, json_counts[num]))

    issues.sort(key=lambda x: (len(x[1]), abs(x[2] - x[3])), reverse=True)

    out = [
        f"  phantom: '{n}' json={j}x pdf={p}x (not found in PDF)"       if kind == "phantom"
        else f"  deficit: '{n}' pdf={p}x json={j}x (missing from extraction)"
        for kind, n, p, j in issues
    ]
    if scope_note:
        out.insert(0, scope_note)
    return out


# A printed table caption declares its unit: '($m)', '(%)', '($)', 'S$m'.
# Footnote markers may trail it ('Key financial ratios (%)2,3').
_CAPTION_UNIT = re.compile(
    r"\(\s*(?:s?\$\s*'?m?|us\$\s*'?m?|%|bps?|bp|\$)\s*\)\s*[\d,\s\u00b9\u00b2\u00b3\u2070-\u2079*\u2020\u2021]*$",
    re.I)


def _states_a_unit(label: str) -> bool:
    return bool(_CAPTION_UNIT.search(str(label or "").strip()))


def split_caption_tables(t: GTable, geom: dict | None = None
                         ) -> list[tuple[GTable, dict | None]]:
    """One physical table holding SEVERAL captioned tables -> one GTable each.

    A bank prints the same section differently between vintages. DBS 4Q25 emits
    the Overview section as three tables, each with its own caption. DBS 2Q26
    emits ONE table captioned 'OVERVIEW' whose body carries those three captions
    as rows:

        OVERVIEW (45 rows)
          r1   Selected income statement items ($m)   <- was a table caption
          r2     Commercial book total income
          ...
          r24  Selected balance sheet items ($m)      <- was a table caption
          r33  Key financial ratios (%)2,3            <- was a table caption

    Left merged, every leaf gains an extra ancestor and — worse — the three
    logical tables compete for ONE `table_t.table_type_id`, so two thirds of the
    correctly-stamped leaves become unreachable by (table_type_id,
    canonical_leaf_id) addressing. Splitting restores one table_type_id per
    logical table.

    THE RULE (general, no per-bank or per-document condition): a row is a table
    caption when it carries NO values, sits at the table's MINIMUM printed level,
    STATES ITS UNIT, and is followed by at least one row before the next such
    caption. A split happens only when there are TWO OR MORE of them — a single
    leading caption is the ordinary caption-echo case that
    `apply_geometry`/`classify` already handle.

    THE UNIT SUFFIX IS THE DISCRIMINATOR, and it is load-bearing. Without it the
    rule cannot tell a table caption from an ordinary row banner: DBS's per-share
    table has 'Earnings2' and 'Reported earnings' as valueless level-0 rows, and
    splitting there shattered one table into two and broke five geometry tests.
    A printed table caption declares the unit its columns are in —
    'Selected income statement items ($m)', 'Key financial ratios (%)2,3',
    'Selected balance sheet items ($m)' — while a row banner scoping a group
    inside a table does not. That is a typographic convention of the filings, not
    a per-bank rule.

    Each part takes the caption row as its `title` and keeps the parent's
    columns; the caption row itself is dropped from the part's body (it IS the
    title now). The geometry side-car is sliced in step, so `rows` stays
    index-aligned to each part.

    Pure — returns new GTables, never mutates the input. Returns
    [(table, geom)] unchanged when the rule does not fire."""
    rows = t.rows
    if len(rows) < 2:
        return [(t, geom)]
    min_lvl = min((r.level or 0) for r in rows)

    def _valueless(r) -> bool:
        return not any(str(c.value or "").strip() for c in (r.values or []))

    caps = [i for i, r in enumerate(rows)
            if (r.level or 0) == min_lvl and _valueless(r) and i + 1 < len(rows)
            and _states_a_unit(r.label)]

    if len(caps) == 1 and caps[0] == 0 and not _states_a_unit(t.title):
        # TITLE REPAIR. Exactly one unit-stating caption, at row 0, and the
        # table's own title states no unit — the extractor took the page header
        # as the title and demoted the real caption to a row. DBS 2Q26's
        # half-year per-share table came through as
        # 'DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES' with 'Per share data
        # ($)3' as row 1. Two things break: the loader derives the table's unit
        # from the title, so every per-share figure inherited the document
        # default 'S$m' and rendered 25 instead of 24.69; and every row gained
        # the caption as an extra ancestor.
        grows0 = (geom or {}).get("rows") or []
        g2 = None
        if geom is not None:
            g2 = dict(geom)
            g2["title_clean"] = None
            g2["rows"] = grows0[1:] if len(grows0) == len(rows) else []
            if len(grows0) != len(rows):
                g2["all_rows_matched"] = False
        return [(t.model_copy(update={"title": rows[0].label,
                                      "rows": [r.model_copy(deep=True)
                                               for r in rows[1:]]}), g2)]

    if len(caps) < 2:
        return [(t, geom)]

    grows = (geom or {}).get("rows") or []
    aligned = len(grows) == len(rows)
    out: list[tuple[GTable, dict | None]] = []
    for n, start in enumerate(caps):
        end = caps[n + 1] if n + 1 < len(caps) else len(rows)
        body = rows[start + 1:end]
        if not body:
            continue
        part = t.model_copy(update={
            "title": rows[start].label,
            "rows": [r.model_copy(deep=True) for r in body],
        })
        sub_geom = None
        if geom is not None:
            sub_geom = dict(geom)
            sub_geom["title_clean"] = None
            sub_geom["rows"] = grows[start + 1:end] if aligned else []
            if not aligned:
                sub_geom["all_rows_matched"] = False
        out.append((part, sub_geom))
    return out or [(t, geom)]
