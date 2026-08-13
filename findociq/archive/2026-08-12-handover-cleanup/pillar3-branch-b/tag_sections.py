"""tag_sections.py — single entry point for section->table tagging.

See findociq/docs/specs/2026-07-09-section-table-tagging-design.md,
AMENDMENT 2026-07-09 PM item 6 (route-visibility requirement: prints which
branch fired for every run).

Flow:
  1. Emit candidates.csv + regions.csv via candidates.py, run in the
     .venv-paddle interpreter (subprocess) — unless --skip-emit and both
     files already exist under <out_root>/<tag>/.
  2. Branch decision (pick_branch): --toc given -> TOC branch
     (toc_match.attribute_from_toc); else -> Gemini branch
     (sections_from_gemini.attribute_from_gemini). The Gemini branch module
     is imported LAZILY (inside the branch) so the TOC branch keeps working
     even if sections_from_gemini.py is mid-build.
  3. section_manifest.build_manifest(tag, doc_id or tag, out_root).
  4. Print a compact summary: branch, n regions, n sections, output paths.

Usage:
  python3 tag_sections.py <pdf_path> <tag> [--toc <toc.json>] [--out <root>]
                           [--skip-emit] [--doc-id <id>]
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))
PADDLE_PYTHON = os.path.join(REPO_ROOT, ".venv-paddle", "bin", "python")

_DEFAULT_OUT = os.path.normpath(os.path.join(
    HERE, "..", "..", "..", "experiments", "2026-07-07_paddleocr_eval", "outputs"))

if HERE not in sys.path:
    sys.path.insert(0, HERE)
import section_manifest  # noqa: E402
import toc_match  # noqa: E402


def pick_branch(toc_path: str | None) -> str:
    """Deterministic branch decision (route-visibility requirement): a TOC
    path means the printed-TOC deterministic branch fires; no TOC means the
    Gemini heading-validator branch fires. Pure function, no I/O, so it's
    testable without paddle/Gemini."""
    return "toc" if toc_path else "gemini"


def _emit_candidates(pdf_path: str, tag: str, out_root: str) -> None:
    if not os.path.exists(PADDLE_PYTHON):
        sys.exit(
            f"[{tag}] .venv-paddle python not found at {PADDLE_PYTHON} — "
            "cannot emit candidates.csv/regions.csv. Create the venv first."
        )
    candidates_script = os.path.join(HERE, "candidates.py")
    cmd = [PADDLE_PYTHON, candidates_script, pdf_path, tag, "--out", out_root]
    print(f"[{tag}] STEP 1: emitting candidates + regions via .venv-paddle: "
          f"{' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
    for line in proc.stdout:
        print(line, end="", flush=True)
    ret = proc.wait()
    if ret != 0:
        sys.exit(f"[{tag}] candidates.py failed with exit code {ret}")


def _count_csv_rows(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def run(pdf_path: str, tag: str, toc_path: str | None = None,
        out_root: str = _DEFAULT_OUT, skip_emit: bool = False,
        doc_id: str | None = None) -> dict:
    tag_dir = os.path.join(out_root, tag)
    candidates_path = os.path.join(tag_dir, "candidates.csv")
    regions_path = os.path.join(tag_dir, "regions.csv")

    have_emit = os.path.exists(candidates_path) and os.path.exists(regions_path)
    if skip_emit and have_emit:
        print(f"[{tag}] STEP 1: --skip-emit and candidates.csv/regions.csv already "
              f"exist -> skipping paddle emitter", flush=True)
    else:
        _emit_candidates(pdf_path, tag, out_root)

    branch = pick_branch(toc_path)
    print(f"[{tag}] BRANCH: {branch.upper()} "
          f"({'--toc provided' if branch == 'toc' else 'no --toc given'})", flush=True)

    if branch == "toc":
        print(f"[{tag}] STEP 2: toc_match.attribute_from_toc", flush=True)
        toc_match.attribute_from_toc(tag, toc_path, out_root)
    else:
        # Lazy import: keeps the TOC branch fully usable even if
        # sections_from_gemini.py is mid-build (spec requirement).
        print(f"[{tag}] STEP 2: sections_from_gemini.attribute_from_gemini", flush=True)
        from sections_from_gemini import attribute_from_gemini
        attribute_from_gemini(tag, out_root)

    print(f"[{tag}] STEP 3: section_manifest.build_manifest", flush=True)
    manifest_path = section_manifest.build_manifest(tag, doc_id or tag, out_root)

    n_regions = _count_csv_rows(regions_path)
    section_map_csv = os.path.join(tag_dir, "section_map.csv")
    section_map_html = os.path.join(tag_dir, "section_map.html")
    n_sections = _count_csv_rows(section_map_csv)

    summary = dict(branch=branch, n_regions=n_regions, n_sections=n_sections,
                   section_manifest_csv=manifest_path,
                   section_map_csv=section_map_csv,
                   section_map_html=section_map_html)
    print(f"\n[{tag}] SUMMARY: branch={branch}  n_regions={n_regions}  "
          f"n_sections={n_sections}")
    print(f"[{tag}]   section_manifest.csv -> {manifest_path}")
    print(f"[{tag}]   section_map.csv      -> {section_map_csv}")
    print(f"[{tag}]   section_map.html     -> {section_map_html}")
    return summary


def main():
    ap = argparse.ArgumentParser(
        description="Single entry point: candidates emit -> branch arranger -> "
                    "section_manifest.")
    ap.add_argument("pdf_path")
    ap.add_argument("tag")
    ap.add_argument("--toc", default=None, help="printed-TOC json path -> TOC branch; "
                     "omitted -> Gemini branch")
    ap.add_argument("--out", default=_DEFAULT_OUT, help="output root")
    ap.add_argument("--skip-emit", action="store_true",
                     help="skip the paddle emitter if candidates.csv/regions.csv "
                          "already exist")
    ap.add_argument("--doc-id", default=None,
                     help="doc_id stamped into section_manifest.csv (default: tag)")
    args = ap.parse_args()

    if not args.skip_emit and not os.path.exists(args.pdf_path):
        sys.exit(f"PDF not found: {args.pdf_path}")

    run(args.pdf_path, args.tag, toc_path=args.toc, out_root=args.out,
        skip_emit=args.skip_emit, doc_id=args.doc_id)


if __name__ == "__main__":
    main()
