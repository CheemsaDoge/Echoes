FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 echoledger \
    && useradd --uid 10001 --gid echoledger --create-home echoledger
WORKDIR /app
COPY backend /app/backend
COPY web /app/web
RUN pip install --no-cache-dir /app/backend && mkdir /data && chown echoledger:echoledger /data
USER echoledger
EXPOSE 8000
CMD ["uvicorn", "echoledger.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
