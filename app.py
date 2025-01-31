import os
import re
import time
import json
import uuid
import asyncio
import threading
from pathlib import Path
from typing import Dict, Any
from urllib.parse import urlparse
from collections import defaultdict
import logging
import subprocess

import yt_dlp
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, Mount
from pydantic import ValidationError
from models import DownloadRequest, LogErrorRequest
from fastapi import BackgroundTasks, Depends
import uuid
from multiprocessing import Process
from contextlib import asynccontextmanager
import threading

# Используем FastAPI для создания приложения
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация состояния при запуске"""
    # Инициализируем пустое состояние
    if not hasattr(app.state, "_state"):
        app.state._state = {"downloads": {}}
    
    # Пробуем загрузить сохраненное состояние
    try:
        saved_state = load_state()
        app.state._state.update(saved_state)
    except Exception as e:
        logging.error(f"[LIFESPAN] Error loading state: {str(e)}")
    
    # Создаем блокировку если её нет
    if not hasattr(app.state, "_state_lock"):
        app.state._state_lock = threading.Lock()
    
    yield
    
    # Сохраняем состояние при выключении
    try:
        save_state()
    except Exception as e:
        logging.error(f"[LIFESPAN] Error saving state: {str(e)}")

app = FastAPI(debug=True, lifespan=lifespan, middleware=[
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
])

def update_progress(d, download_id):
    """Обновляет прогресс для yt-dlp"""
    try:
        if d['status'] == 'downloading':
            logging.info(f"[PROGRESS] Download progress for {download_id}: {d}")
            
            # Вычисляем процент загрузки
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            if total_bytes > 0:
                downloaded_bytes = d.get('downloaded_bytes', 0)
                progress = (downloaded_bytes / total_bytes) * 100
                
                # Обновляем прогресс в состоянии
                with app.state._state_lock:
                    if download_id in app.state._state["downloads"]:
                        app.state._state["downloads"][download_id]["progress"] = round(progress, 1)
                        logging.info(f"[PROGRESS] Updated progress for {download_id}: {progress:.1f}%")
        
        elif d['status'] == 'finished':
            logging.info(f"[PROGRESS] Download finished for {download_id}")
            with app.state._state_lock:
                if download_id in app.state._state["downloads"]:
                    app.state._state["downloads"][download_id]["status"] = "processing"
                    app.state._state["downloads"][download_id]["progress"] = 100
    
    except Exception as e:
        logging.error(f"[PROGRESS] Error updating progress: {str(e)}")

def parse_ffmpeg_progress(line, download_id):
    """
    Парсим прогресс из вывода FFmpeg.
    Храним общую длительность в downloads[download_id]['duration'],
    а текущий прогресс пишем в downloads[download_id]['progress'].
    """
    # Если процесс почти закончил (после финала FFmpeg может вывести "muxing overhead")
    if 'muxing overhead' in line:
        return 100
    
    # Шаблон для поиска общей длительности в начале лога
    duration_match = re.search(r'Duration: (\d{2}:\d{2}:\d{2}\.\d{2})', line)
    if duration_match:
        h, m, s = duration_match.group(1).split(':')
        total_duration = float(h) * 3600 + float(m) * 60 + float(s)
        with app.state._state_lock:
            app.state._state["downloads"][download_id]['duration'] = total_duration

    # Шаблон для поиска «текущего времени» в логе
    time_match = re.search(r'time=(\d{2}:\d{2}:\d{2}\.\d{2})', line)
    if time_match:
        # Если уже сохранили общую длительность, то считаем процент
        if 'duration' in app.state._state["downloads"][download_id] and app.state._state["downloads"][download_id]['duration'] > 0:
            h, m, s = time_match.group(1).split(':')
            current_time = float(h) * 3600 + float(m) * 60 + float(s)
            total_duration = app.state._state["downloads"][download_id]['duration']
            progress = int((current_time / total_duration) * 100)
            return min(progress, 100)
    
    # Если не смогли извлечь процент — вернём None, чтобы не обновлять прогресс
    return None


def sanitize_filename(filename):
    """Очищает имя файла от недопустимых символов"""
    # Разделяем путь на части
    parts = filename.split('/')
    
    # Транслитерация кириллицы
    translit_table = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
    }
    
    # Обрабатываем каждую часть пути
    sanitized_parts = []
    for part in parts:
        # Переводим в нижний регистр и транслитерируем
        part = part.lower()
        for cyr, lat in translit_table.items():
            part = part.replace(cyr, lat)
        
        # Заменяем все недопустимые символы на пустую строку
        part = re.sub(r'[^a-zA-Z0-9_\-\.]', '', part)
        
        # Если часть пути пустая или это точки, пропускаем её
        if part and not all(c == '.' for c in part):
            sanitized_parts.append(part)
    
    # Если все части пустые, используем timestamp
    if not sanitized_parts:
        sanitized_parts = [str(int(time.time()))]
    
    # Проверяем расширение последней части
    name, ext = os.path.splitext(sanitized_parts[-1])
    if not ext:
        ext = '.mp4'
        sanitized_parts[-1] = name + ext
    
    # Соединяем все части в одну строку
    return ''.join(sanitized_parts)


def get_safe_ydl_opts(output_file, download_id, ffmpeg_location):
    """Возвращает безопасные опции для yt-dlp"""
    return {
        'format': 'best',  # Лучшее качество
        'outtmpl': output_file,
        'progress_hooks': [lambda d: update_progress(d, download_id)],
        'ffmpeg_location': ffmpeg_location,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'concurrent_fragment_downloads': 8,  # Параллельная загрузка
        'file_access_retries': 5,  # Повторные попытки при ошибках доступа
        'fragment_retries': 5,  # Повторные попытки для фрагментов
        'retry_sleep_functions': {'http': lambda n: 3},  # Задержка между попытками
        'http_headers': {  # Имитируем браузер
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
    }


def download_video(download_id: str, url: str, ffmpeg_location: str = ""):
    """Скачивает видео по URL"""
    try:
        logging.info(f"[DOWNLOAD_VIDEO] Starting download for ID: {download_id}, URL: {url}")
        
        # Создаем папку для загрузок если её нет
        os.makedirs("downloads", exist_ok=True)
        
        # Генерируем имя файла
        output_file = os.path.join("downloads", f"{download_id}")
        logging.info(f"[DOWNLOAD_VIDEO] Output file: {output_file}")
        
        # Получаем опции для yt-dlp
        ydl_opts = get_safe_ydl_opts(output_file, download_id, ffmpeg_location)
        logging.info(f"[DOWNLOAD_VIDEO] Using yt-dlp options: {ydl_opts}")
        
        try:
            # Обновляем статус
            with app.state._state_lock:
                app.state._state["downloads"][download_id]["status"] = "downloading"
                app.state._state["downloads"][download_id]["progress"] = 0
                save_state()  # Сохраняем состояние
            
            logging.info(f"[DOWNLOAD_VIDEO] Starting yt-dlp download for ID: {download_id}")
            # Скачиваем видео
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            logging.info(f"[DOWNLOAD_VIDEO] Download completed for ID: {download_id}")
            # Обновляем статус
            with app.state._state_lock:
                app.state._state["downloads"][download_id]["status"] = "completed"
                app.state._state["downloads"][download_id]["progress"] = 100
                save_state()  # Сохраняем состояние
            
            # Запускаем таймер на удаление файла через 24 часа
            threading.Thread(
                target=delete_file_after_delay,
                args=(output_file, 86400),  # 86400 секунд = 24 часа
                daemon=True
            ).start()
            
        except Exception as e:
            logging.error(f"[DOWNLOAD_VIDEO] Error during download: {str(e)}")
            # Обновляем статус при ошибке
            with app.state._state_lock:
                app.state._state["downloads"][download_id]["status"] = "error"
                app.state._state["downloads"][download_id]["error"] = str(e)
                save_state()  # Сохраняем состояние
            raise
            
    except Exception as e:
        logging.error(f"[DOWNLOAD_VIDEO] Unexpected error: {str(e)}")
        with app.state._state_lock:
            app.state._state["downloads"][download_id]["status"] = "error"
            app.state._state["downloads"][download_id]["error"] = str(e)
            save_state()  # Сохраняем состояние


def is_valid_url(url):
    """Проверяет корректность URL"""
    try:
        result = urlparse(url)
        if not all([result.scheme, result.netloc]):
            return False
        
        # Проверяем поддерживаемые схемы
        if result.scheme not in ['http', 'https']:
            return False
        
        # Проверяем M3U8 URL
        if url.lower().endswith('.m3u8'):
            return True
        
        # Проверяем поддерживаемые домены
        supported_domains = [
            'youtube.com', 'youtu.be',
            'vimeo.com',
            'cloudfront.net',
            'example.com'  # Добавляем для тестов
        ]
        
        domain = result.netloc.lower()
        if any(d in domain for d in supported_domains):
            return True
            
        # Проверяем, является ли URL прямой ссылкой на видео
        video_extensions = ['.mp4', '.webm', '.mkv', '.avi']
        if any(url.lower().endswith(ext) for ext in video_extensions):
            return True
            
        return False
    except:
        return False


@app.post("/download")
async def download(request: DownloadRequest):
    """
    Эндпоинт для скачивания видео по URL
    """
    try:
        # Создаем директорию для загрузок
        os.makedirs("downloads", exist_ok=True)
        
        url = str(request.url)
        logging.info(f"[DOWNLOAD] Processing URL: {url}")
        
        # Проверяем URL
        if not is_valid_url(url):
            logging.error(f"[DOWNLOAD] Invalid URL format: {url}")
            return JSONResponse({"error": "Invalid URL format"}, status_code=422)
        
        # Генерируем уникальный ID
        download_id = str(uuid.uuid4())
        
        # Инициализируем состояние загрузки
        with app.state._state_lock:
            app.state._state["downloads"][download_id] = {"status": "pending", "progress": 0}
        
        # Запускаем скачивание в фоновом режиме
        thread = threading.Thread(
            target=download_video, 
            args=(download_id, url, request.ffmpeg_location)
        )
        thread.daemon = True
        thread.start()
        
        logging.info(f"[DOWNLOAD] Started download with ID: {download_id}")
        return JSONResponse({"download_id": download_id}, status_code=200)
    except Exception as e:
        logging.error(f"[DOWNLOAD] Unexpected error: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


async def progress_stream(request):
    """SSE поток для получения прогресса в реальном времени"""
    download_id = request.path_params['download_id']
    if download_id not in app.state._state["downloads"]:
        return JSONResponse({'error': 'Invalid download ID'}, status_code=404)
        
    async def generate():
        while True:
            if app.state._state["downloads"][download_id]['status'] in ['completed', 'error']:
                break
                
            progress = app.state._state["downloads"][download_id].get('progress', 0)
            event = f"id: {download_id}\n"
            event += f"data: {progress}\n\n"
            yield event
            time.sleep(0.1)  # Увеличиваем частоту обновлений
            
    return StreamingResponse(generate(), media_type='text/event-stream')

async def get_progress(request):
    """Возвращает текущий статус и прогресс (для совместимости)"""
    download_id = request.path_params['download_id']
    if download_id not in app.state._state["downloads"]:
        return JSONResponse({'error': 'Invalid download ID'}, status_code=404)
        
    return JSONResponse({
        'status': app.state._state["downloads"][download_id]['status'],
        'progress': app.state._state["downloads"][download_id].get('progress', 0),
        'error': app.state._state["downloads"][download_id].get('error', '')
    })

@app.get("/sync_progress")
async def sync_progress(request: Request):
    """Возвращает текущий прогресс загрузки"""
    download_id = request.query_params.get("download_id")
    if not download_id:
        return JSONResponse({"error": "Missing download_id"}, status_code=400)
    
    if download_id not in app.state._state["downloads"]:
        return JSONResponse({"error": "Download ID not found"}, status_code=404)
    
    download_info = app.state._state["downloads"][download_id]
    return JSONResponse({
        "status": download_info.get("status", "unknown"),
        "progress": download_info.get("progress", 0),
        "size": download_info.get("size", 0),
        "error": download_info.get("error", "")
    }, status_code=200)

async def show_error(error_message: str, status_code: int = 404):
    """Показывает страницу с ошибкой"""
    try:
        logging.info(f"[SHOW_ERROR] Attempting to show error page with message: {error_message}")
        return JSONResponse({'error': error_message}, status_code=status_code)
    except Exception as e:
        logging.error(f"[SHOW_ERROR] Error showing error page: {str(e)}")
        return JSONResponse({'error': error_message}, status_code=status_code)

@app.get("/download/{download_id}")
async def download_file(request: Request):
    """Отдает скачанный файл"""
    try:
        download_id = request.path_params['download_id']
        logging.info(f"[DOWNLOAD_FILE] Starting download for ID: {download_id}")
        
        # Сначала проверяем статус загрузки
        if download_id not in app.state._state.get("downloads", {}):
            error_msg = f"[DOWNLOAD_FILE] Download ID not found: {download_id}"
            logging.error(error_msg)
            # Проверяем, существует ли файл, несмотря на отсутствие статуса
            file_path = os.path.join("downloads", f"{download_id}")
            if os.path.exists(file_path):
                logging.warning(f"[DOWNLOAD_FILE] File exists but no status found. Attempting recovery...")
                # Восстанавливаем статус
                with app.state._state_lock:
                    app.state._state["downloads"][download_id] = {
                        "status": "completed",
                        "progress": 100
                    }
                    save_state()
            else:
                return RedirectResponse(url="/", status_code=302)
        
        download_info = app.state._state["downloads"][download_id]
        if download_info.get('status') != 'completed':
            error_msg = f"[DOWNLOAD_FILE] Download not completed for ID {download_id}. Current status: {download_info.get('status', 'unknown')}"
            logging.error(error_msg)
            return RedirectResponse(url="/", status_code=302)
        
        # Теперь проверяем наличие файла
        file_path = os.path.join("downloads", f"{download_id}")
        if not os.path.exists(file_path):
            error_msg = f"[DOWNLOAD_FILE] File not found: {file_path}"
            logging.error(error_msg)
            # Очищаем некорректный статус
            with app.state._state_lock:
                if download_id in app.state._state["downloads"]:
                    app.state._state["downloads"][download_id]["status"] = "error"
                    app.state._state["downloads"][download_id]["error"] = "File not found"
                    save_state()
            return RedirectResponse(url="/", status_code=302)
        
        # Отправляем файл
        logging.info(f"[DOWNLOAD_FILE] Sending file: {file_path}")
        return FileResponse(
            file_path,
            media_type='video/mp4',
            filename=f'video_{download_id}.mp4'
        )
        
    except Exception as e:
        error_msg = f"[DOWNLOAD_FILE] Error: {str(e)}"
        logging.error(error_msg)
        return RedirectResponse(url="/", status_code=302)

async def log_error(request):
    """Логирует ошибки с клиента"""
    try:
        data = await request.json()
        LogErrorRequest(**data)
    except ValidationError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    
    error_msg = data.get('error')
    download_id = data.get('downloadId')
    
    print(f"[CLIENT ERROR] Download ID: {download_id}, Error: {error_msg}")
    return JSONResponse({'status': 'logged'})

async def cancel_download(request):
    """Отменяет загрузку и удаляет временный файл"""
    download_id = request.path_params['download_id']
    if download_id not in app.state._state["downloads"]:
        return JSONResponse({'error': 'Invalid download ID'}, status_code=404)
        
    if app.state._state["downloads"][download_id]['status'] == 'processing':
        process = app.state._state["downloads"][download_id]['process']
        process.terminate()
        
    if os.path.exists(f"downloads/{download_id}.mp4"):
        os.remove(f"downloads/{download_id}.mp4")
        
    del app.state._state["downloads"][download_id]
    
    return JSONResponse({'status': 'cancelled'})


@app.get("/")
async def homepage(request: Request):
    """Отображает главную страницу"""
    return FileResponse("views/index.html", media_type='text/html')

# Определяем маршруты после всех функций
routes = [
    Mount('/static', StaticFiles(directory='static'), name='static'),
    Mount('/views', StaticFiles(directory='views'), name='views'),
    Route('/', homepage),
    Route('/download', download, methods=['POST']),
    Route('/progress/{download_id}', progress_stream),
    Route('/sync_progress', sync_progress),
    Route('/download/{download_id}', download_file),
    Route('/log_error', log_error, methods=['POST']),
    Route('/cancel/{download_id}', cancel_download, methods=['POST', 'GET'])
]

app.router.routes.extend(routes)

def save_state():
    """Сохраняет состояние в файл"""
    try:
        # Создаем копию состояния без объекта блокировки
        downloads = {}
        if hasattr(app.state, "_state") and isinstance(app.state._state, dict):
            downloads = app.state._state.get("downloads", {})
        
        state_to_save = {
            "downloads": downloads,
            "timestamp": time.time()
        }
        
        # Создаем временный файл
        temp_file = "downloads_state.json.tmp"
        
        # Сначала пишем во временный файл
        with open(temp_file, "w") as f:
            json.dump(state_to_save, f, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Убеждаемся, что данные записаны на диск
            
        # Проверяем, что данные корректно записались
        try:
            with open(temp_file, "r") as f:
                json.load(f)
        except:
            raise ValueError("Failed to verify saved state")
            
        # Переименовываем временный файл в основной
        if os.path.exists("downloads_state.json"):
            os.rename("downloads_state.json", "downloads_state.json.bak")
        os.rename(temp_file, "downloads_state.json")
        
        # Удаляем бэкап если всё прошло успешно
        if os.path.exists("downloads_state.json.bak"):
            os.remove("downloads_state.json.bak")
            
    except Exception as e:
        logging.error(f"[SAVE_STATE] Error saving state: {str(e)}")
        # Восстанавливаем из бэкапа если он есть
        try:
            if os.path.exists("downloads_state.json.bak"):
                os.rename("downloads_state.json.bak", "downloads_state.json")
        except:
            pass
        # Удаляем временный файл если он остался
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except:
            pass

def load_state():
    """Загружает состояние из файла"""
    try:
        state = {"downloads": {}}
        
        # Пробуем загрузить основной файл
        if os.path.exists("downloads_state.json"):
            try:
                with open("downloads_state.json", "r") as f:
                    content = f.read()
                    if content.strip():
                        loaded_state = json.loads(content)
                        if isinstance(loaded_state, dict):
                            if "downloads" in loaded_state and isinstance(loaded_state["downloads"], dict):
                                state = loaded_state
                                logging.info("[LOAD_STATE] Successfully loaded state from main file")
            except Exception as e:
                logging.error(f"[LOAD_STATE] Error loading main state file: {str(e)}")
        
        # Если основной файл поврежден, пробуем загрузить бэкап
        if not state["downloads"] and os.path.exists("downloads_state.json.bak"):
            try:
                with open("downloads_state.json.bak", "r") as f:
                    content = f.read()
                    if content.strip():
                        loaded_state = json.loads(content)
                        if isinstance(loaded_state, dict):
                            if "downloads" in loaded_state and isinstance(loaded_state["downloads"], dict):
                                state = loaded_state
                                logging.info("[LOAD_STATE] Successfully loaded state from backup file")
                                # Восстанавливаем основной файл из бэкапа
                                os.rename("downloads_state.json.bak", "downloads_state.json")
            except Exception as e:
                logging.error(f"[LOAD_STATE] Error loading backup state file: {str(e)}")
        
        # Проверяем состояние загрузок
        downloads = state.get("downloads", {})
        for download_id, info in list(downloads.items()):
            file_path = os.path.join("downloads", f"{download_id}")
            # Если файл не существует, но статус completed - меняем статус
            if info.get("status") == "completed" and not os.path.exists(file_path):
                downloads[download_id]["status"] = "error"
                downloads[download_id]["error"] = "File not found"
                logging.warning(f"[LOAD_STATE] File not found for completed download {download_id}")
            # Если загрузка была в процессе - помечаем как прерванную
            elif info.get("status") in ["downloading", "processing", "pending"]:
                downloads[download_id]["status"] = "interrupted"
                downloads[download_id]["error"] = "Download interrupted by server restart"
                logging.warning(f"[LOAD_STATE] Marking interrupted download {download_id}")
        
        return state
        
    except Exception as e:
        logging.error(f"[LOAD_STATE] Error loading state: {str(e)}")
        return {"downloads": {}}

if __name__ == '__main__':
    # Настройка логирования
    import logging
    
    # Уменьшаем уровень логирования для uvicorn и fastapi
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.WARNING)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('app.log'),
            logging.StreamHandler()
        ]
    )
    
    # Создаём папку для загрузок, если её нет
    os.makedirs('downloads', exist_ok=True)
    
    # Создаем файл лога, если его нет
    if not os.path.exists('app.log'):
        open('app.log', 'a').close()
        
    def clear_logs():
        """Очищает файл логов каждые 24 часа"""
        try:
            with open('app.log', 'w') as f:
                f.truncate(0)
            logging.info("[LOGS] Logs cleared successfully")
        except Exception as e:
            logging.error(f"[LOGS] Error clearing logs: {str(e)}")
        finally:
            # Запускаем таймер снова через 24 часа
            threading.Timer(86400, clear_logs).start()
    
    # Запускаем очистку логов
    clear_logs()
    
    # Устанавливаем права доступа для статических файлов
    os.chmod('static', 0o755)
    os.chmod('static/style.css', 0o644)
    os.chmod('views', 0o755)
    os.chmod('views/index.html', 0o644)
    
    # Определяем хост для разных окружений
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 8080))
    
    logging.info(f"Starting server on http://{host}:{port}")
    print(f"Server is running on http://{host}:{port}")
    
    # Запуск приложения
    import uvicorn
    uvicorn.run("app:app", host=host, port=port, reload=False, log_level="info")

def delete_file_after_delay(file_path, delay):
    """Удаляет файл через указанное время"""
    def delete():
        time.sleep(delay)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logging.info(f"[DELETE] Successfully deleted file: {file_path}")
            else:
                logging.info(f"[DELETE] File already deleted: {file_path}")
        except Exception as e:
            logging.error(f"[DELETE ERROR] Failed to delete {file_path}: {str(e)}")
    
    threading.Thread(target=delete, daemon=True).start()
