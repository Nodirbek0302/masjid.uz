# ── masjid-uz — yagona image (web API + statik klient, bot va OSM import shu image'dan ishlaydi) ──
#
# Qatlamlar ataylab shunday tartibda: bog'liqliklar koddan OLDIN o'rnatiladi, shu tufayli
# kod o'zgarganda pip qayta ishlamaydi — build bir necha soniya bo'ladi.
FROM python:3.12-slim AS runtime

ARG DEBIAN_FRONTEND=noninteractive

# OCI metama'lumotlari — Docker Hub'dagi image sahifasida ko'rinadi.
ARG VERSION=dev
ARG VCS_REF=unknown
ARG OCI_SOURCE=""
LABEL org.opencontainers.image.title="masjid-uz" \
      org.opencontainers.image.description="Masjid xaritasi: jamoat namozi vaqtlari, jamoa tahriri, moderatsiya" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="${OCI_SOURCE}" \
      org.opencontainers.image.licenses="proprietary"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONFAULTHANDLER=1 \
    HOST=0.0.0.0 \
    PORT=8080

# tini — PID 1 sifatida SIGTERM'ni to'g'ri uzatadi (`docker compose stop` 10 soniya kutmaydi).
# curl — deploy paytidagi health tekshiruvi uchun.
# tesseract-ocr — Telegram bot namoz jadvali rasmidan vaqtlarni o'qishi uchun (rasm uchinchi tomonga yuborilmaydi va saqlanmaydi).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
        curl \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-uzb tesseract-ocr-uzb-cyrl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 1-qatlam: faqat bog'liqliklar ─────────────────────────────
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── 2-qatlam: kod (o'zgarsa faqat shu qatlam qayta quriladi) ──
COPY server ./server
COPY web ./web
COPY deploy/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

# Root bo'lmagan foydalanuvchi
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Web uchun: API javob beradimi. (bot konteyneri compose'da healthcheck'ni o'chiradi.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["web"]
