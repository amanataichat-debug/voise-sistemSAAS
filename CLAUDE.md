# Voksy AI (WellcomeAI) — SaaS Voice AI Platform

## Overview

Voksy AI is a SaaS platform for creating and managing AI-powered voice assistants. Users can build conversational agents using OpenAI GPT-Live, Google Gemini Live (gemini-3.8-live), Fish Audio (OpenAI text + Fish TTS), ElevenLabs (OpenAI text + Eleven v3 TTS, Kyrgyz by default), and xAI Grok Voice — then connect them to telephony (own SIP gateway, see below) or embed as web widgets. The platform includes a CRM, knowledge base, conversation analytics, partner program, and subscription billing.

## ⚠️ Voximplant is NOT used (read this first)

Telephony runs **only** through our own SIP gateway (`infra/sip-gateway/`: Hetzner VPS with Asterisk + `bridge.py` → `/ws/sip/{call_id}` on the backend → the same voice handlers as the web widget). Voximplant is switched off: no account, no scenarios deployed, no calls go through it.

The Voximplant code is still in the tree and is **dead code awaiting removal**. Do not fix, extend or document it as a working path, and never route a new feature through it:

- `backend/api/voximplant.py`, `backend/api/voximplant_settings.py`, `backend/api/telephony.py` (number binding, scenario deployment, `/config`, `/outbound-config`)
- `backend/services/voximplant_partner.py`, `backend/models/voximplant_child.py`, `User.voximplant_*` columns, `VOXIMPLANT_*` settings
- `backend/websockets/voximplant_handler.py`, `voximplant_adapter.py`, `handler_vox_gemini.py`, `handler_fish_tts.py` (old Fish TTS proxy for VoxEngine)
- `voximplant_scenarios/`, `.claude/skills/voximplant-*`, `backend/static/outbound-calls.html`, `test_outbound-calls.html` (the old Voximplant `telephony.html` is gone: the page now works on `/api/sip/*`)
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
- **External APIs:** OpenAI, Google Gemini, Fish Audio, ElevenLabs (TTS only, server key), xAI Grok, Finik (payments, KGS); telephony — own SIP gateway (Asterisk) with operator O!

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
│   │   ├── websocket.py     # OpenAI voice WS: /ws/{id}, /ws/demo → handler_live (GPT-Live)
│   │   ├── gemini_ws.py     # Gemini Live WebSocket proxy
│   │   ├── grok_ws.py       # Grok Voice WebSocket proxy
│   │   ├── telephony.py     # Outbound calls, call scheduling
│   │   ├── voximplant.py    # DEAD: Voximplant integration (not used, see warning above)
│   │   ├── fish_assistants.py # Fish assistant CRUD (/api/fish-assistants)
│   │   ├── fish_ws.py       # Fish voice WS: /ws/fish/{id} (OpenAI Realtime text + Fish TTS)
│   │   ├── eleven_assistants.py # Eleven assistant CRUD (/api/eleven-assistants: options with prices, voices, library)
│   │   ├── eleven_ws.py     # Eleven voice WS: /ws/eleven/{id} (OpenAI Realtime text + ElevenLabs TTS)
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
│   │   ├── eleven_assistant.py  # ElevenAssistantConfig + ElevenConversation (eleven_conversations), TTS models/prices, languages
│   │   ├── conversation.py  # Conversation model
│   │   ├── contact.py       # CRM Contact model
│   │   ├── subscription.py  # Subscription, SubscriptionPlan
│   │   ├── task.py          # Scheduled call tasks
│   │   ├── call_log.py      # CallLog (call_logs): event timeline + summary of a voice session, linked by session_id
│   │   ├── agent_file.py    # AgentFile (agent_files): call-agent library / client attachments / generated documents (bytes in R2)
│   │   ├── sip_gateway.py   # SipPhoneNumber (assistant or agent binding), SipCall, O! prefix check (own SIP telephony)
│   │   ├── partner.py       # Partner referral model
│   │   ├── embed_config.py  # Embeddable widget config
│   │   └── ...
│   ├── schemas/             # Pydantic request/response schemas
│   ├── services/            # Business logic layer
│   │   ├── auth_service.py          # Authentication logic
│   │   ├── assistant_service.py     # OpenAI assistant operations
│   │   ├── conversation_service.py  # Conversation CRUD
│   │   ├── google_sheets_service.py # Google Sheets integration
│   │   ├── payment_service.py       # Finik post-payment business logic
│   │   ├── finik_service.py         # Finik API client (RSA signing, 302→Location, webhook verify)
│   │   ├── pinecone_service.py      # Pinecone vector search
│   │   ├── r2_storage.py            # Cloudflare R2 file storage
│   │   ├── partner_service.py       # Partner program logic
│   │   ├── sip_gateway_service.py   # SIP gateway: outbound queue, bridge events, conversation tagging
│   │   ├── agent_media_service.py   # Call-agent files: client voice/photos/docs → Whisper / OCR / text, create_document, credits
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
│   ├── websockets/          # WebSocket handlers for real-time voice
│   │   ├── handler_live.py          # OpenAI GPT-Live handler (gpt-live-1, full-duplex) — production
│   │   ├── live_client.py           # GPT-Live WS client (session.start, delegation.responses, tools)
│   │   ├── function_calls.py        # Shared async function executor (handler_live, handler_fish)
│   │   ├── handler.py               # LEGACY: OpenAI Realtime handler
│   │   ├── handler_gemini.py        # Gemini Live handler (gemini-3.8-live, async functions)
│   │   ├── handler_grok.py          # Grok Voice handler
│   │   ├── openai_client.py         # LEGACY: OpenAI Realtime WS client (handler_realtime_new/openai_client_new — legacy too)
│   │   ├── gemini_client.py         # Gemini Live WS client (setup, NON_BLOCKING tools, toolResponse scheduling)
│   │   ├── grok_client.py           # Grok WS client
│   │   ├── sip_media_adapter.py     # HandlerSocket: SIP bridge audio <-> browser handler protocol
│   │   ├── handler_fish.py          # Fish handler: OpenAI Realtime (text) + Fish Audio TTS, widget protocol
│   │   ├── fish_llm_client.py       # Text-mode OpenAI Realtime client for Fish (server VAD, transcription, tools)
│   │   ├── fish_tts_client.py       # Fish Audio live TTS client (msgpack, barge-in via reconnect)
│   │   ├── handler_eleven.py        # Eleven handler: FishVoiceSession + FishLLMClient + ElevenTTSClient (server keys)
│   │   ├── call_log.py              # CallLogRecorder: per-session event timeline (+ WARNING/ERROR of any logger via contextvar) → call_logs; render_text for .txt
│   │   ├── chat_llm_client.py       # ChatLLMClient: Eleven brain in ASR mode — OpenAI Chat Completions streaming (gpt-5.6-luna), FishLLMClient interface, Realtime-style events, own history with tool-call placeholders
│   │   ├── yandex_stt_client.py     # YandexSTTClient: Yandex SpeechKit v3 streaming over gRPC (yandexcloud SDK), server EOU → turn, silence_chunk while idle, session rotation before the 5-min limit
│   │   ├── google_stt_client.py     # GoogleSTTClient: Google Speech-to-Text V2 Chirp 3 streaming (google-cloud-speech, gRPC), interim → barge-in, is_final (endpointing SHORT) → turn, silence while idle, stream rotation before 5 min, rejected settings dropped one by one
│   │   ├── openai_stt_client.py     # OpenAISTTClient: gpt-live-transcribe (Realtime ?intent=transcription), own energy VAD → input_audio_buffer.commit, language hints ky+ru; same interface as ScribeSTTClient
│   │   ├── scribe_stt_client.py     # ElevenLabs Scribe Realtime ASR for Eleven (ky, VAD 500 ms): partial → barge-in, committed → text turn
│   │   ├── eleven_tts_client.py     # ElevenLabs Text-to-Dialogue multi-context WS client (Eleven v3, PCM16 24 kHz, barge-in via close_context)
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
│       ├── eleven-agents.html   # ElevenLabs agents page (TTS model + price, voices from the account / public library, language, stability)
│       ├── eleven-test.html     # Browser test of an Eleven agent: widget.js with data-ws-path="/ws/eleven/"
│       ├── dashboard.html       # User dashboard
│       ├── telephony.html       # Own SIP telephony UI: numbers → bind assistant/agent, outbound call to an O! number, call journal (/api/sip/*)
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
    └── whatsapp-gateway/    # WhatsApp for call agents on a separate VPS (Evolution API in Docker). See claude-whatsapp-gateway.md, SERVER.md
        ├── install.sh       # One-command install/update on the VPS (fetches this folder from raw GitHub)
        ├── docker-compose.yml # evolution (v2.3.7) + postgres + redis + caddy (HTTPS)
        └── Caddyfile
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
| `/api/telephony` | Outbound calls, call tasks |
| `/api/voximplant` | DEAD — Voximplant (not used) |
| `/api/fish-assistants` | Fish assistant CRUD, `/options`, `/status` (server keys configured?) |
| `/api/eleven-assistants` | Eleven assistant CRUD, `/options` (TTS models with prices, languages, stability), `/status`, `/voices` (account voices, recommended for the language first), `/voices/library` + `/voices/library/add` (public library) |
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
| `/ws/eleven/{id}` | Eleven voice WS (widget protocol; OpenAI text brain + ElevenLabs TTS) |
| `/api/sip` | Own SIP telephony: numbers (bind to an assistant or an `agent_configs` agent), call journal, manual outbound to O! numbers only (`/api/sip/numbers`, `/api/sip/calls`, `/api/sip/gateways`) |
| `/ws/sip-gateway/control` | Control socket from the VPS bridge (auth by `SIP_GATEWAY_TOKEN`) |
| `/ws/sip/{call_id}` | Per-call media socket from the VPS bridge (PCM16 8 kHz) |
| `/api/agent/whatsapp` | Call-agent WhatsApp: status, `/connect` (QR), `/qr`, `/settings`, disconnect |
| `/api/whatsapp/webhook/{account_id}` | Evolution API webhook (header `X-Voksy-Token`) |

## Database

PostgreSQL with SQLAlchemy ORM. Migrations managed by Alembic (`alembic/versions/`).

Key tables: `users`, `assistant_configs`, `gemini_assistant_configs`, `grok_assistant_configs`, `fish_assistant_configs`, `eleven_assistant_configs`, `conversations`, `gemini_conversations`, `fish_conversations`, `eleven_conversations`, `contacts`, `tasks`, `subscription_plans`, `user_subscriptions`, `embed_configs`, `partners`, `sip_phone_numbers`, `sip_calls`.

`conversations.assistant_id` is a FK to `assistant_configs` (OpenAI), so Gemini dialogs go to `gemini_conversations`, Fish dialogs to `fish_conversations` and Eleven dialogs to `eleven_conversations`; the "Диалоги" page unions all four tables (`backend/api/conversations.py`) but lists **only the user's Eleven assistants** (`/sessions` reads only `eleven_conversations`: one grouped query with a window `count(*) over ()` for the total, function calls in the list carry only name/status; filter dropdown = `/eleven-assistants` + Eleven voices of call agents, loaded in parallel with the list; startup `ensure_dialog_indexes()` adds indexes on `function_logs.conversation_id`, `sip_calls.conversation_session_id`, `eleven_conversations (assistant_id, session_id, created_at)`); other providers' dialogs stay in the DB, hidden, and `SipGatewayService.tag_conversations` picks the table by `assistant_type`.

## Environment Variables (Key)

- `DATABASE_URL` — PostgreSQL connection string
- `OPENAI_API_KEY` — OpenAI API key (server-level, users can also set their own; Fish assistants always use the server key)
- `FISH_API_KEY` — Fish Audio API key (server-level; Fish assistants never use user keys)
- `ELEVENLABS_API_KEY` — ElevenLabs API key (server-level; all Eleven assistants synthesize on this key, the account's voice library is shared by all users)
- `ELEVEN_TEXT_LLM_PROVIDER` / `ELEVEN_TEXT_LLM_MODEL` / `ELEVEN_TEXT_LLM_ROUTE` — brain of Eleven assistants in ASR mode, Chat Completions streaming: default `openrouter` + `deepseek/deepseek-v4.1-flash` routed to `together` (`OPENROUTER_API_KEY`; without it — OpenAI `gpt-5.6-luna`); `openai` + `gpt-5.6-luna` is the previous setup; a model name containing `realtime` switches back to the Realtime text brain. The card's `llm_model` is not used in this mode
- `ELEVEN_ASR_PROVIDER` — speech recognition engine for Eleven assistants: `yandex` (default, SpeechKit v3; falls back to Scribe), `google` (Chirp 3; falls back to Yandex, then Scribe), `openai` (`gpt-live-transcribe`, `ELEVEN_ASR_OPENAI_MODEL` / `ELEVEN_ASR_OPENAI_DELAY`; falls back to Scribe) or `scribe` (falls back to OpenAI)
- `YANDEX_SPEECHKIT_API_KEY` (or `YANDEX_API_KEY`) / `YANDEX_FOLDER_ID` / `YANDEX_STT_LANGUAGES` / `YANDEX_STT_MODEL` — Yandex SpeechKit streaming STT (API key auth, languages default `ky-KG,ru-RU` — Kyrgyz is not in the official SpeechKit list, a rejected code falls back to `auto`; model `general`)
- `GOOGLE_SPEECH_CREDENTIALS_JSON` (default: `GOOGLE_SERVICE_ACCOUNT_JSON`) or `GOOGLE_SPEECH_API_KEY` + `GOOGLE_SPEECH_PROJECT_ID` / `GOOGLE_SPEECH_LOCATION` / `GOOGLE_STT_MODEL` / `GOOGLE_STT_LANGUAGES` / `GOOGLE_STT_ENDPOINTING` / `GOOGLE_STT_DENOISE` / `GOOGLE_STT_PHRASES` / `GOOGLE_STT_COMMIT_HOLD_MS` — Google Speech-to-Text V2 streaming for `ELEVEN_ASR_PROVIDER=google` (defaults `eu`, `chirp_3`, `ky-KG,ru-RU` — Kyrgyz is Preview, `short`, `true`, empty biasing list). The project needs the Speech-to-Text API enabled and the service account the Cloud Speech Client role. Rejected settings are dropped one by one (phrases → denoise → region eu→us → languages `auto` → endpointing). Google finalizes at every pause between sentences, so finals are merged into one turn (`GOOGLE_STT_COMMIT_HOLD_MS`=150 after a final; if `SPEECH_ACTIVITY_BEGIN` follows — wait for `SPEECH_ACTIVITY_END` and the next final, up to 8 s). Render test (5 Oct 2026): VAD events arrive, but Chirp 3 streaming sends no interim results (also with one language and no denoise), so barge-in happens on the finished phrase; end of speech → text ≈ 1.5 s with SHORT. Check from Render Shell: `python scripts/test_google_stt.py [--debug]`
- `ELEVEN_ASR_SECONDARY_LANGUAGES` / `ELEVEN_ASR_PHONE_8K` — Scribe extra languages (default `ru`; `none` = Kyrgyz only) and native 8 kHz for phone calls (default `true`)
- `ELEVEN_ASR_ENABLED` / `ELEVEN_ASR_SILENCE_MS` — Eleven assistants: Scribe Realtime ASR → text into OpenAI (defaults `true`, `500`); `false` = audio straight into OpenAI as before
- `AGENT_STT_MODEL` / `AGENT_STT_LANGUAGES` / `AGENT_STT_PROMPT` / `AGENT_STT_SCRIBE_FALLBACK` / `AGENT_STT_CREDITS_PER_MINUTE` / `AGENT_VISION_MODEL` / `AGENT_MEDIA_MAX_MB` / `AGENT_VOICE_MAX_SECONDS` — call-agent attachments in personal Telegram and Instagram (defaults `whisper-1`, `russian,kyrgyz,ru,ky`, ru/ky hint, `true`, `15`, `google/gemini-3.5-flash`, `20`, `600`). Whisper has no Kyrgyz, so a language outside the list is retried on ElevenLabs Scribe (`ELEVENLABS_API_KEY`)
- `PINECONE_API_KEY` / `PINECONE_INDEX` — Pinecone key and index name (default `voicufi`; dense, dimension 1536 for `text-embedding-3-small`, metric cosine). One index for everyone, one namespace per assistant knowledge base
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
- `WHATSAPP_GATEWAY_URL` / `WHATSAPP_GATEWAY_API_KEY` — WhatsApp gateway (Evolution API on `wa-gateway-1`): `https://wa.voksyai.online` and the `AUTHENTICATION_API_KEY` from `/opt/voksy-wa/.env` on that VPS (printed by `install.sh`)
- `SIP_GATEWAY_DEFAULT_ID` — gateway id used for outbound calls (default `sip-gw-1`)
- `LIVE_MODEL` / `LIVE_DELEGATION_MODEL` / `LIVE_DEFAULT_VOICE` / `LIVE_VOICE_INSTRUCTIONS_MAX_CHARS` — OpenAI GPT-Live transport (defaults: `gpt-live-1`, `gpt-5.6-terra`, `marin`, `6000`). GPT-Live and its delegation backend run on the assistant owner's `openai_api_key`
- `GEMINI_VAD_PROFILE` / `GEMINI_VAD_START_SENSITIVITY` / `GEMINI_VAD_END_SENSITIVITY` / `GEMINI_VAD_SILENCE_MS` — Gemini Live speech detection profile, same for widget and telephony (defaults: `fast`, `low`, `high`, `500`)
- `GEMINI_LIVE_MODEL` / `GEMINI_TOOL_SCHEDULING` — Gemini Live model (`gemini-3.8-live`; the extended-thinking variant is intentionally not used) and how the model voices async function results (`WHEN_IDLE`, `INTERRUPT` or `SILENT`)

Users provide their own API keys for: OpenAI (OpenAI assistants), Google Gemini, xAI Grok. Fish and Eleven assistants run on server keys only (`User.elevenlabs_api_key` is a leftover of the removed ElevenLabs Conversational AI integration and is not used).

## Architecture Notes

- **Import redirection:** `main.py` contains a custom `MetaPathFinder` that redirects bare module imports (e.g., `core.config`) to `backend.core.config`. This allows modules to work both standalone and within the backend package.
- **Modular functions:** `backend/functions/` uses a registry pattern — new AI-callable functions are auto-discovered at startup via `discover_functions()`.
- **Multi-provider voice:** The WebSocket layer abstracts the voice providers (OpenAI, Gemini, Fish, Eleven, Grok) behind handlers with one client protocol (the "widget protocol": `input_audio_buffer.append` in, `response.audio.delta` 24 kHz out, `speech.started` / `conversation.interrupted` / `assistant.speech.*` / `function_call.*` events). Anything speaking that protocol works in the widget and on the phone.
- **OpenAI assistants run on GPT-Live (`gpt-live-1`):** `backend/websockets/handler_live.py` + `live_client.py` replaced the Realtime API handler (`handler_realtime_new.py`/`openai_client_new.py` are legacy, not routed). GPT-Live is full-duplex: no VAD events, the model listens while speaking and handles interruptions itself, output audio arrives at real-time pace. Consequences: the widget streams the microphone continuously and plays audio gapless with a 200 ms cushion when `connection_status.full_duplex` is true; the SIP adapter holds the start of each reply for `OUTBOUND_CUSHION_MS`; `assistant.speech.started/ended` are derived from the audio stream. Functions run in the delegation backend (`delegation.responses`, model `LIVE_DELEGATION_MODEL`, Responses-format tools): calls arrive as `response.event` → `response.output_item.done`, results go back via `response.item.create` + `response.create`. Transcripts are fragments; dialogs are saved as (user, assistant) pairs at session end. The greeting is requested with `session.instructions.append`. Vision (`screen.context`) is not available on this model. Details: `backend/websockets/claude-websockets.md`.
- **Gemini assistants run on `gemini-3.8-live`:** `backend/websockets/handler_gemini.py` + `gemini_client.py` (v2.0). Same BidiGenerateContent protocol as before, but: no thinking config (the fast model only, the "thinking" toggle is gone from the UI), function declarations are `NON_BLOCKING` and run in the background through `function_calls.py`, `toolResponse` carries `name` + `scheduling`, proactive audio is always on. Context compression / session resumption are not enabled, so an audio session is capped at 15 min and a connection at ~10 min (accepted). The 3.1 / 2.5 variants, the browser agent (`/ws/gemini-browser`, `start_browser_task`) and their widgets were deleted in September 2026.
- **Own SIP telephony:** a Hetzner VPS (`178.105.79.237`, Asterisk 20 + `infra/sip-gateway/bridge/bridge.py`) terminates the operator's SIP trunk and streams call audio to the backend over outbound WebSockets. On the backend `backend/websockets/sip_media_adapter.py` wraps the *same* browser handlers (OpenAI, Gemini, Fish — map `SIP_HANDLERS` in `backend/api/sip_gateway.py`), so phone calls and the widget share functions, transcripts, conversation saving and behaviour. Rule: telephony and widget must behave the same. Outbound calls are queued in `sip_calls` and picked up by the worker that holds the control socket. Every answered call is recorded: Asterisk `MixMonitor` → bridge (`lame` MP3) → `POST /api/sip/recordings/{call_id}` → R2 `recordings/sip/...` → `sip_calls.recording_url`, shown as `record_url` in «Диалоги» and the agent call history (90-day retention is an R2 lifecycle rule). Full picture: `infra/sip-gateway/claude-sip-gateway.md`; server how-to: `infra/sip-gateway/SERVER.md`.
- **Fish assistants (half-cascade on server keys):** `backend/websockets/handler_fish.py`. OpenAI Realtime `gpt-realtime-2` in text-only mode (`fish_llm_client.py`: server VAD, input transcription, tools) is the brain; Fish Audio live TTS (`fish_tts_client.py`, msgpack, PCM16 24 kHz) is the voice. Text deltas are cut into sentences (`sentence_detector.py`) and sent to Fish; the greeting goes to Fish directly and is added to the OpenAI context as an assistant message. Barge-in = `response.cancel` + Fish reconnect (Fish has no cancel). Functions reuse `execute_and_send_function_result` from the OpenAI handler; `hangup_call` is handled by `HandlerSocket`. Keys: `OPENAI_API_KEY` + `FISH_API_KEY` from env only. Dialogs → `fish_conversations`. Browser test: `/static/fish-test.html?id=<uuid>` (widget.js with `data-ws-path="/ws/fish/"`). Billing gate (cascade credits) is planned, not implemented yet.
- **Eleven assistants (same half-cascade, ElevenLabs voice, Kyrgyz by default):** `backend/websockets/handler_eleven.py` reuses `FishVoiceSession` and `FishLLMClient` (with `conversation_model=ElevenConversation`, `label="ELEVEN-LLM"`); only the TTS client differs: `eleven_tts_client.py` speaks the ElevenLabs Text-to-Dialogue multi-context WebSocket (`/v1/text-to-dialogue/multi-stream-input`, `model_id` `eleven_v3_conversational` or `eleven_v3` — the only family with Kyrgyz, `output_format=pcm_24000`, `language_code` from the card). Each assistant reply is a context: `say()` = `inputs` + `flush` per sentence, `end_of_response()` marks it finished, the context is closed on the server once its audio played out (max 5 open contexts per socket), barge-in = `close_context` + audio from old contexts dropped. An idle socket (14 s without messages, no live context) is replaced by a fresh one in advance (`_rotate`): the server drops sockets after 20 s of silence and rejects `keep_alive` without a `context_id`. While a function runs longer than 0.7 s in silence, `FishVoiceSession` speaks a short filler phrase (`FILLER_PHRASES`, not for `hangup_call`). Call-log latency is measured from the real end of speech (the ASR/VAD pause is added back). The card language (`ky` default) also appends a "reply in this language" instruction to the OpenAI brain (`LANGUAGE_INSTRUCTIONS` in `fish_llm_client.py`); Kyrgyz speech understanding by `gpt-realtime-2` is untested, and whisper transcripts have no Kyrgyz. Voices come from the ElevenLabs account on the server key (`/voices`, recommended = verified for the language) or the public library (`/voices/library` → `/voices/library/add` copies the voice into the account). Keys: `OPENAI_API_KEY` + `ELEVENLABS_API_KEY` only. Dialogs → `eleven_conversations`. Browser test: `/static/eleven-test.html?id=<uuid>`. Telephony: `SIP_HANDLERS["eleven"]`. **ASR → text mode (default, branch `3009-ASR`):** the recognizer is chosen by `ELEVEN_ASR_PROVIDER` — by default Yandex SpeechKit v3 (`yandex_stt_client.py`, gRPC `stt.api.cloud.yandex.net:443`, `Api-Key` auth, PCM16 8 kHz on the phone / 16 kHz in the widget, `language_restriction` WHITELIST from `YANDEX_STT_LANGUAGES`, `DefaultEouClassifier` with `max_pause_between_words_hint_ms` = `ELEVEN_ASR_SILENCE_MS`; partial → barge-in, finals joined on `eou_update` → the turn), then OpenAI `gpt-live-transcribe` (`openai_stt_client.py`: `wss://api.openai.com/v1/realtime?intent=transcription`, `session.type=transcription`, `languages` [card language + `ELEVEN_ASR_SECONDARY_LANGUAGES`], prompt, keywords, `delay`; the model has no server VAD, so an energy VAD with an adaptive noise floor sends `input_audio_buffer.commit` after `ELEVEN_ASR_SILENCE_MS` of silence; deltas = partials for barge-in, `completed` = the turn; fields the server rejects are dropped one by one), with Scribe as the fallback. Scribe mode: caller audio goes to ElevenLabs Scribe Realtime (`scribe_stt_client.py`, `scribe_v2_realtime`, `pcm_24000`, `language_code` from the card, `commit_strategy=vad`, pause `ELEVEN_ASR_SILENCE_MS`=500) instead of OpenAI; the partial transcript = `speech.started` + barge-in (on words, not noise), the committed one = `input.transcription` + `conversation.item.create` (text) + `response.create`; The brain is `ChatLLMClient` (`chat_llm_client.py`, Chat Completions streaming, `ELEVEN_TEXT_LLM_MODEL` = `gpt-5.6-luna`, `reasoning_effort` as low as the API accepts, voice rules «1–2 sentences, no lists/markdown» appended to the prompt); it emits Realtime-style events into `FishVoiceSession.handle_llm_events`. The HTTPS connection is kept warm (`keepalive_expiry=300` + a ping after 15 s idle): with httpx's default 5 s every turn opened a new connection and waited 5–6 s. No first byte in 6 s → one retry. Each request's timing (server response / first text / total) goes to the call log. Barge-in by ASR ignores partials without letters and partials that repeat what the assistant is saying (line echo); during the greeting only a 2+ word phrase interrupts, a short «алло» is answered after the greeting; an interrupted greeting followed by 2.5 s of silence is repeated once. Event-loop stalls ≥400 ms are logged. Session start (`handler_eleven`): ElevenLabs, Scribe, OpenAI and the subscription check (in a thread) connect in parallel; the greeting is spoken as soon as ElevenLabs is ready, and from the second call instantly from an in-process cache of the synthesized greeting (`_GREETING_CACHE`, key = voice/model/language/stability/text); connection timings go to the call log. Dialog turns, function logs and Google Sheets rows are written in threads (`asyncio.to_thread`): sync DB writes used to freeze the call for ~1.2 s right before every reply. «Speaking» for echo/barge-in follows real playback (`_play_until` = generated audio length), not synthesis end. `hangup_call` with reason `technical_issue` is rejected in code (the model re-asks instead of hanging up). Phone audio goes to Scribe at native 8 kHz (`ELEVEN_ASR_PHONE_8K`), with Russian as a secondary language (`ELEVEN_ASR_SECONDARY_LANGUAGES`, default `ru`; retried without it if Scribe rejects). If Scribe fails to connect at start, the session uses the old Realtime audio scheme; if Scribe drops mid-call it is reconnected up to 3 times (the chat brain has no audio fallback). Language instructions (`LANGUAGE_INSTRUCTIONS`): start in the card language, switch when the caller asks or speaks another language and stay on it. Kill switch: `ELEVEN_ASR_ENABLED=false`. Fish assistants are unchanged. The old ElevenLabs Conversational AI integration (`api/elevenlabs.py`, `services/elevenlabs_service.py`, `models/elevenlabs.py`, `elevenlabs-agents.html`, `wigetelevanlabs.js`) was deleted in September 2026; the `elevenlabs_agents` tables are no longer created.
- **Design system (VoksiAI):** spec + sources in `design-system/` (start with its `README.md`). Cabinet pages use `<body class="vf">` + `/static/css/voksiai.css` + `voksiai-legacy.css` (bridge for old page classes) + `/static/js/i18n.js` + `ui.js` + `sidebar.js` (single menu — add menu items only there). Brand is **VoksiAI**, accent `#2a5ce8`. The cabinet now offers **only ElevenLabs assistants** (`/static/voice-assistants.html`); the OpenAI/Gemini/Fish/Grok pages, the standalone knowledge-base page, the translator and JARVIS are closed (redirects in `app.py`, `LEGACY_ASSISTANT_PAGES`) — their backends still run for existing assistants. Knowledge base is per assistant (`/api/knowledge-base/assistant/{type}/{id}`). UI languages RU/KY: cabinet dictionaries `backend/static/i18n/{ru,ky}.json`, landing `frontend/src/i18n/`; shared `localStorage.vs_lang`. Details: `backend/static/claude-static.md`.
- **Call logs ("Логи звонка" on the Диалоги page):** `backend/websockets/call_log.py` — `CallLogRecorder` lives in the contextvar `CURRENT_CALL_LOG`; every task of the session inherits it. It records explicit events (`FishVoiceSession`: ASR/VAD, user/assistant lines, reply latency = first TTS audio after the end of the caller's phrase, barge-ins, functions via `_EventTap`; SIP route: call start/end, hangup, DTMF) plus every WARNING/ERROR of any logger in that context (`_ContextHandler` on the root logger). Widget sessions (Eleven, Fish) save themselves at the end; for phone calls `api/sip_gateway.py` creates the recorder before the handler and saves it after `socket.finish()` (other providers get `session_id = "sip:<call_id>"`). Stored in `call_logs` by `session_id`; `GET /api/conversations/{id}/log` (JSON with `text`, or `?format=txt` as a file); the dialog modal shows summary chips, a timeline (auto-opened when there are errors) and "Скачать .txt". Visible to the dialog owner. Deleting a dialog deletes its log. The recording player (`record_url` from `sip_calls.recording_url`) sits above the log block.
- **Call-agent attachments and files (branch `0410-golos`):** client voice messages, audio, video notes, photos and documents in the agent's personal Telegram (`telegram_user_service.poll_dialogs` downloads media via Telethon) and Instagram DM (`attachments` CDN links) are processed in the background by `services/agent_media_service.py`: voice → Whisper (ru + ky, Scribe fallback when Whisper detects another language), images and scanned PDFs → OCR/description by an OpenRouter vision model, PDF/DOCX/XLSX/TXT → text locally. The result goes into the thread and to the orchestrator as text with `[file_id=…]` (any orchestrator model works, no multimodality needed). Charged in agent credits (`CreditService.charge_amount`: STT per started minute, OCR by OpenRouter cost); skipped when the agent is inactive or the owner has no access. Files live in `agent_files` + R2 `agent-files/...`. Agent tools: `list_agent_files`, `read_agent_file`, `create_document` (PDF/DOCX/XLSX/CSV/TXT, DejaVu font in `backend/assets/fonts`), `telegram_send_file`, `instagram_send_file` (Instagram: images as photos, other files as a 7-day link — Composio has no document send). Owner library: card «Файлы агента» on `/static/agent.html` (`agent/files.js`, `/api/agent/files`).
- **Servers outside Render (do not mix them up):** (1) SIP gateway `178.105.79.237` — Asterisk + bridge, telephony with operator O! (`infra/sip-gateway/`); (2) WhatsApp gateway `wa-gateway-1`, `91.99.124.169`, domain `wa.voksyai.online` — Hetzner CX23 Falkenstein, Docker with Evolution API v2.3.7 (unofficial WhatsApp Web protocol, Baileys) + Postgres + Redis + Caddy, everything in `/opt/voksy-wa`, firewall `wa-gateway` (TCP 22/80/443), Hetzner backups on (`infra/whatsapp-gateway/`, created 6 Oct 2026). DNS of `voksyai.online` is Namecheap BasicDNS. Both servers are updated only via their `install.sh` from GitHub.
- **WhatsApp for call agents (branch `0410-golos`):** chat only — unofficial libraries cannot do WhatsApp voice calls; calls would need the official WhatsApp Business Calling API (Meta, SIP into our Asterisk), not started. One Evolution instance = one WhatsApp number = one agent, connected by QR. Backend → gateway over HTTPS with header `apikey` (`WHATSAPP_GATEWAY_URL`, `WHATSAPP_GATEWAY_API_KEY`); gateway → backend via per-instance webhook set by the backend at instance creation (global webhook off). Ban risk for numbers (violates WhatsApp ToS), so the agent needs per-day limits on new chats. Backend: `models/agent_whatsapp.py` (account = instance, chats, messages), `services/whatsapp_service.py` (Evolution client), `services/whatsapp_inbound.py` (webhook in the background loop: series of messages debounced 8 s into one `handle_inbound_whatsapp` → PostCall `whatsapp_inbound`; media via `agent_media_service`), `api/agent_whatsapp.py` (`/api/agent/whatsapp/*`: connect → QR, poll, settings, disconnect; `POST /api/whatsapp/webhook/{account_id}` with header `X-Voksy-Token`). Agent tools `whatsapp_send_message` (reply or write FIRST by contact phone; daily new-chat limit `daily_new_chats_limit`, 40/hour, number check, typing delay), `whatsapp_send_file`, `whatsapp_get_thread`. UI: `static/agent/whatsapp-account.js` (row in the agent's connectors, QR modal). Status: `infra/whatsapp-gateway/claude-whatsapp-gateway.md`.
- **Startup schema fixes:** `app.py` startup event runs comprehensive schema checks and auto-adds missing columns for backwards compatibility.
- **Task scheduler:** Background scheduler (`core/task_scheduler.py`) polls for scheduled call tasks every 30 seconds and executes them automatically.
- **Background loop:** all periodic jobs (subscription checker/blocker, task scheduler, personal Telegram and Instagram pollers) run in a separate thread with its own event loop (`core/background_loop.py`, started from `app.py`), as does PostCall after a SIP call (`agent_call_finalizer._schedule` → `background_loop.submit`). Their sync DB queries used to stall call audio in the worker's main loop for 0.4–2 s. Background code must talk to the rest of the app only via the DB, never touch call websockets/queues. The SIP control socket's DB work (`_claim_outbound_sync`, `_apply_bridge_event_sync`) runs in `asyncio.to_thread`.
- **Static pages:** App pages (agents, dashboard, CRM, etc.) are vanilla HTML/JS served by FastAPI's `StaticFiles`. The React app is only used for the landing page.

## Development Workflow (current)

- Work happens on branch `1409-sip-v1` (operator trunk live: national number format, 5+5 channels; branched from `0509-v1-sip-good`). Commit and push there; no pull requests unless asked. `infra/sip-gateway/install.sh` now defaults to `VOKSY_BRANCH=1409-sip-v1`, so updating the VPS needs no extra variable.
- The SIP gateway VPS is updated from GitHub: after changing anything in `infra/sip-gateway/`, commit, push, then run `install.sh` on the VPS (see `infra/sip-gateway/SERVER.md`). Never edit configs on the server by hand.
- Render deploys the backend automatically from the branch it is bound to; during a deploy the SIP bridge logs `backend_unavailable` for 1–2 minutes (expected).
- Render actually runs Python 3.14 despite `runtime.txt`; `audioop` comes from `audioop-lts`.
- Documentation for AI agents lives in `claude-*.md` files next to the code, indexed in `claude-index.md`. Update them when adding a subsystem.
