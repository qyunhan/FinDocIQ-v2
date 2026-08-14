"""`col_dim.col_role` — which columns are NOT measurements.

Masterlist-INDEPENDENT, so it applies to every table of every document, and it
is the gate every serving query relies on: the app's anchor query allowlists
`col_role IS NULL`, so anything named here is excluded from the fact grain by
construction rather than by a downstream filter.

Two roles today:

  derived_skip     a column that RESTATES other columns ('% chg', '+/(-)%').
                   Ingesting it as a period fact would double-count.

  reference_skip   a column that carries a CROSS-REFERENCE rather than a
                   measurement — the 'Note' column an income statement prints
                   beside its figures, pointing at the note that explains them.

WHY reference_skip HAD TO EXIST (2026-08-14). OCBC's consolidated income
statement prints `Note | 1H 2026 | 1H 2025`. The Note column has no period of
its own, so the loader's period cascade fell through to the table title and
stamped its cells `2026-06-30 / 1H` — the SAME (leaf, period, span) address as
the real figure beside it. With `canonical_col_id` unpopulated, that address is
the finest grain the dashboard can match on, so the two became
indistinguishable and the tie-break picked the footnote index: net interest
income rendered as **3** instead of 4,486, fee income as **4** instead of 1,414.
Ten anchored addresses across two documents were affected.

WHY THE PREDICATE IS NOT "col_period IS NULL". That was the tempting rule and it
is wrong: a hard-axis table's value columns legitimately have no period of their
own — UOB's 'Performance by Geographical Segment' prints `Singapore | Malaysia |
...` as banners and takes its period from the title, which is exactly why the
cascade exists. Periodlessness cannot separate a reference column from a
geography column. What separates them is what the column IS, which is what this
module encodes.

WHY A LABEL REGEX IS THE RIGHT SHAPE. Same shape as `derived_skip`, which has
always been a label rule, and for the same reason: the role is a property of the
printed header, readable without the masterlist and identical for a bank we have
never seen. It is NOT a per-document conditional — no doc_id, no bank, no
table_type_id appears here.

Pure — no DB, no Streamlit. Both callers drive these helpers rather than
re-deriving them: `stage2_load/load_v7.py::_stamp_identity` stage 1 at load
time, and `stage3_stamp/apply/restamp_columns.py` for an already-built DB.
"""
from __future__ import annotations

import re

# A column that restates others. Unchanged from load_v7, where it lived as
# _DERIVED_COL_RX; moved here so the loader and the restamp tool share one
# definition instead of drifting.
DERIVED_COL_RX = re.compile(
    r"(\+\s*/\s*\(?-\)?|%\s*chg|\bchg\b|\bchange\b|\bvariance\b|\bvs\b)", re.I)

# A column of cross-references. Anchored and whole-label ON PURPOSE: `\bnote\b`
# unanchored would also claim a real value column captioned 'Notes receivable'
# or 'Notes and coins', both of which are line items a bank genuinely reports.
# The reference column is only ever the bare word plus optional punctuation or
# a footnote marker ('Note', 'Notes', 'Note (a)', 'Note 1', 'Ref.').
REFERENCE_COL_RX = re.compile(
    r"^[\s(\[]*(?:note|notes|ref|refer|reference)s?"      # the keyword
    r"(?:\s*(?:no\.?|number|\#))?"                        # 'Note no.'
    r"(?:\s*\(?[\w]{1,3}\)?)?"                            # 'Note 1', 'Note (a)'
    r"[\s)\].:,*†‡]*$", re.I)


def role_for(col_leaf_label) -> str | None:
    """The `col_role` a column's printed label earns, or None for a value column.

    `derived_skip` is tested FIRST: a header like '% chg vs note' is a
    restatement before it is anything else, and the derived rule is the older,
    load-bearing one. Order is fixed here so both callers agree.
    """
    lab = "" if col_leaf_label is None else str(col_leaf_label)
    if not lab.strip():
        return None
    if DERIVED_COL_RX.search(lab):
        return "derived_skip"
    if REFERENCE_COL_RX.match(lab.strip()):
        return "reference_skip"
    return None
