import os
import time
import logging
import asyncio
from typing import Optional
import aiofiles
import aiofiles.os
from metrics import measure_time

class CleanupManager:
    """Менеджер очистки файлов и логов"""
    
    def __init__(self, downloads_dir: str, logs_dir: str):
        self.downloads_dir = downloads_dir
        self.logs_dir = logs_dir
        self._cleanup_task: Optional[asyncio.Task] = None
        
    @measure_time()
    async def start(self, cleanup_interval: int):
        """Запускает периодическую очистку"""
        if self._cleanup_task and not self._cleanup_task.done():
            logging.info("[CLEANUP] Задача очистки уже запущена")
            return
            
        logging.info(f"[CLEANUP] Запуск задачи очистки с интервалом {cleanup_interval} секунд")
        self._cleanup_task = asyncio.create_task(
            self._periodic_cleanup(cleanup_interval)
        )
        
    @measure_time()
    async def stop(self):
        """Останавливает периодическую очистку"""
        if self._cleanup_task:
            logging.info("[CLEANUP] Остановка задачи очистки")
            self._cleanup_task.cancel()
            try:
                await asyncio.wait_for(self._cleanup_task, timeout=10.0)
                logging.info("[CLEANUP] Задача очистки успешно остановлена")
            except asyncio.TimeoutError:
                logging.error("[CLEANUP] Таймаут при остановке задачи очистки")
            except asyncio.CancelledError:
                logging.info("[CLEANUP] Задача очистки отменена")
            except Exception as e:
                logging.error(f"[CLEANUP] Ошибка при остановке задачи очистки: {str(e)}")
            finally:
                self._cleanup_task = None
            
    async def _periodic_cleanup(self, interval: int):
        """Периодическая очистка"""
        while True:
            try:
                await self.cleanup_all()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[CLEANUP] Error in periodic cleanup: {str(e)}")
                await asyncio.sleep(60)  # Ждем минуту перед повторной попыткой
                
    @measure_time()
    async def cleanup_all(self):
        """Выполняет полную очистку"""
        try:
            # Очищаем старые файлы загрузок
            await self.cleanup_downloads()
            # Очищаем старые логи
            await self.cleanup_logs()
        except Exception as e:
            logging.error(f"[CLEANUP] Error during cleanup: {str(e)}")
            
    @measure_time()
    async def cleanup_downloads(self, max_age_hours: int = 24):  # 24 часа по умолчанию
        """Очистка старых загрузок"""
        try:
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            files = await aiofiles.os.listdir(self.downloads_dir)
            for filename in files:
                if filename.endswith('.json') or filename.startswith('.') or filename == 'logs':
                    continue
                    
                file_path = os.path.join(self.downloads_dir, filename)
                try:
                    if not await aiofiles.os.path.isfile(file_path):
                        continue
                        
                    stat = await aiofiles.os.stat(file_path)
                    if current_time - stat.st_mtime > max_age_seconds:
                        await aiofiles.os.remove(file_path)
                        logging.info(f"[CLEANUP] Deleted old file: {filename}")
                except Exception as e:
                    logging.error(f"[CLEANUP] Error processing file {filename}: {str(e)}")
                    
        except Exception as e:
            logging.error(f"[CLEANUP] Error cleaning downloads: {str(e)}")
            
    @measure_time()
    async def cleanup_logs(self, max_age_hours: int = 24):  # 24 часа по умолчанию
        """Очистка старых логов"""
        try:
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            files = await aiofiles.os.listdir(self.logs_dir)
            for filename in files:
                if not filename.endswith('.log'):
                    continue
                    
                file_path = os.path.join(self.logs_dir, filename)
                try:
                    if not await aiofiles.os.path.isfile(file_path):
                        continue
                        
                    stat = await aiofiles.os.stat(file_path)
                    if current_time - stat.st_mtime > max_age_seconds:
                        await aiofiles.os.remove(file_path)
                        logging.info(f"[CLEANUP] Deleted old log: {filename}")
                except Exception as e:
                    logging.error(f"[CLEANUP] Error processing log {filename}: {str(e)}")
                    
        except Exception as e:
            logging.error(f"[CLEANUP] Error cleaning logs: {str(e)}")
