"""Checklist filters that name what to keep rather than what to drop.

The filter sends the shorter side of the selection. Unticking a few values
sends those few as exclusions; clearing the column and ticking one sends that
one as an inclusion. The reason is the query string: a column with five hundred
distinct values cannot put four hundred and ninety-nine of them in a URL — the
proxy answers 414 and the grid shows nothing, which looks exactly like a filter
that matched nothing.

Including is not the inverse of excluding. Excluding hides what it names and
lets anything else through, a value this list has never seen included;
including shows only what it names.
"""

from test_device_schema_api import SchemaCase


class SoftwareIncludeFilterTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name in ("backup-restore", "netguard", "iperf3", "wireshark"):
            cls.post("/api/v1/software", {"name": name, "version": "1.0"})

    @classmethod
    def listed(cls, query):
        page = cls.get(f"/api/v1/software?latest_only=true&{query}").json()
        return sorted(item["name"] for item in page["items"])

    def test_including_one_name_returns_only_it(self):
        self.assertEqual(self.listed('include__name=["backup-restore"]'), ["backup-restore"])

    def test_including_several_returns_those(self):
        self.assertEqual(
            self.listed('include__name=["backup-restore","iperf3"]'),
            ["backup-restore", "iperf3"],
        )

    def test_including_nothing_returns_nothing(self):
        """Not everything: a checklist with no box ticked is showing no rows,
        and the filter has to say the same."""
        self.assertEqual(self.listed("include__name=[]"), [])

    def test_excluding_still_works_the_way_it_did(self):
        self.assertEqual(
            self.listed('exclude__name=["netguard","iperf3","wireshark"]'),
            ["backup-restore"],
        )

    def test_the_two_forms_agree_on_the_same_selection(self):
        """Same intent from either end, when the column's values are all known."""
        self.assertEqual(
            self.listed('include__name=["backup-restore"]'),
            self.listed('exclude__name=["netguard","iperf3","wireshark"]'),
        )


class DeviceIncludeFilterTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("/api/v1/devices", {"unique_id": "d-cisco", "make": "Cisco"})
        cls.post("/api/v1/devices", {"unique_id": "d-dell", "make": "Dell"})
        cls.post("/api/v1/devices", {"unique_id": "d-blank"})

    @classmethod
    def listed(cls, query):
        page = cls.get(f"/api/v1/devices?{query}").json()
        return sorted(item["unique_id"] for item in page["items"])

    def test_including_a_value_returns_only_its_rows(self):
        self.assertEqual(self.listed('include__make=["Cisco"]'), ["d-cisco"])

    def test_the_blank_entry_selects_the_rows_with_no_value(self):
        self.assertEqual(self.listed("include__make=[null]"), ["d-blank"])
        self.assertEqual(self.listed('include__make=[""]'), ["d-blank"])

    def test_a_value_and_the_blank_together(self):
        self.assertEqual(
            self.listed('include__make=["Cisco",null]'), ["d-blank", "d-cisco"],
        )

    def test_including_differs_from_excluding_for_unseen_values(self):
        """The distinction that matters: excluding Dell leaves the blank row
        showing, because excluding only hides what it names."""
        self.assertEqual(self.listed('exclude__make=["Dell"]'), ["d-blank", "d-cisco"])
        self.assertEqual(self.listed('include__make=["Cisco"]'), ["d-cisco"])


class BooleanChecklistTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_visible("online_status")
        from app.db import SessionLocal
        from app.models import Device
        from sqlalchemy import select
        for name, value in (("online", True), ("offline", False), ("unknown", None)):
            response = cls.post("/api/v1/devices", {"unique_id": name, "make": "Cisco"})
            assert response.status_code == 201, response.text
            with SessionLocal() as db:
                device = db.scalar(select(Device).where(Device.unique_id == name))
                device._data = {**device._data, "online_status": value}
                db.commit()

    def test_every_boolean_selection_and_its_exclusion_complement(self):
        import itertools
        import json
        from urllib.parse import urlencode
        domain = [True, False, None]
        names = ["online", "offline", "unknown"]
        for count in range(4):
            for indices in itertools.combinations(range(3), count):
                expected = sorted(names[i] for i in indices)
                for mode, values in (
                    ("include", [domain[i] for i in indices]),
                    ("exclude", [domain[i] for i in range(3) if i not in indices]),
                ):
                    with self.subTest(mode=mode, values=values):
                        query = urlencode({f"{mode}__online_status": json.dumps(values)})
                        response = self.get(f"/api/v1/devices?{query}")
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(sorted(row["unique_id"] for row in response.json()["items"]), expected)

    def test_empty_string_blank_is_not_coerced_to_false(self):
        response = self.get('/api/v1/devices?exclude__online_status=[""]')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sorted(row["unique_id"] for row in response.json()["items"]), ["offline", "online"])

    def test_boolean_filter_combines_with_another_column(self):
        response = self.get('/api/v1/devices?include__online_status=[true,false]&include__make=["Cisco"]')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 2)

    def test_filter_values_preserve_boolean_types_and_blanks(self):
        response = self.get('/api/v1/suggestions/devices/online_status/filter-values')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"values": [None, False, True], "has_more": False})

    def test_filter_values_include_the_long_tail_after_500(self):
        from app.db import SessionLocal
        from app.models import Device
        with SessionLocal() as db:
            db.add_all(Device(unique_id=f"tail-{i:04}", _data={"location": f"rack-{i:04}"}) for i in range(503))
            db.commit()
        first = self.get('/api/v1/suggestions/devices/location/filter-values?limit=500').json()
        self.assertEqual(len(first["values"]), 500)
        self.assertIsNone(first["values"][0])
        self.assertTrue(first["has_more"])
        second = self.get('/api/v1/suggestions/devices/location/filter-values?offset=500&limit=500').json()
        self.assertFalse(second["has_more"])
        self.assertEqual(len(set(first["values"] + second["values"])), 504)
        # Keep the shared class fixture unchanged for the other tests.
        with SessionLocal() as db:
            from sqlalchemy import delete
            db.execute(delete(Device).where(Device.unique_id.like("tail-%")))
            db.commit()

    def test_filter_values_do_not_expose_sensitive_fields(self):
        response = self.get('/api/v1/suggestions/devices/password/filter-values')
        self.assertEqual(response.status_code, 404)


class VendorCustomChecklistTests(SchemaCase):
    def test_custom_boolean_filters_and_values_on_both_vendor_lists(self):
        response = self.post('/api/v1/entity-fields/vendor_devices', {
            'key': 'verified', 'label': 'Verified', 'field_type': 'boolean',
        })
        self.assertEqual(response.status_code, 201, response.text)
        software = self.post('/api/v1/software', {'name': 'Vendor filter suite', 'version': '1'}).json()
        endpoint = f"/api/v1/software/{software['id']}/vendor-devices"
        for name, value in (('yes', True), ('no', False), ('blank', None)):
            response = self.post(endpoint, {'make': name, 'model': 'R1', 'misc_data': {'verified': value}})
            self.assertEqual(response.status_code, 201, response.text)
        for endpoint in (f'{endpoint}/grouped', '/api/v1/vendor-devices'):
            for query in ('include__verified=[true,false]', 'exclude__verified=[null]'):
                response = self.get(f'{endpoint}?{query}')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['total'], 2)
        response = self.get('/api/v1/suggestions/vendor-devices/verified/filter-values')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['values'], [None, False, True])

    def test_relationship_values_include_blanks(self):
        response = self.get('/api/v1/suggestions/tests/component_name/filter-values')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(None, response.json()['values'])

    def test_test_author_values_do_not_enumerate_unrelated_users(self):
        response = self.get('/api/v1/suggestions/tests/created_by_username/filter-values')
        self.assertEqual(response.status_code, 200, response.text)
        # The seeded admin exists, but has authored no tests in this fixture.
        self.assertNotIn('admin', response.json()['values'])


class ChecklistQueryTransportTests(SchemaCase):
    def test_post_and_get_use_identical_filters_on_every_grid_endpoint(self):
        import json
        from urllib.parse import urlencode
        device = self.post('/api/v1/devices', {'unique_id': 'query-device', 'make': 'Cisco'}).json()
        software = self.post('/api/v1/software', {'name': 'Query suite', 'version': '1'}).json()
        response = self.post('/api/v1/tests', {'device_id': device['id'], 'software_id': software['id'], 'outcome': 'pass'})
        self.assertEqual(response.status_code, 201, response.text)
        vendor = f"/api/v1/software/{software['id']}/vendor-devices"
        self.post(vendor, {'make': 'Cisco', 'model': 'R1'})
        endpoints = [('/api/v1/devices', 'make'), ('/api/v1/software', 'name'),
                     ('/api/v1/tests', 'outcome'), ('/api/v1/users/paged', 'username'),
                     ('/api/v1/audit_logs', 'action'), ('/api/v1/vendor-devices', 'make'),
                     (f'{vendor}/grouped', 'make'),
                     (f"/api/v1/software/{software['id']}/tested-devices", 'make')]
        for endpoint, field in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertGreater(self.get(endpoint).json()['total'], 0)
                body = {f'include__{field}': json.dumps([])}
                response = self.post(f'{endpoint}/query?page_size=25', body)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['total'], 0)
                expected = self.get(f'{endpoint}?page_size=25&{urlencode(body)}')
                self.assertEqual(response.json(), expected.json())
        # A large exclusion remains an exclusion, rather than its incomplete
        # complement, without exposing a huge URL to the ingress proxy.
        response = self.post('/api/v1/devices/query', {
            'exclude__make': json.dumps([f'vendor {i}' for i in range(1000)]),
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 1)

    def test_post_preserves_validation_and_authentication(self):
        self.assertEqual(self.post('/api/v1/devices/query?page_size=0', {}).status_code, 422)
        self.assertEqual(self.post('/api/v1/devices/query', {'page_size': '0'}).status_code, 422)
        self.assertEqual(self.client.post('/api/v1/devices/query', json={}).status_code, 401)


class FacetedFilterValueTests(SchemaCase):
    def test_user_boolean_options_follow_other_user_filters(self):
        import json
        from urllib.parse import urlencode

        params = urlencode({'include__username': json.dumps(['admin'])})
        response = self.get(f'/api/v1/suggestions/users/is_online/filter-values?{params}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['values']), 1)
        self.assertIsInstance(response.json()['values'][0], bool)

    def test_other_columns_narrow_device_and_test_options(self):
        import json
        from urllib.parse import urlencode

        cisco = self.post('/api/v1/devices', {
            'unique_id': 'facet-cisco', 'make': 'Cisco', 'model': 'R1',
        }).json()
        dell = self.post('/api/v1/devices', {
            'unique_id': 'facet-dell', 'make': 'Dell', 'model': 'R10',
        }).json()
        software = self.post('/api/v1/software', {
            'name': 'Facet suite', 'version': '1',
        }).json()
        for device in (cisco, dell):
            self.post('/api/v1/tests', {
                'device_id': device['id'], 'software_id': software['id'], 'outcome': 'pass',
            })
        self.post('/api/v1/devices', {'unique_id': 'facet-untested', 'make': 'Other',
                                      'model': 'R100'})
        device_facet = '/api/v1/suggestions/devices/model/filter-values'
        query = urlencode({'include__make': json.dumps(['Cisco'])})
        response = self.get(f'{device_facet}?{query}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['values'], ['R1'])
        scoped = self.get(f'{device_facet}?software_id={software["id"]}')
        self.assertEqual(scoped.status_code, 200, scoped.text)
        self.assertEqual(scoped.json()['values'], ['R1', 'R10'])

        test_facet = '/api/v1/suggestions/tests/device_unique_id/filter-values'
        query = urlencode({'include__device_unique_id': json.dumps(['facet-dell']),
                           'include__outcome': json.dumps(['pass'])})
        response = self.get(f'{test_facet}?{query}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['values'], ['facet-cisco', 'facet-dell'])
