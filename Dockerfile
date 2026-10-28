# masjid.uz — one image, several commands: serve (default) | bot | migrate | import-osm | import --file F
# Built by TIMUS from a release tag: `docker build .` (linux/amd64). No secrets, no build args.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONFAULTHANDLER=1

WORKDIR /app

# Dependencies first (cached layer): exact versions with hashes.
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt

COPY server ./server
COPY web ./web

# Non-root user; the code directory is read-only at runtime (the only writable path is /tmp, mounted by TIMUS).
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin masjiduz && chmod -R a+rX /app
USER 10001

EXPOSE 8080
ENTRYPOINT ["python", "/app/server/cli.py"]
CMD ["serve"]
