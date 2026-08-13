"""One physical table belongs to exactly ONE table_type_id.

`locate_tables` scores every masterlist entry against every table independently,
so two entries can both clear the bar on the same table; `stamp_tables` then
stamps both and the later one wins, leaving rows carrying a leaf id from an
exhibit they are not part of. `_one_table_one_type` is the pass that resolves
that, and these tests pin the three behaviours it has to get right.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path

from stage3_stamp.resolve import resolve_canonical_leaf as RCL  # noqa: E402


def _con(sections):
    """A DB with just the two tables the pass reads."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE table_t (doc_id TEXT, table_id TEXT, section_id TEXT)")
    con.execute("CREATE TABLE section (doc_id TEXT, section_id TEXT, section_title TEXT)")
    for tid, sec in sections.items():
        con.execute("INSERT INTO table_t VALUES ('D', ?, ?)", (tid, sec))
        con.execute("INSERT INTO section VALUES ('D', ?, ?)", (sec, sec))
    return con


def _hit(tid, matched, n_leaves):
    return dict(doc_id="D", table_id=tid, title="", page="1", period=None,
                span=None, section_id="", discriminator=None,
                matched=matched, n_rows=40, n_leaves=n_leaves)


def test_fraction_decides_not_the_raw_count():
    """The count favours whichever entry declares MORE leaves, which is how the
    geography entry (18/22) out-scored the segment entry (9/9) on DBS's
    business-segments table. Fraction asks how much of what the exhibit IS was
    found."""
    # The geography entry also owns its OWN table, so the starvation floor does
    # not fire and the decider is tested in isolation.
    con = _con({"segments_table": "performance_by_business_segments",
                "geography_table": "performance_by_geography"})
    master = {("DBS", "FS_PERF_BY_SEGMENT"): {"sections": set()},
              ("DBS", "FS_PERF_BY_GEOGRAPHY"): {"sections": set()}}
    hits = {("DBS", "FS_PERF_BY_SEGMENT"): [_hit("segments_table", 9, 9)],
            ("DBS", "FS_PERF_BY_GEOGRAPHY"): [_hit("geography_table", 22, 22),
                                              _hit("segments_table", 18, 22)]}
    out = RCL._one_table_one_type(con, hits, master)
    owners = {h["table_id"]: k[1] for k, v in out.items() for h in v}
    assert owners["segments_table"] == "FS_PERF_BY_SEGMENT", owners
    assert owners["geography_table"] == "FS_PERF_BY_GEOGRAPHY", owners


def test_section_bar_collapses_audited_and_unaudited():
    """DBS's masterlist is authored off the AUDITED statement of changes in
    equity and must still claim the UNAUDITED half-year one — same exhibit, a
    different reporting date. That is why the bar normalises with `norm_family`
    (which drops audited/unaudited/condensed/interim) and not
    `normalize_segment`."""
    con = _con({"unaudited_soce": "unaudited_consolidated_statement_of_changes_in_equity",
                "cashflow_table": "cash_flow_statement"})
    master = {("DBS", "FS_EQUITY_CHANGES_GROUP"):
              {"sections": {"audited_consolidated_statement_of_changes_in_equity"}},
              ("DBS", "FS_CASHFLOW"): {"sections": {"cash_flow_statement"}}}
    hits = {("DBS", "FS_EQUITY_CHANGES_GROUP"): [_hit("unaudited_soce", 7, 10)],
            ("DBS", "FS_CASHFLOW"): [_hit("cashflow_table", 10, 10),
                                     _hit("unaudited_soce", 9, 10)]}
    out = RCL._one_table_one_type(con, hits, master)
    owners = {h["table_id"]: k[1] for k, v in out.items() for h in v}
    # cash flow scores HIGHER on the equity table (9/10 vs 7/10) but is barred:
    # it does not declare that section. The audited/unaudited difference must
    # NOT bar the equity entry from its own exhibit.
    assert owners["unaudited_soce"] == "FS_EQUITY_CHANGES_GROUP", owners
    assert owners["cashflow_table"] == "FS_CASHFLOW", owners


def test_an_entry_is_never_starved_of_every_table():
    """Losing every claim is a worse failure than the cross-claim this pass
    fixes — the type stops existing as far as stamping is concerned. Measured on
    the real corpus: strict assignment left six OCBC entries with nothing,
    including FS_INCOME_SELECTED and FS_RATIOS_KEY, both addressed by the
    highlights dashboard."""
    con = _con({"one_table": "financial_highlights"})
    master = {("OCBC", "FS_BALANCE_SELECTED"): {"sections": {"financial_highlights"}},
              ("OCBC", "FS_INCOME_SELECTED"): {"sections": {"financial_highlights"}}}
    hits = {("OCBC", "FS_BALANCE_SELECTED"): [_hit("one_table", 6, 6)],
            ("OCBC", "FS_INCOME_SELECTED"): [_hit("one_table", 13, 14)]}
    out = RCL._one_table_one_type(con, hits, master)
    assert set(out) == set(hits), "neither entry may be left with no table"


def test_uncontested_tables_are_untouched():
    con = _con({"a": "sec_a", "b": "sec_b"})
    master = {("DBS", "FS_A"): {"sections": set()}, ("DBS", "FS_B"): {"sections": set()}}
    hits = {("DBS", "FS_A"): [_hit("a", 5, 5)], ("DBS", "FS_B"): [_hit("b", 5, 5)]}
    assert RCL._one_table_one_type(con, hits, master) == hits
