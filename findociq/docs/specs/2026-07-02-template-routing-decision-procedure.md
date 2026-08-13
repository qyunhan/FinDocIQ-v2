# Template routing decision procedure (deep-reasoner deliverable, 2026-07-02)

Design for reconciling ruled-fragment counts with template identity in
pipeline/route/scan.py. Verified against: uob_12.9 route JSON (10/4/10/4 stays
generic), OCBC_4Q25_Pillar3.pdf p96-98 + p7 (positive / narrative / near-miss
controls), DBS 4-page NSFR scratch (split-page + false-continuation).

## Conclusion
Route by TEMPLATE-EVIDENCE first, fragment-count second. A page is a template
table iff BOTH pass, independent of find_tables():
  S2 — >=80% of template_col canonical headers found as token-subsequences in
       the header band (band = above the first ladder row); group_label hit
       lowers the leaf requirement by 2.
  S3 — a line-number LADDER: template_row.line_no tokens sharing one x-column
       (mode x0, tol 2.5pt), longest-increasing-by-top, covering >=90% of the
       form. Fallback when no printed numbers: ordered canonical-label matching.
Only then collapse the N ruled fragments into ONE TEMPLATE unit. The _TYPES
keyword (discover.py) is confirmatory only — NEVER a gate (OCBC p97 running
header has no 'nsfr' at all; section title lives on narrative p96).

## Key rules
- Instance boundaries across pages = LADDER RESTART AT LINE 1, not the (cont'd)
  header flag. Evidence: DBS p3 restarts at line 1 while its header still says
  "(continued)" — it is a NEW period instance; p2 resuming at 23 after p1's 22
  is a true continuation (first == succ(open.last_line)).
- Unit bbox = WORD-ANCHORED (ladder ∪ matched header words ∪ overlapping
  fragments, extended to last row bottom, 6pt pad) — NEVER fragment union.
  Evidence: OCBC p97 fragment union (283,331,557,737) misses headers
  (y≈105-135), line 1 (y=167), and the whole label column (x≈77).
- Period per instance = first date-regex match ABOVE the column headers on the
  instance's first page (OCBC p97 "31 December 2025", p98 "30 September 2025").
- A numbered list alone NEVER routes TEMPLATE (S2 required). Negative control:
  OCBC p7 Key Metrics mentions NSFR, numbers 1-20, "Weighted" — fails S2+S3.
- Everything failing the test falls through to the existing route() UNCHANGED
  → uob 12.9 stays BORDERED_MULTI per-fragment (heterogeneous widths 5-22 cols,
  no shared ladder, no sa_cr template in registry).

## Pseudocode
```
def template_test(page_words, T):
    cands  = [w for w in page_words if w.text in T.line_nos]
    x_col  = mode(round(w.x0) for w in cands)
    ladder = longest_increasing_by_top([w for w in cands if abs(w.x0-x_col) <= 2.5])
    cov    = len(ladder) / len(T.line_nos)
    band   = words_above(ladder[0].top) if ladder else words_above(0.35 * page.height)
    leaf   = count(h subsequence-in band for h in T.leaf_headers)
    s2     = leaf >= ceil(0.8*len(T.leaf_headers)) or (group_hit(band,T) and leaf >= len(T.leaf_headers)-2)
    if cov < 0.5:  # bank omits printed line numbers
        ladder, cov = ordered_label_matches(page_words, T.labels)
    return s2, ladder, cov

def route_page(page, open_instance):
    for T in TEMPLATES:                      # registry small; test all types
        s2, ladder, cov = template_test(page.words, T)
        first, last = ladder[0].line_no, ladder[-1].line_no
        if s2 and cov >= 0.9 and first == T.line_nos[0]:
            close(open_instance); open_instance = new_instance(T, page)
            emit TEMPLATE;  close if last == T.line_nos[-1];  return
        if open_instance and open_instance.type == T and cov > 0 \
           and first == succ(open_instance.last_line):
            open_instance.pages.append(page); emit TEMPLATE_CONT
            close if last == T.line_nos[-1];  return
        if s2 and 0.5 <= cov < 0.9:
            emit GENERIC + flag('template_suspect', T, cov); return
        if cov >= 0.9 and not s2 and not open_instance:
            emit GENERIC + flag('template_headers_missing', T); return
    emit route(n_tables, bscore)             # existing fallthrough, unchanged
```
normalize(): lower, unicode-fold (≥ etc.), collapse non-alnum; headers matched
as TOKEN SUBSEQUENCES (they wrap across lines: "6 months to" / "<1 yr").

## Thresholds
0.9 ladder (~3/34 lines noise tolerance) · 0.8 headers (1/5 wrap loss) ·
x-tol 2.5pt (matches scan.py ALIGN_TOL). Calibrated on n=3 docs — re-verify
when KM1/LCR added (shorter ladders shift weight onto S2).

## Failure modes → GENERIC + review flag
1. no printed line numbers & label-fallback <0.9 → flag template_labels_only
2. partial ladder 0.5-0.9 with headers → flag template_suspect (never half-consolidate)
3. ladder w/o headers, nothing open (orphan continuation) → flag template_headers_missing
4. two instances on one page → split at mid-page ladder restart if x-cols disjoint,
   else flag template_multi_instance_page
5. header wording drift beyond normalization → case 2; remedy = header-alias
   table grown via review (mirror concept_map)
6. image-only pages → all signals zero → existing BORDERLESS/MinerU path

## Caveats
- Original OCBC_NSFR.pdf / uob_12.9.pdf were not at their stated paths at design
  time; positive control verified on OCBC_4Q25_Pillar3.pdf p97/98 (same signature).
- DBS stray "1"/"20" tokens on continuation pages: ladder MUST use
  longest-increasing-subsequence at the modal x-column, not raw set-match.
