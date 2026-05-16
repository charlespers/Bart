#!/bin/sh
# Symlink every piece of stateful data onto the Fly volume (mounted at /data)
# so it survives redeploys, then start uvicorn.

set -e

# Fly mounts the volume at /data. If we're not on Fly (or running locally
# without a volume), make sure the dir exists anyway.
mkdir -p /data/materials/users \
         /data/output/users \
         /data/workspaces

# bart.db — SQLite reads/writes through the symlink, so the file lives on /data
if [ ! -e /data/bart.db ]; then
  touch /data/bart.db
fi
rm -f /app/website/bart.db
ln -sf /data/bart.db /app/website/bart.db

# uploaded course materials
rm -rf /app/materials/users
mkdir -p /app/materials
ln -sf /data/materials/users /app/materials/users

# generated packets
rm -rf /app/output/users
mkdir -p /app/output
ln -sf /data/output/users /app/output/users

# per-user .claude/ credentials (where `claude auth login` writes)
rm -rf /app/.workspaces
ln -sf /data/workspaces /app/.workspaces

# Run uvicorn with proxy-aware settings so request.url.scheme and the
# rate limiter's IP detection both see Fly's forwarded headers.
cd /app/website
exec ./.venv/bin/python -m uvicorn server:app \
     --host 0.0.0.0 --port 8080 \
     --proxy-headers --forwarded-allow-ips='*'
