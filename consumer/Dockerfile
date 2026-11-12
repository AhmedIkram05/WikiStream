FROM python:3.14-slim

# uv is the package manager for this project (pyproject.toml + uv.lock).
# uv 0.9.30
COPY --from=ghcr.io/astral-sh/uv@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
# Create the runtime user and the volume mountpoint. The compose init service
# chowns the bind mount before this container starts; the app itself runs as
# uid 10001 from the first instruction.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && chown -R app:app /app \
    && mkdir -p /state \
    && chown app:app /state

ENV HOME=/home/app \
    UV_CACHE_DIR=/tmp/uv-cache
USER app
CMD ["uv", "run", "python", "-m", "src.consumer"]
