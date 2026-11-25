#!/usr/bin/env bash
# =============================================================================
# Voksy AI SIP gateway — installer / updater.
#
# Run on the gateway VPS (Ubuntu 24.04) as root:
#   curl -fsSL https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/1409-sip-v1/infra/sip-gateway/install.sh | bash
#
# Idempotent: re-running updates Asterisk configs and the bridge code, keeps
# the generated secrets in /etc/voksy-bridge/bridge.env.
#
# What it does
#   1. apt: asterisk, python3-venv, curl, lame (MP3 for call recordings)
#   2. writes /etc/asterisk/{pjsip,extensions,rtp,manager,modules}.conf
#      (original directory is preserved once as /etc/asterisk.orig)
#   3. installs the bridge into /opt/voksy-bridge (own venv, own system user)
#   4. generates secrets: AMI password, softphone test password, GATEWAY_TOKEN
#   5. creates /var/spool/voksy-rec (call recordings waiting for upload)
#   6. enables systemd units and restarts Asterisk
#   7. WhatsApp Business Calling (optional, only with WA_SIP_HOST): Let's Encrypt
#      certificate for that host (certbot standalone, needs TCP 80 open for a
#      minute), TLS transport 5061 + endpoint wa-meta (asterisk/pjsip_whatsapp.conf)
#
#   Enable WhatsApp once (the host is remembered in bridge.env):
#     curl -fsSL .../install.sh | VOKSY_BRANCH=<branch> VOKSY_WA_SIP_HOST=sip.voksyai.online bash
# =============================================================================
set -euo pipefail

BRANCH="${VOKSY_BRANCH:-1409-sip-v1}"
BASE="${VOKSY_BASE:-https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/${BRANCH}/infra/sip-gateway}"
BACKEND_WS_URL="${VOKSY_BACKEND_WS_URL:-wss://voksyai.online}"
GATEWAY_ID="${VOKSY_GATEWAY_ID:-sip-gw-1}"

ENV_DIR=/etc/voksy-bridge
ENV_FILE=$ENV_DIR/bridge.env
APP_DIR=/opt/voksy-bridge

FILES=(
  asterisk/pjsip.conf
  asterisk/pjsip_whatsapp.conf
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
# asterisk-modules carries func_curl/res_curl, which the dialplan needs to register
# inbound calls with the bridge. It is only a Recommends of "asterisk", so name it.
# lame compresses call recordings (MixMonitor WAV) to MP3 before upload.
apt-get install -y -qq asterisk asterisk-modules python3-venv python3-pip curl lame >/dev/null

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
# Channels sold by the operator (service card): 5 in + 5 out
MAX_OUTBOUND=5
MAX_INBOUND=5
ORIGINATE_TIMEOUT_MS=45000

# Number format towards the operator: 996705701707 -> 0705701707 (national, "070"/"050")
COUNTRY_CODE=996
NATIONAL_PREFIX=0
SUBSCRIBER_DIGITS=9

# Temporary softphone account (user "test" on UDP 5080)
TEST_SIP_PASSWORD=$TEST_SIP_PASSWORD
LOG_LEVEL=INFO
EOF
  chmod 600 "$ENV_FILE"
else
  say "keeping existing $ENV_FILE"
  # Settings introduced after the first install are appended here, so an already
  # running gateway picks up the operator's real channel counts and the number
  # format without anyone editing bridge.env by hand.
  add_env() {
    grep -q "^$1=" "$ENV_FILE" || { printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; say "added $1=$2"; }
  }
  # MAX_OUTBOUND=4 was our placeholder before the operator confirmed 5 channels.
  if grep -q '^MAX_OUTBOUND=4$' "$ENV_FILE"; then
    sed -i 's|^MAX_OUTBOUND=4$|MAX_OUTBOUND=5|' "$ENV_FILE"
    say "raised MAX_OUTBOUND 4 -> 5"
  fi
  add_env MAX_OUTBOUND 5
  add_env MAX_INBOUND 5
  add_env COUNTRY_CODE 996
  add_env NATIONAL_PREFIX 0
  add_env SUBSCRIBER_DIGITS 9
fi
# WhatsApp Business Calling: the host given once on the command line is kept in bridge.env.
if [ -n "${VOKSY_WA_SIP_HOST:-}" ]; then
  if grep -q '^WA_SIP_HOST=' "$ENV_FILE"; then
    sed -i "s|^WA_SIP_HOST=.*|WA_SIP_HOST=$VOKSY_WA_SIP_HOST|" "$ENV_FILE"
  else
    printf 'WA_SIP_HOST=%s\n' "$VOKSY_WA_SIP_HOST" >> "$ENV_FILE"
  fi
  say "WhatsApp SIP host: $VOKSY_WA_SIP_HOST"
fi
grep -q '^MAX_INBOUND_WA=' "$ENV_FILE" || printf 'MAX_INBOUND_WA=5\n' >> "$ENV_FILE"
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
[ -n "${AMI_SECRET:-}" ] && [ -n "${TEST_SIP_PASSWORD:-}" ] || die "$ENV_FILE is missing AMI_SECRET / TEST_SIP_PASSWORD"

# ---------------------------------------------------------------- whatsapp tls
# Meta sends WhatsApp calls as SIP over TLS (5061) and checks that the certificate
# matches WA_SIP_HOST. The certificate is copied next to the Asterisk configs so the
# asterisk user can read it; renewals copy it again and restart Asterisk once no
# call is running (a TLS transport does not pick up a new certificate on reload).
WA_ON=0
WA_KEYS=/etc/asterisk/keys
if [ -n "${WA_SIP_HOST:-}" ]; then
  say "WhatsApp Business Calling: TLS certificate for $WA_SIP_HOST"
  apt-get install -y -qq certbot >/dev/null
  RESOLVED="$(getent ahostsv4 "$WA_SIP_HOST" | awk 'NR==1{print $1}')"
  if [ "$RESOLVED" != "$PUBLIC_IP" ]; then
    warn "$WA_SIP_HOST resolves to '${RESOLVED:-nothing}', not $PUBLIC_IP: add the DNS A record and run install.sh again"
  fi
  LE_DIR="/etc/letsencrypt/live/$WA_SIP_HOST"
  if [ ! -f "$LE_DIR/fullchain.pem" ] && [ "$RESOLVED" = "$PUBLIC_IP" ]; then
    certbot certonly --standalone --non-interactive --agree-tos --register-unsafely-without-email \
      --preferred-challenges http -d "$WA_SIP_HOST" \
      || warn "certbot failed: is TCP 80 open in the Hetzner firewall?"
  fi
  install -d -o asterisk -g asterisk -m 750 "$WA_KEYS"
  cat > /usr/local/sbin/voksy-wa-cert <<'HOOK'
#!/usr/bin/env bash
# Copy the Let's Encrypt certificate of WA_SIP_HOST for Asterisk; with --restart,
# restart Asterisk once no call is running (at most 6 h of waiting).
set -eu
. /etc/voksy-bridge/bridge.env
LE="/etc/letsencrypt/live/$WA_SIP_HOST"
[ -f "$LE/fullchain.pem" ] || exit 0
install -o asterisk -g asterisk -m 640 "$LE/fullchain.pem" /etc/asterisk/keys/wa-fullchain.pem
install -o asterisk -g asterisk -m 600 "$LE/privkey.pem" /etc/asterisk/keys/wa-privkey.pem
if [ "${1:-}" = "--restart" ]; then
  for _ in $(seq 1 360); do
    n="$(asterisk -rx 'core show channels count' </dev/null 2>/dev/null | awk '/active call/{print $1}')"
    [ "${n:-0}" = 0 ] && break
    sleep 60
  done
  systemctl restart asterisk
fi
HOOK
  chmod 755 /usr/local/sbin/voksy-wa-cert
  install -d /etc/letsencrypt/renewal-hooks/deploy
  cat > /etc/letsencrypt/renewal-hooks/deploy/voksy-asterisk.sh <<'HOOK'
#!/usr/bin/env bash
# Renewed certificate -> Asterisk, restart in the background when the line is idle.
systemd-run --unit "voksy-wa-cert-$(date +%s)" /usr/local/sbin/voksy-wa-cert --restart >/dev/null 2>&1 || true
HOOK
  chmod 755 /etc/letsencrypt/renewal-hooks/deploy/voksy-asterisk.sh
  /usr/local/sbin/voksy-wa-cert
  if [ -f "$WA_KEYS/wa-fullchain.pem" ] && [ -f "$WA_KEYS/wa-privkey.pem" ]; then
    WA_ON=1
  else
    warn "no certificate for $WA_SIP_HOST yet: WhatsApp calls stay OFF"
  fi
fi

# ---------------------------------------------------------------- asterisk
say "installing Asterisk configuration"
[ -d /etc/asterisk.orig ] || cp -a /etc/asterisk /etc/asterisk.orig
if [ "$WA_ON" != 1 ]; then
  # Without a certificate the TLS transport cannot start: leave the include empty.
  printf '; WhatsApp Business Calling is off (no WA_SIP_HOST or no certificate). See install.sh.\n' \
    > "$TMP/asterisk/pjsip_whatsapp.conf"
fi
for f in pjsip pjsip_whatsapp extensions rtp manager modules; do
  sed -e "s|__PUBLIC_IP__|$PUBLIC_IP|g" \
      -e "s|__WA_SIP_HOST__|${WA_SIP_HOST:-}|g" \
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
# Call recordings: Asterisk (MixMonitor) writes here, the bridge (group voksy) reads,
# converts and deletes after upload. setgid keeps new files in group voksy.
REC_DIR=/var/spool/voksy-rec
install -d -o asterisk -g voksy -m 2770 "$REC_DIR"
chown asterisk:voksy "$REC_DIR"
chmod 2770 "$REC_DIR"
install -m 644 "$TMP/bridge/voksy-bridge.service" /etc/systemd/system/voksy-bridge.service
systemctl daemon-reload

# ---------------------------------------------------------------- services
say "restarting services"
systemctl enable asterisk >/dev/null 2>&1 || true
systemctl restart asterisk
# All Asterisk CLI calls go through ast(): stdin from /dev/null (under "curl | bash"
# stdin is the script itself), output captured into a variable, no pipes (pipefail +
# an early-exiting grep made loaded modules look missing).
ast() { asterisk -rx "$1" </dev/null 2>/dev/null || true; }
for _ in $(seq 1 30); do
  asterisk -rx "core waitfullybooted" </dev/null >/dev/null 2>&1 && break
  sleep 1
done
systemctl enable voksy-bridge >/dev/null 2>&1
systemctl restart voksy-bridge
sleep 2

# ---------------------------------------------------------------- checks
say "checking"
ok=1
module_loaded() { case "$(ast "module show like $1")" in *"$1.so "*) return 0 ;; esac; return 1; }
# Ubuntu keeps modules under /usr/lib/<arch>-linux-gnu/asterisk/modules.
module_on_disk() {
  local d
  for d in /usr/lib/x86_64-linux-gnu/asterisk/modules /usr/lib/aarch64-linux-gnu/asterisk/modules /usr/lib/asterisk/modules; do
    [ -f "$d/$1.so" ] && return 0
  done
  return 1
}
for mod in app_audiosocket app_mixmonitor res_pjsip func_curl; do
  # autoload can skip a module whose dependency was not ready at boot; one explicit
  # load settles whether it is really missing from disk.
  module_loaded "$mod" || ast "module load ${mod}.so" >/dev/null
  if module_loaded "$mod"; then
    echo "  [ok] Asterisk module $mod"
  else
    echo "  [!!] Asterisk module $mod is NOT loaded"; ok=0
    if ! module_on_disk "$mod"; then
      echo "       ${mod}.so is missing on disk: apt-get install --reinstall asterisk-modules"
    else
      echo "       the file exists but refuses to load, see: grep -i ${mod} /var/log/asterisk/full"
    fi
    [ "$mod" = func_curl ] && echo "       without func_curl every INBOUND call gets congestion"
    [ "$mod" = app_mixmonitor ] && echo "       without app_mixmonitor calls are NOT recorded"
  fi
done
if [ "$WA_ON" = 1 ]; then
  for mod in res_srtp codec_opus_open_source res_format_attr_opus res_pjsip_endpoint_identifier_ip; do
    module_loaded "$mod" || ast "module load ${mod}.so" >/dev/null
    if module_loaded "$mod"; then
      echo "  [ok] Asterisk module $mod (WhatsApp)"
    else
      echo "  [!!] Asterisk module $mod is NOT loaded: WhatsApp calls will fail"; ok=0
    fi
  done
  if case "$(ast "pjsip show endpoints")" in *wa-meta*) true ;; *) false ;; esac; then
    echo "  [ok] WhatsApp endpoint wa-meta configured"
  else
    echo "  [!!] WhatsApp endpoint wa-meta missing"; ok=0
  fi
  if timeout 10 openssl s_client -connect "$WA_SIP_HOST:5061" -servername "$WA_SIP_HOST" \
       -verify_hostname "$WA_SIP_HOST" -verify_return_error </dev/null >/dev/null 2>&1; then
    echo "  [ok] TLS 5061 answers with a valid certificate for $WA_SIP_HOST"
  else
    echo "  [!!] TLS check failed: openssl s_client -connect $WA_SIP_HOST:5061 -verify_hostname $WA_SIP_HOST"; ok=0
  fi
fi
if case "$(ast "pjsip show endpoints")" in *o-trunk*) true ;; *) false ;; esac; then
  echo "  [ok] trunk endpoint o-trunk configured"
else
  echo "  [!!] trunk endpoint missing"; ok=0
fi
if systemctl is-active --quiet voksy-bridge; then
  echo "  [ok] voksy-bridge running"
else
  echo "  [!!] voksy-bridge not running: journalctl -u voksy-bridge -n 50"; ok=0
fi
if command -v lame >/dev/null 2>&1 && [ -d /var/spool/voksy-rec ]; then
  echo "  [ok] call recording: lame installed, /var/spool/voksy-rec ready"
else
  echo "  [!!] call recording: lame or /var/spool/voksy-rec missing"; ok=0
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

  WhatsApp calls (Meta SIP)         : $( [ "$WA_ON" = 1 ] && echo "ON  -> sip:$WA_SIP_HOST:5061 (TLS, SRTP SDES, Opus)" || echo "off (VOKSY_WA_SIP_HOST=<host> to enable)" )

  Recordings waiting for upload     : ls /var/spool/voksy-rec   (empty = all uploaded)
  Logs: journalctl -u voksy-bridge -f      Asterisk CLI: asterisk -rvvv
=============================================================================
EOF
[ "$ok" = 1 ] || warn "some checks failed, see above"
