# Dependencies go into a virtualenv built in a throwaway stage; the runtime image gets the venv
# and the code only (no pip, no build cache, no test suites of third-party packages).
FROM python:3.14-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt requirements-dev.txt ./
RUN /venv/bin/pip install -r requirements.txt

# Production prunes what this API never loads: translations (USE_I18N is off), the admin's and the
# browsable API's static files (JSON renderer only), GeoDjango. `manage.py check` proves it still boots.
FROM deps AS deps-prod
RUN /venv/bin/pip uninstall -qy pip \
 && cd /venv/lib/python3.*/site-packages \
 && find . -depth -type d -name tests -exec rm -rf {} + \
 && find . -name '*.po' -delete \
 && find . -path '*/locale/*' -name '*.mo' ! -path '*/locale/en/*' -delete \
 && rm -rf django/contrib/admin/static django/contrib/gis rest_framework/static

FROM deps AS deps-dev
RUN /venv/bin/pip install -r requirements-dev.txt && /venv/bin/pip uninstall -qy pip

FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/venv/bin:$PATH
RUN useradd --system --uid 10001 app
WORKDIR /app

FROM base AS prod
COPY --from=deps-prod /venv /venv
COPY . .
RUN python -m compileall -q -j 0 /app \
 && DJANGO_SECRET_KEY=build-check GATEWAY_SECRET=build-check-build-check-build-check python manage.py check --deploy --fail-level ERROR >/dev/null
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "-c", "gunicorn.conf.py"]

FROM base AS dev
COPY --from=deps-dev /venv /venv
COPY . .
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "-c", "gunicorn.conf.py"]
