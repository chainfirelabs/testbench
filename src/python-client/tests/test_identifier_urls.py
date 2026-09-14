"""Names must remain data, rather than becoming fragments, queries or paths."""
import httpx
import pytest

from testbench_client import TestBench


@pytest.mark.parametrize('name', ['rack#2', 'rack?two=2', 'rack%2F2', 'rack 2', 'rack/2', '..'])
def test_device_reads_actions_and_writes_address_the_exact_record(name):
    seen = []
    lookup = '/' in name or name in {'.', '..'}
    target = {'id': 'target-id', 'unique_id': name, 'status': 'available'}

    def handler(request):
        seen.append(request)
        assert not request.url.fragment
        if request.url.path == '/api/v1/devices/lookup/by-unique-id':
            assert request.method == 'GET'
            assert request.url.params['unique_id'] == name
            return httpx.Response(200, json=target)
        assert not request.url.query
        key = 'target-id' if lookup else name
        assert request.url.path in {f'/api/v1/devices/{key}', f'/api/v1/devices/{key}/actions'}
        return httpx.Response(200, json={'actions': []} if request.url.path.endswith('/actions') else target)

    with TestBench(base_url='http://test.test/api/v1', api_key='tb_prefix_secret',
                   transport=httpx.MockTransport(handler)) as tb:
        assert tb.devices.get(name).unique_id == name
        assert tb.devices.actions(name) == []
        assert tb.devices.set_status(name, 'available').id == 'target-id'
    assert any(request.method == 'PATCH' for request in seen)


@pytest.mark.parametrize('name', ['tool#2', 'tool?edition=2', 'tool%2F2', 'tool/2', '.'])
def test_software_lookup_and_versions_preserve_the_name(name):
    lookup = '/' in name or name in {'.', '..'}
    target = {'id': 'software-id', 'name': name, 'version': '2'}

    def handler(request):
        assert not request.url.fragment
        if request.url.path == '/api/v1/software/lookup/by-name':
            assert request.url.params['name'] == name
            return httpx.Response(200, json=target)
        assert not request.url.query
        key = 'software-id' if lookup else name
        assert request.url.path in {f'/api/v1/software/{key}', f'/api/v1/software/{key}/versions'}
        return httpx.Response(200, json=[target] if request.url.path.endswith('/versions') else target)

    with TestBench(base_url='http://test.test/api/v1', api_key='tb_prefix_secret',
                   transport=httpx.MockTransport(handler)) as tb:
        assert tb.software.get(name).name == name
        assert tb.software.versions(name)[0].id == 'software-id'
