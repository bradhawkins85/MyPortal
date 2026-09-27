# MyPortal container image.
#
# Built and published to ghcr.io by .github/workflows/docker-release.yml for
# every GitHub release, and built locally by scripts/myportal-docker.sh when a
# release image cannot be pulled. Run it with that script (which writes the
# docker-compose.yml, database and configuration) rather than on its own.

# Ubuntu 24.04 matches the supported bare-metal platform (same Python 3.12 and
# system libraries as scripts/install_production.sh installs).
ARG BASE_IMAGE=ubuntu:24.04

# ---------------------------------------------------------------------------
# Build a virtualenv with every locked dependency. Compilers stay here.
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS build

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv python3.12-dev build-essential pkg-config libffi-dev ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
# Networks that inspect TLS can supply their CA bundle as the optional build
# secret "build_ca" (docker build --secret id=build_ca,src=bundle.pem). It is
# used only while downloading dependencies and never stored in the image.
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
 && python3.12 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check --requirement /tmp/requirements.lock \
 && /opt/venv/bin/pip check

# ---------------------------------------------------------------------------
# Runtime image.
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE}

ARG MYPORTAL_VERSION=development
ARG MYPORTAL_REVISION=unknown

LABEL org.opencontainers.image.title="MyPortal" \
      org.opencontainers.image.description="MyPortal customer portal" \
      org.opencontainers.image.source="https://github.com/bradhawkins85/MyPortal" \
      org.opencontainers.image.version="${MYPORTAL_VERSION}" \
      org.opencontainers.image.revision="${MYPORTAL_REVISION}"

# WeasyPrint (PDF rendering) needs pango/harfbuzz and fonts, python-magic needs
# libmagic, and zoneinfo needs the system time zone database.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.12 ca-certificates \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libmagic1 \
      fonts-dejavu-core fonts-liberation tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MYPORTAL_DEPLOYMENT=docker \
    APP_LOG_PATH= \
    PORT=8000

RUN groupadd --system --gid 10001 myportal \
 && useradd --system --uid 10001 --gid myportal --home-dir /app --no-create-home \
      --shell /usr/sbin/nologin myportal

WORKDIR /app
COPY --chown=root:root . /app

# The release identifier is what /readyz reports and what migrations record.
# Persistent data lives in volumes mounted over these directories; everything
# else in /app is read-only for the service account.
RUN printf '%s\n' "${MYPORTAL_VERSION}" > /app/version.txt \
 && rm -f /app/.env \
 && install -d -m 0750 -o myportal -g myportal \
      /app/private_uploads /app/app/static/uploads /app/var /app/var/state /app/var/data \
 && chmod 0755 /app/docker/entrypoint.sh \
 && python -m compileall -q /app/app /app/manage.py >/dev/null

USER myportal
EXPOSE 8000
VOLUME ["/app/private_uploads", "/app/app/static/uploads", "/app/var"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
  CMD python -c "import os,sys,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/readyz', timeout=8); sys.exit(0)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["serve"]
