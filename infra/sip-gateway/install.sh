#!/usr/bin/env bash
# =============================================================================
# Voksy AI SIP gateway — installer / updater.
#
# Run on the gateway VPS (Ubuntu 24.04) as root:
#   curl -fsSL https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/2308-agent-v2/infra/sip-gateway/install.sh | bash
#
# Idempotent: re-running updates Asterisk configs and the bridge code, keeps
# the generated secrets in /etc/voksy-bridge/bridge.env.
#
# What it does
#   1. apt: asterisk, python3-venv, curl
#   2. writes /etc/asterisk/{pjsip,extensions,rtp,manager,modules}.conf
#      (original directory is preserved once as /etc/asterisk.orig)
#   3. installs the bridge into /opt/voksy-bridge (own venv, own system user)
#   4. generates secrets: AMI password, softphone test password, GATEWAY_TOKEN
#   5. enables systemd units and restarts Asterisk
# =============================================================================
set -euo pipefail

BRANCH="${VOKSY_BRANCH:-2308-agent-v2}"
BASE="${VOKSY_BASE:-https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/${BRANCH}/infra/sip-gateway}"
BACKEND_WS_URL="${VOKSY_BACKEND_WS_URL:-wss://voksyai.online}"
GATEWAY_ID="${VOKSY_GATEWAY_ID:-sip-gw-1}"

ENV_DIR=/etc/voksy-bridge
ENV_FILE=$ENV_DIR/bridge.env
APP_DIR=/opt/voksy-bridge

FILES=(
  asterisk/pjsip.conf
  asterisk/extensions.conf
  asterisk/rtp.conf
  asterisk/manager.conf
  asterisk/modules.conf
  bridge/bridge.py
  bridge/requirements.txt
  bridge/voksy-bridge.service
)

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || warn "tested on Ubuntu 24.04, you are on ${PRETTY_NAME:-unknown}"

PUBLIC_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)"
[ -n "$PUBLIC_IP" ] || PUBLIC_IP="$(curl -4 -fsS https://api.ipify.org || true)"
[ -n "$PUBLIC_IP" ] || die "cannot determine public IPv4"
say "public IP: $PUBLIC_IP"

say "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq asterisk python3-venv python3-pip curl >/dev/null

say "downloading gateway files from $BASE"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for f in "${FILES[@]}"; do
  mkdir -p "$TMP/$(dirname "$f")"
  curl -fsSL "$BASE/$f" -o "$TMP/$f" || die "download failed: $f"
done

# ---------------------------------------------------------------- secrets
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
  say "generating secrets (first install)"
  AMI_SECRET="$(openssl rand -hex 16)"
  GATEWAY_TOKEN="$(openssl rand -hex 24)"
  TEST_SIP_PASSWORD="$(openssl rand -hex 8)"
  cat > "$ENV_FILE" <<EOF
# Voksy AI SIP gateway bridge — environment. Generated $(date -u +%FT%TZ)
BACKEND_WS_URL=$BACKEND_WS_URL
GATEWAY_ID=$GATEWAY_ID
# Same value must be set on the backend (Render) as SIP_GATEWAY_TOKEN
GATEWAY_TOKEN=$GATEWAY_TOKEN
PUBLIC_IP=$PUBLIC_IP

AMI_HOST=127.0.0.1
AMI_PORT=5038
AMI_USER=bridge
AMI_SECRET=$AMI_SECRET

TRUNK_ENDPOINT=o-trunk
TRUNK_HOSTS=195.216.237.6:5070,195.216.237.7:5070
MAX_OUTBOUND=4
ORIGINATE_TIMEOUT_MS=45000

# Temporary softphone account (user "test" on UDP 5080)
TEST_SIP_PASSWORD=$TEST_SIP_PASSWORD
LOG_LEVEL=INFO
EOF
  chmod 600 "$ENV_FILE"
else
  say "keeping existing $ENV_FILE"
fi
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
[ -n "${AMI_SECRET:-}" ] && [ -n "${TEST_SIP_PASSWORD:-}" ] || die "$ENV_FILE is missing AMI_SECRET / TEST_SIP_PASSWORD"

# ---------------------------------------------------------------- asterisk
say "installing Asterisk configuration"
[ -d /etc/asterisk.orig ] || cp -a /etc/asterisk /etc/asterisk.orig
for f in pjsip extensions rtp manager modules; do
  sed -e "s|__PUBLIC_IP__|$PUBLIC_IP|g" \
      -e "s|__AMI_SECRET__|$AMI_SECRET|g" \
      -e "s|__TEST_PASSWORD__|$TEST_SIP_PASSWORD|g" \
      "$TMP/asterisk/$f.conf" > "/etc/asterisk/$f.conf"
  chown asterisk:asterisk "/etc/asterisk/$f.conf"
  chmod 640 "/etc/asterisk/$f.conf"
done

# ---------------------------------------------------------------- bridge
say "installing bridge into $APP_DIR"
id -u voksy >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin voksy
mkdir -p "$APP_DIR"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip >/dev/null
"$APP_DIR/venv/bin/pip" install -q -r "$TMP/bridge/requirements.txt"
install -m 644 "$TMP/bridge/bridge.py" "$APP_DIR/bridge.py"
chown -R voksy:voksy "$APP_DIR"
install -m 644 "$TMP/bridge/voksy-bridge.service" /etc/systemd/system/voksy-bridge.service
systemctl daemon-reload

# ---------------------------------------------------------------- services
say "restarting services"
systemctl enable asterisk >/dev/null 2>&1 || true
systemctl restart asterisk
asterisk -rx "core waitfullybooted" >/dev/null 2>&1 || sleep 5
systemctl enable voksy-bridge >/dev/null 2>&1
systemctl restart voksy-bridge
sleep 2

# ---------------------------------------------------------------- checks
say "checking"
ok=1
for mod in app_audiosocket res_pjsip func_curl; do
  if asterisk -rx "module show like $mod" | grep -q "^$mod"; then
    echo "  [ok] Asterisk module $mod"
  else
    echo "  [!!] Asterisk module $mod is NOT loaded"; ok=0
  fi
done
if asterisk -rx "pjsip show endpoints" | grep -q "o-trunk"; then
  echo "  [ok] trunk endpoint o-trunk configured"
else
  echo "  [!!] trunk endpoint missing"; ok=0
fi
if systemctl is-active --quiet voksy-bridge; then
  echo "  [ok] voksy-bridge running"
else
  echo "  [!!] voksy-bridge not running: journalctl -u voksy-bridge -n 50"; ok=0
fi
if curl -fsS http://127.0.0.1:9091/health >/dev/null 2>&1; then
  echo "  [ok] bridge HTTP answers"
else
  echo "  [!!] bridge HTTP not answering"; ok=0
fi

cat <<EOF

=============================================================================
Voksy SIP gateway installed.

  Public IP (for the operator form) : $PUBLIC_IP   SIP 5060/UDP, RTP 10000-20000/UDP
  Backend                           : $BACKEND_WS_URL
  Gateway id                        : $GATEWAY_ID

  Softphone test account (temporary):
      server   : $PUBLIC_IP:5080   (UDP)
      user     : test
      password : $TEST_SIP_PASSWORD
      dial 100 : Asterisk echo test (works without the backend)

  GATEWAY_TOKEN (set it on Render as SIP_GATEWAY_TOKEN):
      $GATEWAY_TOKEN

  Logs: journalctl -u voksy-bridge -f      Asterisk CLI: asterisk -rvvv
=============================================================================
EOF
[ "$ok" = 1 ] || warn "some checks failed, see above"
