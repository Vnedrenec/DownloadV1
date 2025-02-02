FROM python:3.9.18-slim

# Установка системных зависимостей и копирование ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    && mkdir -p /tmp/ffmpeg \
    && cp $(which ffmpeg) /tmp/ffmpeg/ \
    && apt-get remove -y ffmpeg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем только requirements.txt сначала для кэширования слоя с зависимостями
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf ~/.cache/pip/*

# Копируем ffmpeg в локальную директорию bin
RUN mkdir -p bin
COPY --from=0 /tmp/ffmpeg/ffmpeg bin/

# Копируем остальные файлы проекта
COPY . .

# Создаем необходимые директории
RUN mkdir -p downloads logs

# Настройка переменных окружения
ENV HOST=0.0.0.0
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV WORKERS=4
ENV TIMEOUT=120
ENV GRACEFUL_TIMEOUT=30

# Запуск Gunicorn с воркерами uvicorn
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:8080", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info"]