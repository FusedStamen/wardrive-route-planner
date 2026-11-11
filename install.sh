#!/usr/bin/env bash
# Installs the wardrive route planner (Docker). Written for a Raspberry Pi 5, works on any Docker host.
# Run from inside the routeplanner folder:  sed -i 's/\r$//' install.sh && bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Docker is not installed."; exit 1; }
docker network inspect proxy >/dev/null 2>&1 || { echo "Docker network 'proxy' not found, creating it."; docker network create proxy >/dev/null; }
mkdir -p data

if [ ! -f .env ]; then
  echo "Paste the read-only warroom bearer token (starts with wr_). Nothing is shown as you type."
  read -r -s -p "Warroom token: " TOKEN; echo
  [ -n "$TOKEN" ] || { echo "No token entered."; exit 1; }
  read -r -p "Warroom address (for example https://warroom.example.com): " WURL
  [ -n "$WURL" ] || { echo "No warroom address entered."; exit 1; }
  echo "Choose a password for signing in to the planner (8 or more letters and numbers)."
  read -r -s -p "Password: " PW; echo
  read -r -s -p "Password again: " PW2; echo
  [ "$PW" = "$PW2" ] || { echo "Passwords did not match."; exit 1; }
  [ "${#PW}" -ge 8 ] || { echo "Use at least 8 characters."; exit 1; }
  SECRET="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  umask 077
  {
    echo "WARROOM_URL=$WURL"
    echo "WARROOM_TOKEN=$TOKEN"
    echo "APP_PASSWORD=$PW"
    echo "SECRET=$SECRET"
  } > .env
  chmod 600 .env
  echo "Saved settings to .env"
else
  echo ".env already exists, keeping it."
fi

docker compose up -d --build
echo "Waiting for the planner to start..."
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8090/healthz >/dev/null 2>&1; then
    echo "Running."
    echo
    echo "Quick LAN check: http://<this machine's address>:8090"
    echo "To reach it from outside your home, put it behind a tunnel or reverse proxy with HTTPS."
    echo "Cloudflare Tunnel example: published application route, service HTTP, URL routeplanner:8080,"
    echo "with cloudflared on the same Docker network ('proxy')."
    exit 0
  fi
  sleep 2
done
echo "It did not come up. Run:  docker logs routeplanner"
exit 1
