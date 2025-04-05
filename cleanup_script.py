#!/usr/bin/env python3
import os
import time
import logging
import sys

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

def cleanup_downloads(downloads_dir, max_age_hours=0.5):
    """Очистка старых загрузок"""
    try:
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600  # Переводим часы в секунды
        
        logging.info(f"Начинаем очистку файлов в {downloads_dir}, старше {max_age_hours} часов")
        
        if not os.path.exists(downloads_dir):
            logging.error(f"Директория {downloads_dir} не существует")
            return
        
        files = os.listdir(downloads_dir)
        logging.info(f"Найдено {len(files)} файлов")
        
        deleted_count = 0
        deleted_size = 0
        
        for filename in files:
            if filename.endswith('.json') or filename.startswith('.') or filename == 'logs' or filename == 'lost+found':
                continue
                
            file_path = os.path.join(downloads_dir, filename)
            try:
                if not os.path.isfile(file_path):
                    continue
                    
                stat = os.stat(file_path)
                file_size = stat.st_size
                file_age_seconds = current_time - stat.st_mtime
                file_age_minutes = file_age_seconds / 60
                
                logging.info(f"Файл: {filename}, возраст: {file_age_minutes:.1f} минут, размер: {file_size / 1024 / 1024:.1f} МБ")
                
                # Удаляем все файлы, независимо от возраста
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    deleted_size += file_size
                    logging.info(f"Удален файл: {filename} (возраст: {file_age_minutes:.1f} минут, размер: {file_size / 1024 / 1024:.1f} МБ)")
                except Exception as e:
                    logging.error(f"Ошибка при удалении файла {filename}: {str(e)}")
                    
            except Exception as e:
                logging.error(f"Ошибка при обработке файла {filename}: {str(e)}")
                
        logging.info(f"Очистка завершена. Удалено {deleted_count} файлов, освобождено {deleted_size / 1024 / 1024:.1f} МБ")
        
    except Exception as e:
        logging.error(f"Ошибка при очистке загрузок: {str(e)}")

if __name__ == "__main__":
    downloads_dir = "/app/downloads"
    if len(sys.argv) > 1:
        downloads_dir = sys.argv[1]
        
    cleanup_downloads(downloads_dir)
