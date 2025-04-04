import os
import time
import pytest
import asyncio
import pytest_asyncio
from cleanup_manager import CleanupManager

@pytest.fixture
def tmp_dirs(tmp_path):
    """Фикстура для создания временных директорий"""
    downloads_dir = tmp_path / "downloads"
    logs_dir = tmp_path / "logs"
    downloads_dir.mkdir()
    logs_dir.mkdir()
    return str(downloads_dir), str(logs_dir)

@pytest_asyncio.fixture
async def cleanup_manager(tmp_dirs):
    """Фикстура для создания менеджера очистки"""
    downloads_dir, logs_dir = tmp_dirs
    manager = CleanupManager(downloads_dir, logs_dir)
    yield manager
    if manager._cleanup_task:
        await manager.stop()

@pytest.mark.asyncio
async def test_init(cleanup_manager, tmp_dirs):
    """Тест инициализации менеджера"""
    downloads_dir, logs_dir = tmp_dirs
    assert cleanup_manager.downloads_dir == downloads_dir
    assert cleanup_manager.logs_dir == logs_dir
    assert cleanup_manager._cleanup_task is None

@pytest.mark.asyncio
async def test_start_stop(cleanup_manager):
    """Тест запуска и остановки очистки"""
    await cleanup_manager.start(1)
    assert cleanup_manager._cleanup_task is not None
    assert not cleanup_manager._cleanup_task.done()
    
    await cleanup_manager.stop()
    assert cleanup_manager._cleanup_task.done()

@pytest.mark.asyncio
async def test_cleanup_downloads(cleanup_manager, tmp_dirs):
    """Тест очистки старых загрузок"""
    downloads_dir, _ = tmp_dirs
    
    # Создаем тестовые файлы
    old_file = os.path.join(downloads_dir, "old.mp4")
    new_file = os.path.join(downloads_dir, "new.mp4")
    json_file = os.path.join(downloads_dir, "meta.json")
    
    # Создаем файлы
    open(old_file, 'w').close()
    open(new_file, 'w').close()
    open(json_file, 'w').close()
    
    # Меняем время модификации старого файла
    os.utime(old_file, (time.time() - 25*3600, time.time() - 25*3600))
    
    # Запускаем очистку
    await cleanup_manager.cleanup_downloads(24)
    
    # Проверяем результат
    assert not os.path.exists(old_file)  # Старый файл удален
    assert os.path.exists(new_file)      # Новый файл остался
    assert os.path.exists(json_file)     # JSON файл не тронут

@pytest.mark.asyncio
async def test_cleanup_logs(cleanup_manager, tmp_dirs):
    """Тест очистки старых логов"""
    _, logs_dir = tmp_dirs
    
    # Создаем тестовые файлы
    old_log = os.path.join(logs_dir, "old.log")
    new_log = os.path.join(logs_dir, "new.log")
    other_file = os.path.join(logs_dir, "other.txt")
    
    # Создаем файлы
    open(old_log, 'w').close()
    open(new_log, 'w').close()
    open(other_file, 'w').close()
    
    # Меняем время модификации старого файла
    os.utime(old_log, (time.time() - 25*3600, time.time() - 25*3600))
    
    # Запускаем очистку
    await cleanup_manager.cleanup_logs(24)
    
    # Проверяем результат
    assert not os.path.exists(old_log)    # Старый лог удален
    assert os.path.exists(new_log)        # Новый лог остался
    assert os.path.exists(other_file)     # Другой файл не тронут

@pytest.mark.asyncio
async def test_periodic_cleanup(cleanup_manager, tmp_dirs):
    """Тест периодической очистки"""
    downloads_dir, logs_dir = tmp_dirs
    
    # Создаем тестовые файлы
    old_file = os.path.join(downloads_dir, "old.mp4")
    old_log = os.path.join(logs_dir, "old.log")
    
    # Создаем файлы
    open(old_file, 'w').close()
    open(old_log, 'w').close()
    
    # Меняем время модификации
    old_time = time.time() - 25*3600
    os.utime(old_file, (old_time, old_time))
    os.utime(old_log, (old_time, old_time))
    
    # Запускаем периодическую очистку
    await cleanup_manager.start(1)
    
    # Ждем выполнения очистки
    await asyncio.sleep(2)
    
    # Проверяем результат
    assert not os.path.exists(old_file)  # Старый файл удален
    assert not os.path.exists(old_log)   # Старый лог удален

@pytest.mark.asyncio
async def test_cleanup_partial_downloads(cleanup_manager, tmp_dirs):
    """Тест очистки частично загруженных файлов"""
    downloads_dir, _ = tmp_dirs
    
    # Создаем частично загруженный файл
    partial_file = os.path.join(downloads_dir, "partial.mp4.part")
    temp_file = os.path.join(downloads_dir, "temp.mp4.temp")
    open(partial_file, 'w').close()
    open(temp_file, 'w').close()
    
    # Меняем время модификации файлов
    old_time = time.time() - 25*3600
    os.utime(partial_file, (old_time, old_time))
    os.utime(temp_file, (old_time, old_time))
    
    # Запускаем очистку
    await cleanup_manager.cleanup_downloads(24)
    
    # Проверяем что частичные файлы удалены
    assert not os.path.exists(partial_file)
    assert not os.path.exists(temp_file)
