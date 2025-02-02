import os
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime
from typing import Dict, Any, Optional

import yt_dlp
from fastapi import FastAPI

# Глобальные переменные для состояния
_app = None
_save_state = None

class State:
    """Класс для хранения состояния"""
    def __init__(self, app, save_callback=None):
        self.app = app
        self.save_callback = save_callback
        
    def get_download_state(self, download_id):
        """Получить состояние загрузки"""
        try:
            with self.app.state._state_lock:
                return self.app.state._state["downloads"].get(download_id)
        except Exception as e:
            logging.error(f"[STATE] Error: {str(e)}")
            return None
            
    def update_download_state(self, download_id, **kwargs):
        """Обновить состояние загрузки"""
        try:
            with self.app.state._state_lock:
                if download_id in self.app.state._state["downloads"]:
                    self.app.state._state["downloads"][download_id].update(kwargs)
                    if self.save_callback:
                        self.save_callback()
                    return True
                return False
        except Exception as e:
            logging.error(f"[STATE] Error: {str(e)}")
            return False

def init_app(app, save_callback=None):
    """Инициализация приложения"""
    global _app
    _app = app
    app.state.manager = State(app, save_callback)

def sync_update_download_status(download_id, status, progress=None, error=None):
    """Синхронно обновить статус загрузки"""
    try:
        if not _app or not hasattr(_app.state, "manager"):
            logging.error("[STATUS] App or state manager not initialized")
            return
            
        state = {
            "status": status,
            "last_update": datetime.now().isoformat()
        }
        if progress is not None:
            state["progress"] = progress
        if error is not None:
            state["error"] = error
            
        _app.state.manager.update_download_state(download_id, **state)
            
    except Exception as e:
        logging.error(f"[STATUS] Error updating status: {str(e)}")

async def update_download_status(download_id, status, progress=None, error=None):
    """Асинхронно обновить статус загрузки"""
    try:
        await asyncio.to_thread(
            sync_update_download_status,
            download_id,
            status,
            progress,
            error
        )
    except Exception as e:
        logging.error(f"[STATUS] Error updating status: {str(e)}")

def parse_ffmpeg_progress(progress_line: str) -> float:
    """Парсит строку прогресса ffmpeg и возвращает процент"""
    if not progress_line:
        return 0.0
    
    try:
        # Ищем время в строке
        match = re.search(r'time=(\d{2}):(\d{2}):(\d{2}.\d{2})', progress_line)
        if not match:
            return 0.0
        
        # Конвертируем время в секунды
        hours, minutes, seconds = map(float, match.groups())
        total_seconds = hours * 3600 + minutes * 60 + seconds
        
        # Возвращаем прогресс в секундах
        return total_seconds
    except Exception as e:
        logging.error(f"[FFMPEG] Error parsing progress: {str(e)}")
        return 0.0

def sanitize_filename(filename: str) -> str:
    """Очищает имя файла от недопустимых символов"""
    # Заменяем недопустимые символы на _
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    # Убираем пробелы в начале и конце
    filename = filename.strip()
    
    # Ограничиваем длину имени файла
    max_length = 255
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length-len(ext)] + ext
        
    return filename

async def delete_file_after_delay(file_path: str, delay: int = 86400):
    """Удаляет файл после указанной задержки"""
    try:
        await asyncio.sleep(delay)
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"[DELETE] Deleted file: {file_path}")
    except Exception as e:
        logging.error(f"[DELETE] Error deleting file {file_path}: {str(e)}")

async def clear_logs_task(delay=3600, logs_dir=None):
    """Очистка старых логов"""
    while True:
        try:
            await asyncio.sleep(delay)
            if logs_dir is None:
                logs_dir = os.path.join(os.path.dirname(__file__), "logs")
            
            if os.path.exists(logs_dir):
                for file in os.listdir(logs_dir):
                    if file.endswith(".log"):
                        file_path = os.path.join(logs_dir, file)
                        if os.path.getmtime(file_path) < time.time() - 86400:  # 24 часа
                            os.remove(file_path)
                            logging.info(f"[CLEAR_LOGS] Deleted log: {file}")
        except Exception as e:
            logging.error(f"[CLEAR_LOGS] Error: {str(e)}")

def get_cookies_path():
    """Получает путь к файлу cookies"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube.com_cookies.txt")

def get_safe_ydl_opts(output_file, download_id, ffmpeg_location=None):
    """Получить безопасные опции для yt-dlp"""
    def progress_hook(d):
        try:
            status = d.get('status', '')
            logging.info(f"[PROGRESS] Hook called with status: {status}")
            
            if status == "downloading":
                try:
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    speed = d.get("speed", 0)
                    fragment_index = d.get("fragment_index", 0)
                    fragment_count = d.get("fragment_count", 0)
                    
                    logging.info(f"[PROGRESS] Downloaded: {downloaded}, Total: {total}, Speed: {speed}, Fragment: {fragment_index}/{fragment_count}")
                    
                    # Сохраняем промежуточный прогресс
                    current_state = _app.state.manager.get_download_state(download_id)
                    last_progress = current_state.get("progress", 0) if current_state else 0
                    
                    # Для HLS используем фрагменты для расчета прогресса
                    if fragment_count > 0:
                        progress = round((fragment_index / fragment_count) * 100, 1)
                    elif total > 0:
                        progress = round((downloaded / total) * 100, 1)
                    else:
                        # Если нет ни total, ни фрагментов, используем downloaded
                        progress = round((downloaded / (100 * 1024 * 1024)) * 100, 1)
                        if progress > 100:
                            progress = 99  # Не показываем 100% пока не закончили
                    
                    # Проверяем, не откатился ли прогресс назад (признак переподключения)
                    if progress < last_progress:
                        progress = last_progress
                            
                    logging.info(f"[PROGRESS] Calculated progress: {progress}%")
                    sync_update_download_status(
                        download_id,
                        status="downloading",
                        progress=progress
                    )
                    
                except Exception as e:
                    logging.error(f"[PROGRESS] Error calculating progress: {str(e)}")
                    logging.exception(e)
                    
            elif status == "finished":
                logging.info("[PROGRESS] Download finished")
                sync_update_download_status(
                    download_id,
                    status="processing",
                    progress=100
                )
            elif status == "error":
                error_msg = d.get("error", "Unknown error")
                logging.error(f"[PROGRESS] Download error: {error_msg}")
                sync_update_download_status(
                    download_id,
                    status="error",
                    error=str(error_msg)
                )
            elif status == "reconnecting":
                logging.info("[PROGRESS] Reconnecting...")
                # Сохраняем текущий прогресс при переподключении
                current_state = _app.state.manager.get_download_state(download_id)
                if current_state:
                    sync_update_download_status(
                        download_id,
                        status="reconnecting",
                        progress=current_state.get("progress", 0)
                    )
                
        except Exception as e:
            logging.error(f"[PROGRESS] Hook error: {str(e)}")
            logging.exception(e)

    # Настройки для yt-dlp
    opts = {
        'format': 'best',
        'outtmpl': output_file,
        'progress_hooks': [progress_hook],
        'retries': 10,  # Увеличиваем количество попыток
        'fragment_retries': 10,  # Увеличиваем количество попыток для фрагментов
        'retry_sleep_functions': {'http': lambda n: 5},  # Пауза 5 секунд между попытками
        'socket_timeout': 30,  # Таймаут сокета
        'extractor_retries': 5,  # Количество попыток для экстрактора
        'file_access_retries': 5,  # Количество попыток доступа к файлу
        'http_chunk_size': 10485760,  # Размер чанка (10MB)
        'continuedl': True,  # Продолжать загрузку
        'noprogress': False,
        'quiet': False,
        'verbose': True,
        'no_warnings': False,
    }
    
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
        
    return opts

def get_ffmpeg_path():
    """
    Получает путь к ffmpeg, сначала ищет в локальной директории bin,
    затем в системных путях
    """
    local_ffmpeg = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bin', 'ffmpeg')
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    return 'ffmpeg'  # вернуть системный путь как fallback

def get_yt_dlp_opts():
    """
    Возвращает опции для yt-dlp с настроенным путем к ffmpeg
    """
    return {
        'format': 'bestvideo+bestaudio/best',
        'ffmpeg_location': get_ffmpeg_path(),
        'merge_output_format': 'mp4'
    }

async def download_video(url, download_id, ffmpeg_location=""):
    """Скачивает видео по URL"""
    try:
        logging.info(f"[DOWNLOAD] Starting download for URL: {url}")
        
        # Создаем директорию для загрузки
        download_dir = os.path.join(
            os.path.dirname(__file__),
            "downloads",
            download_id
        )
        os.makedirs(download_dir, exist_ok=True)
        
        # Путь для сохранения файла
        output_file = os.path.join(download_dir, "%(title)s.%(ext)s")
        
        ydl_opts = get_yt_dlp_opts()
        ydl_opts.update({
            'outtmpl': output_file,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [lambda d: handle_progress(d, download_id)] if download_id else []
        })
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            except yt_dlp.utils.DownloadError as e:
                error = str(e)
                logging.error(f"[DOWNLOAD] {error}")
                await update_download_status(download_id, "error", error=error)
                return False
            except Exception as e:
                error = str(e)
                logging.error(f"[DOWNLOAD] Unexpected error during info extraction: {error}")
                await update_download_status(download_id, "error", error=error)
                return False
                
            if not info:
                error = "Не удалось получить информацию о видео"
                logging.error(f"[DOWNLOAD] {error}")
                await update_download_status(download_id, "error", error=error)
                return False
                
            logging.info(f"[DOWNLOAD] Video info extracted successfully: {info.get('title', 'Unknown title')}")
            
            # Проверяем размер файла
            filesize = info.get('filesize') or info.get('filesize_approx')
            if filesize and filesize > 2 * 1024 * 1024 * 1024:  # 2GB
                error = "Файл слишком большой (более 2GB)"
                logging.error(f"[DOWNLOAD] {error}")
                await update_download_status(download_id, "error", error=error)
                return False
            
            # Скачиваем видео
            logging.info("[DOWNLOAD] Starting video download...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    await asyncio.to_thread(ydl.download, [url])
                except yt_dlp.utils.DownloadError as e:
                    error = str(e)
                    logging.error(f"[DOWNLOAD] {error}")
                    await update_download_status(download_id, "error", error=error)
                    return False
                except Exception as e:
                    error = str(e)
                    logging.error(f"[DOWNLOAD] Unexpected error during download: {error}")
                    await update_download_status(download_id, "error", error=error)
                    return False
                
            # Проверяем, что файл существует и не пустой
            files = [f for f in os.listdir(download_dir) if os.path.isfile(os.path.join(download_dir, f))]
            if not files:
                error = "Файл не был создан после загрузки"
                logging.error(f"[DOWNLOAD] {error}")
                await update_download_status(download_id, "error", error=error)
                return False
                
            # Проверяем размер файла
            file_path = os.path.join(download_dir, files[0])
            if os.path.getsize(file_path) == 0:
                error = "Загруженный файл пустой"
                logging.error(f"[DOWNLOAD] {error}")
                os.remove(file_path)
                await update_download_status(download_id, "error", error=error)
                return False
                
            logging.info("[DOWNLOAD] Download completed successfully")
            return True
            
    except Exception as e:
        error = f"Критическая ошибка: {str(e)}"
        logging.error(f"[DOWNLOAD] {error}")
        logging.exception(e)
        await update_download_status(download_id, "error", error=error)
        return False

async def download_m3u8(url: str, output_path: str, download_id: str = None) -> bool:
    """
    Скачивает видео в формате m3u8 с помощью ffmpeg
    """
    try:
        ffmpeg_path = get_ffmpeg_path()
        cmd = [
            ffmpeg_path,
            '-i', url,
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # ждем завершения процесса
        await process.wait()
        
        # проверяем код завершения
        if process.returncode != 0:
            error = f"Ошибка скачивания: {process.returncode}"
            logging.error(f"[DOWNLOAD] {error}")
            await update_download_status(download_id, "error", error=error)
            return False
        
        logging.info("[DOWNLOAD] Download completed successfully")
        return True
        
    except Exception as e:
        error = f"Критическая ошибка: {str(e)}"
        logging.error(f"[DOWNLOAD] {error}")
        logging.exception(e)
        await update_download_status(download_id, "error", error=error)
        return False

def clean_old_logs(log_path: str, max_size_mb: int = 10) -> None:
    """
    Очищает старые логи если файл превышает максимальный размер
    
    Args:
        log_path: Путь к файлу логов
        max_size_mb: Максимальный размер файла в мегабайтах
    """
    try:
        if not os.path.exists(log_path):
            return
            
        # Проверяем размер файла
        size_mb = os.path.getsize(log_path) / (1024 * 1024)
        if size_mb <= max_size_mb:
            return
            
        # Читаем последние строки
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Оставляем только последние 1000 строк
        lines = lines[-1000:]
        
        # Перезаписываем файл
        with open(log_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            
    except Exception as e:
        print(f"Ошибка при очистке логов: {str(e)}")

def handle_progress(d, download_id):
    try:
        status = d.get('status', '')
        logging.info(f"[PROGRESS] Hook called with status: {status}")
        
        if status == "downloading":
            try:
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                speed = d.get("speed", 0)
                fragment_index = d.get("fragment_index", 0)
                fragment_count = d.get("fragment_count", 0)
                
                logging.info(f"[PROGRESS] Downloaded: {downloaded}, Total: {total}, Speed: {speed}, Fragment: {fragment_index}/{fragment_count}")
                
                # Сохраняем промежуточный прогресс
                current_state = _app.state.manager.get_download_state(download_id)
                last_progress = current_state.get("progress", 0) if current_state else 0
                
                # Для HLS используем фрагменты для расчета прогресса
                if fragment_count > 0:
                    progress = round((fragment_index / fragment_count) * 100, 1)
                elif total > 0:
                    progress = round((downloaded / total) * 100, 1)
                else:
                    # Если нет ни total, ни фрагментов, используем downloaded
                    progress = round((downloaded / (100 * 1024 * 1024)) * 100, 1)
                    if progress > 100:
                        progress = 99  # Не показываем 100% пока не закончили
                
                # Проверяем, не откатился ли прогресс назад (признак переподключения)
                if progress < last_progress:
                    progress = last_progress
                        
                logging.info(f"[PROGRESS] Calculated progress: {progress}%")
                sync_update_download_status(
                    download_id,
                    status="downloading",
                    progress=progress
                )
                
            except Exception as e:
                logging.error(f"[PROGRESS] Error calculating progress: {str(e)}")
                logging.exception(e)
                
        elif status == "finished":
            logging.info("[PROGRESS] Download finished")
            sync_update_download_status(
                download_id,
                status="processing",
                progress=100
            )
        elif status == "error":
            error_msg = d.get("error", "Unknown error")
            logging.error(f"[PROGRESS] Download error: {error_msg}")
            sync_update_download_status(
                download_id,
                status="error",
                error=str(error_msg)
            )
        elif status == "reconnecting":
            logging.info("[PROGRESS] Reconnecting...")
            # Сохраняем текущий прогресс при переподключении
            current_state = _app.state.manager.get_download_state(download_id)
            if current_state:
                sync_update_download_status(
                    download_id,
                    status="reconnecting",
                    progress=current_state.get("progress", 0)
                )
            
    except Exception as e:
        logging.error(f"[PROGRESS] Hook error: {str(e)}")
        logging.exception(e)
