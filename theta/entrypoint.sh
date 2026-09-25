#!/bin/sh
# T125. Authenticate the Theta Terminal from the environment, then run it.
# THETADATA_API_KEY wins if set; otherwise THETADATA_USERNAME + THETADATA_PASSWORD are written
# to a creds.txt (email on line 1, password on line 2) readable only by this process.
set -eu

if [ -n "${THETADATA_API_KEY:-}" ]; then
    set -- --api-key "$THETADATA_API_KEY"
elif [ -n "${THETADATA_USERNAME:-}" ] && [ -n "${THETADATA_PASSWORD:-}" ]; then
    umask 077
    printf '%s\n%s\n' "$THETADATA_USERNAME" "$THETADATA_PASSWORD" > /tmp/creds.txt
    set -- --creds-file /tmp/creds.txt
else
    echo "theta-terminal: set THETADATA_API_KEY, or THETADATA_USERNAME and THETADATA_PASSWORD, in .env" >&2
    exit 64
fi

exec java ${THETA_JAVA_OPTS:--Xmx2g} -jar /opt/theta/ThetaTerminalv3.jar "$@"
