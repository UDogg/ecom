FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN useradd --system --uid 10001 app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY worker worker
USER app
EXPOSE 8090
CMD ["python", "-m", "worker", "run"]
