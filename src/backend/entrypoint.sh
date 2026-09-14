#!/bin/sh
set -e
echo "Running database migrations..."
alembic upgrade head
echo "Starting API..."
# --proxy-headers makes the app read X-Forwarded-For, and --forwarded-allow-ips
# says whose it may believe. Both matter for the audit log: behind nginx or
# Traefik every request arrives from a container address, and without these it
# is the proxy's IP that gets recorded against every action. The default trusts
# only loopback, which is uvicorn's own, so nothing changes unless it is set.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips "${TB_FORWARDED_ALLOW_IPS:-127.0.0.1}"
