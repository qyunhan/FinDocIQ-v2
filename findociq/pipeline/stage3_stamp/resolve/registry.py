"""stage3_stamp.resolve.registry — resolve a table to a registry-assigned `table_type_id`.

Why a THREE-LEVEL lookup and not just the title: `table_id` is built as
`<section>_<table_title>_<period>`, and the two banks put the exhibit's identity
in different halves.

  DBS   section='Overview'                    title='Selected income statement items ($m)'
        -> the TITLE is the identity; the section is a page grouping.

  OCBC  section='Performance Ratios'          title='FINANCIAL HIGHLIGHTS (continued)'
        -> the SECTION is the identity; the title is the PAGE HEADER. OCBC's
           4Q25 media release has TWELVE tables titled 'FINANCIAL HIGHLIGHTS',
           each 22-33 rows, several repeating the same ratio rows. Keying on the
           title alone would collapse them into one ambiguous exhibit.

So we try, most specific first:

  1. `<section_norm>__<title_norm>`   composite — pins an exact pair
  2. `<section_norm>`                 OCBC-shaped documents
  3. `<title_norm>`                   DBS-shaped documents

SECTION IS TRIED BEFORE TITLE, deliberately. In every observed ambiguity the
section is the more specific of the two (OCBC's `Performance Ratios` vs the
page header `FINANCIAL HIGHLIGHTS`), and in the DBS-shaped case the section is a
bland page grouping (`Overview`) that is simply left unseeded and falls through
to the title. Trying the title first would let the generic page header win over
the precise section.

and within each level, a bank-specific alias beats the `'*'` alias. The first
hit wins; a total miss is UNCLASSIFIED and goes to the review queue. There is no
fuzzy match and no wildcard concept fallback — a near miss is a flag, never a
guess (MAPPING_LAYER §1.5).

An ambiguous level is simply left unseeded: `financial_highlights` is
deliberately NOT a title-level alias, so OCBC's highlights tables fall through
to their section aliases (`performance_ratios`, `earnings_per_share`, …).
"""
from __future__ import annotations

import sqlite3

from stage3_stamp.resolve.normalize import normalize_exhibit_title, safe_clean

COMPOSITE_SEP = "__"


def exhibit_aliases(section_title: str | None, table_title: str | None) -> list[str]:
    """Candidate `alias_norm` values, MOST SPECIFIC FIRST. May be shorter than 3
    when a part is empty or the two parts normalize identically (DBS 1Q26, where
    section and title are the same string)."""
    sec = normalize_exhibit_title(section_title)
    tit = normalize_exhibit_title(table_title)
    out: list[str] = []
    if sec and tit and sec != tit:
        out.append(f"{sec}{COMPOSITE_SEP}{tit}")
    if sec:
        out.append(sec)
    if tit and tit != sec:
        out.append(tit)
    return out


def resolve_table_type(con: sqlite3.Connection, bank: str,
                       section_title: str | None,
                       table_title: str | None) -> tuple[str | None, str | None]:
    """-> (table_type_id, matched_alias). (None, None) => UNCLASSIFIED.

    Bank-specific alias beats '*' at the SAME specificity level; a more specific
    level always beats a less specific one.
    """
    for alias in exhibit_aliases(section_title, table_title):
        row = con.execute(
            "SELECT table_type_id FROM table_registry_alias "
            "WHERE alias_norm = ? AND bank = ?", (alias, bank)).fetchone()
        if row:
            return row[0], alias
        row = con.execute(
            "SELECT table_type_id FROM table_registry_alias "
            "WHERE alias_norm = ? AND bank = '*'", (alias,)).fetchone()
        if row:
            return row[0], alias
    return None, None


BANK_OF_INSTITUTION = {
    "DBS Group Holdings Ltd": "DBS",
    "United Overseas Bank Ltd": "UOB",
    "Oversea-Chinese Banking Corporation Ltd": "OCBC",
}


def bank_of(institution: str | None) -> str:
    """Institution long name -> short bank code used as the map's anchor."""
    return BANK_OF_INSTITUTION.get(institution or "", (institution or "?")[:8])


def classify_corpus(con: sqlite3.Connection) -> dict:
    """Resolve every table in the DB, write `table_t.table_type_id`, and return
    match statistics. Idempotent: re-running overwrites the pointer only, never
    `table_t.table_type` (as-reported is preserved)."""
    rows = con.execute("""
        SELECT t.doc_id, t.table_id, d.institution, s.section_title, t.table_title,
               t.table_title_clean
        FROM table_t t
        JOIN document d ON d.doc_id = t.doc_id
        LEFT JOIN section s ON s.doc_id = t.doc_id AND s.section_id = t.section_id
    """).fetchall()

    stats = {"total": len(rows), "matched": 0, "unclassified": 0,
             "by_level": {"composite": 0, "title": 0, "section": 0},
             "by_type": {}, "unclassified_aliases": {}}

    for doc_id, table_id, institution, sec_title, title, title_clean in rows:
        bank = bank_of(institution)
        # prefer the typography-clean title when the geometry stage produced one
        use_title = safe_clean(title, title_clean)
        ttid, alias = resolve_table_type(con, bank, sec_title, use_title)
        con.execute("UPDATE table_t SET table_type_id = ? WHERE doc_id = ? AND table_id = ?",
                    (ttid, doc_id, table_id))
        if ttid:
            stats["matched"] += 1
            stats["by_type"][ttid] = stats["by_type"].get(ttid, 0) + 1
            level = ("composite" if COMPOSITE_SEP in alias
                     else "section" if alias == normalize_exhibit_title(sec_title)
                     else "title")
            stats["by_level"][level] += 1
        else:
            stats["unclassified"] += 1
            key = " | ".join(exhibit_aliases(sec_title, use_title)) or "(empty title)"
            stats["unclassified_aliases"][key] = stats["unclassified_aliases"].get(key, 0) + 1
    con.commit()
    return stats
