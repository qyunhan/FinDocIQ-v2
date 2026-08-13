"""align — reconcile an extracted table INSTANCE against the master TEMPLATE.

Two passes:
  1. AUTO-MATCH only — exact label, line-number anchor, or high parent-aware fuzzy (>=HI).
     These consume a template row. Nothing below HI is ever auto-resolved.
  2. PAIR the leftovers — each unmatched instance row is paired with its closest remaining
     template row (any score) so a reviewer sees "instance row ↔ likely template line" side by
     side. suggested='same' if score>=LO (a reword), else 'new' (genuinely new line).

Repeated labels (e.g. NSFR "≤35% risk weight" under two different parents) are NOT deduped:
the used-set + reading order + parent bonus assign each occurrence to its own template row.

Output `Report`: matched (load) · drift (→ review_queue) · absent (template lines not seen).
clean == no drift.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re
from difflib import SequenceMatcher

HI, LO = 0.90, 0.70


def _norm(s: str) -> str:
    s = re.sub(r"\(\d+\)|\[\d+\]", " ", (s or ""))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()

def _sim(a: str, b: str) -> float:
    na, nb = set(_norm(a).split()), set(_norm(b).split())
    jacc = len(na & nb) / len(na | nb) if (na | nb) else 0.0
    return max(jacc, SequenceMatcher(None, _norm(a), _norm(b)).ratio())


@dataclass
class Row:
    label: str
    line_no: str | None = None
    parent_label: str | None = None

@dataclass
class Match:
    label: str
    line_no: str | None
    concept_key: str
    score: float

@dataclass
class Drift:
    instance_label: str
    instance_line_no: str | None
    candidate_label: str | None        # closest template line (for the reviewer)
    candidate_line_no: str | None
    candidate_concept_key: str | None
    score: float
    suggested: str                     # 'same' (reword) | 'new'

@dataclass
class Report:
    matched: list = field(default_factory=list)
    drift: list = field(default_factory=list)
    absent: list = field(default_factory=list)
    @property
    def clean(self) -> bool:
        return not self.drift


def align(instance_rows, template, concept_map=None) -> Report:
    cmap = concept_map or {}
    used: set[int] = set()
    matched, unmatched = [], []
    # ---- PASS 1: auto-match only ----
    for r in instance_rows:
        n = _norm(r.label); ck = cmap.get(n); best = None
        for i, t in enumerate(template):                       # a. exact / known variant
            if i in used: continue
            if n == _norm(t["canonical_label"]) or (ck and t["concept_key"] == ck):
                best = (1.0, i); break
        if best is None and r.line_no:                         # b. line-number anchor
            for i, t in enumerate(template):
                if i in used: continue
                if str(t.get("line_no")) == str(r.line_no) and _sim(r.label, t["canonical_label"]) >= 0.5:
                    best = (0.95, i); break
        if best is None:                                       # c. high parent-aware fuzzy
            scored = []
            for i, t in enumerate(template):
                if i in used: continue
                s = _sim(r.label, t["canonical_label"])
                if r.parent_label and t.get("parent_label") and _norm(r.parent_label) == _norm(t["parent_label"]):
                    s = min(1.0, s + 0.05)
                scored.append((s, i))
            if scored:
                bs = max(scored, key=lambda x: x[0])
                if bs[0] >= HI: best = bs
        if best:
            used.add(best[1]); matched.append(Match(r.label, r.line_no, template[best[1]]["concept_key"], round(best[0], 2)))
        else:
            unmatched.append(r)
    # ---- PASS 2: pair each leftover with its closest remaining template row ----
    absent_idx = [i for i in range(len(template)) if i not in used]
    pairs = sorted(((_sim(r.label, template[i]["canonical_label"]), j, i)
                    for j, r in enumerate(unmatched) for i in absent_idx),
                   key=lambda x: x[0], reverse=True)
    pair_j, used_i, picked = {}, set(), set()
    for s, j, i in pairs:
        if j in picked or i in used_i: continue
        pair_j[j] = (i, s); picked.add(j); used_i.add(i)
    drift = []
    for j, r in enumerate(unmatched):
        if j in pair_j:
            i, s = pair_j[j]; t = template[i]
            drift.append(Drift(r.label, r.line_no, t["canonical_label"], t.get("line_no"),
                               t["concept_key"], round(s, 2), "same" if s >= LO else "new"))
        else:
            drift.append(Drift(r.label, r.line_no, None, None, None, 0.0, "new"))
    absent = [template[i] for i in absent_idx if i not in used_i]
    return Report(matched=matched, drift=drift, absent=absent)
