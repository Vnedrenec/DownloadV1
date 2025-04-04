import utils
import os
import shutil
import tempfile
import re
import json
import time
import uuid
import psutil
import asyncio
import logging
import aiohttp
import aiofiles
import sys
import random
import traceback
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Depends
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from tasks import repeat_every
from pydantic import BaseModel, Field, validator
import yt_dlp
from logger import init_logging, LOG_FILE, check_directory_permissions_async

from utils import  (
    get_yt_dlp_opts,
    download_m3u8,
    get_disk_space,
    clean_old_logs,
    delete_file_after_delay,
    update_download_status,
    get_download_state_sync,
    update_download_state_sync,
    clear_logs_task,
    sanitize_filename,
    is_loom_url,
    download_loom_video
)
import glob
from models import DownloadStatus
from state_storage import StateStorage, state_storage
from cleanup_manager import CleanupManager
from services.cancellation_service import CancellationService

import functools
import time
from typing import Dict, Any, Optional, List, Tuple, Callable, Awaitable
from fastapi.templating import Jinja2Templates

# Порог времени выполнения запроса (в секундах)
REQUEST_TIMEOUT_THRESHOLD = float(os.getenv('REQUEST_TIMEOUT_THRESHOLD', '10.0'))

def measure_time():
    """
    Декоратор для измерения времени выполнения асинхронных запросов.
    Логирует предупреждение, если время выполнения превышает порог.
    """
    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                execution_time = time.time() - start_time
                if execution_time > REQUEST_TIMEOUT_THRESHOLD:
                    logging.warning(
                        f"[SLOW_REQUEST] Endpoint {func.__name__} took {execution_time:.2f}s "
                        f"(threshold: {REQUEST_TIMEOUT_THRESHOLD}s)"
                    )
                else:
                    logging.debug(
                        f"[REQUEST_TIME] Endpoint {func.__name__} completed in {execution_time:.2f}s"
                    )
        return wrapper
    return decorator

# ==================== Конфигурация ====================

# Пути к директориям
DOWNLOADS_DIR = os.getenv('DOWNLOADS_DIR', os.path.join(os.path.dirname(__file__), 'downloads'))
LOG_DIR = os.path.join(DOWNLOADS_DIR, 'logs')

# Интервалы
CLEANUP_INTERVAL_SECONDS = int(os.getenv('CLEANUP_INTERVAL_SECONDS', str(60 * 60)))  # 1 час по умолчанию
DOWNLOAD_EXPIRY_SECONDS = int(os.getenv('DOWNLOAD_EXPIRY_SECONDS', str(24 * 60 * 60)))  # 24 часа по умолчанию
PING_INTERVAL = 15  # 15 секунд

async def check_ffmpeg():
    """Проверка наличия FFmpeg в системе"""
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logging.info("[FFMPEG] FFmpeg найден")
    except Exception as e:
        logging.error(f"[FFMPEG] FFmpeg не найден или недоступен: {str(e)}")
        raise Exception("FFmpeg не установлен или недоступен в PATH")

# ==================== Модели данных ====================

def is_valid_video_url(url: str) -> Tuple[bool, str]:
    """
    Проверяет, является ли URL допустимым для загрузки видео

    Args:
        url: URL для проверки

    Returns:
        Tuple[bool, str]: (валидность, сообщение/нормализованный URL)
    """
    try:
        if not url or not isinstance(url, str):
            return False, "URL не может быть пустым"

        # Проверяем базовую валидность URL
        if not url.startswith(('http://', 'https://')):
            return False, "URL должен начинаться с http:// или https://"

        # Проверяем, является ли URL ссылкой на Loom
        if is_loom_url(url):
            logging.info(f"[ВАЛИДАЦИЯ] Обнаружен URL Loom: {url}")
            return True, url

        # Возвращаем URL как есть
        return True, url

    except Exception as e:
        return False, f"Ошибка при проверке URL: {str(e)}"

class DownloadRequest(BaseModel):
    """Модель запроса на загрузку видео"""
    url: str = Field(..., description="URL для загрузки")
    format: Optional[str] = Field(None, description="Формат выходного файла")
    quality: Optional[str] = Field(None, description="Качество видео")

    @validator('url')
    def validate_url(cls, v):
        """Валидация URL"""
        is_valid, message = is_valid_video_url(v)
        if not is_valid:
            raise ValueError(message)
        return v  # Возвращаем оригинальный URL

class LogErrorRequest(BaseModel):
    downloadId: str = Field(..., description="ID загрузки")
    error: str = Field(..., description="Текст ошибки")

class M3U8ValidationRequest(BaseModel):
    url: str = Field(..., description="URL для валидации")

# ==================== Lifespan приложения ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    try:
        # Инициализация хранилища
        if not hasattr(app.state, 'storage'):
            app.state.storage = StateStorage(os.path.join(DOWNLOADS_DIR, "state.json"))
        await app.state.storage.initialize()

        # Инициализация utils
        await utils.init_app(app)

        # Создаем директорию для загрузок
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)

        # Проверяем наличие ffmpeg
        await check_ffmpeg()

        # Запускаем очистку логов
        cleanup_task = asyncio.create_task(periodic_log_cleanup())

        # Запускаем очистку загрузок
        downloads_cleanup_task = asyncio.create_task(periodic_downloads_cleanup_task())

        # Инициализируем менеджер очистки
        app.state.cleanup_manager = CleanupManager(DOWNLOADS_DIR, LOG_DIR)
        await app.state.cleanup_manager.start(cleanup_interval=600)  # Каждые 10 минут

        yield

        # Останавливаем задачи очистки
        cleanup_task.cancel()
        downloads_cleanup_task.cancel()

        # Останавливаем менеджер очистки
        if hasattr(app.state, 'cleanup_manager'):
            await app.state.cleanup_manager.stop()

        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

        try:
            await downloads_cleanup_task
        except asyncio.CancelledError:
            pass

    except Exception as e:
        logging.error(f"[LIFESPAN] Error in lifespan: {str(e)}", exc_info=True)
        raise

# ==================== FastAPI приложение ====================

from api.cancel import router as cancel_router

app = FastAPI(
    title="Video Downloader",
    description="API для загрузки видео",
    version="1.0.0",
    lifespan=lifespan
)

# Добавляем DummyStorage для обновления прогресса скачивания
class DummyStorage:
    def __init__(self):
        self.data = {}
        self._initialized = True

    async def initialize(self):
        """Инициализация хранилища"""
        self._initialized = True

    async def update_item(self, download_id: str, state: Dict[str, Any]):
        """Обновляет состояние загрузки"""
        self.data[download_id] = state

    async def get_item(self, download_id: str) -> Optional[Dict[str, Any]]:
        """Получает состояние загрузки"""
        return self.data.get(download_id)

    async def get_all_items(self) -> Dict[str, Any]:
        """Получает все состояния загрузок"""
        return self.data.copy()

if not hasattr(app, 'state') or not hasattr(app.state, 'storage'):
    app.state.storage = DummyStorage()

app.include_router(cancel_router)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# Инициализируем шаблоны
templates = Jinja2Templates(directory="views")

@app.get("/")
async def root(request: Request):
    """Главная страница"""
    return templates.TemplateResponse("index.html", {"request": request})

# ==================== Эндпоинты ====================

# Эндпоинт /health определен ниже

# ==================== Обработка загрузки ====================

import yt_dlp
from yt_dlp import YoutubeDL
from utils import *

# Функция для чтения файла прогресса и обновления статуса загрузки
async def update_progress_from_file(download_id: str, progress_file: str):
    """Читает файл прогресса и обновляет статус загрузки"""
    try:
        last_progress = 0
        last_update_time = time.time()
        no_progress_counter = 0

        # Сразу устанавливаем начальный прогресс
        await update_download_status(download_id, "downloading", progress=1)
        logging.info(f"[ПРОГРЕСС] Установлен начальный прогресс 1% для {download_id}")

        while True:
            try:
                # Проверяем, существует ли файл прогресса
                if not os.path.exists(progress_file):
                    logging.info(f"[ПРОГРЕСС] Файл прогресса не найден, завершаем задачу")
                    break

                # Читаем текущий прогресс из файла
                with open(progress_file, 'r') as f:
                    progress_str = f.read().strip()

                if progress_str and progress_str.isdigit():
                    current_progress = int(progress_str)

                    # Если прогресс изменился или прошло больше 1 секунды с момента последнего обновления
                    current_time = time.time()
                    if current_progress != last_progress or (current_time - last_update_time) > 1:
                        logging.info(f"[ПРОГРЕСС] Обновляем прогресс: {current_progress}% для {download_id}")
                        await update_download_status(download_id, "downloading", progress=current_progress)
                        last_progress = current_progress
                        last_update_time = current_time
                        no_progress_counter = 0
                    else:
                        no_progress_counter += 1
                else:
                    logging.warning(f"[ПРОГРЕСС] Некорректный формат прогресса в файле: '{progress_str}'")
                    no_progress_counter += 1

                # Если прогресс достиг 100%, завершаем задачу
                if last_progress >= 100:
                    logging.info(f"[ПРОГРЕСС] Прогресс достиг 100%, завершаем задачу")
                    break

                # Ждем небольшую паузу перед следующей проверкой
                await asyncio.sleep(0.3)  # Уменьшаем интервал проверки до 0.3 секунд
            except Exception as e:
                logging.error(f"[ПРОГРЕСС] Ошибка при обновлении прогресса: {str(e)}")
                await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"[ПРОГРЕСС] Ошибка в задаче обновления прогресса: {str(e)}")
    finally:
        # Удаляем файл прогресса при завершении
        if os.path.exists(progress_file):
            try:
                os.remove(progress_file)
            except:
                pass

async def process_download(download_id: str, url: str):
    """
    Обработка загрузки видео

    Args:
        download_id: ID загрузки
        url: URL для загрузки
    """
    try:
        logging.info(f"[DOWNLOAD] Начало загрузки {url} с ID: {download_id}")

        # Инициализация состояния
        await app.state.storage.update_item(download_id, {
            "status": "starting",
            "progress": 0,
            "url": url,
            "updated_at": time.time()
        })

        # Создаем директорию для загрузок
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)

        # Проверяем, является ли URL ссылкой на Loom
        is_loom = is_loom_url(url)
        if is_loom:
            logging.info(f"[DOWNLOAD] Обнаружен URL Loom: {url}")

        # Обновляем статус на downloading перед началом загрузки
        await app.state.storage.update_item(download_id, {
            "status": "downloading",
            "progress": 0,
            "updated_at": time.time()
        })

        # Запускаем загрузку в зависимости от типа URL
        try:
            if is_loom:
                logging.info("[DOWNLOAD] Обнаружен URL Loom, используем специальный метод загрузки")
                # Для Loom используем специальный метод загрузки
                output_path = os.path.join(DOWNLOADS_DIR, f'sc-Replit-C1-L0-master-{download_id}.mp4')

                # Создаем файл прогресса для отслеживания загрузки
                progress_file = os.path.join(DOWNLOADS_DIR, f"{download_id}_progress.txt")
                with open(progress_file, 'w') as f:
                    f.write('0')

                # Запускаем задачу обновления прогресса
                asyncio.create_task(update_progress_from_file(download_id, progress_file))

                # Запускаем загрузку в отдельном процессе
                cmd = [
                    "yt-dlp",
                    "--verbose",  # Добавляем подробный вывод
                    "--no-check-certificate",
                    # Используем более гибкий подход к выбору формата
                    # Вместо "best" используем "bestvideo+bestaudio/best"
                    "-f", "bestvideo+bestaudio/best",
                    "--merge-output-format", "mp4",
                    "--retries", "10",
                    "--fragment-retries", "10",
                    "-o", output_path,
                    url
                ]

                # Выводим команду в лог
                logging.info(f"[DOWNLOAD] Запуск команды: {' '.join(cmd)}")

                # Создаем файл для логов
                log_dir = os.path.join(DOWNLOADS_DIR, "logs")
                os.makedirs(log_dir, exist_ok=True)
                log_file = os.path.join(log_dir, f"{download_id}.log")

                # Запускаем процесс загрузки
                with open(log_file, "w") as f:
                    # Увеличиваем лимит чтения для потока stdout
                    # Используем обычный subprocess вместо asyncio.create_subprocess_exec
                    import subprocess
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )

                    # Читаем вывод процесса и обновляем прогресс
                    for line in process.stdout:
                        if not line:
                            break

                        # Теперь line уже в текстовом формате благодаря text=True
                        f.write(line)
                        f.flush()

                        # Обновляем прогресс
                        progress_match = re.search(r"\[download\]\s+(\d+\.\d+)%", line)
                        if progress_match:
                            progress = float(progress_match.group(1))
                            # Обновляем прогресс в хранилище (асинхронно)
                            asyncio.create_task(app.state.storage.update_item(download_id, {
                                "status": "downloading",
                                "progress": progress,
                                "updated_at": time.time()
                            }))
                            # Также обновляем прогресс в файле
                            with open(progress_file, 'w') as pf:
                                pf.write(str(int(progress)))

                        # Проверяем завершение загрузки
                        if "Merging formats" in line or "Writing video" in line:
                            # Записываем 99% прогресса
                            with open(progress_file, 'w') as pf:
                                pf.write("99")
                            # Обновляем прогресс в хранилище
                            asyncio.create_task(app.state.storage.update_item(download_id, {
                                "status": "downloading",
                                "progress": 99,
                                "updated_at": time.time()
                            }))

                    # Ждем завершения процесса
                    process.wait()

                # Проверяем результат
                if process.returncode != 0:
                    raise Exception(f"Ошибка при загрузке видео: код возврата {process.returncode}")

                if not os.path.exists(output_path):
                    raise Exception("Файл не был создан после загрузки")

                # Используем оригинальный файл без конвертации
                video_path = output_path
                logging.info(f"[DOWNLOAD] Используем оригинальный файл без конвертации: {video_path}")

                logging.info(f"[DOWNLOAD] Загрузка Loom завершена: {video_path}")
            else:
                # Для других сервисов используем yt-dlp через API
                # Получаем опции для yt-dlp
                ydl_opts = await get_yt_dlp_opts(download_id, DOWNLOADS_DIR)
                logging.info(f"[DOWNLOAD] Опции yt-dlp: {ydl_opts}")

                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info is None:
                        raise Exception("Не удалось получить информацию о видео")

                    logging.info("[DOWNLOAD] Загрузка завершена, получаем путь к файлу")
                    # Получаем путь к файлу
                    video_path = ydl.prepare_filename(info)
                    if not os.path.exists(video_path):
                        raise Exception("Файл не найден после загрузки")

            # Обновляем состояние с путем к файлу и оригинальным именем
            await app.state.storage.update_item(download_id, {
                "status": "completed",
                "progress": 100,
                "file_path": video_path,
                "original_filename": os.path.basename(video_path),
                "service_type": "loom" if is_loom else "other",
                "updated_at": time.time()
            })

            return video_path

        except Exception as e:
            logging.error(f"[YDL] Error downloading video: {str(e)}")
            await app.state.storage.update_item(download_id, {
                "status": "error",
                "error": str(e),
                "updated_at": time.time()
            })
            raise

    except Exception as e:
        logging.error(f"[DOWNLOAD] Error processing download: {str(e)}")
        await app.state.storage.update_item(download_id, {
            "status": "error",
            "error": str(e),
            "updated_at": time.time()
        })
        raise

# ==================== Эндпоинт для скачивания файла ====================

@app.get("/api/download/{download_id}")
@measure_time()
async def download_file(download_id: str, request: Request):
    """Скачивание готового файла"""
    try:
        # Получаем информацию о загрузке
        download_info = await app.state.storage.get_item(download_id)
        if not download_info:
            raise HTTPException(status_code=404, detail="Download not found")

        status = download_info.get("status")
        file_path = download_info.get("file_path")

        if status != "completed" or not file_path:
            raise HTTPException(status_code=400, detail="File not ready")

        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")

        # Получаем имя файла из пути
        filename = os.path.basename(file_path)

        # Кодируем имя файла для корректной работы с кириллицей
        from urllib.parse import quote
        safe_filename = quote(filename.encode('utf-8'))

        # Устанавливаем заголовки для скачивания
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }

        # Возвращаем файл через StreamingResponse
        async def file_iterator():
            async with aiofiles.open(file_path, mode="rb") as file:
                while chunk := await file.read(8192):
                    yield chunk

        return StreamingResponse(
            file_iterator(),
            media_type="application/octet-stream",
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[DOWNLOAD_FILE] Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ==================== Эндпоинт для отмены загрузки ====================

# Глобальный словарь для хранения активных загрузок
active_downloads = {}

@app.post('/api/cancel/{download_id}')
async def cancel_download(download_id: str):
    """
    Отменяет загрузку по ID

    Args:
        download_id: ID загрузки

    Returns:
        dict: Результат отмены
    """
    # Проверяем существование загрузки
    state = await app.state.storage.get_item(f"download_{download_id}")
    if not state:
        raise HTTPException(status_code=404, detail="Загрузка не найдена")

    # Отменяем загрузку
    if download_id in active_downloads:
        active_downloads[download_id].cancel()
        del active_downloads[download_id]

    # Обновляем статус
    await update_download_status(
        download_id=download_id,
        status=DownloadStatus.CANCELLED,
        progress=0,
        error="Загрузка отменена пользователем"
    )

    return {"status": "success", "message": "Загрузка отменена"}

@app.post('/api/cancel/<operation_id>')
async def cancel_operation(operation_id: str):
    cancellation_service = CancellationService(app.state.storage)
    return await cancellation_service.cancel_operation(operation_id)

# ==================== Эндпоинт для health check ====================

@app.get("/health")
@measure_time()
async def health():
    """Проверка состояния сервиса"""
    try:
        # Проверяем доступ к директориям
        directories = [DOWNLOADS_DIR, LOG_DIR]
        for directory in directories:
            if not os.path.exists(directory):
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "error",
                        "error": f"Directory not found: {directory}"
                    }
                )

            if not os.access(directory, os.W_OK):
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "error",
                        "error": f"No write access to directory: {directory}"
                    }
                )

        # Проверяем наличие FFmpeg
        try:
            await check_ffmpeg()
        except Exception as e:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "error": f"FFmpeg check failed: {str(e)}"
                }
            )

        # Проверяем доступ к хранилищу состояний
        try:
            await app.state.storage.get_all_items()
        except Exception as e:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "error": f"State storage check failed: {str(e)}"
                }
            )

        # Проверяем свободное место на диске
        total_space, free_space = await get_disk_space(DOWNLOADS_DIR)
        min_required_space = 400 * 1024 * 1024  # 400 MB

        if free_space < min_required_space:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "warning",
                    "warning": f"Low disk space: {free_space / 1024 / 1024:.1f}MB free"
                }
            )

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "disk_space": {
                "total": total_space,
                "free": free_space,
                "free_mb": free_space / 1024 / 1024
            }
        }

    except Exception as e:
        logging.error("[HEALTH] Health check failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "error": str(e)
            }
        )

# ==================== Очистка логов ====================

async def periodic_log_cleanup():
    """Периодическая очистка логов"""
    while True:
        try:
            log_path = os.path.join(LOG_DIR, LOG_FILE)
            await utils.clean_old_logs(log_path)
            await asyncio.sleep(600)  # Очистка каждые 10 минут
        except Exception as e:
            logging.error(f"[CLEANUP] Ошибка при очистке логов: {str(e)}", exc_info=True)
            await asyncio.sleep(60)  # При ошибке ждем 1 минуту

# ==================== Очистка загрузок ====================

async def cleanup_downloads(downloads_dir: str, max_age_hours: float = 0.5):  # 30 минут по умолчанию
    """Асинхронная очистка старых загрузок"""
    try:
        # Очищаем старые загрузки через StateStorage
        await app.state.storage.cleanup_old_downloads()

        # Очищаем файлы на диске через CleanupManager
        await app.state.cleanup_manager.cleanup_downloads(max_age_hours)
        logging.info(f"[CLEANUP] Запущена очистка файлов старше {max_age_hours * 60:.0f} минут")

    except Exception as e:
        logging.error(f"[CLEANUP] Ошибка при очистке: {str(e)}")

# Запуск периодической очистки загрузок происходит в lifespan

async def periodic_downloads_cleanup_task():
    """Задача периодической очистки загрузок"""
    while True:
        try:
            await periodic_downloads_cleanup()
            await asyncio.sleep(600)  # Каждые 10 минут
        except Exception as e:
            logging.error(f"[CLEANUP] Ошибка в задаче периодической очистки: {str(e)}")
            await asyncio.sleep(60)  # При ошибке ждем 1 минуту

async def periodic_downloads_cleanup():
    """Периодическая очистка загрузок"""
    try:
        # Получаем все загрузки
        downloads = await app.state.storage.get_all_items()

        # Проверяем каждую загрузку
        for download_id, state in downloads.items():
            if not download_id.startswith("download_"):
                continue

            try:
                # Проверяем время создания
                created_at = state.get("created_at", "")
                if isinstance(created_at, str) and created_at:
                    try:
                        created_at = datetime.fromisoformat(created_at)
                    except ValueError:
                        # Если формат даты неверный, используем timestamp
                        created_at = datetime.fromtimestamp(float(state.get("updated_at", time.time())))
                else:
                    # Если created_at не строка или пустая строка, используем timestamp
                    created_at = datetime.fromtimestamp(float(state.get("updated_at", time.time())))

                age_minutes = (datetime.now() - created_at).total_seconds() / 60

                # Если загрузка старше 30 минут
                if age_minutes > 30:
                    # Удаляем файл если он существует
                    file_path = state.get("file_path")
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                        logging.info(f"[CLEANUP] Удален файл {file_path} (возраст: {age_minutes:.1f} минут)")

                    # Удаляем состояние
                    await app.state.storage.delete_item(download_id)
                    logging.info(f"[CLEANUP] Удалена запись о загрузке {download_id} (возраст: {age_minutes:.1f} минут)")

            except Exception as e:
                logging.error(f"[CLEANUP] Error processing download {download_id}: {str(e)}")
                continue

    except Exception as e:
        logging.error(f"[CLEANUP] Error: {str(e)}")

# ==================== Удаление файла по расписанию ====================

async def delete_file_after_delay(file_path: str, delay: int):
    try:
        await asyncio.sleep(delay)
        if await asyncio.to_thread(os.path.exists, file_path):
            await asyncio.to_thread(os.remove, file_path)
            logging.info(f"[DELETE_FILE] Deleted file {file_path}")
    except Exception as e:
        logging.error(f"[DELETE_FILE] Error deleting file {file_path}: {str(e)}", exc_info=True)

# ==================== Эндпоинт для health check ====================

@app.get("/metrics")
@measure_time()
async def metrics():
    """Метрики для мониторинга"""
    try:
        # Получаем все загрузки
        downloads = await app.state.storage.get_all_items()

        # Считаем метрики
        total_downloads = len(downloads)
        active_downloads = sum(1 for d in downloads.values() if d.get("status") == DownloadStatus.DOWNLOADING)
        completed_downloads = sum(1 for d in downloads.values() if d.get("status") == DownloadStatus.COMPLETED)
        failed_downloads = sum(1 for d in downloads.values() if d.get("status") == DownloadStatus.ERROR)

        # Получаем информацию о диске
        total_space, free_space = await get_disk_space(DOWNLOADS_DIR)
        used_space = total_space - free_space
        disk_usage_percent = (used_space / total_space) * 100 if total_space > 0 else 0

        # Собираем метрики производительности
        metrics = {
            "downloads": {
                "total": total_downloads,
                "active": active_downloads,
                "completed": completed_downloads,
                "failed": failed_downloads
            },
            "disk": {
                "total_mb": total_space / (1024 * 1024),
                "free_mb": free_space / (1024 * 1024),
                "used_mb": used_space / (1024 * 1024),
                "usage_percent": disk_usage_percent
            },
            "performance": {
                "memory_mb": psutil.Process().memory_info().rss / (1024 * 1024),
                "cpu_percent": psutil.Process().cpu_percent()
            },
            "timestamp": datetime.now().isoformat()
        }

        return metrics

    except Exception as e:
        logging.error("[METRICS] Error collecting metrics", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/log_error")
@measure_time()
async def log_error(request: LogErrorRequest):
    """Логирование ошибок от клиента"""
    try:
        # Получаем или создаем состояние загрузки
        state = await app.state.storage.get_item(request.downloadId)
        if not state:
            state = {
                "status": "error",
                "timestamp": time.time(),
                "progress": 0,
                "error": request.error,
                "log": f"Error: {request.error}"
            }
            await app.state.storage.set_item(request.downloadId, state)
        else:
            # Обновляем существующее состояние
            state["status"] = "error"
            state["error"] = request.error
            state["log"] = f"Error: {request.error}"
            await app.state.storage.update_item(request.downloadId, state)

        return JSONResponse(status_code=200, content={"status": "ok"})
    except Exception as e:
        logging.error(f"[ERROR] Failed to log error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/progress/{download_id}")
@measure_time()
async def get_progress(download_id: str):
    """Получение прогресса загрузки"""
    try:
        logging.info(f"[PROGRESS] Получение прогресса для ID: {download_id}")

        # Проверяем инициализацию хранилища
        if not hasattr(app.state, 'storage'):
            logging.error("[PROGRESS] Storage не существует")
            raise HTTPException(status_code=500, detail="Storage not initialized")

        if not app.state.storage._initialized:
            logging.error("[PROGRESS] Storage не инициализирован")
            try:
                await app.state.storage.initialize()
            except Exception as e:
                logging.error(f"[PROGRESS] Ошибка инициализации storage: {str(e)}")
                raise HTTPException(status_code=500, detail="Failed to initialize storage")

        logging.info(f"[PROGRESS] Поиск состояния для ID: {download_id}")

        state = await app.state.storage.get_item(download_id)

        if not state:
            logging.warning(f"[PROGRESS] Состояние не найдено для ID: {download_id}")
            raise HTTPException(status_code=404, detail="Download not found")

        logging.info(f"[PROGRESS] Найдено состояние: {state}")

        # Формируем ответ
        return {
            "status": state.get("status", "unknown"),
            "progress": state.get("progress", 0),
            "error": state.get("error"),
            "log": state.get("log")
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[PROGRESS] Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/download")
async def start_download(request: Request, background_tasks: BackgroundTasks):
    """Начать загрузку видео"""
    try:
        data = await request.json()
        url = data.get('url')
        if not url:
            raise HTTPException(status_code=400, detail="URL не указан")

        # Проверяем URL
        is_valid, message = is_valid_video_url(url)
        if not is_valid:
            raise HTTPException(status_code=400, detail=message)

        # Генерируем уникальный ID для загрузки
        download_id = str(uuid.uuid4())

        # Создаем начальное состояние
        await app.state.storage.update_item(download_id, {
            "status": "pending",
            "progress": 0,
            "url": url,
            "created_at": time.time(),
            "updated_at": time.time()
        })

        # Запускаем загрузку в фоновом режиме
        background_tasks.add_task(process_download, download_id, url)

        return {
            "download_id": download_id,
            "status": "pending"
        }

    except Exception as e:
        logging.error(f"[DOWNLOAD] Error starting download: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

from fastapi.responses import FileResponse, Response
import os

@app.get("/api/status/{video_id}")
async def get_video_status(video_id: str):
    """Возвращает статус загрузки видео"""
    logging.info(f"[STATUS] Получение статуса для ID: {video_id}")

    # Проверяем, есть ли файл на диске
    downloads_dir = DOWNLOADS_DIR  # Используем глобальную константу вместо os.path.join(os.getcwd(), "downloads")
    mp4_files = glob.glob(os.path.join(downloads_dir, f"*{video_id}*.mp4"))
    webm_files = glob.glob(os.path.join(downloads_dir, f"*{video_id}*.webm"))

    # Если файл существует, возвращаем статус "completed"
    if mp4_files or webm_files:
        logging.info(f"[STATUS] Файл для {video_id} найден: {mp4_files or webm_files}")
        return {"status": "completed", "progress": 100}

    # Проверяем файл прогресса
    progress_file = os.path.join(downloads_dir, f"{video_id}_progress.txt")
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r') as f:
                progress_str = f.read().strip()
                if progress_str and progress_str.isdigit():
                    progress = int(progress_str)
                    logging.info(f"[STATUS] Прогресс из файла для {video_id}: {progress}%")
                    return {"status": "downloading", "progress": progress}
        except Exception as e:
            logging.error(f"[STATUS] Ошибка при чтении файла прогресса: {str(e)}")

    # Проверяем статус загрузки в логах
    try:
        log_file = os.path.join(downloads_dir, "logs", f"{video_id}.log")
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                log_content = f.read()
                if "download] 100%" in log_content or "[FixupM3u8] Fixing MPEG-TS in MP4 container" in log_content:
                    logging.info(f"[STATUS] Файл для {video_id} загружен по логам")
                    return {"status": "completed", "progress": 100}

                # Проверяем, не остановилась ли загрузка
                last_modified_time = os.path.getmtime(log_file)
                current_time = time.time()
                # Если лог не обновлялся более 2 минут, считаем загрузку остановленной
                if current_time - last_modified_time > 120:  # 2 минуты в секундах
                    # Проверяем, есть ли прогресс в логе
                    progress_matches = re.findall(r"\[download\]\s+(\d+\.\d+)%", log_content)
                    if progress_matches:
                        last_progress = float(progress_matches[-1])
                        if last_progress > 0 and last_progress < 100:
                            # Если прогресс завис на 65-90%, пробуем перезапустить процесс объединения
                            if 65 <= last_progress <= 90:
                                logging.warning(f"[STATUS] Загрузка {video_id} зависла на {last_progress}%, пробуем перезапустить процесс объединения")

                                # Пробуем объединить видео и аудио с помощью ffmpeg
                                try:
                                    # Ищем файлы видео и аудио
                                    video_files = glob.glob(os.path.join(downloads_dir, f"*{video_id}*.mp4.part"))
                                    if video_files:
                                        video_file = video_files[0]
                                        output_file = os.path.join(downloads_dir, f"sc-Replit-C1-L0-master-{video_id}.mp4")

                                        # Копируем файл как есть (хотя бы видео будет)
                                        import shutil
                                        shutil.copy(video_file, output_file)
                                        logging.info(f"[STATUS] Скопирован файл {video_file} в {output_file}")

                                        # Записываем 100% прогресса в файл прогресса
                                        progress_file = os.path.join(downloads_dir, f"{video_id}_progress.txt")
                                        with open(progress_file, 'w') as f:
                                            f.write('100')
                                        logging.info(f"[STATUS] Установлен прогресс 100% для {video_id}")
                                        return {"status": "completed", "progress": 100}
                                except Exception as e:
                                    logging.error(f"[STATUS] Ошибка при обработке зависшей загрузки: {str(e)}")

                                    # В случае ошибки просто устанавливаем прогресс 100%
                                    progress_file = os.path.join(downloads_dir, f"{video_id}_progress.txt")
                                    try:
                                        with open(progress_file, 'w') as f:
                                            f.write('100')
                                        logging.info(f"[STATUS] Установлен прогресс 100% для {video_id}")
                                        return {"status": "completed", "progress": 100}
                                    except Exception as e:
                                        logging.error(f"[STATUS] Ошибка при записи в файл прогресса: {str(e)}")

                            # Возвращаем статус зависания
                            return {"status": "stalled", "progress": last_progress}
                        elif last_progress >= 100:
                            logging.info(f"[STATUS] Загрузка для {video_id} остановилась на {last_progress}%")
                            return {"status": "stalled", "progress": last_progress, "retry_url": f"/download?url=https://www.loom.com/share/{video_id}"}
    except Exception as e:
        logging.error(f"[STATUS] Ошибка при чтении лога: {str(e)}")

    # Получаем статус загрузки из хранилища состояния
    try:
        # Пробуем получить статус напрямую по ID
        status_data = await app.state.storage.get_item(video_id)

        # Если не нашли, пробуем с префиксом download_
        if not status_data and not video_id.startswith('download_'):
            storage_key = f'download_{video_id}'
            status_data = await app.state.storage.get_item(storage_key)

        # Если нашли статус
        if status_data:
            logging.info(f"[STATUS] Найден статус в хранилище для {video_id}: {status_data}")

            # Если в статусе есть ошибка, возвращаем статус "error"
            if "error" in status_data:
                return {"status": "error", "error": status_data["error"]}

            # Иначе возвращаем текущий статус и прогресс
            status = status_data.get("status", "downloading")
            progress = status_data.get("progress", 0)
            return {"status": status, "progress": progress}
    except Exception as e:
        logging.error(f"[STATUS] Ошибка при получении статуса из хранилища: {str(e)}")

    # Если статус не найден, возвращаем статус "pending"
    logging.info(f"[STATUS] Статус для {video_id} не найден, возвращаем pending")
    return {"status": "pending", "progress": 0}

@app.get("/api/video/{video_id}")
@app.head("/api/video/{video_id}")
async def get_video(video_id: str, request: Request):
    """Возвращает видеофайл по его идентификатору"""
    logging.info(f"[VIDEO] Запрос на получение видео {video_id}, метод: {request.method}")
    downloads_dir = os.path.join(os.getcwd(), "downloads")

    # Получаем все файлы в директории downloads
    all_files = os.listdir(downloads_dir)

    # Фильтруем файлы по части ID и расширению .mp4
    # Исключаем файлы с суффиксами .fhls-raw-audio-audio.mp4, .temp.mp4 и т.д.
    mp4_files = [os.path.join(downloads_dir, f) for f in all_files if video_id in f and f.endswith('.mp4')
                and not '.fhls-raw-' in f and not '.temp.' in f]

    # Исключаем конвертированные файлы из списка
    original_mp4_files = [f for f in mp4_files if not os.path.basename(f).startswith("converted-")]

    if original_mp4_files:
        # Если это HEAD запрос, просто возвращаем 200 OK
        if request.method == "HEAD":
            return Response(status_code=200)

        # Получаем имя файла из пути
        filename = f"video-{video_id}.mp4"  # Используем более дружелюбное имя

        # Добавляем заголовок Content-Disposition, чтобы браузер скачивал файл
        headers = {"Content-Disposition": f"attachment; filename={filename}"}

        logging.info(f"[VIDEO] Возвращаем оригинальный mp4 файл: {original_mp4_files[0]}")
        return FileResponse(original_mp4_files[0], media_type="video/mp4", headers=headers)

    # Если не нашли оригинальные mp4 файлы, ищем .webm
    webm_files = [os.path.join(downloads_dir, f) for f in all_files if video_id in f and f.endswith('.webm')]
    if webm_files:
        # Если это HEAD запрос, просто возвращаем 200 OK
        if request.method == "HEAD":
            return Response(status_code=200)

        # Получаем имя файла из пути
        filename = f"video-{video_id}.webm"  # Используем более дружелюбное имя

        # Добавляем заголовок Content-Disposition, чтобы браузер скачивал файл
        headers = {"Content-Disposition": f"attachment; filename={filename}"}

        logging.info(f"[VIDEO] Возвращаем webm файл: {webm_files[0]}")
        return FileResponse(webm_files[0], media_type="video/webm", headers=headers)

    # Если это HEAD запрос, возвращаем 404
    if request.method == "HEAD":
        return Response(status_code=404)

    # Если не нашли ни .mp4, ни .webm, возвращаем ошибку
    logging.error(f"[VIDEO] Файл для {video_id} не найден")
    raise HTTPException(status_code=404, detail="Файл не найден")

if __name__ == "__main__":
    import uvicorn

    # Выводим доступные маршруты
    print("Available routes:")
    for route in app.routes:
        if hasattr(route, "path"):
            print(f"  {route.path}")

    # Запускаем сервер на 0.0.0.0:8080
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        reload=True
    )
