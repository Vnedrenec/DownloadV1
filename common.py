"""
Модуль для общих зависимостей и функций, используемых в других модулях.
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel

class CommonState(BaseModel):
    """Общее состояние, используемое в state_storage и metrics."""
    status: str
    progress: float = 0.0
    error: Optional[str] = None
    log: Optional[str] = None
