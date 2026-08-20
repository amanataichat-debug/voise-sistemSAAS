# models — SQLAlchemy ORM-модели (схема БД PostgreSQL)

## Назначение
Папка содержит все ORM-модели приложения на SQLAlchemy 2.x — декларативные описания таблиц PostgreSQL. Здесь определены пользователи, ассистенты всех провайдеров (OpenAI, Gemini, Grok, Cartesia, ElevenLabs, Translate), диалоги, CRM-контакты, задачи обзвона, подписки/платежи, кредиты, партнёрская программа и Voximplant-телефония. Модели — единственный источник истины по структуре БД; миграции в `alembic/versions/` должны им соответствовать. Дополнительно `base.py` содержит самодельную инициализацию таблиц с ручными `CREATE TABLE` фолбэками.

## Состав
- `__init__.py` — реэкспорт всех моделей + `Base`, `engine`, `create_tables_with_full_tracking`; список `__all__`. Импорт всех моделей здесь критичен для регистрации в `Base.metadata`.
- `base.py` — `Base` (declarative_base), `engine` (читает `DATABASE_URL`), миксин `BaseModel` с `to_dict()`, функция `create_tables_with_full_tracking()` с ручными SQL-фолбэками для таблиц подписок.
- `user.py` — `User` (таблица `users`), корневая сущность; хранит ключи всех провайдеров, настройки Voximplant/Telegram, подписку, баланс кредитов.
- `assistant.py` — `AssistantConfig` (`assistant_configs`), ассистент OpenAI Realtime.
- `gemini_assistant.py` — `GeminiAssistantConfig` (`gemini_assistant_configs`) + `GeminiConversation` (`gemini_conversations`).
- `grok_assistant.py` — `GrokAssistantConfig` (`grok_assistant_configs`) + `GrokConversation` (`grok_conversations`) + enum `GrokVoice`.
- `cartesia_assistant.py` — `CartesiaAssistantConfig` (`cartesia_assistant_configs`), TTS-ассистент Cartesia.
- `translate_assistant.py` — `TranslateAssistantConfig` (`translate_assistant_configs`) + `TranslateConversation` (`translate_conversations`), синхронный перевод (OpenAI Realtime Translation).
- `elevenlabs.py` — `ElevenLabsAgent` (`elevenlabs_agents`) + `ElevenLabsConversation` (`elevenlabs_conversations`); основная логика — на стороне ElevenLabs API.
- `conversation.py` — `Conversation` (`conversations`), история диалогов OpenAI-ассистентов; связь с CRM-контактом.
- `contact.py` — `Contact` (`contacts`) + `ContactNote` (`contact_notes`), CRM-контакты и лента заметок.
- `task.py` — `Task` (`tasks`) + enum `TaskStatus`, запланированные звонки (поддерживает обычный режим и agent-режим).
- `agent_config.py` — `AgentConfig` (`agent_configs`), конфиг автономного «Voicyfy Agent» (оркестратор + голосовой движок).
- `agent_contact.py` — `AgentContact` (`agent_contacts`), контакты конкретного агента с памятью и счётчиком попыток.
- `agent_call.py` — `AgentCall` (`agent_calls`), запись о звонке агента (pre/post-call логи, транскрипт, решение).
- `agent_telegram_chat_history.py` — `AgentTelegramChatHistory` (`agent_telegram_chat_histories`), история Telegram-чатов агента.
- `browser_task.py` — `BrowserTask` (`browser_tasks`) + enum `BrowserTaskStatus`, задачи браузерной автоматизации (план, шаги, DOM-снапшоты).
- `subscription.py` — `SubscriptionPlan` (`subscription_plans`), `SubscriptionLog` (`subscription_logs`), `PaymentTransaction` (`payment_transactions`).
- `credit_transaction.py` — `CreditTransaction` (`credit_transactions`) + enum `CreditTransactionType`, движение кредитов оркестратора.
- `credit_package.py` — `CreditPackage` (`credit_packages`), пакеты кредитов для покупки.
- `partner.py` — `Partner` (`partners`), `ReferralRelationship` (`referral_relationships`), `PartnerCommission` (`partner_commissions`).
- `embed_config.py` — `EmbedConfig` (`embed_configs`), конфиг встраиваемого виджета (короткий `embed_code`).
- `email_verification.py` — `EmailVerification` (`email_verifications`), коды подтверждения email.
- `file.py` — `File` (`files`), загруженные файлы ассистента (хранилище R2 + OpenAI file id).
- `function_log.py` — `FunctionLog` (`function_logs`), лог вызовов AI-функций (универсальный, без FK на ассистента/диалог).
- `integration.py` — `Integration` (`integrations`), webhook-интеграции ассистента (например n8n).
- `pinecone_config.py` — `PineconeConfig` (`pinecone_configs`), namespace и контент базы знаний ассистента в Pinecone.
- `sms_message.py` — `SmsMessage` (`sms_messages`), входящие/исходящие SMS через Voximplant.
- `voximplant_child.py` — `VoximplantChildAccount` (`voximplant_child_accounts`) + `VoximplantPhoneNumber` (`voximplant_phone_numbers`) + enum `VoximplantVerificationStatus`.

## Ключевые сущности / точки входа
- **`User`** (`users`) — центр графа. Связи: `assistants`, `gemini_assistants`, `grok_assistants`, `cartesia_assistants`, `translate_assistants`, `elevenlabs_agents`, `files`, `subscription_plan_rel`; backref-ы: `contacts`, `partner_profile`, `voximplant_child_account`. Хранит per-user API-ключи (`openai_api_key`, `gemini_api_key`, `grok_api_key`, `cartesia_api_key`, `openrouter_api_key`, `elevenlabs_api_key`), Voximplant-креды, баланс `credits_balance`, флаги триала агента.
- **Ассистенты по провайдерам** — пять отдельных таблиц с похожей, но не унифицированной структурой: `AssistantConfig` (OpenAI), `GeminiAssistantConfig`, `GrokAssistantConfig`, `CartesiaAssistantConfig`, `TranslateAssistantConfig`. У каждого свой `*Conversation` (кроме Cartesia). Общие поля: `user_id`, `name`, `system_prompt`, `voice`, `language`, `greeting_message`, `is_active`, `is_public`, `functions` (JSON), `total_conversations`.
- **`AgentConfig`** (`agent_configs`) — «Voicyfy Agent»: `orchestrator_model` (по умолчанию `deepseek/deepseek-v4-pro`), `agent_model`, `agent_functions`, документы-промпты (`doc_who_am_i`, `doc_who_we_call`, `doc_how_we_talk`, `doc_what_we_offer`, `doc_rules_and_goals`), Telegram-настройки, рабочие часы. Ссылается на один из ассистентов через `gemini_assistant_id` / `openai_assistant_id` / `cartesia_assistant_id` (`assistant_type`).
- **`Task`** (`tasks`) — задача обзвона. FK на `contacts`, на три типа ассистентов (`assistant_id`/`gemini_assistant_id`/`cartesia_assistant_id`), на `users`. Поля agent-режима: `is_agent_task`, `agent_contact_id`, `agent_call_id`, `retry_count`, `post_call_decision`, `channel` («call» — звонок, дефолт; «telegram» — отложенное сообщение с личного TG-аккаунта агента, инструкция в `description`). enum `TaskStatus`.
- **CRM**: `Contact` ↔ `Conversation`, `ContactNote`, `Task` (каскадное удаление). `agent_memory` (JSONB) хранит запомненный агентом контекст по контакту.
- **Биллинг**: `SubscriptionPlan`/`PaymentTransaction`/`SubscriptionLog` (платежи Finik — поле `payment_system` по умолчанию `finik`, `external_payment_id` = наш PaymentId (UUID), `finik_transaction_id` = transactionId из webhook (уникальный, идемпотентность); исторические записи Robokassa сохранены с `payment_system='robokassa'`) и параллельно система кредитов `CreditTransaction`/`CreditPackage` для оркестратора агента.
- **Партнёрка**: `Partner` (referral_code, commission_rate ~30%), `ReferralRelationship` (UTM-метки), `PartnerCommission`.
- **Telephony**: `VoximplantChildAccount` (дочерний аккаунт Voximplant, scenario/rule ids в JSON) и `VoximplantPhoneNumber` (купленные/SIP-номера, привязка к ассистенту через `assistant_type`+`assistant_id`).
- **`create_tables_with_full_tracking(engine)`** в `base.py` — вызывается при старте; сначала `Base.metadata.create_all`, затем ручные `CREATE TABLE IF NOT EXISTS` для таблиц подписок и вставка дефолтных планов (`free`/`start`/`pro`).

## Связи с другими частями проекта
- Используется: `../api/` (все роутеры импортируют модели), `../services/`, `../websockets/`, `../core/scheduler.py` и `../core/task_scheduler.py`, `../../app.py` (startup: `create_tables_with_full_tracking`), `../db/` (сессии), `../../alembic/env.py` (target_metadata).
- Использует: `../db/` неявно (через `engine` из `base.py`), `DATABASE_URL` из окружения (`../core/config.py`). Прямых импортов бизнес-логики нет — модели чистые.

## На что обратить внимание
- **PK — `UUID(as_uuid=True)`** почти везде, default `uuid.uuid4`. Исключение: `BrowserTask` использует `String(36)` для id/user_id/assistant_id/session_id (без FK, индексы вручную).
- **Нет единой базовой модели ассистентов** — пять таблиц дублируют поля. Любое общее изменение надо вносить во все.
- **`FunctionLog` намеренно без ForeignKey** на `assistant_id`/`conversation_id` (универсален для всех провайдеров) — целостность не гарантируется на уровне БД.
- **`ElevenLabsAgent`/`ElevenLabsConversation`** — большинство полей (`name`, `system_prompt`, `voice_id`, `is_active`) помечены в коде как НЕ используемые: данные тянутся из ElevenLabs API, в БД хранится в основном `elevenlabs_agent_id`.
- **Ручной фолбэк в `base.py`** дублирует определения таблиц подписок в сыром SQL — расхождение с ORM-моделью `subscription.py` приведёт к рассинхрону. При изменении этих моделей правьте оба места.
- **`__init__.py` обязан импортировать каждую модель** — иначе таблица не попадёт в `Base.metadata` и не создастся/не отследится Alembic.
- **`Conversation` (OpenAI)** содержит ссылку `Boolean is_flagged` и `func.now()` — импорт `Boolean`/`func` приходит транзитивно; при правках проверяйте импорты SQLAlchemy в шапке файла.
- **Voximplant**: пароли субюзера (`vox_subuser_password`) хранятся в открытом виде — в коде стоит `TODO: зашифровать в проде`.
- **Чувствительные данные** (API-ключи провайдеров, Voximplant-креды) лежат в `users` как обычные строки без шифрования.
- `AgentCall`/`AgentContact`/`Task` образуют циклические FK (task → call → contact → task) с `ondelete` SET NULL/CASCADE — следите за порядком удаления.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../schemas/claude-schemas.md` — Pydantic-схемы (валидация ввода/вывода поверх этих моделей)
- `../db/claude-db.md` — управление сессиями БД
- `../../alembic/claude-alembic.md` — миграции схемы
- `../api/claude-api.md` — роутеры, использующие модели
- `../services/claude-services.md` — бизнес-логика над моделями
