from pydantic import BaseModel, validator, HttpUrl
from typing import Optional
from urllib.parse import urlparse

class DownloadRequest(BaseModel):
    url: str
    ffmpeg_location: Optional[str] = 'ffmpeg'
    outputPath: Optional[str] = None
    format: Optional[str] = None

    @validator('url')
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://')):
            raise ValueError('URL должен начинаться с http:// или https://')
        return v

class LogErrorRequest(BaseModel):
    error: str
    downloadId: Optional[str] = None
    stackTrace: Optional[str] = None

    @validator('error')
    def validate_error(cls, v):
        if not v:
            raise ValueError('Сообщение об ошибке не может быть пустым')
        if len(v) > 1000:
            raise ValueError('Сообщение об ошибке слишком длинное')
        return v
