import subprocess
import os
import re
import threading
import time
import psutil
from collections import defaultdict
import mimetypes
import uvicorn
import logging
from starlette.applications import Starlette
from starlette.responses import JSONResponse, FileResponse, StreamingResponse
from starlette.routing import Route, Mount
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from pydantic import ValidationError
from models import DownloadRequest, LogErrorRequest
from fastapi import BackgroundTasks, Depends

templates = Jinja2Templates(directory="views")

middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
]

# Хранилище состояния загрузок
downloads = defaultdict(dict)


def update_progress(d, download_id):
    """Обновляет прогресс для yt-dlp"""
    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
        if total_bytes > 0:
            downloaded_bytes = d.get('downloaded_bytes', 0)
            progress = int((downloaded_bytes / total_bytes) * 100)
            downloads[download_id]['progress'] = progress
            print(f"Updated progress {download_id}: {progress}%")

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
        downloads[download_id]['duration'] = total_duration

    # Шаблон для поиска «текущего времени» в логе
    time_match = re.search(r'time=(\d{2}:\d{2}:\d{2}\.\d{2})', line)
    if time_match:
        # Если уже сохранили общую длительность, то считаем процент
        if 'duration' in downloads[download_id] and downloads[download_id]['duration'] > 0:
            h, m, s = time_match.group(1).split(':')
            current_time = float(h) * 3600 + float(m) * 60 + float(s)
            total_duration = downloads[download_id]['duration']
            progress = int((current_time / total_duration) * 100)
            return min(progress, 100)
    
    # Если не смогли извлечь процент — вернём None, чтобы не обновлять прогресс
    return None


def download_video(download_id, url, ffmpeg_location="ffmpeg"):
    """Запускает FFmpeg или yt-dlp для скачивания видео"""
    import yt_dlp
    output_file = f"downloads/{download_id}.mp4"
    logging.info(f"[DOWNLOAD] Starting download for ID: {download_id}")
    logging.info(f"[DOWNLOAD] Output file: {output_file}")
    logging.info(f"[DOWNLOAD] URL: {url}")
    
    try:
        # Определяем тип URL
        if 'youtube.com' in url:
            logging.info("[DOWNLOAD] Using yt-dlp for YouTube")
            ydl_opts = {
                'format': 'best',
                'outtmpl': output_file,
                'progress_hooks': [lambda d: update_progress(d, download_id)],
                'quiet': False,
                'no_warnings': False,
                'extract_flat': False,
                'nocheckcertificate': True,
                'ffmpeg_location': ffmpeg_location, # Передаем ffmpeg_location в yt-dlp
            }
            logging.info(f"[YT-DLP] Using format: best (highest available quality)")
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    downloads[download_id]['status'] = 'processing'
                    ydl.download([url])
                    
                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    downloads[download_id]['status'] = 'completed'
                    downloads[download_id]['file'] = output_file
                    downloads[download_id]['progress'] = 100
                    downloads[download_id]['size'] = os.path.getsize(output_file)
                    logging.info(f"[DOWNLOAD] Successfully downloaded video to {output_file}")
                    return
                else:
                    raise Exception("Failed to download video with yt-dlp")
            except Exception as e:
                logging.error(f"[DOWNLOAD] yt-dlp error: {str(e)}")
                downloads[download_id]['error'] = str(e)
                downloads[download_id]['status'] = 'error'
                return
                
        # Для Vimeo используем специальные параметры
        if 'vimeo.com' in url:
            logging.info("[DOWNLOAD] Using special settings for Vimeo")
            ydl_opts = {
                'format': 'bestvideo+bestaudio/best',
                'outtmpl': output_file,
                'progress_hooks': [lambda d: update_progress(d, download_id)],
                'quiet': False,
                'no_warnings': False,
                'extract_flat': False,
                'nocheckcertificate': True,
                'referer': url,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                },
                'ffmpeg_location': ffmpeg_location
            }
            logging.info(f"[YT-DLP] Using format: bestvideo+bestaudio/best (best video + best audio or best combined quality)")
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    downloads[download_id]['status'] = 'processing'
                    ydl.download([url])
                    
                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    downloads[download_id]['status'] = 'completed'
                    downloads[download_id]['file'] = output_file
                    downloads[download_id]['progress'] = 100
                    downloads[download_id]['size'] = os.path.getsize(output_file)
                    logging.info(f"[DOWNLOAD] Successfully downloaded video to {output_file}")
                    return
                else:
                    raise Exception("Failed to download video with yt-dlp")
            except Exception as e:
                logging.error(f"[DOWNLOAD] yt-dlp error: {str(e)}")
                downloads[download_id]['error'] = str(e)
                downloads[download_id]['status'] = 'error'
                return
                
        # Используем ffmpeg для скачивания m3u8
        logging.info("[DOWNLOAD] Using FFmpeg for download")
        ffmpeg_cmd = [
            'ffmpeg',
            '-headers', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            '-i', url,
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            output_file
        ]
        
        logging.info(f"[DOWNLOAD] FFmpeg command: {' '.join(ffmpeg_cmd)}")
        
        # Запускаем процесс
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        downloads[download_id]['process'] = process
        downloads[download_id]['status'] = 'processing'
        
        # Читаем вывод ffmpeg для обновления прогресса
        while True:
            line = process.stderr.readline()
            if not line:
                break
            logging.debug(f"[FFMPEG] {line.strip()}")
            progress = parse_ffmpeg_progress(line, download_id)
            if progress is not None:
                downloads[download_id]['progress'] = progress
        
        # Ждем завершения процесса
        process.wait()
        
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            downloads[download_id]['status'] = 'completed'
            downloads[download_id]['file'] = output_file
            downloads[download_id]['progress'] = 100
            downloads[download_id]['size'] = os.path.getsize(output_file)
            logging.info(f"[DOWNLOAD] Successfully downloaded video to {output_file}")
            return
        else:
            error_msg = "Failed to download video"
            logging.error(f"[DOWNLOAD] {error_msg}")
            downloads[download_id]['status'] = 'error'
            downloads[download_id]['error'] = error_msg
            return
            
    except Exception as e:
        error_msg = f"Error downloading video: {str(e)}"
        logging.error(f"[DOWNLOAD] {error_msg}")
        downloads[download_id]['status'] = 'error'
        downloads[download_id]['error'] = str(e)
        return


async def start_download(request):
    """Запускает процесс скачивания"""
    try:
        data = await request.json()
        DownloadRequest(**data)
    except ValidationError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    url = data.get('url')
    ffmpeg_location = data.get('ffmpeg_location', 'ffmpeg')
    download_id = str(int(time.time()))
    downloads[download_id] = {
        'status': 'pending',
        'progress': 0
    }
    
    background_tasks = BackgroundTasks()
    background_tasks.add_task(download_video, download_id, url, ffmpeg_location)
    
    response = JSONResponse({'download_id': download_id})
    response.background = background_tasks
    return response


async def progress_stream(request):
    """SSE поток для получения прогресса в реальном времени"""
    download_id = request.path_params['download_id']
    if download_id not in downloads:
        return JSONResponse({'error': 'Invalid download ID'}, status_code=404)
        
    async def generate():
        while True:
            if downloads[download_id]['status'] in ['completed', 'error']:
                break
                
            progress = downloads[download_id].get('progress', 0)
            event = f"id: {download_id}\n"
            event += f"data: {progress}\n\n"
            yield event
            time.sleep(0.1)  # Увеличиваем частоту обновлений
            
    return StreamingResponse(generate(), media_type='text/event-stream')

async def get_progress(request):
    """Возвращает текущий статус и прогресс (для совместимости)"""
    download_id = request.path_params['download_id']
    if download_id not in downloads:
        return JSONResponse({'error': 'Invalid download ID'}, status_code=404)
        
    return JSONResponse({
        'status': downloads[download_id]['status'],
        'progress': downloads[download_id].get('progress', 0),
        'error': downloads[download_id].get('error', '')
    })

async def get_sync_progress(request):
    """Возвращает прогресс для синхронной загрузки"""
    download_id = request.query_params.get('download_id')
    if not download_id or download_id not in downloads:
        return JSONResponse({'error': 'Invalid download ID'}, status_code=404)
        
    return JSONResponse({
        'status': downloads[download_id]['status'],
        'progress': downloads[download_id].get('progress', 0),
        'error': downloads[download_id].get('error', '')
    })


def delete_file_after_delay(file_path, delay):
    """Удаляет файл через указанное время"""
    def delete():
        time.sleep(delay)
        try:
            if os.path.exists(file_path):
                # Закрываем все процессы, использующие файл
                for proc in psutil.process_iter():
                    try:
                        files = proc.open_files()
                        for f in files:
                            if f.path == file_path:
                                proc.terminate()
                                proc.wait()
                                break
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue
                
                # Принудительно закрываем файл
                try:
                    with open(file_path, 'rb') as f:
                        f.close()
                except:
                    pass
                
                # Удаляем файл
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"[DELETE] Successfully deleted file: {file_path}")
                else:
                    print(f"[DELETE] File already deleted: {file_path}")
        except Exception as e:
            print(f"[DELETE ERROR] Failed to delete {file_path}: {str(e)}")
    
    threading.Thread(target=delete, daemon=True).start()


async def download_file(request):
    """Отдает скачанный файл"""
    try:
        download_id = request.path_params['download_id']
        print(f"[DEBUG] Starting download for ID: {download_id}")
        
        if download_id not in downloads:
            print(f"[ERROR] Invalid download ID: {download_id}")
            return JSONResponse({'error': 'Invalid download ID'}, status_code=404)
            
        # Проверяем завершение процесса FFmpeg
        if downloads[download_id]['status'] != 'completed':
            print(f"[ERROR] File not ready for ID: {download_id}")
            return JSONResponse({'error': 'File not ready'}, status_code=400)
            
        file_path = downloads[download_id]['file']
        print(f"[DEBUG] File path: {file_path}")
        
        if not os.path.exists(file_path):
            print(f"[ERROR] File not found: {file_path}")
            return JSONResponse({'error': 'File not found'}, status_code=404)
            
        # Проверяем размер файла
        file_size = os.path.getsize(file_path)
        print(f"[DEBUG] File size: {file_size} bytes")
        
        if file_size == 0:
            print(f"[ERROR] File is empty: {file_path}")
            return JSONResponse({'error': 'File is empty'}, status_code=500)
            
        # Проверяем права доступа
        if not os.access(file_path, os.R_OK):
            print(f"[ERROR] File not readable: {file_path}")
            print(f"[DEBUG] File permissions: {oct(os.stat(file_path).st_mode)[-3:]}")
            return JSONResponse({'error': 'File is not readable'}, status_code=500)
            
        # Упрощенный вызов send_file без ручной установки заголовков
        print(f"[DEBUG] Sending file: {file_path}")
        
        async def send_file_stream():
            try:
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        logging.debug(f"[STREAM] Read chunk of size: {len(chunk)}")
                        yield chunk
            except Exception as e:
                logging.error(f"[STREAM] Error reading file: {str(e)}")
                raise
        
        response = StreamingResponse(
            send_file_stream(),
            media_type='video/mp4',
            headers={
                'Content-Disposition': f'attachment; filename="video_{download_id}.mp4"'
            }
        )
        print(f"Response headers: {response.headers}")
        
        # Запланировать удаление файла через 5 минут
        print(f"[DEBUG] Scheduling file deletion: {file_path}")
        delete_file_after_delay(file_path, 60)
            
        print(f"[INFO] Successfully sent file: {file_path}")
        return response
        
    except Exception as e:
        error_msg = f"Error downloading file {file_path}: {str(e)}"
        print(f"[CRITICAL] {error_msg}")
        # Логируем полный traceback для отладки
        import traceback
        traceback.print_exc()
        return JSONResponse({
            'error': 'File download failed',
            'details': error_msg
        }, status_code=500)


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
    if download_id not in downloads:
        return JSONResponse({'error': 'Invalid download ID'}, status_code=404)
        
    if downloads[download_id]['status'] == 'processing':
        process = downloads[download_id]['process']
        process.terminate()
        
    if os.path.exists(f"downloads/{download_id}.mp4"):
        os.remove(f"downloads/{download_id}.mp4")
        
    del downloads[download_id]
    
    return JSONResponse({'status': 'cancelled'})


async def homepage(request):
    return templates.TemplateResponse(request, "index.html")

# Определяем маршруты после всех функций
routes = [
    Mount('/static', StaticFiles(directory='static'), name='static'),
    Route("/", endpoint=homepage),
    Route("/index.html", endpoint=homepage),
    Route("/download", endpoint=start_download, methods=["POST"]),
    Route("/progress_stream/{download_id}", endpoint=progress_stream),
    Route("/progress/{download_id}", endpoint=get_progress),
    Route("/download_file/{download_id}", endpoint=download_file),
    Route("/log_error", endpoint=log_error, methods=["POST"]),
    Route("/cancel/{download_id}", endpoint=cancel_download, methods=["POST"]),
    Route("/sync_progress", endpoint=get_sync_progress, methods=["GET"]),
]

# Создаем приложение с маршрутами
app = Starlette(
    debug=True,
    middleware=middleware,
    routes=routes
)

if __name__ == '__main__':
    # Настройка логирования
    import logging
    logging.basicConfig(
        filename='app.log',
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
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
    
    # Запуск приложения только на localhost:8080
    uvicorn.run("app:app", host="127.0.0.1", port=8080, reload=False)
