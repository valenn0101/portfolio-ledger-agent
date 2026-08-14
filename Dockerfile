FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_DATA_DIR=/app/data

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app app.py ./
COPY --chown=app:app src ./src
COPY --chown=app:app static ./static
COPY --chown=app:app assets ./assets

RUN mkdir -p /app/data && chown app:app /app/data

USER app

EXPOSE 8765
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; port=os.getenv('PORT', '8765'); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2)"]

CMD ["python", "app.py", "--host", "0.0.0.0", "--no-browser"]
