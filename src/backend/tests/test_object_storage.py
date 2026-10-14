"""External S3 output reads reject oversized and non-text objects."""

import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.api.devices import get_device_output
from app.services.object_storage import OutputError, object_location, read_ascii_output


class ObjectStorageTests(unittest.TestCase):
    @patch("app.api.devices.read_ascii_output")
    @patch("app.api.devices.get_effective_fields")
    @patch("app.api.devices._get_device")
    @patch("app.api.devices.settings")
    def test_device_output_requires_enabled_assigned_field(self, settings, get_device, fields, read):
        settings.object_storage_enabled = False
        db = Mock()
        with self.assertRaises(HTTPException) as error:
            get_device_output("device-1", db=db, user=Mock())
        self.assertEqual(error.exception.status_code, 404)
        get_device.assert_not_called()

        settings.object_storage_enabled = True
        get_device.return_value = SimpleNamespace(device_type_id="type-1", data={"output": "s3://logs-bucket/a.txt"})
        fields.return_value = [SimpleNamespace(key="output", visible=False, protected=True)]
        with self.assertRaises(HTTPException) as error:
            get_device_output("device-1", db=db, user=Mock())
        self.assertEqual(error.exception.status_code, 404)
        read.assert_not_called()

        fields.return_value[0].visible = True
        read.return_value = "hello\n"
        self.assertEqual(get_device_output("device-1", db=db, user=Mock()), {"text": "hello\n"})
        read.assert_called_once_with("s3://logs-bucket/a.txt")

    def test_only_s3_object_references(self):
        self.assertEqual(object_location("s3://logs-bucket/device%201.txt"), ("logs-bucket", "device 1.txt"))
        for value in ("https://other.example/file", "s3://bucket", "s3://bucket/key?token=x", "s3://bucket:9000/key", "s3://bucket/%00"):
            with self.subTest(value=value), self.assertRaises(OutputError):
                object_location(value)

    @patch("app.services.object_storage.boto3.client")
    @patch("app.services.object_storage.settings")
    def test_bounded_ascii_reads(self, settings, client_factory):
        settings.object_storage_url = "https://s3.example.com"
        settings.object_storage_bucket = "logs-bucket"
        settings.object_storage_access_key = "access"
        settings.object_storage_secret_key = "secret"
        settings.object_storage_region = "us-east-1"
        settings.object_storage_max_bytes = 8
        client = client_factory.return_value
        with self.assertRaises(OutputError):
            read_ascii_output("s3://other-bucket/a.txt")
        client_factory.assert_not_called()
        client.head_object.return_value = {"ContentLength": 8}

        for data in (b"ok\nline\n", b""):
            client.get_object.return_value = {"Body": io.BytesIO(data)}
            self.assertEqual(read_ascii_output("s3://logs-bucket/a.txt"), data.decode("ascii"))
            client.get_object.assert_called_with(Bucket="logs-bucket", Key="a.txt")

        for data in (b"\xff", b"a\x00b", b"123456789"):
            client.get_object.return_value = {"Body": io.BytesIO(data)}
            with self.subTest(data=data), self.assertRaises(OutputError):
                read_ascii_output("s3://logs-bucket/a.txt")

        client.head_object.return_value = {"ContentLength": 9}
        client.get_object.reset_mock()
        with self.assertRaises(OutputError) as error:
            read_ascii_output("s3://logs-bucket/a.txt")
        self.assertEqual(error.exception.status, 413)
        client.get_object.assert_not_called()


if __name__ == "__main__":
    unittest.main()
