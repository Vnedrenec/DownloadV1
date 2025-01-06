import logging
import threading
downloads_lock = threading.Lock()
import os
import subprocess
import tempfile
import uuid
import time
import json
from urllib.parse import urlparse
import sys
from bottle import route, run, static_file, request, response, template, error

@route('/static/<filename>')
def serve_static(filename):
    return static_file(filename, root='./static')

# Получаем директорию, где находится скрипт
script_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(script_dir, 'server.log')

# Настройка логгера
def setup_logger():
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.info("Логгирование настроено.")
    return logger

logger = setup_logger()

def get_video_duration(url):
    result = None
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        return float(result.stdout.strip()) if result.stdout.strip() else None
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка ffprobe: {e.stderr}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка в ffprobe: {repr(e)}")
    return None

# Константы
ALLOWED_DOMAINS = ['dyckms5inbsqq.cloudfront.net', 'example.com']
CLEANUP_INTERVAL = 600
MAX_DOWNLOADS = 5

downloads = {}

# ===== Утилитарные функции =====
def is_valid_url(url):
    """Проверяет, является ли URL допустимым и разрешен ли домен."""
    try:
        # Проверка длины URL
        if len(url) > 2048:
            return False
            
        parsed = urlparse(url)
        
        # Проверка схемы и домена
        if parsed.scheme not in ('http', 'https') or parsed.netloc not in ALLOWED_DOMAINS:
            return False
            
        # Проверка на недопустимые символы
        invalid_chars = set('<>"\'\\')
        if any(char in url for char in invalid_chars):
            return False
            
        # Проверка параметров запроса
        if parsed.query:
            try:
                # Проверяем корректность параметров
                from urllib.parse import parse_qs
                parse_qs(parsed.query)
            except ValueError:
                return False
                
        # Экранирование специальных символов
        from urllib.parse import quote
        safe_url = quote(url, safe=':/?&=')
        if safe_url != url:
            return False
            
        return True
    except Exception as e:
        logger.error(f"Ошибка при разборе URL: {e}")
        return False

def cleanup_temp_files():
    """Функция для очистки устаревших временных файлов.
    
    Работает в фоновом режиме в отдельном потоке.
    
    Логика работы:
        1. Проверяет возраст каждого файла в downloads
        2. Удаляет файлы старше TEMP_FILE_LIFETIME
        3. Удаляет записи из словаря downloads
        4. Повторяет проверку каждые CLEANUP_INTERVAL секунд
        
    Обрабатываемые ошибки:
        - PermissionError: ошибки прав доступа
        - FileNotFoundError: файл уже удалён
        - OSError: системные ошибки
        
    Особенности:
        - Работает в бесконечном цикле
        - Завершается при KeyboardInterrupt
        - Логирует все операции
    """
    try:
        while True:
            current_time = time.time()
            with downloads_lock:
                for download_id, info in list(downloads.items()):
                    if 'file_path' in info and info['status'] == 'completed':
                        file_age = current_time - os.path.getmtime(info['file_path'])
                        if file_age > TEMP_FILE_LIFETIME:
                            if info.get('status') == 'completed' and os.path.exists(info['file_path']):
                                try:
                                    os.remove(info['file_path'])
                                    del downloads[download_id]
                                    logger.info(f"Временный файл удалён: {info['file_path']}")
                                except PermissionError as e:
                                    logger.warning(f"Ошибка прав доступа при удалении {info['file_path']}: {e}")
                                except FileNotFoundError as e:
                                    logger.warning(f"Файл не найден при удалении {info['file_path']}: {e}")
                                except OSError as e:
                                    logger.warning(f"Системная ошибка при удалении {info['file_path']}: {e}")
            try:
                time.sleep(CLEANUP_INTERVAL)
            except KeyboardInterrupt:
                logger.info("Очистка временных файлов завершена.")
                break
    finally:
        logger.info("Фоновый поток очистки завершён.")

def timeout_handler(process, download_id):
    if process.poll() is None:
        process.terminate()
        with downloads_lock:
            downloads[download_id]['status'] = 'timeout'
        logger.error(f"FFmpeg завершён из-за тайм-аута для ID {download_id}.")

import os
downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
if not os.path.exists(downloads_dir):
    os.makedirs(downloads_dir)

def handle_download(video_url, download_id):
    """Основная функция обработки загрузки видео.
    
    Args:
        video_url (str): URL видео для загрузки
        download_id (str): Уникальный идентификатор загрузки
        
    Процесс работы:
        1. Создает выходной файл в директории downloads
        2. Запускает ffmpeg для загрузки видео
        3. Обрабатывает прогресс загрузки
        4. В случае ошибки ffmpeg пробует yt-dlp
        5. Управляет состоянием загрузки в глобальном словаре downloads
        6. Очищает ресурсы при завершении
        
    Возможные состояния загрузки:
        - processing: загрузка в процессе
        - completed: загрузка успешно завершена
        - error: произошла ошибка
        
    Обрабатываемые ошибки:
        - Ошибки subprocess при вызове ffmpeg/yt-dlp
        - Ошибки файловой системы
        - Тайм-ауты выполнения
    """
    process = None
    try:
        output_path = os.path.join(downloads_dir, f"{download_id}.mp4")
        with downloads_lock:
            downloads[download_id] = {'status': 'processing', 'progress': 0}

        # Сначала пробуем через ffmpeg
        command = [
            'ffmpeg', '-i', video_url,
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            output_path
        ]
        logger.info(f"Запуск команды: {' '.join(command)}")

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        downloads[download_id]['process'] = process
        logger.debug(f"FFmpeg PID: {process.pid}") # Лог PID процесса

        timer = threading.Timer(600, timeout_handler, [process, download_id])
        timer.start()

        # Функция для чтения stderr в отдельном потоке
        def read_stderr():
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                logger.debug(f"FFmpeg stderr: {line.strip()}")
                # Обработка прогресса из stderr
                if 'time=' in line:
                    try:
                        time_str = line.split('time=')[1].split()[0]
                        h, m, s = map(float, time_str.split(':'))
                        total_seconds = h * 3600 + m * 60 + s
                        with downloads_lock:
                            downloads[download_id]['progress'] = int(total_seconds)
                        logger.info(f"Прогресс загрузки {download_id}: {total_seconds} секунд")
                    except (IndexError, ValueError) as e:
                        logger.warning(f"Не удалось обработать строку прогресса: {line.strip()}")

        # Функция для чтения stdout в отдельном потоке
        def read_stdout():
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                logger.debug(f"FFmpeg stdout: {line.strip()}")

        # Запуск потоков для чтения
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stdout_thread = threading.Thread(target=read_stdout, daemon=True)
        stderr_thread.start()
        stdout_thread.start()

        # Ожидание завершения процесса
        process.wait()
        stderr_thread.join()
        stdout_thread.join()

        if timer.is_alive():
            timer.cancel()

        if process.returncode == 0:
            downloads[download_id].update({'status': 'completed', 'file_path': output_path})
            # Получаем длительность видео
            duration = get_video_duration(output_path)
            if duration is not None:
                downloads[download_id]['duration'] = duration
                logger.info(f"Длительность видео: {duration} секунд")
            logger.info(f"Скачивание завершено: {output_path}")
        else:
            stderr = process.stderr.read()
            if process.returncode == 1:
                logger.info(f"FFmpeg завершился с кодом 1. Подробности: {stderr}")
                # Если ffmpeg не справился, пробуем через yt-dlp
                if download_hls_with_ytdlp(video_url, output_path):
                    downloads[download_id].update({
                        'status': 'completed',
                        'file_path': output_path,
                        'progress': 100
                    })
                    duration = get_video_duration(output_path)
                    if duration is not None:
                        downloads[download_id]['duration'] = duration
                    logger.info(f"Видео успешно скачано через yt-dlp: {output_path}")
                else:
                    logger.error(f"Ошибка при скачивании через yt-dlp")
                    downloads[download_id]['status'] = 'error'
            else:
                logger.error(f"Ошибка FFmpeg: {stderr}")
                downloads[download_id]['status'] = 'error'
    except Exception as e:
        error_message = str(e)
        logger.error(f"Ошибка в handle_download: {error_message}")
        downloads[download_id]['status'] = 'error'
        downloads[download_id]['message'] = error_message
    finally:
        try:
            # Безопасное закрытие потоков
            if process:
                if process.stdout and not process.stdout.closed:
                    process.stdout.close()
                if process.stderr and not process.stderr.closed:
                    process.stderr.close()
            
            # Удаление незавершенного файла
            if 'status' in downloads[download_id] and downloads[download_id]['status'] != 'completed' and os.path.exists(output_path):
                os.remove(output_path)
                logger.info(f"Удален незавершенный файл: {output_path}")
                
        except Exception as e:
            logger.error(f"Ошибка при завершении загрузки: {e}")

# ===== Маршруты Bottle =====
@route('/')
def index():
    """Главная страница с формой."""
    return static_file('index.html', root='./views')

@route('/download', method='POST')
def download():
    logger.info("Получен запрос /download")
    try:
        video_url = request.forms.get('url')
        logger.info(f"URL, полученный в запросе: {video_url}")
        if not video_url:
            logger.warning("URL не предоставлен в запросе /download.")
            response.status = 400
            response.headers['Content-Type'] = 'application/json'
            return json.dumps({"status": "error", "message": "URL не предоставлен"})
        logger.info("URL предоставлен.")

        # Проверяем, является ли URL YouTube
        if 'youtube.com' in video_url or 'youtu.be' in video_url:
            logger.info("Обнаружен YouTube URL, начинаем обработку.")
            download_id = str(uuid.uuid4())
            output_path = os.path.join(downloads_dir, f"{download_id}.mp4")

            # Скачиваем видео с YouTube
            success = download_youtube_video(video_url, output_path)
            if success:
                downloads[download_id] = {'status': 'completed', 'file_path': output_path}
                response.headers['Content-Type'] = 'application/json'
                response.status = 202
                return {"download_id": download_id, "status": "success"}
            else:
                response.status = 500
                return {"error": "Не удалось скачать видео с YouTube"}

        if not is_valid_url(video_url.strip()):
            logger.warning(f"Недопустимый URL: {video_url}")
            response.status = 400
            response.headers['Content-Type'] = 'application/json'
            return json.dumps({"status": "error", "message": "Недопустимый URL"})
        logger.info("URL прошел валидацию.")

        # Проверяем количество активных загрузок
        with downloads_lock:
            active_downloads = sum(1 for d in downloads.values() if d['status'] == 'processing')
            if active_downloads >= MAX_DOWNLOADS:
                logger.warning("Превышено максимальное количество одновременных загрузок.")
                response.status = 429
                response.headers['Content-Type'] = 'application/json'
                return json.dumps({
                    "status": "error", 
                    "message": f"Превышено максимальное количество одновременных загрузок ({MAX_DOWNLOADS}). Попробуйте позже."
                })

        download_id = str(uuid.uuid4())
        downloads[download_id] = {'status': 'pending'}
        threading.Thread(target=handle_download, args=(video_url.strip(), download_id)).start()

        response.headers['Content-Type'] = 'application/json'
        response.status = 202
        logger.info(f"Отправлен ID загрузки: {download_id}")
        return {"download_id": download_id, "status": "success"}
    except Exception as e:
        logger.error(f"Ошибка в /download: {e}")
        response.status = 500
        return {'error': str(e), 'status': 'error'}

@route('/progress/<download_id>')
def progress(download_id):
    """Возвращает текущий статус загрузки."""
    logger.info(f"Вызов /progress с download_id: {download_id}")
    try:
        with downloads_lock:
            data = downloads.get(download_id)
            if not data:
                logger.warning(f"ID загрузки не найден: {download_id}")
                response.status = 404
                response.headers['Content-Type'] = 'application/json'
                return json.dumps({"status": "error", "message": "ID не найден"})
            logger.info(f"Запрос состояния загрузки ID {download_id}: {downloads.get(download_id, {}).get('status', 'неизвестно')}")
            logger.info(f"Статус загрузки {download_id}: {data.get('status')}")
        return {
            'status': data['status'],
            'progress': data.get('progress', 0),
            'message': data.get('message', ''),
            'duration': data.get('duration'),
            'file_path': data.get('file_path')
        }
    except Exception as e:
        logger.error(f"Ошибка в /progress/{download_id}: {e}")
        response.status = 500
        response.headers['Content-Type'] = 'application/json'
        return json.dumps({"status": "error", "message": "Internal server error"})


@route('/download_file/<download_id>')
def download_file(download_id):
    """Позволяет скачать завершённый файл и удалить его после отправки."""
    logger.info(f"Вызов /download_file с download_id: {download_id}")
    try:
        if download_id in downloads and downloads[download_id]['status'] == 'completed':
            file_path = downloads[download_id]['file_path']
            if os.path.exists(file_path):
                # Отправляем файл клиенту
                response.headers['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
                response.headers['Content-Type'] = 'application/octet-stream'

                # Читаем содержимое файла для отправки
                with open(file_path, 'rb') as file:
                    file_content = file.read()

                # Удаляем файл после отправки
                os.remove(file_path)
                logger.info(f"Файл {file_path} удалён после отправки.")
                return file_content
            else:
                logger.warning(f"Файл не найден для ID загрузки: {download_id}")
                response.status = 404
                response.headers['Content-Type'] = 'application/json'
                return json.dumps({"status": "error", "message": "Файл не найден"})
        else:
            response.status = 404
            response.headers['Content-Type'] = 'application/json'
            return json.dumps({"status": "error", "message": "Скачивание ещё не завершено или файл не существует"})
    except Exception as e:
        logger.error(f"Ошибка при скачивании файла {download_id}: {e}")
        response.status = 500
        response.headers['Content-Type'] = 'application/json'
        return json.dumps({"status": "error", "message": "Internal server error"})

@route('/stop_download/<download_id>', method='POST')
def stop_download(download_id):
    """Останавливает процесс скачивания."""
    try:
        if download_id not in downloads:
            response.status = 404
            response.headers['Content-Type'] = 'application/json'
            return json.dumps({"status": "error", "message": "ID не найден"})
            
        with downloads_lock:
            downloads[download_id]['status'] = 'stopped'
            if 'process' in downloads[download_id]:
                downloads[download_id]['process'].terminate()
                logger.info(f"Процесс FFmpeg для ID {download_id} остановлен.")
            # Добавляем удаление файла после остановки
            if os.path.exists(downloads[download_id].get('file_path', '')):
                os.remove(downloads[download_id]['file_path'])
                logger.info(f"Удалён файл {downloads[download_id]['file_path']} после остановки.")
            else:
                logger.warning(f"Файл для ID {download_id} уже удалён или не существует.")
            return {'status': 'ok', 'message': f'Скачивание {download_id} остановлено.'}
    except Exception as e:
        logger.error(f"Ошибка в /stop_download/{download_id}: {e}")
        response.status = 500
        response.headers['Content-Type'] = 'application/json'
        return json.dumps({"status": "error", "message": "Internal server error"})

@route('/<re:.*>', method='OPTIONS')
def enable_cors():
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Origin, Content-Type, Accept'
    return {}

@error(500)
def error_500(error):
    logger.error(f"Internal server error: {error.body}")
    response.headers['Content-Type'] = 'application/json'
    return json.dumps({"status": "error", "message": "Internal server error"})

# ===== Проверка FFmpeg и FFprobe =====
def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
        subprocess.run(['ffprobe', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
        subprocess.run(['yt-dlp', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
        logger.info("FFmpeg, FFprobe и yt-dlp доступны.")
    except FileNotFoundError as e:
        logger.error(f"FFmpeg, FFprobe или yt-dlp не найдены. Проверьте установку: {e}")
        raise SystemExit("FFmpeg, FFprobe и yt-dlp должны быть установлены для работы приложения.")

def download_hls_with_ytdlp(video_url, output_path):
    try:
        result = subprocess.run(
            ['yt-dlp', '-o', output_path, video_url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        if result.stderr:
            logger.warning(f"yt-dlp stderr: {result.stderr}")
        logger.info(f"Видео успешно скачано с помощью yt-dlp: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при скачивании видео через yt-dlp: {e.stderr}")
        return False

def download_youtube_video(video_url, output_path):
    """
    Скачивает видео с YouTube с помощью yt-dlp.
    """
    try:
        command = [
            'yt-dlp',
            '-f', 'bestvideo+bestaudio/best',  # Выбор лучшего качества видео и аудио
            '--merge-output-format', 'mp4',  # Объединить в формат mp4
            '-o', output_path,  # Сохранить в output_path
            video_url
        ]
        logger.info(f"Запуск команды: {' '.join(command)}")
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        logger.info(f"Видео успешно скачано с YouTube: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при скачивании видео с YouTube: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Неизвестная ошибка при скачивании видео с YouTube: {str(e)}")
        return False


# ===== Запуск сервера =====
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8083, help='Порт для запуска сервера')
    args = parser.parse_args()

    check_ffmpeg()
    threading.Thread(target=cleanup_temp_files, daemon=True).start()

    logger.info("Фоновые потоки запущены.")
    logger.info(f"Сервер запущен на http://localhost:{args.port}")
    run(host='localhost', port=args.port, debug=True)
