import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.config import settings
from app.services.device_info import device_url, launch_device_info, missing_required_fields, render_prompt


class DeviceInfoTests(unittest.TestCase):
    def setUp(self):
        self.device = SimpleNamespace(wan_ip="192.0.2.4", make="Acme", model="Router", serial="abc")

    def test_required_fields_are_configurable(self):
        with patch.object(settings, "device_info_required_fields", "wan_ip,make,serial"):
            self.assertEqual(missing_required_fields(self.device), [])
            self.device.serial = ""
            self.assertEqual(missing_required_fields(self.device), ["serial"])

    def test_url_field_and_prompt_tokens_are_configurable(self):
        with (
            patch.object(settings, "device_info_url_field", "wan_ip"),
            patch.object(settings, "device_info_prompt", "Browse {device_url}; data={device_json}"),
        ):
            self.assertEqual(device_url(self.device), "http://192.0.2.4")
            prompt = render_prompt(self.device, {"make": "Acme"})
        self.assertEqual(prompt, 'Browse http://192.0.2.4; data={"make": "Acme"}')

    @patch("app.services.device_info._docker_request")
    def test_launch_uses_local_image_detached_with_auto_remove(self, request):
        request.side_effect = [{"Id": "container-123"}, {}]
        with (
            patch.object(settings, "device_info_image", "oh-my-pi:latest"),
            patch.object(settings, "device_info_forward_env", "LITELLM_API_KEY"),
            patch.dict("os.environ", {"LITELLM_API_KEY": "secret"}),
        ):
            container_id = launch_device_info(self.device, {"make": "Acme"})

        self.assertEqual(container_id, "container-123")
        create_payload = request.call_args_list[0].args[2]
        self.assertEqual(create_payload["Image"], "oh-my-pi:latest")
        self.assertEqual(create_payload["Env"], ["LITELLM_API_KEY=secret"])
        self.assertTrue(create_payload["HostConfig"]["AutoRemove"])
        self.assertEqual(request.call_args_list[1].args[:2], ("POST", "/v1.41/containers/container-123/start"))


if __name__ == "__main__":
    unittest.main()
