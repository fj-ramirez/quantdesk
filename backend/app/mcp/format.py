"""Rendering result sets for a model to read (T82).

**A markdown table beats JSON here**, and the reason is token economy: JSON repeats every column
name on every row, so a 20-column, 100-row result spends most of its budget restating the
schema. A table states it once. It is also what models are most fluent at reading back.

Two rules that are about honesty rather than formatting:

**Truncation is always stated.** A model shown 100 of 134,377 rows, with nothing saying so, will
summarise them as though they were all of them — and will do it confidently. The note goes above
the table, where it cannot be skipped by something reading top-down.

**`None` renders as an explicit marker, never as blank or zero.** Across this desk a missing
value and a zero are different facts (GEX invariant 3, the terminal's whole point-in-time model,
the research module's null Sharpes), and a blank cell in a markdown table is indistinguishable
from an empty string. `·` is used because it survives being read aloud and cannot be mistaken
for a number.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.mcp.db import QueryResult

__all__ = ["render_result", "render_rows", "render_value"]

NULL_MARKER = "·"

#: Long free-text columns (a strategy note, a brief paragraph) blow the budget for no gain in a
#: table. Truncated with a marker so the model knows to fetch the row individually.
MAX_CELL = 160


def render_value(value: Any) -> str:
    if value is None:
        return NULL_MARKER
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        # Enough precision for a Sharpe or a beta; not so much that a table becomes unreadable.
        return f"{value:.4g}"
    if isinstance(value, dt.datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value)
    # Pipes would break the table structure.
    text = text.replace("|", "\\|").replace("\n", " ")
    if len(text) > MAX_CELL:
        text = text[: MAX_CELL - 1] + "…"
    return text


def render_rows(columns: list[str], rows: list[tuple]) -> str:
    if not columns:
        return "(no columns)"
    if not rows:
        return "(no rows)"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(render_value(cell) for cell in row) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def render_result(result: QueryResult, *, note: str | None = None) -> str:
    """The table, preceded by anything the model must know before reading it."""
    parts: list[str] = []
    if note:
        parts.append(note)
    if result.truncated:
        parts.append(
            f"**Truncated to {result.limit} rows.** There are more rows than shown; any summary "
            f"of this table describes the first {result.limit} only. Narrow the query or raise "
            f"`limit` (maximum 1000)."
        )
    parts.append(render_rows(result.columns, result.rows))
    if not result.truncated and result.rows:
        parts.append(f"({len(result.rows)} row{'s' if len(result.rows) != 1 else ''})")
    return "\n\n".join(parts)
