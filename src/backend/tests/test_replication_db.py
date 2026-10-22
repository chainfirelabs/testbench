"""Database round trip for the narrow replication exchange.

Set TB_REPLICATION_TEST_DB_URL to a disposable PostgreSQL database. The test
creates and drops two randomly named schemas; it never touches public.
"""

import os
import unittest
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, utcnow
from app.models import (
    Device, Software, SoftwareComponent, Test as ResultModel, TestRun,
    VendorDevice, VendorDeviceComponentSupport,
)
from app.services.replication import apply_items, snapshot_page


@unittest.skipUnless(os.getenv("TB_REPLICATION_TEST_DB_URL"), "Set TB_REPLICATION_TEST_DB_URL")
class ReplicationDatabaseTests(unittest.TestCase):
    def test_records_and_cascading_deletes(self):
        url = os.environ["TB_REPLICATION_TEST_DB_URL"]
        admin = create_engine(url)
        source_name = f"replication_source_{uuid4().hex[:12]}"
        peer_name = f"replication_peer_{uuid4().hex[:12]}"
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {source_name}"))
            connection.execute(text(f"CREATE SCHEMA {peer_name}"))
        source_engine = create_engine(url, connect_args={"options": f"-csearch_path={source_name}"})
        peer_engine = create_engine(url, connect_args={"options": f"-csearch_path={peer_name}"})
        try:
            Base.metadata.create_all(source_engine)
            Base.metadata.create_all(peer_engine)
            Source = sessionmaker(source_engine, expire_on_commit=False)
            Peer = sessionmaker(peer_engine, expire_on_commit=False)
            with Source() as source, Peer() as peer:
                device = Device(id=str(uuid4()), unique_id="replicated-device",
                                _data={"status": "available", "password": "local-secret"})
                software = Software(id=str(uuid4()), _data={"name": "Suite", "version": "1.0"})
                component = SoftwareComponent(id=str(uuid4()), software_id=software.id,
                                              name="Agent", version="2", position=0)
                run = TestRun(id=str(uuid4()), name="Nightly #7", external_id="pipeline-7",
                              url="https://ci.example/7", storage_folder="test-runs/Nightly-7_x")
                test = ResultModel(id=str(uuid4()), device_id=device.id, software_id=software.id,
                            component_id=component.id, test_run_id=run.id, _data={"outcome": "pass"})
                claim = VendorDevice(id=str(uuid4()), software_id=software.id,
                                     make="Example", model="Router", match_key="example|router|||",
                                     support_status="supported", misc_data={})
                support = VendorDeviceComponentSupport(vendor_device_id=claim.id,
                                                       component_id=component.id,
                                                       support_status="supported")
                source.add_all([device, software, component, run, test, claim, support])
                source.commit()

                for entity in ("devices", "software", "vendor_claims", "tests"):
                    page = snapshot_page(source, entity)
                    self.assertIsNone(page["next_cursor"])
                    apply_items(peer, page["items"])
                copied = peer.get(Device, device.id)
                self.assertEqual(copied.unique_id, device.unique_id)
                self.assertNotIn("password", copied.data)
                self.assertEqual(peer.get(ResultModel, test.id).component_id, component.id)
                # The run comes along with its test, folder and all.
                self.assertEqual(peer.get(ResultModel, test.id).test_run_id, run.id)
                copied_run = peer.get(TestRun, run.id)
                self.assertEqual((copied_run.name, copied_run.external_id, copied_run.storage_folder),
                                 ("Nightly #7", "pipeline-7", "test-runs/Nightly-7_x"))
                self.assertEqual(peer.get(VendorDevice, claim.id).component_support[0].component_id, component.id)

                # An edit made after the snapshot must survive its stale copy.
                peer_test = peer.get(ResultModel, test.id)
                peer_test._data = {"outcome": "fail"}
                peer_test.updated_at = utcnow() + timedelta(seconds=10)
                peer.commit()
                old_test = snapshot_page(source, "tests")["items"]
                self.assertEqual(apply_items(peer, old_test)["skipped"], 1)
                peer.refresh(peer_test)
                self.assertEqual(peer_test.outcome, "fail")

                source.delete(device)
                source.commit()
                for entity in ("devices", "tests"):
                    markers = snapshot_page(source, entity, tombstones=True)["items"]
                    self.assertEqual(len(markers), 1)
                    apply_items(peer, markers)
                self.assertIsNone(peer.get(Device, device.id))
                peer.expire_all()
                self.assertIsNone(peer.get(ResultModel, test.id))

                source.delete(software)
                source.commit()
                for entity in ("software", "vendor_claims"):
                    markers = snapshot_page(source, entity, tombstones=True)["items"]
                    self.assertEqual(len(markers), 1)
                    apply_items(peer, markers)
                peer.expire_all()
                self.assertIsNone(peer.get(Software, software.id))
                self.assertIsNone(peer.get(VendorDevice, claim.id))

                source.add_all([
                    Device(id=str(uuid4()), unique_id=f"page-device-{number}", _data={})
                    for number in range(251)
                ])
                source.commit()
                first = snapshot_page(source, "devices")
                self.assertEqual(len(first["items"]), 250)
                self.assertIsNotNone(first["next_cursor"])
                second = snapshot_page(source, "devices", first["next_cursor"])
                self.assertEqual(len(second["items"]), 1)
        finally:
            source_engine.dispose()
            peer_engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {source_name} CASCADE"))
                connection.execute(text(f"DROP SCHEMA {peer_name} CASCADE"))
            admin.dispose()
