FROM python:3.14-slim

# uv is the package manager for this project (pyproject.toml + uv.lock).
# uv 0.9.30
COPY --from=ghcr.io/astral-sh/uv@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Create the runtime user and the volume mountpoint; the entrypoint re-chowns
# the bind mount, then drops privileges before uv starts the consumer.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && chmod 755 /usr/local/bin/docker-entrypoint.sh \
    && chown -R app:app /app \
    && mkdir -p /state \
    && chown app:app /state

ENV HOME=/home/app \
    UV_CACHE_DIR=/tmp/uv-cache
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uv", "run", "python", "-m", "src.consumer"]
