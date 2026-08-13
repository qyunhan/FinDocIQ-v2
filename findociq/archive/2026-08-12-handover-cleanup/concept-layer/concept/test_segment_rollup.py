"""Segment roll-up: recover a parent segment slice by summing a declared,
exhaustive partition of member segments — FALLBACK ONLY.

Structurally unlike every `formula:` in the dictionary, which combines DIFFERENT
concepts at the SAME grain. This sums the SAME concept ACROSS the segment axis,
so it is a separate pass rather than a formula-string extension.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.compute_ratios import _load_partitions, segment_rollup  # noqa: E402

_PART = [{"bank": "DBS", "parent": "SEG_TOTAL",
          "members": ["SEG_COMMERCIAL", "SEG_MARKETS"]}]
_UNITS = {"pnl.nii.net": "currency", "ratio.nim": "percent",
          "pnl.eps.basic": "per_share"}

_DDL = """
CREATE TABLE fact_metric (
  institution TEXT, concept_key TEXT, concept_name TEXT, thesis TEXT,
  period TEXT, period_span TEXT, period_start TEXT, segment_key TEXT,
  geo_key TEXT, industry_key TEXT, legal_entity TEXT, value_num REAL,
  unit TEXT, unit_source TEXT, source_doc_id TEXT, source_table_id TEXT,
  source_row_label TEXT, n_candidates INTEGER, resolved_by TEXT);
"""


def _db(rows):
    con = sqlite3.connect(":memory:")
    con.executescript(_DDL)
    for r in rows:
        d = {"institution": "DBS Group Holdings Ltd", "concept_key": "pnl.nii.net",
             "concept_name": "NII", "thesis": "", "period": "2025-12-31",
             "period_span": "FY", "period_start": None, "geo_key": "GLOBAL",
             "industry_key": "IND_TOTAL", "legal_entity": "CONSOLIDATED",
             "unit": "S$m", "unit_source": "as_loaded", "source_doc_id": "d",
             "source_table_id": "t", "source_row_label": "l", "n_candidates": 1,
             "resolved_by": "single"}
        d.update(r)
        con.execute("INSERT INTO fact_metric ({}) VALUES ({})".format(
            ",".join(d), ",".join(f":{k}" for k in d)), d)
    con.commit()
    return con


def _total(con, concept="pnl.nii.net"):
    return con.execute("SELECT value_num, resolved_by FROM fact_metric "
                       "WHERE segment_key='SEG_TOTAL' AND concept_key=?",
                       (concept,)).fetchone()


def test_fires_when_the_parent_slot_is_empty():
    """The case the mechanism exists for: both members present, no group row."""
    con = _db([{"segment_key": "SEG_COMMERCIAL", "value_num": 14494.0},
               {"segment_key": "SEG_MARKETS", "value_num": 6.0}])
    rep = segment_rollup(con, _PART, _UNITS)
    assert rep["written"] == 1, rep
    assert _total(con) == (14500.0, "segment_rollup")
    con.close()


def test_does_not_fire_when_a_direct_value_exists():
    """DBS today: the statutory row already resolved via the tier system, so the
    roll-up must leave 14,500/'prefer_table' exactly as it found it."""
    con = _db([{"segment_key": "SEG_COMMERCIAL", "value_num": 14494.0},
               {"segment_key": "SEG_MARKETS", "value_num": 6.0},
               {"segment_key": "SEG_TOTAL", "value_num": 14500.0,
                "resolved_by": "prefer_table"}])
    rep = segment_rollup(con, _PART, _UNITS)
    assert rep["written"] == 0 and rep["skipped_existing"] == 1, rep
    assert _total(con) == (14500.0, "prefer_table"), "a direct value must win"
    con.close()


def test_does_not_fire_on_a_partial_partition():
    """One member missing = not an exhaustive partition; summing would understate
    the parent. Must produce nothing, not a wrong total."""
    con = _db([{"segment_key": "SEG_COMMERCIAL", "value_num": 14494.0}])
    rep = segment_rollup(con, _PART, _UNITS)
    assert rep["written"] == 0 and rep["incomplete"] == 1, rep
    assert _total(con) is None
    con.close()


def test_does_not_sum_non_additive_concepts():
    """A ratio or a per-share amount is intensive — it does not add across
    business units. Keyed off the declared unit KIND, so this holds for any
    future ratio without naming it."""
    for concept in ("ratio.nim", "pnl.eps.basic"):
        con = _db([{"segment_key": "SEG_COMMERCIAL", "value_num": 2.5, "concept_key": concept},
                   {"segment_key": "SEG_MARKETS", "value_num": 1.5, "concept_key": concept}])
        rep = segment_rollup(con, _PART, _UNITS)
        assert rep["written"] == 0 and rep["skipped_non_additive"] == 1, (concept, rep)
        assert _total(con, concept) is None, f"{concept} must not be summed"
        con.close()


def test_members_that_disagree_on_grain_are_never_summed():
    """Only segment_key may differ. A member at another period / span / geo /
    industry / legal_entity / unit belongs to a different grain and must not be
    added in — checked one axis at a time."""
    for axis, other in (("period", "2024-12-31"), ("period_span", "2H"),
                        ("geo_key", "SG"), ("industry_key", "IND_MFG"),
                        ("legal_entity", "BANK_SOLO"), ("unit", "US$m")):
        con = _db([{"segment_key": "SEG_COMMERCIAL", "value_num": 14494.0},
                   {"segment_key": "SEG_MARKETS", "value_num": 6.0, axis: other}])
        rep = segment_rollup(con, _PART, _UNITS)
        assert rep["written"] == 0, f"{axis} mismatch must not sum: {rep}"
        assert _total(con) is None, axis
        con.close()


def test_each_grain_is_rolled_up_independently():
    con = _db([{"segment_key": "SEG_COMMERCIAL", "value_num": 14494.0, "period_span": "FY"},
               {"segment_key": "SEG_MARKETS", "value_num": 6.0, "period_span": "FY"},
               {"segment_key": "SEG_COMMERCIAL", "value_num": 7150.0, "period_span": "2H"},
               {"segment_key": "SEG_MARKETS", "value_num": 21.0, "period_span": "2H"}])
    assert segment_rollup(con, _PART, _UNITS)["written"] == 2
    got = dict(con.execute("SELECT period_span, value_num FROM fact_metric "
                           "WHERE resolved_by='segment_rollup'").fetchall())
    assert got == {"FY": 14500.0, "2H": 7171.0}, got
    con.close()


def test_declared_partition_is_reported_when_its_members_never_appear():
    """A declaration that matches nothing is a silent no-op otherwise — surface
    it so a typo'd segment key is visible instead of just doing nothing."""
    con = _db([{"segment_key": "SEG_TOTAL", "value_num": 1.0}])
    rep = segment_rollup(con, [{"bank": "DBS", "parent": "SEG_TOTAL",
                                "members": ["SEG_TYPO_A", "SEG_TYPO_B"]}], _UNITS)
    assert rep["written"] == 0 and rep["unknown_members"], rep
    con.close()


def test_shipped_declaration_parses_and_is_well_formed():
    parts = _load_partitions()
    assert parts, "concept_dictionary.yaml must declare segment_partitions"
    for p in parts:
        assert {"bank", "parent", "members"} <= set(p)
        assert len(p["members"]) >= 2, "a partition needs >= 2 members"
        assert p["parent"] not in p["members"], "parent cannot be its own member"
        assert len(set(p["members"])) == len(p["members"]), "duplicate member"


if __name__ == "__main__":
    for t in (test_fires_when_the_parent_slot_is_empty,
              test_does_not_fire_when_a_direct_value_exists,
              test_does_not_fire_on_a_partial_partition,
              test_does_not_sum_non_additive_concepts,
              test_members_that_disagree_on_grain_are_never_summed,
              test_each_grain_is_rolled_up_independently,
              test_declared_partition_is_reported_when_its_members_never_appear,
              test_shipped_declaration_parses_and_is_well_formed):
        t()
        print(f"  [PASS] {t.__name__}")
    print("ALL PASS")
