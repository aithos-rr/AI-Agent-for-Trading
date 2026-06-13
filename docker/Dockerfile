FROM python:3.12-slim AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ src/
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

RUN useradd -u 10001 -m -s /bin/bash aiat

WORKDIR /app

COPY --from=builder --chown=aiat:aiat /app/.venv .venv
COPY --chown=aiat:aiat src/ src/
COPY --chown=aiat:aiat alembic/ alembic/
COPY --chown=aiat:aiat alembic.ini ./

ENV PYTHONPATH="/app/src"
ENV PATH="/app/.venv/bin:$PATH"

USER aiat

CMD ["python", "-m", "aiat"]
