import json
import asyncio
import logging
import threading
from typing import Dict, Any, Optional

class StateManager:
    """Менеджер состояния приложения"""
    
    _default_state = {
        "downloads": {}
    }
    
    def __init__(self):
        """Инициализация менеджера состояния"""
        self._memory_state = self._default_state.copy()
        self._sync_lock = threading.Lock()
        
    async def connect(self) -> None:
        """Инициализация состояния"""
        pass

    async def disconnect(self) -> None:
        """Очистка ресурсов"""
        pass

    async def get_state(self) -> Dict[str, Any]:
        """Получение текущего состояния"""
        return self._memory_state.copy()
    
    async def save_state(self, state: Dict[str, Any]) -> None:
        """Сохранение состояния"""
        self._memory_state = state.copy()
            
    async def clear_state(self) -> None:
        """Очистка состояния"""
        self._memory_state = self._default_state.copy()

    def get_download_state_sync(self, download_id: str) -> Optional[Dict[str, Any]]:
        """Получение состояния загрузки (синхронно)"""
        with self._sync_lock:
            return self._memory_state["downloads"].get(download_id)
            
    def update_download_state_sync(self, download_id: str, state: Dict[str, Any]):
        """Обновление состояния загрузки (синхронно)"""
        with self._sync_lock:
            self._memory_state["downloads"][download_id] = state.copy()
            
    def delete_download_state_sync(self, download_id: str):
        """Удаление состояния загрузки (синхронно)"""
        with self._sync_lock:
            self._memory_state["downloads"].pop(download_id, None)
            
    async def get_download_state(self, download_id: str) -> Optional[Dict[str, Any]]:
        """Получение состояния загрузки"""
        if not download_id:
            return None
        return self.get_download_state_sync(download_id)
        
    async def update_download_state(self, download_id: str, state: Dict[str, Any]):
        """Обновление состояния загрузки"""
        if not download_id or not state:
            return
        self.update_download_state_sync(download_id, state)
        
    async def delete_download_state(self, download_id: str):
        """Удаление состояния загрузки"""
        if not download_id:
            return
        self.delete_download_state_sync(download_id)
