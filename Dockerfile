# syntax=docker/dockerfile:1.7
# TrucCa - Alert Exporter & NOC Shift Report
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------- Stage 1: cài thư viện Python vào venv ----------
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# ---------- Stage 2: runtime ----------
FROM ${PYTHON_IMAGE} AS runtime
ARG APP_VERSION=dev
ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="TrucCa" \
      org.opencontainers.image.description="Alert Exporter & NOC Shift Report cho Prometheus Alertmanager" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    APP_VERSION=${APP_VERSION} \
    TRUCCA_CONFIG=/app/config/config.yml \
    TZ=Asia/Ho_Chi_Minh

COPY --from=builder /opt/venv /opt/venv

# Chromium headless shell (chỉ để render PDF) + đúng các thư viện nó cần; tini làm PID 1.
# Không dùng `playwright install --with-deps` vì kéo thêm ~150MB font CJK/X11 không cần thiết.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tini fontconfig fonts-dejavu-core \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 \
      libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 \
      libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
 && playwright install --only-shell chromium \
 && rm -rf /ms-playwright/ffmpeg-* /var/lib/apt/lists/* /tmp/* /root/.cache \
 && pip uninstall -y pip \
 && chmod -R a+rX /ms-playwright

# Font tiếng Việt nhúng sẵn (Be Vietnam Pro, giấy phép OFL) cho PDF
COPY trucca/static/fonts/*.ttf /usr/local/share/fonts/bevietnampro/
RUN fc-cache -f >/dev/null

RUN groupadd --system --gid 10001 trucca \
 && useradd --system --uid 10001 --gid trucca --home-dir /app --shell /usr/sbin/nologin trucca \
 && mkdir -p /app/config /data/reports \
 && chown -R trucca:trucca /data

WORKDIR /app
COPY --chown=root:root migrations /app/migrations
COPY --chown=root:root mock /app/mock
COPY --chown=root:root trucca /app/trucca
COPY --chown=root:root config/config.example.yml /app/config.example.yml

USER 10001:10001
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=8s --start-period=40s --retries=3 CMD ["python", "-m", "trucca.healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "trucca.main"]
