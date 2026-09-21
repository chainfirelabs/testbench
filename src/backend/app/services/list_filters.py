"""Helpers shared by server-paged grid checklist filters.

A checklist filter sends the side of its selection that is shorter. Unticking
a couple of values out of hundreds sends `exclude__<field>`; clearing the lot
and ticking one sends `include__<field>`. The two are not merely inverses —
see `include_clause` — but the reason for having both is plain arithmetic: the
list travels in the query string, and a column with five hundred distinct
values cannot put four hundred and ninety-nine of them in a URL. nginx rejects
the request at around eight kilobytes and the grid is left showing nothing.
"""

import json
from typing import Any

from sqlalchemy import and_, or_


def excluded_values(raw: Any) -> list[Any]:
    if not isinstance(raw, str):
        return []
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return values if isinstance(values, list) else []


def exclude_clause(expression, values: list[Any]):
    """Exclude exact values, with the checklist's blank item covering NULL/empty."""
    blanks = any(value is None or value == "" for value in values)
    concrete = [value for value in values if value is not None and value != ""]
    if concrete and not blanks:
        return or_(expression.is_(None), expression.notin_(concrete))
    clauses = []
    if concrete:
        clauses.append(expression.notin_(concrete))
    if blanks:
        clauses.extend((expression.is_not(None), expression != ""))
    return and_(*clauses) if clauses else None


def include_clause(expression, values: list[Any]):
    """Keep only these exact values, with the checklist's blank item covering
    NULL/empty.

    Not the inverse of `exclude_clause`, and deliberately so. Excluding says
    "hide these and show whatever else turns up", which lets a value nobody has
    seen yet appear on its own. Including says "show these and nothing else" —
    which is what someone who cleared the list and ticked one thing means, and
    the only reading under which a value they never saw stays hidden.
    """
    blanks = any(value is None or value == "" for value in values)
    concrete = [value for value in values if value is not None and value != ""]
    clauses = []
    if concrete:
        clauses.append(expression.in_(concrete))
    if blanks:
        clauses.append(or_(expression.is_(None), expression == ""))
    if not clauses:
        # Nothing ticked at all: the honest answer is no rows, not every row.
        return expression.is_(None) & expression.is_not(None)
    return or_(*clauses)
