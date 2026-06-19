# FitLit — single container: FastAPI API + the background 10s scheduler.
#
# Build for the platform Azure runs (linux/amd64). On an Apple-Silicon Mac:
#   docker buildx build --platform linux/amd64 -t fitlit .
# See README ("Deploy to Azure Container Registry") for the full ACR flow.
FROM python:3.11-slim

# uv for fast, reproducible installs straight from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (cached layer) from the lockfile only.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the source, and finish the install.
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    # Persist SQLite DBs + scheduler state here; mount a volume / Azure Files
    # share at this path (the container filesystem is ephemeral).
    FITLIT_DATA_DIR=/app/data

# Run as a non-root user; give it ownership of the writable data directory.
RUN mkdir -p /app/data \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data
USER appuser

VOLUME ["/app/data"]
EXPOSE 8000

# Docker-level liveness probe (Azure adds its own ingress probe too).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://localhost:'+os.environ.get('PORT','8000')+'/health')"

# `exec` so uvicorn becomes PID 1 (clean SIGTERM on Azure scale-down) while the
# shell still expands ${HOST}/${PORT} — Azure injects the target port.
CMD ["sh", "-c", "exec uvicorn fitlit.server:app --host ${HOST} --port ${PORT}"]
