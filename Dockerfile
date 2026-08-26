# --- Frontend build stage (React + Vite SPA) -------------------------------
FROM node:20-alpine AS node
WORKDIR /app
COPY web/package*.json ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build

# --- Python runtime stage ----------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is needed by the compose healthcheck; postgresql-client for psql/pg_isready.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Compiled SPA from the node stage lands in /app/web/dist (bot/web/app.py
# serves it when present, falling back to the legacy login page otherwise).
COPY --from=node /app/dist /app/web/dist

# Run as a non-root user; logging falls back to stderr if logs/ isn't writable.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["python", "-m", "bot"]
