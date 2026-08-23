# backend/schemas — Pydantic-схемы запросов/ответов API

## Назначение
Слой валидации и сериализации данных на границе HTTP. Здесь лежат Pydantic-модели, описывающие тело запроса и форму ответа для роутеров `backend/api/`. Схемы отделены от ORM-моделей (`backend/models/`): модели описывают таблицы БД, схемы — контракт API. Покрытие неполное — часть роутеров (агент, кредиты, voximplant, telephony) валидирует данные локальными inline-схемами или dict'ами, а не использует этот пакет.

## Состав
- `__init__.py` — реэкспорт основного набора схем (`Token`, `User*`, `Assistant*`, `Conversation*`, `File*`) и список `__all__`. Не все файлы папки сюда включены.
- `auth.py` — аутентификация: `Token`, `TokenData`, `LoginRequest`, `RegisterRequest`, сброс пароля (`PasswordReset*`), смена пароля, плюс схемы партнёрской системы (`ReferralValidation*`, `UTMTrackingData`).
- `user.py` — профиль пользователя: `UserBase`, `UserCreate`, `UserUpdate`, `UserPasswordUpdate`, `UserResponse`, `UserDetailResponse`.
- `assistant.py` — OpenAI-ассистент: `AssistantBase/Create/Update/Response`, `EmbedCodeResponse`, плюс модели описания функций (`Function`, `FunctionParameter(s)`) для function-calling.
- `conversation.py` — диалоги: `ConversationBase/Create/Update/Response`, `ConversationListResponse`, `ConversationStats` (агрегаты для дашборда).
- `file.py` — файлы базы знаний: `FileBase/Create/Update/Response`, `FileUploadResponse`, `FilesListResponse`.
- `elevenlabs.py` — ElevenLabs: `ElevenLabsApiKeyRequest`, `ElevenLabsVoiceResponse`, `ElevenLabsAgentCreate/Update/Response`, `ElevenLabsEmbedResponse`.
- `integration.py` — webhook-интеграции ассистента: `IntegrationBase/Create/Update/Response`.
- `subscription.py` — подписки: `SubscriptionPlanBase/Create/Update/Response`, `UserSubscriptionInfo`.
- `translate_assistant.py` — ассистент синхронного перевода: `TranslateAssistantBase/Create/Update/Response`, `TranslateEmbedCodeResponse`; содержит список поддерживаемых выходных языков (13) OpenAI Realtime Translation.

## Ключевые сущности / точки входа
- **Auth-контур:** `RegisterRequest`/`LoginRequest` (вход в `api/auth.py`), `Token`/`TokenData` (JWT-ответ и payload). `RegisterRequest` несёт UTM/реферальные поля — они прокидываются в `auth_service`.
- **CRUD-триплеты:** почти каждая сущность представлена тройкой `*Create` (вход на создание) → `*Update` (частичное обновление, поля `Optional`) → `*Response` (выход, обычно с `id`, `created_at`). `*Base` — общие поля, от которых наследуются остальные.
- **`AssistantCreate.functions`** и схемы `Function*` — описывают AI-функции, включаемые для ассистента; перекликаются с реестром `backend/functions/`.
- **`ConversationStats`** — форма агрегатов (число диалогов, длительность и т.п.), используется страницами аналитики.

## Связи с другими частями проекта
- Используется: `backend/api/*` (роутеры объявляют их в `response_model=` и в аннотациях тела запроса), частично `backend/services/*`.
- Использует: только `pydantic` (+ `typing`, `datetime`, `uuid`). Бизнес-логики и импортов моделей не содержит — чистый слой контрактов.

## На что обратить внимание
- **Покрытие неполное.** Новые подсистемы (Voksy AI Agent `api/agent.py`, кредиты `api/credits.py`, telephony, voximplant, gemini/grok/cartesia-ассистенты) часто описывают тела запросов локальными Pydantic-классами прямо в роутере или принимают `dict`/`Request`. Не ищите для них схему здесь — смотрите сам роутер.
- **`__init__.py` реэкспортит не всё** — `elevenlabs`, `integration`, `subscription`, `translate_assistant` импортируются напрямую из своих модулей. При добавлении схемы решите, нужен ли реэкспорт.
- **Схемы ≠ модели.** Имена полей в `*Response` могут отличаться от колонок в `backend/models/` (например, переименования/вычисляемые поля). Несоответствие схемы и модели — частый источник 422/500.
- Файла схем для gemini/grok/cartesia-ассистентов нет, хотя их роутеры существуют — валидация там inline.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../models/claude-models.md` — ORM-модели (то, что лежит в БД под этими схемами)
- `../api/claude-api.md` — роутеры, использующие схемы
- `../services/claude-services.md` — бизнес-логика
- `../functions/claude-functions.md` — определения AI-функций (схемы `Function*`)
- `../../claude-index.md` — корневой индекс
