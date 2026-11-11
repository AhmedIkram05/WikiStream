FROM python:3.14-slim

# uv is the package manager for this project (pyproject.toml + uv.lock).
# uv 0.9.30
COPY --from=ghcr.io/astral-sh/uv@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src

CMD ["uv", "run", "python", "-m", "src.consumer"]
