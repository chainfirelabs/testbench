"""Exercise the inventory join without requiring a PostgreSQL server."""
import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.compat import inventory_match_counts


def test_inventory_matching_wildcards_aliases_and_missing_values():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE devices (id TEXT PRIMARY KEY, data JSON)'))
        conn.execute(text('CREATE TABLE vendor_devices (id TEXT PRIMARY KEY, make TEXT, model TEXT, firmware_version TEXT, hardware_version TEXT, architecture TEXT)'))
        for device_id, data in [
            ('one', dict(make=' ACME ', model='Router', firmware_version='1', architecture='aarch64')),
            ('two', dict(make='acme', model='router', firmware_version='2', architecture='arm64')),
            ('unknown', dict(make='Acme', model='Router')),
            ('other', dict(make='Other', model='Router', firmware_version='1')),
        ]:
            conn.execute(text('INSERT INTO devices VALUES (:id, :data)'), {'id': device_id, 'data': json.dumps(data)})
        for claim_id, make, model, firmware, arch in [
            ('broad', 'acme', 'router', None, None),
            ('specific', ' ACME ', 'ROUTER', '1', 'arm64'),
            ('newer', 'Acme', 'Router', '2', None),
            ('absent', 'Acme', 'Router', '3', None),
            ('blank', ' ', None, None, None),
            ('architecture', None, None, None, 'aarch64'),
        ]:
            conn.execute(text('INSERT INTO vendor_devices VALUES (:id, :make, :model, :firmware, NULL, :arch)'),
                         dict(id=claim_id, make=make, model=model, firmware=firmware, arch=arch))
    with Session(engine) as db:
        assert inventory_match_counts(db, ['broad', 'specific', 'newer', 'absent', 'blank', 'architecture']) == {
            'broad': 3, 'specific': 1, 'newer': 1, 'architecture': 2,
        }
        assert inventory_match_counts(db, []) == {}
        assert inventory_match_counts(db, ['absent', 'blank']) == {}


def test_inventory_summary_counts_every_match_and_previews_the_first_few():
    from app.services.compat import inventory_match_summary

    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE devices (id TEXT PRIMARY KEY, unique_id TEXT, data JSON)'))
        conn.execute(text('CREATE TABLE vendor_devices (id TEXT PRIMARY KEY, make TEXT, model TEXT, firmware_version TEXT, hardware_version TEXT, architecture TEXT)'))
        for index, unique_id in enumerate(['dev-d', 'dev-b', 'dev-a', 'dev-c', 'dev-e']):
            conn.execute(text('INSERT INTO devices VALUES (:id, :uid, :data)'),
                         {'id': f'd{index}', 'uid': unique_id, 'data': json.dumps(dict(make='Acme', model='Router'))})
        conn.execute(text("INSERT INTO devices VALUES ('x', 'dev-x', :data)"), {'data': json.dumps(dict(make='Other'))})
        conn.execute(text("INSERT INTO vendor_devices VALUES ('many', 'acme', 'router', NULL, NULL, NULL)"))
        conn.execute(text("INSERT INTO vendor_devices VALUES ('one', 'other', NULL, NULL, NULL, NULL)"))
        conn.execute(text("INSERT INTO vendor_devices VALUES ('none', 'nobody', NULL, NULL, NULL, NULL)"))
    with Session(engine) as db:
        assert inventory_match_summary(db, ['many', 'one', 'none']) == {
            'many': (5, ['dev-a', 'dev-b', 'dev-c']),
            'one': (1, ['dev-x']),
        }
        assert inventory_match_summary(db, ['many'], preview=1) == {'many': (5, ['dev-a'])}
        assert inventory_match_summary(db, []) == {}
