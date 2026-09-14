"""Worker credentials must remain revoked after terminal state transitions."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv('TB_SCAN_INTERVAL_MINUTES', '0')
    monkeypatch.setenv('TB_PLUGIN_SHARED_SECRET', 'controller-secret')
    monkeypatch.setitem(sys.modules, 'kubernetes', SimpleNamespace(client=Mock(), config=Mock()))
    spec = importlib.util.spec_from_file_location('scan_server_test',
        Path(__file__).parents[1] / 'network_scan' / 'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RUNS['run'] = {
        'run_id': 'run', 'state': 'running', 'token': 'worker-token',
        'entities': [{'id': 'device-1', '_scan_addresses': {'mgmt': '192.0.2.1'}}],
    }
    response = Mock()
    response.json.return_value = {'updated': ['device-1']}
    monkeypatch.setattr(module.httpx, 'post', Mock(return_value=response))
    return module


@pytest.mark.parametrize('state', ['completed', 'cancelled', 'failed'])
@pytest.mark.parametrize('authorization', [None, '', 'Bearer ', 'Bearer worker-token'])
def test_terminal_runs_reject_input_and_completion(server, state, authorization):
    server.RUNS['run'].update(state=state, token='')
    with TestClient(server.app) as client:
        headers = {'Authorization': authorization} if authorization is not None else {}
        assert client.get('/worker/v1/runs/run', headers=headers).status_code == 401
        assert client.post('/worker/v1/runs/run/complete', headers=headers, json={'items': []}).status_code == 401
    assert server.RUNS['run']['state'] == state
    server.httpx.post.assert_not_called()


@pytest.mark.parametrize('authorization', [None, 'worker-token', 'Basic worker-token', 'Bearer wrong', 'Bearer é'])
def test_active_run_requires_a_valid_bearer_credential(server, authorization):
    with pytest.raises(HTTPException) as error:
        server.worker_input('run', authorization)
    assert error.value.status_code == 401


def test_terminal_state_rejects_even_a_stale_nonempty_token(server):
    server.RUNS['run']['state'] = 'cancelled'
    with pytest.raises(HTTPException) as error:
        server.worker_complete('run', {'items': []}, 'Bearer worker-token')
    assert error.value.status_code == 409
    server.httpx.post.assert_not_called()


@pytest.mark.parametrize('items', [
    [{'device_id': 'outside-run'}],
    [{'device_id': 'device-1'}, {'device_id': 'device-1'}],
    [None], {'device_id': 'device-1'}, [{'device_id': []}],
])
def test_completion_cannot_escape_its_device_snapshot(server, items):
    with pytest.raises(HTTPException) as error:
        server.worker_complete('run', {'items': items}, 'Bearer worker-token')
    assert error.value.status_code == 422
    assert server.RUNS['run']['state'] == 'running'
    server.httpx.post.assert_not_called()


def test_completion_uses_controller_snapshot_and_revokes_token(server):
    assert server.worker_input('run', 'Bearer worker-token')['entities'][0]['id'] == 'device-1'
    result = server.worker_complete('run', {'items': [{
        'device_id': 'device-1', 'starting_addresses': {'mgmt': 'attacker-value'},
        'probed': True, 'online': True,
    }]}, 'Bearer worker-token')
    assert result == {'updated': ['device-1']}
    item = server.httpx.post.call_args.kwargs['json']['items'][0]
    assert item['starting_addresses'] == {'mgmt': '192.0.2.1'}
    assert server.RUNS['run']['state'] == 'completed'
    assert server.RUNS['run']['token'] == ''
    with pytest.raises(HTTPException):
        server.worker_complete('run', {'items': []}, 'Bearer worker-token')
    assert server.httpx.post.call_count == 1


def test_cancel_revokes_token_and_failure_cannot_reopen_it(server):
    server.cancel('run', 'network-scan', 'controller-secret')
    assert server.RUNS['run']['state'] == 'cancelled'
    assert server.RUNS['run']['token'] == ''
    server._fail_run('run', 'job disappeared')
    assert server.RUNS['run']['state'] == 'cancelled'
    with pytest.raises(HTTPException):
        server.worker_input('run', None)
