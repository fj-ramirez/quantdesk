# T125. The Theta Terminal, for the homeserver's stack (plans/thetadata/README.md).
#
# ThetaTerminalv3.jar is a *bootstrap*: on start it downloads the real terminal into
# /opt/theta/lib (a volume, so a restart does not re-download) and runs it. The jar is
# ThetaData's, not ours, so it is not committed: copy it into this directory before building
# (`theta/ThetaTerminalv3.jar`, gitignored).
#
# The terminal serves its REST API on 0.0.0.0:25503 by default (verified 2026-09-25, terminal
# 20260924), so workers reach it at http://theta-terminal:25503 with no forwarding.
FROM eclipse-temurin:21-jre

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/theta
COPY ThetaTerminalv3.jar /opt/theta/ThetaTerminalv3.jar
COPY entrypoint.sh /usr/local/bin/theta-entrypoint
RUN chmod +x /usr/local/bin/theta-entrypoint

EXPOSE 25503
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS -o /dev/null "http://127.0.0.1:25503/v3/option/list/expirations?symbol=SPY" || exit 1

ENTRYPOINT ["theta-entrypoint"]
