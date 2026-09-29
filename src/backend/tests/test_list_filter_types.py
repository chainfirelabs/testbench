"""Blank predicates must not compare typed SQL values with empty strings."""
import pytest
from sqlalchemy import Boolean, DateTime, Numeric, String, column
from sqlalchemy.dialects import postgresql
from app.services.list_filters import exclude_clause, include_clause


@pytest.mark.parametrize('kind', [Boolean, Numeric, DateTime])
@pytest.mark.parametrize('predicate', [include_clause, exclude_clause])
def test_typed_blank_predicates_use_null_only(kind, predicate):
    compiled = predicate(column('value', kind), [None, '']).compile(dialect=postgresql.dialect())
    assert not compiled.params
    assert 'NULL' in str(compiled)


@pytest.mark.parametrize('predicate', [include_clause, exclude_clause])
def test_text_blanks_also_match_empty_strings(predicate):
    compiled = predicate(column('value', String), [None]).compile(dialect=postgresql.dialect())
    assert '' in compiled.params.values()
