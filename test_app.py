import pytest
from starlette.testclient import TestClient
from app import app, downloads
from fastapi import BackgroundTasks

@pytest.fixture(scope="module")
def client():
    client = TestClient(app, backend='asyncio')
    return client

@pytest.fixture
def bg_tasks():
    return BackgroundTasks()

def test_download_valid_youtube_url(client):
    response = client.post("/download", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert response.status_code == 200
    assert "download_id" in response.json()

def test_download_valid_vimeo_url_no_ffmpeg(client, monkeypatch):
    # Эмулируем отсутствие ffmpeg
    monkeypatch.setenv('PATH', '')
    response = client.post("/download", json={"url": "https://vimeo.com/76979871", "ffmpeg_location": ""})
    assert response.status_code == 200
    download_id = response.json()["download_id"]

    # Проверяем что получаем ошибку о missing ffmpeg
    progress_response = client.get(f"/sync_progress?download_id={download_id}")
    assert progress_response.status_code == 200
    assert progress_response.json()["status"] == "error"
    assert "ffmpeg" in progress_response.json().get("error", "").lower()

def test_download_valid_vimeo_url_with_ffmpeg(client, monkeypatch):
    # Проверяем случай когда ffmpeg не найден по указанному пути
    monkeypatch.setenv('PATH', '/usr/local/bin:/usr/bin')
    response = client.post("/download", json={"url": "https://vimeo.com/76979871", "ffmpeg_location": "ffmpeg_env/bin/ffmpeg"})
    assert response.status_code == 200
    download_id = response.json()["download_id"]

    # Проверяем что получаем ошибку о отсутствии ffmpeg
    progress_response = client.get(f"/sync_progress?download_id={download_id}")
    assert progress_response.status_code == 200
    assert progress_response.json()["status"] == "error"
    assert "ffmpeg" in progress_response.json().get("error", "").lower()

def test_download_valid_m3u8_url(client):
    response = client.post("/download", json={"url": "https://dyckms5inbsqq.cloudfront.net/OpenAI/o1/C4_L0/sc-OpenAI-o1-C4_L0-master.m3u8"})
    assert response.status_code == 200
    assert "download_id" in response.json()

def test_download_invalid_url(client):
    response = client.post("/download", json={"url": "invalid-url"})
    assert response.status_code == 400
    assert "error" in response.json()

def test_log_error_valid_data(client):
    response = client.post("/log_error", json={"error": "test error", "downloadId": "123"})
    assert response.status_code == 200
    assert "status" in response.json()

def test_log_error_invalid_data(client):
    response = client.post("/log_error", json={"downloadId": "123"})
    assert response.status_code == 400
    assert "error" in response.json()

def test_progress_stream_invalid_id(client):
    response = client.get("/progress_stream/invalid_id")
    assert response.status_code == 404
    assert "error" in response.json()

def test_progress_invalid_id(client):
    response = client.get("/progress/invalid_id")
    assert response.status_code == 404
    assert "error" in response.json()

def test_sync_progress_invalid_id(client):
    response = client.get("/sync_progress?download_id=invalid_id")
    assert response.status_code == 404
    assert "error" in response.json()

def test_cancel_invalid_id(client):
    response = client.post("/cancel/invalid_id")
    assert response.status_code == 404
    assert "error" in response.json()

def test_valid_m3u8_url_validation(client):
    """Тест валидации корректного M3U8 URL"""
    url = "https://example.com/video.m3u8"
    response = client.post("/download", json={"url": url})
    assert response.status_code == 200
    assert "download_id" in response.json()

def test_invalid_m3u8_url(client):
    """Тест валидации некорректного M3U8 URL"""
    url = "not_a_url"
    response = client.post("/download", json={"url": url})
    assert response.status_code == 400
    assert "error" in response.json()

def test_cleanup_after_download(client, tmp_path):
    """Тест очистки файлов после загрузки"""
    import os
    from app import delete_file_after_delay
    
    test_file = tmp_path / "test.mp4"
    test_file.write_bytes(b"test content")
    
    # Тестируем функцию удаления напрямую
    delete_file_after_delay(str(test_file), 1)
    
    # Проверяем что файл удаляется
    import time
    time.sleep(2)
    assert not os.path.exists(str(test_file))

def test_static_files(client):
    """Тест доступа к статическим файлам"""
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]

def test_html_template(client):
    """Тест рендеринга HTML шаблона"""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>" in response.text
    assert "Скачать видео по URL" in response.text
