#!/usr/bin/env bash
# Creates .env for docker compose: random secrets + the first free host port (from 8080 up).
# Usage: bash deploy/init-env.sh <server-ip>
set -euo pipefail
cd "$(dirname "$0")/.."

SERVER_IP="${1:?usage: bash deploy/init-env.sh <server-ip>}"

command -v ss >/dev/null || { echo "'ss' not found (install iproute2)." >&2; exit 1; }

if [ -f .env ]; then
  echo ".env already exists - delete it first to regenerate." >&2
  exit 1
fi

port=8080
while ss -tln | awk 'NR>1 {print $4}' | grep -qE "[:.]${port}$"; do
  port=$((port + 1))
done

cp .env.example .env
sed -i \
  -e "s|^HOST_PORT=.*|HOST_PORT=${port}|" \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" \
  -e "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$(openssl rand -hex 32)|" \
  -e "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')|" \
  -e "s|^CORS_ORIGINS=.*|CORS_ORIGINS=http://${SERVER_IP}:${port}|" \
  .env

echo "Created .env (HOST_PORT=${port}). Replace every CHANGE_ME:  nano .env"
