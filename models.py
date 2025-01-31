from pydantic import BaseModel
from typing import Optional

class DownloadRequest(BaseModel):
    url: str
    ffmpeg_location: Optional[str] = 'ffmpeg'

class LogErrorRequest(BaseModel):
    error: str
    downloadId: Optional[str] = None
