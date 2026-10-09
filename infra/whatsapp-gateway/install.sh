#!/usr/bin/env bash
# =============================================================================
# Voksy AI WhatsApp gateway — installer / updater.
#
# Run on the VPS wa-gateway-1 (Ubuntu 24.04) as root:
#   curl -fsSL https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/0410-golos/infra/whatsapp-gateway/install.sh | bash
#
# Idempotent: re-running downloads fresh compose/Caddy files, pulls images and
# restarts the stack. Generated secrets in /opt/voksy-wa/.env are kept.
#
# What it does
#   1. apt: docker.io + docker compose v2, curl, openssl
#   2. 2 GB swap file (once)
#   3. downloads docker-compose.yml + Caddyfile into /opt/voksy-wa
#   4. generates /opt/voksy-wa/.env on first run (API key, Postgres password)
#   5. docker compose pull + up -d
#   6. waits until https://$WA_DOMAIN answers and prints the values for Render
# =============================================================================
set -euo pipefail

BRANCH="${VOKSY_BRANCH:-0410-golos}"
BASE="${VOKSY_BASE:-https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/${BRANCH}/infra/whatsapp-gateway}"
WA_DOMAIN="${VOKSY_WA_DOMAIN:-wa.voksyai.online}"

APP_DIR=/opt/voksy-wa
ENV_FILE=$APP_DIR/.env

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || warn "tested on Ubuntu 24.04, you are on ${PRETTY_NAME:-unknown}"

# ---------------------------------------------------------------- packages
say "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 curl openssl dnsutils </dev/null >/dev/null
systemctl enable --now docker >/dev/null 2>&1

# ---------------------------------------------------------------- swap
if [ -z "$(swapon --show --noheadings)" ]; then
  say "creating 2 GB swap"
  [ -f /swapfile ] || { fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null; }
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ---------------------------------------------------------------- files
say "downloading gateway files from $BASE"
mkdir -p "$APP_DIR"
for f in docker-compose.yml Caddyfile; do
  curl -fsSL "$BASE/$f" -o "$APP_DIR/$f.new" || die "download failed: $f"
  mv "$APP_DIR/$f.new" "$APP_DIR/$f"
done

# ---------------------------------------------------------------- secrets
if [ ! -f "$ENV_FILE" ]; then
  say "generating secrets (first install)"
  API_KEY="$(openssl rand -hex 24)"
  PG_PASSWORD="$(openssl rand -hex 16)"
  cat > "$ENV_FILE" <<EOF
# Voksy AI WhatsApp gateway (Evolution API) — environment. Generated $(date -u +%FT%TZ)
WA_DOMAIN=$WA_DOMAIN
EVOLUTION_VERSION=v2.3.7
POSTGRES_PASSWORD=$PG_PASSWORD

SERVER_TYPE=http
SERVER_PORT=8080
SERVER_URL=https://$WA_DOMAIN
CORS_ORIGIN=*
# Same value goes to Render as WHATSAPP_GATEWAY_API_KEY
AUTHENTICATION_API_KEY=$API_KEY
AUTHENTICATION_EXPOSE_IN_FETCH_INSTANCES=false

DATABASE_ENABLED=true
DATABASE_PROVIDER=postgresql
DATABASE_CONNECTION_URI=postgresql://evolution:$PG_PASSWORD@postgres:5432/evolution?schema=evolution_api
DATABASE_CONNECTION_CLIENT_NAME=voksy_wa
DATABASE_SAVE_DATA_INSTANCE=true
DATABASE_SAVE_DATA_NEW_MESSAGE=true
DATABASE_SAVE_MESSAGE_UPDATE=true
DATABASE_SAVE_DATA_CONTACTS=true
DATABASE_SAVE_DATA_CHATS=true
DATABASE_SAVE_DATA_LABELS=false
# Old chat history synced from the phone is not needed by the agent
DATABASE_SAVE_DATA_HISTORIC=false

CACHE_REDIS_ENABLED=true
CACHE_REDIS_URI=redis://redis:6379/6
CACHE_REDIS_TTL=604800
CACHE_REDIS_PREFIX_KEY=voksy_wa
CACHE_REDIS_SAVE_INSTANCES=false
CACHE_LOCAL_ENABLED=false

# Webhooks are set per instance by the backend (POST /instance/create), not globally
WEBHOOK_GLOBAL_ENABLED=false

CONFIG_SESSION_PHONE_CLIENT=VoksiAI
CONFIG_SESSION_PHONE_NAME=Chrome
QRCODE_LIMIT=30
DEL_INSTANCE=false
LANGUAGE=en
LOG_LEVEL=ERROR,WARN,INFO
LOG_BAILEYS=error
TELEMETRY_ENABLED=false
EOF
  chmod 600 "$ENV_FILE"
else
  say "keeping existing $ENV_FILE"
fi
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
[ -n "${AUTHENTICATION_API_KEY:-}" ] || die "$ENV_FILE has no AUTHENTICATION_API_KEY"

# ---------------------------------------------------------------- DNS check
PUBLIC_IP="$(curl -4 -fsS https://api.ipify.org || true)"
DNS_IP="$(dig +short A "$WA_DOMAIN" @1.1.1.1 | tail -1 || true)"
if [ -n "$PUBLIC_IP" ] && [ "$DNS_IP" != "$PUBLIC_IP" ]; then
  warn "$WA_DOMAIN resolves to '${DNS_IP:-nothing}', this server is $PUBLIC_IP — HTTPS certificate will fail until the A record is fixed"
fi

# ---------------------------------------------------------------- start
say "starting containers"
cd "$APP_DIR"
docker compose pull -q
docker compose up -d --remove-orphans
docker image prune -f >/dev/null 2>&1 || true

# ---------------------------------------------------------------- checks
say "waiting for https://$WA_DOMAIN (certificate + Evolution start, up to 3 min)"
ok=0
for _ in $(seq 1 36); do
  if curl -fsS -m 5 "https://$WA_DOMAIN/" 2>/dev/null | grep -qi evolution; then ok=1; break; fi
  sleep 5
done
docker compose ps

if [ "$ok" = 1 ]; then
  say "gateway is up"
else
  warn "https://$WA_DOMAIN does not answer yet. Look at: cd $APP_DIR && docker compose logs --tail 80 evolution caddy"
fi

cat <<EOF

-----------------------------------------------------------------------------
Put these into Render (Environment) for the backend:
  WHATSAPP_GATEWAY_URL=https://$WA_DOMAIN
  WHATSAPP_GATEWAY_API_KEY=$AUTHENTICATION_API_KEY

Manager UI (for checks): https://$WA_DOMAIN/manager  (API key as above)
Logs:    cd $APP_DIR && docker compose logs -f evolution
Restart: cd $APP_DIR && docker compose restart
-----------------------------------------------------------------------------
EOF
