FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system saferelay && \
    adduser --system --ingroup saferelay saferelay

COPY requirements.txt .
RUN python -m pip install --upgrade pip && \
    pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/logs && \
    chown -R saferelay:saferelay /app

USER saferelay

VOLUME ["/app/data", "/app/logs"]

CMD ["python", "main.py"]
