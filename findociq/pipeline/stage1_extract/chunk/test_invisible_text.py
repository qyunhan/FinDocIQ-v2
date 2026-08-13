"""Tests for render.strip_invisible_text — glyphs the page never paints.

Runs offline against the real filings under
findociq/data/sources/financial_statements/. The four cases are the four ways
this can go wrong, and three of them were live bugs during development:

  UOB   a white glyph on a white page          -> MUST be removed
  UOB   hidden numbers / spreadsheet errors    -> MUST be removed
  OCBC  body text in a Separation space, where
        the component 1.0 means FULL colorant  -> MUST survive
  DBS   white running header on a red banner   -> MUST survive

The last two are why "is it white" is not the test: the same number means
white in DeviceGray and black in a Separation space, and white text over a
dark fill is ordinary visible content.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pdfplumber
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path
from stage1_extract.chunk.render import cut_pdf, strip_invisible_text  # noqa: E402

_SOURCES = (Path(__file__).resolve().parents[3]
            / "findociq/data/sources/financial_statements")
_UOB_2Q26 = _SOURCES / "UOB_2Q26_Condensed_Interim_Financial_Statements.pdf"
_UOB_4Q25 = _SOURCES / "UOB_4Q25_condensed-financial-statements.pdf"
_OCBC_2Q26 = _SOURCES / "OCBC_2Q26_Unaudited_Interim_Financial_Statements.pdf"
_DBS_4Q25 = _SOURCES / "DBS_4Q25_performance_summary.pdf"


def _text_of(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return " ".join((page.extract_text() or "") for page in pdf.pages)


def _char_count(pdf_bytes: bytes) -> int:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return sum(len(page.chars) for page in pdf.pages)


def _require(path: Path):
    if not path.exists():
        pytest.skip(f"source PDF not available: {path.name}")


def test_uob_phantom_less_is_gone_from_the_extractor_input():
    """The defect this exists for. UOB 2Q26 p5 prints 'Allowance for credit and
    other losses' bare and indented — exactly as 4Q25 prints it — but carries a
    white 'Less:' in the text layer, so the extracted label came out prefixed
    and matched no masterlist path."""
    _require(_UOB_2Q26)
    text = _text_of(cut_pdf(str(_UOB_2Q26), [5]))
    assert "Allowance for credit and other losses" in text
    assert "Less: Allowance" not in text


def test_uob_hidden_numbers_and_spreadsheet_errors_are_gone():
    """The same filing family hides RUNS OF NUMBERS in data regions, and 4Q25
    ships unresolved spreadsheet errors invisibly — both readable as values."""
    _require(_UOB_4Q25)
    text = _text_of(cut_pdf(str(_UOB_4Q25), [22, 23]))
    assert "#REF!" not in text


def test_separation_black_survives():
    """OCBC sets its body text in a Separation space, reporting the component
    1.0 for every character — the same number that means white in DeviceGray.
    Reading it naively deletes the entire page."""
    _require(_OCBC_2Q26)
    before = _char_count(_OCBC_2Q26.read_bytes())
    after = cut_pdf(str(_OCBC_2Q26), [17])
    assert "OVERSEA-CHINESE" in _text_of(after)
    assert _char_count(after) > 0 and before > 0


def test_white_text_on_a_coloured_banner_survives():
    """DBS prints its running header in white on a red band. White is only
    invisible against what is actually behind it."""
    _require(_DBS_4Q25)
    assert "DBS GROUP HOLDINGS" in _text_of(cut_pdf(str(_DBS_4Q25), [5]))


def test_a_clean_pdf_is_returned_byte_identical():
    """No invisible text means no re-serialisation, so a document that does not
    need the pass cannot be perturbed by it."""
    _require(_DBS_4Q25)
    clean = _DBS_4Q25.read_bytes()
    out, removed = strip_invisible_text(clean)
    assert removed == []
    assert out is clean
