# Памятка: WhatsApp-шлюз Voksy AI на VPS

Простыми словами: что это за сервер, зачем он нужен и что на нём делать.

## Что это за сервер

| Параметр | Значение |
|---|---|
| Роль | Подключение WhatsApp-номеров агентов обзвона для переписки (неофициальный способ, как WhatsApp Web) |
| Хостинг | Hetzner Cloud, сервер **CX23** (2 vCPU, 4 ГБ), дата-центр **Falkenstein** (Германия) |
| Имя в Hetzner | `wa-gateway-1` |
| Публичный IP | `91.99.124.169` |
| Домен | `wa.voksyai.online` (A-запись в Namecheap → Advanced DNS) |
| ОС | Ubuntu 24.04 |
| Файрвол Hetzner | `wa-gateway`: входящие TCP 22, 80, 443 |
| Бэкапы | Включены в Hetzner (ежедневно) |
| SSH-ключ | Тот же, что у SIP-сервера (`C:\Users\<вы>\.ssh\id_ed25519`) |
| Что установлено | Docker: Evolution API v2.3.7 + Postgres + Redis + Caddy (HTTPS) |
| Где лежит | `/opt/voksy-wa` (`docker-compose.yml`, `Caddyfile`, `.env` с секретами) |
| Кто ставил | Скрипт `install.sh` из этой папки, одной командой |

Это **отдельный** сервер от SIP-шлюза (`178.105.79.237`): проблемы с WhatsApp
не задевают телефонию.

Зачем отдельный сервер: подключение к WhatsApp должно жить постоянно, по одному
на номер. На Render так нельзя: 4 воркера gunicorn и перезапуск при каждом деплое
рвали бы сессии.

Схема:

```
Телефон клиента (WhatsApp) ⇄ серверы WhatsApp ⇄ [VPS 91.99.124.169]
    Evolution API (связанное устройство, по номеру на агента)
        ├─ входящие сообщения → webhook → https://voksyai.online/api/whatsapp/... (Render)
        └─ ← отправка сообщений от бэкенда: https://wa.voksyai.online (API-ключ)
```

Звонки через этот шлюз **невозможны**: неофициальные библиотеки не умеют
голосовые звонки WhatsApp. Для звонков нужен официальный WhatsApp Business
Calling API (Meta) по SIP в наш Asterisk — отдельная задача.

## Риск бана

Это неофициальный способ, он нарушает правила WhatsApp. Номер могут заблокировать,
особенно если агент сам пишет незнакомым людям. Клиент подключает свой номер на
свой риск; в агенте нужны лимиты на новые чаты в день и паузы между сообщениями.

## Как подключиться

```powershell
ssh root@91.99.124.169
```

## Установка и обновление

Одна и та же команда (на сервере под root):

```bash
curl -fsSL https://raw.githubusercontent.com/amanataichat-debug/voise-sistemSAAS/0410-golos/infra/whatsapp-gateway/install.sh | bash
```

Скрипт ставит Docker, скачивает файлы из GitHub, при первом запуске создаёт
`/opt/voksy-wa/.env` с API-ключом и паролем БД, запускает контейнеры и в конце
печатает значения для Render:

```
WHATSAPP_GATEWAY_URL=https://wa.voksyai.online
WHATSAPP_GATEWAY_API_KEY=<ключ>
```

Повторный запуск секреты не меняет. Руками конфиги на сервере не правим:
меняем в репозитории, пушим, запускаем `install.sh` ещё раз.

## Полезные команды

```bash
cd /opt/voksy-wa
docker compose ps                       # что запущено
docker compose logs -f evolution        # логи Evolution API
docker compose logs --tail 80 caddy     # проблемы с HTTPS-сертификатом
docker compose restart evolution        # перезапуск
grep AUTHENTICATION_API_KEY .env        # посмотреть API-ключ
```

Проверка снаружи: `https://wa.voksyai.online/` отвечает JSON «Welcome to the
Evolution API». Веб-панель для проверки номеров: `https://wa.voksyai.online/manager`
(вход по API-ключу).

## Если что-то не так

- **Нет HTTPS / сертификата** — проверить, что `wa.voksyai.online` указывает на
  `91.99.124.169` и порт 80 открыт в файрволе Hetzner; `docker compose logs caddy`.
- **QR-код не появляется или номер сразу отключается** — часто WhatsApp не
  принимает старую версию протокола. Вариант: обновить `EVOLUTION_VERSION` в
  `.env` на новую стабильную 2.3.x (2.4.x требует лицензию) или задать
  `CONFIG_SESSION_PHONE_VERSION`, затем `docker compose up -d`.
- **Номер забанили** — это не ошибка сервера; номер нужно восстанавливать в самом
  WhatsApp на телефоне.
