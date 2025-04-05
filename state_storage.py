import os
import json
import asyncio
import logging
import aiofiles
from typing import Dict, Any, AsyncGenerator, Optional
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import time
from metrics import measure_time
from common import CommonState

class StateStorage:
    """Хранилище состояний загрузок с поддержкой атомарных операций и асинхронной синхронизации"""

    def __init__(self, state_file: str):
        """Инициализация хранилища состояний"""
        self.state_file = state_file
        self.state: Dict[str, CommonState] = {}
        self.lock = asyncio.Lock()  # Для операций с файлом
        self._initialized = False
        self._backup_file = f"{state_file}.backup"
        self._temp_file = f"{state_file}.temp"

    @measure_time()
    async def initialize(self):
        """Асинхронная инициализация хранилища с восстановлением из бэкапа при необходимости"""
        try:
            if os.path.exists(self.state_file):
                async with aiofiles.open(self.state_file, 'r') as f:
                    content = await f.read()
                    if content:
                        try:
                            self.state = json.loads(content)
                        except json.JSONDecodeError:
                            logging.error("[STATE] Ошибка декодирования JSON в основном файле")
                            # Пробуем восстановить из бэкапа
                            if os.path.exists(self._backup_file):
                                logging.info("[STATE] Восстановление из резервной копии")
                                async with aiofiles.open(self._backup_file, 'r') as bf:
                                    backup_content = await bf.read()
                                    try:
                                        self.state = json.loads(backup_content)
                                    except json.JSONDecodeError:
                                        logging.error("[STATE] Ошибка декодирования JSON в бэкапе")
                                        self.state = {}
                            else:
                                self.state = {}
                    else:
                        self.state = {}
            elif os.path.exists(self._backup_file):
                # Пробуем восстановить из бэкапа
                logging.info("[STATE] Восстановление из резервной копии")
                async with aiofiles.open(self._backup_file, 'r') as f:
                    content = await f.read()
                    try:
                        self.state = json.loads(content)
                    except json.JSONDecodeError:
                        logging.error("[STATE] Ошибка декодирования JSON в бэкапе")
                        self.state = {}
                await self._save_state()
            else:
                # Создаем новый файл
                self.state = {}
                await self._save_state()

            self._initialized = True
            logging.info("[STATE] Хранилище успешно инициализировано")
        except Exception as e:
            logging.error(f"[STATE] Ошибка при инициализации хранилища: {str(e)}")
            self._initialized = False
            raise

    @measure_time()
    async def stop(self):
        """Остановка хранилища с сохранением состояния"""
        if self._initialized:
            async with self.lock:
                await self._save_state()
            self._initialized = False

    @asynccontextmanager
    async def atomic_operation(self):
        """Атомарная операция с состоянием"""
        async with self.lock:
            try:
                yield self.state
                await self._save_state()
            except Exception as e:
                logging.error(f"[STATE] Error in atomic operation: {str(e)}", exc_info=True)
                raise

    async def _save_state(self):
        """Сохраняет состояние в файл"""
        try:
            # Создаем временный файл
            temp_file = self._temp_file
            async with aiofiles.open(temp_file, 'w') as f:
                await f.write(json.dumps(self.state, indent=2))
                await f.flush()
                os.fsync(f.fileno())

            # Атомарно заменяем основной файл
            if os.path.exists(self.state_file):
                os.replace(self.state_file, self._backup_file)
            os.replace(temp_file, self.state_file)
        except Exception as e:
            logging.error(f"[STATE] Ошибка при сохранении состояния: {str(e)}", exc_info=True)
            raise

    @measure_time()
    async def get_item(self, key: str) -> Optional[Dict]:
        """Получить значение по ключу"""
        if not self._initialized:
            await self.initialize()

        try:
            async with self.lock:
                return self.state.get(key)
        except Exception as e:
            logging.error(f"[STATE] Ошибка чтения состояния: {str(e)}")
            return None

    @measure_time()
    async def set_item(self, key: str, value: CommonState):
        """Устанавливает состояние по ключу"""
        if not self._initialized:
            logging.error("[STATE] Попытка установить состояние до инициализации")
            raise RuntimeError("StateStorage не инициализирован")
        try:
            async with self.atomic_operation():
                self.state[key] = value
        except Exception as e:
            logging.error(f"[STATE] Ошибка при установке состояния для {key}: {str(e)}")
            raise

    @measure_time()
    async def update_item(self, key: str, value: CommonState = None, **kwargs):
        """Обновляет существующее состояние по ключу. Допускает передачу дополнительных полей как именованных аргументов."""
        if not self._initialized:
            if not hasattr(self, '_lazy_init_attempted') or not self._lazy_init_attempted:
                self._lazy_init_attempted = True
                logging.warning("[STATE] Хранилище не инициализировано, выполняется ленивый запуск.")
                try:
                    await self.initialize()
                except Exception as e:
                    logging.error(f"[STATE] Не удалось инициализировать хранилище: {e}")
                    return
            if not self._initialized:
                logging.error("[STATE] Хранилище не инициализировано после ленивой инициализации, операция update_item не выполнена.")
                return

        try:
            # Если переданы дополнительные именованные аргументы, объединяем их с value
            if kwargs:
                if value is None:
                    value = {}
                if isinstance(value, dict):
                    value.update(kwargs)
                else:
                    value = kwargs

            async with self.atomic_operation():
                if key not in self.state:
                    logging.warning(f"[STATE] Попытка обновить несуществующий ключ: {key}")
                    self.state[key] = value
                else:
                    current = self.state[key]
                    if isinstance(current, dict):
                        current.update(value)
                    else:
                        self.state[key] = value
        except Exception as e:
            logging.error(f"[STATE] Ошибка при обновлении состояния для {key}: {str(e)}")
            raise

    @measure_time()
    async def delete_item(self, key: str):
        """Удаляет состояние по ключу"""
        async with self.atomic_operation():
            if key in self.state:
                del self.state[key]

    async def get_all_items(self) -> Dict:
        """Получает все состояния"""
        return self.state

    @measure_time()
    async def cleanup_old_items(self, max_age_hours: float = 0.5):  # 30 минут по умолчанию
        """Очищает старые состояния"""
        async with self.atomic_operation():
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600  # Переводим часы в секунды

            keys_to_delete = []
            for key, value in self.state.items():
                if isinstance(value, dict):
                    # Проверяем разные поля для времени
                    timestamp = value.get("timestamp") or value.get("updated_at") or value.get("created_at")
                    if timestamp:
                        if isinstance(timestamp, str):
                            try:
                                # Пробуем преобразовать строку в timestamp
                                timestamp = datetime.fromisoformat(timestamp).timestamp()
                            except ValueError:
                                # Если не получилось, пропускаем
                                continue

                        age = current_time - float(timestamp)
                        age_minutes = age / 60

                        if age > max_age_seconds:
                            keys_to_delete.append(key)
                            logging.info(f"[STATE] Помечен на удаление ключ: {key} (возраст: {age_minutes:.1f} минут)")

            for key in keys_to_delete:
                del self.state[key]
                logging.info(f"[STATE] Удален ключ: {key}")

            if keys_to_delete:
                logging.info(f"[STATE] Удалено {len(keys_to_delete)} старых записей")

# Создаем экземпляр хранилища состояний
state_storage = StateStorage(os.path.join(os.getcwd(), "state.json"))
