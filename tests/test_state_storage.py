import os
import json
import pytest
import asyncio
import pytest_asyncio
from state_storage import StateStorage
from datetime import datetime, timedelta

@pytest_asyncio.fixture
async def storage():
    """Фикстура для создания тестового хранилища"""
    # Создаем временный файл для тестов
    test_file = "test_states.json"
    storage = StateStorage(test_file)
    await storage.initialize()
    yield storage
    # Очищаем после тестов
    if os.path.exists(test_file):
        os.remove(test_file)

@pytest.mark.asyncio
async def test_initialize(storage):
    """Тест инициализации хранилища"""
    assert os.path.exists(storage.state_file)
    assert storage._lock is not None
    assert storage._state == {}

@pytest.mark.asyncio
async def test_atomic_operation(storage):
    """Тест атомарных операций"""
    async with storage.atomic_operation() as states:
        states["test"] = {"value": 1}
    
    # Проверяем, что изменения сохранены
    assert storage._states["test"]["value"] == 1
    
    # Проверяем, что файл создан и содержит правильные данные
    with open(storage.state_file, 'r') as f:
        data = json.load(f)
        assert data["test"]["value"] == 1

@pytest.mark.asyncio
async def test_set_get_item(storage):
    """Тест установки и получения значений"""
    test_data = {"id": "test", "value": 42}
    
    # Устанавливаем значение
    await storage.set_item("test", test_data)
    
    # Получаем значение
    result = await storage.get_item("test")
    assert result == test_data

@pytest.mark.asyncio
async def test_update_item(storage):
    """Тест обновления значений"""
    # Создаем начальное состояние
    initial_data = {"id": "test", "value": 1}
    await storage.set_item("test", initial_data)
    
    # Обновляем состояние
    update_data = {"value": 2}
    await storage.update_item("test", update_data)
    
    # Проверяем результат
    result = await storage.get_item("test")
    assert result["value"] == 2
    assert result["id"] == "test"  # Старые поля сохранены

@pytest.mark.asyncio
async def test_delete_item(storage):
    """Тест удаления значений"""
    # Создаем тестовые данные
    await storage.set_item("test", {"value": 1})
    
    # Удаляем
    await storage.delete_item("test")
    
    # Проверяем что элемент удален
    result = await storage.get_item("test")
    assert result is None

@pytest.mark.asyncio
async def test_get_all_items(storage):
    """Тест получения всех элементов"""
    test_data = {"test": {"value": 1}}
    await storage.set_item("test", test_data["test"])
    result = await storage.get_all_items()
    assert result == test_data

@pytest.mark.asyncio
async def test_cleanup_old_items(storage):
    """Тест очистки старых элементов"""
    # Создаем старый элемент
    old_data = {"value": 1, "timestamp": (datetime.now() - timedelta(hours=25)).isoformat()}
    await storage.set_item("old", old_data)
    
    # Создаем новый элемент
    new_data = {"value": 2, "timestamp": datetime.now().isoformat()}
    await storage.set_item("new", new_data)
    
    # Очищаем старые элементы
    await storage.cleanup_old_items(max_age_hours=24)
    
    # Проверяем что старый удален, а новый остался
    assert await storage.get_item("old") is None  # Старый удален
    assert await storage.get_item("new") is not None  # Новый остался

@pytest.mark.asyncio
async def test_random_item(storage):
    """Тест получения случайного элемента"""
    # Создаем 10 элементов
    for i in range(10):
        await storage.set_item(str(i), {"value": i})
    
    # Получаем случайный элемент
    result = await storage.get_random_item()
    
    # Проверяем что результат корректный
    assert isinstance(result["value"], int)
    assert 0 <= result["value"] <= 9

@pytest.mark.asyncio
async def test_concurrent_access(storage):
    """Тест конкурентного доступа"""
    async def update_value(key, value):
        await storage.set_item(key, {"value": value})
    
    # Запускаем несколько конкурентных операций
    tasks = []
    for i in range(10):
        tasks.append(asyncio.create_task(update_value("test", i)))
    
    # Ждем завершения всех операций
    await asyncio.gather(*tasks)
    
    # Проверяем, что состояние консистентно
    result = await storage.get_item("test")
    assert isinstance(result["value"], int)
    assert 0 <= result["value"] <= 9
