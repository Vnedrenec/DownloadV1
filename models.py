from pydantic import BaseModel, field_validator, Field
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime

class DownloadStatus(str, Enum):
    INITIALIZING = "initializing"  # Начальная инициализация
    PENDING = "pending"            # Ожидание начала загрузки
    DOWNLOADING = "downloading"    # Процесс загрузки
    CONVERTING = "converting"      # Конвертация видео
    PROCESSING = "processing"      # Обработка видео
    COMPLETED = "completed"        # Загрузка завершена
    ERROR = "error"               # Ошибка
    EXPIRED = "expired"           # Загрузка устарела
    RETRYING = "retrying"         # Повторная попытка
    CANCELLED = "cancelled"       # Отменено пользователем

class DownloadRequest(BaseModel):
    """Модель запроса на скачивание"""
    url: str = Field(..., description="URL для загрузки")
    format: Optional[str] = Field(None, description="Формат выходного файла")
    quality: Optional[str] = Field(None, description="Качество видео")
    download_speed: Optional[str] = Field(None, description="Ограничение скорости загрузки")
    ffmpeg_location: Optional[str] = Field(None, description="Путь к FFmpeg")
    outputPath: Optional[str] = Field(None, description="Путь для сохранения файла")

    @field_validator('url')
    def validate_url(cls, value):
        """Валидация URL"""
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Некорректный URL")
        if parsed.scheme not in ["http", "https"]:
            raise ValueError("URL должен использовать протокол HTTP(S)")
        return value

    @field_validator('format')
    def validate_format(cls, value):
        """Валидация формата видео"""
        if value is None:
            return value
        valid_formats = ["mp4", "mkv", "webm", "m4a", "mp3"]
        if value not in valid_formats:
            raise ValueError(f"Некорректный формат. Допустимые значения: {', '.join(valid_formats)}")
        return value

    @field_validator('quality')
    def validate_quality(cls, value):
        """Валидация качества видео"""
        if value is None:
            return value
        valid_qualities = ["144p", "240p", "360p", "480p", "720p", "1080p", "1440p", "2160p"]
        if value not in valid_qualities:
            raise ValueError(f"Некорректное качество. Допустимые значения: {', '.join(valid_qualities)}")
        return value

    @field_validator('download_speed')
    def validate_download_speed(cls, value):
        """Валидация скорости загрузки"""
        if value is None:
            return value
        if not value.endswith(('K', 'M', 'G')):
            raise ValueError("Скорость загрузки должна заканчиваться на K, M или G (например, '1M')")
        try:
            float(value[:-1])
        except ValueError:
            raise ValueError("Некорректный формат скорости загрузки")
        return value

class LogErrorRequest(BaseModel):
    """Модель запроса на логирование ошибки"""
    error: str = Field(..., description="Текст ошибки", max_length=1000)
    downloadId: Optional[str] = Field(None, description="ID загрузки")
    stackTrace: Optional[str] = Field(None, description="Стек вызовов")

    @field_validator('error')
    def validate_error(cls, value):
        """Валидация сообщения об ошибке"""
        if not value:
            raise ValueError("Сообщение об ошибке не может быть пустым")
        if len(value) > 1000:
            raise ValueError("Сообщение об ошибке слишком длинное (максимум 1000 символов)")
        return value

class DownloadState(BaseModel):
    """Модель состояния загрузки"""
    id: str = Field(..., description="Уникальный идентификатор загрузки")
    status: DownloadStatus = Field(..., description="Текущий статус загрузки")
    url: str = Field(..., description="URL источника")
    progress: float = Field(0, description="Прогресс загрузки", ge=0, le=100)
    error: Optional[str] = Field(None, description="Сообщение об ошибке")
    created_at: datetime = Field(default_factory=datetime.now, description="Время создания")
    updated_at: datetime = Field(default_factory=datetime.now, description="Время последнего обновления")
    file_path: Optional[str] = Field(None, description="Путь к загруженному файлу")
    file_size: Optional[int] = Field(None, description="Размер файла в байтах")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные метаданные")
    retry_count: int = Field(0, description="Количество попыток загрузки")
    max_retries: int = Field(3, description="Максимальное количество попыток")

    def update_status(self, new_status: DownloadStatus, error: Optional[str] = None):
        """Обновление статуса загрузки"""
        self.status = new_status
        if error:
            self.error = error
        self.updated_at = datetime.now()

    def increment_retry(self) -> bool:
        """Увеличение счетчика попыток. Возвращает True если еще можно повторить."""
        self.retry_count += 1
        return self.retry_count <= self.max_retries

    def is_expired(self, max_age_hours: int = 24) -> bool:
        """Проверка на устаревание загрузки"""
        age = datetime.now() - self.created_at
        return age.total_seconds() > max_age_hours * 3600

    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь для сохранения"""
        return {
            "id": self.id,
            "status": self.status.value,
            "url": self.url,
            "progress": self.progress,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "file_path": self.file_path,
            "file_size": self.file_size,
            "metadata": self.metadata,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DownloadState":
        """Создание объекта из словаря"""
        # Преобразуем строковые даты в datetime
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        # Преобразуем строковый статус в enum
        if isinstance(data.get("status"), str):
            data["status"] = DownloadStatus(data["status"])
        return cls(**data)

class StateStorageItem(BaseModel):
    """Модель элемента хранилища состояний"""
    key: str = Field(..., description="Ключ элемента")
    value: Any = Field(..., description="Значение элемента")
    timestamp: datetime = Field(default_factory=datetime.now, description="Время создания/обновления")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные метаданные")

    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь для сохранения"""
        return {
            "key": self.key,
            "value": self.value,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StateStorageItem":
        """Создание объекта из словаря"""
        if isinstance(data.get("timestamp"), str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)
