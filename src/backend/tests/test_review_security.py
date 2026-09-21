"""Regression coverage for account boundaries and spreadsheet-safe exports."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api import auth, users
from app.api.deps import get_current_user
from app.db import get_db
from app.models import Role, User
from app.services.permissions import PERMISSION_KEYS
from app.services.io import parse_csv, template_csv, to_csv_rows


def account(**changes):
    return User(**{
        'id': 'user-1', 'username': 'person', 'auth_provider': 'authentik',
        'authentik_sub': 'subject', 'role': 'readonly', 'is_active': True,
        'created_at': datetime(2026, 9, 7), **changes,
    })


def admin_role():
    """The seeded admin role, as `role_permissions` would read it back."""
    return Role(id='role-admin', slug='admin', name='Administrator',
                permissions=list(PERMISSION_KEYS), is_builtin=True)


@pytest.mark.parametrize('active', [False, True])
def test_oidc_preserves_administrative_deactivation(active):
    user = account(is_active=active)
    db = Mock()
    db.scalar.return_value = user
    response = Mock()
    response.read.return_value = json.dumps({'id_token': 'issuer-token'}).encode()
    context = Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=False))
    request = SimpleNamespace(state=SimpleNamespace(), client=None, headers={})
    with (patch.object(auth, '_oidc_enabled', return_value=True),
          patch.object(auth, '_issuer_urls', return_value={'token': 'https://issuer.test/token'}),
          patch.object(auth.pyjwt, 'decode', side_effect=[
              {'state': 'state', 'nonce': 'nonce', 'code_verifier': 'verifier'},
              {'aud': auth.settings.authentik_client_id, 'sub': 'subject', 'nonce': 'nonce'},
          ]),
          patch.object(auth.urllib.request, 'urlopen', return_value=context),
          patch.object(auth, '_extract_groups', return_value=[]),
          # Which role the groups map to is a different test; this one is about
          # a deactivated account staying deactivated through an SSO login.
          patch.object(auth, '_resolve_role', return_value='readonly'),
          patch.object(auth, 'create_token', return_value='session-token') as mint):
        if active:
            assert auth.oidc_callback('code', 'state', request, db, 'state').status_code == 307
            mint.assert_called_once()
            db.commit.assert_called_once()
        else:
            with pytest.raises(HTTPException) as caught:
                auth.oidc_callback('code', 'state', request, db, 'state')
            assert caught.value.status_code == 403
            assert user.is_active is False
            mint.assert_not_called()
            db.commit.assert_not_called()


@pytest.mark.parametrize('method,status', [('api_key', 403), ('mcp_internal', 403), ('jwt', 200)])
@pytest.mark.parametrize('body', [{'role': 'admin'}, {'is_active': True}])
def test_account_changes_require_an_admin_session(method, status, body):
    app = FastAPI()
    app.include_router(users.router)
    db = Mock()
    target = account(id='target', auth_provider='local', is_active=False)
    db.get.return_value = target
    # What the caller may do is now read off the roles table, so the mock has
    # to answer for the admin role the caller holds.
    db.scalar.return_value = admin_role()

    def current_user(request: Request):
        request.state.auth_method = method
        return account(id='admin', role='admin')

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        response = client.patch('/users/target', json=body)
    assert response.status_code == status, response.text
    if status == 403:
        db.get.assert_not_called()
        db.commit.assert_not_called()
    else:
        db.commit.assert_called_once()


@pytest.mark.parametrize('aware', [False, True])
def test_users_online_status_handles_database_and_aware_timestamps(aware):
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    recent = now - timedelta(minutes=2)
    old = now - timedelta(days=10)
    if not aware:
        recent, old = recent.replace(tzinfo=None), old.replace(tzinfo=None)
    db = Mock()
    db.scalars.return_value.all.return_value = [
        account(last_login_at=recent), account(last_login_at=old), account(last_login_at=None),
    ]
    with patch.object(users, 'utcnow', return_value=now):
        assert [u.is_online for u in users.list_users(db, account())] == [True, False, False]


def test_formula_headers_are_safe_and_round_trip_without_collisions():
    keys = ['=1+1', '+1', '-1', '@SUM(A1)', '\tformula', '\rformula', "'=1+1", "''=1+1", 'normal']
    row = {key: f'value-{index}' for index, key in enumerate(keys)}
    exported = to_csv_rows([row], keys)
    import csv
    import io
    headers = next(csv.reader(io.StringIO(exported)))
    assert all(not key.startswith(('=', '+', '-', '@', '\t', '\r')) for key in headers)
    assert len(set(headers)) == len(keys)
    assert parse_csv(exported.encode()) == [row]
    assert next(csv.reader(io.StringIO(template_csv(keys)))) == headers
