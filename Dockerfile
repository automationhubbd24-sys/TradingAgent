FROM node:22-slim AS frontend-builder

WORKDIR /app/web/frontend
COPY web/frontend/package*.json ./
RUN npm ci
COPY web/frontend ./
RUN npm run build

FROM python:3.12-slim AS python-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY tradingagents ./tradingagents
COPY cli ./cli
COPY web/backend ./web/backend
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    TRADINGAGENTS_CACHE_DIR=/home/appuser/.tradingagents/cache \
    TRADINGAGENTS_RESULTS_DIR=/home/appuser/.tradingagents/results \
    TRADINGAGENTS_MEMORY_LOG_PATH=/home/appuser/.tradingagents/memory/trading_memory.md

COPY --from=python-builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents/cache \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents/results \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents/memory \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/app

WORKDIR /home/appuser/app
COPY --from=python-builder --chown=appuser:appuser /build ./
COPY --from=frontend-builder --chown=appuser:appuser /app/web/frontend/dist ./web/frontend/dist

USER appuser
EXPOSE 8000

CMD ["sh", "-c", "uvicorn web.backend.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
