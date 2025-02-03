# Используем многоэтапную сборку для ffmpeg
FROM python:3.9.18-slim as builder

# Устанавливаем ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && mkdir -p /tmp/ffmpeg \
    && cp $(which ffmpeg) /tmp/ffmpeg/ \
    && chmod +x /tmp/ffmpeg/ffmpeg

# Финальный образ
FROM python:3.9.18-slim

# Установка aria2
RUN apt-get update && apt-get install -y \
    aria2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем только requirements.txt сначала для кэширования слоя с зависимостями
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --upgrade yt-dlp \
    && rm -rf ~/.cache/pip/*

# Копируем ffmpeg из builder и устанавливаем права
COPY --from=builder /tmp/ffmpeg/ffmpeg /usr/local/bin/
RUN chmod +x /usr/local/bin/ffmpeg

# Копируем остальные файлы проекта
COPY . .

# Создаем необходимые директории
RUN mkdir -p downloads logs

# Настройка переменных окружения для Gunicorn
ENV WORKERS=4
ENV TIMEOUT=300
ENV GRACEFUL_TIMEOUT=30

# Запускаем приложение
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "--timeout", "300", "--worker-class", "uvicorn.workers.UvicornWorker", "app:app"]