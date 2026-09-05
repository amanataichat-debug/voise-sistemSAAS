# Voksy AI (WellcomeAI) — SaaS Voice AI Platform

## Overview

Voksy AI is a SaaS platform for creating and managing AI-powered voice assistants. Users can build conversational agents using OpenAI Realtime API, Google Gemini Live, Fish Audio (OpenAI text + Fish TTS), xAI Grok Voice, and ElevenLabs — then connect them to telephony (own SIP gateway, see below) or embed as web widgets. The platform includes a CRM, knowledge base, conversation analytics, partner program, and subscription billing.

## ⚠️ Voximplant is NOT used (read this first)

Telephony runs **only** through our own SIP gateway (`infra/sip-gateway/`: Hetzner VPS with Asterisk + `bridge.py` → `/ws/sip/{call_id}` on the backend → the same voice handlers as the web widget). Voximplant is switched off: no account, no scenarios deployed, no calls go through it.

The Voximplant code is still in the tree and is **dead code awaiting removal**. Do not fix, extend or document it as a working path, and never route a new feature through it:

- `backend/api/voximplant.py`, `backend/api/voximplant_settings.py`, `backend/api/telephony.py` (number binding, scenario deployment, `/config`, `/outbound-config`)
- `backend/services/voximplant_partner.py`, `backend/models/voximplant_child.py`, `User.voximplant_*` columns, `VOXIMPLANT_*` settings
- `backend/websockets/voximplant_handler.py`, `voximplant_adapter.py`, `handler_vox_gemini.py`, `handler_fish_tts.py` (old Fish TTS proxy for VoxEngine)
- `voximplant_scenarios/`, `.claude/skills/voximplant-*`, `backend/static/telephony.html`, `outbound-calls.html`
- fallbacks in `backend/core/task_scheduler.py` (`_execute_via_partner_api`, `_execute_via_legacy_api`) and Voximplant number handling in `backend/api/agent.py`
- `send_sms` function (Voximplant Management API) — has no working transport now

Assistant types that existed only as VoxEngine scenarios (`cascade`, `cartesia`, `yandex`) do not work anymore and are being re-implemented as backend handlers one by one, the way Fish was (see **Fish assistants** below). A new provider = a handler in `backend/websockets/` speaking the widget protocol + registration in `SIP_HANDLERS` (`backend/api/sip_gateway.py`), `SIP_SUPPORTED_ASSISTANT_TYPES` and `HANDLER_IN_RATE`.

**Production URL:** https://voksyai.online
**Version:** 3.0.0
**Python:** 3.10.11
**Hosting:** Render (Frankfurt region)

## Tech Stack

- **Backend:** FastAPI + Uvicorn + Gunicorn (Python 3.10)
- **Database:** PostgreSQL (via SQLAlchemy 2.x ORM, Alembic migrations)
- **Frontend (landing):** React + Vite (builds to `backend/static/landing/`)
- **Frontend (app pages):** Vanilla HTML/CSS/JS in `backend/static/`
- **WebSocket:** Native FastAPI WebSocket for real-time voice streaming
- **Storage:** Cloudflare R2 (S3-compatible)
- **Vector DB:** Pinecone (knowledge base search)
- **External APIs:** OpenAI, Google Gemini, Fish Audio, xAI Grok, ElevenLabs, Finik (payments, KGS); telephony — own SIP gateway (Asterisk) with operator O!

## Project Structure

```
├── main.py                  # Entry point, Gunicorn/Uvicorn setup, import redirect
├── app.py                   # FastAPI app init, middleware, routes, startup events
├── gunicorn_config.py       # Gunicorn production config
├── render.yaml              # Render deployment config
├── requirements.txt         # Python dependencies
├── alembic/                 # Database migrations
│   ├── env.py
│   └── versions/            # Migration scripts
├── backend/
│   ├── api/                 # API route handlers (FastAPI routers)
│   │   ├── auth.py          # JWT auth (register, login, token refresh)
│   │   ├── users.py         # User profile and settings
│   │   ├── assistants.py    # OpenAI assistant CRUD
│   │   ├── gemini_assistants.py  # Gemini assistant CRUD
│   │   ├── grok_assistants.py    # Grok assistant CRUD
│   │   ├── elevenlabs.py    # ElevenLabs agent management
│   │   ├── websocket.py     # OpenAI Realtime WebSocket proxy
│   │   ├── gemini_ws.py     # Gemini Live WebSocket proxy
│   │   ├── grok_ws.py       # Grok Voice WebSocket proxy
│   │   ├── telephony.py     # Outbound calls, call scheduling
│   │   ├── voximplant.py    # DEAD: Voximplant integration (not used, see warning above)
│   │   ├── fish_assistants.py # Fish assistant CRUD (/api/fish-assistants)
│   │   ├── fish_ws.py       # Fish voice WS: /ws/fish/{id} (OpenAI Realtime text + Fish TTS)
│   │   ├── sip_gateway.py   # Own SIP telephony: bridge WS (/ws/sip-gateway/control, /ws/sip/{id}) + /api/sip/*
│   │   ├── conversations.py # Conversation history and analytics
│   │   ├── contacts.py      # CRM contacts management
│   │   ├── knowledge_base.py # Knowledge base (Pinecone)
│   │   ├── payments.py      # Finik payment processing (create + webhook)
│   │   ├── subscriptions.py # Subscription plan management
│   │   ├── partners.py      # Partner/referral program
│   │   ├── embeds.py        # Embeddable widget pages
│   │   ├── functions.py     # Custom function management
│   │   └── admin.py         # Admin panel endpoints
│   ├── core/                # App core
│   │   ├── config.py        # Pydantic settings (env vars)
│   │   ├── security.py      # JWT token creation/validation
│   │   ├── dependencies.py  # FastAPI dependencies (get_current_user, etc.)
│   │   ├── scheduler.py     # Subscription expiry checker
│   │   ├── task_scheduler.py # Automated call task scheduler
│   │   └── logging.py       # Logging configuration
│   ├── models/              # SQLAlchemy ORM models
│   │   ├── user.py          # User model
│   │   ├── assistant.py     # OpenAI AssistantConfig
│   │   ├── gemini_assistant.py  # GeminiAssistantConfig
│   │   ├── grok_assistant.py    # GrokAssistantConfig
│   │   ├── fish_assistant.py    # FishAssistantConfig + FishConversation (fish_conversations)
│   │   ├── elevenlabs.py    # ElevenLabsAgent, ElevenLabsConversation
│   │   ├── conversation.py  # Conversation model
│   │   ├── contact.py       # CRM Contact model
│   │   ├── subscription.py  # Subscription, SubscriptionPlan
│   │   ├── task.py          # Scheduled call tasks
│   │   ├── sip_gateway.py   # SipPhoneNumber, SipCall (own SIP telephony)
│   │   ├── partner.py       # Partner referral model
│   │   ├── embed_config.py  # Embeddable widget config
│   │   └── ...
│   ├── schemas/             # Pydantic request/response schemas
│   ├── services/            # Business logic layer
│   │   ├── auth_service.py          # Authentication logic
│   │   ├── assistant_service.py     # OpenAI assistant operations
│   │   ├── conversation_service.py  # Conversation CRUD
│   │   ├── elevenlabs_service.py    # ElevenLabs API client
│   │   ├── google_sheets_service.py # Google Sheets integration
│   │   ├── payment_service.py       # Finik post-payment business logic
│   │   ├── finik_service.py         # Finik API client (RSA signing, 302→Location, webhook verify)
│   │   ├── pinecone_service.py      # Pinecone vector search
│   │   ├── r2_storage.py            # Cloudflare R2 file storage
│   │   ├── partner_service.py       # Partner program logic
│   │   ├── sip_gateway_service.py   # SIP gateway: outbound queue, bridge events, conversation tagging
│   │   ├── telegram_notification.py # Telegram notifications
│   │   ├── notification_service.py  # General notifications
│   │   └── llm_streaming/          # LLM streaming utilities
│   ├── functions/           # Modular AI function calling system
│   │   ├── base.py          # Base function class
│   │   ├── registry.py      # Function discovery and registry
│   │   ├── add_google_sheet_row.py
│   │   ├── search_pinecone.py
│   │   ├── send_telegram_notification.py
│   │   ├── send_webhook.py
│   │   ├── query_llm.py
│   │   ├── hangup_call.py
│   │   ├── get_current_time.py
│   │   ├── create_crm_voicyfy_task.py
│   │   ├── api_request.py
│   │   ├── read_google_doc.py
│   │   └── start_browser_task.py
│   ├── websockets/          # WebSocket handlers for real-time voice
│   │   ├── handler.py               # OpenAI Realtime handler
│   │   ├── handler_gemini.py        # Gemini Live handler
│   │   ├── handler_grok.py          # Grok Voice handler
│   │   ├── openai_client.py         # OpenAI WS client
│   │   ├── gemini_client.py         # Gemini WS client
│   │   ├── grok_client.py           # Grok WS client
│   │   ├── sip_media_adapter.py     # HandlerSocket: SIP bridge audio <-> browser handler protocol
│   │   ├── handler_fish.py          # Fish handler: OpenAI Realtime (text) + Fish Audio TTS, widget protocol
│   │   ├── fish_llm_client.py       # Text-mode OpenAI Realtime client for Fish (server VAD, transcription, tools)
│   │   ├── fish_tts_client.py       # Fish Audio live TTS client (msgpack, barge-in via reconnect)
│   │   ├── voximplant_handler.py    # DEAD: Voximplant WS bridge
│   │   ├── voximplant_adapter.py    # DEAD: Voximplant audio adapter
│   │   └── sentence_detector.py     # Sentence boundary detection
│   ├── utils/               # Utility modules
│   ├── db/                  # Database session management
│   └── static/              # All frontend HTML/CSS/JS pages
│       ├── landing/         # React landing page (built)
│       ├── agents.html      # OpenAI agents management page
│       ├── gemini-agents.html   # Gemini agents page
│       ├── grok-agents.html     # Grok agents page
│       ├── fish-agents.html     # Fish agents page (server keys, browser test button)
│       ├── fish-test.html       # Browser test of a Fish agent: widget.js with data-ws-path="/ws/fish/"
│       ├── dashboard.html       # User dashboard
│       ├── telephony.html       # Telephony settings
│       ├── conversations.html   # Conversation history
│       ├── crm.html             # CRM contacts list
│       ├── crm-contact.html     # Individual contact view
│       ├── knowledge-base.html  # Knowledge base management
│       ├── settings.html        # User settings
│       ├── admin.html           # Admin panel
│       ├── agents/              # JS modules for agents page
│       │   ├── index.js         # Main agents logic
│       │   ├── api.js           # API client
│       │   └── ui.js            # UI rendering
│       └── js/                  # Shared JS modules
├── frontend/                # React landing page source
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/      # Navbar, Footer, PricingSection, etc.
│   │   ├── hooks/           # useAuth, useEmailVerification, useReferralTracker
│   │   └── utils/           # api.js, notifications.js
│   ├── package.json
│   └── vite.config.js
├── chrome-extension/        # Chrome extension (side panel + popup)
│   ├── manifest.json
│   ├── background.js
│   ├── popup/
│   └── sidepanel/
└── infra/
    └── sip-gateway/         # Own SIP telephony on a VPS (Asterisk + Python bridge). See claude-sip-gateway.md, SERVER.md
        ├── install.sh       # One-command install/update on the VPS (fetches this folder from raw GitHub)
        ├── asterisk/        # pjsip.conf (operator trunk + test softphone), extensions.conf, rtp.conf, manager.conf, modules.conf
        └── bridge/          # bridge.py (AudioSocket <-> backend WebSocket, AMI originate), systemd unit
```

## Running the Project

### Local Development
```bash
pip install -r requirements.txt
# Set env vars in .env (DATABASE_URL, OPENAI_API_KEY, JWT_SECRET_KEY, etc.)
python main.py
# Server starts at http://localhost:5050
```

### Production (Render)
```bash
gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:$PORT main:application
```

### Frontend Landing (development)
```bash
cd frontend && npm install && npm run dev
# Build: npm run build (outputs to backend/static/landing/)
```

### ⚠️ ОБЯЗАТЕЛЬНО: пересборка лендинга после правок `frontend/`

Render собирает **только Python** (`buildCommand: pip install -r requirements.txt` в `render.yaml`).
`npm run build` при деплое **не запускается**. Прод отдаёт закоммиченный бандл из
`backend/static/landing/` (см. `app.py` → `FileResponse("backend/static/landing/index.html")`).

Поэтому любые изменения в `frontend/src/**` **не попадут на прод**, пока бандл не пересобран
и не закоммичен. Это уже приводило к тому, что лендинг месяц показывал устаревший контент.

После **любой** правки в `frontend/`:
```bash
cd frontend && npm ci && npm run build
cd .. && git add -A backend/static/landing frontend
```
Имена ассетов хешированные (`index-<hash>.js`), Vite чистит `outDir` — старый файл
удаляется, новый добавляется, `index.html` обновляет ссылки. Все три изменения
(удаление старого JS, новый JS, изменённый `index.html`) должны попасть в коммит.

Проверка перед коммитом — в `git status` рядом с правками в `frontend/src/**`
обязаны быть изменения в `backend/static/landing/`. Если их нет — сборка не выполнена.

## Key API Prefixes

| Prefix | Description |
|--------|-------------|
| `/api/auth` | Authentication (register, login, refresh) |
| `/api/users` | User profile, settings |
| `/api/assistants` | OpenAI assistant CRUD |
| `/api/gemini-assistants` | Gemini assistant CRUD |
| `/api/grok-assistants` | Grok assistant CRUD |
| `/api/elevenlabs` | ElevenLabs agents |
| `/api/telephony` | Outbound calls, call tasks |
| `/api/voximplant` | DEAD — Voximplant (not used) |
| `/api/fish-assistants` | Fish assistant CRUD, `/options`, `/status` (server keys configured?) |
| `/api/conversations` | Conversation history |
| `/api/contacts` | CRM contacts |
| `/api/knowledge-base` | Knowledge base (Pinecone) |
| `/api/payments` | Finik payments (KGS) |
| `/api/subscriptions` | Subscription plans |
| `/api/partners` | Partner referral program |
| `/api/embeds` | Embeddable widget configs |
| `/api/functions` | Custom AI functions |
| `/ws/openai/{id}` | OpenAI Realtime voice WS |
| `/ws/gemini/{id}` | Gemini Live voice WS |
| `/ws/grok/{id}` | Grok Voice WS |
| `/ws/fish/{id}` | Fish voice WS (widget protocol; OpenAI text brain + Fish TTS) |
| `/api/sip` | Own SIP telephony: numbers, call journal, manual outbound (`/api/sip/numbers`, `/api/sip/calls`, `/api/sip/gateways`) |
| `/ws/sip-gateway/control` | Control socket from the VPS bridge (auth by `SIP_GATEWAY_TOKEN`) |
| `/ws/sip/{call_id}` | Per-call media socket from the VPS bridge (PCM16 8 kHz) |

## Database

PostgreSQL with SQLAlchemy ORM. Migrations managed by Alembic (`alembic/versions/`).

Key tables: `users`, `assistant_configs`, `gemini_assistant_configs`, `grok_assistant_configs`, `fish_assistant_configs`, `elevenlabs_agents`, `conversations`, `gemini_conversations`, `fish_conversations`, `contacts`, `tasks`, `subscription_plans`, `user_subscriptions`, `embed_configs`, `partners`, `sip_phone_numbers`, `sip_calls`.

`conversations.assistant_id` is a FK to `assistant_configs` (OpenAI), so Gemini dialogs go to `gemini_conversations` and Fish dialogs to `fish_conversations`; the "Диалоги" page unions all three tables (`backend/api/conversations.py`), and `SipGatewayService.tag_conversations` picks the table by `assistant_type`.

## Environment Variables (Key)

- `DATABASE_URL` — PostgreSQL connection string
- `OPENAI_API_KEY` — OpenAI API key (server-level, users can also set their own; Fish assistants always use the server key)
- `FISH_API_KEY` — Fish Audio API key (server-level; Fish assistants never use user keys)
- `JWT_SECRET_KEY` — JWT signing secret
- `HOST_URL` — Public URL (e.g., https://voksyai.online)
- `PRODUCTION` — "true" in production (disables docs, enables optimizations)
- `CORS_ORIGINS` — Allowed CORS origins
- `FINIK_API_KEY` — Finik API key (QR-эквайринг, валюта KGS)
- `FINIK_API_URL` — Finik API base URL (prod: https://api.acquiring.averspay.kg, beta: https://beta.api.acquiring.averspay.kg)
- `FINIK_PRIVATE_PEM` — приватный RSA-ключ мерчанта (содержимое .pem целиком)
- `FINIK_ACCOUNT_ID` — ID счёта Finik для зачисления средств
- `FINIK_PUBLIC_KEY` — публичный ключ Finik для проверки подписи webhook'ов (опционально до выдачи)
- `FINIK_VERIFY_WEBHOOK_SIGNATURE` — проверять подпись webhook'ов (default "True")
- `SIP_GATEWAY_TOKEN` — shared secret with the VPS SIP bridge (equals `GATEWAY_TOKEN` in `/etc/voksy-bridge/bridge.env` on the VPS)
- `SIP_GATEWAY_DEFAULT_ID` — gateway id used for outbound calls (default `sip-gw-1`)
- `GEMINI_VAD_PROFILE` / `GEMINI_VAD_START_SENSITIVITY` / `GEMINI_VAD_END_SENSITIVITY` / `GEMINI_VAD_SILENCE_MS` — Gemini Live speech detection profile, same for widget and telephony (defaults: `fast`, `low`, `high`, `500`)

Users provide their own API keys for: OpenAI (OpenAI assistants), Google Gemini, xAI Grok, ElevenLabs. Fish assistants run on server keys only.

## Architecture Notes

- **Import redirection:** `main.py` contains a custom `MetaPathFinder` that redirects bare module imports (e.g., `core.config`) to `backend.core.config`. This allows modules to work both standalone and within the backend package.
- **Modular functions:** `backend/functions/` uses a registry pattern — new AI-callable functions are auto-discovered at startup via `discover_functions()`.
- **Multi-provider voice:** The WebSocket layer abstracts the voice providers (OpenAI, Gemini, Fish, Grok) behind handlers with one client protocol (the "widget protocol": `input_audio_buffer.append` in, `response.audio.delta` 24 kHz out, `speech.started` / `conversation.interrupted` / `assistant.speech.*` / `function_call.*` events). Anything speaking that protocol works in the widget and on the phone.
- **Own SIP telephony:** a Hetzner VPS (`178.105.79.237`, Asterisk 20 + `infra/sip-gateway/bridge/bridge.py`) terminates the operator's SIP trunk and streams call audio to the backend over outbound WebSockets. On the backend `backend/websockets/sip_media_adapter.py` wraps the *same* browser handlers (OpenAI, Gemini, Fish — map `SIP_HANDLERS` in `backend/api/sip_gateway.py`), so phone calls and the widget share functions, transcripts, conversation saving and behaviour. Rule: telephony and widget must behave the same. Outbound calls are queued in `sip_calls` and picked up by the worker that holds the control socket. Full picture: `infra/sip-gateway/claude-sip-gateway.md`; server how-to: `infra/sip-gateway/SERVER.md`.
- **Fish assistants (half-cascade on server keys):** `backend/websockets/handler_fish.py`. OpenAI Realtime `gpt-realtime-2` in text-only mode (`fish_llm_client.py`: server VAD, input transcription, tools) is the brain; Fish Audio live TTS (`fish_tts_client.py`, msgpack, PCM16 24 kHz) is the voice. Text deltas are cut into sentences (`sentence_detector.py`) and sent to Fish; the greeting goes to Fish directly and is added to the OpenAI context as an assistant message. Barge-in = `response.cancel` + Fish reconnect (Fish has no cancel). Functions reuse `execute_and_send_function_result` from the OpenAI handler; `hangup_call` is handled by `HandlerSocket`. Keys: `OPENAI_API_KEY` + `FISH_API_KEY` from env only. Dialogs → `fish_conversations`. Browser test: `/static/fish-test.html?id=<uuid>` (widget.js with `data-ws-path="/ws/fish/"`). Billing gate (cascade credits) is planned, not implemented yet.
- **Startup schema fixes:** `app.py` startup event runs comprehensive schema checks and auto-adds missing columns for backwards compatibility.
- **Task scheduler:** Background scheduler (`core/task_scheduler.py`) polls for scheduled call tasks every 30 seconds and executes them automatically.
- **Static pages:** App pages (agents, dashboard, CRM, etc.) are vanilla HTML/JS served by FastAPI's `StaticFiles`. The React app is only used for the landing page.

## Development Workflow (current)

- Work happens on branch `0509-v1-sip-good` (SIP telephony + Fish on server keys; branched from `2308-agent-v2`). Commit and push there; no pull requests unless asked. `infra/sip-gateway/install.sh` still defaults to `VOKSY_BRANCH=2308-agent-v2` — pass `VOKSY_BRANCH=0509-v1-sip-good` when updating the VPS from this branch.
- The SIP gateway VPS is updated from GitHub: after changing anything in `infra/sip-gateway/`, commit, push, then run `install.sh` on the VPS (see `infra/sip-gateway/SERVER.md`). Never edit configs on the server by hand.
- Render deploys the backend automatically from the branch it is bound to; during a deploy the SIP bridge logs `backend_unavailable` for 1–2 minutes (expected).
- Render actually runs Python 3.14 despite `runtime.txt`; `audioop` comes from `audioop-lts`.
- Documentation for AI agents lives in `claude-*.md` files next to the code, indexed in `claude-index.md`. Update them when adding a subsystem.
