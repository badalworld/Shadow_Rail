# ── Shadow Rail — production image ────────────────────────────────────────────
# Multi-stage: the dashboard is built with Node and its bundle is copied into the
# Python runtime stage, which serves API + UI from a single origin/port.
#
#   docker build -t shadow-rail .
#   docker run -d --name shadow-rail -p 127.0.0.1:8080:8080 \
#              -e SHADOW_RAIL_API_TOKEN="$(openssl rand -hex 32)" \
#              -v shadow-rail-data:/data shadow-rail
#
# The engine is single-process by design (in-process state, one SQLite writer):
# never scale this image above one replica per data volume.

# ── stage 1: dashboard bundle ────────────────────────────────────────────────
FROM node:22-alpine AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite writes to ../backend/web, which is exactly what the runtime stage copies
RUN mkdir -p /src/backend && npm run build

# ── stage 2: engine + API + dashboard ────────────────────────────────────────
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SHADOW_RAIL_DATA_DIR=/data \
    SHADOW_RAIL_HOST=0.0.0.0 \
    SHADOW_RAIL_PORT=8080
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY scripts /app/scripts
COPY --from=ui /src/backend/web ./web

# the SQLite journal, encrypted config and Fernet key live on the volume
RUN mkdir -p /data && useradd --system --uid 10001 --home /app shadow && \
    chown -R shadow:shadow /data /app
USER shadow
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "app.main"]
