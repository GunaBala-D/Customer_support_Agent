# Build context: repository root (see docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Layout mirrors the repo so settings.REPO_ROOT (= /app) resolves data/ and evaluation/ the same way.
COPY backend backend
COPY evaluation evaluation
COPY scripts scripts
COPY data/documents data/documents

RUN useradd --create-home appuser && mkdir -p data/chroma data/state && chown -R appuser /app
USER appuser

WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
