import json
import asyncio
import logging
import time
from typing import Dict, Any, Optional
import os
from state_storage import StateStorage
from app import DOWNLOADS_DIR

class StateManager:
    """Менеджер состояния приложения"""

    def __init__(self):
        """Инициализация менеджера состояния"""
        self.state_file = os.path.join(DOWNLOADS_DIR, 'download_states.json')
        self.storage = StateStorage(self.state_file)

    async def initialize(self):
        """Асинхронная инициализация"""
        await self.storage.initialize()

    async def disconnect(self) -> None:
        """Очистка ресурсов"""
        # Состояние уже сохранено в storage
        pass

    async def get_state(self) -> Dict[str, Any]:
        """Получение текущего состояния"""
        downloads = await self.storage.get_all_items()
        return {"downloads": downloads}

    async def save_state(self, state: Dict[str, Any]) -> None:
        """Сохранение состояния"""
        async with self.storage.atomic_operation() as current_state:
            current_state.clear()
            current_state.update(state.get("downloads", {}))

    async def clear_state(self) -> None:
        """Очистка состояния"""
        async with self.storage.atomic_operation() as current_state:
            current_state.clear()

    async def get_download_state(self, download_id: str) -> Optional[Dict[str, Any]]:
        """Получает состояние загрузки по ID"""
        return await self.storage.get_item(f"download_{download_id}")

    async def update_download_state(self, download_id: str, state: Dict[str, Any]) -> None:
        """Обновляет состояние загрузки"""
        await self.storage.update_item(f"download_{download_id}", state)

    async def cleanup_old_downloads(self):
        """Очищает старые загрузки"""
        await self.storage.cleanup_old_items(0.5)  # 30 минут

    async def delete_download_state(self, download_id: str) -> None:
        """Удаляет состояние загрузки"""
        await self.storage.delete_item(f"download_{download_id}")

    async def get_all_downloads(self) -> Dict[str, Dict[str, Any]]:
        """Возвращает все загрузки"""
        return await self.storage.get_all_items()
