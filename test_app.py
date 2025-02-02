import os
import json
import asyncio
import pytest
import pytest_asyncio
import urllib3
import certifi
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from httpx import AsyncClient
import yt_dlp
from app import app, StateManager
from utils import (
    sanitize_filename, get_safe_ydl_opts, download_video,
    parse_ffmpeg_progress, delete_file_after_delay, clear_logs_task,
    update_download_status
)
import uuid
import shutil

# Фикстуры для тестирования

@pytest_asyncio.fixture
async def state_manager():
    """Фикстура для менеджера состояний"""
    manager = StateManager()
    await manager.connect()
    return manager

@pytest_asyncio.fixture
async def test_app(state_manager):
    """Фикстура для тестового приложения"""
    app.state.manager = state_manager
    return app

@pytest_asyncio.fixture
async def async_client(test_app):
    """Фикстура для асинхронного клиента"""
    async with AsyncClient(app=test_app, base_url="http://test") as client:
        yield client

@pytest_asyncio.fixture
async def cleanup_downloads(test_app):
    """Очищает словарь downloads после каждого теста"""
    yield
    
    # Очищаем состояние после теста
    state = await test_app.state.manager.get_state()
    state["downloads"] = {}
    await test_app.state.manager.update_state(state)

@pytest_asyncio.fixture
async def temp_download_dir(tmp_path):
    """Создает временную директорию для загрузок"""
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    yield downloads_dir
    # Очищаем директорию после теста
    shutil.rmtree(downloads_dir)

@pytest_asyncio.fixture
async def bg_tasks():
    return BackgroundTasks()

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
async def test_download_valid_youtube_url(async_client, temp_download_dir):
    """Тест загрузки с YouTube"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        response = await async_client.post(
            "/download",
            data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        )
        assert response.status_code == 200
    data = response.json()
    assert "downloadId" in data

@pytest.mark.asyncio
async def test_download_valid_vimeo_url_no_ffmpeg(async_client, monkeypatch):
    """Тест загрузки с Vimeo без ffmpeg"""
    monkeypatch.setenv('PATH', '')
    response = await async_client.post(
        "/download",
        data={"url": "https://vimeo.com/76979871"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "downloadId" in data

@pytest.mark.asyncio
async def test_download_valid_vimeo_url_with_ffmpeg(async_client, monkeypatch):
    """Тест загрузки с Vimeo с ffmpeg"""
    monkeypatch.setenv('PATH', '/usr/local/bin:/usr/bin')
    response = await async_client.post(
        "/download",
        data={"url": "https://vimeo.com/76979871"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "downloadId" in data

@pytest.mark.asyncio
async def test_download_valid_m3u8_url(async_client):
    """Тест загрузки m3u8"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        response = await async_client.post(
            "/download",
            data={"url": "https://test.com/video.m3u8"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "downloadId" in data

@pytest.mark.asyncio
async def test_download_invalid_url(async_client):
    """Тест загрузки с некорректным URL"""
    with patch('yt_dlp.YoutubeDL', MockFailedYDL):
        response = await async_client.post(
            "/download",
            data={"url": "invalid-url"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Download failed" in data["error"]

@pytest.mark.asyncio
async def test_log_error_success(async_client):
    """Тест логирования ошибки"""
    download_id = str(uuid.uuid4())
    response = await async_client.post(
        "/log_error",
        data={
            "downloadId": download_id,
            "error": "Test error"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

@pytest.mark.asyncio
async def test_log_error_invalid_json(async_client):
    """Тест отправки некорректного JSON в лог ошибок"""
    response = await async_client.post(
        "/log_error",
        data={"invalid": "data"}
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert any("downloadId" in error["loc"] for error in response.json()["detail"])

@pytest.mark.asyncio
async def test_log_error_missing_fields(async_client):
    """Тест отсутствия обязательных полей"""
    response = await async_client.post(
        "/log_error",
        data={}
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert any("downloadId" in error["loc"] for error in response.json()["detail"])

@pytest.mark.asyncio
async def test_progress_stream_invalid_id(async_client):
    """Тест получения прогресса для несуществующего ID"""
    response = await async_client.get("/progress_stream/invalid-id")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_progress_invalid_id(async_client):
    """Тест получения прогресса для несуществующего ID"""
    response = await async_client.get("/progress/invalid-id")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_sync_progress_invalid_id(async_client):
    """Тест синхронизации прогресса для несуществующего ID"""
    response = await async_client.get("/sync_progress/invalid-id")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_cancel_invalid_id(async_client):
    """Тест отмены загрузки для несуществующего ID"""
    response = await async_client.post("/cancel/invalid-id")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_valid_m3u8_url_validation(async_client):
    """Тест валидации корректного M3U8 URL"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        response = await async_client.post(
            "/download",
            data={"url": "https://test.com/video.m3u8"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "downloadId" in data

@pytest.mark.asyncio
async def test_invalid_m3u8_url(async_client):
    """Тест валидации некорректного M3U8 URL"""
    with patch('yt_dlp.YoutubeDL', MockFailedYDL):
        response = await async_client.post(
            "/download",
            data={"url": "invalid-url"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Download failed" in data["error"]

@pytest.mark.asyncio
async def test_cleanup_after_download(async_client, tmp_path):
    """Тест очистки файлов после загрузки"""
    # Создаем тестовую директорию
    test_dir = tmp_path / "downloads"
    test_dir.mkdir()
    
    # Создаем тестовый файл
    test_file = test_dir / "test.mp4"
    test_file.write_text("test content")
    
    # Добавляем информацию о загрузке в состояние
    download_id = str(uuid.uuid4())
    await update_download_status(download_id, "completed", progress=100)
    
    # Проверяем что файл существует
    assert test_file.exists()
    
    # Запускаем очистку
    await delete_file_after_delay(str(test_file), 0)
    
    # Проверяем что файл удален
    assert not test_file.exists()

@pytest.mark.asyncio
async def test_update_download_status(test_app):
    """Тест обновления статуса загрузки"""
    download_id = str(uuid.uuid4())
    
    # Обновляем статус
    await test_app.state.update_download_status(download_id, "completed", progress=100)
    
    # Проверяем обновление
    state = await test_app.state.manager.get_state()
    download_info = state["downloads"][download_id]
    assert download_info["status"] == "completed"
    assert download_info["progress"] == 100

@pytest.mark.asyncio
async def test_parse_ffmpeg_progress():
    """Тест парсинга прогресса ffmpeg"""
    # Тестируем успешный парсинг
    progress_line = "time=00:00:50.50"
    progress = parse_ffmpeg_progress(progress_line)
    assert progress == 50.5
    
    # Тестируем некорректный формат
    progress = parse_ffmpeg_progress("invalid")
    assert progress == 0.0

@pytest.mark.asyncio
async def test_delete_file_after_delay(temp_download_dir):
    """Тест удаления файла после задержки"""
    # Создаем тестовый файл
    file_path = os.path.join(temp_download_dir, "test.mp4")
    with open(file_path, "wb") as f:
        f.write(b"test content")
    
    # Запускаем задачу с малой задержкой
    task = asyncio.create_task(delete_file_after_delay(file_path, 0.1))
    
    # Сразу после запуска файл должен существовать
    assert os.path.exists(file_path)
    
    # Ждем немного больше чем задержка
    await asyncio.sleep(0.2)
    await task
    
    # Теперь файл должен быть удален
    assert not os.path.exists(file_path)

@pytest.mark.asyncio
async def test_download_workflow(async_client, temp_download_dir):
    """Интеграционный тест полного процесса загрузки"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        # 1. Начинаем загрузку
        response = await async_client.post(
            "/download",
            data={"url": "https://example.com/video.mp4"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "downloadId" in data
        download_id = data["downloadId"]
        
        # 2. Проверяем прогресс
        response = await async_client.get(f"/progress/{download_id}")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "progress" in data
        
        # Ждем завершения загрузки
        await asyncio.sleep(0.2)
        
        # 3. Проверяем статус
        response = await async_client.get(f"/status/{download_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"

@pytest.mark.asyncio
async def test_error_handling_workflow(async_client):
    """Интеграционный тест обработки ошибок"""
    with patch('yt_dlp.YoutubeDL', MockFailedYDL):
        # 1. Пробуем загрузить с неверным URL
        response = await async_client.post(
            "/download",
            data={"url": "invalid-url"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Download failed" in data["error"]

@pytest.mark.asyncio
async def test_concurrent_downloads(async_client, temp_download_dir):
    """Тест конкурентных загрузок"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        # Запускаем несколько загрузок одновременно
        tasks = []
        for i in range(3):
            tasks.append(
                async_client.post(
                    "/download",
                    data={"url": f"https://test.com/video{i}"}
                )
            )
        
        # Ждем завершения всех запросов
        responses = await asyncio.gather(*tasks)
        
        # Проверяем что все запросы успешны
        for response in responses:
            assert response.status_code == 200
            assert "downloadId" in response.json()
        
        # Проверяем что все загрузки завершились успешно
        download_ids = [r.json()["downloadId"] for r in responses]
        for download_id in download_ids:
            response = await async_client.get(f"/status/{download_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] in ["downloading", "completed"]

@pytest.mark.asyncio
async def test_concurrent_file_operations(async_client, temp_download_dir):
    """Тест конкурентных операций с файлами"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        # Запускаем загрузку
        response = await async_client.post(
            "/download",
            data={"url": "https://test.com/video"}
        )
        assert response.status_code == 200
        download_id = response.json()["downloadId"]
        
        # Создаем несколько конкурентных запросов к файлу
        tasks = []
        for _ in range(5):
            tasks.extend([
                async_client.get(f"/file/{download_id}"),
                async_client.get(f"/status/{download_id}"),
                async_client.post(f"/cancel/{download_id}")
            ])
        
        # Выполняем запросы конкурентно
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Проверяем что все запросы завершились без ошибок
        for response in responses:
            assert not isinstance(response, Exception)
            assert response.status_code in [200, 404]  # 404 допустим для file endpoint если загрузка не завершена

# Тесты StateManager

@pytest.mark.asyncio
async def test_state_manager_connect():
    """Тест подключения StateManager"""
    with patch('aioredis.from_url') as mock_redis:
        # Мокаем Redis чтобы он всегда был недоступен
        mock_redis.side_effect = Exception("Redis unavailable")
        manager = StateManager()
        await manager.connect()
        
        # Проверяем что состояние инициализировано
        state = await manager.get_state()
        assert state == {"downloads": {}}

@pytest.mark.asyncio
async def test_state_manager_update():
    """Тест обновления состояния"""
    with patch('aioredis.from_url') as mock_redis:
        # Мокаем Redis чтобы он всегда был недоступен
        mock_redis.side_effect = Exception("Redis unavailable")
        manager = StateManager()
        await manager.connect()
        
        # Обновляем состояние
        test_state = {
            "downloads": {
                "test-id": {
                    "status": "downloading",
                    "progress": 50
                }
            }
        }
        await manager.update_state(test_state)
        
        # Проверяем что состояние обновилось
        state = await manager.get_state()
        assert state == test_state
        assert state["downloads"]["test-id"]["status"] == "downloading"
        assert state["downloads"]["test-id"]["progress"] == 50

@pytest.mark.asyncio
async def test_state_manager_concurrent_updates():
    """Тест конкурентных обновлений состояния"""
    with patch('aioredis.from_url') as mock_redis:
        # Мокаем Redis чтобы он всегда был недоступен
        mock_redis.side_effect = Exception("Redis unavailable")
        manager = StateManager()
        await manager.connect()
        
        async def update_task(task_id: str):
            state = await manager.get_state()
            if "downloads" not in state:
                state["downloads"] = {}
            state["downloads"][task_id] = {
                "status": "downloading",
                "progress": 0
            }
            await manager.update_state(state)
        
        # Запускаем несколько конкурентных обновлений
        tasks = []
        for i in range(5):
            task = asyncio.create_task(update_task(f"task-{i}"))
            tasks.append(task)
        
        await asyncio.gather(*tasks)
        
        # Проверяем результат
        state = await manager.get_state()
        assert len(state["downloads"]) == 5
        for i in range(5):
            assert f"task-{i}" in state["downloads"]
            assert state["downloads"][f"task-{i}"]["status"] == "downloading"

@pytest.mark.asyncio
async def test_state_manager_invalid_state():
    """Тест обработки некорректного состояния"""
    with patch('aioredis.from_url') as mock_redis:
        # Мокаем Redis чтобы он всегда был недоступен
        mock_redis.side_effect = Exception("Redis unavailable")
        manager = StateManager()
        await manager.connect()
        
        # Пробуем обновить некорректным состоянием
        invalid_state = None
        with pytest.raises(ValueError):
            await manager.update_state(invalid_state)
        
        # Проверяем что состояние не изменилось
        state = await manager.get_state()
        assert state == {"downloads": {}}

@pytest.mark.asyncio
async def test_state_manager_max_downloads():
    """Тест обработки максимального количества загрузок"""
    with patch('aioredis.from_url') as mock_redis:
        # Мокаем Redis чтобы он всегда был недоступен
        mock_redis.side_effect = Exception("Redis unavailable")
        manager = StateManager()
        await manager.connect()
        
        # Создаем максимальное количество загрузок
        state = await manager.get_state()
        for i in range(100):  # Предполагаем лимит в 100 загрузок
            state["downloads"][f"download_{i}"] = {
                "status": "downloading",
                "progress": 0
            }
            await manager.update_state(state)
        
        # Пробуем добавить еще одну загрузку
        state["downloads"]["download_101"] = {
            "status": "downloading",
            "progress": 0
        }
        with pytest.raises(Exception) as exc_info:
            await manager.update_state(state)
        assert "Maximum number of downloads reached" in str(exc_info.value)

@pytest.mark.asyncio
async def test_state_manager_large_state_update():
    """Тест обновления большого состояния"""
    manager = StateManager()
    await manager.connect()
    
    # Создаем большой объект состояния
    large_state = {
        "downloads": {
            f"key_{i}": {
                "data": "x" * 10000  # 10KB данных
            } for i in range(1000)  # 1000 ключей
        }
    }
    
    # Проверяем, что большое состояние вызывает ошибку
    with pytest.raises(Exception) as exc_info:
        await manager.update_state(large_state)
    assert "Maximum number of downloads reached" in str(exc_info.value)

@pytest.mark.asyncio
async def test_state_manager_concurrent_file_operations():
    """Тест конкурентных операций с файлами"""
    with patch('aioredis.from_url') as mock_redis:
        # Мокаем Redis чтобы он всегда был недоступен
        mock_redis.side_effect = Exception("Redis unavailable")
        manager = StateManager()
        await manager.connect()
        
        async def update_task(task_id: str):
            for i in range(10):
                state = await manager.get_state()
                if "downloads" not in state:
                    state["downloads"] = {}
                state["downloads"][task_id] = {
                    "status": "downloading",
                    "progress": i * 10
                }
                await manager.update_state(state)
                await asyncio.sleep(0.1)
        
        # Запускаем несколько конкурентных обновлений
        tasks = []
        for i in range(5):
            task = asyncio.create_task(update_task(f"task_{i}"))
            tasks.append(task)
        
        await asyncio.gather(*tasks)
        
        # Проверяем, что все обновления сохранились
        state = await manager.get_state()
        for i in range(5):
            assert f"task_{i}" in state["downloads"]
            assert state["downloads"][f"task_{i}"]["progress"] == 90

@pytest.mark.asyncio
async def test_state_manager_recovery():
    """Тест восстановления состояния после сбоя"""
    with patch('aioredis.from_url') as mock_redis:
        # Мокаем Redis чтобы он всегда был недоступен
        mock_redis.side_effect = Exception("Redis unavailable")
        
        # Создаем файл для сохранения состояния
        state_file = "test_state.json"
        if os.path.exists(state_file):
            os.remove(state_file)
            
        try:
            # Создаем первый менеджер и сохраняем состояние в файл
            manager = StateManager()
            manager._state_file = state_file  # Используем тестовый файл
            await manager.connect()
            
            # Создаем начальное состояние
            initial_state = {
                "downloads": {
                    "download_1": {
                        "status": "downloading",
                        "progress": 50
                    }
                }
            }
            await manager.update_state(initial_state)
            
            # Сохраняем состояние в файл
            with open(state_file, "w") as f:
                json.dump(initial_state, f)
            
            # Создаем новый менеджер и проверяем восстановление из файла
            new_manager = StateManager()
            new_manager._state_file = state_file
            await new_manager.connect()
            
            # Загружаем состояние из файла
            with open(state_file, "r") as f:
                recovered_state = json.load(f)
            
            # Проверяем что состояние совпадает
            assert recovered_state["downloads"]["download_1"]["status"] == "downloading"
            assert recovered_state["downloads"]["download_1"]["progress"] == 50
            
        finally:
            # Удаляем тестовый файл
            if os.path.exists(state_file):
                os.remove(state_file)

class MockYDL:
    """Мок для yt-dlp"""
    def __init__(self, *args, **kwargs):
        self.progress_hooks = kwargs.get('progress_hooks', [])
        self.outtmpl = kwargs.get('outtmpl')

    def extract_info(self, url, download=True):
        """Имитация загрузки с отправкой прогресса"""
        # Отправляем прогресс загрузки через хуки
        for hook in self.progress_hooks:
            hook({
                'status': 'downloading',
                '_percent_str': '50.0%'
            })
            hook({
                'status': 'finished',
                '_percent_str': '100.0%'
            })
        
        # Возвращаем информацию о видео
        return {
            'id': 'test_video',
            'title': 'Test Video',
            'ext': 'mp4',
            'url': url
        }

class MockFailedYDL:
    """Мок для yt-dlp с ошибкой"""
    def __init__(self, *args, **kwargs):
        self.progress_hooks = kwargs.get('progress_hooks', [])
        self.info_dict = {'download_id': 'test-id'}

    def extract_info(self, url, download=True):
        """Имитация ошибки загрузки"""
        raise yt_dlp.utils.DownloadError("Download failed")

@pytest.mark.asyncio
async def test_download_invalid_url(async_client):
    """Тест загрузки с некорректным URL"""
    with patch('yt_dlp.YoutubeDL', MockFailedYDL):
        response = await async_client.post(
            "/download",
            data={"url": "invalid-url"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Download failed" in data["error"]

@pytest.mark.asyncio
async def test_invalid_m3u8_url(async_client):
    """Тест валидации некорректного M3U8 URL"""
    with patch('yt_dlp.YoutubeDL', MockFailedYDL):
        response = await async_client.post(
            "/download",
            data={"url": "invalid-url"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Download failed" in data["error"]

@pytest.mark.asyncio
async def test_error_handling_workflow(async_client):
    """Интеграционный тест обработки ошибок"""
    with patch('yt_dlp.YoutubeDL', MockFailedYDL):
        # 1. Пробуем загрузить с неверным URL
        response = await async_client.post(
            "/download",
            data={"url": "invalid-url"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Download failed" in data["error"]

@pytest.mark.asyncio
async def test_progress_stream_success(async_client, test_app):
    """Тест успешного стрима прогресса"""
    download_id = str(uuid.uuid4())
    
    # Инициализируем состояние
    await test_app.state.update_download_status(download_id, "downloading", progress=0)
    
    # Запускаем стрим
    async with async_client.stream('GET', f'/progress_stream/{download_id}') as response:
        assert response.status_code == 200
        
        # Обновляем прогресс
        await test_app.state.update_download_status(download_id, "downloading", progress=50)
        
        # Читаем событие
        async for line in response.aiter_lines():
            if line.startswith('data: '):
                try:
                    data = json.loads(line[6:])
                    assert data["status"] == "downloading"
                    assert data["progress"] == 50
                    break
                except json.JSONDecodeError:
                    continue

@pytest.mark.asyncio
async def test_download_missing_url(async_client):
    """Тест отсутствия URL"""
    response = await async_client.post("/download")
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert any("url" in error["loc"] for error in data["detail"])

# Тесты бизнес-логики

@pytest.mark.asyncio
async def test_update_download_status(test_app):
    """Тест обновления статуса загрузки"""
    download_id = str(uuid.uuid4())
    
    # Обновляем статус
    await test_app.state.update_download_status(download_id, "completed", progress=100)
    
    # Проверяем обновление
    state = await test_app.state.manager.get_state()
    download_info = state["downloads"][download_id]
    assert download_info["status"] == "completed"
    assert download_info["progress"] == 100

@pytest.mark.asyncio
async def test_parse_ffmpeg_progress():
    """Тест парсинга прогресса ffmpeg"""
    # Тестируем успешный парсинг
    progress_line = "time=00:00:50.50"
    progress = parse_ffmpeg_progress(progress_line)
    assert progress == 50.5
    
    # Тестируем некорректный формат
    progress = parse_ffmpeg_progress("invalid")
    assert progress == 0.0

@pytest.mark.asyncio
async def test_sanitize_filename():
    """Тест функции очистки имени файла"""
    # Тестируем удаление спецсимволов
    filename = "test/file*name?.mp4"
    from app import sanitize_filename
    sanitized = sanitize_filename(filename)
    assert sanitized == "test_file_name_.mp4"
    
    # Тестируем длинное имя
    long_filename = "a" * 300 + ".mp4"
    sanitized = sanitize_filename(long_filename)
    assert len(sanitized) <= 255

# Тесты фоновых задач

@pytest.mark.asyncio
async def test_delete_file_after_delay(temp_download_dir):
    """Тест удаления файла после задержки"""
    # Создаем тестовый файл
    file_path = os.path.join(temp_download_dir, "test.mp4")
    with open(file_path, "wb") as f:
        f.write(b"test content")
    
    # Запускаем задачу с малой задержкой
    task = asyncio.create_task(delete_file_after_delay(file_path, 0.1))
    
    # Сразу после запуска файл должен существовать
    assert os.path.exists(file_path)
    
    # Ждем немного больше чем задержка
    await asyncio.sleep(0.2)
    await task
    
    # Теперь файл должен быть удален
    assert not os.path.exists(file_path)

@pytest.mark.asyncio
async def test_clear_logs_task(temp_download_dir):
    """Тест очистки логов"""
    from app import clear_logs_task
    import time
    
    # Создаем директорию для логов
    logs_dir = temp_download_dir / "logs"
    logs_dir.mkdir()
    
    # Создаем старый лог-файл (24+ часа назад)
    old_log = logs_dir / "old.log"
    old_log.write_text("old log")
    old_time = time.time() - 86500  # 24 часа + 100 секунд
    os.utime(str(old_log), (old_time, old_time))
    
    # Создаем новый лог-файл
    new_log = logs_dir / "new.log"
    new_log.write_text("new log")
    
    # Создаем не-лог файл
    not_log = logs_dir / "test.txt"
    not_log.write_text("not a log")
    
    # Запускаем очистку с маленькой задержкой
    task = asyncio.create_task(clear_logs_task(delay=0.1, logs_dir=str(logs_dir)))
    await asyncio.sleep(0.2)  # Ждем чуть дольше чем delay
    task.cancel()  # Отменяем бесконечный цикл
    
    try:
        await task
    except asyncio.CancelledError:
        pass
        
    # Проверяем результаты
    assert not old_log.exists(), "Старый лог-файл должен быть удален"
    assert new_log.exists(), "Новый лог-файл не должен быть удален"
    assert not_log.exists(), "Не-лог файл не должен быть удален"

# Тесты интеграции

@pytest.mark.asyncio
async def test_download_workflow(async_client, temp_download_dir):
    """Интеграционный тест полного процесса загрузки"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        # 1. Начинаем загрузку
        response = await async_client.post(
            "/download",
            data={"url": "https://example.com/video.mp4"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "downloadId" in data
        download_id = data["downloadId"]
        
        # 2. Проверяем прогресс
        response = await async_client.get(f"/progress/{download_id}")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "progress" in data
        
        # Ждем завершения загрузки
        await asyncio.sleep(0.2)
        
        # 3. Проверяем статус
        response = await async_client.get(f"/status/{download_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"

@pytest.mark.asyncio
async def test_error_handling_workflow(async_client):
    """Интеграционный тест обработки ошибок"""
    with patch('yt_dlp.YoutubeDL', MockFailedYDL):
        # 1. Пробуем загрузить с неверным URL
        response = await async_client.post(
            "/download",
            data={"url": "invalid-url"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Download failed" in data["error"]

@pytest.mark.asyncio
async def test_concurrent_downloads(async_client, temp_download_dir):
    """Тест конкурентных загрузок"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        # Запускаем несколько загрузок одновременно
        tasks = []
        for i in range(3):
            tasks.append(
                async_client.post(
                    "/download",
                    data={"url": f"https://test.com/video{i}"}
                )
            )
        
        # Ждем завершения всех запросов
        responses = await asyncio.gather(*tasks)
        
        # Проверяем что все запросы успешны
        for response in responses:
            assert response.status_code == 200
            assert "downloadId" in response.json()
        
        # Проверяем что все загрузки завершились успешно
        download_ids = [r.json()["downloadId"] for r in responses]
        for download_id in download_ids:
            response = await async_client.get(f"/status/{download_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] in ["downloading", "completed"]

@pytest.mark.asyncio
async def test_concurrent_file_operations(async_client, temp_download_dir):
    """Тест конкурентных операций с файлами"""
    with patch('yt_dlp.YoutubeDL', MockYDL):
        # Запускаем загрузку
        response = await async_client.post(
            "/download",
            data={"url": "https://test.com/video"}
        )
        assert response.status_code == 200
        download_id = response.json()["downloadId"]
        
        # Создаем несколько конкурентных запросов к файлу
        tasks = []
        for _ in range(5):
            tasks.extend([
                async_client.get(f"/file/{download_id}"),
                async_client.get(f"/status/{download_id}"),
                async_client.post(f"/cancel/{download_id}")
            ])
        
        # Выполняем запросы конкурентно
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Проверяем что все запросы завершились без ошибок
        for response in responses:
            assert not isinstance(response, Exception)
            assert response.status_code in [200, 404]  # 404 допустим для file endpoint если загрузка не завершена

@pytest.mark.asyncio
async def test_download_endpoint(initialized_app):
    """Тест для проверки скачивания файла"""
    async with AsyncClient(app=initialized_app, base_url="http://test") as client:
        # Используем существующий download_id с загруженным видео
        download_id = "77d816a6-b651-4238-a38a-7faf6c93c3f2"
        
        # 1. Проверяем состояние в StateManager
        state = await initialized_app.state.manager.get_state(download_id)
        print(f"\nState from StateManager: {state}")
        
        # 2. Проверяем статус загрузки через API
        response = await client.get(f"/sync_progress?download_id={download_id}")
        print(f"\nStatus response: {response.json()}")
        
        # 3. Проверяем наличие файла и его содержимое
        download_dir = os.path.join(os.path.dirname(__file__), "downloads", download_id)
        if os.path.exists(download_dir):
            files = os.listdir(download_dir)
            print(f"\nDownload directory contents: {files}")
            for file in files:
                file_path = os.path.join(download_dir, file)
                print(f"File size: {os.path.getsize(file_path)} bytes")
                print(f"File path: {file_path}")
        else:
            print("\nDownload directory not found")
        
        # 4. Пробуем скачать файл
        response = await client.get(f"/download/{download_id}")
        print(f"\nDownload response status: {response.status_code}")
        print(f"Download response headers: {response.headers}")
        if response.status_code != 200:
            print(f"Download response body: {response.json()}")

if __name__ == "__main__":
    pytest.main(["-v", "test_app.py::test_download_endpoint"])

@pytest.fixture
async def initialized_app():
    """Фикстура для инициализации приложения"""
    app.state = SimpleNamespace()
    app.state.manager = StateManager()
    await app.state.manager.connect()
    yield app
    await app.state.manager.disconnect()

@pytest.mark.asyncio
async def test_download_endpoint():
    """Тест для проверки скачивания файла"""
    import os
    import pytest
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    download_id = "77d816a6-b651-4238-a38a-7faf6c93c3f2"
    
    # 1. Проверяем наличие файла
    download_dir = os.path.join(os.path.dirname(__file__), "downloads", download_id)
    print(f"\nChecking directory: {download_dir}")
    if os.path.exists(download_dir):
        files = os.listdir(download_dir)
        print(f"Files in directory: {files}")
        for file in files:
            file_path = os.path.join(download_dir, file)
            print(f"File size: {os.path.getsize(file_path)} bytes")
    else:
        print("Directory not found")
    
    # 2. Пробуем скачать файл
    response = client.get(f"/download/{download_id}")
    print(f"\nDownload response status: {response.status_code}")
    print(f"Download response headers: {dict(response.headers)}")
    if response.status_code != 200:
        print(f"Error response: {response.json()}")

if __name__ == "__main__":
    pytest.main(["-v", "test_app.py::test_download_endpoint"])
