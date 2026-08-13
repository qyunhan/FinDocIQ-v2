"""review — persistent, async drift queue (the human-mapping layer).

The pipeline NEVER blocks on a human. align() drift is written to a CSV `review_queue`
that anyone can open (Excel/Sheets), fill the DECISION column, and save — at any time,
not tied to whoever ran the extraction. `apply_decisions` ingests the filled CSV and
returns concept_map + row_template updates (learned once, reused forever).

CSV columns:
  doc_id, table_type, instance_label, instance_line_no,
  closest_template, template_line_no, candidate_concept_key, score, suggested,
  DECISION(<- you fill: same|new|split),  map_to_concept_key(<- optional override)

A reviewer typically just confirms `suggested`: type 'same' to accept the paired concept,
'new' to mint a new template line, 'split' to replace one line with children.
"""
from __future__ import annotations
import os, csv, re

FIELDS = ["doc_id", "table_type", "instance_label", "instance_line_no",
          "closest_template", "template_line_no", "candidate_concept_key",
          "score", "suggested", "DECISION", "map_to_concept_key"]

def _slug(s): return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:40]
def _norm(s): return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def write_queue(report, doc_id: str, table_type: str, path: str) -> int:
    """Write the drift report to a reviewer-fillable CSV. Returns # of rows queued."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for d in report.drift:
            w.writerow({
                "doc_id": doc_id, "table_type": table_type,
                "instance_label": d.instance_label, "instance_line_no": d.instance_line_no or "",
                "closest_template": d.candidate_label or "", "template_line_no": d.candidate_line_no or "",
                "candidate_concept_key": d.candidate_concept_key or "",
                "score": d.score, "suggested": d.suggested,
                "DECISION": "", "map_to_concept_key": "",
            })
    return len(report.drift)


def apply_decisions(path: str):
    """Read a filled CSV → (concept_map_additions, row_template_additions).
       concept_map_additions: {label_norm: concept_key}   (DECISION=same)
       row_template_additions: [{table_type, label, concept_key}]  (DECISION=new)
       Rows with blank DECISION are left pending (re-queued next run)."""
    cmap_add, tmpl_add, pending = {}, [], 0
    if not os.path.exists(path):
        return cmap_add, tmpl_add, pending
    for row in csv.DictReader(open(path)):
        dec = (row.get("DECISION") or "").strip().lower()
        if dec == "same":
            ck = (row.get("map_to_concept_key") or "").strip() or row.get("candidate_concept_key", "")
            if ck:
                cmap_add[_norm(row["instance_label"])] = ck
        elif dec == "new":
            ck = (row.get("map_to_concept_key") or "").strip() or _slug(row["instance_label"])
            tmpl_add.append({"table_type": row["table_type"], "label": row["instance_label"], "concept_key": ck})
        elif dec in ("", "split"):
            pending += 1                        # split handled interactively / re-queued
    return cmap_add, tmpl_add, pending
