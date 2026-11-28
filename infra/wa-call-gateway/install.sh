#!/usr/bin/env bash
# =============================================================================
# Voksy AI — шлюз звонков WhatsApp (wa-call-1): установка и обновление.
#
# На свежем VPS (Hetzner CX22/CX23, Ubuntu 24.04) от root:
#   curl -fsSL https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/2811-wa-calls/infra/wa-call-gateway/install.sh | bash
#
# Повторный запуск — обновление: тянет код шлюза с GitHub, пересобирает, перезапускает.
# Секреты в /etc/voksy-wa-call/gateway.env и сессии WhatsApp в /opt/voksy-wa-call/data
# сохраняются. Руками на сервере ничего не правим — только этим скриптом.
#
# Переменные запуска (необязательно):
#   VOKSY_BRANCH=2811-wa-calls        ветка, с которой брать код
#   VOKSY_BACKEND_WS_URL=wss://…      адрес бэкенда (по умолчанию wss://voksyai.online)
#   VOKSY_GATEWAY_ID=wa-call-1        имя шлюза
#   VOKSY_ECHO_TEST=1|0               эхо-тест без бэкенда: шлюз сам берёт трубку и повторяет голос
#   VOKSY_SEND_PRESENCE=1|0           объявлять устройство «в сети» (если звонки не доходят без этого)
#   VOKSY_SKIP_UFW=1                  не трогать файрвол
#
# Что делает
#   1. apt: golang-go (сборщик), lame (MP3 записей), ufw, curl
#   2. скачивает infra/wa-call-gateway/gateway/* с GitHub и собирает бинарник
#      (нужная версия Go скачивается сама, с проверкой контрольных сумм Go)
#   3. системный пользователь voksy-wa, каталоги данных с правами 700/600
#   4. первый раз генерирует GATEWAY_TOKEN (его же ставим в Render как WA_CALL_GATEWAY_TOKEN)
#   5. systemd voksy-wa-call, файрвол: входящие только SSH
# =============================================================================
set -euo pipefail

BRANCH="${VOKSY_BRANCH:-2811-wa-calls}"
BASE="${VOKSY_BASE:-https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/${BRANCH}/infra/wa-call-gateway}"
BACKEND_WS_URL="${VOKSY_BACKEND_WS_URL:-wss://voksyai.online}"
GATEWAY_ID="${VOKSY_GATEWAY_ID:-wa-call-1}"

ENV_DIR=/etc/voksy-wa-call
ENV_FILE=$ENV_DIR/gateway.env
APP_DIR=/opt/voksy-wa-call
DATA_DIR=$APP_DIR/data
REC_DIR=/var/spool/voksy-wa-call-rec
SVC_USER=voksy-wa

# Все файлы, из которых собирается шлюз (тест scripts/voice_tests/test_wa_calls.py
# проверяет, что здесь перечислен каждый .go-файл из gateway/)
FILES=(
  gateway/go.mod
  gateway/go.sum
  gateway/main.go
  gateway/config.go
  gateway/logging.go
  gateway/control.go
  gateway/gateway.go
  gateway/account.go
  gateway/call.go
  gateway/audio.go
  gateway/recorder.go
  voksy-wa-call.service
  THIRD_PARTY_LICENSES.md
  uninstall.sh
  licenses/meowcaller-MIT.txt
  licenses/hypermeow-MPL-2.0.txt
)

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || warn "tested on Ubuntu 24.04, you are on ${PRETTY_NAME:-unknown}"

say "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq golang-go lame curl ca-certificates >/dev/null
command -v ufw >/dev/null 2>&1 || apt-get install -y -qq ufw >/dev/null

say "downloading gateway source from $BASE"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for f in "${FILES[@]}"; do
  mkdir -p "$TMP/$(dirname "$f")"
  curl -fsSL "$BASE/$f" -o "$TMP/$f" || die "download failed: $f"
done

say "building (the Go version from go.mod is fetched automatically)"
export GOTOOLCHAIN=auto GOFLAGS=-mod=readonly CGO_ENABLED=0
export GOPATH=/root/go GOCACHE=/root/.cache/go-build
( cd "$TMP/gateway" && go build -trimpath -ldflags "-s -w" -o "$TMP/voksy-wa-call" . ) || die "build failed"
"$TMP/voksy-wa-call" --version

# ---------------------------------------------------------------- user, dirs
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SVC_USER"
install -d -m 755 "$APP_DIR" "$APP_DIR/bin"
install -d -m 700 -o "$SVC_USER" -g "$SVC_USER" "$DATA_DIR" "$REC_DIR"
# сессии WhatsApp — только владельцу сервиса
find "$DATA_DIR" -type f -exec chmod 600 {} + 2>/dev/null || true
chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR" "$REC_DIR"

# ---------------------------------------------------------------- secrets / env
mkdir -p "$ENV_DIR"
chmod 755 "$ENV_DIR"
FIRST_INSTALL=0
if [ ! -f "$ENV_FILE" ]; then
  FIRST_INSTALL=1
  say "generating secrets (first install)"
  GATEWAY_TOKEN="$(openssl rand -hex 24)"
  cat > "$ENV_FILE" <<EOF
# Voksy AI WhatsApp call gateway — environment. Generated $(date -u +%FT%TZ)
BACKEND_WS_URL=$BACKEND_WS_URL
GATEWAY_ID=$GATEWAY_ID
# То же значение ставится в Render как WA_CALL_GATEWAY_TOKEN
GATEWAY_TOKEN=$GATEWAY_TOKEN
DATA_DIR=$DATA_DIR
RECORDINGS_DIR=$REC_DIR

# Одновременных звонков на шлюз (сверх — входящий отклоняется, исходящий ждёт в очереди бэкенда)
MAX_CALLS=10
# Сторож: звонок длиннее этого кладётся
MAX_CALL_SECONDS=3600
# Сколько ждать решения бэкенда по входящему, мс
ACCEPT_TIMEOUT_MS=6000
# Исходящий: сколько ждать ответа, с
OUTBOUND_RING_SECONDS=45
# Записывать звонки (MP3 → бэкенд → R2, как SIP)
RECORD_CALLS=1
# Эхо-тест без бэкенда (этап 1): 1 — шлюз сам отвечает и повторяет голос звонящего
ECHO_TEST=0
# Объявлять устройство «в сети» (1 — только если входящие звонки не доходят до шлюза без этого)
SEND_PRESENCE=0
# Имя связанного устройства в WhatsApp на телефоне
DEVICE_NAME=Voksy AI
LOG_LEVEL=INFO
WA_LOG_LEVEL=WARN
EOF
else
  say "keeping existing $ENV_FILE"
  add_env() {
    grep -q "^$1=" "$ENV_FILE" || { printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; say "added $1=$2"; }
  }
  add_env MAX_CALLS 10
  add_env MAX_CALL_SECONDS 3600
  add_env ACCEPT_TIMEOUT_MS 6000
  add_env OUTBOUND_RING_SECONDS 45
  add_env RECORD_CALLS 1
  add_env ECHO_TEST 0
  add_env SEND_PRESENCE 0
fi
if [ -n "${VOKSY_SEND_PRESENCE:-}" ]; then
  case "$VOKSY_SEND_PRESENCE" in 1|true|yes) v=1 ;; *) v=0 ;; esac
  sed -i "s|^SEND_PRESENCE=.*|SEND_PRESENCE=$v|" "$ENV_FILE"
  say "SEND_PRESENCE=$v"
fi
if [ -n "${VOKSY_ECHO_TEST:-}" ]; then
  case "$VOKSY_ECHO_TEST" in 1|true|yes) v=1 ;; *) v=0 ;; esac
  sed -i "s|^ECHO_TEST=.*|ECHO_TEST=$v|" "$ENV_FILE"
  say "ECHO_TEST=$v"
fi
chmod 600 "$ENV_FILE"

# ---------------------------------------------------------------- install
install -m 755 "$TMP/voksy-wa-call" "$APP_DIR/bin/voksy-wa-call"
install -m 644 "$TMP/THIRD_PARTY_LICENSES.md" "$APP_DIR/THIRD_PARTY_LICENSES.md"
install -m 755 "$TMP/uninstall.sh" "$APP_DIR/uninstall.sh"
install -d -m 755 "$APP_DIR/licenses"
install -m 644 "$TMP"/licenses/*.txt "$APP_DIR/licenses/"
install -m 644 "$TMP/voksy-wa-call.service" /etc/systemd/system/voksy-wa-call.service

# ---------------------------------------------------------------- firewall: входящие только SSH
# На ОБЩЕМ сервере (рядом с WhatsApp-шлюзом переписки или SIP-шлюзом) файрвол не трогаем:
# «закрыть всё входящее» отрезало бы Caddy (80/443) или SIP/RTP. Шлюзу звонков входящие порты
# не нужны вовсе — все его соединения исходящие.
SHARED_HOST=0
for marker in /opt/voksy-wa /etc/voksy-bridge /etc/asterisk/pjsip.conf; do
  [ -e "$marker" ] && SHARED_HOST=1
done
command -v docker >/dev/null 2>&1 && SHARED_HOST=1
if [ "$SHARED_HOST" = 1 ]; then
  say "shared server detected (other Voksy services here): firewall is left as is"
elif [ "${VOKSY_SKIP_UFW:-0}" != 1 ]; then
  say "firewall: inbound SSH only (all gateway connections are outbound)"
  ufw allow OpenSSH >/dev/null
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw --force enable >/dev/null
fi

systemctl daemon-reload
systemctl enable voksy-wa-call >/dev/null 2>&1
systemctl restart voksy-wa-call
sleep 2
systemctl --no-pager --lines=0 status voksy-wa-call || true

say "done"
ECHO_NOW="$(grep '^ECHO_TEST=' "$ENV_FILE" | cut -d= -f2)"
if [ "$FIRST_INSTALL" = 1 ]; then
  cat <<EOF

  Шлюз установлен. Дальше:
    1. В Render поставьте переменную WA_CALL_GATEWAY_TOKEN = $(grep '^GATEWAY_TOKEN=' "$ENV_FILE" | cut -d= -f2)
       и WA_CALLS_ENABLED=1, WA_CALLS_USERS=<id владельца>  (деплой бэкенда — сам)
    2. В Render Shell:  python scripts/wa_call_account.py create --agent <id агента>
       — QR-код появится там же (и в журнале шлюза ниже).
    Журнал шлюза:  journalctl -u voksy-wa-call -f
EOF
fi
if [ "$ECHO_NOW" = 1 ]; then
  cat <<EOF

  ЭХО-ТЕСТ включён: бэкенд не нужен. QR-код для привязки номера — в журнале:
    journalctl -u voksy-wa-call -f
  Отсканируйте его в WhatsApp тестового номера (Связанные устройства → Привязать) и
  позвоните на этот номер с другого телефона — шлюз ответит и повторит ваш голос.
  Выключить:  VOKSY_ECHO_TEST=0 bash install.sh
EOF
fi
