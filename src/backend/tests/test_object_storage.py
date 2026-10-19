"""Object references and the ASCII rule test output is held to."""

import unittest

from app.services.object_storage import OutputError, ascii_text, object_location


class ObjectStorageTests(unittest.TestCase):
    def test_only_s3_object_references(self):
        self.assertEqual(object_location("s3://logs-bucket/device%201.txt"), ("logs-bucket", "device 1.txt"))
        for value in ("https://other.example/file", "s3://bucket", "s3://bucket/key?token=x", "s3://bucket:9000/key", "s3://bucket/%00"):
            with self.subTest(value=value), self.assertRaises(OutputError):
                object_location(value)

    def test_output_is_ascii_text_with_ordinary_whitespace(self):
        for data in (b"ok\nline\n", b"tab\there\r\n\f", b""):
            self.assertEqual(ascii_text(data), data.decode("ascii"))
        for data in (b"\xff", b"a\x00b", b"del\x7f"):
            with self.subTest(data=data), self.assertRaises(OutputError):
                ascii_text(data)


if __name__ == "__main__":
    unittest.main()
