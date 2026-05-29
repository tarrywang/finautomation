# Build the fapiao app image using the official uv base.
# Self-contained, supports both x86_64 and arm64.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Layer cache: lock + manifest first
COPY pyproject.toml uv.lock README.md ./
COPY tax_auto/ ./tax_auto/

RUN uv sync --frozen --no-dev


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    TZ=Asia/Shanghai

RUN useradd -m -u 1000 -s /bin/bash app \
    && apt-get update \
    && apt-get install -y --no-install-recommends libpq5 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/tax_auto /app/tax_auto
COPY alembic.ini /app/
COPY scripts/ /app/scripts/

RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "tax_auto.web.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
