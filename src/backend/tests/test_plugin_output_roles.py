"""Output role readiness follows each device type, including mixed fleet pages."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from app.api import plugins
from app.services import device_schema as schema


ROLES = [
    'discovery_firmware', 'discovery_hardware',
    'discovery_lan_mac', 'discovery_wan_mac',
]
MANIFEST = {
    'id': 'device-info-agent',
    'optional_output_roles': ROLES,
    'minimum_output_roles': 1,
    'actions': [{'id': 'research', 'entity': 'devices', 'scope': 'row'}],
}


def fields(*roles, visible=True):
    return tuple(SimpleNamespace(role=role, visible=visible) for role in roles)


class OutputRoleTests(TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.scalars.return_value = [
            SimpleNamespace(id='router', key='router'),
            SimpleNamespace(id='phone', key='phone'),
        ]
        self.schemas = {'router': fields(*ROLES), 'phone': ()}
        self.device = SimpleNamespace(
            id='device-1', unique_id='router-1', device_type_id='router',
            device_type_label='Router',
        )
        for patcher in [
            patch.object(plugins.registry, 'manifests', return_value=[MANIFEST]),
            patch.object(plugins.registry, 'manifest', return_value=MANIFEST),
            patch.object(plugins.registry, 'refresh'),
            patch.object(plugins, 'get_allowed_plugins', return_value={'device-info-agent': Mock(configuration={})}),
            patch.object(schema, 'get_allowed_plugins', return_value={'device-info-agent': Mock(configuration={})}),
            patch.object(plugins, 'get_effective_fields', side_effect=lambda db, key: self.schemas[key]),
            patch.object(schema, 'get_effective_fields', side_effect=lambda db, key: self.schemas[key]),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def listing(self, device_type=None):
        return plugins.list_actions(entity='devices', device_type=device_type, db=self.db, user=None)[0]

    def test_type_specific_fields_make_action_available(self):
        self.assertNotIn('unavailable_reason', self.listing('router'))
        self.assertEqual(self.listing('router')['unavailable_reasons_by_type'], {})

    def test_mixed_fleet_does_not_block_a_configured_type(self):
        result = self.listing()
        self.assertNotIn('unavailable_reason', result)
        self.assertEqual(set(result['unavailable_reasons_by_type']), {'phone'})
        self.assertIn('at least 1', self.listing('phone')['unavailable_reason'])

    def test_each_type_can_configure_a_different_single_output(self):
        self.schemas = {'router': fields(ROLES[0]), 'phone': fields(ROLES[2])}
        result = self.listing()
        self.assertNotIn('unavailable_reason', result)
        self.assertEqual(result['unavailable_reasons_by_type'], {})

    def test_hidden_output_fields_still_block_the_action(self):
        self.schemas['router'] = fields(*ROLES, visible=False)
        self.assertIn('at least 1', self.listing('router')['unavailable_reason'])

    def test_empty_output_values_do_not_block_research(self):
        actions = schema.get_available_actions(self.db, self.device)
        self.assertTrue(actions[0]['available'])
        _, devices = schema.validate_plugin_invocation(
            self.db, 'device-info-agent', 'research', [self.device],
        )
        self.assertEqual(devices, [self.device])
        self.assertEqual(schema.missing_plugin_roles(self.db, 'device-info-agent', 'router'), [])

    def test_missing_outputs_agree_in_listing_detail_policy_and_invocation(self):
        self.schemas['router'] = fields()
        reason = self.listing('router')['unavailable_reason']
        action = schema.get_available_actions(self.db, self.device)[0]
        self.assertFalse(action['available'])
        self.assertEqual(action['unavailable_reason'], reason)
        self.assertEqual(
            schema.missing_plugin_roles(self.db, 'device-info-agent', 'router'),
            ['one of: discovery_firmware or discovery_hardware or discovery_lan_mac or discovery_wan_mac'],
        )
        with self.assertRaises(schema.PluginPolicyError) as caught:
            schema.validate_plugin_invocation(self.db, 'device-info-agent', 'research', [self.device])
        self.assertIn(reason, str(caught.exception))
