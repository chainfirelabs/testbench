import io
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from app.services import test_artifact_storage as storage
from app.services.object_storage import OutputError


def test_filenames_and_keys_cannot_escape_test_prefix():
    for supplied, expected in [('..\\..\\report.zip', 'report.zip'), ('/etc/passwd', 'passwd'),
                               ('\r\n', 'artifact'), ('..', 'artifact'), ('résumé.pdf', 'résumé.pdf')]:
        name = storage.artifact_filename(supplied)
        assert name == expected
        key = storage.artifact_key('test-123', 'artifact-456', name)
        assert key.startswith('tests/test-123/artifacts/artifact-456/')
        assert len(key.split('/')) == 5


@patch.object(storage, 'storage_client')
@patch.object(storage.settings, 'artifact_max_bytes', 4)
def test_upload_checks_actual_file_size_and_keeps_binary_bytes(client_factory):
    client = client_factory.return_value
    payload = io.BytesIO(b'\x00\xff\x01\x02')
    payload.seek(2)
    assert storage.upload_artifact('bucket', 'tests/t/artifacts/a/report', payload, 'application/zip') == 4
    args = client.put_object.call_args.kwargs
    assert args['Body'].read() == b'\x00\xff\x01\x02'
    assert args['ContentLength'] == 4
    client.reset_mock()
    with pytest.raises(OutputError) as error:
        storage.upload_artifact('bucket', 'key', io.BytesIO(b'12345'), 'text/plain')
    assert error.value.status == 413
    client.put_object.assert_not_called()
    assert storage.upload_artifact('bucket', 'key', io.BytesIO(), 'text/plain') == 0


@patch.object(storage, 'object_storage_client')
@patch.object(storage.settings, 'object_storage_bucket', 'test-bucket')
@patch.object(storage.settings, 'object_storage_enabled', False)
def test_disabled_storage_and_bucket_changes_do_not_access_s3(client):
    with pytest.raises(OutputError) as error:
        storage.storage_client('test-bucket')
    assert error.value.status == 503
    with patch.object(storage.settings, 'object_storage_enabled', True):
        with pytest.raises(OutputError):
            storage.storage_client('another-bucket')
    client.assert_not_called()


@patch.object(storage, 'storage_client')
def test_missing_objects_have_a_useful_download_error(client_factory):
    client_factory.return_value.get_object.side_effect = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
    with pytest.raises(OutputError) as error:
        storage.download_artifact('bucket', 'key')
    assert error.value.status == 404


@patch('app.api.test_artifacts.get_test', return_value=Mock(test_run=None))
@patch('app.api.test_artifacts.storage.upload_artifact', return_value=3)
@patch('app.api.test_artifacts.storage.delete_artifact')
@patch('app.api.test_artifacts.log_action')
def test_failed_metadata_commit_removes_uploaded_object(audit, delete, upload, get_test):
    from unittest.mock import Mock
    from fastapi import UploadFile
    from app.api.test_artifacts import upload_artifact
    db = Mock()
    db.commit.side_effect = RuntimeError('database write failed')
    with pytest.raises(RuntimeError, match='database write failed'):
        upload_artifact('test-123', UploadFile(filename='report.zip', file=io.BytesIO(b'abc')),
                        request=Mock(), db=db, user=Mock(id='user-1'))
    db.rollback.assert_called_once()
    delete.assert_called_once()
    assert delete.call_args.args[1].startswith('tests/test-123/artifacts/')


def test_output_keys_sit_beside_artifacts_under_the_test():
    assert storage.output_key('test-123', '../run log.txt') == 'tests/test-123/output/.._run_log.txt'
    name = storage.artifact_filename('../../run.log')
    assert storage.output_key('test-123', name) == 'tests/test-123/output/run.log'


def test_a_run_keeps_its_shared_files_and_its_tests_files_in_one_folder():
    folder = storage.run_folder('run-1', 'Nightly #1234 / main')
    assert folder == 'test-runs/Nightly-1234-main_run-1'
    assert storage.run_folder('run-2', '  ../  ') == 'test-runs/run_run-2'
    assert len(storage.run_folder('run-3', 'x' * 500)) == len('test-runs/') + 60 + len('_run-3')
    assert storage.run_artifact_key(folder, 'a-1', 'pytest report.html') == (
        'test-runs/Nightly-1234-main_run-1/artifacts/a-1/pytest_report.html')
    assert storage.artifact_key('test-9', 'a-2', 'capture.pcap', folder) == (
        'test-runs/Nightly-1234-main_run-1/tests/test-9/artifacts/a-2/capture.pcap')
    assert storage.output_key('test-9', 'run.log', folder) == (
        'test-runs/Nightly-1234-main_run-1/tests/test-9/output/run.log')


@patch.object(storage, 'storage_client')
@patch.object(storage.settings, 'object_storage_max_bytes', 8)
def test_output_upload_refuses_what_the_viewer_cannot_show(client_factory):
    client = client_factory.return_value
    assert storage.upload_output('bucket', 'tests/t/output/run.log', io.BytesIO(b'ok\nfine')) == 7
    assert client.put_object.call_args.kwargs['Body'] == b'ok\nfine'
    client.reset_mock()
    for payload, status in ((b'123456789', 413), (b'\x00\xff', 422)):
        with pytest.raises(OutputError) as error:
            storage.upload_output('bucket', 'key', io.BytesIO(payload))
        assert error.value.status == status
    client.put_object.assert_not_called()


@patch.object(storage, 'storage_client')
@patch.object(storage.settings, 'object_storage_max_bytes', 8)
def test_output_reads_are_bounded(client_factory):
    client = client_factory.return_value
    client.get_object.return_value = {'Body': io.BytesIO(b'hello\n')}
    assert storage.read_output('bucket', 'key') == 'hello\n'
    client.get_object.return_value = {'Body': io.BytesIO(b'123456789')}
    with pytest.raises(OutputError) as error:
        storage.read_output('bucket', 'key')
    assert error.value.status == 413
    client.get_object.side_effect = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
    with pytest.raises(OutputError) as error:
        storage.read_output('bucket', 'key')
    assert error.value.status == 404
    assert 'Output' in str(error.value)
