"""Render the real chart to check registry inheritance across workloads."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

CHART = Path(__file__).resolve().parents[1]
IMAGE_PATHS = [
    ('frontend', 'image'), ('backend', 'image'), ('mcp', 'image'),
    ('plugins', 'networkScan', 'image'), ('plugins', 'deviceInfo', 'image'),
    ('plugins', 'deviceInfo', 'researchImage'), ('plugins', 'reboot', 'image'),
    ('plugins', 'reboot', 'researchImage'),
]
BASE = {
    'global': {'imageRegistry': 'global.example/'},
    'applicationSecret': {'existingSecret': 'test'},
    'database': {'host': 'postgres', 'credentials': {'existingSecret': 'test'}},
    'config': {'frontendUrl': 'https://test.example', 'corsOrigins': 'https://test.example'},
    'schema': {'devices': {'source': 'configMap', 'existingConfigMap': 'testbench-device-schema'}},
    'mcp': {'enabled': True},
    'plugins': {
        'sharedSecret': {'existingSecret': 'test'},
        'networkScan': {'enabled': True},
        'deviceInfo': {'enabled': True, 'ai': {'url': 'https://ai.example', 'model': 'test', 'existingSecret': 'test'}},
        'reboot': {'enabled': True, 'ai': {'url': 'https://ai.example', 'model': 'test', 'existingSecret': 'test'}},
    },
}


def render(values):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as handle:
        json.dump(values, handle)
        handle.flush()
        return subprocess.run(['helm', 'template', 'testbench', str(CHART), '-f', handle.name],
                              capture_output=True, text=True)


@unittest.skipUnless(shutil.which('helm'), 'helm is required')
class ImageRegistryTests(unittest.TestCase):
    def test_replication_entity_selection_renders_and_rejects_invalid_lists(self):
        values = copy.deepcopy(BASE)
        values['replication'] = {
            'enabled': True, 'peerUrl': 'https://peer.example/api/v1',
            'token': {'existingSecret': 'replication-token'},
            'entities': ['tests', 'devices'],
        }
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name: TB_REPLICATION_ENTITIES, value: "[\\"tests\\",\\"devices\\"]"', result.stdout)
        for invalid in ([], ['devices', 'devices'], ['unknown']):
            with self.subTest(invalid=invalid):
                values['replication']['entities'] = invalid
                result = render(values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('/replication/entities', result.stderr)

    def test_replication_is_not_a_plugin(self):
        values = copy.deepcopy(BASE)
        values['plugins'] = {}  # No plugins, so no shared secret either.
        values['replication'] = {
            'enabled': True, 'peerUrl': 'https://peer.example/api/v1',
            'token': {'existingSecret': 'replication-token'},
        }
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('TB_PLUGINS: ""', result.stdout)
        self.assertIn('TB_REPLICATION_CONTROLLER_URL: "http://testbench-testbench-replication:8080"', result.stdout)
        self.assertNotIn('TB_PLUGIN_SHARED_SECRET', result.stdout)
        self.assertIn('path: /v1/health', result.stdout)
        values['replication']['mode'] = 'receive'
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('TB_REPLICATION_CONTROLLER_URL', result.stdout)
        self.assertNotIn("kind: Deployment\nmetadata:\n  name: testbench-testbench-replication", result.stdout)

    def test_old_plugins_replication_key_is_refused(self):
        values = copy.deepcopy(BASE)
        values['plugins']['replication'] = {'enabled': True}
        result = render(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('plugins.replication has moved to the top-level replication key', result.stderr)

    def test_object_storage_size_renders_as_decimal_integer(self):
        for size in (1048576, 10485760):
            with self.subTest(size=size):
                values = copy.deepcopy(BASE)
                values['backend'] = {'objectStorage': {'maxBytes': size}}
                result = render(values)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'TB_OBJECT_STORAGE_MAX_BYTES: "{size}"', result.stdout)

    def test_artifact_limits_adjust_proxy_and_config(self):
        for size in (104857600, 1073741824):
            values = copy.deepcopy(BASE)
            values['backend'] = {'objectStorage': {'artifactMaxBytes': size}}
            result = render(values)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'TB_ARTIFACT_MAX_BYTES: "{size}"', result.stdout)
            self.assertIn(f'client_max_body_size {size + 1048576};', result.stdout)
        values['backend']['objectStorage']['artifactMaxBytes'] = 0
        self.assertNotEqual(render(values).returncode, 0)

    def test_default_database_source_does_not_require_configmap(self):
        values = copy.deepcopy(BASE)
        values.pop('schema')
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('TB_DEVICE_SCHEMA_PATH', result.stdout)
        self.assertNotIn('name: testbench-device-schema', result.stdout)

    def test_device_schema_uses_external_configmap(self):
        result = render(copy.deepcopy(BASE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name: testbench-device-schema', result.stdout)
        self.assertNotIn('kind: DeviceSchema', result.stdout)

    def test_authentik_ca_is_mounted_in_the_backend(self):
        values = copy.deepcopy(BASE)
        values['authentik'] = {'tls': {'existingConfigMap': 'authentik-ca', 'key': 'root.pem'}}
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name: SSL_CERT_FILE', result.stdout)
        self.assertIn('value: /etc/testbench/authentik-ca/root.pem', result.stdout)
        self.assertIn('name: authentik-ca', result.stdout)
        self.assertIn('name: authentik-ca\n            items:', result.stdout)
        self.assertIn('key: root.pem', result.stdout)

    def test_authentik_ca_requires_a_key(self):
        values = copy.deepcopy(BASE)
        values['authentik'] = {'tls': {'existingConfigMap': 'authentik-ca', 'key': ''}}
        result = render(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('/authentik/tls/key', result.stderr)

    def test_global_ca_reaches_https_clients_and_overrides_legacy_backend_ca(self):
        values = copy.deepcopy(BASE)
        values['global']['tls'] = {'existingConfigMap': 'testbench-ca', 'key': 'roots.pem'}
        values['authentik'] = {'tls': {'existingConfigMap': 'old-authentik-ca', 'key': 'old.pem'}}
        values['replication'] = {
            'enabled': True, 'peerUrl': 'https://peer.example/api/v1',
            'token': {'existingSecret': 'replication-token'},
        }
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('name: SSL_CERT_FILE'), 6)
        self.assertEqual(result.stdout.count('value: /etc/testbench/ca/roots.pem'), 30)
        self.assertEqual(result.stdout.count('name: global-ca'), 12)
        self.assertIn('name: TB_WORKER_CA_CONFIGMAP, value: "testbench-ca"', result.stdout)
        self.assertNotIn('old-authentik-ca', result.stdout)

    def test_global_ca_requires_a_key(self):
        values = copy.deepcopy(BASE)
        values['global']['tls'] = {'existingConfigMap': 'testbench-ca', 'key': ''}
        result = render(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('/global/tls/key', result.stderr)

    def test_reboot_image_capture_defaults_off_and_can_be_enabled(self):
        values = copy.deepcopy(BASE)
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name: TB_REBOOT_CAPTURE_IMAGES, value: "false"', result.stdout)

        values['plugins']['reboot']['captureImages'] = True
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name: TB_REBOOT_CAPTURE_IMAGES, value: "true"', result.stdout)

    def test_gui_managed_ai_does_not_require_helm_endpoint_or_secret(self):
        values = copy.deepcopy(BASE)
        for plugin in ('deviceInfo', 'reboot'):
            values['plugins'][plugin]['ai'] = {'url': '', 'model': '', 'existingSecret': ''}
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name: TB_REBOOT_AI_REPEAT_MODEL, value: ""', result.stdout)

    def test_partial_helm_ai_configuration_is_rejected(self):
        values = copy.deepcopy(BASE)
        values['plugins']['reboot']['ai']['model'] = ''
        result = render(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must either both be set', result.stderr)

    def test_device_info_image_capture_defaults_off_and_can_be_enabled(self):
        values = copy.deepcopy(BASE)
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name: TB_INFO_CAPTURE_IMAGES, value: "false"', result.stdout)

        values['plugins']['deviceInfo']['captureImages'] = True
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name: TB_INFO_CAPTURE_IMAGES, value: "true"', result.stdout)

    def test_configmap_source_requires_external_name(self):
        values = copy.deepcopy(BASE)
        values['schema']['devices']['existingConfigMap'] = ''
        result = render(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('existingConfigMap', result.stderr)

    def test_global_pull_policy_reaches_every_workload_and_worker(self):
        values = copy.deepcopy(BASE)
        values['global']['imagePullPolicy'] = 'Always'
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Six controllers/deployments, two audit-cleanup workloads, and two
        # database-schema Job containers.
        self.assertEqual(result.stdout.count('imagePullPolicy: Always'), 10)
        for variable in (
            'TB_SCAN_WORKER_IMAGE_PULL_POLICY',
            'TB_INFO_RESEARCH_IMAGE_PULL_POLICY',
            'TB_REBOOT_PLUGIN_IMAGE_PULL_POLICY',
            'TB_REBOOT_RESEARCH_IMAGE_PULL_POLICY',
        ):
            self.assertIn(variable, result.stdout)
        self.assertGreaterEqual(result.stdout.count('value: "Always"'), 4)

    def test_image_pull_policy_overrides_global(self):
        values = copy.deepcopy(BASE)
        values['global']['imagePullPolicy'] = 'Always'
        values['frontend'] = {'image': {'pullPolicy': 'Never'}}
        values['plugins']['reboot']['researchImage'] = {'pullPolicy': 'Never'}
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('imagePullPolicy: Never', result.stdout)
        self.assertIn('name: TB_REBOOT_RESEARCH_IMAGE_PULL_POLICY, value: "Never"', result.stdout)

    def test_invalid_global_pull_policy_is_rejected(self):
        values = copy.deepcopy(BASE)
        values['global']['imagePullPolicy'] = 'Sometimes'
        result = render(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('imagePullPolicy', result.stderr)

    def test_all_image_paths(self):
        for mode in ('inherit', 'override', 'empty', 'digest'):
            with self.subTest(mode=mode):
                values = copy.deepcopy(BASE)
                expected = []
                for index, path in enumerate(IMAGE_PATHS):
                    target = values
                    for part in path:
                        target = target.setdefault(part, {})
                    target.update(repository=f'team/image-{index}', tag='test')
                    prefix = 'global.example/'
                    if mode == 'override':
                        target['registry'] = f'private-{index}.example:5000/'
                        prefix = f'private-{index}.example:5000/'
                    elif mode == 'empty':
                        target['registry'] = ''
                        target['repository'] = f'own.example/team/image-{index}'
                        prefix = ''
                    suffix = ':test'
                    if mode == 'digest':
                        target['registry'] = 'digest.example'
                        target['digest'] = 'sha256:' + 'a' * 64
                        prefix = 'digest.example/'
                        suffix = '@' + target['digest']
                    expected.append(prefix + target['repository'] + suffix)
                result = render(values)
                self.assertEqual(result.returncode, 0, result.stderr)
                for image in expected:
                    self.assertIn(image, result.stdout)
                if mode != 'inherit':
                    self.assertNotIn('global.example/', result.stdout)
                # Backend image is used by deployment and schema Jobs; reboot
                # and scan controller images are also passed to their workers.
                for index in (1, 3, 6):
                    self.assertGreaterEqual(result.stdout.count(expected[index]), 2)

    def test_mixed_overrides_preserve_other_defaults(self):
        values = copy.deepcopy(BASE)
        values['plugins']['deviceInfo']['researchImage'] = {
            'registry': 'private.example', 'repository': 'browser', 'tag': 'custom'}
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('private.example/browser:custom', result.stdout)
        self.assertIn('global.example/chainfirelabs/testbench/backend:', result.stdout)
        self.assertIn('global.example/oh-my-pi:', result.stdout)

    def test_registry_must_be_string(self):
        values = copy.deepcopy(BASE)
        values['frontend'] = {'image': {'registry': 123}}
        result = render(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('registry', result.stderr)


if __name__ == '__main__':
    unittest.main()
