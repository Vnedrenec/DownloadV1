import asyncio
import httpx
import json
import logging
import sys

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

async def test_download():
    # URL для тестирования (короткое видео)
    test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # Первое видео на YouTube
    timeout = httpx.Timeout(30.0, connect=30.0)  # Увеличиваем таймаут
    
    # Создаем клиент
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            # Отправляем запрос на скачивание
            logger.info(f"Отправляем запрос на скачивание видео: {test_url}")
            response = await client.post(
                "http://localhost:8000/api/download",
                json={"url": test_url}
            )
            response.raise_for_status()
            data = response.json()
            download_id = data.get("download_id")
            
            if not download_id:
                logger.error("Не получен download_id")
                return
                
            logger.info(f"Получен download_id: {download_id}")
            
            # Подключаемся к SSE для получения обновлений
            logger.info(f"Подключаемся к SSE для {download_id}...")
            async with client.stream('GET', f'http://localhost:8000/api/progress_stream/{download_id}') as response:
                response.raise_for_status()
                logger.info("SSE соединение установлено")
                
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        try:
                            event_data = json.loads(line[6:])
                            status = event_data.get('status', 'unknown')
                            progress = event_data.get('progress', 0)
                            
                            logger.info(f"Статус: {status}, Прогресс: {progress}%")
                            
                            if status == 'error':
                                error = event_data.get('error', 'Неизвестная ошибка')
                                logger.error(f"Ошибка загрузки: {error}")
                                if 'log' in event_data:
                                    logger.error(f"Лог ошибки: {event_data['log']}")
                                return
                                
                            if status == 'completed':
                                file_path = event_data.get('file_path', '')
                                logger.info(f"Загрузка успешно завершена! Файл: {file_path}")
                                return
                                
                        except json.JSONDecodeError as e:
                            logger.error(f"Ошибка декодирования JSON: {line}")
                            logger.error(f"Детали ошибки: {str(e)}")
                            
        except Exception as e:
            logger.error(f"Ошибка: {str(e)}")
            logger.exception("Полный стек ошибки:")

if __name__ == "__main__":
    try:
        asyncio.run(test_download())
    except KeyboardInterrupt:
        logger.info("Тест остановлен пользователем")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {str(e)}")
        logger.exception("Полный стек ошибки:")
