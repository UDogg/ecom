FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN useradd --system --uid 10001 app

FROM base AS prod
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
USER app
EXPOSE 8000
CMD ["sh", "-c", "gunicorn config.wsgi -b 0.0.0.0:8000 -w ${WEB_CONCURRENCY:-3} --timeout 30 --access-logfile -"]

FROM prod AS dev
USER root
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
USER app
