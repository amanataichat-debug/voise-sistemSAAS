# Android-приложение Voksy AI

Настоящее Android-приложение (Trusted Web Activity): внутри тот же кабинет voksyai.online, без адресной
строки и кнопок браузера. Пакет `online.voksyai.app`, название «Voksy AI», Android 7 и новее (minSdk 24).
Проект сгенерирован [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap) из `twa-manifest.json`
(26.11.2026, решение владельца: «приложение скачивается с сайта»).

## Откуда его ставят

- **Сайт:** https://voksyai.online/app → «Скачать для Android». Файл — `backend/static/download/voksy-ai.apk`,
  маршрут `/download/voksy-ai.apk` в `app.py`. Ссылки на страницу — в подвале лендинга, в карточке
  «Приложение на телефоне», на странице Voksy Beauty и в кабинете («Установить приложение» на Android).
- **Google Play:** файл `app-release-bundle.aab` из сборки (ниже).
- **iPhone:** Apple не даёт ставить приложения с сайта — кабинет ставится «Поделиться → На экран „Домой“»
  (подсказка на `/static/login.html?install=1`). Для App Store нужен Mac с Xcode и аккаунт Apple Developer.

## Ключ подписи — только у владельца

- Ключ `voksy-ai-android.keystore` (alias `voksy-ai`) и его пароль хранятся у владельца, в репозиторий их
  **не кладём** (`.gitignore`).
- Без этого ключа нельзя выпустить обновление: Android не обновит приложение, подписанное другим ключом,
  — только удалить и поставить заново.
- Отпечаток SHA-256 ключа записан в `app.py` (`ANDROID_APP_SHA256`) и отдаётся в
  `https://voksyai.online/.well-known/assetlinks.json`. Без него Android откроет приложение с адресной строкой.
  Посмотреть отпечаток: `keytool -list -v -keystore voksy-ai-android.keystore -alias voksy-ai`.

## Сборка

Нужны JDK 17+, Android SDK (`platforms;android-36`, `build-tools;35.0.0`) и `npm i -g @bubblewrap/cli`;
в `~/.bubblewrap/config.json` — `{"jdkPath": "…", "androidSdkPath": "…"}` (в SDK нужна папка `tools`:
`ln -s cmdline-tools/latest tools`).

1. Положите ключ в эту папку под именем `android.keystore`.
2. Поднимите версию в `twa-manifest.json`: `appVersionCode` +1 и `appVersionName`.
3. Запустите `./build.sh` (спросит пароль ключа или возьмёт `BUBBLEWRAP_KEYSTORE_PASSWORD`). Скрипт
   пересоберёт проект из `twa-manifest.json`, соберёт `app-release-signed.apk` и `app-release-bundle.aab`
   и положит APK в `backend/static/download/voksy-ai.apk`.
4. Закоммитьте `backend/static/download/voksy-ai.apk` (и изменения проекта) — после деплоя на сайте новая версия.

`iconUrl` в `twa-manifest.json` указывает на локальный сервер: `build.sh` сам поднимает его на время сборки
(`python3 -m http.server 8765 --directory backend`).

## Google Play

1. Аккаунт разработчика Google Play (25 $ один раз), новое приложение «Voksy AI».
2. Загрузите `app-release-bundle.aab`, включите подпись Google Play (Play App Signing): ключ подписи хранит
   Google, ваш ключ становится ключом загрузки.
3. Play Console → «Целостность приложения» → «Подпись приложений» → скопируйте SHA-256 **ключа подписи
   приложения** и добавьте его в Render env `ANDROID_APP_SHA256` (несколько — через запятую). Без этого версия
   из Google Play откроется с адресной строкой; версия с сайта продолжит работать.
4. Политика конфиденциальности: https://voksyai.online/static/privacy-policy.html.

## Что внутри

- Старт: `/static/dashboard.html?source=twa`. Нет входа или он истёк — экран входа `/static/login.html`
  (вход, регистрация, код из письма, новый пароль) — сайт внутри приложения не открывается.
- Уведомления включены (`enableNotifications`): веб-push кабинета приходят как уведомления приложения.
- Ярлыки на значке: «Чаты», «Записи».
