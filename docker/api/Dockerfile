# Stage 1: Build React frontend SPA
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime & API server
FROM python:3.12-slim

# ghostscript reads PostScript/PCL jobs; tesseract is the OCR tier. Both are optional at
# runtime — /api/v1/health reports which are present.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ghostscript tesseract-ocr tesseract-ocr-eng cups-client curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir . && pip install --no-cache-dir pytest reportlab

COPY rules/ ./rules/
COPY config/ ./config/
COPY tests/ ./tests/
COPY --from=frontend-builder /build/dist /app/frontend/dist

ENV JANUS_PRINT_RULES_DIR=/app/rules \
    JANUS_PRINT_PRINTERS_CONFIG=/app/config/printers.yaml \
    JANUS_PRINT_ARCHIVE_PATH=/var/lib/janus-print/archive \
    PYTHONUNBUFFERED=1

RUN mkdir -p /var/lib/janus-print/archive

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s \
  CMD curl -fsS http://localhost:8080/api/v1/health || exit 1

CMD ["uvicorn", "janusprint.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
