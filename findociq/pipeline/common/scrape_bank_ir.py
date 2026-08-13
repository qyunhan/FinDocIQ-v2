"""Bank IR scraper — crawl each bank's investor-relations page for quarterly PDFs.

Implements the ingest step upstream of the document-family router
(`findociq/pipeline/classify/family.py`): crawl each bank's real investor-
relations landing page, find quarterly-filing PDF links, download each,
classify it with `classify_doc`, and decide keep/discard based on family.
Kept `fs`-family docs are additionally batch-checked with `build_manifest`
(per bank) to compute the `fs_preferred` tie-break. This script only
downloads, classifies, and places files on disk — it never re-implements
classification logic itself.

No per-bank / per-document special-casing beyond the landing-page URLs and
the domain->institution mapping: link discovery, classification, keep/
discard, and placement are identical generic logic across banks.

CLI:
    python3 findociq/pipeline/ingest/scrape_bank_ir.py [--bank DBS|OCBC|UOB|all] [--out DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stage1_extract.route.family import build_manifest, classify_doc, period_from_stem  # noqa: E402

# ===========================================================================
# Bank-specific constants (the ONLY bank-specific data in this module)
# ===========================================================================
LANDING_PAGES: dict[str, list[str]] = {
    "DBS": ["https://www.dbs.com/investors/financials/quarterly-financials"],
    "OCBC": [
        "https://www.ocbc.com/group/investors/financials.page",
        "https://www.ocbc.com/group/investors/regulatory-disclosure.page",
    ],
    "UOB": ["https://www.uobgroup.com/investor-relations/financial/index.html"],
}

_DOMAIN_TO_INSTITUTION = {
    "dbs.com": "DBS",
    "ocbc.com": "OCBC",
    "uobgroup.com": "UOB",
}

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_REQUEST_TIMEOUT_S = 30
_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "sources" / "financial_statements"
_DEFAULT_OUT_DISPLAY = "findociq/data/sources/financial_statements"


# ===========================================================================
# institution derivation — simple, obvious domain mapping
# ===========================================================================
def _institution_from_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    for domain, inst in _DOMAIN_TO_INSTITUTION.items():
        if domain in netloc:
            return inst
    return ""


# ===========================================================================
# period scoping — generic, bank-agnostic pre-download filter
# ===========================================================================
def _parse_periods_arg(periods: str | None) -> tuple[set[int], set[str]] | None:
    """Parse --periods into (whole_years, specific_quarters).

    Tokens are comma-separated; each is either a bare year ('2025' -> all of
    that year) or a 'YYYY-Qn' quarter ('2026-Q1'). Returns None when no filter
    was requested (crawl everything — the original behaviour).
    """
    if not periods:
        return None
    years: set[int] = set()
    quarters: set[str] = set()
    for tok in periods.split(","):
        tok = tok.strip().upper()
        if not tok:
            continue
        if "-Q" in tok:
            quarters.add(tok)
        else:
            years.add(int(tok))
    return years, quarters


def _url_in_scope(url: str, scope: tuple[set[int], set[str]] | None) -> bool:
    """Decide, from the URL alone, whether to fetch this PDF.

    Two signals, most specific first:
      1. period_from_stem on the URL basename — the same '1Q25'/'FY25' token
         logic the classifier uses. When a quarter is derivable it decides
         exactly (year in scope, or the specific quarter requested).
      2. Otherwise, any 4-digit year in the URL path. Banks foldername their
         archives by year (`/quarterly-results/2025/`, `/Major Regulatory/2025/`),
         so this prunes the deep back-catalogue even when the filename carries
         no quarter token. A year matches if it's a wanted whole year or the
         year of a wanted quarter (quarter can't be resolved from a bare year,
         so we keep and let the downstream period filter narrow it).

    Only when neither signal yields any year do we fall through to True, so a
    target doc with a truly opaque URL is never silently skipped.
    """
    if scope is None:
        return True
    years, quarters = scope
    path = urlparse(url).path
    period = period_from_stem(Path(path).stem)
    if period is not None:
        year = int(period.split("-", 1)[0])
        return year in years or period in quarters
    wanted_years = years | {int(q.split("-", 1)[0]) for q in quarters}
    url_years = {int(y) for y in re.findall(r"20[0-2]\d", path)}
    if url_years:
        return bool(url_years & wanted_years)
    return True  # no year signal anywhere -> keep; classifier decides after download


# ===========================================================================
# link discovery — stdlib HTML parsing only (no bs4 dependency)
# ===========================================================================
class _PdfLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value and value.lower().endswith(".pdf"):
                self.hrefs.append(value)


def _discover_pdf_links(page_url: str) -> list[str]:
    """Fetch a landing page live and return absolute .pdf URLs found on it."""
    try:
        resp = requests.get(
            page_url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - network flakiness must not crash the run
        print(f"[warn] failed to fetch landing page {page_url}: {type(exc).__name__}: {exc}")
        return []

    parser = _PdfLinkParser()
    try:
        parser.feed(resp.text)
    except Exception as exc:  # noqa: BLE001 - malformed HTML must not crash the run
        print(f"[warn] failed to parse landing page {page_url}: {type(exc).__name__}: {exc}")
        return []

    return sorted({urljoin(page_url, href) for href in parser.hrefs})


# ===========================================================================
# download
# ===========================================================================
def _download_to_temp(url: str) -> str | None:
    """Download to a scratch dir, preserving the URL's original basename.

    classify_doc derives institution/period from the filename stem (see
    _derive_institution / _derive_period in family.py), so the temp path must
    keep the real filename — a random tempfile name would blank out both
    fields for every document, for every bank.
    """
    try:
        resp = requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - one bad PDF must not crash the run
        print(f"[warn] failed to download {url}: {type(exc).__name__}: {exc}")
        return None

    filename = Path(urlparse(url).path).name or "download.pdf"
    scratch_dir = Path(tempfile.mkdtemp(prefix="scrape_bank_ir_"))
    tmp_path = scratch_dir / filename
    tmp_path.write_bytes(resp.content)
    return str(tmp_path)


# ===========================================================================
# cleanup
# ===========================================================================
def _cleanup_temp(tmp_path: str) -> None:
    """Remove the downloaded file and its per-download scratch dir."""
    p = Path(tmp_path)
    p.unlink(missing_ok=True)
    try:
        p.parent.rmdir()
    except OSError:
        pass  # not empty / already gone — fine, it's in the OS temp area


# ===========================================================================
# placement
# ===========================================================================
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dest_path(out_root: Path, bank: str, url: str, period: str) -> Path:
    filename = Path(urlparse(url).path).name
    if period and "-" in period:
        year, quarter = period.split("-", 1)
        return out_root / bank / year / quarter / filename
    return out_root / bank / "UNKNOWN_PERIOD" / filename


# ===========================================================================
# per-bank crawl + classify + keep/discard
# ===========================================================================
def _process_bank(bank: str, out_root: Path, dry_run: bool,
                  scope: tuple[set[int], set[str]] | None = None,
                  seen_hashes: dict[str, str] | None = None) -> dict:
    """seen_hashes: sha256 -> first URL that produced it, shared across the
    whole scrape invocation (all banks/pages) so the SAME PDF served at two
    different URLs (a real recurring case — IR sites relink the same file
    under a bank-prefixed and an unprefixed path) is placed only once,
    regardless of what either URL happens to be named."""
    if seen_hashes is None:
        seen_hashes = {}
    summary = {
        "links_found": 0,
        "skipped_out_of_scope": 0,
        "kept_pillar3": 0,
        "kept_fs": 0,
        "discarded_slides": 0,
        "discarded_other": 0,
        "discarded_error": 0,
        "discarded_duplicate_content": 0,
        "fs_preferred_zero_kept_anyway": 0,
        "fs_tiebreak_ambiguous": 0,
    }

    all_links: list[str] = []
    for page_url in LANDING_PAGES[bank]:
        links = _discover_pdf_links(page_url)
        print(f"[{bank}] {page_url} -> {len(links)} pdf link(s)")
        all_links.extend(links)
    # de-dup across the bank's landing pages (e.g. OCBC has two)
    all_links = sorted(set(all_links))
    summary["links_found"] = len(all_links)

    # Period scoping: prune out-of-scope URLs before spending a download on them.
    if scope is not None:
        in_scope = [u for u in all_links if _url_in_scope(u, scope)]
        summary["skipped_out_of_scope"] = len(all_links) - len(in_scope)
        print(f"[{bank}] period filter: {len(in_scope)}/{len(all_links)} link(s) "
              f"in scope ({summary['skipped_out_of_scope']} skipped)")
        all_links = in_scope

    # docs classified this run, keyed by temp path, with their source url
    keep_candidates: list[dict] = []  # {"url", "tmp_path", "row"}

    for url in all_links:
        tmp_path = _download_to_temp(url)
        if tmp_path is None:
            summary["discarded_error"] += 1
            continue

        digest = _sha256(tmp_path)
        if digest in seen_hashes:
            print(f"[{bank}] discarded (duplicate content of {seen_hashes[digest]}): {url}")
            summary["discarded_duplicate_content"] += 1
            _cleanup_temp(tmp_path)
            continue
        seen_hashes[digest] = url

        row = classify_doc(tmp_path)
        print(
            f"[{bank}] {url} -> family={row['family']} institution={row['institution']} "
            f"period={row['period']} fs_subtype={row['fs_subtype']} "
            f"confidence={row['confidence']} flags={row['flags']}"
        )

        if row["family"] in ("slides", "other", "ERROR"):
            key = {"slides": "discarded_slides", "other": "discarded_other"}.get(
                row["family"], "discarded_error")
            summary[key] += 1
            print(f"[{bank}] discarded ({row['family']}): {url}")
            _cleanup_temp(tmp_path)
            continue

        keep_candidates.append({"url": url, "tmp_path": tmp_path, "row": row})

    # batch fs_preferred tie-break, scoped to this bank's fs docs from this run
    fs_candidates = [c for c in keep_candidates if c["row"]["family"] == "fs"]
    if fs_candidates:
        manifest_rows = build_manifest([c["tmp_path"] for c in fs_candidates])
        rows_by_path = {r["path"]: r for r in manifest_rows}
        for c in fs_candidates:
            resolved = str(Path(c["tmp_path"]).resolve())
            updated = rows_by_path.get(resolved)
            if updated is not None:
                c["row"] = updated

    for c in keep_candidates:
        url, tmp_path, row = c["url"], c["tmp_path"], c["row"]
        family = row["family"]
        flags = row["flags"].split(";") if row["flags"] else []

        if family == "pillar3":
            print(f"[{bank}] kept (pillar3): {url}")
            summary["kept_pillar3"] += 1
        else:  # fs
            fs_preferred = row.get("fs_preferred", "")
            if fs_preferred == 0:
                print(f"[{bank}] kept (fs, not preferred, kept anyway for now): {url}")
                summary["fs_preferred_zero_kept_anyway"] += 1
            elif fs_preferred == "" and "fs_tiebreak_ambiguous" in flags:
                print(f"[{bank}] kept (fs, ambiguous tie-break, flagged for human review): {url}")
                summary["fs_tiebreak_ambiguous"] += 1
            else:
                print(f"[{bank}] kept (fs): {url}")
            summary["kept_fs"] += 1

        if dry_run:
            _cleanup_temp(tmp_path)
            continue

        if not row["period"]:
            print(f"[warn][{bank}] no period derived for {url}; placing under UNKNOWN_PERIOD")

        dest = _dest_path(out_root, bank, url, row["period"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_path, dest)
        _cleanup_temp(tmp_path)
        print(f"[{bank}] placed -> {dest}")

    return summary


# ===========================================================================
# CLI
# ===========================================================================
def _print_summary(bank: str, summary: dict) -> None:
    print(f"\n=== summary: {bank} ===")
    print(f"  pdf links found:            {summary['links_found']}")
    print(f"  skipped (out of scope):      {summary['skipped_out_of_scope']}")
    print(f"  kept pillar3:                {summary['kept_pillar3']}")
    print(f"  kept fs:                     {summary['kept_fs']}")
    print(f"  discarded (slides):          {summary['discarded_slides']}")
    print(f"  discarded (other):           {summary['discarded_other']}")
    print(f"  discarded (ERROR):           {summary['discarded_error']}")
    print(f"  discarded (duplicate bytes): {summary['discarded_duplicate_content']}")
    print(f"  fs_preferred=0 kept anyway:  {summary['fs_preferred_zero_kept_anyway']}")
    print(f"  fs ambiguous tie-break:      {summary['fs_tiebreak_ambiguous']}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Crawl bank IR pages for quarterly PDFs.")
    parser.add_argument("--bank", default="all", help="DBS|OCBC|UOB|all (default: all)")
    parser.add_argument("--out", default=_DEFAULT_OUT_DISPLAY, help="output directory root")
    parser.add_argument("--dry-run", action="store_true", help="classify only, write nothing")
    parser.add_argument("--periods", default=None,
                        help="comma-separated scope, e.g. '2025,2026-Q1' (a bare year "
                             "= all its quarters). Default: no filter (crawl everything).")
    args = parser.parse_args(argv)

    bank_arg = args.bank.upper()
    if bank_arg == "ALL":
        banks = list(LANDING_PAGES.keys())
    elif bank_arg in LANDING_PAGES:
        banks = [bank_arg]
    else:
        print(f"error: unknown --bank {args.bank!r}; expected DBS|OCBC|UOB|all", file=sys.stderr)
        return 1

    out_root = Path(args.out) if args.out != _DEFAULT_OUT_DISPLAY else _DEFAULT_OUT
    if not args.dry_run:
        out_root.mkdir(parents=True, exist_ok=True)

    scope = _parse_periods_arg(args.periods)
    if scope is not None:
        print(f"period scope: years={sorted(scope[0])} quarters={sorted(scope[1])}")

    all_summaries: dict[str, dict] = {}
    seen_hashes: dict[str, str] = {}   # shared across ALL banks in this invocation
    for bank in banks:
        all_summaries[bank] = _process_bank(bank, out_root, args.dry_run, scope, seen_hashes)

    for bank, summary in all_summaries.items():
        _print_summary(bank, summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
