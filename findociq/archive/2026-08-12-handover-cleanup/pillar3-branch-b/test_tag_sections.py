"""test_tag_sections.py — plain check() tests for tag_sections.py's PURE
branch-decision logic and CLI arg parsing. Does NOT invoke paddle or Gemini
(see module docstring / spec: no pytest, no live paddle/Gemini calls here).

Usage:
  python3 findociq/pipeline/discover/section/test_tag_sections.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tag_sections as ts  # noqa: E402

_PASS = _FAIL = 0


def check(label, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def main():
    # --- pick_branch: pure branch-decision logic ---
    check("pick_branch(None) -> gemini", ts.pick_branch(None) == "gemini")
    check("pick_branch('') -> gemini (falsy path)", ts.pick_branch("") == "gemini")
    check("pick_branch('toc.json') -> toc", ts.pick_branch("toc.json") == "toc")
    check("pick_branch('/abs/path/toc.json') -> toc",
          ts.pick_branch("/abs/path/toc.json") == "toc")

    # --- CLI arg parsing (argparse only; no execution of run()) ---
    import argparse

    def _build_parser():
        ap = argparse.ArgumentParser()
        ap.add_argument("pdf_path")
        ap.add_argument("tag")
        ap.add_argument("--toc", default=None)
        ap.add_argument("--out", default=ts._DEFAULT_OUT)
        ap.add_argument("--skip-emit", action="store_true")
        ap.add_argument("--doc-id", default=None)
        return ap

    parser = _build_parser()
    args = parser.parse_args(["a.pdf", "mytag"])
    check("default: toc is None", args.toc is None)
    check("default: out is _DEFAULT_OUT", args.out == ts._DEFAULT_OUT)
    check("default: skip_emit is False", args.skip_emit is False)
    check("default: doc_id is None", args.doc_id is None)

    args2 = parser.parse_args(["a.pdf", "mytag", "--toc", "toc.json",
                                "--out", "/tmp/out", "--skip-emit",
                                "--doc-id", "doc123"])
    check("--toc parsed", args2.toc == "toc.json")
    check("--out parsed", args2.out == "/tmp/out")
    check("--skip-emit parsed", args2.skip_emit is True)
    check("--doc-id parsed", args2.doc_id == "doc123")
    check("pdf_path/tag positional parsed", args2.pdf_path == "a.pdf" and args2.tag == "mytag")

    # branch decision matches what run() would pick, given parsed args
    check("branch from args (no --toc) -> gemini", ts.pick_branch(args.toc) == "gemini")
    check("branch from args2 (--toc set) -> toc", ts.pick_branch(args2.toc) == "toc")

    # --- module-level path resolution sanity (no subprocess invoked) ---
    check("PADDLE_PYTHON resolves under REPO_ROOT/.venv-paddle/bin/python",
          ts.PADDLE_PYTHON.endswith(os.path.join(".venv-paddle", "bin", "python")))
    check("REPO_ROOT resolves to a real directory",
          os.path.isdir(ts.REPO_ROOT))
    check("_DEFAULT_OUT matches sibling modules' default out root",
          os.path.normpath(ts._DEFAULT_OUT) ==
          os.path.normpath(os.path.join(HERE, "..", "..", "..", "experiments",
                                         "2026-07-07_paddleocr_eval", "outputs")))

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
