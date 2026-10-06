# claude-whatsapp-gateway — WhatsApp-шлюз агентов обзвона

Отдельный VPS с **Evolution API** (неофициальный протокол WhatsApp Web, Baileys внутри)
для переписки агентов обзвона в WhatsApp. Человеческая памятка — `SERVER.md`.

## Сервер
- Hetzner Cloud CX23, Falkenstein, имя `wa-gateway-1`, IP `91.99.124.169`, Ubuntu 24.04.
- Домен `wa.voksyai.online` (DNS домена — Namecheap BasicDNS, A-запись `wa`).
- Файрвол Hetzner `wa-gateway`: TCP 22/80/443. Бэкапы Hetzner включены.
- Не путать с SIP-шлюзом (`infra/sip-gateway/`, `178.105.79.237`) — это разные машины.

## Файлы
- `docker-compose.yml` — `evolution` (`evoapicloud/evolution-api`, версия из `EVOLUTION_VERSION`,
  по умолчанию `v2.3.7`; 2.4.x требует лицензию — не обновлять вслепую), `postgres:15`, `redis:7`,
  `caddy:2`. Наружу открыт только Caddy (80/443). Лимиты памяти на контейнеры.
- `Caddyfile` — `{$WA_DOMAIN}` → `reverse_proxy evolution:8080`, сертификат Let's Encrypt автоматически.
- `install.sh` — установка/обновление одной командой (curl из raw GitHub, ветка `VOKSY_BRANCH`,
  по умолчанию `0410-golos`). Идемпотентен; `.env` генерируется один раз (`AUTHENTICATION_API_KEY`,
  `POSTGRES_PASSWORD`) и не перезаписывается.
- На сервере всё лежит в `/opt/voksy-wa`. Конфиги руками не правим — только через репозиторий + `install.sh`.

## Связь с бэкендом (Render)
- Бэкенд → шлюз: HTTPS `WHATSAPP_GATEWAY_URL` (`https://wa.voksyai.online`), заголовок
  `apikey: WHATSAPP_GATEWAY_API_KEY` (= `AUTHENTICATION_API_KEY` из `.env` на сервере).
- Шлюз → бэкенд: webhook **на каждый instance**, задаётся бэкендом при `POST /instance/create`
  (глобальный webhook выключен, `WEBHOOK_GLOBAL_ENABLED=false`). В URL webhook'а — секрет для проверки,
  у Evolution нет подписи запросов.
- Один instance Evolution = один WhatsApp-номер = один агент. Подключение по QR (`/instance/connect/{name}`).

## Ограничения
- **Только переписка.** Голосовые звонки WhatsApp неофициальные библиотеки не поддерживают.
  Звонки — только официальный WhatsApp Business Calling API (Meta, SIP → наш Asterisk), не начато.
- Неофициальный способ нарушает правила WhatsApp → риск бана номера, особенно при первых
  сообщениях незнакомым. Нужны лимиты и паузы на стороне агента.

## Состояние (6 октября 2026)
- [x] VPS создан, DNS настроен, файлы шлюза в репозитории.
- [ ] `install.sh` запущен, переменные `WHATSAPP_GATEWAY_*` добавлены на Render.
- [ ] Коннектор на бэкенде (модель номера, webhook, инструменты агента, QR в карточке агента).
