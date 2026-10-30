FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && useradd --uid 10001 --create-home app

COPY --chown=app:app bot ./bot
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini .

USER app
CMD ["python", "-m", "bot"]

FROM base AS test
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.txt
COPY --chown=app:app tests ./tests
COPY --chown=app:app pyproject.toml .
USER app
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

FROM base AS runtime
