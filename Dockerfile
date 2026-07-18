# python:3.11-slim is published for both Apple Silicon (arm64) and Intel
# (amd64). LibreOffice is installed from the Debian repository so the image
# uses the same headless renderer on either architecture.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PPTLIB_HOME=/data \
    PPTLIB_OUTPUT_ROOT=/exports \
    PPTLIB_TEMP_DIR=/tmp/pptlib \
    PPTLIB_LOG_DIR=/logs \
    PPTLIB_HOST=0.0.0.0 \
    PPTLIB_PORT=8765

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-liberation \
        fonts-noto-cjk \
        libreoffice-impress \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install . \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin pptlib \
    && mkdir -p /data /exports /logs /tmp/pptlib /sources \
    && chown -R pptlib:pptlib /app /data /exports /logs /tmp/pptlib /sources

USER pptlib

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=3)"

CMD ["pptlib", "serve", "--no-open"]
