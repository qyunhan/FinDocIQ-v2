"""Parse the extract step's HTML into element/row/cell records.

The slide extract prompt emits ONE
`<table data-element="TYPE" data-title="...">` per distinct element on the
slide, with the per-bar/per-series attributes `data-kind` and `data-sign` on the
waterfall rows. This turns that into plain dicts.

STDLIB ONLY, deliberately. `pandas.read_html` needs lxml or bs4 and neither is
installed in this venv; adding a parser dependency to read our own output would
be the wrong trade. `html.parser` handles the subset the prompt emits.

Pure — no IO, no API. Tested by `test_html_tables.py`.
"""
from __future__ import annotations

from html.parser import HTMLParser


class _TableParser(HTMLParser):
    """Collect <table> elements as {attrs, header_rows, body_rows}.

    A cell is (text, attrs). Rows keep <th> and <td> together in document order
    — the prompt uses <th> for headers but a value cell inside <thead> is still
    positional information we must not drop.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self._t: dict | None = None
        self._row: list | None = None
        self._cell: list[str] | None = None
        self._cell_attrs: dict = {}
        self._row_attrs: dict = {}
        self._in_head = False

    # -- structure ---------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._t = {"attrs": a, "head": [], "body": []}
        elif self._t is None:
            return
        elif tag == "thead":
            self._in_head = True
        elif tag == "tbody":
            self._in_head = False
        elif tag == "tr":
            # data-kind / data-sign sit on the <tr> (the waterfall bar), not on
            # its cells — keep them with the row, not the first <td>.
            self._row, self._row_attrs = [], a
        elif tag in ("td", "th"):
            self._cell, self._cell_attrs = [], a

    def handle_endtag(self, tag):
        if tag == "table" and self._t is not None:
            self.tables.append(self._t)
            self._t, self._in_head = None, False
        elif self._t is None:
            return
        elif tag == "thead":
            self._in_head = False
        elif tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            (self._row if self._row is not None else []).append(
                (text, self._cell_attrs))
            self._cell, self._cell_attrs = None, {}
        elif tag == "tr" and self._row is not None:
            bucket = self._t["head"] if self._in_head else self._t["body"]
            bucket.append({"attrs": self._row_attrs, "cells": self._row})
            self._row, self._row_attrs = None, {}

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_elements(html: str) -> list[dict]:
    """HTML -> [{element_type, element_title, columns, rows}].

    `columns` is the LAST header row's texts (the leaf headers — a group header
    row above it spans them and is not per-column). `rows` is
    [{label, cells: [str], kind, sign}] where `label` is the first cell of the
    row and `cells` the rest, so a row lines up with `columns`.

    An element with no header row gets `columns == []`; a kpi_grid with a single
    value column legitimately looks like that.
    """
    p = _TableParser()
    p.feed(html or "")
    out: list[dict] = []
    for t in p.tables:
        columns = [txt for txt, _ in t["head"][-1]["cells"]] if t["head"] else []
        # the leading header cell labels the row axis, not a period — drop it so
        # `columns` aligns with the cells that follow each row's label
        if columns:
            columns = columns[1:]
        rows = []
        for r in t["body"]:
            cells, attrs = r["cells"], r["attrs"]
            if not cells:
                continue
            rows.append({
                "label": cells[0][0],
                "cells": [txt for txt, _ in cells[1:]],
                # row-level first (that is where the prompt puts them), then any
                # cell that carries them, so a stray cell-level attr still lands
                "kind": attrs.get("data-kind") or next(
                    (c[1].get("data-kind") for c in cells if c[1].get("data-kind")), None),
                "sign": attrs.get("data-sign") or next(
                    (c[1].get("data-sign") for c in cells if c[1].get("data-sign")), None),
            })
        out.append({
            "element_type": t["attrs"].get("data-element") or "other",
            "element_title": t["attrs"].get("data-title") or "",
            "columns": columns,
            "rows": rows,
        })
    return out
