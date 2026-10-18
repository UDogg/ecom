# Dependencies go into a virtualenv built in a throwaway stage; the runtime image gets the venv
# and the code only (no pip, no build cache, no test suites of third-party packages).
FROM python:3.14-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install -r requirements.txt \
 && /venv/bin/pip uninstall -qy pip \
 && find /venv -depth -type d -name tests -path '*/site-packages/*' -exec rm -rf {} + \
 && find /venv -name '*.po' -delete

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/venv/bin:$PATH
RUN useradd --system --uid 10001 app
COPY --from=deps /venv /venv
WORKDIR /app
COPY app app
COPY serve.py .
RUN python -m compileall -q -j 0 /app  # bytecode once, at build time: the container never writes it
USER app
EXPOSE 8003
CMD ["python", "serve.py"]
