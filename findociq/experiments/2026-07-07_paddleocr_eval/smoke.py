"""smoke — prove PP-StructureV3 runs on this machine and emits table HTML + cell geometry.

Renders DBS 4Q23 p75 (ruled NSFR page) at 200 DPI, runs PP-StructureV3, persists all
artifacts under outputs/smoke/, and prints the REAL result-JSON shape (keys + the paths
holding table HTML) — Task 3 (md_tables) is written against this captured reality.

Run: .venv-paddle/bin/python findociq/experiments/2026-07-07_paddleocr_eval/smoke.py
"""
import json, os, sys
import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs", "smoke")
PDF = os.path.join(HERE, "..", "..", "data", "sources", "pillar3", "DBS_4Q23_Pillar3.pdf")


def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return bool(cond)


def find_html_paths(node, path="$"):
    hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and "<table" in v:
                hits.append(path + "." + k)
            hits += find_html_paths(v, path + "." + k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits += find_html_paths(v, f"{path}[{i}]")
    return hits


def main():
    os.makedirs(OUT, exist_ok=True)
    png = os.path.join(OUT, "p75.png")
    with pdfplumber.open(PDF) as pdf:
        pdf.pages[74].to_image(resolution=200).save(png)

    from paddleocr import PPStructureV3
    # use_chart_recognition=False: PP-Chart2Table (VLM chart-to-table component) segfaults
    # on this machine (paddlepaddle 3.0.0 / paddlex 3.1.0, macOS arm64, CPU) while loading
    # its generation_config after weight load.
    # use_formula_recognition=False: PP-FormulaNet_plus-L then hit a SIGBUS while loading
    # its 727MB inference.pdiparams on the same machine/stack.
    # Neither chart-to-table nor LaTeX formula recognition is needed for ruled financial
    # table extraction (our target document class never contains charts or math formulas),
    # so both are documented capability toggles scoped to the task, not per-doc hacks.
    # use_doc_orientation_classify/use_doc_unwarping/use_seal_recognition=False: our source
    # PDFs are digital-native (not scanned), so page-orientation classification, document
    # unwarping, and seal/stamp recognition are irrelevant subsystems for this document
    # class. Disabling them shrinks the peak concurrently-loaded-model footprint (memory
    # pressure has caused repeated SIGBUS crashes on this machine during model loading;
    # these are documented capability toggles, not per-doc hacks). Table subsystems (wired/
    # wireless cell detection, table classification) stay ON — those are the ones we need.
    pipe = PPStructureV3(
        device="cpu",
        use_chart_recognition=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_seal_recognition=False,
        use_formula_recognition=False,
    )
    results = list(pipe.predict(png))

    ok = check("exactly one page result", len(results) == 1, len(results))
    res = results[0]
    res.save_to_json(save_path=OUT)      # if these treat save_path as a file, adjust to
    res.save_to_markdown(save_path=OUT)  # save_path=os.path.join(OUT, "p75.json") etc.
    jsons = [f for f in os.listdir(OUT) if f.endswith(".json")]
    mds = [f for f in os.listdir(OUT) if f.endswith(".md")]
    ok &= check("json persisted", bool(jsons), os.listdir(OUT))
    ok &= check("markdown persisted", bool(mds), os.listdir(OUT))
    raw = json.load(open(os.path.join(OUT, jsons[0])))
    ok &= check("table html somewhere in json", "<table" in json.dumps(raw))
    md = open(os.path.join(OUT, mds[0])).read()
    ok &= check("markdown embeds <table html", "<table" in md, md[:300])
    ok &= check("cell geometry present", "cell_box" in json.dumps(raw)[:2_000_000]
                or "cell_box_list" in json.dumps(raw))
    print("\n--- top-level json keys:", sorted(raw.keys()) if isinstance(raw, dict) else type(raw))
    print("--- paths holding table html:", find_html_paths(raw)[:10])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
