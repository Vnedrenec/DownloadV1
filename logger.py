import os
import sys
import logging
import asyncio
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
import queue
from metrics import measure_time
import aiofiles
import aiofiles.os

# Настройка путей для логов
LOG_DIR = os.path.join(os.path.dirname(__file__), 'downloads', 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'app.log')

# Создаем очередь для логов
log_queue = queue.Queue()

class AsyncRotatingFileHandler(RotatingFileHandler):
    """Асинхронный обработчик файлов с ротацией"""
    
    async def do_rollover(self):
        """Асинхронная ротация файлов"""
        if await aiofiles.os.path.exists(self.baseFilename):
            if self.stream:
                self.stream.close()
                self.stream = None
                
            if await aiofiles.os.path.exists(self.baseFilename + ".1"):
                await aiofiles.os.remove(self.baseFilename + ".1")
            await aiofiles.os.rename(self.baseFilename, self.baseFilename + ".1")
            
            self.mode = 'a'
            self.stream = self._open()

def check_directory_permissions(path: str, create: bool = True) -> bool:
    """Проверка и создание директории с нужными правами"""
    try:
        if create:
            os.makedirs(path, mode=0o755, exist_ok=True)
        
        # Проверка прав на запись
        test_file = os.path.join(path, '.write_test')
        try:
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except Exception as e:
            logging.error(f"No write access to {path}: {str(e)}")
            return False
            
        # Проверка прав на чтение
        try:
            os.listdir(path)
        except Exception as e:
            logging.error(f"No read access to {path}: {str(e)}")
            return False
            
        return True
    except Exception as e:
        logging.error(f"Error checking directory {path}: {str(e)}")
        return False

async def check_directory_permissions_async(path: str, create: bool = True) -> bool:
    """Проверка и создание директории с нужными правами"""
    try:
        if create:
            await asyncio.to_thread(os.makedirs, path, mode=0o755, exist_ok=True)
        
        # Проверка прав на запись
        test_file = os.path.join(path, '.write_test')
        try:
            async with asyncio.Lock():
                await asyncio.to_thread(lambda: open(test_file, 'w').write('test'))
                await asyncio.to_thread(os.remove, test_file)
        except Exception as e:
            logging.error(f"No write access to {path}: {str(e)}")
            return False
            
        # Проверка прав на чтение
        try:
            await asyncio.to_thread(os.listdir, path)
        except Exception as e:
            logging.error(f"No read access to {path}: {str(e)}")
            return False
            
        return True
    except Exception as e:
        logging.error(f"Error checking directory {path}: {str(e)}")
        return False

@measure_time()
async def init_logging():
    """Инициализация логирования"""
    try:
        # Проверяем и создаем директорию для логов
        if not await check_directory_permissions_async(LOG_DIR):
            raise Exception(f"Cannot access logs directory: {LOG_DIR}")
            
        # Настраиваем логирование
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        
        # Создаем обработчики
        queue_handler = QueueHandler(log_queue)
        file_handler = AsyncRotatingFileHandler(
            LOG_FILE,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=3
        )
        console_handler = logging.StreamHandler()
        
        # Настраиваем форматирование
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Создаем слушателя очереди
        listener = QueueListener(
            log_queue,
            file_handler,
            console_handler,
            respect_handler_level=True
        )
        
        # Настраиваем корневой логгер
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.addHandler(queue_handler)
        
        # Запускаем слушателя
        listener.start()
        
        logging.info("Logging initialized")
        
    except Exception as e:
        print(f"Failed to initialize logging: {str(e)}", file=sys.stderr)
        sys.exit(1)

@measure_time()
async def clean_old_logs():
    """Очистка текущего лог-файла"""
    try:
        async with aiofiles.open(LOG_FILE, 'w', encoding='utf-8') as f:
            await f.truncate(0)
        await init_logging()
        logging.info("[LOG] Лог очищен")
        return {"status": "success", "message": "Лог успешно очищен"}
    except Exception as e:
        error_msg = f"Ошибка при очистке лога: {str(e)}"
        logging.error(f"[CLEAR_LOG] {error_msg}", exc_info=True)
        return {"status": "error", "message": error_msg}

@measure_time()
async def clean_old_logs_rotated(max_size_mb: int = 10):
    """Очистка старых лог-файлов"""
    try:
        # Проверяем размер файла логов
        if await aiofiles.os.path.exists(LOG_FILE):
            stat = await aiofiles.os.stat(LOG_FILE)
            size_mb = stat.st_size / (1024 * 1024)
            if size_mb > max_size_mb:
                # Архивируем текущий файл
                backup_file = f"{LOG_FILE}.1"
                if await aiofiles.os.path.exists(backup_file):
                    await aiofiles.os.remove(backup_file)
                await aiofiles.os.rename(LOG_FILE, backup_file)
                
                # Создаем новый файл
                async with aiofiles.open(LOG_FILE, 'a'):
                    pass
                logging.info("[LOGS] Rotated log file")
    except Exception as e:
        logging.error(f"[LOGS] Error cleaning logs: {str(e)}", exc_info=True)

@measure_time()
async def check_directory_permissions_async_rotated():
    """Асинхронная проверка прав доступа к директориям"""
    try:
        # Проверяем права на запись в директорию логов
        stat = await aiofiles.os.stat(LOG_DIR)
        if not stat.st_mode & 0o200:  # Проверка на запись
            logging.error(f"[PERM] No write access to log directory: {LOG_DIR}")
            return False
            
        # Проверяем права на запись в файл логов
        if await aiofiles.os.path.exists(LOG_FILE):
            stat = await aiofiles.os.stat(LOG_FILE)
            if not stat.st_mode & 0o200:  # Проверка на запись
                logging.error(f"[PERM] No write access to log file: {LOG_FILE}")
                return False
            
        return True
    except Exception as e:
        logging.error(f"[PERM] Error checking permissions: {str(e)}", exc_info=True)
        return False
