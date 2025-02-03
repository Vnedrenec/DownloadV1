# Стандартные библиотеки для работы с системой
import os
import sys
import shutil
import tempfile
import re

# Стандартные библиотеки для работы с данными
import json
import uuid
import time
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks

# FastAPI и зависимости
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import HTTPException
from sse_starlette.sse import EventSourceResponse

# Внешние библиотеки
import yt_dlp

# Константы для логирования
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
LOG_FILE = 'downloads.log'
LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "logs"))

# Директории для файлов
DOWNLOADS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "downloads"))

def init_logging():
    """Инициализация логирования"""
    try:
        # Создаем директорию для логов, если её нет
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, LOG_FILE)
        
        # Создаем форматтер для логов
        formatter = logging.Formatter(LOG_FORMAT)
        
        # Настраиваем корневой логгер
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        
        # Очищаем существующие хендлеры
        root_logger.handlers.clear()
        
        # Добавляем хендлер для вывода в консоль
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        
        # Добавляем хендлер для записи в файл
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        
        logging.info("[LOGGING] Логирование инициализировано")
        return True
    except Exception as e:
        print(f"Ошибка при инициализации логирования: {str(e)}")
        return False

# Инициализируем логгер
logger = logging.getLogger('app')

# Создаем API роутер
class DownloadManager:
    def __init__(self):
        self.downloads: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.update_queues: Dict[str, asyncio.Queue] = {}
        logging.info("[DOWNLOAD_MANAGER] Initialized")

    async def create_update_queue(self, download_id: str) -> asyncio.Queue:
        """Создает очередь обновлений для загрузки"""
        queue = asyncio.Queue()
        self.update_queues[download_id] = queue
        return queue
    
    def remove_update_queue(self, download_id: str):
        """Удаляет очередь обновлений"""
        if download_id in self.update_queues:
            del self.update_queues[download_id]

    async def update_download_state(self, download_id: str, state: Dict[str, Any]):
        """Асинхронно обновляет состояние загрузки"""
        try:
            async with self._lock:
                self.downloads[download_id] = state
                # Отправляем обновление в очередь, если она существует
                if download_id in self.update_queues:
                    await self.update_queues[download_id].put(state.copy())
                logging.info(f"[DOWNLOAD_MANAGER] State updated for {download_id}: {state}")
        except Exception as e:
            logging.error(f"[DOWNLOAD_MANAGER] Error updating state for {download_id}: {str(e)}")
            raise

    def update_download_state_sync(self, download_id: str, state: Dict[str, Any]):
        """Синхронно обновляет состояние загрузки"""
        try:
            self.downloads[download_id] = state
            # Создаем футуру для отправки в очередь
            if download_id in self.update_queues:
                future = asyncio.run_coroutine_threadsafe(
                    self.update_queues[download_id].put(state.copy()),
                    asyncio.get_event_loop()
                )
                try:
                    future.result(timeout=1.0)  # Ждем максимум 1 секунду
                except Exception as e:
                    logging.error(f"[MANAGER] Error sending update to queue: {str(e)}")
            logging.info(f"[DOWNLOAD_MANAGER] State updated (sync) for {download_id}: {state}")
        except Exception as e:
            logging.error(f"[DOWNLOAD_MANAGER] Error updating state (sync) for {download_id}: {str(e)}")
            raise

    async def get_download_state(self, download_id: str) -> Optional[Dict[str, Any]]:
        """Асинхронно получает состояние загрузки"""
        try:
            async with self._lock:
                state = self.downloads.get(download_id, {}).copy()
                logging.info(f"[DOWNLOAD_MANAGER] Got state for {download_id}: {state}")
                return state
        except Exception as e:
            logging.error(f"[DOWNLOAD_MANAGER] Error getting state for {download_id}: {str(e)}")
            raise

    def get_download_state_sync(self, download_id: str) -> Optional[Dict[str, Any]]:
        """Синхронно получает состояние загрузки"""
        try:
            state = self.downloads.get(download_id, {}).copy()
            logging.info(f"[DOWNLOAD_MANAGER] Got state (sync) for {download_id}: {state}")
            return state
        except Exception as e:
            logging.error(f"[DOWNLOAD_MANAGER] Error getting state (sync) for {download_id}: {str(e)}")
            raise

    async def delete_download_state(self, download_id: str):
        """Удалить состояние загрузки"""
        try:
            async with self._lock:
                if download_id in self.downloads:
                    del self.downloads[download_id]
                    logging.info(f"[DOWNLOAD_MANAGER] State deleted for {download_id}")
        except Exception as e:
            logging.error(f"[DOWNLOAD_MANAGER] Error deleting state for {download_id}: {str(e)}")
            raise

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    try:
        # Инициализируем логирование
        if not init_logging():
            raise Exception("Не удалось инициализировать логирование")
            
        # Инициализируем менеджер загрузок
        app.state.manager = DownloadManager()
        logging.info("[STARTUP] Download manager initialized")
        
        # Создаем директорию для загрузок
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        logging.info(f"[STARTUP] Downloads directory created: {DOWNLOADS_DIR}")
        
        yield
        
    except Exception as e:
        logging.error(f"[STARTUP] Error during startup: {str(e)}")
        raise
    finally:
        logging.info("[SHUTDOWN] Application shutting down")

# Создаем FastAPI приложение
app = FastAPI(
    title="Video Downloader",
    description="Сервис для скачивания видео",
    version="1.0.0",
    lifespan=lifespan
)

# Подключаем CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# Определяем базовый роут для HTML страницы
@app.get("/")
async def root():
    """Возвращает HTML страницу"""
    return FileResponse("views/index.html")

async def update_download_status(download_id: str, status: str, progress: Optional[float] = None, error: Optional[str] = None):
    """Обновить статус загрузки"""
    try:
        logging.info(f"[STATUS] Updating status for {download_id}: status={status}, progress={progress}, error={error}")
        
        # Формируем состояние
        state = {
            "status": status,
            "timestamp": time.time()
        }
        
        if progress is not None:
            state["progress"] = progress
            
        if error:
            state["error"] = error
            
        app.state.manager.update_download_state_sync(download_id, state)
        logging.info(f"[STATUS] Status updated for {download_id}: {state}")
            
    except Exception as e:
        logging.error(f"[STATUS] Error updating status: {str(e)}")
        raise

def remove_ansi(s):
    """Удаляет ANSI escape-последовательности из строки"""
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', s)

def my_progress_hook(d, download_id):
    """Функция-хук для обработки прогресса загрузки"""
    try:
        # Получаем все ключи из словаря прогресса
        keys_info = list(d.keys())
        
        # Логируем информацию о событии
        status = d.get('status', 'unknown')
        logging.info(f"[YT-DLP] Получено событие '{status}' для {download_id}")
        
        # Логируем ключи и их значения для отладки
        important_keys = ['_percent_str', 'downloaded_bytes', 'total_bytes', 'total_bytes_estimate', 
                         'fragment_index', 'fragment_count']
        debug_info = {k: d.get(k) for k in important_keys if k in d}
        logging.info(f"[YT-DLP] Важные ключи для {download_id}: {debug_info}")
        
        # Полное логирование всех ключей на уровне DEBUG
        logging.debug(f"[YT-DLP] Все ключи для {download_id}: {keys_info}")
        logging.debug(f"[YT-DLP] Сырые данные прогресса: {d}")
        
        # Явно добавляем download_id в словарь прогресса
        if 'download_id' not in d:
            d['download_id'] = download_id
        
        # Передаем данные в синхронный обработчик
        sync_progress_hook(d)
    except Exception as e:
        logging.error(f"[YT-DLP] Ошибка в progress_hook для {download_id}: {str(e)}")
        # Не пробрасываем ошибку дальше, чтобы не прерывать загрузку

def sync_progress_hook(d):
    """Синхронная функция для обновления прогресса"""
    try:
        download_id = d.get('download_id')
        if not download_id:
            logging.error("[SYNC_PROGRESS_HOOK] No download_id in progress data")
            return

        logging.info(f"[SYNC_PROGRESS_HOOK] Processing update for {download_id}")
        logging.debug(f"[SYNC_PROGRESS_HOOK] Raw data: {d}")
        
        # Получаем текущее состояние
        state = app.state.manager.get_download_state_sync(download_id)
        if not state:
            logging.warning(f"[SYNC_PROGRESS_HOOK] No state found for {download_id}")
            return
            
        # Вычисляем прогресс
        progress = None
        progress_source = None
        
        # Пробуем получить прогресс из процента
        if '_percent_str' in d:
            try:
                raw_str = d['_percent_str']
                clean_str = remove_ansi(raw_str)
                progress_str = clean_str.replace('%', '').strip()
                progress = float(progress_str)
                progress_source = '_percent_str'
                logging.info(f"[SYNC_PROGRESS_HOOK] Progress from _percent_str: {progress}% (raw: '{raw_str}', cleaned: '{clean_str}')")
            except (ValueError, AttributeError) as e:
                logging.warning(f"[SYNC_PROGRESS_HOOK] Error parsing _percent_str: {str(e)}")
        
        # Если не удалось получить из процента, пробуем из байтов
        if progress is None and 'downloaded_bytes' in d and ('total_bytes' in d or 'total_bytes_estimate' in d):
            try:
                downloaded = d['downloaded_bytes']
                total = d.get('total_bytes') or d.get('total_bytes_estimate')
                if total:
                    progress = (downloaded / total) * 100
                    progress_source = 'bytes'
                    logging.info(f"[SYNC_PROGRESS_HOOK] Progress from bytes: {progress}% ({downloaded}/{total} bytes)")
            except Exception as e:
                logging.warning(f"[SYNC_PROGRESS_HOOK] Error calculating progress from bytes: {str(e)}")
        
        # Обновляем состояние
        status = d.get('status', state.get('status', 'downloading'))
        
        # Формируем новое состояние
        new_state = {
            'status': status,
            'progress': progress if progress is not None else state.get('progress', 0),
            'timestamp': time.time(),
            'heartbeat': False,
            'log': f"Progress update: {progress}% (source: {progress_source})"
        }
        
        # Добавляем URL, если он есть в текущем состоянии
        if 'url' in state:
            new_state['url'] = state['url']
        
        # Если есть ошибка, добавляем её
        if 'error' in d:
            new_state['error'] = d['error']
            new_state['log'] = f"Error occurred: {d['error']}"
        
        # Обновляем состояние в менеджере
        app.state.manager.update_download_state_sync(download_id, new_state)
        logging.info(f"[SYNC_PROGRESS_HOOK] State updated for {download_id}: {new_state}")
        
    except Exception as e:
        logging.error(f"[SYNC_PROGRESS_HOOK] Error processing progress: {str(e)}")

async def delete_file_after_delay(file_path: str, delay: int):
    """Удаляет файл после заданной задержки"""
    try:
        await asyncio.sleep(delay)
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"[DELETE_FILE] Deleted file {file_path}")
    except Exception as e:
        logging.error(f"[DELETE_FILE] Error deleting file {file_path}: {str(e)}")

def is_valid_video_url(url: str) -> tuple[bool, str]:
    """Проверяет валидность URL видео для разных хостингов"""
    import re
    from urllib.parse import urlparse
    
    # YouTube паттерны
    youtube_patterns = [
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtu\.be\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})'
    ]
    
    # Vimeo паттерны
    vimeo_patterns = [
        r'(?:https?:\/\/)?(?:www\.)?vimeo\.com\/([0-9]+)',
        r'(?:https?:\/\/)?player\.vimeo\.com\/video\/([0-9]+)'
    ]
    
    # Dailymotion паттерны
    dailymotion_patterns = [
        r'(?:https?:\/\/)?(?:www\.)?dailymotion\.com\/video\/([a-zA-Z0-9]+)',
        r'(?:https?:\/\/)?dai\.ly\/([a-zA-Z0-9]+)'
    ]
    
    # TikTok паттерны
    tiktok_patterns = [
        r'(?:https?:\/\/)?(?:www\.)?tiktok\.com\/@[^\/]+\/video\/\d+',
        r'(?:https?:\/\/)?vm\.tiktok\.com\/[A-Za-z0-9]+'
    ]
    
    # Instagram паттерны
    instagram_patterns = [
        r'(?:https?:\/\/)?(?:www\.)?instagram\.com\/(?:p|reel|tv)\/[A-Za-z0-9_-]+'
    ]
    
    # Twitter/X паттерны
    twitter_patterns = [
        r'(?:https?:\/\/)?(?:www\.)?(?:twitter|x)\.com\/\w+\/status\/\d+'
    ]
    
    # Проверяем YouTube
    for pattern in youtube_patterns:
        match = re.match(pattern, url)
        if match:
            video_id = match.group(1)
            if len(video_id) == 11:
                return True, f"https://youtube.com/watch?v={video_id}"
    
    # Проверяем Vimeo
    for pattern in vimeo_patterns:
        if re.match(pattern, url):
            return True, url
    
    # Проверяем Dailymotion
    for pattern in dailymotion_patterns:
        if re.match(pattern, url):
            return True, url
    
    # Проверяем TikTok
    for pattern in tiktok_patterns:
        if re.match(pattern, url):
            return True, url
    
    # Проверяем Instagram
    for pattern in instagram_patterns:
        if re.match(pattern, url):
            return True, url
    
    # Проверяем Twitter/X
    for pattern in twitter_patterns:
        if re.match(pattern, url):
            return True, url
    
    # Проверяем прямые ссылки на видео
    try:
        parsed = urlparse(url)
        if parsed.scheme in ['http', 'https']:
            # Проверяем расширение файла
            path = parsed.path.lower()
            
            # Список поддерживаемых расширений
            extensions = ['.mp4', '.m3u8', '.m3u', '.mpd', '.f4m', '.webm', '.mkv', '.mov', '.ts']
            
            # Проверяем наличие расширения в пути или параметрах
            if any(ext in path for ext in extensions) or any(ext in parsed.query.lower() for ext in extensions):
                return True, url
            
            # Специальная проверка для m3u8 стримов
            if 'cloudfront.net' in parsed.netloc and 'master.m3u8' in path:
                return True, url
            
    except Exception as e:
        logging.warning(f"[URL_VALIDATION] Error parsing URL: {str(e)}")
    
    # Если URL не соответствует ни одному паттерну
    return False, """Неверный формат URL. Поддерживаемые форматы:
• YouTube: youtube.com/watch?v=ID, youtu.be/ID
• Vimeo: vimeo.com/ID
• Dailymotion: dailymotion.com/video/ID
• TikTok: tiktok.com/@user/video/ID
• Instagram: instagram.com/p/ID, instagram.com/reel/ID
• Twitter/X: twitter.com/user/status/ID
• Прямые ссылки: .mp4, .m3u8, .m3u, .mpd, .f4m, .webm, .mkv, .mov, .ts"""

@app.post("/api/download")
async def download(request: Request):
    """Эндпоинт для начала загрузки видео"""
    try:
        # Получаем данные запроса
        data = await request.json()
        url = data.get('url')
        if not url:
            return JSONResponse(
                status_code=400,
                content={"error": "URL обязателен"}
            )
        
        # Проверяем и нормализуем URL видео
        is_valid, result = is_valid_video_url(url)
        if not is_valid:
            return JSONResponse(
                status_code=400,
                content={"error": result}
            )
        url = result  # Используем нормализованный URL
        
        # Генерируем уникальный ID для загрузки
        download_id = str(uuid.uuid4())
        logging.info(f"[DOWNLOAD] Starting download {download_id} for URL: {url}")
        
        # Создаем начальное состояние
        initial_state = {
            "status": "initializing",
            "progress": 0,
            "url": url,
            "timestamp": time.time(),
            "log": "Download initialized"
        }
        await app.state.manager.update_download_state(download_id, initial_state)
        logging.info(f"[DOWNLOAD] Created initial state for {download_id}: {initial_state}")
        
        # Создаем очередь для обновлений
        queue = await app.state.manager.create_update_queue(download_id)
        
        # Запускаем загрузку в фоновом режиме
        background_tasks = BackgroundTasks()
        background_tasks.add_task(
            process_download,
            url=url,
            download_id=download_id,
            queue=queue
        )
        
        # Возвращаем ID загрузки
        return JSONResponse(
            status_code=202,
            content={
                "download_id": download_id,
                "message": "Загрузка начата",
                "initial_state": initial_state
            },
            background=background_tasks
        )
        
    except Exception as e:
        error_msg = f"Ошибка запуска загрузки: {str(e)}"
        logging.error(f"[DOWNLOAD] {error_msg}")
        return JSONResponse(
            status_code=500,
            content={"error": error_msg}
        )

async def process_download(url: str, download_id: str, queue: asyncio.Queue):
    """Обрабатывает загрузку видео"""
    try:
        logging.info(f"[DOWNLOAD_VIDEO] Starting download process for {download_id}")
        
        # Получаем путь к ffmpeg
        ffmpeg_location = os.path.join(os.path.dirname(__file__), 'bin', 'ffmpeg')
        
        # Создаем временную директорию для загрузки
        with tempfile.TemporaryDirectory() as temp_dir:
            # Настраиваем yt-dlp
            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'ffmpeg_location': ffmpeg_location,
                'progress_hooks': [lambda d: my_progress_hook(d, download_id)],
                'quiet': False,
                'no_warnings': False,
                'verbose': True,
                'extract_flat': False,
                'nocheckcertificate': True,
                'ignoreerrors': True,
                'no_color': True,
                'sleep_interval': 1,
                'max_sleep_interval': 5,
                'retries': 10,
                'fragment_retries': 10,
                'retry_sleep': 3,
                'hls_prefer_native': False,
                'hls_use_mpegts': True,
                'external_downloader': 'ffmpeg',
                'external_downloader_args': [
                    '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
                    '-allowed_extensions', 'ALL'
                ],
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'http_headers': {
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'DNT': '1'
                }
            }
            
            logger.info(f"[{download_id}] Начинаем загрузку видео: {url}")
            logger.info(f"[{download_id}] Используем временную директорию: {temp_dir}")
            
            # Логируем конфигурацию без функций
            ydl_opts_log = ydl_opts.copy()
            ydl_opts_log.pop('progress_hooks', None)  # Удаляем функции из копии для логирования
            logger.info(f"[{download_id}] Конфигурация yt-dlp: {json.dumps(ydl_opts_log, indent=2)}")

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    logger.info(f"[{download_id}] Извлекаем информацию о видео")
                    info = ydl.extract_info(url, download=False)
                    logger.info(f"[{download_id}] Информация о видео получена: {json.dumps(info, indent=2)}")
                    
                    logger.info(f"[{download_id}] Начинаем скачивание")
                    ydl.download([url])
                    logger.info(f"[{download_id}] Скачивание завершено")
                    
                    # Получаем имя скачанного файла
                    logger.info(f"[{download_id}] Ищем скачанный файл в {temp_dir}")
                    files = os.listdir(temp_dir)
                    logger.info(f"[{download_id}] Найдены файлы: {files}")
                    
                    if not files:
                        raise Exception("Не найден скачанный файл")
                        
                    downloaded_file = os.path.join(temp_dir, files[0])
                    logger.info(f"[{download_id}] Найден файл: {downloaded_file}")
                    
                    # Копируем файл в постоянное хранилище
                    filename = os.path.basename(downloaded_file)
                    target_path = os.path.join(DOWNLOADS_DIR, filename)
                    
                    logger.info(f"[{download_id}] Source file exists: {os.path.exists(downloaded_file)}")
                    logger.info(f"[{download_id}] Source file size: {os.path.getsize(downloaded_file)}")
                    logger.info(f"[{download_id}] Target directory exists: {os.path.exists(DOWNLOADS_DIR)}")
                    logger.info(f"[{download_id}] Target directory path: {DOWNLOADS_DIR}")
                    
                    # Создаем директорию если её нет
                    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
                    
                    shutil.copy2(downloaded_file, target_path)
                    logger.info(f"[{download_id}] File copied to {target_path}")
                    logger.info(f"[{download_id}] Target file exists: {os.path.exists(target_path)}")
                    logger.info(f"[{download_id}] Target file size: {os.path.getsize(target_path)}")
                    
                    # Обновляем состояние
                    await app.state.manager.update_download_state(download_id, {
                        "status": "completed",
                        "progress": 100,
                        "filename": filename,
                        "timestamp": time.time()
                    })
            except Exception as e:
                error_msg = f"Ошибка загрузки: {str(e)}"
                logging.error(f"[{download_id}] {error_msg}")
                await app.state.manager.update_download_state(download_id, {
                    "status": "error",
                    "error": error_msg,
                    "timestamp": time.time()
                })
                raise
                    
    except Exception as e:
        error_msg = f"Ошибка процесса: {str(e)}"
        logging.error(f"[{download_id}] {error_msg}")
        await app.state.manager.update_download_state(download_id, {
            "status": "error",
            "error": error_msg,
            "timestamp": time.time()
        })
        raise

@app.get("/api/progress_stream/{download_id}")
async def progress_stream(download_id: str):
    """Стрим прогресса загрузки"""
    logging.info(f"[PROGRESS_STREAM] Starting stream for {download_id}")
    
    try:
        async def event_generator():
            # Получаем или создаем очередь обновлений
            queue = app.state.manager.update_queues.get(download_id)
            if not queue:
                queue = await app.state.manager.create_update_queue(download_id)
            
            try:
                # Отправляем начальное состояние
                initial_state = await app.state.manager.get_download_state(download_id)
                if initial_state:
                    logging.info(f"[PROGRESS_STREAM] Sending initial state for {download_id}: {initial_state}")
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            **initial_state,
                            "log": "Initial state sent"
                        })
                    }
                
                # Ждем обновлений из очереди
                while True:
                    try:
                        # Ждем следующее обновление с таймаутом
                        state = await asyncio.wait_for(queue.get(), timeout=2.0)
                        
                        logging.info(f"[PROGRESS_STREAM] Received update from queue for {download_id}: {state}")
                        
                        # Отправляем состояние
                        state_with_log = {
                            **state,
                            "log": f"State update from queue, progress: {state.get('progress', 0)}%"
                        }
                        yield {
                            "event": "message",
                            "data": json.dumps(state_with_log)
                        }
                        
                        # Проверяем завершение
                        if state.get('status') in ['completed', 'error']:
                            logging.info(f"[PROGRESS_STREAM] Download {state.get('status')} for {download_id}")
                            break
                            
                    except asyncio.TimeoutError:
                        # Отправляем пинг при таймауте
                        logging.info(f"[PROGRESS_STREAM] Queue timeout, sending ping for {download_id}")
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "ping": time.time(),
                                "log": "Queue timeout ping"
                            })
                        }
                        
                    except Exception as e:
                        error_msg = f"Error processing queue update: {str(e)}"
                        logging.error(f"[PROGRESS_STREAM] {error_msg}")
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "status": "error",
                                "error": str(e),
                                "log": error_msg
                            })
                        }
                        break
                        
            finally:
                # Удаляем очередь при завершении
                app.state.manager.remove_update_queue(download_id)
                logging.info(f"[PROGRESS_STREAM] Removed update queue for {download_id}")
        
        return EventSourceResponse(event_generator())
        
    except Exception as e:
        error_msg = f"Error creating stream: {str(e)}"
        logging.error(f"[PROGRESS_STREAM] {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/api/download/{download_id}")
async def get_download(download_id: str):
    """Получить скачанный файл"""
    try:
        # Получаем состояние загрузки
        state = await app.state.manager.get_download_state(download_id)
        if not state:
            logging.error(f"[GET_DOWNLOAD] Download state not found for {download_id}")
            return JSONResponse(
                status_code=404,
                content={"error": "Download not found"},
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS"
                }
            )
            
        # Проверяем завершена ли загрузка
        if state.get("status") != "completed":
            logging.error(f"[GET_DOWNLOAD] Download not completed for {download_id}")
            return JSONResponse(
                status_code=400,
                content={"error": "Download not completed"},
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS"
                }
            )
            
        # Получаем путь к файлу
        file_path = os.path.join(DOWNLOADS_DIR, state.get("filename"))
        logging.info(f"[GET_DOWNLOAD] Checking file {file_path}")
        logging.info(f"[GET_DOWNLOAD] File exists: {os.path.exists(file_path)}")
        logging.info(f"[GET_DOWNLOAD] File size: {os.path.getsize(file_path) if os.path.exists(file_path) else 'N/A'}")
        logging.info(f"[GET_DOWNLOAD] Directory contents: {os.listdir(DOWNLOADS_DIR)}")
        
        if not file_path or not os.path.exists(file_path):
            logging.error(f"[GET_DOWNLOAD] File not found at {file_path}")
            return JSONResponse(
                status_code=404,
                content={"error": "File not found"},
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS"
                }
            )
            
        logging.info(f"[GET_DOWNLOAD] Sending file {file_path} for {download_id}")
        
        # Получаем имя файла из пути
        filename = os.path.basename(file_path)
        
        # Запускаем таймер на удаление файла через 24 часа
        asyncio.create_task(delete_file_after_delay(file_path, 86400))
        logging.info(f"[GET_DOWNLOAD] Scheduled deletion of {file_path} in 24 hours")
        
        # Отправляем файл с CORS заголовками
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
        
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type="video/mp4",
            headers=headers
        )
        
    except Exception as e:
        logging.error(f"[GET_DOWNLOAD] Error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS"
            }
        )

@app.post("/api/cancel/{download_id}")
async def cancel_download(download_id: str):
    """Отменить загрузку"""
    try:
        logging.info(f"[CANCEL] Cancelling download {download_id}")
        await app.state.manager.update_download_state(download_id, {
            "status": "cancelled",
            "timestamp": time.time()
        })
        return JSONResponse(content={"status": "cancelled"})
    except Exception as e:
        logging.error(f"[CANCEL] Error cancelling download: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

def clean_old_logs(log_path):
    """Очистить лог-файл"""
    try:
        # Очищаем файл логов
        with open(log_path, 'w', encoding='utf-8') as f:
            f.truncate(0)
        
        # Переинициализируем логирование
        init_logging()
        
        logging.info("[LOG] Лог очищен")
        return {"status": "success", "message": "Лог успешно очищен"}
    except Exception as e:
        error_msg = f"Ошибка при очистке лога: {str(e)}"
        logging.error(f"[CLEAR_LOG] {error_msg}")
        return {"status": "error", "message": error_msg}

async def periodic_log_cleanup():
    """Периодическая очистка старых логов"""
    while True:
        try:
            log_path = os.path.join(LOG_DIR, LOG_FILE)
            clean_old_logs(log_path)
            await asyncio.sleep(600)  # 10 минут
        except Exception as e:
            logging.error(f"Ошибка при очистке логов: {str(e)}")
            await asyncio.sleep(60)  # Подождем минуту перед следующей попыткой

@app.on_event("startup")
async def startup_event():
    """Действия при запуске приложения"""
    # Запускаем очистку логов
    asyncio.create_task(periodic_log_cleanup())

if __name__ == "__main__":
    import uvicorn
    
    print("Available routes:")
    for route in app.routes:
        print(f"Path: {route.path}, Methods: {route.methods if hasattr(route, 'methods') else 'N/A'}")
    
    # Настройки для продакшена:
    # reload=False - отключаем автоперезагрузку
    # workers=1 - один рабочий процесс (можно увеличить если нужно)
    # proxy_headers=True - для корректной работы за прокси
    # forwarded_allow_ips='*' - разрешаем форвардинг с любых IP
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,  # Используем тот же порт, что и в Dockerfile
        reload=False,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips='*',
        access_log=False  # отключаем доступ к логам uvicorn, так как у нас есть свое логирование
    )
