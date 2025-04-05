import asyncio
import logging
from typing import Callable, Awaitable, Optional
from fastapi import FastAPI

logger = logging.getLogger(__name__)

def repeat_every(
    seconds: float,
    wait_first: bool = False,
    logger: Optional[logging.Logger] = None,
    raise_exceptions: bool = False,
    max_repetitions: Optional[int] = None
):
    """
    Декоратор, который запускает декорированную функцию повторно каждые `seconds` секунд.
    
    Параметры:
    - seconds: Количество секунд между повторными запусками
    - wait_first: Если True, ждет `seconds` секунд перед первым запуском
    - logger: Логгер для записи ошибок
    - raise_exceptions: Если True, пробрасывает исключения, иначе логирует их
    - max_repetitions: Максимальное количество повторений, None для бесконечного количества
    """
    def decorator(func: Callable[..., Awaitable]):
        is_coroutine = asyncio.iscoroutinefunction(func)
        if not is_coroutine:
            raise TypeError(f"Функция '{func.__name__}' должна быть корутиной")
        
        task_name = func.__name__
        
        async def wrapped():
            repetitions = 0
            
            while max_repetitions is None or repetitions < max_repetitions:
                if wait_first:
                    await asyncio.sleep(seconds)
                
                try:
                    await func()
                except Exception as e:
                    log_msg = f"Ошибка в задаче {task_name}: {str(e)}"
                    if logger:
                        logger.error(log_msg)
                    else:
                        logging.error(log_msg)
                    
                    if raise_exceptions:
                        raise
                
                repetitions += 1
                
                if not wait_first:
                    await asyncio.sleep(seconds)
        
        @func.register
        def _startup_event(app: FastAPI):
            """
            Регистрирует задачу для запуска при старте приложения
            """
            @app.on_event("startup")
            async def startup():
                asyncio.create_task(wrapped())
            
            return func
        
        return _startup_event
    
    return decorator
