# The live simulation demo: the real backend with a fake Twilio and template
# content (docs/architecture.md, section 9). It needs no database server, no API
# keys and no secrets, and it cannot text or call anyone.
#
# Build from the backend directory:   docker build -t disaster-demo backend
# Run:                                docker run -p 8000:8000 disaster-demo
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so editing the app does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY app ./app

# SQLite in the container's own disk. It is thrown away on every redeploy, which
# is fine: the app migrates and re-seeds an empty database at startup.
ENV SIMULATION_MODE=true \
    DATABASE_URL=sqlite+aiosqlite:////tmp/demo.db \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

EXPOSE 8000

# One worker on purpose: the simulator, the scheduler and the WebSocket registry
# all live in this process's memory.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
