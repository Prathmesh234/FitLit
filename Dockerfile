# FitLit — single container that runs the API + the background scheduler.
FROM python:3.11-slim

# uv for fast, reproducible installs from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (cached layer) using the lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the source.
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Token + tuning come from the environment at run time (see .env.example):
#   docker run -e GOOGLE_HEALTH_ACCESS_TOKEN=... -p 8000:8000 fitlit
EXPOSE 8000

# Container healthcheck hits the liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# One process: FastAPI serves HTTP and runs the 10s scheduler in a thread.
CMD ["uvicorn", "fitlit.server:app", "--host", "0.0.0.0", "--port", "8000"]
