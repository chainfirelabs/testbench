"""Exercise the resolver without needing an MCP transport or a live API."""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def resolver(monkeypatch):
    # Only the transport types are substituted. All lookup logic is loaded
    # from the production module and executes real HTTPX URL construction.
    transport_types = types.ModuleType('tb_mcp.client')
    transport_types.ApiClient = object
    transport_types.NotFound = type('NotFound', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'tb_mcp.client', transport_types)
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    spec = importlib.util.spec_from_file_location('tb_mcp._resolve_test',
        Path(__file__).parents[1] / 'tb_mcp' / 'resolve.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('kind', ['devices', 'software'])
@pytest.mark.parametrize('name', ['name#2', 'name?version=2', 'name%2F2', 'name/2', '..'])
def test_resolve_preserves_identifiers(resolver, kind, name):
    target = {'id': 'target', 'unique_id': name, 'name': name, 'version': '2'}
    lookup = '/' in name or name in {'.', '..'}

    def handler(request):
        assert not request.url.fragment
        if lookup:
            path, param = ('by-unique-id', 'unique_id') if kind == 'devices' else ('by-name', 'name')
            assert request.url.path == f'/api/v1/{kind}/lookup/{path}'
            assert request.url.params[param] == name
        else:
            assert request.url.path == f'/api/v1/{kind}/{name}'
            assert not request.url.query
        return httpx.Response(200, json=target)

    async def check():
        async with httpx.AsyncClient(base_url='http://test.test/api/v1', transport=httpx.MockTransport(handler)) as http:
            class Client:
                async def get(self, path, params=None):
                    return (await http.get(path, params=params)).json()
            resolve = resolver.resolve_device if kind == 'devices' else resolver.resolve_software
            assert (await resolve(Client(), name))['id'] == 'target'
    asyncio.run(check())
