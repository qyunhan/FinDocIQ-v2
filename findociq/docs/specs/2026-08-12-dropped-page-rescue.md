# Dropped-page rescue (2026-08-12)

**Status:** implemented — `pipeline/pass2/extract.py`, `extract_unit_chunked`.
**Tests:** `pipeline/pass2/test_dropped_page_rescue.py` (9 assertions).

## The defect

A spanning unit longer than `chunk_size` pages is split by
`extract_unit_chunked` into chunks of ≤2 pages, one model call each. A call can
come back having answered for only SOME of its pages, and nothing detected that.

Measured on `DBS_2Q26_performance_summary`. DBS prints its overview twice —
half-year basis on pages 4-6, quarter basis on pages 7-8:

    chunks/c1  pages [4, 5]  -> 1 table  'OVERVIEW'                    (45 rows)
    chunks/c2  pages [6, 7]  -> 1 table  'DBS GROUP HOLDINGS LTD ...'  ( 8 rows)
    chunks/c3  pages [8]     -> 1 table  'Per share data ($)3'         ( 7 rows)

`c2` covered two pages and returned one table: page 6's per-share block. Page 7's
`Selected income statement items ($m)` (2Q26 / 2Q25 / %chg / 1Q26 / %chg) was
dropped. The page text is present in `chunks/c2/pages.pdf` — the page was sent
and the answer simply omitted it.

Cost: the entire quarter basis was absent from the database. `FS_INCOME_SELECTED`
for that document carried `1H` and `2H` spans only; across the whole document 632
cells span `1H` against 10 spanning `2Q`; every page-7 figure (5,624 / 3,483 /
6,093 / 2,347 / 3,746 …) returned 0 rows from `cell_fact`. The Key Financial
Highlights axis carries a `2Q26` column, so DBS's quarterly income lines rendered
empty.

## Why the table count cannot be the signal

"Fewer tables than pages" is NOT evidence. One table legitimately spans a chunk —
that is the entire reason spanning units exist, and `_merge_tables_into` already
rejoins continuations by title + column signature. A count rule would re-extract
every genuine spanning table, doubling calls and inviting the continuation merge
to mis-fire.

## The rule

Per PAGE, not per chunk. After a multi-page chunk returns:

    pages_with_no_output(page_texts, tables) ->
        for each page:
          tokens = distinct numeric tokens in the page's text (len >= 2 chars)
          if len(tokens) < 12          -> skip: prose page, owes no table
          if tokens & returned_cells   -> skip: represented somewhere
          else                         -> the page produced NOTHING

Each flagged page is re-asked for ALONE (`chunks/c{n}p{page}`), and its tables are
appended before `_merge_tables_into`, so a continuation is still rejoined by the
existing rule.

**Thresholds, and why.** Single-character numbers are excluded because footnote
markers, list numbering and column ordinals are numeric but say nothing. The
density floor of 12 distinct tokens separates a table page from a prose page:
measured on the corpus, the thinnest real exhibit (DBS `Per share data`, 7 rows ×
3 columns) carries 21 tokens, while the fattest prose page (DBS 2Q26 p6 notes
block) carries 6. Comparison is on the PRINTED form (`5,624`), not a parsed
float, because that is what a cell holds verbatim.

## Failure handling

A rescue call that raises is caught, warned loudly, and the unit continues with
the tables it already has. Losing a chunk's real output because a rescue failed
would be worse than the gap it was trying to close.

## Verification

* Replayed against the real failing artifacts with NO model call: page 7 flagged
  (115 numeric tokens, zero overlap), page 6 not (19 tokens, full overlap).
* Swept every chunk in `outputs/` with complete artifacts — 11 chunk dirs, 6 of
  them multi-page. **Exactly 1 flagged**, the known DBS page 7. Zero false
  positives.
* 9 unit assertions including the two that matter: a genuinely spanning table
  flags neither page, and a notes page is never rescued.

## Not done

The rescue fires at EXTRACTION time, so it does not retro-fix documents already
in `compiled_fs.db`. DBS 2Q26 needs a re-extract of `overview_p4-8` for its
quarter basis to appear — see `docs/TO_FIX.md` §0.

## The batch path needs it too

`pass2/batch.py` assembles spanning units in its OWN loop — it collects each
chunk's `Extraction` and calls `_merge_tables_into` directly, so it never goes
through `extract_unit_chunked` and did not inherit the guard.

That is not a corner case: it is the path that produced every document ingested
from 2026-08-07 onward. Those runs are identifiable on disk — their audit dirs
hold only `parsed.json` + `meta.json`, with **no** `prompt.txt`, `response.txt`
or `pages.pdf`, because the batch path writes a reduced trail. Measured:
`uob_2Q26/audit` has 0 of each, `dbs_2Q26` has all three.

So the same rescue is wired into the batch assembly. Two differences:

* the chunk's own pages are recorded as `S["chunk_pages"][ci]` when the chunk
  lands, because the assembly loop otherwise sees only merged tables;
* the rescue call is SYNC even in batch mode — it is one page, and a batch
  round-trip is minutes.

## Consequence for the corpus sweep

The sweep reported above found only 11 chunk dirs with complete artifacts out of
78. That is the same fact seen from the other side: batch-path chunks have no
`pages.pdf`, so they cannot be re-checked offline. The 6 multi-page chunks it
COULD check are all sync-path. Whether any batch-path document lost a page the
way DBS 2Q26 did is therefore still unknown, and cannot be settled from the
stored artifacts — only by re-extracting.
