"""Tests for merge_continuation_tables — one logical table printed across a
page break, and what the merge must NOT break while rejoining it.

WHY this file exists: the merge is the only transform that appends rows from one
table onto another, and TWO downstream contracts are stated in terms of a row's
POSITION INSIDE ITS OWN TABLE. Both silently produced a wrong hierarchy instead
of failing:

  * `GRow.parent = 'hN'` is an ORDINAL into the table's header list
    (`transforms.header_row_indices`, consumed by
    `load_v7.resolve_printed_parents`) — not an id. Appending re-bases every one
    of them, and the join can PROMOTE the predecessor's last row to a header, so
    the shift is not a constant offset. Measured on UOB 2Q26: 'All-currency'
    re-parented onto 'Credit costs on loans', 'Common Equity Tier 1' onto the
    'Notes:' block, 'Basic' onto LCR — the merged 27-row table matched 12 of 27
    masterlist paths, under MIN_MATCH_FRACTION, so a correctly merged exhibit
    still went unstamped.
  * a valueless footnote block is EXCLUDED from ancestry only while nothing
    valued follows it (`masterlist_derive.classify`'s trailing-prose rule). The
    merge puts rows beneath it, so it must be excluded on its own terms — by the
    label — or it becomes a BANNER prefixing the entire continuation.

Run:  python -m pytest findociq/pipeline/stage2_load/test_continuation_merge.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path

from stage3_stamp.masterlist import masterlist_derive as D      # noqa: E402
from stage2_load.load_v7 import resolve_printed_parents            # noqa: E402
from stage1_extract.chunk.schema import GCell, GColumn, GRow, GTable        # noqa: E402
from stage1_extract.chunk.transforms import (header_row_indices,            # noqa: E402
                              merge_continuation_tables)

PASS, FAIL = "  PASS  ", "  FAIL  "
_results: list[bool] = []


def check(name, got, want):
    ok = got == want
    _results.append(ok)
    print(f"{PASS if ok else FAIL}{name}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")
    assert ok, name


def _cols(*leaves):
    return [GColumn(group=None, leaf=leaf) for leaf in leaves]


def _row(label, level, *, row_type="data", parent=None, n_vals=0, row_id=None):
    return GRow(row_id=row_id, row_type=row_type, level=level, parent=parent,
                label=label,
                values=[GCell(value="1.0") for _ in range(n_vals)])


def _uob_p5():
    """The p5 half after `split_caption_tables` — 5 columns, one sub-header
    block, and a footnote block as its LAST row."""
    return GTable(
        title="Key financial ratios (%)",
        columns=_cols("1H26", "1H25", "+/(-)\n%", "2H25", "+/(-)\n%"),
        rows=[
            _row("Net interest margin", 1, n_vals=5),
            _row("Cost/Income ratio", 1, n_vals=5),
            _row("Credit costs on loans (bp)", 1, row_type="sub_header", row_id="h1"),
            _row("General", 2, parent="h1", n_vals=5),
            _row("Total", 2, parent="h1", row_type="total", n_vals=5),
            _row("NPL ratio", 1, n_vals=5),
            _row("Notes:\n1 Relates to amount attributable to equity holders.",
                 0, row_type="note"),
        ])


def _uob_p6():
    """The p6 carry-over: continuation caption, only the three PERIOD columns
    reprinted, and its own hN ordinals restarting at 1."""
    return GTable(
        title="Financial Highlights (cont'd)",
        continued_from_previous=True,
        columns=_cols("1H26", "1H25", "2H25"),
        rows=[
            _row("Key financial ratios (%) (cont'd)", 0, row_type="section_header"),
            _row("Loan/Deposit ratio", 1, n_vals=3),
            _row("Liquidity coverage ratios (\"LCR\")", 1, row_type="sub_header"),
            _row("All-currency", 2, parent="h1", n_vals=3),
            _row("Net stable funding ratio (\"NSFR\")", 1, n_vals=3),
            _row("Capital adequacy ratios", 1, row_type="sub_header"),
            _row("Common Equity Tier 1", 2, parent="h2", n_vals=3),
        ])


print("\nmerge_continuation_tables — hN ordinals are REBASED onto the merged table")
merged, groups = merge_continuation_tables([_uob_p5(), _uob_p6()])
check("the two halves become one table", (len(merged), groups), (1, [[0, 1]]))
t = merged[0]
check("the repeated caption row is dropped, every other row kept",
      [r.label for r in t.rows][-7:],
      ["Notes:\n1 Relates to amount attributable to equity holders.",
       "Loan/Deposit ratio", 'Liquidity coverage ratios ("LCR")', "All-currency",
       'Net stable funding ratio ("NSFR")', "Capital adequacy ratios",
       "Common Equity Tier 1"])

# The join PROMOTES 'Notes:' to a header (level 0 followed by a level-1 row), so
# the continuation's own h1/h2 shift by TWO, not by the one header p5 printed.
check("'Notes:' is promoted to a header by the join",
      [t.rows[i].label.split(":")[0] for i in header_row_indices(t.rows)],
      ["Credit costs on loans (bp)", "Notes", 'Liquidity coverage ratios ("LCR")',
       "Capital adequacy ratios"])
check("the continuation's h1/h2 are rewritten, not left as printed",
      [(r.label, r.parent) for r in t.rows if r.parent],
      [("General", "h1"), ("Total", "h1"),
       ("All-currency", "h3"), ("Common Equity Tier 1", "h4")])

printed = resolve_printed_parents(t.rows)
check("every printed parent resolves to the row the FILING printed it under",
      {t.rows[c].label: t.rows[p].label for c, p in printed.items()},
      {"General": "Credit costs on loans (bp)",
       "Total": "Credit costs on loans (bp)",
       "All-currency": 'Liquidity coverage ratios ("LCR")',
       "Common Equity Tier 1": "Capital adequacy ratios"})

print("\nmerge_continuation_tables — the column subset is re-laid by POSITION")
check("p6's 3 columns land on p5's period columns, variance columns empty",
      [(c.value, c.cell_state) for c in
       next(r for r in t.rows if r.label == "All-currency").values],
      [("1.0", "reported"), ("1.0", "reported"), ("", "empty"),
       ("1.0", "reported"), ("", "empty")])

print("\nmerge_continuation_tables — what must NOT merge")
bare = _uob_p6()
bare.rows[0] = _row("Key financial ratios (%)", 0, row_type="section_header")
check("a repeated caption with NO continuation marker is a separate table",
      len(merge_continuation_tables([_uob_p5(), bare])[0]), 2)

other = _uob_p6()
other.columns = _cols("FY26", "FY25", "FY24")
check("a marked caption whose columns do not match is a separate table",
      len(merge_continuation_tables([_uob_p5(), other])[0]), 2)

check("a table that survives alone has its continuation flag cleared",
      [x.continued_from_previous
       for x in merge_continuation_tables([_uob_p5(), other])[0]],
      [False, False])

print("\nclassify — a footnote BLOCK is excluded by its label, not by position")
# Exactly the shape the merge creates: the block is no longer the last row.
rows = [D.Row(row_id=1, label="Notes:\n1 Relates to amount attributable to "
                             "equity holders of the Bank.", level=0,
              parent=None, has_values=False),
        D.Row(row_id=2, label="Loan/Deposit ratio 3", level=1, parent=None,
              has_values=True)]
D.classify(rows, None)
check("'Notes: …' mid-table is EXCLUDED, not a BANNER",
      [r.cls for r in rows], [D.EXCLUDED, D.DATA])
D.build_ancestry(rows)
check("so the continuation's rows inherit no footnote ancestor",
      rows[1].ancestor_labels_raw, [])

valued = [D.Row(row_id=1, label="Notes and coins", level=1, parent=None,
                has_values=True)]
D.classify(valued, None)
check("a VALUED line item starting with 'Notes' is untouched",
      valued[0].cls, D.DATA)

print(f"\n{sum(_results)}/{len(_results)} checks passed")
assert all(_results)
