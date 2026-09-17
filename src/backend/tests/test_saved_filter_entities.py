from app.models.saved_filter import is_filter_entity


def test_software_detail_saved_views_are_scoped_by_software_uuid():
    software_id = "01234567-89ab-cdef-0123-456789abcdef"
    assert is_filter_entity(f"vendor_devices:{software_id}")
    assert is_filter_entity(f"tested_devices:{software_id}")


def test_software_detail_saved_views_reject_unscoped_or_malformed_entities():
    assert not is_filter_entity("vendor_devices")
    assert not is_filter_entity("tested_devices")
    assert not is_filter_entity("vendor_devices:not-a-uuid")
    assert not is_filter_entity("tested_devices:01234567-89ab-cdef")
