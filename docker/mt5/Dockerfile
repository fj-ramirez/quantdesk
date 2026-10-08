# The desk's MT5 terminal (T143, plans/charter-mt5/README.md): one headless MetaTrader 5 under
# Wine, logged into the Axi demo account, plus the read-only bridge that serves its data on the
# compose network.
#
# Ported from carbon-copy's image (projects/carbon-copy/Dockerfile), which has run MT5 this way
# on the homeserver. Kept: the Wine build, the embedded Windows Python assembled with Linux pip
# (so no installer ever runs under Wine) and the pinned MetaTrader5 wheel. Dropped: the trade
# replicator core.
#
# **Linux hosts only.** MT5's copy protection crashes on Docker Desktop's WSL2 kernel, which is
# why the service sits behind the `broker` compose profile and never starts on the dev machine.
#
# MT5 itself is not baked in: the logged-in template folder is mounted at /mt5 (see
# compose.yaml), the same way carbon-copy mounts its template.

FROM debian:trixie-slim AS winpython
ARG PYTHON_VERSION=3.13.16
ARG METATRADER5_VERSION=5.0.6231
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates wget unzip python3-pip \
 && wget -qO /tmp/py.zip https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip \
 && unzip -q /tmp/py.zip -d /opt/python \
 && pip3 install --break-system-packages --no-cache-dir \
      --target /opt/python/Lib/site-packages \
      --platform win_amd64 --python-version "${PYTHON_VERSION%.*}" \
      --implementation cp --only-binary=:all: \
      MetaTrader5==${METATRADER5_VERSION} numpy \
 && printf 'Lib\\site-packages\r\nZ:\\app\r\nimport site\r\n' >>"$(ls /opt/python/python3*._pth)"

FROM debian:trixie-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG WINE_BRANCH=staging
ARG DEBIAN_CODENAME=trixie
ARG UID=1000
ARG GID=1000

RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates wget gnupg procps tini python3 tzdata \
      xvfb xauth x11vnc x11-utils xdotool \
      winbind cabextract \
 && mkdir -pm755 /etc/apt/keyrings \
 && wget -qO /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key \
 && wget -qNP /etc/apt/sources.list.d/ \
      https://dl.winehq.org/wine-builds/debian/dists/${DEBIAN_CODENAME}/winehq-${DEBIAN_CODENAME}.sources \
 && apt-get update \
 && apt-get install -y --install-recommends winehq-${WINE_BRANCH} \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -g ${GID} mt5 \
 && useradd -m -u ${UID} -g ${GID} -s /bin/bash mt5 \
 && mkdir -p /mt5 /opt/wineprefix \
 && chown mt5:mt5 /mt5 /opt/wineprefix

ENV WINEPREFIX=/opt/wineprefix \
    WINEARCH=win64 \
    WINEDEBUG=-all \
    DISPLAY=:99 \
    PYTHONUNBUFFERED=1 \
    MT5_DIR=/mt5 \
    BRIDGE_PORT=18812

USER mt5

# Build the Wine prefix once, here: no Mono/Gecko prompts at runtime, reported as Windows 11.
RUN WINEDLLOVERRIDES="mscoree,mshtml=" xvfb-run -a wineboot --init \
 && wineserver -w \
 && wine reg add 'HKCU\Software\Wine' /v Version /t REG_SZ /d win11 /f \
 && wineserver -w

COPY --from=winpython /opt/python /opt/python
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chmod=755 healthcheck.py /usr/local/bin/healthcheck.py
COPY bridge /app/bridge

WORKDIR /mt5
EXPOSE 18812 5900

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
