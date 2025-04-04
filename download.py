import os
import httpx
import asyncio
import logging
from typing import Optional, Tuple, Dict, Any
from datetime import datetime
from urllib.parse import urlparse
from models import DownloadRequest, DownloadState, DownloadStatus

class DownloadError(Exception):
    """Базовый класс для ошибок загрузки"""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

class NetworkError(DownloadError):
    """Ошибка сети"""
    pass

class ValidationError(DownloadError):
    """Ошибка валидации"""
    pass

class ProcessingError(DownloadError):
    """Ошибка обработки"""
    pass

async def validate_url(url: str) -> Tuple[bool, Optional[str]]:
    """Проверка доступности URL"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.head(url, follow_redirects=True, timeout=10.0)
            response.raise_for_status()
            return True, None
    except httpx.HTTPError as e:
        return False, f"Ошибка HTTP при проверке URL: {str(e)}"
    except Exception as e:
        return False, f"Ошибка при проверке URL: {str(e)}"

async def prepare_download(request: DownloadRequest) -> DownloadState:
    """Подготовка загрузки"""
    # Проверяем URL
    is_valid, error = await validate_url(request.url)
    if not is_valid:
        raise ValidationError(error or "Недействительный URL")

    # Создаем начальное состояние
    state = DownloadState(
        id=os.urandom(16).hex(),
        status=DownloadStatus.INITIALIZING,
        url=request.url,
        metadata={
            "format": request.format,
            "quality": request.quality,
            "download_speed": request.download_speed,
            "output_path": request.outputPath
        }
    )
    
    return state

async def download_video(request: DownloadRequest, state: DownloadState) -> DownloadState:
    """Загрузка видео с обработкой ошибок и повторными попытками"""
    async def _attempt_download() -> httpx.Response:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "http://localhost:8000/api/download",
                    json={
                        "url": request.url,
                        "format": request.format,
                        "quality": request.quality,
                        "download_speed": request.download_speed,
                        "ffmpeg_location": request.ffmpeg_location,
                        "outputPath": request.outputPath
                    },
                    timeout=30.0
                )
                response.raise_for_status()
                return response
        except httpx.TimeoutException:
            raise NetworkError("Превышено время ожидания запроса")
        except httpx.HTTPError as e:
            if e.response.status_code >= 500:
                raise NetworkError(f"Ошибка сервера: {str(e)}", e.response.status_code)
            else:
                raise ValidationError(f"Ошибка запроса: {str(e)}", e.response.status_code)
        except Exception as e:
            raise ProcessingError(f"Неожиданная ошибка: {str(e)}")

    while True:
        try:
            # Обновляем статус
            state.update_status(DownloadStatus.DOWNLOADING)
            
            # Пытаемся загрузить
            response = await _attempt_download()
            
            # Обрабатываем успешный ответ
            response_data = response.json()
            state.file_path = response_data.get("file_path")
            state.file_size = response_data.get("file_size")
            state.progress = 100
            state.update_status(DownloadStatus.COMPLETED)
            return state
            
        except (NetworkError, ProcessingError) as e:
            # Для этих ошибок пробуем повторить
            if state.increment_retry():
                logging.warning(f"[DOWNLOAD] Ошибка загрузки ({state.retry_count}/{state.max_retries}): {str(e)}")
                state.update_status(DownloadStatus.RETRYING, str(e))
                # Ждем перед повторной попыткой
                await asyncio.sleep(2 ** state.retry_count)  # Экспоненциальная задержка
                continue
            else:
                # Превышено максимальное количество попыток
                state.update_status(DownloadStatus.ERROR, f"Превышено количество попыток: {str(e)}")
                raise
                
        except ValidationError as e:
            # Ошибки валидации не повторяем
            state.update_status(DownloadStatus.ERROR, str(e))
            raise
            
        except Exception as e:
            # Неожиданные ошибки
            logging.error(f"[DOWNLOAD] Неожиданная ошибка: {str(e)}", exc_info=True)
            state.update_status(DownloadStatus.ERROR, f"Внутренняя ошибка: {str(e)}")
            raise ProcessingError(str(e))

async def cancel_download(state: DownloadState) -> DownloadState:
    """Отмена загрузки"""
    if state.status in [DownloadStatus.COMPLETED, DownloadStatus.ERROR, DownloadStatus.EXPIRED]:
        raise ValidationError("Невозможно отменить завершенную или неудачную загрузку")
        
    state.update_status(DownloadStatus.CANCELLED)
    return state
