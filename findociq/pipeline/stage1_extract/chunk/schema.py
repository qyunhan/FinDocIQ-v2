"""stage1_extract.chunk.schema — all constants, pydantic models, RunPaths, and shared mutable state."""
from __future__ import annotations
import threading
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
MODEL          = "gemini-3.5-flash"
TOC_PATH       = "out/step1_toc.json"
OUT_XLSX       = "out/sections.xlsx"
AUDIT_DIR      = "out/audit"
USAGE_LOG_PATH = ""    # set in main() via RunPaths — never write to this default
COST_LOG_PATH  = ""    # set in main() via RunPaths
INDEX_PATH     = "out/sections_index.json"

# ---------------------------------------------------------------------------
# Output layout
# DELIVERABLE/outputs/{family}/
#   {bank}_{period}/
#     {bank}_{period}_{family}.xlsx
#     toc.json
#     audit/{unit_id}/
#     logs/cost_summary.json  api_log.xlsx
#   _ledgers/{bank}_api_usage.jsonl   (cross-period, append-only)
#   _archive/
#
# `family` is the doc-family router's decision (classify/family.py), passed in
# via --family (see PASS2_v2.py) or self-classified when run standalone.
# pillar3 is the ONLY family with byte-identical legacy behaviour — it always
# resolves through _P3_ROOT (below) so the pre-existing --out-root override
# keeps working unchanged. Every other known family gets its own root under
# _OUTPUTS_ROOT; unknown/absent families fall back to pillar3 paths+label.
# ---------------------------------------------------------------------------
_OUTPUTS_ROOT = Path(__file__).resolve().parents[3] / "outputs"   # findociq/outputs
_P3_ROOT = _OUTPUTS_ROOT / "pillar3"

# Single family -> document-title map (Design item 2). Unknown family falls
# back to the pillar3 entry AND resolve_family() prints a visible note.
FAMILY_TITLES = {
    "pillar3": "Pillar 3 Disclosures",
    "fs":      "Financial Statements",
}
_DEFAULT_FAMILY = "pillar3"


def resolve_family(family: str | None) -> tuple[str, str, bool]:
    """(effective_family, doc_title, known) for a raw router/CLI family value.
    Unknown or absent family -> pillar3 layout/label, plus a printed note —
    never a silent default."""
    if family in FAMILY_TITLES:
        return family, FAMILY_TITLES[family], True
    print(f"   ⚠️  unknown/unset family {family!r} — falling back to "
          f"{_DEFAULT_FAMILY} output layout and labelling")
    return _DEFAULT_FAMILY, FAMILY_TITLES[_DEFAULT_FAMILY], False


def _family_root(family: str) -> Path:
    """Root dir for an already-resolved family (must be a FAMILY_TITLES key)."""
    if family == "pillar3":
        return _P3_ROOT           # legacy root; also the --out-root target
    return _OUTPUTS_ROOT / family


class RunPaths:
    """Single source of truth for all per-run output paths."""
    def __init__(self, bank: str, period: str, doc_stem: str, family: str = _DEFAULT_FAMILY):
        self.bank   = bank
        self.period = period
        self.family, _title, _known = resolve_family(family)
        root = _family_root(self.family)
        self.run_dir    = root / f"{bank}_{period}"
        # xlsx/index are per-DOC (keyed by doc_stem), not just bank+period —
        # multiple docs share a quarter (fs + pillar3, or several fs variants),
        # and a bank+period-only workbook name made the 2nd+ doc collide and
        # abort at the "workbook was created from a different PDF" guard.
        self.xlsx       = self.run_dir / f"{doc_stem}_{self.family}.xlsx"
        self.toc        = self.run_dir / "toc.json"
        self.index      = self.run_dir / f"{doc_stem}_{self.family}.index.json"
        self.logs_dir   = self.run_dir / "logs"
        self.cost       = self.logs_dir / "cost_summary.json"
        self.api_log    = self.logs_dir / "api_log.xlsx"
        self.audit_dir  = self.run_dir / "audit" / doc_stem
        self.ledger     = root / "_ledgers" / f"{bank}_api_usage.jsonl"

    def makedirs(self):
        for d in (self.run_dir, self.logs_dir, self.audit_dir,
                  self.ledger.parent):
            d.mkdir(parents=True, exist_ok=True)

IMAGE_SCALE          = 2.0
ENABLE_BOUNDARY_CROP = False   # set True only for targeted testing; off by default
ENABLE_REGION_OWNERSHIP = True  # drop tables whose anchor-y falls in another section's region

# --- Per-bank identity + brand colour (auto-detected; override with --bank) ---
BANKS = {
    "DBS":  {"institution": "DBS Group Holdings Ltd",
             "brand": "CC0000", "match": r"\bDBS\b"},
    "OCBC": {"institution": "Oversea-Chinese Banking Corporation Limited",
             "brand": "CC0000", "match": r"OCBC|Oversea[- ]?Chinese"},
    "UOB":  {"institution": "United Overseas Bank Limited",
             "brand": "1B6EC2", "match": r"\bUOB\b|United Overseas"},
}

# --- Document metadata + brand styling (set per-bank at runtime in main) -------
INSTITUTION  = "DBS Group Holdings Ltd"
DOC_TITLE    = "Pillar 3 Disclosures"
DOC_DATE     = "31 December 2025"
BRAND_COLOUR = "CC0000"
HEADER_FILL  = "1F3864"
DARK_GREY    = "404040"
MID_GREY     = "595959"
WHITE        = "FFFFFF"
LIGHT_GREY   = "D9D9D9"
NUM_FMT      = '#,##0;(#,##0);"-"'
META_HEADERS = ["unique_row_id", "hierarchy_level", "parent_row_id", "Label"]
N_META       = len(META_HEADERS)

# Pricing: gemini-3.5-flash. Last verified 2026-07-14 (ai.google.dev/gemini-api/
# docs/pricing; reconciled to the cent vs actual AI Studio billing: $4.11 for
# the 95 ledgered calls). Thinking bills at the output rate. RE-VERIFY on any
# model change — the stale 2.5-flash constants understated real spend 3.6-5x.
INPUT_PRICE_PER_M   = 1.50
OUTPUT_PRICE_PER_M  = 9.00
THINK_PRICE_PER_M   = 9.00

# Shared mutable state — ONE home, imported everywhere else, never duplicated.
_run_usage = {"calls": 0, "prompt": 0, "output": 0, "thinking": 0, "cost": 0.0}
_call_log: list[dict] = []
_run_usage_lock = threading.Lock()
_call_log_lock  = threading.Lock()
_pdfium_lock    = threading.Lock()

# ===========================================================================
# COMPACT OUTPUT SCHEMA
# ===========================================================================
class GColumn(BaseModel):
    group: str | None = Field(default=None, description="2nd-level group header spanning sub-columns; null if single-level")
    leaf:  str = Field(description="the column header text — a full descriptive phrase; NEVER a bare letter like '(a)' or '(b)' which are reference indices, not headers")

CELL_STATES = {"reported", "nil", "empty", "grey", "zero"}

_LEGACY_CELL_STATES = {
    "suppressed":     "grey",
    "rounds_to_zero": "reported",
}

class GCell(BaseModel):
    value:      str  = Field(description="cell value verbatim as printed; use '-' for any dash (-, –, —); '' ONLY for cells that are truly blank with absolutely no mark; '0' for printed zero")
    cell_state: Literal["reported", "nil", "empty", "grey", "zero"] = Field(default="reported",
                             description="reported | nil | empty | grey | zero")

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, obj):
        if isinstance(obj, str):
            return cls.from_str(obj).model_dump()
        if isinstance(obj, dict):
            cs = obj.get("cell_state")
            if cs in _LEGACY_CELL_STATES:
                obj = {**obj, "cell_state": _LEGACY_CELL_STATES[cs]}
        return obj

    @classmethod
    def from_str(cls, v: str) -> "GCell":
        """Upgrade a plain string (legacy parsed.json) to a GCell."""
        s = str(v).strip()
        if s in ("-", "–", "—"):
            return cls(value="-", cell_state="nil")
        if s == "0":
            return cls(value="0", cell_state="zero")
        if s == "":
            return cls(value="", cell_state="empty")
        return cls(value=s, cell_state="reported")

class GRow(BaseModel):
    row_id:   str | None = Field(default=None, description="printed line number EXACTLY as shown ('1','4a','14a'); null for rows with no printed number (section headers, sub-headers, footnotes)")
    row_type: Literal["section_header", "data", "total", "sub_header", "note"] = Field(default="data", description="section_header | data | total | sub_header | note")
    level:    int = Field(description="0=section header or grand total; 1=primary line item; 2=sub-item (indented / 'of which' / named breakdown); 3=rare")
    parent:   str | None = Field(default=None, description="null for level-0 and level-1 rows; for level-2+ the row_id of the nearest row one level above")
    label:    str = Field(description="row label text, verbatim, including footnote markers")
    values:   list[GCell] = Field(default_factory=list, description="cells left-to-right, one GCell per column; [] for section_header/sub_header/note rows")

    @model_validator(mode="before")
    @classmethod
    def _upgrade_string_values(cls, obj):
        if isinstance(obj, dict):
            vals = obj.get("values")
            if vals and isinstance(vals[0], str):
                obj = {**obj, "values": [GCell.from_str(v).model_dump() for v in vals]}
        return obj

class GTable(BaseModel):
    title:        str = Field(description="printed table title, verbatim, including the reporting date if shown")
    label_header: str = Field(default="", description="header of the row-label column, e.g. 'Metric'; '' if none")
    continued_from_previous: bool = Field(default=False, description="true if this table is the continuation of a table that started on the previous page (rows continue under the same columns, header NOT repeated)")
    section_id:   str = Field(default="", description="for multiple-section pages only: the section number this table belongs to (e.g. '12.2'); leave '' for single-section pages")
    columns:      list[GColumn]
    rows:         list[GRow]

class Extraction(BaseModel):
    tables: list[GTable]
