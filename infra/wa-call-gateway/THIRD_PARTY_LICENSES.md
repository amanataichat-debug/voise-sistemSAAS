# Сторонние библиотеки шлюза звонков WhatsApp

Шлюз (`gateway/`) собирается из исходников на сервере (`install.sh`) и не вносит
изменений в код библиотек. Лицензии разрешают коммерческое использование; их тексты
лежат в `licenses/`, авторство сохраняется. Версии зафиксированы в `gateway/go.mod`
и `gateway/go.sum`.

| Библиотека | Зачем | Лицензия | Автор |
|---|---|---|---|
| [purpshell/meowcaller](https://github.com/purpshell/meowcaller) | звонки WhatsApp (сигнализация, ключи, медиа, кодек MLow) | MIT — `licenses/meowcaller-MIT.txt` | © 2026 Rajeh Taher |
| [polymorfa/hypermeow](https://github.com/polymorfa/hypermeow) | протокол WhatsApp Web: связанное устройство, вход по QR, хранение сессии. Форк [tulir/whatsmeow](https://github.com/tulir/whatsmeow) с теми же именами пакетов; meowcaller построен на нём | MPL 2.0 — `licenses/hypermeow-MPL-2.0.txt` | Tulir Asokan и участники; Polymorfa |
| coder/websocket | WebSocket к бэкенду | ISC | © Coder |
| modernc.org/sqlite | SQLite без CGO (сессии) | BSD-3-Clause | The Sqlite Authors |
| mdp/qrterminal | QR-код в журнале (эхо-тест, первый вход) | MIT | © Mark Percival |
| rs/zerolog | логи библиотеки звонков | MIT | © Olivier Poitrey |
| google/uuid | id звонков | BSD-3-Clause | © Google Inc. |

MPL 2.0 — файловый копилефт: если когда-нибудь меняем файлы hypermeow, изменённые
файлы публикуются под MPL 2.0. Сейчас код библиотек не меняется.

meowcaller и hypermeow — независимые проекты, не связанные с Meta/WhatsApp
(см. DISCLAIMER meowcaller). Протокол неофициальный: есть риск блокировки номера,
поэтому в кабинете клиент видит честное предупреждение, а на пробе используется
отдельный номер.
