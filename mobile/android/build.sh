#!/usr/bin/env bash
# Сборка Android-приложения Voksy AI (см. README.md): APK для сайта и AAB для Google Play.
# Нужны: android.keystore в этой папке, bubblewrap, JDK и Android SDK (~/.bubblewrap/config.json).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"

[ -f android.keystore ] || { echo "Нет android.keystore — положите ключ подписи (он у владельца) в $(pwd)"; exit 1; }
if [ -z "${BUBBLEWRAP_KEYSTORE_PASSWORD:-}" ]; then
  read -r -s -p "Пароль ключа: " BUBBLEWRAP_KEYSTORE_PASSWORD; echo
fi
export BUBBLEWRAP_KEYSTORE_PASSWORD
export BUBBLEWRAP_KEY_PASSWORD="${BUBBLEWRAP_KEY_PASSWORD:-$BUBBLEWRAP_KEYSTORE_PASSWORD}"

# Иконки берутся с локального сервера (iconUrl в twa-manifest.json)
python3 -m http.server 8765 --bind 127.0.0.1 --directory "$ROOT/backend" >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
sleep 1

(echo; echo) | bubblewrap update --skipVersionUpgrade
bubblewrap build --skipPwaValidation

cp app-release-signed.apk "$ROOT/backend/static/download/voksy-ai.apk"
echo "Готово: backend/static/download/voksy-ai.apk (сайт) и $(pwd)/app-release-bundle.aab (Google Play)"
