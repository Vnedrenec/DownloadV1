import pytest
from models import DownloadRequest, LogErrorRequest
from urllib.parse import urlparse

def test_download_request_creation():
    task = DownloadRequest(url="http://example.com")
    assert task.url == "http://example.com"

def test_log_error_request_creation():
    log_req = LogErrorRequest(error="test error")
    assert log_req.error == "test error"

def test_download_request_url_validation():
    """Тест валидации URL в запросе на скачивание"""
    # Проверка корректных URL
    valid_urls = [
        "https://example.com",
        "http://example.com/path",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://vimeo.com/123456789"
    ]
    for url in valid_urls:
        task = DownloadRequest(url=url)
        parsed_url = urlparse(task.url)
        assert parsed_url.scheme in ["http", "https"]

    # Проверка некорректных URL
    invalid_urls = [
        "not_a_url",
        "ftp://example.com",
        "file:///etc/passwd",
        ""
    ]
    for url in invalid_urls:
        with pytest.raises(Exception):
            DownloadRequest(url=url)

def test_download_request_optional_params():
    """Тест опциональных параметров запроса на скачивание"""
    # Тест с ffmpeg_location
    task = DownloadRequest(
        url="https://example.com",
        ffmpeg_location="/usr/local/bin/ffmpeg"
    )
    assert task.ffmpeg_location == "/usr/local/bin/ffmpeg"

    # Тест с outputPath
    task = DownloadRequest(
        url="https://example.com",
        outputPath="/downloads"
    )
    assert task.outputPath == "/downloads"

    # Тест с format
    task = DownloadRequest(
        url="https://example.com",
        format="mp4"
    )
    assert task.format == "mp4"

def test_log_error_request_validation():
    """Тест валидации данных в запросе на логирование ошибки"""
    # Проверка корректных данных
    log_req = LogErrorRequest(
        error="test error",
        downloadId="123",
        stackTrace="Traceback..."
    )
    assert log_req.error == "test error"
    assert log_req.downloadId == "123"
    assert log_req.stackTrace == "Traceback..."

    # Проверка пустой ошибки
    with pytest.raises(Exception):
        LogErrorRequest(error="")

    # Проверка слишком длинной ошибки
    with pytest.raises(Exception):
        LogErrorRequest(error="e" * 10000)  # Предполагаем максимальную длину 1000

def test_log_error_request_optional_fields():
    """Тест опциональных полей в запросе на логирование ошибки"""
    # Только обязательное поле
    log_req = LogErrorRequest(error="test error")
    assert log_req.error == "test error"
    assert log_req.downloadId is None
    assert log_req.stackTrace is None

    # Все поля
    log_req = LogErrorRequest(
        error="test error",
        downloadId="123",
        stackTrace="Traceback..."
    )
    assert log_req.error == "test error"
    assert log_req.downloadId == "123"
    assert log_req.stackTrace == "Traceback..."
