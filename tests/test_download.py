import os
import pytest
import pytest_asyncio
import httpx
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from download import (
    download_video, prepare_download, validate_url, cancel_download,
    NetworkError, ValidationError, ProcessingError
)
from models import DownloadRequest, DownloadState, DownloadStatus

class MockResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "Error", 
                request=httpx.Request("GET", "http://test"),
                response=self
            )

@pytest.fixture
def download_request():
    """Фикстура для создания тестового запроса"""
    return DownloadRequest(
        url="https://example.com/video.mp4",
        format="mp4",
        quality="720p"
    )

@pytest.fixture
def download_state():
    """Фикстура для создания тестового состояния"""
    return DownloadState(
        id="test-id",
        status=DownloadStatus.INITIALIZING,
        url="https://example.com/video.mp4",
        progress=0,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

@pytest.mark.asyncio
async def test_validate_url_success():
    """Тест успешной валидации URL"""
    with patch('httpx.AsyncClient.head') as mock_head:
        mock_head.return_value = MockResponse(200)
        is_valid, error = await validate_url("https://example.com/video.mp4")
        assert is_valid
        assert error is None

@pytest.mark.asyncio
async def test_validate_url_failure():
    """Тест неуспешной валидации URL"""
    with patch('httpx.AsyncClient.head') as mock_head:
        mock_head.side_effect = httpx.HTTPError("Connection error")
        is_valid, error = await validate_url("https://example.com/video.mp4")
        assert not is_valid
        assert "Ошибка HTTP при проверке URL" in error

@pytest.mark.asyncio
async def test_prepare_download_success(download_request):
    """Тест успешной подготовки загрузки"""
    with patch('download.validate_url') as mock_validate:
        mock_validate.return_value = (True, None)
        state = await prepare_download(download_request)
        assert isinstance(state, DownloadState)
        assert state.status == DownloadStatus.INITIALIZING
        assert state.url == download_request.url
        assert state.metadata["format"] == download_request.format
        assert state.metadata["quality"] == download_request.quality

@pytest.mark.asyncio
async def test_prepare_download_invalid_url(download_request):
    """Тест подготовки загрузки с неверным URL"""
    with patch('download.validate_url') as mock_validate:
        mock_validate.return_value = (False, "Недействительный URL")
        with pytest.raises(ValidationError):
            await prepare_download(download_request)

@pytest.mark.asyncio
async def test_download_success(download_request, download_state):
    """Тест успешной загрузки"""
    response_data = {
        "file_path": "/downloads/video.mp4",
        "file_size": 1024
    }
    with patch('httpx.AsyncClient.post') as mock_post:
        mock_post.return_value = MockResponse(200, response_data)
        state = await download_video(download_request, download_state)
        assert state.status == DownloadStatus.COMPLETED
        assert state.file_path == response_data["file_path"]
        assert state.file_size == response_data["file_size"]
        assert state.progress == 100

@pytest.mark.asyncio
async def test_download_network_error_retry(download_request, download_state):
    """Тест повторных попыток при сетевой ошибке"""
    with patch('httpx.AsyncClient.post') as mock_post:
        # Первые две попытки вызывают ошибку, третья успешна
        mock_post.side_effect = [
            httpx.TimeoutException("Timeout"),
            httpx.TimeoutException("Timeout"),
            MockResponse(200, {"file_path": "/downloads/video.mp4", "file_size": 1024})
        ]
        state = await download_video(download_request, download_state)
        assert state.status == DownloadStatus.COMPLETED
        assert state.retry_count == 2

@pytest.mark.asyncio
async def test_download_max_retries_exceeded(download_request, download_state):
    """Тест превышения максимального количества попыток"""
    with patch('httpx.AsyncClient.post') as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Timeout")
        with pytest.raises(NetworkError):
            await download_video(download_request, download_state)
        assert download_state.status == DownloadStatus.ERROR
        assert download_state.retry_count == download_state.max_retries

@pytest.mark.asyncio
async def test_download_validation_error(download_request, download_state):
    """Тест ошибки валидации без повторных попыток"""
    with patch('httpx.AsyncClient.post') as mock_post:
        mock_post.side_effect = httpx.HTTPStatusError(
            "Bad Request",
            request=httpx.Request("POST", "http://test"),
            response=MockResponse(400)
        )
        with pytest.raises(ValidationError):
            await download_video(download_request, download_state)
        assert download_state.status == DownloadStatus.ERROR
        assert download_state.retry_count == 0

@pytest.mark.asyncio
async def test_cancel_download_success(download_state):
    """Тест успешной отмены загрузки"""
    download_state.status = DownloadStatus.DOWNLOADING
    state = await cancel_download(download_state)
    assert state.status == DownloadStatus.CANCELLED

@pytest.mark.asyncio
async def test_cancel_completed_download(download_state):
    """Тест попытки отмены завершенной загрузки"""
    download_state.status = DownloadStatus.COMPLETED
    with pytest.raises(ValidationError):
        await cancel_download(download_state)

@pytest.mark.asyncio
async def test_download_state_expiry(download_state):
    """Тест проверки устаревания загрузки"""
    # Устанавливаем время создания на 25 часов назад
    download_state.created_at = datetime.now() - timedelta(hours=25)
    assert download_state.is_expired(24)  # Проверяем с 24-часовым лимитом

    # Устанавливаем время создания на 23 часа назад
    download_state.created_at = datetime.now() - timedelta(hours=23)
    assert not download_state.is_expired(24)

if __name__ == "__main__":
    pytest.main([__file__])
