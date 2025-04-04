#!/bin/sh

# Настройки для оптимальной производительности
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# Запускаем с настройками для production
exec gunicorn app:app \
    --bind 0.0.0.0:${PORT:-8080} \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers ${GUNICORN_WORKERS:-2} \
    --timeout ${GUNICORN_TIMEOUT:-120} \
    --max-requests ${GUNICORN_MAX_REQUESTS:-1000} \
    --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-100} \
    --access-logfile - \
    --error-logfile - \
    --log-level ${LOG_LEVEL:-info}
