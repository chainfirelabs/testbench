"""Helpers shared by server-paged grid checklist filters."""

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
