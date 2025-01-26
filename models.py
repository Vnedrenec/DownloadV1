from pydantic import BaseModel, HttpUrl
from typing import Optional

class DownloadRequest(BaseModel):
    url: HttpUrl
    ffmpeg_location: Optional[str] = 'ffmpeg'

class LogErrorRequest(BaseModel):
    error: str
    downloadId: Optional[str] = None
