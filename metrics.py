import time
import logging
import functools
from typing import Optional

def measure_time(threshold_ms: Optional[int] = 1000):
    """
    Декоратор для измерения времени выполнения функции
    
    Args:
        threshold_ms: Порог в миллисекундах, при превышении которого будет залогировано предупреждение
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            except TypeError as e:
                logging.error(f"Invalid arguments for {func.__name__}: {e}")
                raise
            except Exception as e:
                logging.error(f"An error occurred in {func.__name__}: {e}")
                raise
            finally:
                execution_time = (time.time() - start_time) * 1000
                if execution_time > threshold_ms:
                    logging.warning(
                        f"[PERF] {func.__name__} took {execution_time:.2f}ms "
                        f"(threshold: {threshold_ms}ms)"
                    )
                else:
                    logging.debug(
                        f"[PERF] {func.__name__} took {execution_time:.2f}ms"
                    )
        return wrapper
    return decorator
