import os
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple, Union, Callable
import yt_dlp
from fastapi import FastAPI
from models import DownloadStatus
import aiofiles
from state_storage import StateStorage
import aiohttp
from urllib.parse import urlparse

# Глобальные переменные для состояния
_app: Optional[FastAPI] = None

async def init_app(app: FastAPI) -> None:
    """Инициализация приложения"""
    global _app
    _app = app

async def update_download_status(
    download_id: str,
    status: str,
    progress: Optional[float] = None,
    error: Optional[str] = None
) -> None:
    """
    Асинхронное обновление статуса загрузки

    Args:
        download_id: ID загрузки
        status: Новый статус
        progress: Прогресс загрузки (0-100)
        error: Сообщение об ошибке
    """
    try:
        from app import app

        # Добавляем логирование для отладки
        logging.info(f"[UPDATE] Updating status for {download_id}: status={status}, progress={progress}")

        # Обновляем состояние
        state = await app.state.storage.get_item(download_id)
        if state:
            old_progress = state.get('progress', 0)
            state['status'] = status
            if progress is not None:
                state['progress'] = progress
                # Логируем изменение прогресса
                if old_progress != progress:
                    logging.info(f"[UPDATE] Progress changed for {download_id}: {old_progress} -> {progress}")
            if error:
                state['error'] = error
            state['updated_at'] = time.time()

            # Сохраняем обновленное состояние
            await app.state.storage.update_item(download_id, state)
            logging.info(f"[UPDATE] State updated for {download_id}: {state}")
        else:
            logging.warning(f"[UPDATE] State not found for {download_id}")

    except Exception as e:
        logging.error(f"[UPDATE] Error updating status for {download_id}: {str(e)}")

def update_download_state_sync(download_id: str, state: Dict[str, Any]):
    """Синхронно обновить состояние загрузки"""
    try:
        if not _app or not hasattr(_app.state, 'storage'):
            logging.warning("[STATE] App or storage not initialized")
            from app import app, DOWNLOADS_DIR
            if not hasattr(app.state, 'storage'):
                from state_storage import StateStorage
                # Используем тот же путь, что и в app.py
                app_state_file = os.path.join(DOWNLOADS_DIR, "state.json")
                app.state.storage = StateStorage(app_state_file)

            # Инициализируем storage синхронно
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                # Если нет event loop, создаем новый
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if not app.state.storage._initialized:
                future = asyncio.run_coroutine_threadsafe(
                    app.state.storage.initialize(),
                    loop
                )
                future.result(timeout=5)

        # Получаем текущий event loop или создаем новый
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Добавляем timestamp к состоянию
        state['timestamp'] = time.time()

        # Обновляем состояние
        storage_key = f'download_{download_id}' if not download_id.startswith('download_') else download_id
        future = asyncio.run_coroutine_threadsafe(
            _app.state.storage.update_item(storage_key, state),
            loop
        )
        future.result(timeout=5)

        logging.info(f"[STATE] Updated state for {storage_key}: {state}")

    except Exception as e:
        logging.error(f"[STATE] Error updating state for {download_id}: {str(e)}", exc_info=True)

def is_loom_url(url: str) -> bool:
    """
    Проверяет, является ли URL ссылкой на Loom

    Args:
        url: URL для проверки

    Returns:
        bool: True если URL ведет на Loom, иначе False
    """
    try:
        parsed = urlparse(url)
        return parsed.netloc.endswith('loom.com') and '/share/' in parsed.path
    except Exception:
        return False

def extract_loom_id(url: str) -> str:
    """
    Извлекает ID видео из URL Loom

    Args:
        url: URL Loom

    Returns:
        str: ID видео
    """
    try:
        parsed = urlparse(url)
        path_parts = parsed.path.split('/')
        if len(path_parts) >= 3 and path_parts[1] == 'share':
            # Возвращаем ID видео (третий элемент после разделения)
            return path_parts[2].split('?')[0]  # Удаляем параметры запроса, если они есть
    except Exception as e:
        logging.error(f"[LOOM] Ошибка при извлечении ID: {str(e)}")
    return ""

async def sanitize_filename(filename: str) -> str:
    """
    Очищает имя файла от недопустимых символов

    Args:
        filename: Исходное имя файла

    Returns:
        str: Очищенное имя файла
    """
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

async def delete_file_after_delay(file_path: str, delay: int = 86400) -> None:
    """
    Удаляет файл после указанной задержки

    Args:
        file_path: Путь к файлу
        delay: Задержка в секундах (по умолчанию 24 часа)
    """
    try:
        await asyncio.sleep(delay)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logging.info(f"[DELETE] Deleted file {file_path}")
            except Exception as e:
                logging.error(f"[DELETE] Error deleting file {file_path}: {str(e)}", exc_info=True)
    except Exception as e:
        logging.error(f"[DELETE] Error in delete task for {file_path}: {str(e)}", exc_info=True)

async def get_cookies_path() -> str:
    """
    Получает путь к файлу cookies

    Returns:
        str: Путь к файлу cookies
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube.com_cookies.txt")

async def get_ffmpeg_path() -> Optional[str]:
    """
    Получает путь к ffmpeg

    Returns:
        Optional[str]: Путь к ffmpeg или None если не найден
    """
    try:
        # Проверяем стандартный путь в контейнере
        container_ffmpeg = "/usr/bin/ffmpeg"
        if await asyncio.to_thread(os.path.exists, container_ffmpeg):
            return container_ffmpeg

        # Проверяем локальную директорию
        local_ffmpeg = os.path.join(os.path.dirname(__file__), "bin", "ffmpeg")
        if await asyncio.to_thread(os.path.exists, local_ffmpeg):
            return local_ffmpeg

        # Ищем в системных путях
        import shutil
        ffmpeg_path = await asyncio.to_thread(shutil.which, "ffmpeg")
        if ffmpeg_path:
            return ffmpeg_path

        return None

    except Exception as e:
        logging.error(f"[FFMPEG] Error getting ffmpeg path: {str(e)}", exc_info=True)
        return None

async def get_safe_ydl_opts(
    output_file: str,
    download_id: Optional[str],
    ffmpeg_location: Optional[str] = None
) -> Dict[str, Any]:
    """
    Получает безопасные опции для yt-dlp

    Args:
        output_file: Путь для сохранения файла
        download_id: ID загрузки
        ffmpeg_location: Путь к ffmpeg

    Returns:
        Dict[str, Any]: Словарь с опциями
    """
    try:
        async def progress_hook(d):
            if not download_id:
                return

            try:
                status = d.get('status', '')
                if status == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    progress = (downloaded / total * 100) if total else 0
                    await update_download_status(download_id=download_id, status="downloading", progress=progress)
                elif status == 'finished':
                    await update_download_status(download_id=download_id, status="completed", progress=100)
                elif status == 'error':
                    await update_download_status(download_id=download_id, status="error", error=str(d.get('error')))
            except Exception as e:
                logging.error(f"[YDL] Progress hook error: {str(e)}", exc_info=True)

        # Базовые опции
        opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'outtmpl': output_file or '%(title)s.%(ext)s',
            'progress_hooks': [lambda d: asyncio.create_task(progress_hook(d))],

            # Настройки для обхода ограничений
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'no_color': True,
            'extract_flat': False,

            # Настройки для запросов
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            }
        }

        # Добавляем путь к ffmpeg если указан
        if ffmpeg_location:
            opts['ffmpeg_location'] = ffmpeg_location

        return opts

    except Exception as e:
        logging.error(f"[YDL] Error getting options: {str(e)}", exc_info=True)
        return {}

async def get_yt_dlp_opts(download_id: str, output_path: str) -> Dict[str, Any]:
    """
    Получает опции для yt-dlp

    Args:
        download_id: ID загрузки
        output_path: Путь для сохранения файла

    Returns:
        Dict: Опции для yt-dlp
    """
    # Формируем путь для сохранения файла
    output_file = os.path.join(output_path, f'%(title)s-{download_id}.%(ext)s')

    # Получаем путь к ffmpeg
    ffmpeg_location = await get_ffmpeg_path()

    # Получаем базовые опции
    opts = await get_safe_ydl_opts(output_file, download_id, ffmpeg_location)

    # Добавляем дополнительные опции для обработки видео
    opts.update({
        'format': 'best[ext=mp4]/best',  # Берем лучшее качество в формате mp4
        'merge_output_format': 'mp4',
        'postprocessors': [
            {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
        ]
    })

    return opts

async def download_video(url: str, download_id: str, ffmpeg_location: str = "") -> Optional[str]:
    """
    Скачивает видео по URL

    Args:
        url: URL видео
        download_id: ID загрузки
        ffmpeg_location: Путь к ffmpeg

    Returns:
        Optional[str]: Путь к скачанному файлу или None при ошибке
    """
    try:
        # Добавляем префикс к ID если его нет
        if not download_id.startswith('download_'):
            download_id = f'download_{download_id}'

        output_file = os.path.join(os.path.dirname(__file__), 'downloads', f'{download_id}.mp4')
        ydl_opts = await get_safe_ydl_opts(output_file, download_id, ffmpeg_location)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logging.info(f"[DOWNLOAD] Начало загрузки {url}")
            ydl.download([url])
            logging.info(f"[DOWNLOAD] Загрузка {url} завершена")
            return output_file

    except Exception as e:
        logging.error(f"[DOWNLOAD] Ошибка при загрузке {url}: {str(e)}")
        await update_download_status(download_id, "error", error=str(e))
        return None

async def download_m3u8(url: str, output_path: str, download_id: Optional[str] = None) -> bool:
    """
    Скачивает видео в формате m3u8 с помощью ffmpeg

    Args:
        url: URL видео
        output_path: Путь для сохранения
        download_id: ID загрузки

    Returns:
        bool: True если загрузка успешна, False при ошибке
    """
    try:
        ffmpeg_path = await get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError("FFmpeg не найден в системе")

        logging.info(f"[M3U8] Начало загрузки {url}")
        process = await asyncio.create_subprocess_exec(
            ffmpeg_path,
            '-i', url,
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Неизвестная ошибка FFmpeg"
            logging.error(f"[M3U8] Ошибка FFmpeg: {error_msg}")
            if download_id:
                await update_download_status(download_id, "error", error=error_msg)
            return False

        logging.info(f"[M3U8] Загрузка {url} завершена")
        return True

    except Exception as e:
        error_msg = f"Ошибка при загрузке M3U8: {str(e)}"
        logging.error(f"[M3U8] {error_msg}")
        if download_id:
            await update_download_status(download_id, "error", error=error_msg)
        return False

async def download_with_requests(url: str, output_path: str, download_id: str) -> Optional[str]:
    """
    Скачивает видео напрямую через requests

    Args:
        url: URL видео
        output_path: Путь для сохранения
        download_id: ID загрузки

    Returns:
        Optional[str]: Путь к скачанному файлу или None при ошибке
    """
    try:
        logging.info(f"[REQUESTS] Начало загрузки {url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if not response.ok:
                    raise aiohttp.ClientError(f"HTTP {response.status}: {response.reason}")

                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0

                async with aiofiles.open(output_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        await f.write(chunk)
                        downloaded += len(chunk)

                        if total_size:
                            progress = (downloaded / total_size) * 100
                            await update_download_status(download_id, "downloading", progress=progress)

        logging.info(f"[REQUESTS] Загрузка {url} завершена")
        return output_path

    except aiohttp.ClientError as e:
        error_msg = f"Ошибка сети: {str(e)}"
        logging.error(f"[REQUESTS] {error_msg}")
        await update_download_status(download_id, "error", error=error_msg)
        return None

    except IOError as e:
        error_msg = f"Ошибка записи файла: {str(e)}"
        logging.error(f"[REQUESTS] {error_msg}")
        await update_download_status(download_id, "error", error=error_msg)
        return None

    except Exception as e:
        error_msg = f"Неожиданная ошибка: {str(e)}"
        logging.error(f"[REQUESTS] {error_msg}")
        await update_download_status(download_id, "error", error=error_msg)
        return None

async def download_loom_video(url: str, output_path: str, download_id: str) -> Optional[str]:
    """
    Скачивает видео с Loom используя yt-dlp

    Args:
        url: URL видео Loom
        output_path: Путь для сохранения
        download_id: ID загрузки

    Returns:
        Optional[str]: Путь к скачанному файлу или None при ошибке
    """
    try:
        logging.info(f"[LOOM] Начало загрузки {url}")

        # Извлекаем ID видео из URL
        video_id = extract_loom_id(url)
        if not video_id:
            raise ValueError("Не удалось извлечь ID видео из URL")

        # Обновляем статус загрузки
        await update_download_status(download_id, "downloading", progress=10)

        # Используем yt-dlp для скачивания видео
        logging.info(f"[LOOM] Используем yt-dlp для скачивания {url}")

        # Создаем прогресс-хук для отслеживания прогресса
        progress_file = os.path.join(os.path.dirname(output_path), f"{download_id}_progress.txt")

        # Создаем файл для отслеживания прогресса
        with open(progress_file, 'w') as f:
            f.write('0')

        # Запускаем отдельный поток для обновления прогресса
        async def update_progress_task():
            try:
                last_progress = 0
                last_update_time = time.time()
                no_progress_counter = 0

                # Сразу устанавливаем начальный прогресс
                await update_download_status(download_id, "downloading", progress=1)
                logging.info(f"[LOOM] Установлен начальный прогресс 1% для {download_id}")

                # Имитируем начальный прогресс
                for initial_progress in range(2, 10, 2):
                    await asyncio.sleep(1)
                    await update_download_status(download_id, "downloading", progress=initial_progress)
                    logging.info(f"[LOOM] Имитация начального прогресса: {initial_progress}% для {download_id}")

                while True:
                    try:
                        # Проверяем, существует ли файл прогресса
                        if not os.path.exists(progress_file):
                            logging.info(f"[LOOM] Файл прогресса не найден, завершаем задачу")
                            break

                        # Читаем текущий прогресс из файла
                        with open(progress_file, 'r') as f:
                            progress_str = f.read().strip()

                        if progress_str and progress_str.isdigit():
                            current_progress = int(progress_str)

                            # Если прогресс изменился или прошло больше 1 секунды с момента последнего обновления
                            current_time = time.time()
                            if current_progress != last_progress or (current_time - last_update_time) > 1:
                                logging.info(f"[LOOM] Обновляем прогресс: {current_progress}% для {download_id}")
                                await update_download_status(download_id, "downloading", progress=current_progress)
                                last_progress = current_progress
                                last_update_time = current_time
                                no_progress_counter = 0
                            else:
                                no_progress_counter += 1
                        else:
                            logging.warning(f"[LOOM] Некорректный формат прогресса в файле: '{progress_str}'")
                            no_progress_counter += 1

                        # Если прогресс достиг 100%, завершаем задачу
                        if last_progress >= 100:
                            logging.info(f"[LOOM] Прогресс достиг 100%, завершаем задачу")
                            break

                        # Если прогресс не меняется долгое время, имитируем прогресс
                        if no_progress_counter > 3 and last_progress < 90:  # Если нет прогресса в течение 3 циклов
                            new_progress = min(90, last_progress + 0.5)  # Увеличиваем прогресс на 0.5%
                            logging.info(f"[LOOM] Имитация прогресса: {new_progress}% для {download_id}")
                            with open(progress_file, 'w') as f:
                                f.write(str(int(new_progress)))
                            # Сразу обновляем прогресс в хранилище
                            await update_download_status(download_id, "downloading", progress=new_progress)
                            no_progress_counter = 0

                        # Ждем небольшую паузу перед следующей проверкой
                        await asyncio.sleep(0.3)  # Уменьшаем интервал проверки до 0.3 секунд
                    except Exception as e:
                        logging.error(f"[LOOM] Ошибка при обновлении прогресса: {str(e)}")
                        await asyncio.sleep(1)
            except Exception as e:
                logging.error(f"[LOOM] Ошибка в задаче обновления прогресса: {str(e)}")
            finally:
                # Удаляем файл прогресса при завершении
                if os.path.exists(progress_file):
                    try:
                        os.remove(progress_file)
                    except:
                        pass

        # Запускаем задачу обновления прогресса
        logging.info(f"[LOOM] Запуск задачи обновления прогресса для {download_id}")
        progress_task = asyncio.create_task(update_progress_task())

        def progress_hook(d):
            try:
                logging.info(f"[LOOM] Progress hook called with status: {d.get('status')}, data: {d}")

                if d['status'] == 'downloading':
                    # Получаем прогресс загрузки
                    downloaded = d.get('downloaded_bytes', 0)
                    total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)

                    # Получаем информацию о фрагментах
                    fragment_index = d.get('fragment_index', 0)
                    fragment_count = d.get('fragment_count', 0)

                    # Получаем информацию о текущем прогрессе
                    try:
                        with open(progress_file, 'r') as f:
                            current_progress_str = f.read().strip()
                            current_progress = int(current_progress_str) if current_progress_str.isdigit() else 0
                    except Exception:
                        current_progress = 0

                    # Вычисляем прогресс на основе фрагментов, если они доступны
                    if fragment_count > 0:
                        # Используем фрагменты для расчета прогресса
                        # Распределяем прогресс от 5% до 90%
                        fragment_progress = 5 + int((fragment_index / fragment_count) * 85)
                        fragment_progress = min(90, fragment_progress)

                        # Обновляем прогресс всегда, чтобы отражать реальный прогресс
                        logging.info(f"[LOOM] Fragment progress: {fragment_progress}% ({fragment_index}/{fragment_count})")
                        with open(progress_file, 'w') as f:
                            f.write(str(fragment_progress))
                    elif total > 0:
                        # Используем стандартный метод, если фрагменты недоступны
                        # Распределяем прогресс от 5% до 90%
                        bytes_progress = 5 + int((downloaded / total) * 85)
                        bytes_progress = min(90, bytes_progress)

                        # Обновляем прогресс всегда, чтобы отражать реальный прогресс
                        logging.info(f"[LOOM] Bytes progress: {bytes_progress}% ({downloaded}/{total})")
                        with open(progress_file, 'w') as f:
                            f.write(str(bytes_progress))
                    else:
                        # Если нет информации о прогрессе, используем имитацию прогресса
                        # Увеличиваем прогресс на небольшую величину
                        new_progress = min(90, current_progress + 1)
                        logging.info(f"[LOOM] Simulated progress: {new_progress}%")
                        with open(progress_file, 'w') as f:
                            f.write(str(new_progress))

                elif d['status'] == 'finished':
                    # Записываем 98% прогресса при завершении загрузки
                    with open(progress_file, 'w') as f:
                        f.write('98')
                    logging.info(f"[LOOM] yt-dlp завершил загрузку")
            except Exception as e:
                logging.error(f"[LOOM] Ошибка в progress_hook: {str(e)}")

        # Настройки для yt-dlp
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',  # Выбираем лучшее видео и аудио
            'outtmpl': output_path,  # Путь для сохранения
            'progress_hooks': [progress_hook],  # Хук для отслеживания прогресса
            'quiet': False,  # Выводим информацию о загрузке
            'no_warnings': False,  # Выводим предупреждения
            'ignoreerrors': False,  # Не игнорируем ошибки
            'retries': 10,  # Количество попыток
            'fragment_retries': 10,  # Количество попыток для фрагментов
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',  # Конвертируем в MP4
            }, {
                'key': 'FFmpegEmbedSubtitle',  # Встраиваем субтитры, если они есть
            }, {
                'key': 'FFmpegMetadata',  # Сохраняем метаданные
            }],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Referer': 'https://www.loom.com/',
            }
        }

        # Запускаем yt-dlp для скачивания
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Скачиваем видео
                ydl.download([url])

                # Проверяем, что файл существует
                if not os.path.exists(output_path):
                    raise FileNotFoundError(f"Файл {output_path} не был создан")

                # Записываем 100% прогресса при завершении
                with open(progress_file, 'w') as f:
                    f.write('100')

                # Ждем завершения задачи обновления прогресса
                try:
                    await asyncio.wait_for(progress_task, timeout=5.0)
                except asyncio.TimeoutError:
                    # Если задача не завершилась вовремя, обновляем статус напрямую
                    await update_download_status(download_id, "completed", progress=100)

                logging.info(f"[LOOM] Загрузка завершена: {output_path}")
                return output_path

            except Exception as e:
                logging.error(f"[LOOM] Ошибка при скачивании видео с yt-dlp: {str(e)}")

                # Пробуем альтернативный метод - используем subprocess
                logging.info(f"[LOOM] Пробуем альтернативный метод с subprocess")

                try:
                    import subprocess
                    cmd = [
                        'yt-dlp',
                        '-f', 'bestvideo+bestaudio/best',
                        '-o', output_path,
                        '--no-warnings',
                        '--retries', '10',
                        '--fragment-retries', '10',
                        '--merge-output-format', 'mp4',
                        '--embed-subs',
                        '--embed-metadata',
                        url
                    ]

                    # Запускаем команду
                    subprocess.run(cmd, check=True, timeout=300)

                    # Проверяем, что файл существует
                    if not os.path.exists(output_path):
                        raise FileNotFoundError(f"Файл {output_path} не был создан")

                    # Записываем 100% прогресса при завершении
                    with open(progress_file, 'w') as f:
                        f.write('100')

                    # Ждем завершения задачи обновления прогресса
                    try:
                        await asyncio.wait_for(progress_task, timeout=5.0)
                    except asyncio.TimeoutError:
                        # Если задача не завершилась вовремя, обновляем статус напрямую
                        await update_download_status(download_id, "completed", progress=100)

                    logging.info(f"[LOOM] Загрузка завершена с использованием subprocess: {output_path}")
                    return output_path

                except Exception as sub_e:
                    logging.error(f"[LOOM] Ошибка при использовании subprocess: {str(sub_e)}")
                    raise

    except Exception as e:
        error_msg = f"Не удалось скачать видео с Loom: {str(e)}"
        logging.error(f"[LOOM] {error_msg}")
        await update_download_status(download_id, "error", error=error_msg)
        return None

async def download_with_selenium(url: str, output_path: str, download_id: str) -> Optional[str]:
    """
    Скачивает видео через selenium с эмуляцией браузера

    Args:
        url: URL видео
        output_path: Путь для сохранения
        download_id: ID загрузки

    Returns:
        Optional[str]: Путь к скачанному файлу или None при ошибке
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        logging.info(f"[SELENIUM] Начало загрузки {url}")

        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_experimental_option('prefs', {
            'download.default_directory': output_path,
            'download.prompt_for_download': False,
        })

        driver = webdriver.Chrome(options=chrome_options)
        try:
            driver.get(url)

            # Ждем загрузки страницы
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(('tag name', 'video'))
            )

            video_url = driver.execute_script(
                "return document.querySelector('video').src"
            )

            if not video_url:
                raise ValueError("Не удалось найти URL видео на странице")

            # Используем aiohttp для скачивания
            return await download_with_requests(video_url, output_path, download_id)

        finally:
            driver.quit()

    except ImportError as e:
        error_msg = "Selenium не установлен"
        logging.error(f"[SELENIUM] {error_msg}")
        await update_download_status(download_id, "error", error=error_msg)
        return None

    except Exception as e:
        error_msg = f"Ошибка при загрузке через Selenium: {str(e)}"
        logging.error(f"[SELENIUM] {error_msg}")
        await update_download_status(download_id, "error", error=error_msg)
        return None

async def get_disk_space(path: str) -> Tuple[int, int]:
    """
    Получить информацию о свободном месте на диске

    Args:
        path: Путь к директории

    Returns:
        Tuple[int, int]: (общее место, свободное место) в байтах
    """
    try:
        if os.name == 'nt':
            import ctypes
            free_bytes = ctypes.c_ulonglong(0)
            total_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(path),
                None,
                ctypes.pointer(total_bytes),
                ctypes.pointer(free_bytes)
            )
            return total_bytes.value, free_bytes.value
        else:
            st = os.statvfs(path)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            return total, free
    except Exception as e:
        logging.error(f"[DISK] Error getting disk space: {str(e)}", exc_info=True)
        return 0, 0

async def clean_old_logs(log_path: str, max_size_mb: int = 10) -> None:
    """
    Очищает старые логи если файл превышает максимальный размер

    Args:
        log_path: Путь к логам
        max_size_mb: Максимальный размер логов в мегабайтах
    """
    try:
        # Проверяем существование файла
        if not os.path.exists(log_path):
            return

        # Получаем размер файла
        size_mb = os.path.getsize(log_path) / (1024 * 1024)

        if size_mb > max_size_mb:
            # Читаем последние строки
            async with aiofiles.open(log_path, 'r') as f:
                content = await f.read()
                lines = content.splitlines()

            # Оставляем только последние строки
            keep_lines = int(len(lines) * 0.8)  # Оставляем 80% строк
            new_content = '\n'.join(lines[-keep_lines:]) + '\n'

            # Перезаписываем файл
            async with aiofiles.open(log_path, 'w') as f:
                await f.write(new_content)

            logging.info(f"[LOGS] Cleaned old logs, new size: {os.path.getsize(log_path) / (1024 * 1024):.1f}MB")

    except Exception as e:
        logging.error(f"[LOGS] Error cleaning logs: {str(e)}", exc_info=True)

def get_download_state_sync(download_id: str) -> Optional[Dict[str, Any]]:
    """Синхронно получить состояние загрузки"""
    try:
        if not _app or not hasattr(_app.state, 'storage'):
            logging.error("[STATE] App or storage not initialized")
            return None

        # Создаем футуру для асинхронного получения состояния
        loop = asyncio.get_event_loop()
        future = asyncio.run_coroutine_threadsafe(
            _app.state.storage.get_item(f"download_{download_id}"),
            loop
        )
        # Ждем результат с таймаутом
        return future.result(timeout=5)

    except Exception as e:
        logging.error(f"[STATE] Error getting state for {download_id}: {str(e)}")
        return None

def update_download_state_sync(download_id: str, state: Dict[str, Any]):
    """Синхронно обновить состояние загрузки"""
    try:
        if not _app or not hasattr(_app.state, 'storage'):
            logging.warning("[STATE] App or storage not initialized, выполняется ленивый запуск.")
            from app import app, DOWNLOADS_DIR
            if not hasattr(app.state, 'storage'):
                from state_storage import StateStorage
                # Используем тот же путь, что и в app.py
                app_state_file = os.path.join(DOWNLOADS_DIR, "state.json")
                app.state.storage = StateStorage(app_state_file)
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(app.state.storage.initialize(), loop).result(timeout=5)

        # Получаем текущий event loop или создаем новый
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Добавляем timestamp к состоянию
        state['timestamp'] = time.time()

        # Обновляем состояние
        storage_key = f'download_{download_id}' if not download_id.startswith('download_') else download_id
        future = asyncio.run_coroutine_threadsafe(
            _app.state.storage.update_item(storage_key, state),
            loop
        )
        future.result(timeout=5)

        logging.info(f"[STATE] Updated state for {storage_key}: {state}")

    except Exception as e:
        logging.error(f"[STATE] Error updating state for {download_id}: {str(e)}")

async def clear_logs_task(delay=3600, logs_dir=None):
    """Очистка старых логов"""
    try:
        while True:
            try:
                if not logs_dir:
                    logs_dir = os.path.join(os.path.dirname(__file__), 'downloads', 'logs')
                await clean_old_logs(logs_dir)
            except Exception as e:
                logging.error(f"[LOGS] Error cleaning logs: {str(e)}", exc_info=True)
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        pass

import asyncio
