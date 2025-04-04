import os
import json
import time
import uuid
import shutil
import pytest
import pytest_asyncio
import tempfile
import asyncio
import warnings
import urllib3
import certifi
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any
from httpx import AsyncClient
from fastapi import FastAPI

from state_storage import StateStorage
from cleanup_manager import CleanupManager

# Фикстуры для тестирования

@pytest_asyncio.fixture
async def state_storage(tmp_path):
    """Фикстура для хранилища состояний"""
    state_file = tmp_path / "test_state.json"
    storage = StateStorage(str(state_file))
    await storage.initialize()
    yield storage
    await storage.stop()

@pytest_asyncio.fixture
async def test_app():
    """Фикстура для тестового приложения"""
    # Создаем временную директорию для тестов
    with tempfile.TemporaryDirectory() as temp_dir:
        # Создаем пути для тестов
        downloads_dir = os.path.join(temp_dir, 'downloads')
        logs_dir = os.path.join(downloads_dir, 'logs')
        state_file = os.path.join(temp_dir, 'state.json')
        
        # Создаем директории
        os.makedirs(downloads_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)
        
        # Создаем приложение
        from app import app
        
        # Инициализируем хранилище
        app.state.storage = StateStorage(state_file)
        await app.state.storage.initialize()
        
        # Инициализируем менеджер очистки
        app.state.cleanup = CleanupManager(
            downloads_dir=downloads_dir,
            logs_dir=logs_dir
        )
        await app.state.cleanup.start(cleanup_interval=3600)  # 1 час
        
        yield app
        
        # Очищаем после тестов
        await app.state.cleanup.stop()
        await app.state.cleanup.cleanup_all()

@pytest_asyncio.fixture
async def async_client(test_app):
    """Фикстура для асинхронного клиента"""
    async with AsyncClient(app=test_app, base_url="http://test") as client:
        yield client

@pytest_asyncio.fixture
async def cleanup_downloads(test_app):
    """Очищает состояние после каждого теста"""
    yield
    async with test_app.state.storage.atomic_operation() as state:
        for key in list(state.keys()):
            if key.startswith("download_"):
                del state[key]

@pytest_asyncio.fixture
async def temp_download_dir(tmp_path):
    """Создает временную директорию для загрузок"""
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    yield downloads_dir
    # Очищаем директорию после теста
    shutil.rmtree(downloads_dir)

@pytest_asyncio.fixture(autouse=True)
async def setup_ssl_verification():
    """Настраивает SSL-верификацию для тестов"""
    # Отключаем предупреждения о SSL
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    # Устанавливаем пул с проверкой сертификатов
    https = urllib3.PoolManager(
        cert_reqs='CERT_REQUIRED',
        ca_certs=certifi.where()
    )
    yield https

@pytest.mark.asyncio
async def test_download_valid_youtube_url(async_client):
    """Тест загрузки с валидного YouTube URL"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        response = await async_client.post(
            "/api/download",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        )
        assert response.status_code == 202
        data = response.json()
        assert "download_id" in data

@pytest.mark.asyncio
async def test_download_valid_vimeo_url_no_ffmpeg(async_client, monkeypatch):
    """Тест загрузки с Vimeo без ffmpeg"""
    monkeypatch.setenv('PATH', '')
    response = await async_client.post(
        "/api/download",
        json={"url": "https://vimeo.com/76979871"}
    )
    assert response.status_code == 202
    data = response.json()
    assert "download_id" in data

@pytest.mark.asyncio
async def test_download_valid_vimeo_url_with_ffmpeg(async_client, monkeypatch):
    """Тест загрузки с Vimeo с ffmpeg"""
    monkeypatch.setenv('PATH', '/usr/local/bin:/usr/bin')
    response = await async_client.post(
        "/api/download",
        json={"url": "https://vimeo.com/76979871"}
    )
    assert response.status_code == 202
    data = response.json()
    assert "download_id" in data

@pytest.mark.asyncio
async def test_download_valid_m3u8_url(async_client):
    """Тест загрузки m3u8"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        response = await async_client.post(
            "/api/download",
            json={"url": "https://test.com/video.m3u8"}
        )
        assert response.status_code == 202
        data = response.json()
        assert "download_id" in data

@pytest.mark.asyncio
async def test_download_invalid_url(async_client):
    """Тест загрузки с невалидного URL"""
    with patch('yt_dlp.YoutubeDL', MockFailedYDL):
        response = await async_client.post(
            "/api/download",
            json={"url": "invalid-url"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Неверный формат URL" in data["detail"]

@pytest.mark.asyncio
async def test_log_error_success(async_client):
    """Тест успешного логирования ошибки"""
    download_id = str(uuid.uuid4())
    response = await async_client.post(
        "/api/log_error",
        json={
            "downloadId": download_id,
            "error": "Test error"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

@pytest.mark.asyncio
async def test_log_error_invalid_json(async_client):
    """Тест логирования с невалидным JSON"""
    response = await async_client.post(
        "/api/log_error",
        json={"invalid": "data"}
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert any("downloadId" in error["loc"] for error in response.json()["detail"])

@pytest.mark.asyncio
async def test_log_error_missing_fields(async_client):
    """Тест логирования с отсутствующими полями"""
    response = await async_client.post(
        "/api/log_error",
        json={}
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert any("downloadId" in error["loc"] for error in response.json()["detail"])

@pytest.mark.asyncio
async def test_progress_stream_invalid_id(async_client):
    """Тест получения прогресса для несуществующего ID"""
    response = await async_client.get("/api/progress_stream/invalid-id")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_progress_invalid_id(async_client):
    """Тест получения прогресса для несуществующего ID"""
    response = await async_client.get("/api/progress/invalid-id")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_sync_progress_invalid_id(async_client):
    """Тест синхронизации прогресса для несуществующего ID"""
    response = await async_client.get("/api/sync_progress/invalid-id")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_cancel_invalid_id(async_client):
    """Тест отмены загрузки для несуществующего ID"""
    response = await async_client.post("/api/cancel/invalid-id")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_valid_m3u8_url_validation(async_client):
    """Тест валидации корректного M3U8 URL"""
    response = await async_client.post(
        "/api/validate_m3u8",
        json={"url": "https://test.com/video.m3u8"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True

@pytest.mark.asyncio
async def test_invalid_m3u8_url(async_client):
    """Тест валидации некорректного M3U8 URL"""
    response = await async_client.post(
        "/api/validate_m3u8",
        json={"url": "invalid-url"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False

@pytest.mark.asyncio
async def test_cleanup_after_download(async_client, tmp_path):
    """Тест очистки файлов после загрузки"""
    test_file = tmp_path / "test.mp4"
    test_file.write_bytes(b"test")
    
    with patch('os.path.exists', return_value=True), \
         patch('os.remove') as mock_remove:
        await delete_file_after_delay(str(test_file), delay=0.1)
        await asyncio.sleep(0.2)
        mock_remove.assert_called_once_with(str(test_file))

@pytest.mark.asyncio
async def test_parse_ffmpeg_progress():
    """Тест парсинга прогресса ffmpeg"""
    progress_line = "frame=1000 fps=25 q=-1.0 size=1024kB time=00:01:30.00 bitrate=1000.0kbits/s"
    result = await parse_ffmpeg_progress(progress_line)
    assert result == 90.0  # 1 минута 30 секунд

@pytest.mark.asyncio
async def test_api_download(async_client):
    """Тест нового API эндпоинта загрузки"""
    response = await async_client.post(
        "/api/download",
        json={"url": "https://example.com/video.m3u8"}
    )
    assert response.status_code == 202
    data = response.json()
    assert "download_id" in data

@pytest.mark.asyncio
async def test_download_formats(async_client):
    """Тест загрузки разных форматов видео"""
    formats = ["mp4", "mkv", "webm"]
    for format in formats:
        response = await async_client.post(
            "/api/download",
            json={
                "url": "https://example.com/video.m3u8",
                "format": format
            }
        )
        assert response.status_code == 202
        data = response.json()
        assert "download_id" in data

@pytest.mark.asyncio
async def test_cancel_download(async_client):
    """Тест отмены загрузки"""
    # Начинаем загрузку
    response = await async_client.post(
        "/api/download",
        json={"url": "https://example.com/video.m3u8"}
    )
    download_id = response.json()["download_id"]
    
    # Отменяем загрузку
    cancel_response = await async_client.post(f"/api/cancel/{download_id}")
    assert cancel_response.status_code == 200
    
    # Проверяем статус
    status_response = await async_client.get(f"/api/progress/{download_id}")
    assert status_response.status_code == 200
    data = status_response.json()
    assert data["status"] == "cancelled"

@pytest.mark.asyncio
async def test_state_storage_initialization(test_app, tmp_path):
    """Тест инициализации хранилища состояний"""
    state_file = tmp_path / "test_state.json"
    storage = StateStorage(str(state_file))
    
    # Проверяем начальную инициализацию
    await storage.initialize()
    assert storage._initialized
    assert os.path.exists(state_file)
    
    # Проверяем сохранение и восстановление данных
    test_data = {"test_key": "test_value"}
    async with storage.atomic_operation() as state:
        state.update(test_data)
    
    # Проверяем, что данные сохранились
    async with storage.atomic_operation() as state:
        assert state == test_data
    
    # Удаляем основной файл
    os.unlink(state_file)
    
    # Повторная инициализация должна восстановить из бэкапа
    storage2 = StateStorage(str(state_file))
    await storage2.initialize()
    
    async with storage2.atomic_operation() as state:
        assert state == test_data

@pytest.mark.asyncio
async def test_cleanup_manager(test_app, tmp_path):
    """Тест менеджера очистки"""
    downloads_dir = tmp_path / "downloads"
    logs_dir = tmp_path / "logs"
    os.makedirs(downloads_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)
    
    # Создаем тестовые файлы
    old_file = downloads_dir / "old_file.mp4"
    new_file = downloads_dir / "new_file.mp4"
    old_log = logs_dir / "old_log.log"  
    new_log = logs_dir / "new_log.log"  
    
    # Создаем файлы с разным временем
    old_time = time.time() - (25 * 3600)  # 25 часов назад
    with open(old_file, 'w') as f:
        f.write("test")
    with open(new_file, 'w') as f:
        f.write("test")
    with open(old_log, 'w') as f:
        f.write("test")
    with open(new_log, 'w') as f:
        f.write("test")
    
    os.utime(old_file, (old_time, old_time))
    os.utime(old_log, (old_time, old_time))
    
    # Запускаем очистку
    await test_app.state.cleanup.cleanup_all()
    
    # Проверяем результаты
    assert not os.path.exists(old_file)
    assert os.path.exists(new_file)
    assert not os.path.exists(old_log)
    assert os.path.exists(new_log)

@pytest.mark.asyncio
async def test_atomic_operations(test_app):
    """Тест атомарных операций с состоянием"""
    storage = test_app.state.storage
    
    # Тест успешной атомарной операции
    async with storage.atomic_operation() as state:
        state["test_key"] = "test_value"
    
    result = await storage.get_item("test_key")
    assert result == "test_value"
    
    # Тест отката при ошибке
    try:
        async with storage.atomic_operation() as state:
            state["test_key"] = "new_value"
            raise ValueError("Test error")
    except ValueError:
        pass
    
    result = await storage.get_item("test_key")
    assert result == "test_value"  # Значение должно остаться прежним

@pytest.mark.asyncio
async def test_concurrent_updates(test_app):
    """Тест конкурентных обновлений состояния"""
    storage = test_app.state.storage
    test_key = "concurrent_test"
    
    async def update_item(value):
        async with storage.atomic_operation() as state:
            await asyncio.sleep(0.1)  # Имитация долгой операции
            if test_key in state:
                state[test_key].append(value)
            else:
                state[test_key] = [value]
    
    # Запускаем несколько конкурентных обновлений
    tasks = [update_item(i) for i in range(5)]
    await asyncio.gather(*tasks)
    
    # Проверяем результат
    result = await storage.get_item(test_key)
    assert len(result) == 5
    assert sorted(result) == list(range(5))

@pytest.mark.asyncio
async def test_download_state_transitions(test_app, async_client):
    """Тест переходов состояния загрузки"""
    # Создаем загрузку
    response = await async_client.post(
        "/api/download",
        json={"url": "https://example.com/test.mp4"}
    )
    assert response.status_code == 202
    download_id = response.json()["download_id"]
    
    # Проверяем начальное состояние
    state = await test_app.state.storage.get_item(f"download_{download_id}")
    assert state is not None
    assert state["status"] == "initializing"
    
    # Обновляем состояние
    async with test_app.state.storage.atomic_operation() as state:
        download_key = f"download_{download_id}"
        state[download_key] = {
            "status": "downloading",
            "progress": 50
        }
    
    state = await test_app.state.storage.get_item(f"download_{download_id}")
    assert state["status"] == "downloading"
    assert state["progress"] == 50
    
    # Отменяем загрузку
    response = await async_client.post(f"/api/cancel/{download_id}")
    assert response.status_code == 200
    state = await test_app.state.storage.get_item(f"download_{download_id}")
    assert state["status"] == "cancelled"

@pytest.mark.asyncio
async def test_download_progress_stream(test_app, async_client):
    """Тест потока обновлений прогресса"""
    # Создаем загрузку
    response = await async_client.post(
        "/api/download",
        json={"url": "https://example.com/test.mp4"}
    )
    download_id = response.json()["download_id"]
    
    # Запускаем получение обновлений в фоновом режиме
    async def get_updates():
        updates = []
        async with async_client.stream('GET', f'/api/progress/{download_id}') as response:
            async for line in response.aiter_lines():
                if line.startswith('data:'):
                    updates.append(json.loads(line[5:]))
                if len(updates) >= 3:
                    break
        return updates
    
    # Запускаем получение обновлений
    updates_task = asyncio.create_task(get_updates())
    
    # Отправляем обновления через хранилище
    await asyncio.sleep(0.1)
    async with test_app.state.storage.atomic_operation() as state:
        download_key = f"download_{download_id}"
        state[download_key] = {
            "status": "downloading",
            "progress": 25
        }
    
    await asyncio.sleep(0.1)
    async with test_app.state.storage.atomic_operation() as state:
        download_key = f"download_{download_id}"
        state[download_key] = {
            "status": "downloading",
            "progress": 50
        }
    
    await asyncio.sleep(0.1)
    async with test_app.state.storage.atomic_operation() as state:
        download_key = f"download_{download_id}"
        state[download_key] = {
            "status": "completed",
            "progress": 100
        }
    
    # Получаем результаты
    updates = await updates_task
    
    # Проверяем обновления
    assert len(updates) == 3
    assert updates[0]["progress"] == 25
    assert updates[1]["progress"] == 50
    assert updates[2]["progress"] == 100
    assert updates[2]["status"] == "completed"

@pytest.mark.asyncio
async def test_cancel_download(async_client):
    """Тест отмены загрузки"""
    # Начинаем загрузку
    response = await async_client.post(
        "/api/download",
        json={"url": "https://example.com/video.m3u8"}
    )
    download_id = response.json()["download_id"]
    
    # Отменяем загрузку
    cancel_response = await async_client.post(f"/api/cancel/{download_id}")
    assert cancel_response.status_code == 200
    
    # Проверяем статус
    status_response = await async_client.get(f"/api/progress/{download_id}")
    assert status_response.status_code == 200
    data = status_response.json()
    assert data["status"] == "cancelled"

class MockYDL:
    """Мок для yt-dlp"""
    def __init__(self, *args, **kwargs):
        self.progress_hooks = kwargs.get('progress_hooks', [])
        self.outtmpl = kwargs.get('outtmpl')
        
    def extract_info(self, url, download=True):
        """Имитация загрузки с отправкой прогресса"""
        info = {
            'id': 'test-id',
            'title': 'Test Video',
            'ext': 'mp4',
            'duration': 100
        }
        
        # Отправляем прогресс через хуки
        for hook in self.progress_hooks:
            hook({
                'status': 'downloading',
                'downloaded_bytes': 1024,
                'total_bytes': 2048,
                'filename': self.outtmpl,
                'eta': 10,
                'speed': 1024
            })
            
        return info

class MockFailedYDL:
    """Мок для yt-dlp с ошибкой"""
    def __init__(self, *args, **kwargs):
        self.progress_hooks = kwargs.get('progress_hooks', [])
        self.info_dict = {'download_id': 'test-id'}
        
    def extract_info(self, url, download=True):
        """Имитация ошибки загрузки"""
        raise Exception("Download failed")

if __name__ == "__main__":
    pytest.main(["-v", "test_app.py"])
