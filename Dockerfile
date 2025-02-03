FROM python:3.9.18-slim as builder

# Установка системных зависимостей и ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && mkdir -p /tmp/ffmpeg \
    && cp $(which ffmpeg) /tmp/ffmpeg/ \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.9.18-slim

# Установка aria2 и tor
RUN apt-get update && apt-get install -y \
    aria2 \
    tor \
    && rm -rf /var/lib/apt/lists/*

# Копируем конфигурацию Tor
RUN echo "SocksPort 0.0.0.0:9050" > /etc/tor/torrc && \
    echo "Log notice stdout" >> /etc/tor/torrc

WORKDIR /app

# Копируем ffmpeg из builder
COPY --from=builder /tmp/ffmpeg/ffmpeg /usr/local/bin/

# Копируем только requirements.txt сначала для кэширования слоя с зависимостями
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf ~/.cache/pip/*

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

# Запускаем Tor и Gunicorn с воркерами uvicorn
CMD service tor start && gunicorn app:app --bind 0.0.0.0:8080 --worker-class uvicorn.workers.UvicornWorker --workers 4 --timeout 120 --graceful-timeout 30 --access-logfile - --error-logfile - --log-level info