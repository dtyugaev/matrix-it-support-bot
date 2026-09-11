# syntax=docker/dockerfile:1
FROM python:3.13-slim

LABEL org.opencontainers.image.title="matrix-it-support-bot" \
      org.opencontainers.image.description="Бот Службы технической поддержки для Matrix Synapse с интеграцией Jira" \
      org.opencontainers.image.version="1.0.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

WORKDIR /app

# libpq5 нужен драйверу psycopg2, ca-certificates — для HTTPS к Synapse и Jira
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py
COPY src /app/src
COPY tests /app/tests

RUN useradd -r -u 10001 -d /app matrixbot \
    && mkdir -p /app/storage \
    && chown -R matrixbot:matrixbot /app
USER matrixbot

VOLUME ["/app/storage"]

CMD ["python3", "main.py", "--config", "/app/config.yaml"]
