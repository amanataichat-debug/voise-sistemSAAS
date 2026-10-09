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

## Бэкенд-коннектор (ветка `0410-golos`)
- Модели `backend/models/agent_whatsapp.py`: аккаунт (один на агента = один instance), чаты, сообщения.
- `backend/services/whatsapp_service.py` — клиент Evolution API + история; `backend/services/whatsapp_inbound.py` — webhook'и.
- `backend/api/agent_whatsapp.py` — `/api/agent/whatsapp/*` (подключение по QR, настройки) и
  `POST /api/whatsapp/webhook/{account_id}` (заголовок `X-Voksy-Token`).
- Оркестратор: `handle_inbound_whatsapp` → `PostCallOrchestrator.run_for_whatsapp` (`call_direction="whatsapp_inbound"`),
  WhatsApp в общей хронологии контакта, канал `whatsapp` у `AgentCall`.
- Инструменты агента (чат владельца и PostCall, только если номер подключён): `whatsapp_send_message`
  (ответ или **первое сообщение** по `agent_contact_id` / `phone`; контакт создаётся при необходимости),
  `whatsapp_send_file`, `whatsapp_get_thread`.
- Анти-бан: `WA_SEND_HOURLY_LIMIT`=40 исходящих в час, `daily_new_chats_limit` (по умолчанию 20) новых чатов в сутки,
  проверка номера (`/chat/whatsappNumbers`), пауза «печатает…» (`typing_delay_ms`).
- Входящие: серия сообщений склеивается (8 с тишины) в один прогон оркестратора; голосовые/фото/документы
  распознаются `agent_media_service` (`channel="whatsapp"`).
- UI: `backend/static/agent/whatsapp-account.js` (строка «WhatsApp» в коннекторах агента, QR-модалка),
  тред в карточке контакта, бейдж в истории.

## Состояние (6 октября 2026)
- [x] VPS создан, DNS настроен, `install.sh` запущен, шлюз отвечает на `https://wa.voksyai.online`.
- [x] Коннектор на бэкенде и UI (код в ветке `0410-golos`).
- [ ] `WHATSAPP_GATEWAY_URL` / `WHATSAPP_GATEWAY_API_KEY` на Render, проверка на живом номере.
- [ ] Отложенные сообщения WhatsApp (аналог `schedule_telegram_message`) — не сделаны.
