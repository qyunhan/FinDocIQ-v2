"""concept.resolve_deterministic — stamp row_dim.concept_key on an exact
normalised-label match against concept_map. Free, fast, auditable; the LLM only
ever sees what this step cannot match.

For every row_dim row with a non-null row_leaf_label:
  * STRUCTURAL SKIP (never a concept): the normalised label is a date/period
    expression (reuse load_v7.is_period_text / is_date_text), starts with
    'note'/'notes', or has no alpha content.
  * DIMENSIONAL SUPPRESSION: a row in a dimensional-breakdown table (geography/
    segment/industry -- load_dictionary.dimensional_scopes) gets NO wildcard
    fallback, because its labels re-print spine line items at a sub-entity
    grain. Only an alias declared for that scope may stamp it; a stale stamp
    from before the scope existed is cleared, and the row never reaches the LLM.
  * else look up norm(label) in concept_map, preferring a row whose
    table_type_norm matches this row's table type over a wildcard ('*') row.
  * on a match: stamp row_dim.concept_key and append a concept_resolution_log row
    (method='deterministic', confidence=1.0).

IDEMPOTENT: a row already carrying the matched key is a no-op (not re-logged); a
row whose mapping CHANGED is UPDATEd and a fresh log row appended.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.load_dictionary import (NO_WILDCARD_SCOPES, _now_iso,  # noqa: E402
                                     dimensional_scopes, map_table_type_norm)
from concept.normalize import norm  # noqa: E402
from pass2.load_v7 import is_date_text, is_period_text  # noqa: E402

_HAS_ALPHA = re.compile(r"[a-z]")


def skip_reason(label: str, nl: str) -> str | None:
    """Why this label is structural (not a concept), else None. `nl` = norm(label)."""
    if not nl or not _HAS_ALPHA.search(nl):
        return "no-alpha"
    if nl.split(" ", 1)[0] in ("note", "notes"):
        return "note"
    if is_period_text(label) or is_date_text(label):
        return "date/period"
    return None


def build_lookup(con):
    """Return resolve(norm_label, table_type_norm) -> concept_key|None. Loads the
    whole concept_map once; a type-scoped row beats a wildcard row.

    A NO_WILDCARD_SCOPES bucket (a dimensional breakdown -- geography/segment/
    industry, see load_dictionary.dimensional_scopes) gets NO wildcard fallback:
    its rows re-print spine labels at a sub-entity grain, so only an alias
    DECLARED for that bucket may claim one.
    """
    rows = con.execute(
        "SELECT label_norm, COALESCE(table_type_norm,'*'), concept_key "
        "FROM concept_map").fetchall()
    by_label: dict[str, dict[str, str]] = {}
    for ln, ttn, key in rows:
        by_label.setdefault(ln, {})[ttn] = key

    def resolve(nl: str, ttn: str) -> str | None:
        cand = by_label.get(nl)
        if not cand:
            return None
        scoped = cand.get(ttn)
        if scoped is not None:
            return scoped
        if ttn in NO_WILDCARD_SCOPES:
            return None                           # opt-in by declaration only
        return cand.get("*")                      # scoped wins, else wildcard
    return resolve


def _fetch_rows(con):
    """Every row_dim row + its table_type and parent label (for LLM context).

    The label read here is `row_leaf_label_clean` when the pass2 geometry stage
    matched the row, else the verbatim `row_leaf_label`. Concept identity must
    not carry footnote numbering: 'Return on equity4, 5' and 'Return on equity3,
    4' are the SAME line item in two quarters of the same filing, and norm()'s
    glued-digit rule (>=5-letter guard, single trailing run) cannot strip a
    comma-separated marker list. Geometry decides it typographically instead.
    This also feeds the LLM residue, so accepted answers no longer mint
    footnote-polluted concept_map aliases. Purely additive: a row with no
    geometry match (any document not yet re-loaded) resolves exactly as before.
    """
    return con.execute(
        "SELECT r.doc_id, r.table_id, r.row_id, "
        "       COALESCE(r.row_leaf_label_clean, r.row_leaf_label), r.concept_key, "
        "       t.table_type, COALESCE(p.row_leaf_label_clean, p.row_leaf_label), "
        "       r.concept_key_human, r.identity_source "
        "FROM row_dim r "
        "JOIN table_t t ON t.doc_id=r.doc_id AND t.table_id=r.table_id "
        "LEFT JOIN row_dim p ON p.doc_id=r.doc_id AND p.table_id=r.table_id "
        "     AND p.row_id=r.row_parent "
        "WHERE r.row_leaf_label IS NOT NULL "
        "ORDER BY r.doc_id, r.table_id, r.row_id").fetchall()


def resolve_deterministic(con, *, dry_run: bool = False) -> dict:
    """Run the deterministic pass on `con`. Returns a report dict; `residue` is the
    list of still-unmatched, non-structural rows the LLM step will classify."""
    resolve = build_lookup(con)
    dim_scope = dimensional_scopes(con)
    cur = con.cursor()
    total = stamped = restamped = skipped = unmatched = already = 0
    suppressed = unstamped = 0
    residue: list[dict] = []
    for (doc_id, table_id, row_id, label, cur_key, table_type, parent,
         human_key, identity_source) in _fetch_rows(con):
        total += 1
        nl = norm(label)
        reason = skip_reason(label, nl)
        if reason:
            skipped += 1
            continue
        # A dimensional-breakdown table's own scope overrides the raw-title
        # bucket: DBS prints its geography breakdown under the title "Selected
        # income statement items", which map_table_type_norm reads as a genuine
        # income statement.
        ttn = dim_scope.get((doc_id, table_id)) or map_table_type_norm(table_type)
        key = resolve(nl, ttn)
        if key is None:
            if ttn in NO_WILDCARD_SCOPES:
                # Never LLM residue: offering a suppressed row to the LLM would
                # re-introduce by inference exactly what the scope refuses to do
                # by alias. A stale stamp from BEFORE the scope existed (or from
                # a run of the LLM step) is actively cleared -- resolving to
                # "no concept" is a result, not a no-op -- but a human decision
                # (concept_key_human / identity_source='human_anchor') is
                # deliberate and is left exactly as the human set it.
                suppressed += 1
                if (cur_key is not None and human_key is None
                        and identity_source != "human_anchor"):
                    if not dry_run:
                        cur.execute(
                            "UPDATE row_dim SET concept_key=NULL WHERE doc_id=:d "
                            "AND table_id=:t AND row_id=:r",
                            dict(d=doc_id, t=table_id, r=row_id))
                        cur.execute(
                            "INSERT INTO concept_resolution_log(doc_id,table_id,row_id,"
                            "label,norm_label,concept_key,method,confidence,ts) "
                            "VALUES (:d,:t,:r,:l,:n,NULL,'deterministic_dim_scope',1.0,:ts)",
                            dict(d=doc_id, t=table_id, r=row_id, l=label, n=nl,
                                 ts=_now_iso()))
                    unstamped += 1
                continue
            unmatched += 1
            residue.append(dict(doc_id=doc_id, table_id=table_id, row_id=row_id,
                                label=label, norm_label=nl, table_type=table_type,
                                table_type_norm=ttn, parent=parent))
            continue
        if cur_key == key:
            already += 1
            continue
        # NULL->key or a changed mapping: UPDATE + a fresh audit row.
        if not dry_run:
            cur.execute(
                "UPDATE row_dim SET concept_key=:k WHERE doc_id=:d AND table_id=:t "
                "AND row_id=:r", dict(k=key, d=doc_id, t=table_id, r=row_id))
            cur.execute(
                "INSERT INTO concept_resolution_log(doc_id,table_id,row_id,label,"
                "norm_label,concept_key,method,confidence,ts) "
                "VALUES (:d,:t,:r,:l,:n,:k,'deterministic',1.0,:ts)",
                dict(d=doc_id, t=table_id, r=row_id, l=label, n=nl, k=key, ts=_now_iso()))
        if cur_key is None:
            stamped += 1
        else:
            restamped += 1
    if not dry_run:
        con.commit()
    return dict(total=total, stamped=stamped, restamped=restamped,
                skipped_structural=skipped, unmatched=unmatched,
                already_correct=already, suppressed_dimensional=suppressed,
                unstamped_dimensional=unstamped, residue=residue)
