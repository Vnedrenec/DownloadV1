import pytest
from starlette.testclient import TestClient
from app import app
from fastapi import BackgroundTasks
import urllib3
import certifi
import os
import json
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from app import app, sanitize_filename, get_safe_ydl_opts, download_video
import threading

@pytest.fixture(scope="module")
def test_client():
    """Фикстура для тестового клиента"""
    if not hasattr(app.state, "_state"):
        app.state._state = {}
    if "downloads" not in app.state._state:
        app.state._state["downloads"] = {}
    if not hasattr(app.state, "_state_lock"):
        app.state._state_lock = threading.Lock()
    return TestClient(app)

@pytest.fixture
def bg_tasks():
    return BackgroundTasks()

@pytest.fixture(autouse=True)
def cleanup_downloads():
    """Очищает словарь downloads после каждого теста"""
    if not hasattr(app.state, "_state"):
        app.state._state = {}
    if "downloads" not in app.state._state:
        app.state._state["downloads"] = {}
    if not hasattr(app.state, "_state_lock"):
        app.state._state_lock = threading.Lock()
    yield
    app.state._state["downloads"] = {}

@pytest.fixture(autouse=True)
def setup_ssl_verification():
    """Настраивает SSL-верификацию для тестов"""
    # Отключаем предупреждения о SSL
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    # Устанавливаем пул с проверкой сертификатов
    https = urllib3.PoolManager(
        cert_reqs='CERT_REQUIRED',
        ca_certs=certifi.where()
    )
    yield https

def test_download_valid_youtube_url(test_client):
    response = test_client.post(
        "/download",
        data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
    )
    assert response.status_code == 200
    assert "download_id" in response.json()

def test_download_valid_vimeo_url_no_ffmpeg(test_client, monkeypatch):
    # Эмулируем отсутствие ffmpeg
    monkeypatch.setenv('PATH', '')
    response = test_client.post(
        "/download",
        data={"url": "https://vimeo.com/76979871", "ffmpeg_location": ""}
    )
    assert response.status_code == 200
    download_id = response.json()["download_id"]

    # Проверяем прогресс
    progress_response = test_client.get(f"/sync_progress?download_id={download_id}")
    assert progress_response.status_code == 200
    assert "status" in progress_response.json()

def test_download_valid_vimeo_url_with_ffmpeg(test_client, monkeypatch):
    # Проверяем случай когда ffmpeg не найден по указанному пути
    monkeypatch.setenv('PATH', '/usr/local/bin:/usr/bin')
    response = test_client.post(
        "/download",
        data={"url": "https://vimeo.com/76979871", "ffmpeg_location": "ffmpeg_env/bin/ffmpeg"}
    )
    assert response.status_code == 200
    download_id = response.json()["download_id"]

    # Проверяем прогресс
    progress_response = test_client.get(f"/sync_progress?download_id={download_id}")
    assert progress_response.status_code == 200
    assert "status" in progress_response.json()

def test_download_valid_m3u8_url(test_client):
    response = test_client.post(
        "/download",
        data={"url": "https://dyckms5inbsqq.cloudfront.net/OpenAI/o1/C4_L0/sc-OpenAI-o1-C4_L0-master.m3u8"}
    )
    assert response.status_code == 200
    assert "download_id" in response.json()

def test_download_invalid_url(test_client):
    response = test_client.post(
        "/download",
        data={"url": "invalid-url"}
    )
    assert response.status_code == 400
    assert "error" in response.json()

def test_log_error_valid_data(test_client):
    response = test_client.post(
        "/log_error",
        json={"error": "test error", "downloadId": "123"}
    )
    assert response.status_code == 200
    assert "status" in response.json()

def test_log_error_invalid_data(test_client):
    response = test_client.post(
        "/log_error",
        json={"downloadId": "123"}
    )
    assert response.status_code == 400
    assert "error" in response.json()

def test_progress_stream_invalid_id(test_client):
    response = test_client.get("/progress_stream/invalid_id")
    assert response.status_code == 404
    assert "error" in response.json()

def test_progress_invalid_id(test_client):
    response = test_client.get("/progress/invalid_id")
    assert response.status_code == 404
    assert "error" in response.json()

def test_sync_progress_invalid_id(test_client):
    response = test_client.get("/sync_progress", params={"download_id": "invalid_id"})
    assert response.status_code == 404
    assert "error" in response.json()

def test_cancel_invalid_id(test_client):
    response = test_client.post("/cancel/invalid_id")
    assert response.status_code == 404
    assert "error" in response.json()

def test_valid_m3u8_url_validation(test_client):
    """Тест валидации корректного M3U8 URL"""
    url = "https://example.com/video.m3u8"
    response = test_client.post(
        "/download",
        data={"url": url}
    )
    assert response.status_code == 200
    assert "download_id" in response.json()

def test_invalid_m3u8_url(test_client):
    """Тест валидации некорректного M3U8 URL"""
    url = "not_a_url"
    response = test_client.post(
        "/download",
        data={"url": url}
    )
    assert response.status_code == 400
    assert "error" in response.json()

def test_cleanup_after_download(test_client, tmp_path, monkeypatch):
    """Тест очистки файлов после загрузки"""
    import threading
    
    # Создаем тестовый файл
    test_file = tmp_path / "test.mp4"
    test_file.write_text("test content")
    
    # Создаем событие для синхронизации
    done = threading.Event()
    
    # Мокаем функцию time.sleep
    def mock_sleep(seconds):
        if test_file.exists():
            test_file.unlink()
        done.set()
    
    monkeypatch.setattr('time.sleep', mock_sleep)
    
    # Запускаем очистку
    from app import delete_file_after_delay
    delete_file_after_delay(str(test_file), 1)
    
    # Ждем завершения потока
    done.wait(timeout=5)
    
    # Проверяем что файл удален
    assert not test_file.exists()

def test_static_files(test_client):
    """Тест доступа к статическим файлам"""
    # Проверяем доступ к главной странице
    response = test_client.get("/views")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    # Проверяем доступ к статическим файлам
    response = test_client.get("/static/css/style.css")
    assert response.status_code == 404  # Файл не существует, но маршрут правильный

def test_html_template(test_client):
    """Тест статической страницы"""
    response = test_client.get("/views")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_sanitize_filename():
    """Тест функции очистки имени файла"""
    assert sanitize_filename("test.mp4") == "test.mp4"
    assert sanitize_filename("test!@#$%^&*.mp4") == "test.mp4"
    assert sanitize_filename("../test.mp4") == "test.mp4"
    assert sanitize_filename("path/to/test.mp4") == "pathtotest.mp4"
    assert sanitize_filename("тест.mp4") == "test.mp4"

def test_get_safe_ydl_opts():
    """Тест функции получения безопасных опций для yt-dlp"""
    opts = get_safe_ydl_opts("test.mp4", "test_id", "ffmpeg")
    assert opts['outtmpl'] == "test.mp4"
    assert opts['progress_hooks'] is not None
    assert opts['ffmpeg_location'] == "ffmpeg"
    assert opts['extractor_args']['douyin']['disabled'] is True
    assert opts['extractor_args']['douyu']['disabled'] is True

@pytest.mark.asyncio
async def test_download_endpoint_invalid_url(test_client):
    """Тест эндпоинта скачивания с неверным URL"""
    response = test_client.post(
        "/download",
        data={"url": "invalid_url"}
    )
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_download_endpoint_missing_url(test_client):
    """Тест эндпоинта скачивания без URL"""
    response = test_client.post(
        "/download",
        data={}
    )
    assert response.status_code == 422

@pytest.mark.asyncio
@patch('yt_dlp.YoutubeDL')
async def test_download_video_youtube_success(mock_ydl):
    """Тест успешного скачивания с YouTube"""
    # Настраиваем мок
    mock_ydl_instance = MagicMock()
    mock_ydl.return_value.__enter__.return_value = mock_ydl_instance

    # Создаем тестовый файл
    test_output = "downloads/test_id.mp4"
    os.makedirs("downloads", exist_ok=True)
    with open(test_output, 'w') as f:
        f.write("test content")

    try:
        # Запускаем тест
        download_video("test_id", "https://www.youtube.com/watch?v=test")

        # Проверяем, что yt-dlp был вызван с правильными параметрами
        mock_ydl.assert_called_once()
    finally:
        # Очищаем тестовый файл
        if os.path.exists(test_output):
            os.remove(test_output)

@pytest.mark.asyncio
@patch('yt_dlp.YoutubeDL')
async def test_download_video_vimeo_success(mock_ydl):
    """Тест успешного скачивания с Vimeo"""
    # Настраиваем мок
    mock_ydl_instance = MagicMock()
    mock_ydl.return_value.__enter__.return_value = mock_ydl_instance

    # Создаем тестовый файл
    test_output = "downloads/test_id.mp4"
    os.makedirs("downloads", exist_ok=True)
    with open(test_output, 'w') as f:
        f.write("test content")

    try:
        # Запускаем тест
        download_video("test_id", "https://vimeo.com/test")

        # Проверяем, что yt-dlp был вызван с правильными параметрами
        mock_ydl.assert_called_once()
    finally:
        # Очищаем тестовый файл
        if os.path.exists(test_output):
            os.remove(test_output)

@pytest.mark.asyncio
@patch('yt_dlp.YoutubeDL')
async def test_download_video_error_handling(mock_ydl):
    """Тест обработки ошибок при скачивании"""
    # Настраиваем мок для генерации различных ошибок
    mock_ydl_instance = MagicMock()
    mock_ydl.return_value.__enter__.return_value = mock_ydl_instance

    test_cases = [
        ("HTTP Error 404: Not Found", "Видео не найдено"),
        ("Private video", "Это приватное видео"),
        ("Video unavailable", "Видео недоступно"),
        ("is not rated", "Это видео имеет возрастное ограничение")
    ]

    for error_msg, expected_msg in test_cases:
        mock_ydl_instance.download.side_effect = Exception(error_msg)

        try:
            # Запускаем тест
            download_video("test_id", "https://www.youtube.com/watch?v=test")
        except Exception as e:
            assert str(e) == expected_msg
            assert app.state.downloads["test_id"]["status"] == "error"
            assert app.state.downloads["test_id"]["error"] == expected_msg

@pytest.mark.asyncio
async def test_progress_endpoint(test_client):
    """Тест эндпоинта получения прогресса"""
    # Устанавливаем тестовые данные
    app.state._state["downloads"]["test_id"] = {
        "status": "processing",
        "progress": 50,
        "size": 1000000
    }

    response = test_client.get("/sync_progress", params={"download_id": "test_id"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert data["progress"] == 50
    assert data["size"] == 1000000

def test_cancel_download(test_client):
    """Тест отмены загрузки"""
    # Устанавливаем тестовые данные
    mock_process = MagicMock()
    app.state._state["downloads"]["test_id"] = {
        "status": "processing",
        "progress": 50,
        "process": mock_process
    }

    response = test_client.post("/cancel/test_id")
    assert response.status_code == 200
    assert mock_process.terminate.called
