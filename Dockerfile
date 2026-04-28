# Two-stage build: uv builds the venv from the lockfile, runtime
# image is python:3.12-slim with the venv copied in. uvicorn runs
# as PID 1 so SIGTERM reaches Python promptly.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-default-groups

COPY src ./src
RUN uv sync --frozen --no-default-groups


FROM python:3.12-slim AS runtime

RUN groupadd --system --gid 1000 hummingbird \
 && useradd --system --uid 1000 --gid 1000 \
       --home /app --shell /sbin/nologin hummingbird

WORKDIR /app

COPY --from=builder --chown=hummingbird:hummingbird /app /app

# Create writable dirs the runtime expects (bookshelves, sessions,
# bookmarks, audio cache). Operators should bind-mount these so
# state survives container rebuilds.
RUN mkdir -p /app/data /app/cache \
 && chown -R hummingbird:hummingbird /app/data /app/cache

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER hummingbird
EXPOSE 8000

CMD ["uvicorn", "hummingbird.main:app", "--host", "0.0.0.0", "--port", "8000"]
