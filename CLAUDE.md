# Voksy AI (WellcomeAI) — SaaS Voice AI Platform

## Overview

Voksy AI is a SaaS platform for creating and managing AI-powered voice assistants. Users can build conversational agents using OpenAI Realtime API, Google Gemini Live, xAI Grok Voice, and ElevenLabs — then connect them to telephony (Voximplant) or embed as web widgets. The platform includes a CRM, knowledge base, conversation analytics, partner program, and subscription billing.

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
- **External APIs:** OpenAI, Google Gemini, xAI Grok, ElevenLabs, Voximplant, Finik (payments, KGS)

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
│   │   ├── voximplant.py    # Voximplant telephony integration
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
│   │   ├── elevenlabs.py    # ElevenLabsAgent, ElevenLabsConversation
│   │   ├── conversation.py  # Conversation model
│   │   ├── contact.py       # CRM Contact model
│   │   ├── subscription.py  # Subscription, SubscriptionPlan
│   │   ├── task.py          # Scheduled call tasks
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
│   │   ├── voximplant_handler.py    # Telephony WS bridge
│   │   ├── voximplant_adapter.py    # Voximplant audio adapter
│   │   └── sentence_detector.py     # Sentence boundary detection
│   ├── utils/               # Utility modules
│   ├── db/                  # Database session management
│   └── static/              # All frontend HTML/CSS/JS pages
│       ├── landing/         # React landing page (built)
│       ├── agents.html      # OpenAI agents management page
│       ├── gemini-agents.html   # Gemini agents page
│       ├── grok-agents.html     # Grok agents page
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
└── chrome-extension/        # Chrome extension (side panel + popup)
    ├── manifest.json
    ├── background.js
    ├── popup/
    └── sidepanel/
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
| `/api/voximplant` | Voximplant telephony |
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

## Database

PostgreSQL with SQLAlchemy ORM. Migrations managed by Alembic (`alembic/versions/`).

Key tables: `users`, `assistant_configs`, `gemini_assistant_configs`, `grok_assistant_configs`, `elevenlabs_agents`, `conversations`, `contacts`, `tasks`, `subscription_plans`, `user_subscriptions`, `embed_configs`, `partners`.

## Environment Variables (Key)

- `DATABASE_URL` — PostgreSQL connection string
- `OPENAI_API_KEY` — OpenAI API key (server-level, users can also set their own)
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

Users provide their own API keys for: Google Gemini, xAI Grok, ElevenLabs, Voximplant.

## Architecture Notes

- **Import redirection:** `main.py` contains a custom `MetaPathFinder` that redirects bare module imports (e.g., `core.config`) to `backend.core.config`. This allows modules to work both standalone and within the backend package.
- **Modular functions:** `backend/functions/` uses a registry pattern — new AI-callable functions are auto-discovered at startup via `discover_functions()`.
- **Multi-provider voice:** The WebSocket layer abstracts three different voice AI providers (OpenAI, Gemini, Grok) behind similar handler interfaces, with Voximplant telephony bridge support.
- **Startup schema fixes:** `app.py` startup event runs comprehensive schema checks and auto-adds missing columns for backwards compatibility.
- **Task scheduler:** Background scheduler (`core/task_scheduler.py`) polls for scheduled call tasks every 30 seconds and executes them automatically.
- **Static pages:** App pages (agents, dashboard, CRM, etc.) are vanilla HTML/JS served by FastAPI's `StaticFiles`. The React app is only used for the landing page.
