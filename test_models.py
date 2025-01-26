import pytest
from models import DownloadRequest, LogErrorRequest

def test_download_request_creation():
    task = DownloadRequest(url="http://example.com")
    assert str(task.url) == "http://example.com/"

def test_log_error_request_creation():
    log_req = LogErrorRequest(error="test error")
    assert log_req.error == "test error"
