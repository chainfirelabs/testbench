"""Typed predicates shared by server-paged grid checklist filters."""

import json
from typing import Any

from fastapi import HTTPException, Request

from sqlalchemy import String, and_, or_


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
        clauses.append(expression.is_not(None))
        if isinstance(expression.type, String):
            clauses.append(expression != "")
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
        clauses.append(
            or_(expression.is_(None), expression == "")
            if isinstance(expression.type, String) else expression.is_(None)
        )
    if not clauses:
        # Nothing ticked at all: the honest answer is no rows, not every row.
        return expression.is_(None) & expression.is_not(None)
    return or_(*clauses)


def checklist_query(request: Request, body: dict[str, str] | None = None) -> dict[str, str]:
    """Large checklists may travel in a POST body; ordinary controls stay in
    the query string and retain FastAPI's existing validation and defaults.
    """
    if body and any(not key.startswith(("include__", "exclude__")) for key in body):
        raise HTTPException(422, "The query body may contain only checklist filters")
    return {**request.query_params, **(body or {})}
