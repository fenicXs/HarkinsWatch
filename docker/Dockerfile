# syntax=docker/dockerfile:1
# Two targets from one codebase:
#   bot    - python slim; Telegram handlers only, never opens a browser
#   worker - Playwright image (Chromium included), started under xvfb so a headed browser works
#
# PLAYWRIGHT_VERSION must equal the playwright pin in pyproject.toml
# (tests/unit/test_docker_pin.py checks this).
ARG UV_VERSION=0.11.17
ARG PLAYWRIGHT_VERSION=1.65.2

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------------------------------------
FROM python:3.12-slim AS bot
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN uv sync --frozen --no-dev --no-editable
RUN useradd --create-home --uid 10001 app
USER app
CMD ["python", "-m", "showwatch.bot"]

# ---------------------------------------------------------------------------------------------
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS worker
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
# xvfb: virtual display for headed Chromium. x11vnc + novnc + websockify: only used by the
# `login` compose profile, so a person can log in to a site through a browser tab.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN uv sync --frozen --no-dev --no-editable
RUN useradd --create-home --uid 10001 app \
    && mkdir /profiles \
    && chown app /profiles
USER app
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1280x1024x24", "python", "-m", "showwatch.worker"]
