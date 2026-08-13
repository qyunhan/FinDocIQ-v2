"""Swapped-caption repair: the extractor sometimes puts the PAGE MASTHEAD in
`title` and the table's real caption in `label_header`.

Observed in DBS_4Q25_performance_summary's per-share exhibit. Left unrepaired it
costs the table its identity (`table_type` and `table_id` are both slugged from
the title), so the registry cannot classify it and `stamp_human_anchors` refuses
to project the exhibit's human_confirmed anchors.

BOTH signals are required. The two other corpus tables that share the masthead
shape are exactly the cases each signal alone would corrupt, and both are pinned
here as regression guards.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path
from stage2_load.load_v7 import (_label_header_has_caption,  # noqa: E402
                           _title_is_bare_masthead, repair_swapped_captions)

DBS = "DBS Group Holdings Ltd"


class _T:
    """Minimal GTable stand-in: the repair only touches these two fields."""
    def __init__(self, title, label_header):
        self.title, self.label_header = title, label_header


def test_signal_a_bare_masthead_vs_a_caption_that_merely_mentions_the_filer():
    assert _title_is_bare_masthead("DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES", DBS)
    assert _title_is_bare_masthead("DBS Group Holdings Ltd", DBS)
    assert _title_is_bare_masthead("dbs group holdings limited and subsidiaries", DBS)
    # the key_audit_matters case: mentions the filer as boilerplate but is a
    # genuinely descriptive heading -> large residue -> NOT a masthead
    assert not _title_is_bare_masthead(
        "INDEPENDENT AUDITOR'S REPORT TO THE MEMBERS OF DBS GROUP HOLDINGS LTD "
        "(continued) - Key audit matter", DBS)
    assert not _title_is_bare_masthead("Selected income statement items ($m)", DBS)
    assert not _title_is_bare_masthead("", DBS)
    # another bank's name in OUR title is not OUR masthead
    assert not _title_is_bare_masthead("DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES",
                                       "United Overseas Bank Ltd")


def test_signal_b_real_caption_vs_a_bare_unit_banner():
    assert _label_header_has_caption("Per share data ($)3,8")
    assert _label_header_has_caption("Key audit matter")
    assert _label_header_has_caption("In $ millions")
    # the DBS_2Q25 NPA case: same masthead title, but nothing to recover
    assert not _label_header_has_caption("($m)")
    assert not _label_header_has_caption("(%)")
    assert not _label_header_has_caption("($'000)")
    assert not _label_header_has_caption("")
    assert not _label_header_has_caption(None)
    assert not _label_header_has_caption("3,8")


def test_repair_swaps_only_the_table_that_needs_it():
    tables = [
        # the real defect -> repaired
        _T("DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES", "Per share data ($)3,8"),
        # masthead title but no caption to recover (DBS_2Q25 NPA) -> untouched
        _T("DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES", "($m)"),
        # descriptive heading that mentions the filer (key_audit_matters) -> untouched
        _T("INDEPENDENT AUDITOR'S REPORT TO THE MEMBERS OF DBS GROUP HOLDINGS LTD "
           "(continued) - Key audit matter", "Key audit matter"),
        # ordinary well-formed table -> untouched
        _T("Selected income statement items ($m)", ""),
    ]
    warns: list[str] = []
    assert repair_swapped_captions(tables, DBS, warns) == 1
    # repaired: the two fields are SWAPPED, so neither string is invented or lost
    assert tables[0].title == "Per share data ($)3,8"
    assert tables[0].label_header == "DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES"
    # untouched
    assert tables[1].title == "DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES"
    assert tables[1].label_header == "($m)"
    assert tables[2].label_header == "Key audit matter"
    assert tables[3].title == "Selected income statement items ($m)"
    assert len(warns) == 1 and "swapped-caption repair" in warns[0]


def test_repair_is_idempotent():
    """After the swap the title is a real caption and the label_header is the
    masthead, so signal (a) no longer fires -- a second pass is a no-op."""
    tables = [_T("DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES", "Per share data ($)3,8")]
    assert repair_swapped_captions(tables, DBS) == 1
    assert repair_swapped_captions(tables, DBS) == 0
    assert tables[0].title == "Per share data ($)3,8"


def test_repair_is_unit_neutral():
    """The table-default unit is `parse_unit(label_header) or parse_unit(title)`,
    so swapping the two must not change it."""
    from stage2_load.load_v7 import parse_unit
    title, label = "DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES", "Per share data ($)3,8"
    before = parse_unit(label) or parse_unit(title)
    after = parse_unit(title) or parse_unit(label)      # fields swapped
    assert before == after == "per_share"


if __name__ == "__main__":
    for t in (test_signal_a_bare_masthead_vs_a_caption_that_merely_mentions_the_filer,
              test_signal_b_real_caption_vs_a_bare_unit_banner,
              test_repair_swaps_only_the_table_that_needs_it,
              test_repair_is_idempotent, test_repair_is_unit_neutral):
        t()
        print(f"  [PASS] {t.__name__}")
    print("ALL PASS")
