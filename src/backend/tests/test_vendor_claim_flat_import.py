"""CSV row handling for component-specific vendor claims."""

from app.api.vendor_devices import _import_component_support
from app.services.io import parse_csv


def test_flat_component_rows_keep_distinct_components_and_statuses():
    rows = parse_csv(
        b"make,model,support_status,component_name,component_version,component_status\n"
        b"Acme,R1,supported,Outlook,16.2,supported\n"
        b"Acme,R1,supported,Teams,2.1,unsupported\n"
    )
    entries = [_import_component_support(row)[0] for row in rows]
    assert [(entry.component_name, entry.component_version, entry.support_status)
            for entry in entries] == [
                ("Outlook", "16.2", "supported"),
                ("Teams", "2.1", "unsupported"),
            ]
    assert all("component_name" not in row for row in rows)


def test_flat_component_status_defaults_to_claim_status():
    row = parse_csv(
        b"make,model,support_status,component_name,component_version\n"
        b"Acme,R1,partial,Outlook,16.2\n"
    )[0]
    assert _import_component_support(row)[0].support_status == "partial"
