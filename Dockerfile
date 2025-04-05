# Этап сборки
FROM python:3.11-slim as builder

# Устанавливаем необходимые пакеты для сборки
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем и устанавливляем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --upgrade yt-dlp \
    && pip install --no-cache-dir gunicorn \
    && pip install --no-cache-dir aiohttp==3.9.3 \
    && rm -rf ~/.cache/pip/*

# Этап финального образа
FROM python:3.11-slim

# Устанавливаем только необходимые runtime зависимости
RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir aiohttp==3.9.3 \
    && pip install --no-cache-dir fastapi-utils==0.2.1 \
    && pip install --no-cache-dir tasks==2.8.0

WORKDIR /app

# Копируем установленные пакеты из builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/gunicorn /usr/local/bin/gunicorn
COPY --from=builder /usr/local/bin/yt-dlp /usr/local/bin/yt-dlp

# Копируем код приложения
COPY . .

# Create directories and set permissions
RUN addgroup --system downloads && \
    adduser nobody downloads && \
    mkdir -p /app/downloads && \
    chown nobody:downloads /app/downloads && \
    chmod 775 /app/downloads && \
    chmod +x /app/start.sh

# Настройки окружения
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Переключаемся на непривилегированного пользователя
USER nobody

# Запускаем приложение через gunicorn
CMD ["/app/start.sh"]