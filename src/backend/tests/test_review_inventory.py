"""Inventory validation, role snapshots, scans and export regression cases."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from app.api import devices, plugins, software
from app.services import device_schema as schema


def field(key, **changes):
    return NS(**{
        'key': key, 'label': key, 'role': None, 'visible': True, 'writable': True,
        'stored': True, 'storage': 'data', 'required': False, 'sensitive': False,
        'field_type': 'text', 'validation': {}, 'options': (), 'unique_value': False,
        **changes,
    })


@pytest.mark.parametrize('spec,value', [
    ({'field_type': 'number'}, 'not-a-number'),
    ({'validation': {'max_length': 3}}, 'too-long'),
    ({'field_type': 'select', 'options': ('one', 'two')}, 'three'),
    ({'field_type': 'date'}, 'yesterday'),
])
def test_type_change_revalidates_existing_values(spec, value):
    with patch.object(schema, 'effective_field_map', return_value={'value': field('value', **spec)}):
        with pytest.raises(schema.DeviceValidationError):
            schema.validate_device_document(Mock(), 'destination', {}, partial=True,
                                            existing={'value': value}, enforce_required=True)


def test_type_change_normalizes_retained_values_and_preserves_unrelated_data():
    device = NS(id='device', data={'count': '3', 'old_type_value': {'saved': True}, 'last_seen': '2026-09-07'})
    device.merge_data = lambda values: device.data.update(values)
    catalog = {'count': field('count', field_type='number'),
               'last_seen': field('last_seen', field_type='date', writable=False)}
    with patch.object(schema, 'effective_field_map', return_value=catalog):
        devices._write_document(Mock(), device, {}, NS(id='destination'), creating=False, changing_type=True)
    assert device.data == {'count': 3, 'old_type_value': {'saved': True}, 'last_seen': '2026-09-07'}


def test_type_change_allows_a_patch_to_correct_an_incompatible_value():
    with patch.object(schema, 'effective_field_map', return_value={'count': field('count', field_type='number')}):
        assert schema.validate_device_document(Mock(), 'destination', {'count': '5'}, partial=True,
            existing={'count': 'invalid'}, enforce_required=True) == {'count': 5}


def test_normal_partial_update_does_not_revalidate_unrelated_legacy_values():
    with patch.object(schema, 'effective_field_map', return_value={'count': field('count', field_type='number')}):
        assert schema.validate_device_document(Mock(), 'type', {}, partial=True,
            existing={'count': 'invalid'}) == {}


@pytest.mark.parametrize('concurrent_edit', [False, True])
def test_invocation_snapshots_output_roles_and_preserves_conflict_detection(concurrent_edit):
    roles = ['discovery_firmware', 'discovery_hardware', 'discovery_lan_mac', 'discovery_wan_mac']
    manifest = {'id': 'device-info-agent', 'optional_output_roles': roles, 'minimum_output_roles': 1,
                'actions': [{'id': 'research', 'entity': 'devices', 'scope': 'row'}]}
    fields = tuple(field(role, role=role) for role in roles)
    device = NS(id='d', unique_id='d', device_type_id='router-id',
                device_type_key='router', device_type_label='Router',
                online_status=True, _data={'discovery_firmware': 'old'})
    user = NS(id='u', username='tester', role='tester')
    request = NS(state=NS(), client=None, headers={})
    db = Mock()
    db.scalars.return_value.all.return_value = [device]
    with (patch.object(plugins.registry, 'manifest', return_value=manifest),
          patch.object(plugins, 'validate_plugin_invocation', return_value=(manifest['actions'][0], [device])),
          patch.object(plugins, 'fields_for_device', return_value=fields),
          patch.object(plugins.registry, 'request', return_value={'run_id': 'run'}) as send):
        plugins.invoke_action('device-info-agent', 'research', plugins.InvokeIn(entity_id='d'), request, db, user)
    sent = send.call_args.args[3]['entities'][0]['_plugin_roles']
    assert sent == {
        'discovery_firmware': 'old', 'discovery_hardware': None,
        'discovery_lan_mac': None, 'discovery_wan_mac': None,
    }
    if concurrent_edit:
        device._data['discovery_firmware'] = 'human-edit'
    db.scalar.return_value = None
    db.get.return_value = device
    finding = {'value': 'new', 'confidence': 1.0, 'source': 'test', 'starting_value': sent['discovery_firmware']}
    body = plugins.DiscoveryResultIn(device_id='d', run_id='run', findings={'discovery_firmware': finding})
    with patch.object(plugins, 'fields_for_device', return_value=fields):
        result = plugins.device_info_results(body, db, 'device-info-agent')
    assert ('discovery_firmware' in result['conflicts']) is concurrent_edit
    assert device._data['discovery_firmware'] == ('human-edit' if concurrent_edit else 'new')


def test_lan_and_wan_mac_findings_update_their_own_fields():
    fields = (
        field('lan_mac', role='discovery_lan_mac'),
        field('wan_mac', role='discovery_wan_mac'),
    )
    device = NS(id='d', unique_id='router-1', _data={})
    db = Mock()
    db.scalar.return_value = None
    db.get.return_value = device
    finding = lambda value, source: {
        'value': value, 'confidence': 1.0, 'source': source, 'starting_value': None,
    }
    body = plugins.DiscoveryResultIn(
        device_id='d', run_id='run',
        findings={
            'discovery_lan_mac': finding('94-A6-7E-E3-FB-73', 'LAN Port'),
            'discovery_wan_mac': finding('94:A6:7E:E3:FB:75', 'Internet Port'),
        },
    )

    with patch.object(plugins, 'fields_for_device', return_value=fields):
        result = plugins.device_info_results(body, db, 'device-info-agent')

    assert device._data == {
        'lan_mac': '94:a6:7e:e3:fb:73',
        'wan_mac': '94:a6:7e:e3:fb:75',
    }
    assert result['accepted']['discovery_lan_mac']['field'] == 'lan_mac'
    assert result['accepted']['discovery_wan_mac']['field'] == 'wan_mac'


def test_unscoped_default_export_includes_type_only_fields():
    device = NS(id='d', unique_id='phone-1', device_type_key='phone', data={'imei': '123'})
    fields = [field('unique_id', storage='column'), field('imei')]
    db = Mock()
    db.scalars.return_value.all.return_value = [device]
    with (patch.object(devices, '_union_fields', return_value=fields),
          patch.object(devices, '_query_devices', return_value=Mock()),
          patch.object(devices, '_device_dict', return_value={'unique_id': 'phone-1', 'misc_data': {}})):
        response = devices.export_devices(format='json', columns_mode='type', expand_misc=True,
                                           request=None, db=db, user=None)
    assert json.loads(response.body) == [{'device_type': 'phone', 'unique_id': 'phone-1', 'imei': '123'}]


@pytest.mark.parametrize('fleet', [False, True])
def test_scans_accept_type_only_addresses(fleet):
    db = Mock()
    db.scalars.return_value = ['router']
    with patch.object(devices, 'get_effective_fields', side_effect=lambda db, key:
                      [field('mgmt', role='scan_address_lan')] if key == 'router' else []):
        devices._require_scan_addresses(db, None if fleet else NS(device_type_id='router'))


def test_single_scan_respects_type_visibility_override():
    with patch.object(devices, 'get_effective_fields', return_value=[field('mgmt', role='scan_address_lan', visible=False)]):
        with pytest.raises(HTTPException) as caught:
            devices._require_scan_addresses(Mock(), NS(device_type_id='router'))
        assert caught.value.status_code == 409


def test_single_scan_refuses_a_disabled_network_scan_plugin():
    device = NS(id='device', device_type_id='router', unique_id='router-1')
    action = {
        'plugin_id': 'network-scan', 'id': 'network-scan.scan-device',
        'available': False,
        'unavailable_reason': 'Network Scan is not enabled for the Router device type',
    }
    with (patch.object(devices, '_get_device', return_value=device),
          patch.object(devices, 'get_available_actions', return_value=[action]),
          patch.object(devices, 'scan_device') as scan):
        with pytest.raises(HTTPException) as caught:
            devices.scan_single_device('device', NS(), Mock(), NS(role='admin'))
    assert caught.value.status_code == 403
    assert caught.value.detail == action['unavailable_reason']
    scan.assert_not_called()


def test_single_scan_requires_a_healthy_network_scan_plugin():
    device = NS(id='device', device_type_id='router', unique_id='router-1')
    with (patch.object(devices, '_get_device', return_value=device),
          patch.object(devices, 'get_available_actions', return_value=[]),
          patch.object(devices, 'scan_device') as scan):
        with pytest.raises(HTTPException) as caught:
            devices.scan_single_device('device', NS(), Mock(), NS(role='admin'))
    assert caught.value.status_code == 409
    scan.assert_not_called()


def test_query_lookup_passes_exact_name_then_reads_by_id():
    db = Mock()
    db.scalar.return_value = NS(id='real-id')
    with patch.object(devices, 'get_device', return_value={'id': 'real-id'}) as get:
        assert devices.lookup_device('rack/../#2', db, None) == {'id': 'real-id'}
        get.assert_called_once_with('real-id', db=db, user=None)
    assert 'rack/../#2' in db.scalar.call_args.args[0].compile().params.values()
    with (patch.object(software, '_latest_of', return_value=NS(id='software')) as latest,
          patch.object(software, '_annotate', return_value=[{'id': 'software'}])):
        assert software.lookup_software('tool/a#2', db, None) == {'id': 'software'}
        latest.assert_called_once_with(db, 'tool/a#2')


def test_ignored_readonly_patch_cannot_skip_retained_value_validation():
    fields = {'count': field('count', field_type='number', writable=False)}
    with patch.object(schema, 'effective_field_map', return_value=fields):
        with pytest.raises(schema.DeviceValidationError):
            schema.validate_device_document(Mock(), 'destination', {'count': '5'}, partial=True,
                existing={'count': 'invalid'}, enforce_required=True)


def test_required_sensitive_field_cannot_be_satisfied_by_retained_null():
    fields = {'password': field('password', sensitive=True, required=True)}
    with patch.object(schema, 'effective_field_map', return_value=fields):
        with pytest.raises(schema.DeviceValidationError):
            schema.validate_device_document(Mock(), 'destination', {}, partial=True,
                existing={'password': None}, enforce_required=True)
