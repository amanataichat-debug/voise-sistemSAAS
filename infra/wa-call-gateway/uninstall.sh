#!/usr/bin/env bash
# =============================================================================
# Откат: полностью убрать шлюз звонков WhatsApp с сервера. Другие сервисы на этом
# сервере (WhatsApp-шлюз переписки, SIP-шлюз) не затрагиваются.
#   curl -fsSL https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/2811-wa-calls/infra/wa-call-gateway/uninstall.sh | bash
# Привязка номера (сессия WhatsApp) удаляется вместе с данными — на телефоне устройство
# «Voksy AI» после этого можно просто удалить в «Связанных устройствах».
# =============================================================================
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
systemctl disable --now voksy-wa-call 2>/dev/null || true
rm -f /etc/systemd/system/voksy-wa-call.service
systemctl daemon-reload
rm -rf /opt/voksy-wa-call /etc/voksy-wa-call /var/spool/voksy-wa-call-rec
id -u voksy-wa >/dev/null 2>&1 && userdel voksy-wa || true
echo "Шлюз звонков WhatsApp удалён. В Render можно выключить WA_CALLS_ENABLED=0."
