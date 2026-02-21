FROM python:3.11.11-slim-bookworm

ARG APP_USER=todoist
ARG APP_GROUP=todoist
ARG APP_UID=10001
ARG APP_GID=10001

RUN groupadd --gid "${APP_GID}" "${APP_GROUP}" \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin "${APP_USER}"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && chown -R "${APP_UID}:${APP_GID}" /app

USER ${APP_UID}:${APP_GID}

CMD ["python", "-m", "todoist_proxy", "--host", "0.0.0.0", "--port", "8080"]
