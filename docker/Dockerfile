# syntax=docker/dockerfile:1

FROM ghcr.io/prefix-dev/pixi:0.80.0-trixie-slim AS build

# the environment is bound to the path it is created at, which the runtime stage thus reproduces
WORKDIR /opt/error-analysis

COPY pyproject.toml pixi.lock ./
COPY src ./src/

RUN pixi install --locked --environment default


# Shared runtime base for the application services
FROM dhi.io/debian-base:trixie-debian13-dev@sha256:af61a3296b0c92f16969f11ce8c2a93722e84c9e5c1bb42ed6afec796be71b04 AS app

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
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd -g 1001 penpot; \
    useradd -M -u 1001 -g 1001 -s /usr/sbin/nologin -d /opt/data penpot; \
    mkdir -p /opt/data; \
    chown -R penpot:penpot /opt/data

COPY --from=build /opt/error-analysis /opt/error-analysis

USER penpot:penpot
WORKDIR /opt/data


# Dashboard and JSON API.
FROM app AS web

EXPOSE 5100

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5100/', timeout=4)"]

ENTRYPOINT ["error-analysis-web"]
CMD ["--host", "0.0.0.0", "--port", "5100"]


# MCP server (streamable HTTP, endpoint /mcp).
FROM app AS mcp

EXPOSE 5101

ENTRYPOINT ["error-analysis-mcp"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "5101"]
