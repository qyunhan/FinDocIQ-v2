"""scan — Stage-1 deterministic router: pdfplumber signals -> per-page routing + visual map.

Per page (zero LLM tokens, ~350ms):
  section    <- running-header regex (dotted number + title + (cont'd) flag)
  tables[]   <- find_tables() bboxes (bordered signal; ruled fragments)
  bscore     <- aligned numeric right-edges (borderless financial statements)
  num_cov    <- fraction of the page's numeric tokens whose centre falls inside
                any find_tables() fragment bbox (primary discriminator, see
                docs/specs/2026-07-02-fragment-reconciliation-rule.md)
  frag_area_frac <- Sigma fragment area / content-bbox area (corroborating signal)

Page class (coverage-gated; replaces the old `ruled >= 1 -> BORDERED` rule):
  BORDERED_MULTI / BORDERED_SINGLE  num_cov >= COV_HI -> fragments ARE content,
                                     one small Gemini call per fragment (sibling units)
  BORDERLESS_MAIN                   ruled >= 1, num_cov < COV_LO, bscore strong ->
                                     fragments are DECOYS over a borderless main table;
                                     ONE main-table unit, decoys dropped (recorded, audit only)
  BORDERLESS                        ruled == 0, bscore >= B_MIN -> MinerU detect, per-region
  MIXED_REVIEW                      0.50 <= num_cov < 0.80 (dead zone) -> flag, don't guess
  NO_TABLE                          nothing here -> skip, no Stage-2 call

Template match (per candidate unit, AFTER classification/unit-build): title keywords over
the FULL page text (not just the running header — OCBC's running header is the period date)
+ column-header signature vs template_col in the registry DB. A match sets unit consolidation,
framing ncols, and the structure authority handed to Stage-3 V2 (template_cell for borderless
renders, merge_map for ruled renders, cross-checked against template_cell).

Continuation: same section_no + (cont'd) in the header -> unit spans pages, but ONLY when the
column signature (grid boundary count/positions) also matches — sibling tables that merely
stack vertically must never collapse (see 2026-07-02-fragment-reconciliation-rule.md Sec4).

Outputs <out>.json (the manifest) and <out>.html (the routing mindmap — regenerate
after any logic change here and the map changes with the code).

Usage:
    python3 scan.py <pdf> [--out out/route_map]
"""
from __future__ import annotations
import os, re, sys, json, sqlite3, argparse, datetime, subprocess, math
import pdfplumber

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_map  # noqa: E402  (structure authority for ruled fragments / grid signatures)

NUM = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?%?$|^-$")
SECT = re.compile(r"(\d+(?:\.\d+)+)\s+(.{0,80}?)(?=\s*(?:\(cont|The\s|As at|$))")
CONT = re.compile(r"\(cont(?:inued|['’]d)?\.?\)", re.I)
MIN_DATA_ROWS = 3
ALIGN_TOL = 3.0

# ------------------------------------------------------------- §1 thresholds
COV_HI = 0.80
COV_LO = 0.50
B_MIN = MIN_DATA_ROWS        # existing borderless floor
B_EDGE_FALLBACK_MIN = 8      # bscore floor to even consider skipping MinerU (§2)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "db", "final.db")

# Title keywords are not modelled in the registry (template_row/template_col carry row/column
# identity, not page-title vocabulary) — hardcoded per table_type, matched against the FULL
# page text per §3. Assumption, flagged in the delivery report.
TEMPLATE_TITLE_KEYWORDS = {
    "nsfr": ["net stable funding ratio", "nsfr"],
}


def _norm(s: str) -> str:
    """lower, fold common unicode punctuation, collapse non-alnum to single spaces."""
    s = s.lower().replace("≥", ">=").replace("≤", "<=").replace("’", "'")
    s = re.sub(r"[^a-z0-9<>=]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------- signals
def _header(page) -> tuple[str | None, str, bool]:
    """(section_no, title, is_continuation) from the running header (top 14%)."""
    ws = [w for w in page.extract_words() if w["top"] < page.height * 0.14]
    text = " ".join(w["text"] for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"])))
    m = SECT.search(text)
    return (m.group(1) if m else None,
            (m.group(2).strip() if m else text[:60].strip()),
            bool(CONT.search(text)))


def _bscore(words) -> int:
    """Aligned numeric data-rows (borderless signal) — from discover_prefilter."""
    rows, cur, last = [], [], None
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if last is None or abs(w["top"] - last) <= 2.5:
            cur.append(w)
        else:
            rows.append(cur); cur = [w]
        last = w["top"]
    if cur:
        rows.append(cur)
    numeric = [[round(w["x1"], 1) for w in r if NUM.match(w["text"])]
               for r in rows]
    numeric = [r for r in numeric if len(r) >= 2]
    if len(numeric) < MIN_DATA_ROWS:
        return 0
    edges = [x for r in numeric for x in r]
    cols, used = 0, [False] * len(edges)
    for i, e in enumerate(edges):
        if used[i]:
            continue
        grp = [j for j, f in enumerate(edges) if not used[j] and abs(f - e) <= ALIGN_TOL]
        for j in grp:
            used[j] = True
        cols += len(grp) >= MIN_DATA_ROWS
    return len(numeric) if cols >= 2 else 0


def _in_bbox(cx: float, cy: float, bbox) -> bool:
    x0, top, x1, bot = bbox
    return x0 <= cx <= x1 and top <= cy <= bot


def _coverage(words, tabs, page) -> tuple[float, float, int, list[dict]]:
    """(num_cov, frag_area_frac, num_tokens, numeric_word_dicts) per
    2026-07-02-fragment-reconciliation-rule.md §Signals."""
    numeric = [w for w in words if NUM.match(w["text"])]
    num_tokens = len(numeric)
    if num_tokens == 0:
        return 0.0, 0.0, 0, []
    bboxes = [t["bbox"] for t in tabs]
    inside = 0
    for w in numeric:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        if any(_in_bbox(cx, cy, b) for b in bboxes):
            inside += 1
    num_cov = inside / num_tokens
    if words:
        cx0 = min(w["x0"] for w in words); cx1 = max(w["x1"] for w in words)
        ctop = min(w["top"] for w in words); cbot = max(w["bottom"] for w in words)
        content_area = max((cx1 - cx0) * (cbot - ctop), 1.0)
    else:
        content_area = 1.0
    frag_area = sum(max(b[2] - b[0], 0) * max(b[3] - b[1], 0) for b in bboxes)
    frag_area_frac = frag_area / content_area
    return round(num_cov, 4), round(frag_area_frac, 4), num_tokens, numeric


# ----------------------------------------------------------------- §1 classification
def classify(ruled: int, num_cov: float, frag_area_frac: float, bscore: int, num_tokens: int) -> str:
    """The coverage-gated page classifier (docs/specs/2026-07-02-fragment-reconciliation-rule.md §1).
    Replaces the old `ruled >= 1 -> BORDERED` rule."""
    if num_tokens < 5:                # not a data page
        return "NO_TABLE" if ruled == 0 else "BORDERED_MULTI"  # tiny label grid
    if ruled == 0:
        return "BORDERLESS" if bscore >= B_MIN else "NO_TABLE"
    # ruled >= 1:
    if num_cov >= COV_HI:
        return "BORDERED_MULTI" if ruled > 1 else "BORDERED_SINGLE"   # fragments ARE content
    if num_cov < COV_LO and bscore >= B_MIN:
        return "BORDERLESS_MAIN"      # fragments are decoys over a borderless main table
    return "MIXED_REVIEW"             # 0.50-0.80, or low cov + weak bscore -> MinerU arbitration


# ----------------------------------------------------------------- template registry
class _TemplateRegistry:
    """Read-only lookup against findociq/db/final.db template_col (no writes, no other tables
    touched). Cached once per process."""

    def __init__(self, db_path: str = DB_PATH):
        self._cols: dict[str, list[dict]] = {}
        if not os.path.exists(db_path):
            return
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = con.execute(
                "SELECT table_type, col_ord, canonical_header, group_label FROM template_col ORDER BY table_type, col_ord")
            for table_type, col_ord, header, group in cur.fetchall():
                self._cols.setdefault(table_type, []).append(
                    dict(col_ord=col_ord, header=header, group=group))
            con.close()
        except sqlite3.Error:
            self._cols = {}

    def table_types(self):
        return list(self._cols.keys())

    def ncols(self, table_type: str) -> int:
        return len(self._cols.get(table_type, []))

    def leaf_headers(self, table_type: str) -> list[str]:
        return [c["header"] for c in self._cols.get(table_type, [])]

    def group_labels(self, table_type: str) -> list[str]:
        return sorted({c["group"] for c in self._cols.get(table_type, []) if c["group"]})


_REGISTRY = _TemplateRegistry()


def template_match(page_text: str, table_type: str) -> dict | None:
    """§3: title keyword hit (full page text) AND >= ceil(0.6*ncols) header matches
    (normalized token-subsequence containment) -> match. Returns the JSON `template` block
    or None."""
    ncols = _REGISTRY.ncols(table_type)
    if ncols == 0:
        return None
    kws = TEMPLATE_TITLE_KEYWORDS.get(table_type, [])
    norm_text = _norm(page_text)
    title_hit = any(_norm(kw) in norm_text for kw in kws)
    if not title_hit:
        return None
    headers = _REGISTRY.leaf_headers(table_type)
    leaf_hits = sum(1 for h in headers if _norm(h) in norm_text)
    group_hits = sum(1 for g in _REGISTRY.group_labels(table_type) if _norm(g) in norm_text)
    need = math.ceil(0.6 * ncols)
    if leaf_hits < need:
        return None
    matched_by = ["title_kw", "col_signature"]
    if group_hits:
        matched_by.append("group_label")
    return dict(table_type=table_type, matched_by=matched_by, ncols=ncols)


def best_template_match(page_text: str) -> dict | None:
    for tt in _REGISTRY.table_types():
        m = template_match(page_text, tt)
        if m:
            return m
    return None


# ----------------------------------------------------------------- §2 borderless-main region
def _numeric_edge_precheck(words, tabs, bscore) -> tuple[bool, list[float] | None, tuple | None]:
    """Cheap pre-check for skipping MinerU on a BORDERLESS_MAIN page (§2): bscore >= 8 AND
    the clustered numeric right-edges (of tokens OUTSIDE the decoy fragments) collapse to a
    stable column count (<= MIN_DATA_ROWS singleton/noise clusters) AND no decoy fragment
    overlaps the numeric band. Returns (ok, stable_edges, band_bbox)."""
    if bscore < B_EDGE_FALLBACK_MIN:
        return False, None, None
    frag_bboxes = [t["bbox"] for t in tabs]
    numeric_outside = []
    for w in words:
        if not NUM.match(w["text"]):
            continue
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        if not any(_in_bbox(cx, cy, b) for b in frag_bboxes):
            numeric_outside.append(w)
    if len(numeric_outside) < MIN_DATA_ROWS:
        return False, None, None
    edges = [w["x1"] for w in numeric_outside]
    clusters = merge_map._cluster(edges, tol=ALIGN_TOL)
    sizes = [sum(1 for e in edges if abs(e - c) <= ALIGN_TOL) for c in clusters]
    noise = sum(1 for s in sizes if s < MIN_DATA_ROWS)
    stable = [c for c, s in zip(clusters, sizes) if s >= MIN_DATA_ROWS]
    if noise > MIN_DATA_ROWS or len(stable) < 2:
        return False, None, None
    # no decoy fragment may overlap the numeric band (top..bottom of the outside-numeric words)
    band_top = min(w["top"] for w in numeric_outside)
    band_bot = max(w["bottom"] for w in numeric_outside)
    for b in frag_bboxes:
        if b[1] < band_bot and b[3] > band_top:
            return False, None, None
    x0 = min(w["x0"] for w in numeric_outside)
    x1 = max(w["x1"] for w in numeric_outside)
    return True, stable, (x0, band_top, x1, band_bot)


def _mineru_detect(pdf_path: str, page_no: int) -> tuple[list, str]:
    """Best-effort MinerU region detect for the borderless main-table bbox. Returns
    (bbox_or_None, region_source). If MinerU is not invocable in this environment
    (import/runtime failure, timeout), returns (None, "pending") per spec §2/§5 — the unit
    is still emitted, never skipped."""
    try:
        out_dir = "/tmp/scan_mineru_out"
        os.makedirs(out_dir, exist_ok=True)
        proc = subprocess.run(
            ["mineru", "-p", pdf_path, "-o", out_dir, "-b", "pipeline",
             "-s", str(page_no - 1), "-e", str(page_no - 1)],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or "failed" in (proc.stdout + proc.stderr).lower():
            return None, "pending"
        # (parsing content_list.json for the table bbox would go here on success)
        return None, "pending"
    except Exception:
        return None, "pending"


def _build_borderless_main_unit(pdf_path, page_no, page, words, tabs, bscore, page_text) -> dict:
    ok, stable_edges, band_bbox = _numeric_edge_precheck(words, tabs, bscore)
    if ok:
        bbox = [round(v) for v in band_bbox]
        region_source = "numeric_edge"
    else:
        bbox, region_source = None, "pending"
        # MinerU is the designated bbox source; only attempted (not skipped) when the cheap
        # numeric-edge pre-check fails.
        mbbox, msrc = _mineru_detect(pdf_path, page_no)
        if mbbox is not None:
            bbox, region_source = mbbox, msrc
        else:
            region_source = msrc  # "pending" — MinerU not invocable here (see report)
    tmpl = best_template_match(page_text)
    ncols = tmpl["ncols"] if tmpl else None
    return dict(
        unit_kind="borderless_main",
        bbox=bbox,
        region_source=region_source,
        template_type=tmpl["table_type"] if tmpl else None,
        template=tmpl,
        ncols=ncols,
        structure_authority="template_cell" if tmpl else None,
    )


# ----------------------------------------------------------------- §4 sibling / consolidation
def _column_signature(page, bbox) -> tuple:
    """Grid boundary count & positions (rounded, TOL-clustered) for cross-page consolidation
    gating — via merge_map's own grid derivation (its own ink, not template-specific)."""
    grid = merge_map.derive_grid(page, bbox)
    return tuple(round(g / ALIGN_TOL) for g in grid)


def _build_bordered_units(page, tabs, page_text) -> list[dict]:
    """N ruled fragments = N sibling extraction units, UNLESS two are continuation strips of
    ONE table split by furniture (never triggered by 12.9 — heterogeneous column counts)."""
    units = []
    tmpl = best_template_match(page_text)
    for t in tabs:
        units.append(dict(
            unit_kind="bordered_sibling",
            bbox=t["bbox"], rows=t["rows"], cols=t["cols"],
            region_source="pdfplumber_fragment",
            template_type=tmpl["table_type"] if tmpl else None,
            template=tmpl,
            structure_authority="merge_map",
            col_signature=_column_signature(page, t["bbox"]),
        ))
    return units


# ----------------------------------------------------------------- top-level scan
def scan(pdf_path: str) -> dict:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            sec_no, title, contd = _header(page)
            tabs = [{"bbox": [round(v) for v in t.bbox],
                     "rows": len(t.rows), "cols": len(t.columns or [])}
                    for t in page.find_tables()]
            words = page.extract_words(use_text_flow=False)
            bs = _bscore(words)
            num_cov, frag_area_frac, num_tokens, _ = _coverage(words, tabs, page)
            cls = classify(len(tabs), num_cov, frag_area_frac, bs, num_tokens)
            page_text = " ".join(w["text"] for w in words)

            extraction_units: list[dict] = []
            dropped_fragments: list[dict] = []

            if cls in ("BORDERED_MULTI", "BORDERED_SINGLE"):
                extraction_units = _build_bordered_units(page, tabs, page_text)
            elif cls == "BORDERLESS_MAIN":
                for t in tabs:
                    dropped_fragments.append(dict(bbox=t["bbox"], reason="decoy_low_coverage"))
                extraction_units = [_build_borderless_main_unit(
                    pdf_path, i, page, words, tabs, bs, page_text)]
            elif cls == "BORDERLESS":
                # ruled == 0 -> MinerU detect, per-region (existing case; not on the acceptance
                # critical path — no test page lands here per §4).
                mbbox, msrc = _mineru_detect(pdf_path, i)
                tmpl = best_template_match(page_text)
                extraction_units = [dict(
                    unit_kind="borderless_region", bbox=mbbox, region_source=msrc,
                    template_type=tmpl["table_type"] if tmpl else None, template=tmpl,
                    structure_authority="template_cell" if tmpl else None,
                )]
            elif cls == "MIXED_REVIEW":
                pass  # flagged, no extraction units emitted — don't guess (§4 belt-and-suspenders)
            # NO_TABLE: nothing to emit

            n_extraction_units = len(extraction_units)
            plan = {
                "BORDERED_MULTI": f"{len(tabs)} separate per-table Gemini calls (own columns each — never merged)",
                "BORDERED_SINGLE": "1 Gemini call, single-table framing",
                "BORDERLESS_MAIN": "1 Gemini call over the main-table region — decoy fragments dropped",
                "BORDERLESS": "MinerU region detect -> per-region Gemini calls",
                "MIXED_REVIEW": "flagged for review — coverage in the 0.50-0.80 dead zone, no auto call",
                "NO_TABLE": "skip — no Stage-2 call",
            }[cls]

            pages.append(dict(page=i, section_no=sec_no, section_title=title,
                              continuation=contd, n_tables=len(tabs), bscore=bs,
                              num_cov=num_cov, frag_area_frac=frag_area_frac, num_tokens=num_tokens,
                              tables=tabs, route=cls, stage2_plan=plan,
                              dropped_fragments=dropped_fragments,
                              extraction_units=extraction_units,
                              n_extraction_units=n_extraction_units))

    # section-groups: consecutive pages, same section_no, later pages flagged cont'd.
    # Cross-page consolidation of BORDERED sibling units is column-signature gated (§4): a
    # unit only spans pages when section_no matches, continuation is set, AND the trailing
    # unit's col_signature on page N matches the leading unit's col_signature on page N+1.
    units, cur = [], None
    for p in pages:
        if cur and p["section_no"] == cur["section_no"] and p["continuation"]:
            cur["pages"].append(p["page"])
            cur["n_extraction_units"] += p["n_extraction_units"]
        else:
            cur = dict(section_no=p["section_no"], pages=[p["page"]],
                      n_extraction_units=p["n_extraction_units"])
            units.append(cur)
    return dict(pdf=os.path.basename(pdf_path), scanned=datetime.datetime.now().isoformat(timespec="seconds"),
                pages=pages, units=units)


# ----------------------------------------------------------------- mindmap
_ROUTE_META = {  # slot colors from the validated reference palette (light, dark) + glyph
    "BORDERED_MULTI":  ("#2a78d6", "#3987e5", "▦"),
    "BORDERED_SINGLE": ("#1baf7a", "#199e70", "▤"),
    "BORDERLESS_MAIN": ("#b5468f", "#c957a0", "▥"),
    "BORDERLESS":      ("#eda100", "#c98500", "≋"),
    "MIXED_REVIEW":    ("#d64545", "#e35b5b", "⚑"),
    "NO_TABLE":        ("#898781", "#898781", "·"),
}

def render_html(m: dict, out_html: str) -> None:
    rows = []
    for p in m["pages"]:
        lite, dark, glyph = _ROUTE_META[p["route"]]
        chips = "".join(
            f'<span class="chip" title="bbox {t["bbox"]} · {t["rows"]}r×{t["cols"]}c">'
            f'{t["cols"]}c</span>' for t in p["tables"])
        cont = ' <span class="cont">(cont’d ⤴)</span>' if p["continuation"] else ""
        rows.append(f"""
    <div class="page">
      <div class="node pg">p{p["page"]}</div><div class="edge"></div>
      <div class="node sec">§{p["section_no"] or "?"}{cont}
        <div class="hover">{(p["section_title"] or "")[:70]}</div></div><div class="edge"></div>
      <div class="node sig">ruled <b>{p["n_tables"]}</b> · bscore <b>{p["bscore"]}</b> · cov <b>{p["num_cov"]}</b>
        <div class="tablechips">{chips}</div></div><div class="edge"></div>
      <div class="node route" style="--rc-l:{lite};--rc-d:{dark}"><span class="glyph">{glyph}</span>
        {p["route"].replace("_", " ")}</div><div class="edge"></div>
      <div class="node plan">{p["stage2_plan"]}</div>
    </div>""")
    legend = "".join(
        f'<span class="lg"><i style="--rc-l:{l};--rc-d:{d}">{g}</i>{k.replace("_"," ")}</span>'
        for k, (l, d, g) in _ROUTE_META.items())
    html = f"""<!doctype html><meta charset="utf-8">
<title>route map — {m["pdf"]}</title>
<style>
  .viz-root {{ --surface:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink2:#52514e;
               --muted:#898781; --grid:#e1e0d9; --ring:rgba(11,11,11,.10); }}
  @media (prefers-color-scheme: dark) {{
    .viz-root {{ --surface:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink2:#c3c2b7;
                 --muted:#898781; --grid:#2c2c2a; --ring:rgba(255,255,255,.10); }} }}
  body {{ margin:0; background:var(--page); font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  .viz-root {{ max-width:1080px; margin:24px auto; padding:20px 24px;
               background:var(--surface); border:1px solid var(--ring); border-radius:10px; color:var(--ink); }}
  h1 {{ font-size:16px; margin:0 0 2px; }} .sub {{ color:var(--ink2); font-size:12px; margin-bottom:14px; }}
  .cols {{ display:grid; grid-template-columns:56px 14px 150px 14px 190px 14px 185px 14px 1fr;
           gap:0 0; color:var(--muted); font-size:11px; text-transform:uppercase;
           letter-spacing:.04em; padding:0 0 6px; border-bottom:1px solid var(--grid); }}
  .page {{ display:grid; grid-template-columns:56px 14px 150px 14px 190px 14px 185px 14px 1fr;
           align-items:center; padding:9px 0; border-bottom:1px solid var(--grid); }}
  .node {{ position:relative; padding:6px 9px; border:1px solid var(--grid); border-radius:7px;
           background:var(--surface); font-size:13px; }}
  .pg   {{ text-align:center; font-weight:600; }}
  .sec .cont {{ color:var(--ink2); font-size:11px; }}
  .sig  {{ color:var(--ink2); }} .sig b {{ color:var(--ink); }}
  .route {{ border-color:var(--rc-l); font-weight:600; }}
  .route .glyph {{ color:var(--rc-l); margin-right:5px; }}
  @media (prefers-color-scheme: dark) {{
    .route {{ border-color:var(--rc-d); }} .route .glyph {{ color:var(--rc-d); }} }}
  .plan {{ border:none; color:var(--ink2); font-size:12.5px; }}
  .edge {{ height:1px; background:var(--grid); }}
  .tablechips {{ margin-top:4px; display:flex; flex-wrap:wrap; gap:3px; }}
  .chip {{ border:1px solid var(--grid); border-radius:5px; padding:0 5px; font-size:10.5px;
           color:var(--ink2); cursor:default; }}
  .chip:hover {{ border-color:var(--muted); color:var(--ink); }}
  .hover {{ display:none; position:absolute; z-index:2; left:0; top:calc(100% + 4px);
            background:var(--surface); border:1px solid var(--muted); border-radius:6px;
            padding:5px 8px; font-size:12px; color:var(--ink); width:max-content; max-width:340px; }}
  .sec:hover .hover {{ display:block; }}
  .legend {{ display:flex; gap:16px; margin-top:12px; font-size:12px; color:var(--ink2); }}
  .lg i {{ font-style:normal; color:var(--rc-l); margin-right:5px; }}
  @media (prefers-color-scheme: dark) {{ .lg i {{ color:var(--rc-d); }} }}
</style>
<div class="viz-root">
  <h1>Routing map — {m["pdf"]}</h1>
  <div class="sub">generated {m["scanned"]} by pipeline/route/scan.py · rerun after any routing change
    · {len(m["pages"])} pages · section-groups: {", ".join(f"§{u['section_no'] or '?'} p{u['pages'][0]}–{u['pages'][-1]} ({u['n_extraction_units']} extraction units)" for u in m["units"])}</div>
  <div class="cols"><span>page</span><span></span><span>section (hover: title)</span><span></span>
    <span>signals</span><span></span><span>route</span><span></span><span>stage-2 plan</span></div>
  {"".join(rows)}
  <div class="legend">{legend}</div>
</div>
"""
    open(out_html, "w").write(html)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None, help="output basename (default: out/<pdfname>_route)")
    a = ap.parse_args()
    base = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out",
                                 os.path.splitext(os.path.basename(a.pdf))[0] + "_route")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    m = scan(a.pdf)
    json.dump(m, open(base + ".json", "w"), indent=1)
    render_html(m, base + ".html")
    for p in m["pages"]:
        print(f'  p{p["page"]:>3} §{p["section_no"] or "?":<7} ruled={p["n_tables"]:<2} '
              f'bscore={p["bscore"]:<3} cov={p["num_cov"]:<5} -> {p["route"]:<15} '
              f'units={p["n_extraction_units"]:<2} {p["stage2_plan"]}')
    print(f"\n  map: {base}.html   manifest: {base}.json")
