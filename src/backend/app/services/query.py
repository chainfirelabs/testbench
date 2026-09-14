"""Shared helpers for query parameters and row-at-a-time writes."""

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import inspect
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import UnaryExpression


def sortable_columns(model: Any) -> set[str]:
    """The column names a caller may sort by."""
    return set(inspect(model).columns.keys())


def order_by(model: Any, sort: str | None, order: str | None, default: str) -> UnaryExpression:
    """Resolve `?sort=&order=` to an ORDER BY clause.

    `getattr(model, sort)` would hand back any class attribute the caller names
    — `metadata`, `registry`, a relationship — and ordering by one of those
    raises inside SQLAlchemy, turning a bad request into a 500. Only mapped
    columns are sortable; anything else falls back to the default column.
    """
    name = sort if sort in sortable_columns(model) else default
    column = getattr(model, name)
    return column.desc() if order == "desc" else column.asc()


@contextmanager
def row_scope(db: Session) -> Iterator[None]:
    """Apply one import/bulk row inside its own savepoint.

    Rows are validated and applied one at a time but share a session, so
    without this a row that violates a constraint is not discovered until the
    final commit — which then fails for the whole file, including every row
    that was fine and whose count the response had already reported as applied.
    A savepoint keeps the damage to the row that caused it.
    """
    with db.begin_nested():
        yield


def row_error(exc: Exception) -> str:
    """A short, safe message for a row that failed.

    `str()` on a SQLAlchemy error carries the whole statement and every bound
    parameter, and these strings go back in the API response and into the audit
    log. The driver's own message says what was wrong without the payload.
    """
    orig = getattr(exc, "orig", None)
    if orig is not None:
        return str(orig).strip().splitlines()[0]
    return str(exc)
