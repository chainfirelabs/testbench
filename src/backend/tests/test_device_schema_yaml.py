"""Parsing and validating the versioned DeviceSchema document.

A ConfigMap is untrusted configuration. Everything here runs before a single
row is written, so a document that cannot be understood leaves the last
published schema exactly as it was.
"""

import textwrap
import unittest

import yaml

from app.services.device_schema_yaml import API_VERSION, DocumentError, parse_document, parse_uploaded_document

VALID = textwrap.dedent(f"""
    apiVersion: {API_VERSION}
    kind: DeviceSchema
    metadata:
      name: default
    spec:
      fields:
        - key: location
          label: Location
          type: text
        - key: imei
          label: IMEI
          type: text
          indexed: true
          unique: true
        - key: wan_ip
          label: WAN IP
          type: text
          role: scan_address_wan
        - key: carrier
          label: Carrier
          type: select
          options: [AT&T, T-Mobile, Verizon]
      globalFields:
        - key: location
          position: 10
      deviceTypes:
        - key: router
          label: Routers
          fields:
            - key: wan_ip
              required: true
          plugins:
            - id: network-scan
              enabled: true
            - id: device-reboot
              enabled: true
              config:
                timeoutSeconds: 60
        - key: mobile
          label: Mobile Phones
          fields:
            - key: imei
              required: true
            - key: carrier
""")


def document(body: str) -> dict:
    return parse_document(textwrap.dedent(body))


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.doc = parse_document(VALID)

    def test_fields_are_normalised(self):
        imei = next(f for f in self.doc["fields"] if f["key"] == "imei")
        self.assertTrue(imei["unique_value"])
        # A unique field is always indexed: the index is what enforces it.
        self.assertTrue(imei["indexed"])
        self.assertEqual(imei["field_type"], "text")

    def test_a_role_carries_meaning_rather_than_a_field_name(self):
        wan = next(f for f in self.doc["fields"] if f["key"] == "wan_ip")
        self.assertEqual(wan["plugin_role"], "scan_address_wan")

    def test_choices_are_kept_in_order(self):
        carrier = next(f for f in self.doc["fields"] if f["key"] == "carrier")
        self.assertEqual(carrier["options"], ["AT&T", "T-Mobile", "Verizon"])

    def test_device_type_fields_and_plugins(self):
        router = next(t for t in self.doc["device_types"] if t["key"] == "router")
        self.assertEqual([a["key"] for a in router["assignments"]], ["wan_ip"])
        self.assertTrue(router["assignments"][0]["required"])
        self.assertEqual(
            {p["plugin_id"]: p["enabled"] for p in router["plugins"]},
            {"network-scan": True, "device-reboot": True},
        )
        self.assertEqual(router["plugins"][1]["configuration"], {"timeoutSeconds": 60})

    def test_the_same_document_always_produces_the_same_generation(self):
        self.assertEqual(parse_document(VALID)["generation"], self.doc["generation"])
        changed = parse_document(VALID.replace("Routers", "Edge Routers"))
        self.assertNotEqual(changed["generation"], self.doc["generation"])

    def test_exported_configmap_can_be_uploaded_directly(self):
        config_map = yaml.safe_dump({
            "apiVersion": "v1", "kind": "ConfigMap",
            "data": {"device-schema.yaml": VALID},
        })
        self.assertEqual(parse_uploaded_document(config_map), self.doc)

    def test_uploaded_configmap_requires_the_schema_key(self):
        with self.assertRaisesRegex(DocumentError, "device-schema.yaml"):
            parse_uploaded_document("apiVersion: v1\nkind: ConfigMap\ndata: {}\n")

    def test_a_label_defaults_to_the_key(self):
        doc = document(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              fields:
                - key: rack_unit
              globalFields: [rack_unit]
        """)
        self.assertEqual(doc["fields"][0]["label"], "Rack Unit")
        # A bare key is a valid assignment: nothing to override, just include it.
        self.assertEqual(doc["global_assignments"][0]["key"], "rack_unit")

    def test_overrides_and_fields_are_the_same_operation(self):
        doc = document(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              fields:
                - key: serial_number
              deviceTypes:
                - key: server
                  overrides:
                    - key: serial_number
                      required: true
        """)
        server = doc["device_types"][0]
        self.assertEqual(server["assignments"][0]["key"], "serial_number")
        self.assertTrue(server["assignments"][0]["required"])

    def test_protected_system_fields_may_be_assigned_without_redeclaring_them(self):
        doc = document(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              globalFields:
                - key: status
                  position: 5
        """)
        self.assertEqual(doc["global_assignments"][0]["key"], "status")

    def test_pruning_is_opt_in(self):
        self.assertFalse(self.doc["prune"])
        self.assertTrue(document(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              prune: true
        """)["prune"])


class RejectionTests(unittest.TestCase):
    def assert_rejected(self, body: str, fragment: str):
        with self.assertRaises(DocumentError) as caught:
            document(body)
        self.assertIn(fragment, str(caught.exception))

    def test_wrong_api_version(self):
        self.assert_rejected("apiVersion: v1\nkind: DeviceSchema\nspec: {}", "apiVersion")

    def test_wrong_kind(self):
        self.assert_rejected(f"apiVersion: {API_VERSION}\nkind: Devices\nspec: {{}}", "kind")

    def test_malformed_yaml(self):
        self.assert_rejected("apiVersion: [unclosed", "invalid YAML")

    def test_an_invalid_field_key(self):
        self.assert_rejected(
            f"apiVersion: {API_VERSION}\nkind: DeviceSchema\nspec:\n  fields:\n    - key: Rack Unit\n",
            "lowercase",
        )

    def test_a_duplicate_field(self):
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              fields:
                - key: rack
                - key: rack
        """, "declared twice")

    def test_an_unsupported_field_type(self):
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              fields:
                - key: rack
                  type: colour
        """, "unsupported type")

    def test_assigning_a_field_that_was_never_defined(self):
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              globalFields: [rack]
        """, "never defined")

    def test_a_device_type_referencing_an_undefined_field(self):
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              deviceTypes:
                - key: router
                  fields: [imei]
        """, "never defined")

    def test_a_field_key_the_envelope_owns(self):
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              fields:
                - key: device_type
        """, "reserved")

    def test_a_reserved_device_type_key(self):
        # /devices/type/uncategorized already means "devices with no type".
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              deviceTypes:
                - key: uncategorized
        """, "reserved")

    def test_an_invalid_device_type_key(self):
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              deviceTypes:
                - key: Mobile_Phones
        """, "lowercase")

    def test_a_duplicate_device_type(self):
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              deviceTypes:
                - key: router
                - key: router
        """, "declared twice")

    def test_a_plugin_without_an_id(self):
        self.assert_rejected(f"""
            apiVersion: {API_VERSION}
            kind: DeviceSchema
            spec:
              deviceTypes:
                - key: router
                  plugins:
                    - enabled: true
        """, "plugin id")
