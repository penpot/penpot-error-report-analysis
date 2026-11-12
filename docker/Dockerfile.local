# The platform environment, materialized from the committed lock file, so that the image runs
# the exact dependency versions the platform is developed against.
FROM ghcr.io/prefix-dev/pixi:0.80.0-trixie-slim AS build

# the environment is bound to the path it is created at, which the runtime stage thus reproduces
WORKDIR /opt/error-analysis

COPY pyproject.toml pixi.lock ./
COPY src ./src/

RUN pixi install --locked --environment default


FROM debian:trixie-slim AS image

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/opt/error-analysis/.pixi/envs/default/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    ERROR_ANALYSIS_DB=/opt/data/analysis.sqlite

RUN set -ex; \
    apt-get -qq update; \
    apt-get -qq -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" dist-upgrade; \
    apt-get -qqy --no-install-recommends install nginx; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd -g 1001 penpot; \
    useradd -M -u 1001 -g 1001 -s /usr/sbin/nologin -d /opt/data penpot; \
    mkdir -p /opt/data; \
    chown -R penpot:penpot /opt/data /var/log/nginx /var/lib/nginx

COPY --from=build /opt/error-analysis /opt/error-analysis
COPY docker/files/nginx.conf /etc/nginx/nginx.conf
COPY docker/files/entrypoint.sh /entrypoint.sh

EXPOSE 8080

USER penpot:penpot
WORKDIR /opt/data

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=4)"]

ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
