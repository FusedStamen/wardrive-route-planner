#!/usr/bin/env bash
# Changes the planner sign-in password and restarts it.
# Run from the routeplanner folder:  sed -i 's/\r$//' setpassword.sh && bash setpassword.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo ".env not found. Run install.sh first."; exit 1; }

read -r -s -p "New password: " PW; echo
read -r -s -p "New password again: " PW2; echo
[ "$PW" = "$PW2" ] || { echo "Passwords did not match. Nothing changed."; exit 1; }
[ "${#PW}" -ge 8 ] || { echo "Use at least 8 characters. Nothing changed."; exit 1; }
case "$PW" in
  *[[:space:]]*|*'#'*|*'$'*|*'"'*|*"'"*|*'\'*) echo "Password can't contain spaces or the characters # \$ \" ' \\ . Nothing changed."; exit 1;;
esac

grep -v '^APP_PASSWORD=' .env > .env.new
echo "APP_PASSWORD=$PW" >> .env.new
chmod 600 .env.new
mv .env.new .env

docker compose up -d --build
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8090/healthz >/dev/null 2>&1; then echo "Password changed and the planner is running."; exit 0; fi
  sleep 2
done
echo "It did not come up. Run:  docker logs routeplanner"; exit 1
