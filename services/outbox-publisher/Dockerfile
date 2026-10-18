FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN useradd --system --uid 10001 app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY publisher.py .
USER app
EXPOSE 8091
CMD ["python", "publisher.py"]
