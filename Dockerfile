ARG PYTHON_VERSION=3.13

# Runtime image (compose.yaml builds this stage)
FROM python:${PYTHON_VERSION}-slim AS base

# Log lines must reach the container log at once
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY . .

# MEDIA_ROOT is /media (parent of BASE_DIR), the volume "media" is mounted there.
RUN useradd --uid 10001 --create-home labcirs \
    && mkdir -p /media \
    && chown labcirs /media

# The static files go to STATIC_ROOT (/static, parent of BASE_DIR) and are served by whitenoise.
# The settings refuse to load without the required values, these are placeholders for the build only.
RUN LABCIRS_SECRET_KEY=build-only LABCIRS_ALLOWED_HOSTS='[]' \
    LABCIRS_LANGUAGES='{"en": "English"}' LABCIRS_PARLER_LANGUAGES='["en"]' \
    python manage.py collectstatic --noinput

USER labcirs
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]

# Development and tests (compose.dev.yaml builds this stage)
FROM base AS test
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*
COPY requirements_test.txt ./
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements_test.txt
USER labcirs
