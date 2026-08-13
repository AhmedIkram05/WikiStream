# GX 0.18.22 pins pandas<2.2.0 / numpy<2.0.0 which have no cp313 wheels — this
# image MUST stay on Python 3.12 (mirrored by the requires-python upper bound).
FROM python:3.12-slim

# uv is the package manager for this project (pyproject.toml + uv.lock).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

COPY gx/pyproject.toml gx/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY gx/ ./gx/

# Exec form: PID 1 is /bin/uv so SIGTERM (timer/stop) reaches the process.
CMD ["uv", "run", "python", "/app/gx/suite.py"]
