# Stage 1: Build Next.js Static Frontend
FROM node:18-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend ./
RUN npm run build

# Stage 2: Production Python Backend serving Static Frontend
FROM python:3.11-slim
WORKDIR /app

# GitPython invokes the Git executable when importing repositories.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install backend dependencies
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend application source
COPY backend ./backend

# Copy built static frontend from Stage 1
COPY --from=frontend-builder /app/frontend/out ./frontend/out

# Create persistent data directory and unprivileged application user
RUN useradd -m -u 1001 -s /bin/bash appuser \
    && mkdir -p /var/data \
    && chown -R appuser:appuser /app /var/data

USER appuser

VOLUME ["/var/data"]

ENV PORT=8080 \
    DATA_DIR=/var/data
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT', '8080') + '/api/health', timeout=5)" || exit 1

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
