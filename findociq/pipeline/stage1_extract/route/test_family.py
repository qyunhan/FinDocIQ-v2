"""Tests for the document-family classifier (classify/family.py). Locks the
routing-critical signal (pillar3 vs fs vs slides) + filename derivations.
General — no per-bank branch. Spec: docs/specs/2026-07-12-document-family-router-design.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.route import family as F  # noqa: E402


def test_institution_substring_incl_no_separator():
    assert F.institution_from_stem("DBS_4Q25_performance_summary") == "DBS"
    assert F.institution_from_stem("DBS4Q25_CFO_presentation") == "DBS"   # no sep
    assert F.institution_from_stem("OCBC_1Q26_Pillar3") == "OCBC"
    assert F.institution_from_stem("MAS_Notice_637") is None


def test_period_regex_incl_embedded_and_fy():
    assert F.period_from_stem("DBS_4Q25_performance_summary") == "2025-Q4"
    assert F.period_from_stem("DBS4Q25_CFO_presentation") == "2025-Q4"      # embedded
    assert F.period_from_stem("OCBC_1Q26_Pillar3") == "2026-Q1"
    assert F.period_from_stem("UOB_4Q25_Pillar 3") == "2025-Q4"            # space
    assert F.period_from_stem("Bank_FY25_report") == "2025-Q4"            # FY fallback
    assert F.period_from_stem("MAS_Notice_637") is None


def test_period_regex_hyphenated_fullyear_and_halfyear():
    # UOB naming: lowercase quarter, hyphen, 4-digit year
    assert F.period_from_stem("performance-highlights-1q-2025") == "2025-Q1"
    assert F.period_from_stem("condensed-financial-statements-2q-2025") == "2025-Q2"
    assert F.period_from_stem("regulatory-disclosures-pillar-3-disclosures-4q-2026") == "2026-Q4"
    # OCBC half-year -> period-END quarter
    assert F.period_from_stem("OCBC 1H25 Condensed Interim FS") == "2025-Q2"
    assert F.period_from_stem("OCBC 2H25 Results") == "2025-Q4"
    # OCBC Pillar 3 date naming: month + year -> calendar quarter
    assert F.period_from_stem("pillar 3 disclosures as at 30 september 2025") == "2025-Q3"
    assert F.period_from_stem("pillar 3 disclosures as at 31 march 2026") == "2026-Q1"
    assert F.period_from_stem("disclosures as at 31 december 2025") == "2025-Q4"


def test_family_pillar3_beats_everything():
    fam, conf, flags = F.detect_family("Basel III Pillar 3 Disclosures", "",
                                       595, 842, 300)
    assert fam == "pillar3" and conf == "high"


def test_family_slides_landscape_sparse():
    fam, conf, _ = F.detect_family("Q4 2025 CFO Presentation", "", 960, 540, 20)
    assert fam == "slides" and conf == "high"
    fam2, conf2, flags2 = F.detect_family("dense text " * 100, "", 960, 540, 300)
    assert fam2 == "slides" and conf2 == "low" and "weak_slide_signal" in flags2


def test_family_fs_requires_corroboration():
    fam, conf, _ = F.detect_family("Condensed Interim Financial Statements",
                                   "", 595, 842, 400)
    assert fam == "fs" and conf == "high"
    # portrait, dense, but no FS vocabulary -> 'other' (regulatory notice), not fs
    fam2, conf2, flags2 = F.detect_family("Notice of Redemption of Subordinated Notes",
                                          "", 595, 842, 400)
    assert fam2 == "other" and conf2 == "low" and "no_fs_signal" in flags2


def test_family_transcript_routed_to_other_despite_fs_vocabulary():
    # dialogue transcripts are dense with FS words ('results', 'performance')
    # in the Q&A body, but the title line marks them as non-tabular -> 'other'.
    text = ("Edited transcript of DBS first-quarter 2026 analyst call, 30 April 2026\n"
            "Welcome to the call. We delivered strong results this quarter, with "
            "performance across all segments improving.")
    fam, conf, flags = F.detect_family(text, "", 595, 842, 400)
    assert fam == "other" and conf == "high" and "transcript" in flags


def test_contents_page_general_leading_and_trailing():
    lead = "TABLE OF CONTENTS\n1 Overview\n2 Income\n3 Balance Sheet\n4 Notes"
    assert F._is_contents_page(lead)
    trail = "Contents\nOverview ....... 1\nIncome ..... 2\nNotes ..... 3\nEnd .. 4"
    assert F._is_contents_page(trail)
    assert not F._is_contents_page("Income Statement\nRevenue 100\nCosts 40")  # no label
    assert not F._is_contents_page("Contents\nOverview 1\nIncome 2")           # <4 entries


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print("ALL PASS")
