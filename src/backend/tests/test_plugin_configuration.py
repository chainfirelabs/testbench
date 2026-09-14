import pytest
from app.services.plugin_configuration import validate_plugin_configuration
from app.services.device_schema_yaml import DocumentError, parse_document


@pytest.mark.parametrize('configuration', [{}, {'method': None}, {'method': 'ai'}, {'method': 'ssh', 'ssh_command': 'sudo -n reboot', 'ssh_port': 2222}, {'ssh_command': None, 'ssh_port': None}])
def test_reboot_configuration(configuration):
    validate_plugin_configuration('device-reboot', configuration)


@pytest.mark.parametrize('configuration', [{'method': 'auto'}, {'method': ''}, {'ssh_command': ''}, {'ssh_command': '  '}, {'ssh_command': 12}, {'ssh_port': 0}, {'ssh_port': 65536}, {'ssh_port': True}, []])
def test_invalid_reboot_configuration(configuration):
    with pytest.raises(ValueError):
        validate_plugin_configuration('device-reboot', configuration)


def test_yaml_rejects_invalid_reboot_method():
    from test_device_schema_yaml import VALID
    with pytest.raises(DocumentError, match='Reboot method'):
        parse_document(VALID.replace('timeoutSeconds: 60', 'method: auto'))


@pytest.mark.parametrize('configuration', [{}, {'http_port': 8080}, {'https_port': 8443}, {'http_port': None, 'https_port': None}])
def test_device_info_port_configuration(configuration):
    validate_plugin_configuration('device-info-agent', configuration)


@pytest.mark.parametrize('configuration', [{'http_port': 0}, {'https_port': 65536}, {'http_port': '8080'}, {'https_port': False}])
def test_invalid_device_info_port_configuration(configuration):
    with pytest.raises(ValueError):
        validate_plugin_configuration('device-info-agent', configuration)
