FROM python:3.13-slim

# uv is the package manager for this project (pyproject.toml + uv.lock).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src

CMD ["uv", "run", "python", "-m", "src.consumer"]
