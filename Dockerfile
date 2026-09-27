# Mizan - Shariah compliance screening API.
# Python 3.11.9 to match runtime.txt and the Render deployment.

FROM python:3.11.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Dependencies first, so editing server.py does not invalidate this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py index.html ./

# Run as non-root. The app writes nothing to disk, so no extra permissions are needed.
RUN useradd --create-home --uid 1001 mizan
USER mizan

EXPOSE 8000

# /health returns 200 unconditionally. Do not swap this for /screen, which
# requires a symbol parameter and answers 400 without one.
# urllib raises on any non-200, which is a non-zero exit for Docker.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4)"]

# Shell form so ${PORT} expands. Render injects PORT; locally it falls back to 8000.
# Note: server.py calls check_setup() at import time, so the container exits
# immediately if TIINGO_API_KEY is not set.
CMD gunicorn server:app --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --timeout 60
