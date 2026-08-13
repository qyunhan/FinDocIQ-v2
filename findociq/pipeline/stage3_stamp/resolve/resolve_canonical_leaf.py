"""
resolve_canonical_leaf.py

Stamps `row_dim.canonical_leaf_id` by matching each value-carrying row against
the MASTERLIST.

===========================================================================
THE RULE — canonical_leaf_id ALWAYS COMES FROM data/derived/masterlist/
===========================================================================
An id is NEVER derived, invented, computed or guessed by this module. Every id
written to the DB is a verbatim copy of a `canonical_leaf_id` value read out of
`data/derived/masterlist/*.csv`. That file is authored and curated by a human
and is the single source of truth.

Derivation is used for ONE thing only: deciding WHICH masterlist row a given DB
row corresponds to. It never produces the id itself. A row that matches no
masterlist entry is reported UNRESOLVED and left NULL — it is never given a
computed id.

  Why this is written so emphatically: an earlier version of this pipeline
  matched against a GENERATED registry and stamped ids from it. Those ids
  differed from the authored masterlist on the of-which memo form
  (`of_which::net_interest_income` vs `of_which_net_interest_income`) and on the
  whole of FS_PER_SHARE. Any generated leaf list is a PROPOSAL for a human to
  curate; only `data/derived/masterlist/` is authoritative, and this module has
  no code path that can read anything else.

  `stage3_stamp.masterlist.masterlist_derive` is imported for NORMALISATION AND ANCESTRY ONLY.
  Despite the name it holds no leaf ids — it is the shared rule set (footnote
  strip, trailing-year strip, period classification, banner scoping) that both
  sides of a match must apply identically.

===========================================================================
MATCHING
===========================================================================
The masterlist carries `full_path` (the printed ancestor chain, ' > '-joined)
alongside each `canonical_leaf_id`. Matching is on that path, in three stages,
most exact first:

  1. RAW PATH, verbatim        — masterlist full_path == the row's printed chain
  2. NORMALISED PATH           — both sides normalised segment-by-segment via
                                 the shared `masterlist_derive` rules, so
                                 footnote drift ('Earnings2' vs 'Earnings') and
                                 unit/case differences do not break a match
  2b. CURATED LEAF ALIAS       — a human-stated RENAME, from
                                 `masterlist_leaf_aliases.yaml`. Tried only
                                 after every derivable form has missed, so a
                                 rule always beats a hand-written line.
  3. PERSISTED ALIAS           — a previously confirmed raw_path, from
                                 `canonical_leaf_alias`

  The two alias mechanisms are NOT alternatives. 3 is a memo: it is written
  only from matches 1 and 2 already made, so it can never introduce a mapping
  normalisation did not find. 2b is the only place a rename can be STATED.

  miss -> UNRESOLVED, canonical_leaf_id stays NULL, row reported for curation.

Normalisation comes from `stage3_stamp.masterlist.masterlist_derive`, which lives in
this package. There is no second copy anywhere.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_PIPELINE = Path(__file__).resolve().parents[2]
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from stage3_stamp.masterlist import masterlist_derive as D  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
MASTERLIST_DIR = REPO / "findociq/data/derived/masterlist"
ALIAS_YAML = MASTERLIST_DIR / "masterlist_leaf_aliases.yaml"
FAMILY_ALIAS_YAML = MASTERLIST_DIR / "masterlist_source_family_aliases.yaml"

# Filler words that carry no document-type meaning. Dropping them is what lets
# 'Media_Release_and_Financial_Highlights' and 'Media_Release_Financial_
# Highlights' — the same OCBC filing, punctuated differently across vintages —
# both reach the masterlist's 'Media_release_and_financial_highlights'.
_FAMILY_FILLER = {"and", "the", "of", "for"}
# PRESENTATION / ASSURANCE qualifiers on a filing's name. They describe how the
# same statement set is presented at a given reporting date, not a different
# KIND of filing, and every bank varies them by vintage:
#
#   OCBC 4Q25  Condensed Financial Statements
#   OCBC 2Q26  Unaudited Interim Financial Statements
#   UOB  4Q25  condensed-financial-statements
#   UOB  2Q26  Condensed Interim Financial Statements
#
# Four names, one family. Dropping these tokens collapses all four onto
# 'financial_statements' and leaves 'media_release_financial_highlights'
# untouched, so the only distinction the family bar needs — statements vs media
# release — survives intact. Two banks doing the same thing is a convention;
# aliasing each one by hand would be four hand-written facts where one rule
# does, and would need a fifth the next time a bank drops 'Condensed'.
_FAMILY_QUALIFIER = {"condensed", "interim", "unaudited", "audited"}
# A period token leading the doc_id remainder: 1Q25 / 2H26 / FY2025 / 2025.
_FAMILY_PERIOD = re.compile(r"^(?:[1-4]q|[12]h)\d{2,4}$|^fy?\d{4}$|^\d{4}$")

normalize_segment = D.normalize_segment
normalize_caption = D.normalize_caption


# ---------------------------------------------------------------------------
# MASTERLIST — the only source of canonical_leaf_id
# ---------------------------------------------------------------------------
def norm_path(path: str) -> str:
    """A ' > '-joined printed chain -> a comparable key. Segment-wise via the
    shared normaliser, so 'Earnings2 > Basic' and 'Earnings > Basic' agree."""
    segs = [D.normalize_segment(s) for s in str(path or "").split(">")]
    return "::".join(s for s in segs if s)


def _read_csv(path: Path):
    """DictReader over a HAND-MAINTAINED csv.

    Tolerates leading blank lines and a UTF-8 BOM: the masterlist is edited by a
    human in an editor, and a stray blank line above the header made
    `csv.DictReader` take that blank line AS the header — every lookup then
    raised KeyError('bank') and the whole load failed. The data is not in
    question; the file just needs reading forgivingly. Line endings (CRLF/LF)
    are handled by newline="" as usual."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return []
    return list(csv.DictReader(lines))


def match_variants(path: str):
    """The forms of a printed path that may legitimately match a masterlist row.

    Yields the path as-is, then the path with its OUTERMOST ancestor removed.

    WHY: one physical table can contain several logical ones. DBS 4Q25 prints
    Selected income statement items / Selected balance sheet items / Key
    financial ratios as three separate tables; DBS 2Q26 prints them as ONE table
    captioned 'OVERVIEW' with those three captions as rows inside it. Every leaf
    then carries an extra ancestor:

        2Q26        selected_income_statement_items::commercial_book_total_income::net_interest_income
        masterlist                                   commercial_book_total_income::net_interest_income

    `classify()` already drops a caption echo, but only the one matching the
    TABLE's own title — which is 'OVERVIEW' here, so the three inner captions
    survive as banners. Measured on 2Q26: direct match 0/41, caption-stripped
    40/41 (FS_BALANCE_SELECTED 8/8, FS_RATIOS_KEY 12/12, FS_INCOME_SELECTED
    20/21).

    This is a MATCH tolerance only. The id written is still copied verbatim from
    the masterlist, and a stripped path is accepted only if the masterlist
    actually contains it, scoped to (bank, table_type_id)."""
    p = str(path or "")
    yield p
    if "::" in p:
        yield p.split("::", 1)[1]


def norm_family(s: str) -> str:
    """A document-type name -> a comparable key. Applied to BOTH sides, exactly
    like `norm_path`: the masterlist's `source_family` and the type read off a
    doc_id must be normalised by the same rule or the bar never matches."""
    toks = [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]
    return "_".join(t for t in toks
                    if t not in _FAMILY_FILLER and t not in _FAMILY_QUALIFIER)


def load_source_family_aliases(path=None) -> dict[str, dict[str, str]]:
    """Curated document-type synonyms -> {bank: {normalised doc type: family}}.

    See masterlist_source_family_aliases.yaml. Missing file -> {}.
    """
    p = Path(path or FAMILY_ALIAS_YAML)
    if not p.exists():
        return {}
    import yaml                                  # noqa: PLC0415 — optional dep
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(bank).strip(): {norm_family(k): norm_family(v)
                                for k, v in (pairs or {}).items()}
            for bank, pairs in doc.items()}


def doc_source_family(doc_id: str, bank: str, aliases=None) -> str | None:
    """The document's own type, from its doc_id — 'condensed_financial_statements'.

    doc_ids are '<BANK>_<PERIOD>_<type>' by construction (run_doc derives them
    from the PDF stem), so the type is what is left after dropping a leading
    bank token and a leading period token. Both are optional: some early DBS
    doc_ids are bare ('1Q23_trading_update').

    Returns None when nothing is left to name a type — the caller then applies
    NO family bar, which is the pre-existing behaviour. A document we cannot
    classify must not silently lose its tables.
    """
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", doc_id or "") if t]
    if toks and bank_of(toks[0]) != "Other":
        toks = toks[1:]
    if toks and _FAMILY_PERIOD.match(toks[0].lower()):
        toks = toks[1:]
    fam = norm_family("_".join(toks))
    if not fam:
        return None
    return (aliases or {}).get(bank, {}).get(fam, fam)


def load_leaf_aliases(path=None) -> dict[tuple[str, str], dict[str, str]]:
    """The CURATED renames -> {(bank, table_type_id): {old_leaf_id: new_leaf_id}}.

    The residue after normalisation. A bank that RENAMES a line prints genuinely
    different text for the same row, and no amount of footnote/unit/case
    stripping can bridge that — it is a fact about the filing, so a human states
    it once in `masterlist_leaf_aliases.yaml` and it holds for every vintage.
    Anything a rule CAN derive belongs in `masterlist_derive`, never here; the
    yaml's own header states that boundary and it is the file's whole value.

    Note this does NOT introduce ids: the target of every alias is validated
    against the masterlist in `load_masterlist`, so the invariant that every
    stamped id is a verbatim masterlist value still holds.

    Missing file -> {} : the aliases are a curation aid, not a dependency.
    """
    p = Path(path or ALIAS_YAML)
    if not p.exists():
        return {}
    import yaml                                  # noqa: PLC0415 — optional dep
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[tuple[str, str], dict[str, str]] = {}
    for bank, by_table in (doc or {}).items():
        for tt, pairs in (by_table or {}).items():
            for old, new in (pairs or {}).items():
                out.setdefault((str(bank).strip(), str(tt).strip()), {})[
                    str(old).strip()] = str(new).strip()
    return out


def load_masterlist(paths=None):
    """Read every masterlist CSV -> the lookup tables the resolver matches on.

    Returns a dict keyed (bank, table_type_id) with:
        ids         set of canonical_leaf_id  (for validation only)
        sections    normalised canonical_section values this table belongs to —
                    the guard that stops a caption-collision table in another
                    section borrowing this table's leaves
        families    normalised `source_family` values — which KIND of filing
                    this table type is printed in. Enforced in locate_tables().
                    EMPTY means unconstrained: DBS_masterlist.csv carries no
                    source_family column, and an absent column must not bar
                    every DBS table from ever being found.
        by_raw      {full_path            -> canonical_leaf_id}
        by_norm     {norm_path(full_path) -> canonical_leaf_id}
        by_alias    {renamed leaf id      -> canonical_leaf_id}, from
                    masterlist_leaf_aliases.yaml. A SEPARATE index on purpose:
                    `by_norm` doubles as the MIN_MATCH_FRACTION denominator in
                    locate_tables(), so folding aliases into it would make a
                    table HARDER to find on every vintage that prints the
                    current name. Aliases may only ever add matches.
        ordered     [(line_ordinal, canonical_leaf_id, label)]
    Required columns: bank, table_type_id, line_ordinal, canonical_leaf_id,
    label, full_path.
    """
    if paths is None:
        # `*_masterlist.csv`, NOT `*.csv`: the directory now holds a second
        # KIND of file — `*_masterlist_cols.csv`, the column block read by
        # resolve_canonical_col. It has no `canonical_leaf_id` column, so the
        # broad glob fed it here and load died on KeyError. The row block and
        # the column block are separate files by design (spec §6: two axes,
        # additive), so each loader names the shape it reads.
        paths = sorted(MASTERLIST_DIR.glob("*_masterlist.csv"))
    paths = [Path(p) for p in paths]
    if not paths:
        raise FileNotFoundError(
            f"no masterlist CSV found in {MASTERLIST_DIR} — canonical_leaf_id "
            f"can only come from there")
    out: dict[tuple[str, str], dict] = {}
    for p in paths:
        for r in _read_csv(p):
                key = (r["bank"].strip(), r["table_type_id"].strip())
                e = out.setdefault(key, dict(ids=set(), by_raw={}, by_norm={},
                                             by_alias={}, ordered=[],
                                             sections=set(), families=set(),
                                             source=p.name))
                if r.get("canonical_section"):
                    e["sections"].add(D.normalize_segment(
                        r["canonical_section"], is_data=False))
                if r.get("source_family"):
                    e["families"].add(norm_family(r["source_family"]))
                cid = r["canonical_leaf_id"].strip()
                fp = (r.get("full_path") or r.get("label") or "").strip()
                e["ids"].add(cid)
                e["by_raw"].setdefault(fp, cid)
                e["by_norm"].setdefault(norm_path(fp), cid)
                e["ordered"].append((int(r["line_ordinal"]), cid,
                                     (r.get("label") or "").strip()))
    for e in out.values():
        e["ordered"].sort()

    # Curated renames, attached to the entry they belong to. Validated against
    # that entry's OWN id set: an alias may only point at a leaf the masterlist
    # already declares for this (bank, table_type_id), so the "every stamped id
    # is copied verbatim from the masterlist" invariant survives. A typo in the
    # yaml is a hard error rather than a silently dead line — a rename that
    # quietly stops applying is exactly the failure this file exists to prevent.
    for (bank, tt), pairs in load_leaf_aliases().items():
        e = out.get((bank, tt))
        if e is None:
            raise RuntimeError(
                f"alias for ({bank}, {tt}) in {ALIAS_YAML.name}, but no "
                f"masterlist entry has that (bank, table_type_id)")
        for old, new in pairs.items():
            if new not in e["ids"]:
                raise RuntimeError(
                    f"alias {old!r} -> {new!r} for ({bank}, {tt}): {new!r} is "
                    f"not a canonical_leaf_id in {e['source']}")
            if old in e["by_norm"]:
                raise RuntimeError(
                    f"alias {old!r} for ({bank}, {tt}) collides with a LIVE "
                    f"masterlist path — aliases are for names the masterlist "
                    f"no longer carries")
            e["by_alias"][old] = new
    return out


# ---------------------------------------------------------------------------
# DB HELPERS
# ---------------------------------------------------------------------------
def _load_rows(cur, doc_id, table_id) -> list[D.Row]:
    """row_dim as shared-module Rows, with has_values over ALL columns.

    Derived ('% chg') columns are NOT excluded here: some printed lines carry
    values only there — DBS's 'Constant-currency change' is blank in every
    period column — and such a line still exists and still needs an id."""
    valued = {r[0] for r in cur.execute(
        "SELECT DISTINCT row_id FROM cell_fact WHERE doc_id = ? AND table_id = ? "
        "AND value_num IS NOT NULL", (doc_id, table_id))}
    return [D.Row(row_id=rid, label=str(lab or ""), level=int(lvl or 0),
                  parent=par, has_values=rid in valued)
            for rid, lab, lvl, par in cur.execute(
                "SELECT row_id, row_leaf_label, row_hierarchy, row_parent "
                "FROM row_dim WHERE doc_id = ? AND table_id = ? ORDER BY row_id",
                (doc_id, table_id))]


def _printed_chain(row: D.Row) -> str:
    """The row's printed ancestor chain, verbatim labels, ' > '-joined —
    directly comparable to the masterlist's `full_path`."""
    return " > ".join(list(row.ancestor_labels_raw or row.ancestor_labels)
                      + [row.identity_label or row.label])


# ---------------------------------------------------------------------------
# RESOLUTION
# ---------------------------------------------------------------------------
def resolve_table(cur, doc_id, table_id, bank, table_type_id, master_entry,
                  alias_map=None, seed_caption=None, discriminator=None):
    """Match every value-carrying row of ONE table to a masterlist entry.

    `master_entry` is one value from `load_masterlist()`. Every id in the result
    is copied from it; none is computed.
    """
    alias_map = alias_map or {}
    rows = _load_rows(cur, doc_id, table_id)
    D.classify(rows, D.normalize_caption(seed_caption) if seed_caption else None)
    D.build_ancestry(rows, extra_banner=discriminator)

    results, new_aliases = [], []
    for r in rows:
        if r.cls not in (D.DATA, D.PERIOD_ROW):
            continue
        own = r.identity_label or r.label
        if not own:
            continue
        raw = _printed_chain(r)
        nrm = norm_path(raw)

        cid, how = None, "unresolved"
        if raw in alias_map:                       # 3. persisted alias
            cid, how = alias_map[raw], "alias_hit"
        elif raw in master_entry["by_raw"]:        # 1. verbatim path
            cid, how = master_entry["by_raw"][raw], "raw_path"
            new_aliases.append((raw, cid))
        else:                                      # 2. normalised, +/- caption
            for v in match_variants(nrm):
                if v in master_entry["by_norm"]:
                    cid = master_entry["by_norm"][v]
                    how = "norm_path" if v == nrm else "caption_stripped"
                    new_aliases.append((raw, cid))
                    break
            else:                                  # 2b. curated rename
                # LAST, after every derivable form has been tried, so a rule
                # that can bridge the gap always wins over a hand-written line.
                for v in match_variants(nrm):
                    if v in master_entry["by_alias"]:
                        cid = master_entry["by_alias"][v]
                        how = "leaf_alias"
                        new_aliases.append((raw, cid))
                        break

        results.append(dict(row_id=r.row_id, canonical_leaf_id=cid, outcome=how,
                            raw_path=raw, norm_path=nrm, label=r.label))
    return results, new_aliases


def suggest_for_unresolved(results, ordered):
    """Ordinal-position suggestions for unresolved rows — a CURATION AID.
    Never used for matching, and never turned into a stamp."""
    id_to_ord = {cid: o for o, cid, _lab in ordered}
    out = {}
    for i, r in enumerate(results):
        if r["outcome"] != "unresolved":
            continue
        prev_o = next((id_to_ord[results[j]["canonical_leaf_id"]]
                       for j in range(i - 1, -1, -1)
                       if results[j]["canonical_leaf_id"] in id_to_ord), None)
        next_o = next((id_to_ord[results[j]["canonical_leaf_id"]]
                       for j in range(i + 1, len(results))
                       if results[j]["canonical_leaf_id"] in id_to_ord), None)
        out[r["row_id"]] = [(o, cid, lab) for o, cid, lab in ordered
                            if (prev_o is None or o > prev_o)
                            and (next_o is None or o < next_o)]
    return out


def load_aliases(cur, bank, table_type_id):
    try:
        return {r[0]: r[1] for r in cur.execute(
            "SELECT raw_path, canonical_leaf_id FROM canonical_leaf_path_alias "
            "WHERE bank = ? AND table_type_id = ?", (bank, table_type_id))}
    except sqlite3.OperationalError:
        return {}


def ensure_alias_table(con):
    con.execute("""
        -- NOT `canonical_leaf_alias`: that legacy table exists in compiled_fs.db
        -- keyed on (alias_row_label_norm, alias_parent_label_norm) — a label
        -- PAIR. This one keys on the full printed path. CREATE TABLE IF NOT
        -- EXISTS silently kept the legacy shape and every insert then failed
        -- with "no column named raw_path", so the two need distinct names.
        CREATE TABLE IF NOT EXISTS canonical_leaf_path_alias (
            bank              TEXT NOT NULL,
            table_type_id     TEXT NOT NULL,
            raw_path          TEXT NOT NULL,
            canonical_leaf_id TEXT NOT NULL,
            first_seen_doc    TEXT,
            PRIMARY KEY (bank, table_type_id, raw_path)
        )""")


# ---------------------------------------------------------------------------
# STAMPING
# ---------------------------------------------------------------------------
def stamp_into_db(con, doc_id, table_id, results, bank=None, table_type_id=None,
                  new_aliases=(), master_ids=None, dry_run=True):
    """Write canonical_leaf_id onto row_dim.

    GUARD: every id written is re-checked against the masterlist id set. If an
    id ever appears that the masterlist does not contain, this raises rather
    than writing it — the invariant is that the DB can only ever hold ids the
    curated masterlist declares."""
    n = 0
    cur = con.cursor()
    for r in results:
        cid = r["canonical_leaf_id"]
        if not cid:
            continue
        if master_ids is not None and cid not in master_ids:
            raise RuntimeError(
                f"refusing to stamp {cid!r} — not in the masterlist for "
                f"({bank}, {table_type_id}). canonical_leaf_id must come from "
                f"data/derived/masterlist/.")
        if not dry_run:
            # table_type_id is written on the ROW, next to the leaf it belongs
            # to, exactly as load_v7._stamp_identity does — the two halves of
            # the address are stamped by the same masterlist entry and cannot
            # disagree. table_t holds only ONE type, so an exhibit carrying
            # rows from several (OCBC 'FINANCIAL HIGHLIGHTS' = income + balance
            # + ratio lines) used to strand every leaf under the losing type.
            cur.execute("UPDATE row_dim SET canonical_leaf_id = ?, "
                        "table_type_id = ? "
                        "WHERE doc_id = ? AND table_id = ? AND row_id = ?",
                        (cid, table_type_id, doc_id, table_id, r["row_id"]))
        n += 1
    if not dry_run and new_aliases and bank and table_type_id:
        ensure_alias_table(con)
        cur.executemany(
            "INSERT OR IGNORE INTO canonical_leaf_path_alias"
            "(bank, table_type_id, raw_path, canonical_leaf_id, first_seen_doc) "
            "VALUES (?,?,?,?,?)",
            [(bank, table_type_id, p, c, doc_id) for p, c in new_aliases])
    return n


# ---------------------------------------------------------------------------
# TABLE LOCATION — BY CONTENT. No seed, no caption registry, no doc_kind.
#
# A table IS the one whose printed row paths match the masterlist's `full_path`
# values. Scoring every table against the masterlist is strictly better than
# resolving its printed caption:
#
#   * it is immune to caption collisions. DBS prints 'Selected balance sheet
#     items ($m)' in Overview and 'Selected balance sheet items' under
#     PERFORMANCE BY GEOGRAPHY; caption matching fused them, content matching
#     scores the geography table 0 because its rows are different lines.
#   * it finds the same table in EVERY vintage with no per-period config —
#     4Q25, 2Q25, 1Q25, 1Q26 and the trading updates all match off one
#     masterlist.
#   * it needs nothing but the masterlist and the DB.
#
# The caption used to drop a table's caption-echo header row comes from
# `table_t.table_title` — the document's own printed title.
#
# NOTE: this locates TABLES. It never yields a canonical_leaf_id.
# ---------------------------------------------------------------------------
MIN_MATCH_FRACTION = 0.5     # of the masterlist's leaves for that table
MIN_MATCH_ABSOLUTE = 2       # never accept a 1-row coincidence


def bank_of(name: str) -> str:
    """UOB is tested FIRST and OCBC matched on its full spelling: 'United
    Overseas Bank' contains 'OVERSEA', so a bare substring test routes every UOB
    document to OCBC."""
    u = (name or "").upper()
    if "UOB" in u or "UNITED OVERSEAS" in u:
        return "UOB"
    if "OCBC" in u or "OVERSEA-CHINESE" in u or "OVERSEA CHINESE" in u:
        return "OCBC"
    if "DBS" in u:
        return "DBS"
    return "Other"


def table_paths(cur, doc_id, table_id, title):
    """The normalised printed path of every value-carrying row in one table."""
    rows = _load_rows(cur, doc_id, table_id)
    D.classify(rows, D.normalize_caption(title))
    D.build_ancestry(rows)
    out = set()
    for r in rows:
        if r.cls in (D.DATA, D.PERIOD_ROW) and (r.identity_label or r.label):
            out.update(match_variants(norm_path(_printed_chain(r))))
    return out


def locate_tables(con, master, doc_ids=None):
    """(bank, table_type_id) -> [table dicts], scored by content match.

    `doc_ids` restricts the search (e.g. one document type at a time); None
    searches every document in the DB. Each hit carries `matched` / `n_rows` so
    the caller can see how strong the identification was.

    SCOPED TO THE DOCUMENT'S FAMILY FIRST, then matched by content. A table type
    is printed in a KIND of filing, and the masterlist says which in its
    `source_family` column, so a type may only claim a table inside a document
    of its own family. Content score alone is not enough: OCBC's 14-leaf
    FS_INCOME_SELECTED, authored off the media release, cleared
    MIN_MATCH_FRACTION against the consolidated income statement in the
    FINANCIAL STATEMENTS and re-stamped its rows, stranding six dashboard lines
    whose anchors address FS_INCOME_CONSOLIDATED. Family is a structural bar,
    not a tie-break — the wrong table type never gets scored at all.

    Two deliberate escapes, both toward NOT losing tables:
      * an entry with no `source_family` (all of DBS_masterlist.csv) is
        unconstrained;
      * a doc_id that yields no type is unconstrained.
    """
    from collections import defaultdict
    fam_aliases = load_source_family_aliases()
    doc_family: dict[str, str | None] = {}
    tabs = con.execute(
        "SELECT t.doc_id, t.table_id, t.table_title, t.section_id, t.page_range, "
        "       t.period, t.period_span, d.institution "
        "FROM table_t t JOIN document d ON d.doc_id = t.doc_id").fetchall()
    work = con.cursor()
    paths: dict[tuple[str, str], set] = {}
    hits = defaultdict(list)
    for doc_id, tid, title, sec, pg, per, span, inst in tabs:
        if doc_ids and doc_id not in doc_ids:
            continue
        bank = bank_of(inst or doc_id)
        got = paths.setdefault((doc_id, tid), table_paths(work, doc_id, tid, title))
        if not got:
            continue
        fam = doc_family.get(doc_id, ...)
        if fam is ...:
            fam = doc_family.setdefault(
                doc_id, doc_source_family(doc_id, bank, fam_aliases))
        for (mbank, tt), e in master.items():
            if mbank != bank:
                continue
            if e["families"] and fam and fam not in e["families"]:
                continue                       # wrong KIND of filing
            want = set(e["by_norm"])
            # A curated rename identifies the table just as well as the current
            # name does, so alias hits count in the NUMERATOR. They stay out of
            # `want`, the denominator: a vintage printing the current name would
            # otherwise be scored against leaves it was never going to print.
            n = len((want | set(e["by_alias"])) & got)
            if n < MIN_MATCH_ABSOLUTE or n < MIN_MATCH_FRACTION * len(want):
                continue
            hits[(mbank, tt)].append(dict(
                doc_id=doc_id, table_id=tid, title=title, page=pg, period=per,
                span=span, section_id=sec, discriminator=None,
                matched=n, n_rows=len(got), n_leaves=len(want)))
    for k in hits:
        hits[k].sort(key=lambda h: (-h["matched"], h["doc_id"]))
    return _one_table_one_type(con, hits, master)


_SECTION_NOTE_PREFIX = re.compile(r"^notes?_")


def _norm_section(s: str) -> str:
    """`canonical_section` -> a comparable key, for the section bar only.

    `norm_family` (which already drops filler and the condensed/interim/
    unaudited/audited qualifiers) plus a leading `note`/`notes` token. A curator
    naturally writes 'Note 13.1 Business segments' while the document prints the
    heading '13.1 Business segments' — the NUMBER carries the identity, the word
    'Note' is structural, exactly like 'condensed'.

    Measured on OCBC's curated condensed-FS masterlist: `norm_family` alone
    matched 6 of 20 declared sections, and dropping this one token takes it to
    18. That gap was not cosmetic — a type barred from its own section is
    starved, and the no-starvation floor then hands it whatever else it scored
    on. FS_PERF_BY_SEGMENT_CONSOL was landing on the CONSOLIDATED INCOME
    STATEMENT, whose lines it legitimately repeats per segment, and eight
    dashboard anchors went dark.

    Also tried and NOT adopted: matching the declared section against the
    table's own title as well as its section heading. Measured no gain — 18/20
    either way — so the extra surface buys nothing here.
    """
    return _SECTION_NOTE_PREFIX.sub("", norm_family(s or ""))


def _one_table_one_type(con, hits, master):
    """A physical table belongs to exactly ONE table_type_id.

    `locate_tables` scores each masterlist entry against every table
    independently, so two entries can both clear the bar on the same table.
    `stamp_tables` then stamps both, last write wins, and rows end up carrying a
    leaf id from an exhibit they are not part of. Measured before this pass: 27
    non-duplicate tables contested, one claimed by SIX entries at once, and DBS's
    business-segments table stamped as FS_PERF_BY_GEOGRAPHY.

    TWO STAGES, mirroring how `families` already works — a structural bar first,
    content only to decide among what survives it.

    1. SECTION BAR. `load_masterlist` has always computed `sections` and its
       docstring calls it "the guard that stops a caption-collision table in
       another section borrowing this table's leaves" — but nothing ever read it.
       An entry may only claim a table printed in a section it declares.
       Normalised with `norm_family`, not `normalize_segment`, so the
       presentation/assurance qualifiers collapse: DBS's masterlist was authored
       off the AUDITED statement of changes in equity and must still claim the
       UNAUDITED half-year one, which is the same exhibit at a different
       reporting date.

       If the bar would leave a table with NO claimant it is not applied to that
       table. Losing a table outright is worse than an ambiguity, and an orphan
       means the masterlist's `canonical_section` is stale, not that the table
       belongs to nobody.

    2. DECIDE by match FRACTION (matched / declared), then matched, then id.
       Fraction, not the raw count: the count favours whichever entry declares
       more leaves, which is how the geography entry (18/22) out-scored the
       segment entry (9/9) on the segments table. Fraction asks "how much of
       what this exhibit IS did we find", which is the question.

    Contests it cannot split are LOGGED, never silently resolved.
    """
    sect = {(d, t): s for d, t, s in con.execute(
        "SELECT t.doc_id, t.table_id, s.section_title FROM table_t t "
        "LEFT JOIN section s ON s.doc_id=t.doc_id AND s.section_id=t.section_id")}
    claim: dict[tuple, list] = defaultdict(list)
    for key, refs in hits.items():
        for h in refs:
            claim[(h["doc_id"], h["table_id"])].append((key, h))

    dropped: set[tuple] = set()             # (entry_key, doc_id, table_id) to remove
    for (doc_id, tid), cands in claim.items():
        if len(cands) < 2:
            continue
        # THE BAR RUNS FIRST, WHATEVER THE CROWD SIZE. It is structural — an
        # entry may not claim a table printed in a section it does not declare —
        # so a crowd is no reason to skip it. Ordering it after the crowd test
        # left OCBC's consolidated income statement claimed by three entries,
        # two of which (13.1 Business segments, 13.2 Geographical segments) the
        # bar removes outright: those notes repeat the income statement's lines
        # per segment, so content alone can never separate them.
        s0 = _norm_section(sect.get((doc_id, tid)) or "")
        barred = [c for c in cands
                  if not master[c[0]]["sections"]
                  or s0 in {_norm_section(x) for x in master[c[0]]["sections"]}]
        if barred and len(barred) < len(cands):
            for c in cands:
                if c not in barred:
                    dropped.add((c[0], doc_id, tid))
            cands = barred
            if len(cands) < 2:
                continue
        if len({c[0] for c in cands}) > 2:
            # THREE OR MORE CLAIMANTS IS NOT A MIX-UP. OCBC's p12 ratio block is
            # claimed by six entries at once — FS_RATIOS_KEY, FS_PER_SHARE,
            # FS_CAPITAL_ADEQUACY, REG_LCR, REG_NSFR, REG_LEVERAGE — because the
            # page really does print per-share data, capital ratios and all
            # three MAS ratios in ONE table, and OCBC's masterlist models it as
            # five overlapping entries over that one table. No single
            # table_type_id is the right answer, and forcing one measurably
            # broke six dashboard lines: the block moved to FS_PER_SHARE on an
            # alphabetical tie-break and the anchors that address FS_RATIOS_KEY
            # went dark (83/83 -> 77/83).
            #
            # The cross-claims this pass exists to fix are all PAIRWISE —
            # segment vs geography, customer loans vs NPA, equity Group vs
            # Company. A crowd is a modelling problem (a combined exhibit, or a
            # duplicate cluster quarantine has not caught), so leave it alone
            # and say so.
            print(f"  [contest] {tid[:52]} claimed by "
                  f"{len({c[0][1] for c in cands})} types "
                  f"({', '.join(sorted(c[0][1] for c in cands))[:60]}) — "
                  f"combined exhibit, left unassigned")
            continue
        s = _norm_section(sect.get((doc_id, tid)) or "")
        keep = [c for c in cands
                if not master[c[0]]["sections"]
                or s in {_norm_section(x) for x in master[c[0]]["sections"]}]
        if not keep:                        # stage 1 would orphan it — skip the bar
            keep = list(cands)
        keep.sort(key=lambda c: (-(c[1]["matched"] / max(c[1]["n_leaves"], 1)),
                                 -c[1]["matched"], c[0][1]))
        win = keep[0]
        rival = keep[1] if len(keep) > 1 else None
        if rival and (win[1]["matched"] / max(win[1]["n_leaves"], 1)
                      == rival[1]["matched"] / max(rival[1]["n_leaves"], 1)):
            print(f"  [contest] {tid[:52]} — {win[0][1]} and {rival[0][1]} both "
                  f"match {win[1]['matched']}/{win[1]['n_leaves']}; took "
                  f"{win[0][1]} on id order")
        for c in cands:
            if c is not win:
                dropped.add((c[0], doc_id, tid))

    # NEVER STARVE AN EXHIBIT TYPE. An entry that loses every one of its claims
    # stops existing as far as stamping is concerned, which is a worse failure
    # than the cross-claim this pass exists to fix. Measured: strict assignment
    # left six OCBC entries with nothing, including FS_INCOME_SELECTED and
    # FS_RATIOS_KEY, both of which the highlights dashboard addresses.
    #
    # The shape behind it is OCBC's p12 ratio block: ONE printed table extracted
    # nine times, once per section header on the page, and it genuinely carries
    # per-share data AND LCR AND NSFR AND leverage AND capital ratios. No single
    # table_type_id is the right answer for it — the real fix is
    # quarantine_duplicate_page_tables plus a masterlist that can address a
    # sub-range of one table, neither of which is this pass's job.
    #
    # So: give each starved entry its best claim back, and say so out loud.
    for key in list(hits):
        if all((key, h["doc_id"], h["table_id"]) in dropped for h in hits[key]):
            best = max(hits[key], key=lambda h: (h["matched"] / max(h["n_leaves"], 1),
                                                 h["matched"]))
            dropped.discard((key, best["doc_id"], best["table_id"]))
            print(f"  [contest] {key[0]}/{key[1]} lost every claim; kept "
                  f"{best['table_id'][:44]} ({best['matched']}/{best['n_leaves']}) "
                  f"so the type still resolves")

    if dropped:
        out = defaultdict(list)
        for key, refs in hits.items():
            kept = [h for h in refs
                    if (key, h["doc_id"], h["table_id"]) not in dropped]
            if kept:
                out[key] = kept
        return out
    return hits
