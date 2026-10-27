"""
Voksy AI Agent API v2.0 — CRUD, chat (with tools), contacts, calls, stats.
"""

import asyncio
import re
import json
import uuid
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import (
    APIRouter, Depends, HTTPException, Query, status,
    UploadFile, File, Form, BackgroundTasks, Request,
)
from fastapi.responses import Response, StreamingResponse, RedirectResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import func, or_, and_, false

from backend.core.logging import get_logger
from backend.db.session import get_db, SessionLocal
from backend.core.timezone_utils import adjust_to_working_hours, now_utc, iso_utc
from backend.models.user import User
from backend.models.agent_config import AgentConfig
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.models.assistant import AssistantConfig
from backend.models.conversation import Conversation
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.yandex_assistant import (
    YandexAssistantConfig, YandexConversation, DEFAULT_YANDEX_MODEL,
)
from backend.models.grok_assistant import GrokAssistantConfig, GrokConversation
from backend.models.fish_assistant import (
    FishAssistantConfig, FISH_MODELS, FISH_LATENCY_MODES,
    DEFAULT_FISH_MODEL, DEFAULT_FISH_LATENCY, DEFAULT_FISH_SAMPLE_RATE,
    DEFAULT_FISH_LLM_MODEL,
)
from backend.models.eleven_assistant import (
    ElevenAssistantConfig, ElevenConversation, ELEVEN_TTS_MODEL_IDS, DEFAULT_ELEVEN_TTS_MODEL,
    ELEVEN_LANGUAGES, DEFAULT_ELEVEN_LANGUAGE, DEFAULT_ELEVEN_STABILITY, DEFAULT_ELEVEN_LLM_MODEL,
    VOICE_ENGINE_IDS, DEFAULT_VOICE_ENGINE,
)
from backend.websockets.live_client import LIVE_VOICE_SET
from backend.models.voximplant_child import VoximplantChildAccount
from backend.models.task import Task, TaskStatus
from backend.models.contact import Contact
from backend.models.agent_contact import AgentContact
from backend.models.agent_call import AgentCall
from backend.models.agent_connector import AgentConnector, CONNECTOR_TOOLKITS
from backend.services import agent_knowledge_vectors as kb_vectors
from backend.models.agent_whatsapp import AgentWhatsAppMessage
from backend.models.agent_telegram_account import AgentTelegramMessage
from backend.models.agent_instagram import AgentInstagramMessage
from backend.services import composio_service
from backend.services import agent_pause
from backend.services import agent_voice_context
from backend.core.config import settings
from backend.core.dependencies import get_current_user, get_current_user_flexible
from backend.core.pipeline_stages import (
    AGENT_CONTACT_STAGES, is_valid_stage, get_agent_stages,
    is_valid_stage_for_agent, save_agent_stages, normalize_stages_list,
)
from backend.services.agent_prompts import get_voice_agent_prompt, build_voice_agent_prompt
from backend.services.agent_models import (
    ORCHESTRATOR_MODELS, get_default_model, is_valid_model, resolve_slug,
)
from backend.services.agent_tools import assistant_task_kwargs
from backend.services.credit_service import (
    CreditService,
    activate_agent_trial,
    InsufficientCreditsError,
    SubscriptionExpiredError,
    SubscriptionRequiredError,
)

logger = get_logger(__name__)

router = APIRouter()

# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================


VALID_ASSISTANT_TYPES = ("gemini", "openai", "cartesia", "yandex", "cascade", "fish", "eleven")
# Кабинет предлагает новым агентам только ElevenLabs; остальные типы остаются для уже созданных.
NEW_AGENT_ASSISTANT_TYPES = ("eleven",)
ELEVEN_LANGUAGE_CODES = [lang["code"] for lang in ELEVEN_LANGUAGES]

# Доступные голоса по провайдерам (должны совпадать со списками в agent.html).
OPENAI_VOICES = [
    "alloy", "echo", "marin", "cedar", "shimmer",
    "ash", "ballad", "coral", "sage", "verse",
]
# Голоса Yandex SpeechKit (должны совпадать с YANDEX_VOICES в yandex_assistants.py).
YANDEX_VOICES = [
    "marina", "dasha", "alexander", "julia", "lera",
    "masha", "anton", "kirill", "filipp", "ermil",
    "jane", "omazh", "zahar", "madi_ru", "saule_ru",
]
GEMINI_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
]
# Голоса каскада — только VoxTTS realtime (сценарий каскада поддерживает VoxTTS).
CASCADE_VOICES = ["Anna", "Sergey"]
DEFAULT_GEMINI_VOICE = "Kore"
DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_YANDEX_VOICE = "marina"
DEFAULT_CASCADE_VOICE = "Anna"


def _valid_fish_model(model: Optional[str]) -> str:
    """Модель синтеза Fish из запроса или дефолт, если пришло что-то чужое."""
    return model if model in FISH_MODELS else DEFAULT_FISH_MODEL


def _valid_fish_latency(latency: Optional[str]) -> str:
    """Режим латентности Fish из запроса или дефолт."""
    return latency if latency in FISH_LATENCY_MODES else DEFAULT_FISH_LATENCY


def _valid_eleven_model(model: Optional[str]) -> str:
    return model if model in ELEVEN_TTS_MODEL_IDS else DEFAULT_ELEVEN_TTS_MODEL


def _valid_eleven_language(language: Optional[str]) -> str:
    return language if language in ELEVEN_LANGUAGE_CODES else DEFAULT_ELEVEN_LANGUAGE


def _valid_eleven_stability(stability: Optional[float]) -> float:
    return stability if stability in (0.0, 0.5, 1.0) else DEFAULT_ELEVEN_STABILITY


def _valid_voice_engine(engine: Optional[str]) -> str:
    """Движок голоса агента: ElevenLabs (каскад) или OpenAI GPT-Live."""
    return engine if engine in VOICE_ENGINE_IDS else DEFAULT_VOICE_ENGINE


def _valid_live_voice(voice: Optional[str]) -> Optional[str]:
    v = (voice or "").strip().lower()
    return v if v in LIVE_VOICE_SET else None


def _is_valid_voice(assistant_type: str, voice: str) -> bool:
    """Проверка имени голоса для select-провайдеров (gemini/openai/yandex/cascade)."""
    if assistant_type == "gemini":
        return voice in GEMINI_VOICES
    if assistant_type == "openai":
        return voice in OPENAI_VOICES
    if assistant_type == "yandex":
        return voice in YANDEX_VOICES
    if assistant_type == "cascade":
        return voice in CASCADE_VOICES
    return False


def _resolve_voice_assistant(db: Session, agent: AgentConfig):
    """Вернуть связанный голосовой конфиг агента, запрашивая по id (после возможной смены типа)."""
    va_id = agent.get_voice_assistant_id()
    if not va_id:
        return None
    if agent.assistant_type == "gemini":
        return db.query(GeminiAssistantConfig).filter(GeminiAssistantConfig.id == va_id).first()
    if agent.assistant_type == "openai":
        return db.query(AssistantConfig).filter(AssistantConfig.id == va_id).first()
    if agent.assistant_type == "cartesia":
        return db.query(CartesiaAssistantConfig).filter(CartesiaAssistantConfig.id == va_id).first()
    if agent.assistant_type == "yandex":
        return db.query(YandexAssistantConfig).filter(YandexAssistantConfig.id == va_id).first()
    if agent.assistant_type == "cascade":
        return db.query(GrokAssistantConfig).filter(GrokAssistantConfig.id == va_id).first()
    if agent.assistant_type == "fish":
        return db.query(FishAssistantConfig).filter(FishAssistantConfig.id == va_id).first()
    if agent.assistant_type == "eleven":
        return db.query(ElevenAssistantConfig).filter(ElevenAssistantConfig.id == va_id).first()
    return None


# Функции базы знаний у голосового ассистента (ветка 1610-kb-calls).
# search_pinecone убран: он искал только по документам в Pinecone и не видел ни
# пар «вопрос — ответ», ни секций, ни каталога, поэтому агент на звонке уходил
# в поиск на ≈4 секунды и возвращался ни с чем. Логика общая с agent_voice_context
# (её же пересчитывает звонок ЖИВЬЁМ перед стартом — apply_live_kb_functions,
# не дожидаясь, пока владелец ещё раз тронет базу через кабинет); здесь — тот
# же пересчёт, но сохраняется в БД, чтобы кабинет сразу показывал актуальный
# список функций в карточке ассистента.
def _voice_sync_kb_functions(db: Session, agent, va=None) -> None:
    """
    Привести список функций голосовой карточки в соответствие с базой знаний
    (и записать в БД — для звонка это подстраховка: главный пересчёт живой,
    см. agent_voice_context.apply_live_kb_functions).

    Вызывается после любой правки базы: добавили каталог — появился
    find_products, вычистили базу — поиск снялся (раньше функция оставалась
    навсегда и агент ходил искать в пустоту).
    """
    if va is None:
        va = _resolve_voice_assistant(db, agent)
    if va is None:
        return
    wanted = agent_voice_context.kb_function_names(db, agent)
    va.functions = agent_voice_context.merge_kb_functions(va.functions, wanted)
    flag_modified(va, "functions")


def _voice_set_connector_function(va, toolkit: str, enabled: bool) -> None:
    """
    Включает/выключает голосовые функции коннектора (например google_calendar →
    google_calendar_create_event/find_events) у голосового ассистента.

    Зеркалит _voice_sync_kb_functions: функции резолвятся в определения в runtime
    (build_functions_for_openai), а composio_user_id функция берёт из владельца
    ассистента — поэтому здесь достаточно управлять списком functions.
    """
    if va is None:
        return
    names_to_manage = set(composio_service.voice_function_names(toolkit))
    if not names_to_manage:
        return

    funcs = va.functions
    if isinstance(funcs, dict) and "enabled_functions" in funcs:
        names = [n for n in list(funcs.get("enabled_functions", [])) if n not in names_to_manage]
        if enabled:
            names.extend(sorted(names_to_manage))
        va.functions = {"enabled_functions": names}
        flag_modified(va, "functions")
        return

    items = list(funcs) if isinstance(funcs, list) else []
    items = [f for f in items if (f.get("name") if isinstance(f, dict) else f) not in names_to_manage]
    if enabled:
        items.extend(composio_service.TOOLKIT_VOICE_FUNCTIONS.get(toolkit, []))
    va.functions = items
    flag_modified(va, "functions")


# Максимум агентов на одного пользователя (v3.1: было «один на юзера»).
MAX_AGENTS_PER_USER = 3


def _max_agents_for(user) -> int:
    """Старт («только отвечает») — один агент, Profi — до MAX_AGENTS_PER_USER."""
    return 1 if user.agent_mode() == "reactive" else MAX_AGENTS_PER_USER


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    assistant_type: str = Field(...)  # "gemini" | "openai" | "cartesia" | "yandex"
    doc_who_am_i: str = Field(..., min_length=1)
    doc_who_we_call: str = Field(..., min_length=1)
    doc_how_we_talk: str = Field(..., min_length=1)
    doc_what_we_offer: str = Field(..., min_length=1)
    doc_rules_and_goals: str = Field(..., min_length=1)
    additional_instructions: Optional[str] = None
    voice_additional_instructions: Optional[str] = None
    inbound_first_phrase: Optional[str] = Field(None, max_length=500)
    working_hours_start: int = Field(default=9, ge=0, le=23)
    working_hours_end: int = Field(default=20, ge=1, le=24)
    call_window_enabled: bool = True
    orchestrator_model: Optional[str] = None  # default → get_default_model()
    # Голос (gemini/openai/yandex). Для cartesia — cartesia_voice_id + voice_speed,
    # для fish — fish_voice_id + fish_model/fish_latency + voice_speed.
    voice: Optional[str] = None
    cartesia_voice_id: Optional[str] = None
    voice_speed: Optional[float] = Field(None, ge=0.5, le=1.5)
    fish_voice_id: Optional[str] = Field(None, max_length=255)
    fish_model: Optional[str] = None
    fish_latency: Optional[str] = None
    # ElevenLabs: voice_id из аккаунта (/api/eleven-assistants/voices), модель v3, язык, стабильность
    eleven_voice_id: Optional[str] = Field(None, max_length=255)
    eleven_voice_name: Optional[str] = Field(None, max_length=255)
    eleven_tts_model: Optional[str] = None
    eleven_language: Optional[str] = None
    eleven_stability: Optional[float] = None
    # Движок голоса: "eleven" (каскад + ElevenLabs) | "gpt_live" (OpenAI GPT-Live, голос eleven_live_voice)
    eleven_voice_engine: Optional[str] = None
    eleven_live_voice: Optional[str] = Field(None, max_length=50)


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    doc_who_am_i: Optional[str] = None
    doc_who_we_call: Optional[str] = None
    doc_how_we_talk: Optional[str] = None
    doc_what_we_offer: Optional[str] = None
    doc_rules_and_goals: Optional[str] = None
    additional_instructions: Optional[str] = None
    voice_additional_instructions: Optional[str] = None
    inbound_first_phrase: Optional[str] = Field(None, max_length=500)
    working_hours_start: Optional[int] = Field(None, ge=0, le=23)
    working_hours_end: Optional[int] = Field(None, ge=1, le=24)
    # Окно звонков: False — агент звонит в любое время суток
    call_window_enabled: Optional[bool] = None
    is_active: Optional[bool] = None
    default_caller_id: Optional[str] = Field(None, max_length=50)
    webhook_url: Optional[str] = Field(None, max_length=500)
    orchestrator_model: Optional[str] = None
    assistant_type: Optional[str] = None
    # Голос (gemini/openai/yandex). Для cartesia — cartesia_voice_id + voice_speed,
    # для fish — fish_voice_id + fish_model/fish_latency + voice_speed.
    voice: Optional[str] = None
    cartesia_voice_id: Optional[str] = None
    voice_speed: Optional[float] = Field(None, ge=0.5, le=1.5)
    fish_voice_id: Optional[str] = Field(None, max_length=255)
    fish_model: Optional[str] = None
    fish_latency: Optional[str] = None
    # ElevenLabs: voice_id из аккаунта (/api/eleven-assistants/voices), модель v3, язык, стабильность
    eleven_voice_id: Optional[str] = Field(None, max_length=255)
    eleven_voice_name: Optional[str] = Field(None, max_length=255)
    eleven_tts_model: Optional[str] = None
    eleven_language: Optional[str] = None
    eleven_stability: Optional[float] = None
    # Движок голоса: "eleven" (каскад + ElevenLabs) | "gpt_live" (OpenAI GPT-Live, голос eleven_live_voice)
    eleven_voice_engine: Optional[str] = None
    eleven_live_voice: Optional[str] = Field(None, max_length=50)
    # Фирменный стиль PDF-документов агента (ветка 1310-kp-design): название в
    # шапке, цвет плашки, контакты в колонтитуле, логотип (файл из библиотеки).
    brand_company_name: Optional[str] = Field(None, max_length=255)
    brand_color: Optional[str] = Field(None, max_length=16)
    brand_contacts: Optional[str] = Field(None, max_length=500)
    brand_logo_file_id: Optional[str] = Field(None, max_length=64)


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=30000)


class AgentContactCreateRequest(BaseModel):
    name: Optional[str] = None
    phone: str = Field(..., min_length=1, max_length=50)
    company: Optional[str] = None
    position: Optional[str] = None
    notes: Optional[str] = None


class AgentContactUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    company: Optional[str] = Field(None, max_length=255)
    position: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    # ── CRM v2 поля (ветка 2510-crm-v2) ──
    # tags: массив строк → хранится comma-separated в колонке; "" очищает.
    tags: Optional[List[str]] = None
    deal_amount: Optional[float] = Field(None, ge=0)
    next_step_text: Optional[str] = None
    next_step_at: Optional[str] = None  # ISO; парсится ниже
    lost_reason: Optional[str] = None


class PublicAccessToggleRequest(BaseModel):
    enabled: bool


class AgentContactStatusRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=50)


class AgentContactPauseRequest(BaseModel):
    """Остановить агента в переписке с клиентом: minutes или forever=true."""
    minutes: Optional[int] = Field(None, ge=1, le=60 * 24 * 365)
    forever: bool = False


class VoiceMessagesSettingsRequest(BaseModel):
    """
    Голосовые сообщения. ElevenLabs: voice_id / language null — как в звонках
    (Eleven-карточка), stability null — Creative. OpenAI: голос GPT-Live.
    """
    engine: Optional[str] = Field(None, max_length=16)          # eleven | openai
    voice_id: Optional[str] = Field(None, max_length=255)
    voice_name: Optional[str] = Field(None, max_length=255)
    language: Optional[str] = Field(None, max_length=10)
    stability: Optional[float] = None
    openai_voice: Optional[str] = Field(None, max_length=32)
    auto_share: Optional[int] = None                            # 30 / 40 / 50


class VoicePreviewRequest(BaseModel):
    text: Optional[str] = Field(None, max_length=400)
    engine: Optional[str] = Field(None, max_length=16)
    voice_id: Optional[str] = Field(None, max_length=255)
    language: Optional[str] = Field(None, max_length=10)
    stability: Optional[float] = None
    openai_voice: Optional[str] = Field(None, max_length=32)


class MessengerSettingsRequest(BaseModel):
    owner_pause_minutes: Optional[int] = None   # 10/30/60/1440, -1 — навсегда
    reply_delay_seconds: Optional[int] = None   # 5/10/15/20/30/60


class AgentTaskUpdateRequest(BaseModel):
    """Ручное редактирование задачи из UI (карточка контакта / календарь)."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    scheduled_time: Optional[str] = Field(None, description="Новое время звонка (ISO-8601, UTC)")


class AgentTaskCreateRequest(BaseModel):
    """Ручное создание задачи для существующего контакта."""
    title: str = Field(..., min_length=1, max_length=255)
    scheduled_time: str = Field(..., description="Время звонка (ISO-8601, UTC)")


class ImportExecuteRequest(BaseModel):
    preview_token: str = Field(..., min_length=1)
    agent_id: Optional[str] = None
    # True (по умолчанию) — оркестратор проставляет авто-задачу на каждый контакт.
    # False — контакты сохраняются без авто-задач; задача создаётся только для строк,
    # где в файле явно заполнены «Задача» и/или «Когда звонить».
    create_tasks: bool = True


# ============================================================================
# VOICE AGENT SYSTEM PROMPT TEMPLATE
# ============================================================================

VOICE_AGENT_SYSTEM_PROMPT = """Ты голосовой AI-агент компании {company_name}.
Перед каждым звонком ты получаешь задачу и стратегию от оркестратора
в поле custom_greeting — это твоё первое сообщение и контекст звонка.
Говори на русском языке. Будь вежлив, конкретен, не затягивай разговор.
Цель каждого звонка указана в задаче. Следуй стратегии оркестратора.
Если клиент просит перезвонить — уточни удобное время и заверши звонок.
Если клиент отказывается — вежливо попрощайся, не дави."""

ORCHESTRATOR_GENERATION_SYSTEM = """Ты эксперт по созданию AI-агентов для бизнеса.
На основе 5 документов создай системный промпт для AI-оркестратора,
который будет планировать звонки и анализировать их результаты.
Промпт должен быть на русском языке, конкретным и деловым.
Формат: блоки О КОМПАНИИ / ЦЕЛЕВАЯ АУДИТОРИЯ /
СТИЛЬ ОБЩЕНИЯ / ПРОДУКТЫ / ЦЕЛИ И KPI / ПРАВИЛА РАБОТЫ."""


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


async def _generate_orchestrator_prompt(
    doc_who_am_i: str,
    doc_who_we_call: str,
    doc_how_we_talk: str,
    doc_what_we_offer: str,
    doc_rules_and_goals: str,
    openai_api_key: str
) -> str:
    """Generate orchestrator prompt from 5 onboarding documents using gpt-4o-mini."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=openai_api_key)

    user_input = f"""Документ 1 — КТО МЫ:
{doc_who_am_i}

Документ 2 — КОМУ ЗВОНИМ:
{doc_who_we_call}

Документ 3 — КАК ГОВОРИМ:
{doc_how_we_talk}

Документ 4 — ЧТО ПРЕДЛАГАЕМ:
{doc_what_we_offer}

Документ 5 — ПРАВИЛА И ЦЕЛИ:
{doc_rules_and_goals}"""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": ORCHESTRATOR_GENERATION_SYSTEM},
            {"role": "user", "content": user_input}
        ],
        temperature=0.7,
        max_tokens=4000
    )

    return response.choices[0].message.content


def _check_telephony_verified(current_user: User, db: Session):
    """Raise HTTPException(400) if telephony is not verified for the user."""
    child_account = db.query(VoximplantChildAccount).filter(
        VoximplantChildAccount.user_id == current_user.id
    ).first()
    if not child_account or not child_account.is_verified:
        raise HTTPException(status_code=400, detail="telephony_not_verified")


def _check_assistant_keys(assistant_type: str, current_user: User):
    """Validate required API keys for the chosen assistant type."""
    if assistant_type == "gemini":
        if not current_user.gemini_api_key:
            raise HTTPException(status_code=400, detail="api_key_required_gemini")
    elif assistant_type == "openai":
        if not current_user.openai_api_key:
            raise HTTPException(status_code=400, detail="api_key_required_openai")
    elif assistant_type == "cartesia":
        if not current_user.openai_api_key:
            raise HTTPException(status_code=400, detail="api_key_required_openai")
        if not current_user.cartesia_api_key:
            raise HTTPException(status_code=400, detail="api_key_required_cartesia")
    elif assistant_type == "yandex":
        if not current_user.yandex_api_key or not current_user.yandex_folder_id:
            raise HTTPException(status_code=400, detail="api_key_required_yandex")
    elif assistant_type == "cascade":
        # Каскад работает на серверном ключе OpenAI + кредитах каскада —
        # пользовательский ключ не нужен. Проверять баланс здесь не нужно:
        # гейт по кредитам стоит на старте звонка (outbound-config / config).
        pass
    elif assistant_type == "eleven":
        # Eleven работает на серверных ключах (OPENAI_API_KEY + ELEVENLABS_API_KEY).
        pass
    elif assistant_type == "fish":
        # Fish работает на серверных ключах (OPENAI_API_KEY + FISH_API_KEY):
        # диалог ведёт OpenAI Realtime текстом, озвучивает Fish Audio
        # (backend/websockets/handler_fish.py). Пользовательские ключи не нужны.
        pass


# Функции, доступные голосовому агенту во время звонка по умолчанию.
# Обе уже зарегистрированы в реестре (backend/functions/): отправка SMS клиенту
# и корректное завершение звонка. Имена резолвятся в определения в runtime
# (build_functions_for_openai). search_pinecone добавляется отдельно при
# создании базы знаний — см. _voice_sync_kb_functions.
def _default_voice_functions():
    return [
        {"name": "send_sms", "description": "Отправить SMS клиенту во время звонка (адрес, ссылка, код, реквизиты)."},
        {"name": "hangup_call", "description": "Завершить телефонный звонок, когда разговор окончен или по просьбе клиента."},
    ]


def _create_voice_assistant(assistant_type: str, name: str, user_id, db,
                            voice=None, cartesia_voice_id=None, voice_speed=None,
                            voice_additional_instructions=None,
                            fish_voice_id=None, fish_model=None, fish_latency=None,
                            eleven=None):
    """Create a voice assistant of the given type with the hardcoded base prompt.

    voice — имя голоса для gemini/openai/yandex; для cartesia используются
    cartesia_voice_id и voice_speed, для fish — fish_voice_id, fish_model,
    fish_latency и voice_speed. Если не передано — берутся дефолты.
    voice_additional_instructions — доп.инструкции по поведению в живом звонке,
    дописываются к базовому промпту голосового агента.
    """
    prompt = build_voice_agent_prompt(voice_additional_instructions)
    if assistant_type == "gemini":
        gemini_voice = voice if (voice and _is_valid_voice("gemini", voice)) else DEFAULT_GEMINI_VOICE
        va = GeminiAssistantConfig(
            id=uuid.uuid4(), user_id=user_id, name=f"{name} Voice",
            system_prompt=prompt, voice=gemini_voice, language="ru-RU",
            greeting_message="", is_active=True, is_public=False,
            temperature=0.7, max_tokens=4000,
            functions=_default_voice_functions(),
        )
    elif assistant_type == "openai":
        openai_voice = voice if (voice and _is_valid_voice("openai", voice)) else DEFAULT_OPENAI_VOICE
        va = AssistantConfig(
            id=uuid.uuid4(), user_id=user_id, name=f"{name} Voice",
            system_prompt=prompt, voice=openai_voice, language="ru",
            greeting_message="", is_active=True, is_public=False,
            temperature=0.7, max_tokens=4000,
            functions=_default_voice_functions(),
        )
    elif assistant_type == "cartesia":
        va = CartesiaAssistantConfig(
            id=uuid.uuid4(), user_id=user_id, name=f"{name} Voice",
            system_prompt=prompt, greeting_message="", is_active=True,
            cartesia_voice_id=(cartesia_voice_id or None),
            voice_speed=(voice_speed if voice_speed is not None else 1.0),
            functions=_default_voice_functions(),
        )
    elif assistant_type == "yandex":
        yandex_voice = voice if (voice and _is_valid_voice("yandex", voice)) else DEFAULT_YANDEX_VOICE
        va = YandexAssistantConfig(
            id=uuid.uuid4(), user_id=user_id, name=f"{name} Voice",
            system_prompt=prompt, model=DEFAULT_YANDEX_MODEL,
            voice=yandex_voice, language="ru",
            greeting_message="", is_active=True, is_public=False,
            temperature=0.7, max_tokens=4000,
            functions=_default_voice_functions(),
        )
    elif assistant_type == "cascade":
        # Каскад: GrokAssistantConfig(assistant_type='cascade'), VoxTTS realtime.
        # LLM (gpt-realtime-2.1-mini) на серверном ключе, оплата — кредитами
        # каскада. Поле openrouter_model для каскада справочное: сценарий
        # inbound_cascade/outbound_cascade держит модель у себя в LLM_MODEL.
        cascade_voice = voice if (voice and _is_valid_voice("cascade", voice)) else DEFAULT_CASCADE_VOICE
        va = GrokAssistantConfig(
            id=uuid.uuid4(), user_id=user_id, assistant_type="cascade",
            name=f"{name} Voice", system_prompt=prompt, greeting_message="",
            openrouter_model="openai/gpt-realtime-2.1-mini",
            temperature=0.7, max_tokens=1024,
            tts_provider="voxtts", tts_voice=cascade_voice,
            tts_lang="ru", asr_lang="ru",
            is_active=True, is_public=False, is_telephony_enabled=True,
            functions=_default_voice_functions(),
        )
    elif assistant_type == "fish":
        # Fish: OpenAI Realtime (текст) + озвучка Fish Audio в хендлере
        # backend/websockets/handler_fish.py на серверных ключах; виджет и
        # телефон (SIP-шлюз) общие. Голос — reference_id из библиотеки fish.audio.
        va = FishAssistantConfig(
            id=uuid.uuid4(), user_id=user_id, name=f"{name} Voice",
            system_prompt=prompt, greeting_message="", is_active=True,
            fish_voice_id=(fish_voice_id or None),
            fish_model=_valid_fish_model(fish_model),
            fish_latency=_valid_fish_latency(fish_latency),
            sample_rate=DEFAULT_FISH_SAMPLE_RATE,
            voice_speed=(voice_speed if voice_speed is not None else 1.0),
            llm_model=DEFAULT_FISH_LLM_MODEL, language="ru",
            functions=_default_voice_functions(),
        )
    elif assistant_type == "eleven":
        # Eleven: OpenAI Realtime (текст) + синтез ElevenLabs (handler_eleven.py) на серверных
        # ключах; телефон — собственный SIP-шлюз. Голос — voice_id из аккаунта ElevenLabs.
        eleven = eleven or {}
        va = ElevenAssistantConfig(
            id=uuid.uuid4(), user_id=user_id, name=f"{name} Voice",
            system_prompt=prompt, greeting_message="", is_active=True,
            voice_id=(eleven.get("eleven_voice_id") or None),
            voice_name=(eleven.get("eleven_voice_name") or None),
            tts_model=_valid_eleven_model(eleven.get("eleven_tts_model")),
            stability=_valid_eleven_stability(eleven.get("eleven_stability")),
            language=_valid_eleven_language(eleven.get("eleven_language")),
            voice_engine=_valid_voice_engine(eleven.get("eleven_voice_engine")),
            live_voice=_valid_live_voice(eleven.get("eleven_live_voice")),
            llm_model=DEFAULT_ELEVEN_LLM_MODEL,
            # send_sms без транспорта (Voximplant выключен) — голосу Eleven не даём
            functions=[f for f in _default_voice_functions() if f["name"] != "send_sms"],
        )
    else:
        raise HTTPException(status_code=400, detail="invalid_assistant_type")
    db.add(va)
    db.flush()
    return va


# Зависимости голосового ассистента, которые надо снять ПЕРЕД его удалением:
# (модель ассистента) → (модель диалогов провайдера, колонка FK в tasks).
# У Cartesia своей таблицы диалогов нет — там None.
_VOICE_ASSISTANT_DEPS = {
    GeminiAssistantConfig: (GeminiConversation, Task.gemini_assistant_id),
    AssistantConfig: (Conversation, Task.assistant_id),
    CartesiaAssistantConfig: (None, Task.cartesia_assistant_id),
    YandexAssistantConfig: (YandexConversation, Task.yandex_assistant_id),
    GrokAssistantConfig: (GrokConversation, Task.cascade_assistant_id),
    # У Fish своей таблицы диалогов нет — звонки пишутся в conversations.
    FishAssistantConfig: (None, Task.fish_assistant_id),
    ElevenAssistantConfig: (ElevenConversation, Task.eleven_assistant_id),
}


_VOICE_MODEL_BY_TYPE = {
    "gemini": GeminiAssistantConfig,
    "openai": AssistantConfig,
    "cartesia": CartesiaAssistantConfig,
    "yandex": YandexAssistantConfig,
    "cascade": GrokAssistantConfig,
    "fish": FishAssistantConfig,
    "eleven": ElevenAssistantConfig,
}


def _all_voice_assistant_targets(agent: AgentConfig):
    """[(модель, id)] по всем заполненным FK голосовых ассистентов агента.

    В норме заполнен ровно один FK, но у легаси-агентов (до v3.0) мог остаться
    хвост от прежней схемы — забираем всё, чтобы не плодить сирот.
    """
    pairs = (
        (GeminiAssistantConfig, agent.gemini_assistant_id),
        (AssistantConfig, agent.openai_assistant_id),
        (CartesiaAssistantConfig, agent.cartesia_assistant_id),
        (YandexAssistantConfig, agent.yandex_assistant_id),
        (GrokAssistantConfig, agent.cascade_assistant_id),
        (FishAssistantConfig, agent.fish_assistant_id),
        (ElevenAssistantConfig, agent.eleven_assistant_id),
    )
    return [(model_cls, va_id) for model_cls, va_id in pairs if va_id]


def _repoint_agent_tasks(db: Session, old_targets, new_type: str, new_va_id) -> int:
    """Перевести задачи агента со старых голосовых ассистентов на нового.

    Вызывается при смене assistant_type — до удаления старых ассистентов.
    Просто обнулить FK нельзя: планировщик резолвит голосового ассистента
    именно по нему (task_scheduler._get_assistant_info) и без него помечает
    запланированный звонок как FAILED «Assistant not found». Плюс delete_agent
    ищет по этому FK задачи-сироты, чей контакт удалили раньше.
    """
    new_column = _VOICE_ASSISTANT_DEPS[_VOICE_MODEL_BY_TYPE[new_type]][1]
    moved = 0
    for model_cls, va_id in old_targets:
        old_column = _VOICE_ASSISTANT_DEPS[model_cls][1]
        if old_column is new_column:
            continue
        moved += db.query(Task).filter(old_column == va_id).update(
            {old_column: None, new_column: new_va_id}, synchronize_session=False
        )
    db.flush()
    return moved


def _delete_voice_assistant(db: Session, model_cls, va_id, user_id) -> bool:
    """Удалить голосового ассистента агента вместе с его диалогами.

    Диалоги и ссылки из tasks снимаем ЯВНО, а не полагаемся на ON DELETE в БД:
    в старых схемах эти FK могли быть созданы без каскада, и тогда удаление
    падало бы с IntegrityError, откатывая всю транзакцию (агент не удалился бы
    вовсе). Возвращает True, если строка ассистента действительно удалена.
    """
    if not va_id:
        return False

    conv_cls, task_column = _VOICE_ASSISTANT_DEPS[model_cls]

    if conv_cls is not None:
        db.query(conv_cls).filter(conv_cls.assistant_id == va_id).delete(
            synchronize_session=False
        )
    db.query(Task).filter(task_column == va_id).update(
        {task_column: None}, synchronize_session=False
    )

    deleted = db.query(model_cls).filter(
        model_cls.id == va_id,
        model_cls.user_id == user_id,
    ).delete(synchronize_session=False)

    if deleted:
        # voximplant_phone_numbers.assistant_id — колонка без FK, БД её не
        # почистит. Номера, оставшиеся на удалённом ассистенте, отвязываем:
        # иначе телефония показывала бы привязку к несуществующей строке.
        from backend.models.voximplant_child import VoximplantPhoneNumber
        stale = db.query(VoximplantPhoneNumber).filter(
            VoximplantPhoneNumber.assistant_id == va_id
        ).update(
            {
                VoximplantPhoneNumber.assistant_type: None,
                VoximplantPhoneNumber.assistant_id: None,
            },
            synchronize_session=False,
        )
        if stale:
            logger.warning(
                f"[AGENT] Unbound {stale} phone number(s) from deleted assistant {va_id}"
            )

    db.flush()

    logger.info(
        f"[AGENT] Voice assistant {va_id} ({model_cls.__name__}) "
        f"{'deleted' if deleted else 'not found'}"
    )
    return bool(deleted)


async def _rebind_agent_phone_numbers(db: Session, agent: AgentConfig,
                                      new_type: str, new_assistant_id) -> int:
    """Перевести номера, привязанные к агенту, на его нового голосового ассистента.

    Вызывается при смене assistant_type. Без этого номер остался бы указывать на
    старого ассистента (которого мы удаляем) и на inbound-сценарий прежнего
    провайдера — входящие звонки сломались бы.

    Правило в Voximplant пересоздаём, т.к. сценарий зависит от типа
    (inbound_gemini / inbound_cascade / …). Ошибки Voximplant не роняют смену
    типа: привязка в БД всё равно обновляется, правило можно перевыпустить
    повторной привязкой номера на странице телефонии.
    """
    from backend.models.voximplant_child import VoximplantPhoneNumber
    from backend.services.sip_gateway_service import SipGatewayService

    # Номера собственного SIP-шлюза: рабочий путь телефонии.
    sip_rebound = SipGatewayService.sync_agent_numbers(db, agent.id, new_type, new_assistant_id)

    # Ниже — Voximplant (мёртвый код, ждёт удаления).
    numbers = db.query(VoximplantPhoneNumber).filter(
        VoximplantPhoneNumber.agent_config_id == agent.id
    ).all()
    if not numbers:
        return sip_rebound

    # Локальные импорты — telephony импортирует agent-модели, на уровне модуля
    # получилась бы циклическая зависимость.
    from backend.api.telephony import get_scenario_key, normalize_phone_number
    from backend.services.voximplant_partner import get_voximplant_partner_service

    scenario_name = get_scenario_key(new_type, "inbound")

    for num in numbers:
        num.assistant_type = new_type
        num.assistant_id = new_assistant_id

        child_account = num.child_account
        if not child_account or not num.vox_rule_id:
            continue

        raw_scenario_id = child_account.get_scenario_id(scenario_name)
        if not raw_scenario_id:
            logger.warning(
                f"[AGENT] Scenario '{scenario_name}' not found for account "
                f"{child_account.vox_account_id}, rule for {num.phone_number} left as is"
            )
            continue

        # Каскад: сначала vox-turn-taking (объявляет глобальный VoxTurnTaking),
        # затем сам inbound_cascade — как в telephony.bind_assistant_to_number.
        if new_type == "cascade":
            tt_id = child_account.get_scenario_id("vox-turn-taking")
            scenario_id = [int(tt_id), int(raw_scenario_id)] if tt_id else int(raw_scenario_id)
        else:
            scenario_id = int(raw_scenario_id)

        try:
            service = get_voximplant_partner_service()
            delete_result = await service.delete_rule(
                child_account_id=child_account.vox_account_id,
                child_api_key=child_account.vox_api_key,
                rule_id=num.vox_rule_id,
            )
            if not delete_result.get("success"):
                logger.error(
                    f"[AGENT] Failed to delete old rule {num.vox_rule_id}: "
                    f"{delete_result.get('error')}"
                )
                continue

            phone_pattern = normalize_phone_number(num.phone_number)
            new_rule = await service.add_rule(
                child_account_id=child_account.vox_account_id,
                child_api_key=child_account.vox_api_key,
                application_id=child_account.vox_application_id,
                rule_name=f"inbound_{phone_pattern}",
                rule_pattern=phone_pattern,
                scenario_id=scenario_id,
            )
            if new_rule.get("success"):
                num.vox_rule_id = str(new_rule.get("rule_id"))
                logger.info(
                    f"[AGENT] Rebound {num.phone_number} to {scenario_name} "
                    f"(rule {num.vox_rule_id})"
                )
            else:
                logger.error(
                    f"[AGENT] Failed to create rule for {num.phone_number}: "
                    f"{new_rule.get('error')}"
                )
        except Exception as e:
            logger.error(
                f"[AGENT] Voximplant rule update failed for {num.phone_number}: {e}",
                exc_info=True,
            )

    # Сессия с autoflush=False: сбрасываем новые привязки до того, как удаление
    # старого ассистента пройдётся bulk-запросом по voximplant_phone_numbers.
    db.flush()
    return len(numbers) + sip_rebound


def _agent_to_dict(agent: AgentConfig) -> dict:
    """Serialize AgentConfig to dict for API response."""
    voice = agent.get_voice_assistant() if agent.assistant_type else agent.gemini_assistant
    voice_name = voice.name if voice else None

    return {
        "id": str(agent.id),
        "user_id": str(agent.user_id),
        "assistant_type": agent.assistant_type,
        "assistant_id": str(agent.get_voice_assistant_id()) if agent.get_voice_assistant_id() else None,
        "gemini_assistant_id": str(agent.gemini_assistant_id) if agent.gemini_assistant_id else None,
        "openai_assistant_id": str(agent.openai_assistant_id) if agent.openai_assistant_id else None,
        "cartesia_assistant_id": str(agent.cartesia_assistant_id) if agent.cartesia_assistant_id else None,
        "yandex_assistant_id": str(agent.yandex_assistant_id) if agent.yandex_assistant_id else None,
        "cascade_assistant_id": str(agent.cascade_assistant_id) if agent.cascade_assistant_id else None,
        "fish_assistant_id": str(agent.fish_assistant_id) if agent.fish_assistant_id else None,
        "eleven_assistant_id": str(agent.eleven_assistant_id) if agent.eleven_assistant_id else None,
        "voice_assistant_name": voice_name,
        "gemini_assistant_name": voice_name,  # backward-compat for older frontend
        # Каскад хранит голос в tts_voice; остальные — в voice.
        "voice": getattr(voice, "tts_voice", None) if agent.assistant_type == "cascade" else getattr(voice, "voice", None),
        "cartesia_voice_id": getattr(voice, "cartesia_voice_id", None),
        "voice_speed": getattr(voice, "voice_speed", None),
        # Fish: голос задаётся reference_id из библиотеки fish.audio.
        "fish_voice_id": getattr(voice, "fish_voice_id", None),
        "fish_model": getattr(voice, "fish_model", None),
        "fish_latency": getattr(voice, "fish_latency", None),
        # ElevenLabs: voice_id — из аккаунта ElevenLabs на серверном ключе.
        "eleven_voice_id": getattr(voice, "voice_id", None) if agent.assistant_type == "eleven" else None,
        "eleven_voice_name": getattr(voice, "voice_name", None) if agent.assistant_type == "eleven" else None,
        "eleven_tts_model": getattr(voice, "tts_model", None) if agent.assistant_type == "eleven" else None,
        "eleven_language": getattr(voice, "language", None) if agent.assistant_type == "eleven" else None,
        "eleven_stability": getattr(voice, "stability", None) if agent.assistant_type == "eleven" else None,
        "eleven_voice_engine": (getattr(voice, "voice_engine", None) or DEFAULT_VOICE_ENGINE) if agent.assistant_type == "eleven" else None,
        "eleven_live_voice": getattr(voice, "live_voice", None) if agent.assistant_type == "eleven" else None,
        "name": agent.name,
        "is_active": agent.is_active,
        "orchestrator_model": agent.orchestrator_model,
        "orchestrator_prompt": agent.orchestrator_prompt,
        "uses_hardcoded_prompt": agent.uses_hardcoded_prompt,
        "doc_who_am_i": agent.doc_who_am_i,
        "doc_who_we_call": agent.doc_who_we_call,
        "doc_how_we_talk": agent.doc_how_we_talk,
        "doc_what_we_offer": agent.doc_what_we_offer,
        "doc_rules_and_goals": agent.doc_rules_and_goals,
        "additional_instructions": agent.additional_instructions,
        "voice_additional_instructions": agent.voice_additional_instructions,
        "inbound_first_phrase": agent.inbound_first_phrase,
        "working_hours_start": agent.working_hours_start,
        "working_hours_end": agent.working_hours_end,
        "call_window_enabled": bool(getattr(agent, "call_window_enabled", True)),
        "default_caller_id": agent.default_caller_id,
        "webhook_url": agent.webhook_url,
        # Фирменный стиль PDF-документов агента (ветка 1310-kp-design)
        "brand_company_name": agent.brand_company_name,
        "brand_color": agent.brand_color,
        "brand_contacts": agent.brand_contacts,
        "brand_logo_file_id": str(agent.brand_logo_file_id) if agent.brand_logo_file_id else None,
        "has_knowledge_base": agent.has_knowledge_base(),
        "kb_char_count": agent.kb_char_count or 0,
        "kb_name": agent.kb_name,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


# ============================================================================
# ENDPOINTS — AGENT CRUD
# ============================================================================


def _resolve_agent(db: Session, user: User, agent_id: Optional[str] = None) -> Optional[AgentConfig]:
    """
    Вернуть конкретного агента пользователя.
    Если agent_id задан — ищем именно его (в пределах user_id, чужой → None).
    Иначе — первого по дате создания (обратная совместимость с одним агентом).
    """
    q = db.query(AgentConfig).filter(AgentConfig.user_id == user.id)
    if agent_id:
        return q.filter(AgentConfig.id == agent_id).first()
    return q.order_by(AgentConfig.created_at.asc()).first()


@router.get("/list")
async def list_agents(
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db)
):
    """Список всех агентов пользователя (для меню выбора агента). JWT или X-Api-Key."""
    agents = (
        db.query(AgentConfig)
        .filter(AgentConfig.user_id == current_user.id)
        .order_by(AgentConfig.created_at.asc())
        .all()
    )
    return {
        "total": len(agents),
        "max_agents": _max_agents_for(current_user),
        "can_create_more": len(agents) < _max_agents_for(current_user) and current_user.has_agent_chat_access(),
        "has_agent_access": current_user.has_agent_chat_access(),
        # full — автономный агент (Profi), reactive — Старт «только отвечает», None — нет доступа
        "agent_mode": current_user.agent_mode(),
        "agents": [_agent_to_dict(a) for a in agents],
    }


@router.get("/")
async def get_agent(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db)
):
    """Get one of the user's AgentConfigs (defaults to the first one)."""
    agent = _resolve_agent(db, current_user, agent_id)

    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    return _agent_to_dict(agent)


@router.post("/create")
async def create_agent(
    body: AgentCreateRequest,
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db)
):
    """Create a new Voksy AI Agent v3.0 (one per user). No gpt-4o-mini generation."""
    # 1. Validate assistant_type: новые агенты — только ElevenLabs
    if body.assistant_type not in NEW_AGENT_ASSISTANT_TYPES:
        raise HTTPException(status_code=400, detail="invalid_assistant_type")

    # 2. Validate orchestrator_model
    orchestrator_model = body.orchestrator_model or get_default_model()
    if not is_valid_model(orchestrator_model):
        raise HTTPException(status_code=400, detail="invalid_orchestrator_model")
    # Устаревший слаг (напр. google/gemini-3.1-pro) приводим к актуальному —
    # в БД должно лежать только то, что реально существует на OpenRouter.
    orchestrator_model = resolve_slug(orchestrator_model)

    # 3. Верификация телефонии при создании агента НЕ требуется (v3.2):
    #    агент создаётся без телефонии, звонки недоступны до её подключения.
    #    _check_telephony_verified оставлена для обратной совместимости.

    # 4. Ключи голосового провайдера при создании НЕ требуются (v3.2):
    #    оркестратор работает на серверном ключе OpenRouter, голосовой ключ
    #    понадобится только для звонков. _check_assistant_keys оставлена
    #    для обратной совместимости.

    # 5. До MAX_AGENTS_PER_USER агентов на пользователя
    agents_count = db.query(AgentConfig).filter(
        AgentConfig.user_id == current_user.id
    ).count()
    if agents_count >= _max_agents_for(current_user):
        raise HTTPException(status_code=400, detail="agent_limit_reached")

    # 5b. Гейтинг доступа к агенту (v3.1): тестовый период / profi / legacy agent.
    #     Если триал уже использован и доступа нет — требуется тариф profi.
    #     Проверяем ДО создания, чтобы не оставлять «висячий» агент при 402.
    if current_user.agent_trial_used and not current_user.has_agent_chat_access():
        raise HTTPException(status_code=402, detail="subscription_required")

    # 6. Create the voice assistant with the hardcoded base prompt
    voice_assistant = _create_voice_assistant(
        body.assistant_type, body.name, current_user.id, db,
        voice=body.voice,
        cartesia_voice_id=body.cartesia_voice_id,
        voice_speed=body.voice_speed,
        voice_additional_instructions=body.voice_additional_instructions,
        fish_voice_id=body.fish_voice_id,
        fish_model=body.fish_model,
        fish_latency=body.fish_latency,
        eleven=body.dict(),
    )

    # 7. Create the AgentConfig (uses_hardcoded_prompt = TRUE, no orchestrator_prompt)
    agent = AgentConfig(
        id=uuid.uuid4(),
        user_id=current_user.id,
        name=body.name,
        assistant_type=body.assistant_type,
        gemini_assistant_id=voice_assistant.id if body.assistant_type == "gemini" else None,
        openai_assistant_id=voice_assistant.id if body.assistant_type == "openai" else None,
        cartesia_assistant_id=voice_assistant.id if body.assistant_type == "cartesia" else None,
        yandex_assistant_id=voice_assistant.id if body.assistant_type == "yandex" else None,
        cascade_assistant_id=voice_assistant.id if body.assistant_type == "cascade" else None,
        fish_assistant_id=voice_assistant.id if body.assistant_type == "fish" else None,
        eleven_assistant_id=voice_assistant.id if body.assistant_type == "eleven" else None,
        is_active=True,
        orchestrator_model=orchestrator_model,
        orchestrator_prompt=None,  # собирается на лету из захардкоженного шаблона
        doc_who_am_i=body.doc_who_am_i,
        doc_who_we_call=body.doc_who_we_call,
        doc_how_we_talk=body.doc_how_we_talk,
        doc_what_we_offer=body.doc_what_we_offer,
        doc_rules_and_goals=body.doc_rules_and_goals,
        additional_instructions=body.additional_instructions,
        voice_additional_instructions=body.voice_additional_instructions,
        inbound_first_phrase=body.inbound_first_phrase,
        working_hours_start=body.working_hours_start,
        working_hours_end=body.working_hours_end,
        call_window_enabled=body.call_window_enabled,
        uses_hardcoded_prompt=True,
        chat_history=[],
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    # 8. Активируем бесплатный trial тарифа agent при ПЕРВОМ создании
    #    (3 дня + 1500 кредитов). Если trial уже был — не выдаём повторно.
    trial_activated = False
    try:
        trial_activated = activate_agent_trial(db, current_user)
        if trial_activated:
            db.refresh(current_user)
    except Exception as e:
        logger.error(f"[AGENT] Trial activation failed for user {current_user.id}: {e}", exc_info=True)

    logger.info(
        f"[AGENT] Created v3 agent '{body.name}' ({body.assistant_type}) for user {current_user.id} "
        f"(trial_activated={trial_activated})"
    )
    result = _agent_to_dict(agent)
    result["trial_activated"] = trial_activated
    result["agent_trial_used"] = bool(current_user.agent_trial_used)
    return result


@router.put("/")
async def update_agent(
    body: AgentUpdateRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db)
):
    """Update the agent's documents and settings."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    update_data = body.dict(exclude_unset=True)

    # ── Смена типа голосового ассистента ──
    new_type = update_data.get("assistant_type")
    if new_type and new_type != agent.assistant_type:
        # Сменить тип можно только на ElevenLabs (старые провайдеры в кабинете закрыты).
        if new_type not in NEW_AGENT_ASSISTANT_TYPES:
            raise HTTPException(status_code=400, detail="invalid_assistant_type")
        # Ключи голосового провайдера при смене типа не требуются (v3.2) —
        # как и при создании; проверка вернётся вместе с телефонией.
        # Старых ассистентов запоминаем ДО обнуления FK — после смены типа они
        # никому не нужны и удаляются (иначе копились бы «сироты»: висят в
        # списках провайдера и съедают лимит ассистентов тарифа).
        old_voice_targets = _all_voice_assistant_targets(agent)
        new_voice = _create_voice_assistant(
            new_type, agent.name, current_user.id, db,
            voice_additional_instructions=agent.voice_additional_instructions,
            fish_voice_id=update_data.get("fish_voice_id"),
            fish_model=update_data.get("fish_model"),
            fish_latency=update_data.get("fish_latency"),
            eleven=update_data,
        )
        agent.gemini_assistant_id = None
        agent.openai_assistant_id = None
        agent.cartesia_assistant_id = None
        agent.yandex_assistant_id = None
        agent.cascade_assistant_id = None
        agent.fish_assistant_id = None
        agent.eleven_assistant_id = None
        if new_type == "gemini":
            agent.gemini_assistant_id = new_voice.id
        elif new_type == "openai":
            agent.openai_assistant_id = new_voice.id
        elif new_type == "cartesia":
            agent.cartesia_assistant_id = new_voice.id
        elif new_type == "yandex":
            agent.yandex_assistant_id = new_voice.id
        elif new_type == "cascade":
            agent.cascade_assistant_id = new_voice.id
        elif new_type == "fish":
            agent.fish_assistant_id = new_voice.id
        elif new_type == "eleven":
            agent.eleven_assistant_id = new_voice.id
        agent.assistant_type = new_type
        # Если у агента есть база знаний — переносим функцию поиска на нового
        # голосового ассистента.
        _voice_sync_kb_functions(db, agent, new_voice)
        # Переносим голосовые функции подключённых коннекторов (Composio) на
        # нового голосового ассистента — иначе при смене типа они терялись бы.
        for _conn in db.query(AgentConnector).filter(
            AgentConnector.agent_config_id == agent.id,
            AgentConnector.status == "connected",
        ).all():
            _voice_set_connector_function(new_voice, _conn.toolkit, True)

        # FK на старых ассистентов уже сняты — сбрасываем это в БД до их удаления.
        db.flush()

        # Запланированные звонки переводим на нового ассистента — иначе после
        # удаления старого планировщик уронил бы их с «Assistant not found».
        moved_tasks = _repoint_agent_tasks(db, old_voice_targets, new_type, new_voice.id)

        # Номера агента переводим на нового ассистента (и на inbound-сценарий
        # нового провайдера), иначе входящие ушли бы к удалённому ассистенту.
        rebound = await _rebind_agent_phone_numbers(db, agent, new_type, new_voice.id)

        for _model_cls, _va_id in old_voice_targets:
            _delete_voice_assistant(db, _model_cls, _va_id, current_user.id)

        logger.info(
            f"[AGENT] Switched assistant_type to {new_type} for user {current_user.id} "
            f"(old voice assistants removed: {len(old_voice_targets)}, "
            f"tasks moved: {moved_tasks}, phones rebound: {rebound})"
        )

    # ── Смена модели оркестратора ──
    if "orchestrator_model" in update_data and update_data["orchestrator_model"]:
        if not is_valid_model(update_data["orchestrator_model"]):
            raise HTTPException(status_code=400, detail="invalid_orchestrator_model")
        agent.orchestrator_model = resolve_slug(update_data["orchestrator_model"])

    docs_changed = False
    doc_fields = ['doc_who_am_i', 'doc_who_we_call', 'doc_how_we_talk',
                  'doc_what_we_offer', 'doc_rules_and_goals']

    for field in doc_fields:
        if field in update_data and update_data[field] is not None:
            setattr(agent, field, update_data[field])
            docs_changed = True

    for field in ['name', 'additional_instructions', 'voice_additional_instructions',
                  'inbound_first_phrase',
                  'working_hours_start', 'working_hours_end', 'call_window_enabled',
                  'is_active', 'default_caller_id',
                  'webhook_url',
                  # Фирменный стиль PDF-документов агента (ветка 1310-kp-design)
                  'brand_company_name', 'brand_contacts']:
        if field in update_data:
            setattr(agent, field, update_data[field])

    # Окно звонков «с start:00 до end:00» — без перехода через полночь.
    if ('working_hours_start' in update_data or 'working_hours_end' in update_data) \
            and agent.working_hours_start >= agent.working_hours_end:
        raise HTTPException(status_code=400, detail="Время звонков: «с» должно быть раньше, чем «до»")

    # Цвет шапки документов: принимаем «#2a5ce8» и «2a5ce8», пустую строку —
    # как сброс к нейтральному оформлению.
    if 'brand_color' in update_data:
        raw = (update_data['brand_color'] or "").strip()
        if not raw:
            agent.brand_color = None
        else:
            color = raw if raw.startswith("#") else "#" + raw
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise HTTPException(status_code=400, detail="invalid_brand_color")
            agent.brand_color = color.lower()

    # Логотип — файл из библиотеки агента (agent_files), пустое значение снимает.
    if 'brand_logo_file_id' in update_data:
        raw = (update_data['brand_logo_file_id'] or "").strip() if isinstance(update_data['brand_logo_file_id'], str) else update_data['brand_logo_file_id']
        if not raw:
            agent.brand_logo_file_id = None
        else:
            from backend.models.agent_file import AgentFile
            logo = db.query(AgentFile).filter(
                AgentFile.id == raw,
                AgentFile.agent_config_id == agent.id,
            ).first()
            if logo is None:
                raise HTTPException(status_code=404, detail="logo_file_not_found")
            # У модели AgentFile поле называется mime_type: обращение к .mime
            # роняло сохранение логотипа с AttributeError → 500 (ветка 1610-kb-calls).
            if not (logo.mime_type or "").startswith("image/"):
                raise HTTPException(status_code=400, detail="logo_must_be_image")
            agent.brand_logo_file_id = logo.id

    # ── Инструкции голосового агента: пересобираем system_prompt ассистента ──
    if 'voice_additional_instructions' in update_data:
        va = _resolve_voice_assistant(db, agent)
        if va is not None:
            va.system_prompt = build_voice_agent_prompt(agent.voice_additional_instructions)

    # ── Голос: пишем в связанный голосовой конфиг ──
    voice_touched = any(k in update_data for k in (
        "voice", "cartesia_voice_id", "voice_speed",
        "fish_voice_id", "fish_model", "fish_latency",
        "eleven_voice_id", "eleven_tts_model", "eleven_language", "eleven_stability",
        "eleven_voice_engine", "eleven_live_voice",
    ))
    if voice_touched:
        va = _resolve_voice_assistant(db, agent)
        if va is not None:
            if agent.assistant_type in ("gemini", "openai", "yandex"):
                new_voice = update_data.get("voice")
                if new_voice:
                    if not _is_valid_voice(agent.assistant_type, new_voice):
                        raise HTTPException(status_code=400, detail="invalid_voice")
                    va.voice = new_voice
            elif agent.assistant_type == "cascade":
                # Каскад хранит голос в tts_voice (VoxTTS), не в .voice.
                new_voice = update_data.get("voice")
                if new_voice:
                    if not _is_valid_voice("cascade", new_voice):
                        raise HTTPException(status_code=400, detail="invalid_voice")
                    va.tts_voice = new_voice
            elif agent.assistant_type == "cartesia":
                if "cartesia_voice_id" in update_data:
                    va.cartesia_voice_id = update_data["cartesia_voice_id"] or None
                if update_data.get("voice_speed") is not None:
                    va.voice_speed = update_data["voice_speed"]
            elif agent.assistant_type == "fish":
                if "fish_voice_id" in update_data:
                    va.fish_voice_id = update_data["fish_voice_id"] or None
                if update_data.get("fish_model"):
                    va.fish_model = _valid_fish_model(update_data["fish_model"])
                if update_data.get("fish_latency"):
                    va.fish_latency = _valid_fish_latency(update_data["fish_latency"])
                if update_data.get("voice_speed") is not None:
                    va.voice_speed = update_data["voice_speed"]
            elif agent.assistant_type == "eleven":
                if "eleven_voice_id" in update_data:
                    va.voice_id = update_data["eleven_voice_id"] or None
                    va.voice_name = update_data.get("eleven_voice_name") or None
                if update_data.get("eleven_tts_model"):
                    va.tts_model = _valid_eleven_model(update_data["eleven_tts_model"])
                if update_data.get("eleven_language"):
                    va.language = _valid_eleven_language(update_data["eleven_language"])
                if update_data.get("eleven_stability") is not None:
                    va.stability = _valid_eleven_stability(update_data["eleven_stability"])
                if update_data.get("eleven_voice_engine"):
                    va.voice_engine = _valid_voice_engine(update_data["eleven_voice_engine"])
                if "eleven_live_voice" in update_data:
                    va.live_voice = _valid_live_voice(update_data["eleven_live_voice"])

    # ── Регенерация промпта через gpt-4o-mini — ТОЛЬКО для старых агентов ──
    if docs_changed and not agent.uses_hardcoded_prompt:
        if not current_user.openai_api_key:
            raise HTTPException(status_code=400, detail="openai_key_required")
        try:
            agent.orchestrator_prompt = await _generate_orchestrator_prompt(
                doc_who_am_i=agent.doc_who_am_i or "",
                doc_who_we_call=agent.doc_who_we_call or "",
                doc_how_we_talk=agent.doc_how_we_talk or "",
                doc_what_we_offer=agent.doc_what_we_offer or "",
                doc_rules_and_goals=agent.doc_rules_and_goals or "",
                openai_api_key=current_user.openai_api_key
            )
        except Exception as e:
            logger.error(f"[AGENT] Failed to regenerate prompt: {e}")
            raise HTTPException(status_code=500, detail=f"prompt_generation_failed: {str(e)}")

    db.commit()
    db.refresh(agent)

    logger.info(f"[AGENT] Updated agent for user {current_user.id}")
    return _agent_to_dict(agent)


@router.delete("/")
async def delete_agent(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Полное удаление ОДНОГО агента и связанных с ним данных:
    - tasks этого агента: и по его контактам, и по его голосовому ассистенту
      (вторые — «сироты» задач, чей контакт удалили раньше)
    - agent_calls и agent_contacts (каскадятся через FK AgentConfig)
    - отвязка телефонных номеров агента (номера остаются у пользователя)
    - AgentConfig
    - голосовой ассистент(ы), привязанные именно к этому агенту, вместе с их
      диалогами
    """
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    summary = {"tasks": 0, "voice_assistants": 0, "phone_numbers_unbound": 0}

    try:
        # id контактов этого агента — чтобы удалить только его задачи,
        # не задев задачи других агентов того же пользователя.
        contact_ids = [
            row[0] for row in db.query(AgentContact.id).filter(
                AgentContact.agent_config_id == agent.id
            ).all()
        ]

        # 0. Считаем SCHEDULED-задачи этого агента ДО удаления — для журнала.
        #
        # Задача принадлежит агенту, если она указывает на его контакт ИЛИ на
        # его голосового ассистента. Второе условие обязательно: у задачи,
        # чей контакт удалили раньше, agent_contact_id обнуляется
        # (FK ondelete=SET NULL), и по контактам её уже не найти — а FK на
        # ассистента у неё остаётся и блокирует удаление ассистента.
        task_ownership = []
        if contact_ids:
            task_ownership.append(Task.agent_contact_id.in_(contact_ids))
        for column, va_id in (
            (Task.gemini_assistant_id, agent.gemini_assistant_id),
            (Task.assistant_id, agent.openai_assistant_id),
            (Task.cartesia_assistant_id, agent.cartesia_assistant_id),
            (Task.yandex_assistant_id, agent.yandex_assistant_id),
            (Task.cascade_assistant_id, agent.cascade_assistant_id),
            (Task.fish_assistant_id, agent.fish_assistant_id),
            (Task.eleven_assistant_id, agent.eleven_assistant_id),
        ):
            if va_id:
                task_ownership.append(column == va_id)

        task_filter = [
            Task.user_id == current_user.id,
            Task.is_agent_task == True,
            or_(*task_ownership) if task_ownership else false(),
        ]

        scheduled_count = db.query(Task).filter(
            *task_filter, Task.status == TaskStatus.SCHEDULED,
        ).count()

        # Логируем факт удаления агента (баланс кредитов НЕ меняется).
        from backend.models.credit_transaction import CreditTransaction, CreditTransactionType
        db.add(CreditTransaction(
            user_id=current_user.id,
            type=CreditTransactionType.MANUAL_ADJUST.value,
            amount=0,
            balance_after=current_user.credits_balance or 0,
            ref_type="agent_deleted",
            ref_id=agent.id,
            notes=(
                f"Agent {agent.id} deleted by user. Tasks cancelled: {scheduled_count}. "
                f"Credits balance preserved: {current_user.credits_balance}."
            ),
        ))

        # 1. tasks этого агента ПЕРВЫМИ (иначе ON DELETE SET NULL осиротит их).
        #    Тот же фильтр принадлежности, что и при подсчёте выше.
        summary["tasks"] = db.query(Task).filter(*task_filter).delete(
            synchronize_session=False
        )

        # 2. Голосовые ассистенты, привязанные именно к этому агенту.
        va_targets = _all_voice_assistant_targets(agent)

        # 3. Отвязываем номера агента ДО его удаления. FK agent_config_id и так
        #    обнулится каскадом (ON DELETE SET NULL), но assistant_type/assistant_id
        #    в voximplant_phone_numbers — обычные колонки без FK: без явной
        #    очистки номер остался бы висеть на удалённом голосовом ассистенте,
        #    и входящие на него ломались бы.
        #    vox_rule_id намеренно НЕ трогаем: правило переиспользуется при
        #    следующей привязке номера (telephony пересоздаёт его только если
        #    vox_rule_id заполнен).
        from backend.models.voximplant_child import VoximplantPhoneNumber
        from backend.services.sip_gateway_service import SipGatewayService
        agent_numbers = db.query(VoximplantPhoneNumber).filter(
            VoximplantPhoneNumber.agent_config_id == agent.id
        ).all()
        for num in agent_numbers:
            num.assistant_type = None
            num.assistant_id = None
            num.first_phrase = None
            num.agent_config_id = None
        # Номера собственного SIP-шлюза (рабочий путь) отвязываем так же.
        summary["phone_numbers_unbound"] = len(agent_numbers) + SipGatewayService.unbind_agent_numbers(db, agent.id)

        # 4. AgentConfig — каскадом уносит agent_contacts и agent_calls.
        db.delete(agent)
        db.flush()

        summary["voice_assistants"] = sum(
            _delete_voice_assistant(db, model_cls, va_id, current_user.id)
            for model_cls, va_id in va_targets
        )

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[AGENT] Delete failed for user {current_user.id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"delete_failed: {str(e)}")

    logger.info(
        f"[AGENT] Fully deleted agent for user {current_user.id}: "
        f"{summary['tasks']} tasks, {summary['voice_assistants']} voice assistants, "
        f"{summary['phone_numbers_unbound']} phone numbers unbound"
    )
    return {"detail": "deleted", "summary": summary}


# ============================================================================
# ENDPOINTS — KNOWLEDGE BASE (Pinecone vector DB)
# ============================================================================


# Максимальный размер базы знаний агента (символов).
MAX_KB_CHARS = 200_000


class KnowledgeBaseRequest(BaseModel):
    content: str = Field(..., min_length=1)
    name: Optional[str] = Field(None, max_length=100)


def _kb_status_dict(agent: AgentConfig) -> dict:
    return {
        "has_knowledge_base": agent.has_knowledge_base(),
        "namespace": agent.kb_namespace,
        "char_count": agent.kb_char_count or 0,
        "name": agent.kb_name,
        "content": agent.kb_content or "",
        "updated_at": agent.kb_updated_at.isoformat() if agent.kb_updated_at else None,
    }


@router.get("/knowledge-base")
async def get_agent_knowledge_base(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Статус базы знаний агента."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    return _kb_status_dict(agent)


@router.post("/knowledge-base")
async def upsert_agent_knowledge_base(
    body: KnowledgeBaseRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Создать или обновить базу знаний агента.

    Эмбеддинги считаются на системном ключе OPENAI_API_KEY (оркестратор v3
    работает на кредитах). Namespace переиспользуется при обновлении.
    """
    import os
    from backend.services.pinecone_service import PineconeService

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="openai_key_not_configured")

    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="empty_content")
    if len(content) > MAX_KB_CHARS:
        raise HTTPException(status_code=400, detail="kb_too_large")

    try:
        namespace, char_count = await PineconeService.create_or_update_knowledge_base(
            content=content,
            api_key=api_key,
            namespace=agent.kb_namespace,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AGENT-KB] create/update failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"knowledge_base_failed: {e}")

    agent.kb_namespace = namespace
    agent.kb_char_count = char_count
    agent.kb_content = content
    agent.kb_name = body.name or agent.kb_name
    agent.kb_updated_at = datetime.utcnow()

    # Приводим функции знаний голосовой карточки к текущему составу базы.
    _voice_sync_kb_functions(db, agent)

    db.commit()
    db.refresh(agent)
    logger.info(f"[AGENT-KB] Knowledge base saved for agent {agent.id} (ns={namespace}, {char_count} chars)")
    return {"success": True, **_kb_status_dict(agent)}


@router.delete("/knowledge-base")
async def delete_agent_knowledge_base(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Удалить базу знаний агента (из Pinecone и из БД) и выключить поиск у голоса."""
    from backend.services.pinecone_service import PineconeService

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    if agent.kb_namespace:
        try:
            await PineconeService.delete_knowledge_base(agent.kb_namespace)
        except Exception as e:
            logger.warning(f"[AGENT-KB] Pinecone delete failed (continuing): {e}")

    agent.kb_namespace = None
    agent.kb_char_count = 0
    agent.kb_content = None
    agent.kb_name = None
    agent.kb_updated_at = None

    _voice_sync_kb_functions(db, agent)

    db.commit()
    db.refresh(agent)
    logger.info(f"[AGENT-KB] Knowledge base deleted for agent {agent.id}")
    return {"success": True, **_kb_status_dict(agent)}


# ============================================================================
# БАЗА ЗНАНИЙ v2 (ветка 1410-kb-v2): записи, каталог, документы, пробелы
# ============================================================================

class KnowledgeEntryRequest(BaseModel):
    kind: Optional[str] = None                       # qa | section | product | document
    title: Optional[str] = Field(None, max_length=500)
    body: Optional[str] = None
    keywords: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = Field(None, max_length=8)
    attributes: Optional[dict] = None
    in_stock: Optional[bool] = None
    media_file_ids: Optional[List[str]] = None
    in_prompt: Optional[bool] = None
    is_active: Optional[bool] = None
    position: Optional[int] = None


class KnowledgeTestRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


def _kb_media_ids(db: Session, agent: AgentConfig, ids) -> Optional[list]:
    """
    Отфильтровать id фото/видео позиции: берём только файлы этого агента.

    Иначе в позицию каталога можно прикрепить файл другого агента, и он уехал
    бы клиенту вместе с ответом. Непохожие на id значения просто отбрасываем.
    """
    from backend.models.agent_file import AgentFile

    if not ids:
        return None
    clean = []
    for raw in ids[:12]:
        try:
            clean.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError, AttributeError):
            continue
    if not clean:
        return None
    owned = {
        str(fid) for (fid,) in db.query(AgentFile.id).filter(
            AgentFile.id.in_(clean), AgentFile.agent_config_id == agent.id).all()
    }
    out = [str(x) for x in clean if str(x) in owned]
    return out or None


def _kb_entry_or_404(db: Session, agent: AgentConfig, entry_id: str):
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    row = db.query(AgentKnowledgeEntry).filter(
        AgentKnowledgeEntry.id == entry_id,
        AgentKnowledgeEntry.agent_config_id == agent.id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="entry_not_found")
    return row


@router.get("/knowledge")
async def list_knowledge_entries(
    kind: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Записи базы знаний агента. Без kind — сводка по всем разделам: сколько
    записей в каждом и сколько знаний реально уходит в промпт (владельцу важно
    видеть, что агент знает «наизусть», а за чем ходит в поиск).
    """
    from backend.models.agent_knowledge import KB_KINDS, AgentKnowledgeEntry
    from backend.services.agent_knowledge_service import build_prompt_block

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    q = db.query(AgentKnowledgeEntry).filter(AgentKnowledgeEntry.agent_config_id == agent.id)
    if kind:
        if kind not in KB_KINDS:
            raise HTTPException(status_code=400, detail="invalid_kind")
        q = q.filter(AgentKnowledgeEntry.kind == kind)
    rows = q.order_by(AgentKnowledgeEntry.kind.asc(),
                      AgentKnowledgeEntry.position.asc(),
                      AgentKnowledgeEntry.created_at.asc()).all()

    counts = {k: 0 for k in KB_KINDS}
    for r in db.query(AgentKnowledgeEntry.kind).filter(
            AgentKnowledgeEntry.agent_config_id == agent.id).all():
        counts[r[0]] = counts.get(r[0], 0) + 1

    prompt_block = build_prompt_block(db, agent.id)
    entries = _entries_with_media(db, agent, rows)
    return {
        "entries": entries,
        "counts": counts,
        # Сколько символов знаний агент получает мгновенно, без поиска.
        "prompt_chars": len(prompt_block),
        # Что именно агент знает наизусть на звонке: тот же расчёт, что уходит
        # в промпт (services/agent_voice_context.py), чтобы владелец видел
        # реальную картину, а не только размер базы.
        "voice": _voice_knowledge_summary(db, agent),
    }


def _entries_with_media(db: Session, agent: AgentConfig, rows: list) -> list:
    """
    Записи базы вместе с картинками позиций каталога.

    Каталог без фотографий бесполезен: владелец ориентируется по виду товара,
    а не по строке с названием. Поэтому к позиции прикладываются подписанные
    ссылки на её фото — страница рисует плитки.
    """
    from backend.models.agent_file import AgentFile
    from backend.services import agent_media_service

    wanted = set()
    for row in rows:
        if row.kind == "product":
            for fid in (row.media_file_ids or []):
                wanted.add(str(fid))

    media: dict = {}
    if wanted:
        try:
            files = db.query(AgentFile).filter(
                AgentFile.agent_config_id == agent.id,
                AgentFile.id.in_(list(wanted)),
            ).all()
            for f in files:
                media[str(f.id)] = {
                    "id": str(f.id),
                    "filename": f.filename,
                    "title": f.title,
                    "mime_type": f.mime_type,
                    "is_video": (f.mime_type or "").startswith("video/"),
                    "url": agent_media_service.file_link(f, expires_seconds=FILE_PREVIEW_TTL),
                }
        except Exception as e:
            logger.warning(f"[AGENT-KB] фото позиций не прочитаны: {e}")

    out = []
    for row in rows:
        item = row.to_dict()
        if row.kind == "product":
            item["media"] = [media[str(f)] for f in (row.media_file_ids or [])
                             if str(f) in media]
        out.append(item)
    return out


def _voice_knowledge_summary(db: Session, agent: AgentConfig) -> dict:
    """
    Сводка «что агент знает наизусть на звонке»: размеры блоков промпта и
    общий размер. Считается тем же кодом, что собирает промпт звонка, —
    расхождения между кабинетом и трубкой исключены по построению.
    """
    try:
        from backend.services import agent_voice_context
        from backend.websockets.live_client import LIVE_VOICE_INSTRUCTIONS_MAX_CHARS

        block, stats = agent_voice_context.build_voice_knowledge_block(db, agent)
        return {
            "chars": stats.get("total", 0),
            "company": stats.get("company", 0),
            "knowledge": stats.get("knowledge", 0),
            "price": stats.get("price", 0),
            "files": stats.get("files", 0),
            "budget": agent_voice_context.VOICE_KNOWLEDGE_BUDGET,
            "voice_layer_limit": LIVE_VOICE_INSTRUCTIONS_MAX_CHARS,
        }
    except Exception as e:
        logger.warning(f"[AGENT-KB] сводка знаний звонка не собрана: {e}")
        return {}


@router.post("/knowledge")
async def create_knowledge_entry(
    body: KnowledgeEntryRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Добавить запись: вопрос-ответ, секцию или позицию каталога."""
    from backend.models.agent_knowledge import KB_KINDS, AgentKnowledgeEntry

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    kind = (body.kind or "section").strip()
    if kind not in KB_KINDS:
        raise HTTPException(status_code=400, detail="invalid_kind")
    title = (body.title or "").strip()
    text = (body.body or "").strip()
    if not title and not text:
        raise HTTPException(status_code=400, detail="empty_entry")

    row = AgentKnowledgeEntry(
        user_id=current_user.id,
        agent_config_id=agent.id,
        kind=kind,
        title=title[:500],
        body=text,
        keywords=(body.keywords or "").strip() or None,
        price=body.price,
        currency=(body.currency or "KGS") if kind == "product" else None,
        attributes=body.attributes if kind == "product" else None,
        in_stock=True if body.in_stock is None else bool(body.in_stock),
        media_file_ids=_kb_media_ids(db, agent, body.media_file_ids),
        # Каталог и документы в промпт не вшиваем: их много, они съедят бюджет
        # и замедлят каждый ответ. Они достаются поиском.
        in_prompt=(kind in ("qa", "section")) if body.in_prompt is None else bool(body.in_prompt),
        is_active=True if body.is_active is None else bool(body.is_active),
        position=body.position or 0,
        char_count=len(text),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _voice_sync_kb_functions(db, agent)
    db.commit()
    kb_vectors.schedule_sync(agent.id)  # вектор записи — в фоне, ответ клиенту не ждёт
    return {"success": True, "entry": row.to_dict()}


@router.put("/knowledge/{entry_id}")
async def update_knowledge_entry(
    entry_id: str,
    body: KnowledgeEntryRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Изменить одну запись, не трогая остальные (раньше правка = перезалив всей базы)."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    row = _kb_entry_or_404(db, agent, entry_id)

    data = body.dict(exclude_unset=True)
    for field in ("title", "body", "keywords", "price", "currency", "attributes",
                  "in_stock", "media_file_ids", "in_prompt", "is_active", "position"):
        if field in data:
            value = data[field]
            if field in ("title", "keywords") and isinstance(value, str):
                value = value.strip() or None
                if field == "title":
                    value = (value or "")[:500]
            if field == "media_file_ids":
                value = _kb_media_ids(db, agent, value)
            setattr(row, field, value)
    if "body" in data:
        row.char_count = len(row.body or "")
    # Изменилось то, что попадает в вектор, — пометить на переиндексацию (досинхронизирует
    # и фоновая проверка при поиске, если запуск ниже не успеет)
    if any(f in data for f in ("title", "body", "keywords", "price", "currency", "attributes", "is_active")):
        row.vector_status = "pending"
    # Запись могли выключить — тогда у карточки голоса мог стать лишним
    # каталог или поиск целиком.
    _voice_sync_kb_functions(db, agent)
    db.commit()
    db.refresh(row)
    kb_vectors.schedule_sync(agent.id)
    return {"success": True, "entry": row.to_dict()}


@router.delete("/knowledge/{entry_id}")
async def delete_knowledge_entry(
    entry_id: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    row = _kb_entry_or_404(db, agent, entry_id)
    deleted_id = str(row.id)
    db.delete(row)
    db.flush()
    # Удалили последнюю позицию каталога — find_products больше не нужен.
    _voice_sync_kb_functions(db, agent)
    db.commit()
    kb_vectors.schedule_delete(agent.id, [deleted_id])
    return {"success": True}


# ============================================================================
# GOOGLE ТАБЛИЦА АГЕНТА: заявки и прайс (services/sheets_leads.py)
# ============================================================================

class SheetConnectRequest(BaseModel):
    url: str = Field(..., max_length=500)


def _sheet_dict(agent: AgentConfig) -> dict:
    from backend.services import sheets_leads
    sid = agent.leads_sheet_id
    return {
        "connected": bool(sid),
        "sheet_id": sid,
        "title": agent.leads_sheet_title,
        "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else None,
        "service_email": sheets_leads.service_account_email(),
        "leads_sheet": sheets_leads.LEADS_SHEET_TITLE,
    }


@router.get("/sheet")
async def get_agent_sheet(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    return _sheet_dict(agent)


@router.post("/sheet")
async def connect_agent_sheet(
    body: SheetConnectRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Подключить таблицу: проверяем доступ сразу, чтобы владелец увидел ошибку здесь, а не в пустой таблице."""
    from backend.services import sheets_leads
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    sid = sheets_leads.parse_sheet_id(body.url)
    if not sid:
        raise HTTPException(status_code=400, detail="Не похоже на ссылку на Google Таблицу.")
    try:
        info = await asyncio.to_thread(sheets_leads.check_access, sid)
    except sheets_leads.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    agent.leads_sheet_id = sid
    agent.leads_sheet_title = (info.get("title") or "")[:255]
    db.commit()
    db.refresh(agent)
    return _sheet_dict(agent)


@router.delete("/sheet")
async def disconnect_agent_sheet(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    agent.leads_sheet_id = None
    agent.leads_sheet_title = None
    db.commit()
    db.refresh(agent)
    return _sheet_dict(agent)


@router.post("/sheet/import-catalog")
async def import_catalog_from_sheet(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Первый лист подключённой таблицы → позиции каталога базы знаний."""
    from backend.services import sheets_leads
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    if not agent.leads_sheet_id:
        raise HTTPException(status_code=400, detail="Сначала подключите Google Таблицу.")
    try:
        products = await asyncio.to_thread(sheets_leads.read_catalog, agent.leads_sheet_id)
    except sheets_leads.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = sheets_leads.import_catalog(db, agent, current_user.id, products)
    _voice_sync_kb_functions(db, agent)
    db.commit()
    kb_vectors.schedule_sync(agent.id)
    return {"success": True, **result, "total": len(products)}


@router.post("/knowledge/reindex")
async def reindex_knowledge_vectors(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Переиндексировать записи базы (вопросы-ответы, секции, каталог) для поиска по смыслу:
    всё заново, лишние вектора убираются. Обычно не нужно — правки индексируются сами, а старые
    записи подтягиваются фоновой проверкой при поиске; кнопка на случай сомнений.
    """
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    if not kb_vectors.enabled():
        return {"success": False, "enabled": False,
                "detail": "Векторный поиск выключен (KB_VECTOR_ENABLED=0 или нет ключей Pinecone / OpenAI)"}
    stats = await kb_vectors.sync_agent(agent.id, force=True)
    return {"success": True, "enabled": True, **stats}


@router.post("/knowledge/test")
async def test_knowledge_search(
    body: KnowledgeTestRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    «Спросить как клиент»: владелец вводит вопрос и видит, что найдёт агент и
    откуда — из промпта (мгновенно) или поиском. Без этого база отлаживается
    только живыми звонками.
    """
    from backend.services import agent_knowledge_service as kb

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    query = body.query.strip()
    prompt_block = kb.build_prompt_block(db, agent.id)
    words = kb.keywords_of(query)
    # Знание «в промпте» — агент ответит сразу, не вызывая поиск.
    in_prompt_hit = bool(words) and any(w in prompt_block.lower() for w in words)

    rows = kb.search_entries(db, agent.id, query, limit=5)
    return {
        "query": query,
        "answered_from_prompt": in_prompt_hit,
        "results": [
            {**r.to_dict(), "kind": r.kind, "source": "промпт" if r.in_prompt else "поиск"}
            for r in rows
        ],
        "note": (
            "Агент ответит мгновенно — знание уже в его инструкции."
            if in_prompt_hit else
            "Агент найдёт это поиском (чуть дольше)." if rows else
            "Ответа в базе нет — агент скажет, что уточнит у специалиста."
        ),
    }


class KnowledgeDocumentRequest(BaseModel):
    file_id: str = Field(..., max_length=64)
    title: Optional[str] = Field(None, max_length=500)


@router.post("/knowledge/document")
async def add_knowledge_document(
    body: KnowledgeDocumentRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Добавить в базу знаний уже загруженный файл агента (PDF, Word, Excel,
    текст). Так библиотека файлов и база знаний становятся одним целым: один
    и тот же файл агент и отправляет клиенту, и использует как источник
    ответов.

    Текст берётся из того, что уже извлёк agent_media_service при загрузке, и
    векторизуется отдельно от остальных документов — правка одного файла не
    перетирает всю базу.
    """
    import os
    import uuid as _uuid
    from backend.models.agent_file import AgentFile
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    from backend.services import pinecone_service as pinecone_mod

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    file_row = db.query(AgentFile).filter(
        AgentFile.id == body.file_id,
        AgentFile.agent_config_id == agent.id,
    ).first()
    if file_row is None:
        raise HTTPException(status_code=404, detail="file_not_found")

    text = (file_row.extracted_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="file_has_no_text")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="openai_key_not_configured")

    row = db.query(AgentKnowledgeEntry).filter(
        AgentKnowledgeEntry.agent_config_id == agent.id,
        AgentKnowledgeEntry.file_id == file_row.id,
    ).first()
    if row is None:
        row = AgentKnowledgeEntry(
            user_id=current_user.id,
            agent_config_id=agent.id,
            kind="document",
            title=(body.title or file_row.title or file_row.filename or "Документ")[:500],
            body=(file_row.description or "").strip(),
            file_id=file_row.id,
            in_prompt=False,          # документы достаются поиском, не промптом
        )
        db.add(row)
        db.flush()

    namespace = agent.kb_namespace or f"kb-{_uuid.uuid4().hex[:10]}"
    try:
        chunks = await pinecone_mod.upsert_document(
            namespace=namespace, entry_id=str(row.id), text=text, api_key=api_key,
            title=row.title, old_chunk_count=row.chunk_count or 0,
        )
    except Exception as e:
        row.vector_status, row.vector_error = "failed", str(e)[:500]
        db.commit()
        logger.error(f"[AGENT-KB] векторизация документа не удалась: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="vectorize_failed")

    row.chunk_count = chunks
    row.char_count = len(text)
    row.vector_status = "ready" if chunks else "skipped"
    row.vector_error = None
    agent.kb_namespace = namespace
    agent.kb_updated_at = datetime.utcnow()
    _voice_sync_kb_functions(db, agent)
    db.commit()
    db.refresh(row)
    logger.info(f"[AGENT-KB] документ {file_row.filename} → {chunks} кусков (агент {agent.id})")
    return {"success": True, "entry": row.to_dict()}


@router.delete("/knowledge/document/{entry_id}")
async def remove_knowledge_document(
    entry_id: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Убрать документ из базы знаний. Сам файл остаётся в библиотеке агента."""
    from backend.services import pinecone_service as pinecone_mod

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    row = _kb_entry_or_404(db, agent, entry_id)
    if agent.kb_namespace and row.chunk_count:
        await pinecone_mod.delete_document(agent.kb_namespace, str(row.id), row.chunk_count)
    db.delete(row)
    db.flush()
    _voice_sync_kb_functions(db, agent)
    db.commit()
    return {"success": True}


@router.get("/knowledge/gaps")
async def list_knowledge_gaps(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Вопросы клиентов, на которые в базе не нашлось ответа."""
    from backend.models.agent_knowledge import AgentKnowledgeGap

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    rows = (
        db.query(AgentKnowledgeGap)
        .filter(AgentKnowledgeGap.agent_config_id == agent.id,
                AgentKnowledgeGap.resolved.is_(False))
        .order_by(AgentKnowledgeGap.ask_count.desc(),
                  AgentKnowledgeGap.last_asked_at.desc())
        .limit(50).all()
    )
    return {"gaps": [r.to_dict() for r in rows]}


@router.delete("/knowledge/gaps/{gap_id}")
async def resolve_knowledge_gap(
    gap_id: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Скрыть пробел: владелец добавил ответ или счёл вопрос неважным."""
    from backend.models.agent_knowledge import AgentKnowledgeGap

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    row = db.query(AgentKnowledgeGap).filter(
        AgentKnowledgeGap.id == gap_id,
        AgentKnowledgeGap.agent_config_id == agent.id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="gap_not_found")
    row.resolved = True
    db.commit()
    return {"success": True}


# ============================================================================
# ENDPOINTS — CHAT (ChatOrchestrator with tools)
# ============================================================================


@router.post("/chat")
async def agent_chat(
    body: AgentChatRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Text chat with the agent. v3 → OpenRouter, v2 (legacy) → OpenAI Responses API."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    # Legacy agents still need the user's OpenAI key
    if not agent.uses_hardcoded_prompt and not current_user.openai_api_key:
        raise HTTPException(status_code=400, detail="openai_key_required")

    from backend.services.agent_orchestrator import ChatOrchestrator

    try:
        orchestrator = ChatOrchestrator()
        result = await orchestrator.run(
            message=body.message,
            agent_config=agent,
            user=current_user,
            db=db,
        )
    except SubscriptionExpiredError:
        raise HTTPException(status_code=402, detail="subscription_expired")
    except SubscriptionRequiredError:
        raise HTTPException(status_code=402, detail="subscription_required")
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail={
            "error": "insufficient_credits",
            "required": e.required,
            "available": e.available,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AGENT] Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"chat_error: {str(e)}")

    return {
        "reply": result["reply"],
        "timestamp": datetime.utcnow().isoformat(),
        "debug_log": result.get("debug_log", []),
    }


@router.post("/chat/stream")
async def agent_chat_stream(
    body: AgentChatRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Streaming text chat (NDJSON) with live tool progress + token-by-token reply.
    Only v3 (hardcoded-prompt) agents stream. Legacy v2 → 409, the front falls
    back to the non-streaming /chat endpoint.
    """
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    # Legacy v2 agents don't stream — tell the front to use /chat fallback.
    if not getattr(agent, "uses_hardcoded_prompt", False):
        if not current_user.openai_api_key:
            raise HTTPException(status_code=400, detail="openai_key_required")
        raise HTTPException(status_code=409, detail="streaming_not_supported")

    # Subscription/credit precheck BEFORE the stream starts, so 402 returns as
    # an HTTP status (after 200 starts we could only emit an in-stream error).
    try:
        CreditService.precheck(db, current_user)
    except SubscriptionExpiredError:
        raise HTTPException(status_code=402, detail="subscription_expired")
    except SubscriptionRequiredError:
        raise HTTPException(status_code=402, detail="subscription_required")
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail={
            "error": "insufficient_credits",
            "required": e.required,
            "available": e.available,
        })

    from backend.services.agent_orchestrator import ChatOrchestrator
    orchestrator = ChatOrchestrator()

    async def event_gen():
        try:
            async for ev in orchestrator.run_stream(body.message, agent, current_user, db):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except (SubscriptionExpiredError, SubscriptionRequiredError, InsufficientCreditsError):
            # precheck already ran above; this path means a late check — surface in-stream.
            yield json.dumps({"type": "error", "detail": "payment_required", "code": 402}, ensure_ascii=False) + "\n"
        except Exception as e:
            logger.error(f"[AGENT] stream error: {e}", exc_info=True)
            yield json.dumps({"type": "error", "detail": f"chat_error: {e}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_gen(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ═══════════════════════════════════════════════════════════════════════════
# ФАЙЛЫ АГЕНТА (agent_files): библиотека владельца, вложения клиентов,
# документы, созданные агентом. Логика — services/agent_media_service.py.
# ═══════════════════════════════════════════════════════════════════════════

AGENT_LIBRARY_MAX_FILES = 100


class AgentFileUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)


def _agent_file_or_404(db: Session, agent: AgentConfig, file_id: str):
    from backend.models.agent_file import AgentFile
    try:
        fid = uuid.UUID(str(file_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="file_not_found")
    row = db.query(AgentFile).filter(
        AgentFile.id == fid, AgentFile.agent_config_id == agent.id
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="file_not_found")
    return row


# Сколько живёт подписанная ссылка на превью. Час — страница успевает
# показать картинки и не держит ссылки дольше нужного.
FILE_PREVIEW_TTL = 3600


def _files_with_roles(db: Session, agent: AgentConfig, rows: list) -> list:
    """
    Файлы агента вместе с их ролями — для объединённой страницы знаний
    (ветка 1610-kb-calls).

    До этой ветки база знаний и библиотека файлов были двумя отдельными
    окнами, хотя один и тот же файл нужен в двух ролях: его и отправляют
    клиенту, и используют как источник ответов. Страница показывает эти роли
    переключателями, поэтому к каждому файлу добавляются:

      preview_url    — подписанная ссылка на картинку/видео, чтобы в каталоге
                       и в библиотеке были видны сами фото, а не имена файлов;
      in_knowledge   — агент ищет ответы внутри этого файла (есть запись
                       базы знаний вида document), и id этой записи;
      used_in_catalog— фото прикреплено к позиции каталога.
    """
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    from backend.services import agent_media_service

    if not rows:
        return []

    # Какие файлы уже являются источниками ответов
    docs = {}
    try:
        for entry_id, file_id, status in db.query(
                AgentKnowledgeEntry.id, AgentKnowledgeEntry.file_id,
                AgentKnowledgeEntry.vector_status).filter(
                AgentKnowledgeEntry.agent_config_id == agent.id,
                AgentKnowledgeEntry.kind == "document",
                AgentKnowledgeEntry.file_id.isnot(None)).all():
            docs[str(file_id)] = {"entry_id": str(entry_id), "vector_status": status}
    except Exception as e:
        logger.warning(f"[AGENT-FILES] роли источников не прочитаны: {e}")

    # Какие картинки заняты позициями каталога
    in_catalog = set()
    try:
        for (media,) in db.query(AgentKnowledgeEntry.media_file_ids).filter(
                AgentKnowledgeEntry.agent_config_id == agent.id,
                AgentKnowledgeEntry.kind == "product",
                AgentKnowledgeEntry.media_file_ids.isnot(None)).all():
            for fid in (media or []):
                in_catalog.add(str(fid))
    except Exception as e:
        logger.warning(f"[AGENT-FILES] связи с каталогом не прочитаны: {e}")

    out = []
    for row in rows:
        item = row.to_dict()
        fid = str(row.id)
        doc = docs.get(fid)
        item["in_knowledge"] = doc is not None
        item["knowledge_entry_id"] = doc["entry_id"] if doc else None
        item["knowledge_status"] = doc["vector_status"] if doc else None
        # Источником ответов может стать только файл, из которого извлёкся текст.
        item["can_be_knowledge"] = bool(row.extracted_text)
        item["used_in_catalog"] = fid in in_catalog
        if (row.mime_type or "").startswith(("image/", "video/")):
            try:
                item["preview_url"] = agent_media_service.file_link(
                    row, expires_seconds=FILE_PREVIEW_TTL)
            except Exception:
                item["preview_url"] = None
        out.append(item)
    return out


@router.get("/files")
async def list_agent_files(
    agent_id: Optional[str] = Query(None),
    source: str = Query("library"),
    contact_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Файлы агента: source = library | generated | inbound | all."""
    from backend.models.agent_file import AgentFile
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    q = db.query(AgentFile).filter(AgentFile.agent_config_id == agent.id)
    if source in ("library", "generated", "inbound"):
        q = q.filter(AgentFile.source == source)
    if contact_id:
        q = q.filter(AgentFile.agent_contact_id == contact_id)
    rows = q.order_by(AgentFile.created_at.desc()).limit(limit).all()
    from backend.services.r2_storage import R2StorageService
    return {
        "files": _files_with_roles(db, agent, rows),
        "storage_configured": R2StorageService.objects_available(),
        "max_mb": settings.AGENT_MEDIA_MAX_MB,
    }


@router.post("/files")
async def upload_agent_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Загрузить файл в библиотеку агента. Текст извлекается сразу (документы —
    бесплатно, картинки и PDF-сканы — OCR за кредиты), чтобы агент знал, что
    внутри, и мог отвечать по содержимому.
    """
    from backend.models.agent_file import AgentFile
    from backend.services import agent_media_service
    from backend.services.r2_storage import R2StorageService

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    if not R2StorageService.objects_available():
        raise HTTPException(status_code=503, detail="storage_not_configured")
    count = db.query(AgentFile).filter(
        AgentFile.agent_config_id == agent.id, AgentFile.source == "library"
    ).count()
    if count >= AGENT_LIBRARY_MAX_FILES:
        raise HTTPException(status_code=400, detail="library_limit_reached")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty_file")
    if len(data) > settings.AGENT_MEDIA_MAX_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file_too_large")

    row = await agent_media_service.store_file(
        db,
        user_id=current_user.id,
        agent_config_id=agent.id,
        data=data,
        filename=file.filename or "file",
        mime=file.content_type,
        source="library",
        title=(title or "").strip() or None,
        description=(description or "").strip() or None,
    )
    if not row.storage_key:
        db.delete(row)
        db.commit()
        raise HTTPException(status_code=502, detail="storage_upload_failed")
    await agent_media_service.analyze_file(db, row, data)
    if row.status == "skipped" and row.error == "unsupported_format":
        # Файл без текста (архив, аудио и т.п.) — отправлять его можно и так.
        row.status, row.error = "ready", None
        db.commit()
    db.refresh(row)
    logger.info(f"[AGENT-FILES] library upload {row.filename} ({row.size_bytes} B) → {row.status}, agent {agent.id}")
    return {"success": True, "file": row.to_dict()}


@router.get("/files/{file_id}")
async def get_agent_file(
    file_id: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Файл с извлечённым текстом (расшифровка голосового, OCR, текст документа)."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    return _agent_file_or_404(db, agent, file_id).to_dict(with_text=True)


@router.get("/files/{file_id}/link")
async def get_agent_file_link(
    file_id: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Временная ссылка на скачивание (1 час)."""
    from backend.services import agent_media_service
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    url = agent_media_service.file_link(_agent_file_or_404(db, agent, file_id), expires_seconds=3600)
    if not url:
        raise HTTPException(status_code=404, detail="file_not_stored")
    return {"url": url}


@router.patch("/files/{file_id}")
async def update_agent_file(
    file_id: str,
    body: AgentFileUpdateRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Переименовать файл библиотеки / поменять описание «когда отправлять»."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    row = _agent_file_or_404(db, agent, file_id)
    if body.title is not None:
        row.title = body.title.strip() or None
    if body.description is not None:
        row.description = body.description.strip() or None
    db.commit()
    db.refresh(row)
    return {"success": True, "file": row.to_dict()}


@router.delete("/files/{file_id}")
async def delete_agent_file(
    file_id: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Удалить файл (из R2 и из базы)."""
    from backend.services import agent_media_service
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    await agent_media_service.delete_file(db, _agent_file_or_404(db, agent, file_id))
    return {"success": True}


@router.post("/transcribe")
async def agent_transcribe(
    file: UploadFile = File(...),
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Распознавание речи (STT) для веб-чата. Принимает аудио-файл (webm/mp4/ogg/…),
    возвращает {"text": ...}. Отдельно за STT кредиты не списываются — тарифицируется
    последующий ответ агента в /chat[/stream].
    """
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="empty_audio")
    if len(audio) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="audio_too_large")

    from backend.services.transcription_service import TranscriptionService
    text = await TranscriptionService.transcribe(
        audio,
        filename=file.filename or "audio.webm",
        content_type=file.content_type or "audio/webm",
    )
    if text is None:
        raise HTTPException(status_code=502, detail="transcription_failed")
    return {"text": text}


@router.post("/chat/clear")
async def agent_chat_clear(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Очистить историю веб-чата агента — каждый новый диалог начинается с нуля.

    Затрагивает только UI-чат (agent_config.chat_history). Telegram-история
    хранится отдельно (telegram_history) и не трогается.
    """
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    agent.chat_history = []
    db.commit()
    return {"ok": True}


# ============================================================================
# ENDPOINTS — PUBLIC HTTP CHANNEL (приём заявок «сервер-к-серверу»)
# ============================================================================
# Внешний бэкенд (форма сайта, CRM, и т.п.) шлёт запрос с секретным ключом
# агента — запрос попадает в ChatOrchestrator (stateless), агент сам решает,
# что делать: создать контакт, поставить звонок, ответить на вопрос.
#
# Управление ключом — авторизованные эндпоинты /public-access* (для владельца).
# Сам приём — публичный POST /public/{agent_id}/message (без JWT, по ключу).
# ============================================================================


def _public_endpoint_url(agent_id) -> str:
    base = (settings.HOST_URL or settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/api/agent/public/{agent_id}/message"


def _public_access_dict(agent: AgentConfig) -> dict:
    """Статус публичного канала для настроек владельца (ключ показываем полностью)."""
    return {
        "enabled": bool(agent.public_enabled),
        "has_key": bool(agent.public_api_key),
        "api_key": agent.public_api_key,
        "endpoint_url": _public_endpoint_url(agent.id),
        "agent_id": str(agent.id),
    }


def _extract_api_key(request: Request) -> Optional[str]:
    """Достаём ключ из X-Api-Key, Authorization: Bearer <key> или ?key=."""
    key = request.headers.get("X-Api-Key") or request.headers.get("x-api-key")
    if key:
        return key.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("key")


def _coerce_public_message(payload) -> str:
    """
    Свернуть произвольное тело запроса в текстовое сообщение для оркестратора.
    - строка → как есть;
    - dict с полем message/text/... → берём его (+ остальные поля контекстом);
    - dict без текстового поля → перечисляем «ключ: значение»;
    - иное → JSON.
    """
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("message", "text", "msg", "query", "content", "comment"):
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                extras = {
                    k: val for k, val in payload.items()
                    if k != key and val not in (None, "", [], {})
                }
                if extras:
                    lines = [v.strip(), "", "Дополнительные данные:"]
                    lines += [f"- {k}: {val}" for k, val in extras.items()]
                    return "\n".join(lines)
                return v.strip()
        lines = [f"- {k}: {val}" for k, val in payload.items() if val not in (None, "", [], {})]
        return ("Новая заявка:\n" + "\n".join(lines)) if lines else ""
    return json.dumps(payload, ensure_ascii=False)


@router.get("/public-access")
async def get_public_access(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Статус публичного канала (URL + ключ) для настроек агента."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return _public_access_dict(agent)


@router.post("/public-access/regenerate")
async def regenerate_public_key(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Сгенерировать (или перевыпустить) секретный ключ. Старый ключ перестаёт работать."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    agent.public_api_key = secrets.token_urlsafe(32)
    if not agent.public_enabled:
        agent.public_enabled = True
    db.commit()
    db.refresh(agent)
    logger.info(f"[AGENT-PUBLIC] Regenerated key for agent {agent.id}")
    return _public_access_dict(agent)


@router.put("/public-access")
async def toggle_public_access(
    body: PublicAccessToggleRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Включить/выключить публичный канал. При первом включении ключ создаётся автоматически."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    agent.public_enabled = bool(body.enabled)
    if agent.public_enabled and not agent.public_api_key:
        agent.public_api_key = secrets.token_urlsafe(32)
    db.commit()
    db.refresh(agent)
    return _public_access_dict(agent)


# ============================================================================
# ENDPOINTS — CONNECTORS (Composio: Google Calendar, Gmail)
# ============================================================================

def _connectors_base_url() -> str:
    """Публичный базовый URL для callback'а OAuth (без хвостового слэша)."""
    base = settings.PUBLIC_BASE_URL or settings.HOST_URL or ""
    return base.rstrip("/")


def _toolkit_label(toolkit: str) -> str:
    return {
        "google_calendar": "Google Календарь",
        "gmail": "Gmail",
        "instagram": "Instagram",
    }.get(toolkit, toolkit)


def _connectors_status(db: Session, agent: AgentConfig) -> dict:
    """Сводка по коннекторам агента для UI."""
    rows = {
        r.toolkit: r
        for r in db.query(AgentConnector).filter(
            AgentConnector.agent_config_id == agent.id
        ).all()
    }
    items = []
    for toolkit in CONNECTOR_TOOLKITS:
        row = rows.get(toolkit)
        items.append({
            "toolkit": toolkit,
            "label": _toolkit_label(toolkit),
            "available": composio_service.toolkit_available(toolkit),
            "status": row.status if row else "disconnected",
            "connected": bool(row and row.is_connected()),
            "connected_email": row.connected_email if row else None,
        })
        if toolkit == "instagram":
            items[-1]["owner_pause_minutes"] = agent_pause.owner_pause_minutes(row)
            items[-1]["reply_delay_seconds"] = agent_pause.reply_delay_seconds(row)
    return {"configured": composio_service.is_configured(), "connectors": items}


async def _reconcile_connectors(db: Session, agent: AgentConfig) -> None:
    """
    Сверить «подключённые» строки агента с реальным состоянием в Composio, чтобы
    UI не показывал зелёным то, что по факту не работает (главная причина бага
    «в UI подключено, агент говорит не подключён»).

    Для каждой строки (по состоянию в Composio через LIST):
      • есть Active-аккаунт → строка должна быть connected: чиним статус
        (pending/error→connected, частый кейс «OAuth прошёл, но callback не
        подтвердил») и/или устаревший connected_account_id, возвращаем голос;
      • Active-аккаунта нет, а строка была connected → error + снять голос +
        снести висячие аккаунты в Composio (чтобы reconnect был с нуля);
      • Composio недоступен (ok=False) → не трогаем ничего.
    Best-effort, под кешем верификации — сеть дёргается не на каждый рендер.
    """
    if not composio_service.is_configured():
        return
    # Берём ВСЕ строки:
    #   • connected — могли «протухнуть» (downgrade → error);
    #   • error/pending — в Composio мог появиться Active-аккаунт (часто: OAuth
    #     завершился, но старый callback не подтвердил строку) → авто-восстановление.
    # pending НЕ понижаем (там может идти живой OAuth), только восстанавливаем вверх.
    rows = db.query(AgentConnector).filter(
        AgentConnector.agent_config_id == agent.id,
        AgentConnector.status.in_(("connected", "error", "pending")),
    ).all()
    if not rows:
        return

    changed = False
    for row in rows:
        composio_user_id = row.composio_user_id or composio_service.composio_user_id_for_agent(agent.id)
        try:
            state = await composio_service.connection_state(composio_user_id, row.toolkit)
            if not state.get("ok"):
                # Composio недоступен — НЕ трогаем статус (иначе временный сбой
                # отключил бы рабочий коннектор). Попробуем в следующий раз.
                continue

            active_id = state.get("active_id")
            if active_id:
                # Активный аккаунт есть → должно быть connected. Чиним статус и/или
                # устаревший указатель, при восстановлении возвращаем голосовые функции.
                prev_status = row.status
                if prev_status != "connected" or row.connected_account_id != active_id:
                    row.status = "connected"
                    row.connected_account_id = active_id
                    if state.get("email"):
                        row.connected_email = state["email"]
                    changed = True
                    if prev_status != "connected":
                        try:
                            va = _resolve_voice_assistant(db, agent)
                            _voice_set_connector_function(va, row.toolkit, True)
                            logger.info(f"[AGENT-CONNECTORS] reconcile: {row.toolkit} agent={agent.id} {prev_status}→connected (recovered)")
                        except Exception as e:
                            logger.warning(f"[AGENT-CONNECTORS] reconcile voice restore failed: {e}")
                continue

            # Список получен, активного аккаунта нет. Понижаем/чистим ТОЛЬКО строки,
            # что числились connected. error/pending не трогаем (для error уже всё
            # сделано, у pending может идти живой OAuth) — и не дёргаем сеть зря.
            if row.status == "connected":
                row.status = "error"
                changed = True
                try:
                    va = _resolve_voice_assistant(db, agent)
                    _voice_set_connector_function(va, row.toolkit, False)
                except Exception as e:
                    logger.warning(f"[AGENT-CONNECTORS] reconcile voice cleanup failed: {e}")
                try:
                    await composio_service.delete_all_connections(composio_user_id, row.toolkit)
                except Exception as e:
                    logger.warning(f"[AGENT-CONNECTORS] reconcile remote cleanup failed: {e}")
                logger.info(f"[AGENT-CONNECTORS] reconcile: {row.toolkit} agent={agent.id} → error (no active account)")
        except Exception as e:
            logger.warning(f"[AGENT-CONNECTORS] reconcile failed toolkit={row.toolkit}: {e}")

    if changed:
        db.commit()


# ============================================================================
# ENDPOINTS — ГОЛОСОВЫЕ СООБЩЕНИЯ (ElevenLabs, services/agent_voice_service.py)
# ============================================================================

VOICE_PREVIEW_TEXTS = {
    "ky": "[softly] Саламатсызбы… [short pause] Ооба, суроңузду көрдүм. Азыр баарын тактап, сизге жазып жиберем, макулбу?",
    "ru": "[softly] Здравствуйте… [short pause] Так, смотрите, я ваш вопрос посмотрел. Сейчас всё уточню и вам напишу, хорошо?",
    "kk": "[softly] Сәлеметсіз бе… [short pause] Сұрағыңызды көрдім. Қазір бәрін нақтылап, сізге жазамын, жарай ма?",
    "uz": "[softly] Assalomu alaykum… [short pause] Savolingizni ko'rdim. Hozir hammasini aniqlab, sizga yozaman, xo'pmi?",
    "en": "[softly] Hi there… [short pause] So, I've looked at your question. Let me check everything and I'll text you back, okay?",
    "tr": "[softly] Merhaba… [short pause] Sorunuza baktım. Şimdi her şeyi netleştirip size yazacağım, olur mu?",
}


def _voice_messages_state(db: Session, agent: AgentConfig) -> dict:
    from backend.services import agent_voice_service as avs
    from backend.services import telegram_user_service, whatsapp_service
    from backend.models.eleven_assistant import ElevenAssistantConfig
    card = None
    if agent.eleven_assistant_id:
        card = db.query(ElevenAssistantConfig).filter(ElevenAssistantConfig.id == agent.eleven_assistant_id).first()
    channels = {}
    tg = telegram_user_service.get_account_for_agent(db, agent.id, require_connected=False)
    if tg is not None and tg.status == "connected":
        channels["telegram"] = avs.voice_reply_mode(tg)
    wa = whatsapp_service.get_account_for_agent(db, agent.id, require_connected=False)
    if wa is not None and wa.status == "connected":
        channels["whatsapp"] = avs.voice_reply_mode(wa)
    voice = avs.resolve_voice(db, agent)
    engine = agent.voice_msg_engine if agent.voice_msg_engine in avs.VOICE_ENGINES else avs.ENGINE_ELEVEN
    from backend.websockets.live_client import LIVE_VOICES
    return {
        "configured": avs.any_engine_configured(),
        "engines": {e: avs.is_configured(e) for e in avs.VOICE_ENGINES},
        "engine": engine,
        "openai_voice": agent.voice_msg_openai_voice or avs.DEFAULT_OPENAI_VOICE,
        "openai_voices": LIVE_VOICES,
        "auto_share": avs.auto_share(agent),
        "auto_share_choices": list(avs.AUTO_SHARE_CHOICES),
        "default_stability": avs.DEFAULT_MESSAGE_STABILITY,
        "voice_id": agent.voice_msg_voice_id,
        "voice_name": agent.voice_msg_voice_name,
        "language": agent.voice_msg_language,
        "stability": agent.voice_msg_stability,
        "call_voice": {
            "voice_id": card.voice_id if card else None,
            "language": card.language if card else None,
            "stability": card.stability if card else None,
        },
        "effective": voice,
        "channels": channels,
        "credits_per_400_chars": avs.credits_for("x" * 400, engine),
        "max_chars": settings.AGENT_VOICE_MAX_CHARS,
    }


@router.get("/voice-messages")
async def get_voice_messages_settings(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return _voice_messages_state(db, agent)


@router.put("/voice-messages")
async def update_voice_messages_settings(
    body: VoiceMessagesSettingsRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Движок, голос, язык, подача и доля голосовых. Меняются только переданные поля."""
    from backend.models.eleven_assistant import ELEVEN_LANGUAGES
    from backend.services import agent_voice_service as avs
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    if body.language and body.language not in {l["code"] for l in ELEVEN_LANGUAGES}:
        raise HTTPException(status_code=400, detail="invalid_language")
    if body.stability is not None and body.stability not in (0.0, 0.5, 1.0):
        raise HTTPException(status_code=400, detail="invalid_stability")
    if body.engine is not None and body.engine not in avs.VOICE_ENGINES:
        raise HTTPException(status_code=400, detail="invalid_engine")
    if body.openai_voice is not None and body.openai_voice.lower() not in LIVE_VOICE_SET:
        raise HTTPException(status_code=400, detail="invalid_voice")
    if body.auto_share is not None and body.auto_share not in avs.AUTO_SHARE_CHOICES:
        raise HTTPException(status_code=400, detail="invalid_auto_share")
    fields = body.model_fields_set
    if "engine" in fields and body.engine:
        agent.voice_msg_engine = body.engine
    if "openai_voice" in fields and body.openai_voice:
        agent.voice_msg_openai_voice = body.openai_voice.lower()
    if "auto_share" in fields and body.auto_share:
        agent.voice_msg_auto_share = body.auto_share
    if "voice_id" in fields:
        agent.voice_msg_voice_id = (body.voice_id or "").strip() or None
        agent.voice_msg_voice_name = ((body.voice_name or "").strip() or None) if agent.voice_msg_voice_id else None
    if "language" in fields:
        agent.voice_msg_language = body.language or None
    if "stability" in fields:
        agent.voice_msg_stability = body.stability
    db.commit()
    return _voice_messages_state(db, agent)


@router.post("/voice-messages/preview")
async def preview_voice_message(
    body: VoicePreviewRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Озвучить пример текущим (или выбранным в форме) голосом — MP3. Списывает кредиты."""
    from backend.services import agent_voice_service as avs
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    engine = body.engine or agent.voice_msg_engine or avs.ENGINE_ELEVEN
    if engine not in avs.VOICE_ENGINES:
        raise HTTPException(status_code=400, detail="invalid_engine")
    if not avs.is_configured(engine):
        raise HTTPException(status_code=503, detail="tts_not_configured")
    if engine == avs.ENGINE_OPENAI:
        name = (body.openai_voice or agent.voice_msg_openai_voice or avs.DEFAULT_OPENAI_VOICE).lower()
        if name not in LIVE_VOICE_SET:
            raise HTTPException(status_code=400, detail="invalid_voice")
        lang = body.language or avs._agent_language(db, agent) or "ru"
        voice = {"engine": engine, "voice_id": name, "language": lang,
                 "model": settings.AGENT_VOICE_OPENAI_TTS_MODEL}
    else:
        current = avs.resolve_voice(db, agent) if (agent.voice_msg_engine or avs.ENGINE_ELEVEN) == avs.ENGINE_ELEVEN else None
        voice = dict(current or {}, engine=avs.ENGINE_ELEVEN)
        if body.voice_id:
            voice["voice_id"] = body.voice_id
        if not voice.get("voice_id"):
            # Голос «как в звонках», а движок агента сейчас другой — берём карточку.
            from backend.models.eleven_assistant import ElevenAssistantConfig
            card = db.query(ElevenAssistantConfig).filter(
                ElevenAssistantConfig.id == agent.eleven_assistant_id).first() if agent.eleven_assistant_id else None
            if card and card.voice_id:
                voice["voice_id"] = card.voice_id
                voice.setdefault("language", card.language)
        if not voice.get("voice_id"):
            raise HTTPException(status_code=400, detail="voice_not_selected")
        voice["language"] = body.language or voice.get("language") or "ky"
        voice["stability"] = float(body.stability) if body.stability is not None else (
            voice.get("stability") if voice.get("stability") is not None else avs.DEFAULT_MESSAGE_STABILITY)
        voice["model"] = settings.AGENT_VOICE_TTS_MODEL
        lang = voice["language"]
    text = (body.text or "").strip() or VOICE_PREVIEW_TEXTS.get(lang, VOICE_PREVIEW_TEXTS["ru"])
    res = await avs.synthesize_preview(text, voice)
    if not res.get("ok"):
        # Причина от провайдера — в тост пользователю (иначе «ошибка» без объяснения).
        raise HTTPException(status_code=502, detail={"code": res.get("error") or "tts_error",
                                                     "provider_message": (res.get("detail") or "")[:300]})
    avs.charge(db, current_user.id, text, ref_id=agent.id, engine=engine, seconds=res.get("seconds"),
               notes="Прослушивание голоса для голосовых сообщений")
    return Response(content=res["data"], media_type=res.get("mime") or "audio/mpeg")


@router.patch("/connectors/instagram/settings")
async def update_instagram_settings(
    body: MessengerSettingsRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Настройки переписки Instagram: пауза после сообщения владельца, задержка ответа."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    row = db.query(AgentConnector).filter(
        AgentConnector.agent_config_id == agent.id,
        AgentConnector.toolkit == "instagram",
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="not_connected")
    agent_pause.apply_messenger_settings(row, body)
    db.commit()
    return _connectors_status(db, agent)


@router.get("/connectors")
async def list_connectors(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Статус коннекторов агента (для раздела настроек)."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    # Сверяем с Composio, чтобы статус в UI был честным (и self-heal/чистка висяков).
    await _reconcile_connectors(db, agent)
    return _connectors_status(db, agent)


@router.post("/connectors/{toolkit}/connect")
async def connect_connector(
    toolkit: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Старт OAuth-подключения toolkit'а. Возвращает {redirect_url} — фронт делает
    редирект пользователя в Composio/Google. По возврату сработает /connectors/callback.
    """
    if toolkit not in CONNECTOR_TOOLKITS:
        raise HTTPException(status_code=400, detail="unknown_toolkit")
    if not composio_service.is_configured():
        raise HTTPException(status_code=400, detail="composio_not_configured")
    if not composio_service.toolkit_available(toolkit):
        raise HTTPException(status_code=400, detail="toolkit_auth_config_missing")

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")

    base = _connectors_base_url()
    if not base:
        raise HTTPException(status_code=500, detail="public_base_url_not_set")

    # Identity Composio — по агенту (вариант A): каждый агент имеет своё
    # изолированное подключение, не общее с другими агентами владельца.
    composio_user_id = composio_service.composio_user_id_for_agent(agent.id)
    state_token = secrets.token_urlsafe(24)
    callback_url = f"{base}/api/agent/connectors/callback?state={state_token}"

    # Upsert ряда коннектора (один на agent+toolkit).
    row = db.query(AgentConnector).filter(
        AgentConnector.agent_config_id == agent.id,
        AgentConnector.toolkit == toolkit,
    ).first()
    prev_status = row.status if row else None
    if not row:
        row = AgentConnector(
            agent_config_id=agent.id,
            user_id=current_user.id,
            toolkit=toolkit,
        )
        db.add(row)
    row.status = "pending"
    row.composio_user_id = composio_user_id
    row.state_token = state_token
    db.flush()

    # Принудительный чистый reconnect: если предыдущее состояние было 'error'
    # (reconcile/ callback признали аккаунт нерабочим), сносим ВСЕ аккаунты этого
    # агента+toolkit в Composio и НЕ переиспользуем — гоним свежий OAuth. Иначе
    # reuse мог бы снова подхватить «полу-мёртвый» аккаунт.
    force_fresh = prev_status == "error"
    if force_fresh:
        try:
            await composio_service.delete_all_connections(composio_user_id, toolkit)
        except Exception as e:
            logger.warning(f"[AGENT-CONNECTORS] force-fresh cleanup failed: {e}")

    # Идемпотентность: identity per-agent, поэтому ищем уже существующее активное
    # подключение ИМЕННО этого агента (например, повторный клик «Подключить» или
    # ранее завершённый OAuth). Берём ТОЛЬКО ACTIVE — протухший/полу-отозванный
    # аккаунт переиспользовать нельзя, иначе снова получим «не подключён».
    try:
        existing = None if force_fresh else await composio_service.find_active_connection(
            composio_user_id, toolkit, require_active=True
        )
    except Exception as e:
        logger.warning(f"[AGENT-CONNECTORS] find_active_connection failed: {e}")
        existing = None

    if existing and existing.get("connected_account_id"):
        row.status = "connected"
        row.connected_account_id = existing["connected_account_id"]
        row.connected_email = existing.get("email")
        row.state_token = None
        try:
            va = _resolve_voice_assistant(db, agent)
            _voice_set_connector_function(va, toolkit, True)
        except Exception as e:
            logger.error(f"[AGENT-CONNECTORS] voice inject (reuse) failed: {e}", exc_info=True)
        db.commit()
        logger.info(f"[AGENT-CONNECTORS] reused existing connection toolkit={toolkit} agent={agent.id}")
        return {"redirect_url": None, "connected": True, "reused": True}

    try:
        result = await composio_service.initiate_connection(
            composio_user_id=composio_user_id,
            toolkit=toolkit,
            callback_url=callback_url,
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[AGENT-CONNECTORS] initiate failed toolkit={toolkit}: {type(e).__name__}: {e}", exc_info=True)
        # Пробрасываем реальную причину в ответ — иначе видно только обёртку.
        raise HTTPException(status_code=502, detail=f"composio_initiate_failed: {type(e).__name__}: {str(e)[:300]}")

    redirect_url = result.get("redirect_url")
    if not redirect_url:
        db.rollback()
        raise HTTPException(status_code=502, detail="no_redirect_url")

    # connection_id (запрос подключения) временно держим в connected_account_id —
    # на callback'е заменим реальным connected_account_id.
    if result.get("connection_id"):
        row.connected_account_id = result["connection_id"]
    db.commit()

    logger.info(f"[AGENT-CONNECTORS] connect toolkit={toolkit} agent={agent.id} → redirect")
    return {"redirect_url": redirect_url}


def _connector_callback_html(toolkit: str, ok: bool) -> str:
    """Простая страница возврата: уведомляет фронт (postMessage) и закрывается."""
    status = "success" if ok else "error"
    label = _toolkit_label(toolkit or "")
    title = f"{label}: {'подключено' if ok else 'ошибка подключения'}"
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{title}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#e2e8f0;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
.card{{background:#1e293b;padding:32px 40px;border-radius:16px;max-width:360px}}
h1{{font-size:18px;margin:0 0 8px}}p{{color:#94a3b8;font-size:14px;margin:0}}</style></head>
<body><div class="card"><h1>{'✅' if ok else '⚠️'} {title}</h1>
<p>Можно вернуться в дашборд агента. Это окно закроется автоматически.</p></div>
<script>
try{{ if(window.opener){{ window.opener.postMessage({{type:'connector_result',toolkit:'{toolkit}',status:'{status}'}},'*'); }} }}catch(e){{}}
setTimeout(function(){{ try{{window.close();}}catch(e){{}}
 if(!window.closed){{ window.location.href='/agent.html?connector={toolkit}&status={status}'; }} }}, 1200);
</script></body></html>"""


@router.get("/connectors/callback")
async def connector_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Возврат пользователя после OAuth (открывается в браузере пользователя, без JWT).

    Composio редиректит сюда с нашим state и своими параметрами (status,
    connected_account_id). По state находим ряд, помечаем connected и включаем
    голосовые функции коннектора.
    """
    qp = request.query_params
    state = qp.get("state")
    status_param = (qp.get("status") or "").lower()
    connected_account_id = (
        qp.get("connectedAccountId")
        or qp.get("connected_account_id")
        or qp.get("connectionId")
    )

    if not state:
        return HTMLResponse(_connector_callback_html("", False), status_code=400)

    row = db.query(AgentConnector).filter(AgentConnector.state_token == state).first()
    if not row:
        logger.warning("[AGENT-CONNECTORS] callback: unknown state token")
        return HTMLResponse(_connector_callback_html("", False), status_code=404)

    # Явная ошибка от провайдера — сразу error, без обращения к Composio.
    explicit_error = status_param in ("error", "failed", "denied", "cancelled")
    row.state_token = None

    if not explicit_error:
        # НЕ доверяем отсутствию ошибки в редиректе: помечаем connected ТОЛЬКО
        # после подтверждения, что аккаунт реально ACTIVE в Composio. Резолвим
        # настоящий connected_account_id (из callback-параметра либо самый свежий
        # ACTIVE по user_id) и ждём активации (eventual consistency после OAuth).
        composio_user_id = row.composio_user_id or composio_service.composio_user_id_for_agent(row.agent_config_id)
        active = None
        try:
            active = await composio_service.wait_for_active(
                composio_user_id, row.toolkit, connected_account_id or row.connected_account_id
            )
        except Exception as e:
            logger.warning(f"[AGENT-CONNECTORS] wait_for_active failed: {e}")

        if active and active.get("connected_account_id"):
            row.status = "connected"
            row.connected_account_id = active["connected_account_id"]
            if active.get("email"):
                row.connected_email = active["email"]
            # Включаем голосовые функции коннектора у голосового ассистента агента.
            try:
                agent = db.query(AgentConfig).filter(AgentConfig.id == row.agent_config_id).first()
                if agent:
                    va = _resolve_voice_assistant(db, agent)
                    _voice_set_connector_function(va, row.toolkit, True)
            except Exception as e:
                logger.error(f"[AGENT-CONNECTORS] voice inject failed: {e}", exc_info=True)

            db.commit()
            logger.info(f"[AGENT-CONNECTORS] callback OK toolkit={row.toolkit} agent={row.agent_config_id} acc={row.connected_account_id}")
            return HTMLResponse(_connector_callback_html(row.toolkit, True))

        logger.warning(f"[AGENT-CONNECTORS] callback: no ACTIVE account toolkit={row.toolkit} agent={row.agent_config_id} (status_param={status_param!r})")

    # Сюда попадаем при явной ошибке ИЛИ если активный аккаунт так и не появился.
    row.status = "error"
    db.commit()
    logger.warning(f"[AGENT-CONNECTORS] callback ERROR toolkit={row.toolkit} status={status_param}")
    return HTMLResponse(_connector_callback_html(row.toolkit, False))


@router.delete("/connectors/{toolkit}")
async def disconnect_connector(
    toolkit: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отключить коннектор: убрать голосовые функции и удалить локальный ряд."""
    if toolkit not in CONNECTOR_TOOLKITS:
        raise HTTPException(status_code=400, detail="unknown_toolkit")

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")

    row = db.query(AgentConnector).filter(
        AgentConnector.agent_config_id == agent.id,
        AgentConnector.toolkit == toolkit,
    ).first()
    if not row:
        return {"ok": True, "toolkit": toolkit, "status": "disconnected"}

    # Удаляем В COMPOSIO все connected accounts этого агента+toolkit (а не только
    # тот, что записан у нас): из-за прошлых багов/дублей их могло накопиться
    # несколько, и недоудалённый аккаунт ломает следующий reconnect. Best-effort:
    # не блокирует локальное отключение.
    composio_user_id = row.composio_user_id or composio_service.composio_user_id_for_agent(agent.id)
    try:
        removed = await composio_service.delete_all_connections(composio_user_id, toolkit)
        # Подстраховка: если list ничего не вернул, но у нас записан id — удалим его.
        if not removed and row.connected_account_id:
            await composio_service.delete_connection(row.connected_account_id)
    except Exception as e:
        logger.warning(f"[AGENT-CONNECTORS] remote delete failed: {e}")

    # Убираем голосовые функции коннектора.
    try:
        va = _resolve_voice_assistant(db, agent)
        _voice_set_connector_function(va, toolkit, False)
    except Exception as e:
        logger.error(f"[AGENT-CONNECTORS] voice cleanup failed: {e}", exc_info=True)

    db.delete(row)
    db.commit()
    logger.info(f"[AGENT-CONNECTORS] disconnected toolkit={toolkit} agent={agent.id}")
    return {"ok": True, "toolkit": toolkit, "status": "disconnected"}


@router.post("/public/{agent_id}/message")
async def agent_public_message(
    agent_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Публичный вход в чат-оркестратор агента (сервер-к-серверу, без JWT).

    Аутентификация — секретный ключ агента: заголовок `X-Api-Key`,
    `Authorization: Bearer <key>` или query-параметр `?key=`.

    Тело — произвольный JSON или text/plain. Оркестратор (stateless) сам решает,
    что сделать с входящими данными. Ответ: {reply, timestamp}.
    """
    # 1. Резолв агента по id (валидируем UUID)
    try:
        agent_uuid = uuid.UUID(str(agent_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="agent_not_found")

    agent = db.query(AgentConfig).filter(AgentConfig.id == agent_uuid).first()
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")

    # 2. Канал включён?
    if not agent.public_enabled or not agent.public_api_key:
        raise HTTPException(status_code=403, detail="public_access_disabled")

    # 3. Проверка ключа (constant-time)
    provided = _extract_api_key(request)
    if not provided or not secrets.compare_digest(provided, agent.public_api_key):
        raise HTTPException(status_code=401, detail="invalid_api_key")

    # 4. Владелец агента (он платит за обработку)
    owner = db.query(User).filter(User.id == agent.user_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="owner_not_found")

    # 5. Тело запроса → текст
    raw = await request.body()
    payload = None
    if raw:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            payload = raw.decode("utf-8", errors="ignore")
    message = _coerce_public_message(payload)
    if not message:
        raise HTTPException(status_code=400, detail="empty_message")

    # 6. Оркестратор (stateless)
    from backend.services.agent_orchestrator import ChatOrchestrator
    try:
        result = await ChatOrchestrator().run_public(
            message=message, agent_config=agent, user=owner, db=db,
        )
    except ValueError as e:
        # например, public_channel_requires_v3_agent
        raise HTTPException(status_code=400, detail=str(e))
    except SubscriptionExpiredError:
        raise HTTPException(status_code=402, detail="subscription_expired")
    except SubscriptionRequiredError:
        raise HTTPException(status_code=402, detail="subscription_required")
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail={
            "error": "insufficient_credits",
            "required": e.required,
            "available": e.available,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AGENT-PUBLIC] processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="processing_error")

    return {"reply": result["reply"], "timestamp": datetime.utcnow().isoformat()}


# ============================================================================
# ENDPOINTS — STATS
# ============================================================================


@router.get("/stats")
async def get_agent_stats(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get agent statistics from AgentContact + AgentCall (scoped to one agent)."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    total_contacts = db.query(func.count(AgentContact.id)).filter(
        AgentContact.agent_config_id == agent.id
    ).scalar() or 0

    active_contacts = db.query(func.count(AgentContact.id)).filter(
        AgentContact.agent_config_id == agent.id,
        AgentContact.status.notin_(["rejected", "do_not_call"]),
    ).scalar() or 0

    total_calls = db.query(func.count(AgentCall.id)).filter(
        AgentCall.agent_config_id == agent.id
    ).scalar() or 0

    success_calls = db.query(func.count(AgentCall.id)).filter(
        AgentCall.agent_config_id == agent.id,
        AgentCall.post_call_decision == "SUCCESS",
    ).scalar() or 0

    followup_calls = db.query(func.count(AgentCall.id)).filter(
        AgentCall.agent_config_id == agent.id,
        AgentCall.post_call_decision == "FOLLOWUP",
    ).scalar() or 0

    no_answer_calls = db.query(func.count(AgentCall.id)).filter(
        AgentCall.agent_config_id == agent.id,
        AgentCall.post_call_decision.in_(["NO_ANSWER", "REJECTED"]),
    ).scalar() or 0

    scheduled_tasks = db.query(func.count(Task.id)).join(
        AgentContact, Task.agent_contact_id == AgentContact.id
    ).filter(
        AgentContact.agent_config_id == agent.id,
        Task.is_agent_task == True,
        Task.status == TaskStatus.SCHEDULED,
    ).scalar() or 0

    return {
        "total_contacts": total_contacts,
        "active_contacts": active_contacts,
        "total_calls": total_calls,
        "success_calls": success_calls,
        "followup_calls": followup_calls,
        "no_answer_calls": no_answer_calls,
        "scheduled_tasks": scheduled_tasks,
    }


# ============================================================================
# ENDPOINTS — ORCHESTRATOR MODELS
# ============================================================================


@router.get("/orchestrator-models")
async def get_orchestrator_models(
    current_user: User = Depends(get_current_user_flexible),
):
    """Return the list of available orchestrator models for the wizard select. JWT или X-Api-Key."""
    return {"models": ORCHESTRATOR_MODELS, "default": get_default_model()}


# ============================================================================
# ENDPOINTS — TASKS (upcoming scheduled calls for the dashboard)
# ============================================================================


def _parse_scheduled_time(value: str) -> datetime:
    """ISO-8601 строка (UTC) → aware UTC datetime. 400 при невалидном вводе."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="invalid_scheduled_time")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _agent_task_dict(t: Task) -> dict:
    """Компактная сериализация задачи для UI (карточка контакта / календарь)."""
    return {
        "id": str(t.id),
        "title": t.title,
        "description": t.description,
        "channel": t.channel or "call",
        "scheduled_time": iso_utc(t.scheduled_time),
        "status": t.status.value if hasattr(t.status, "value") else t.status,
    }


@router.get("/tasks")
async def list_agent_tasks(
    status: Optional[str] = Query("scheduled"),
    limit: int = Query(10, ge=1, le=500),
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List the agent's upcoming tasks (with contact names) for the dashboard."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    q = db.query(Task).join(
        AgentContact, Task.agent_contact_id == AgentContact.id
    ).filter(
        Task.user_id == current_user.id,
        Task.is_agent_task == True,
        AgentContact.agent_config_id == agent.id,
    )
    if status:
        try:
            q = q.filter(Task.status == TaskStatus(status))
        except ValueError:
            q = q.filter(Task.status == status)

    tasks = q.order_by(Task.scheduled_time.asc()).limit(limit).all()

    # Resolve contact names in one pass
    contact_ids = [t.agent_contact_id for t in tasks if t.agent_contact_id]
    contacts_map = {}
    if contact_ids:
        rows = db.query(AgentContact).filter(AgentContact.id.in_(contact_ids)).all()
        contacts_map = {str(c.id): c for c in rows}

    result = []
    for t in tasks:
        c = contacts_map.get(str(t.agent_contact_id)) if t.agent_contact_id else None
        result.append({
            "id": str(t.id),
            "title": t.title,
            "description": t.description,
            "channel": t.channel or "call",
            "scheduled_time": iso_utc(t.scheduled_time),
            "status": t.status.value if hasattr(t.status, "value") else t.status,
            "contact_name": (c.name or c.phone) if c else None,
            "contact_phone": c.phone if c else None,
        })

    return {"total": len(result), "tasks": result}


@router.delete("/tasks")
async def delete_agent_tasks_bulk(
    date: Optional[str] = Query(None, description="YYYY-MM-DD (Бишкек) — удалить задачи только этого дня"),
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Массовое удаление запланированных задач агента из календаря.
    Без `date` — все scheduled-задачи агента, с `date` — только за этот день (Бишкек).
    Задачи в других статусах (calling, completed, ...) не затрагиваются.
    """
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    q = db.query(Task).join(
        AgentContact, Task.agent_contact_id == AgentContact.id
    ).filter(
        Task.user_id == current_user.id,
        Task.is_agent_task == True,
        AgentContact.agent_config_id == agent.id,
        Task.status == TaskStatus.SCHEDULED,
    )

    if date:
        try:
            day_start_local = datetime.strptime(date, "%Y-%m-%d")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="invalid_date")
        # Бишкек = UTC+6 без сезонных переходов
        day_start_utc = day_start_local.replace(tzinfo=timezone.utc) - timedelta(hours=6)
        q = q.filter(
            Task.scheduled_time >= day_start_utc,
            Task.scheduled_time < day_start_utc + timedelta(days=1),
        )

    tasks = q.all()
    for t in tasks:
        db.delete(t)
    db.commit()

    logger.info(
        f"[AGENT] Bulk deleted {len(tasks)} scheduled tasks for user {current_user.id}, "
        f"agent {agent.id}" + (f", day {date} Bishkek" if date else "")
    )
    return {"detail": "deleted", "deleted": len(tasks)}


@router.delete("/tasks/{task_id}")
async def delete_agent_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a single agent task (manual removal from the calendar)."""
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.user_id == current_user.id,
        Task.is_agent_task == True,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="not_found")

    db.delete(task)
    db.commit()

    logger.info(f"[AGENT] Deleted task {task_id} for user {current_user.id}")
    return {"detail": "deleted"}


@router.put("/tasks/{task_id}")
async def update_agent_task(
    task_id: str,
    body: AgentTaskUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ручное редактирование задачи (название и/или время) из UI."""
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.user_id == current_user.id,
        Task.is_agent_task == True,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="not_found")

    # Редактировать можно только ещё не выполненные задачи.
    if task.status != TaskStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="task_not_editable")

    updated_fields = []

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="title_required")
        task.title = title
        updated_fields.append("title")

    if body.scheduled_time is not None:
        task.scheduled_time = _parse_scheduled_time(body.scheduled_time)
        updated_fields.append("scheduled_time")

    if updated_fields:
        db.commit()
        db.refresh(task)
        logger.info(f"[AGENT] Updated task {task_id} fields: {updated_fields}")

    return _agent_task_dict(task)


# ============================================================================
# ENDPOINTS — PHONE NUMBERS
# ============================================================================


class AgentPhoneNumberRequest(BaseModel):
    """Номер собственной телефонии для агента (null — отвязать)."""
    number_id: Optional[str] = None


def _agent_phone_payload(db: Session, user: User, agent: Optional[AgentConfig]) -> dict:
    """Номера пользователя в собственной SIP-телефонии и какой из них у агента."""
    from backend.models.sip_gateway import SipPhoneNumber, normalize_sip_number

    numbers = (
        db.query(SipPhoneNumber)
        .filter(SipPhoneNumber.user_id == user.id, SipPhoneNumber.is_active == True)  # noqa: E712
        .order_by(SipPhoneNumber.created_at.asc())
        .all()
    )
    default_digits = normalize_sip_number(agent.default_caller_id) if agent and agent.default_caller_id else None
    agent_names = {
        a.id: a.name for a in db.query(AgentConfig).filter(AgentConfig.user_id == user.id).all()
    }
    items = []
    selected_id = None
    for n in numbers:
        bound_here = bool(agent and n.agent_config_id == agent.id)
        if bound_here or (default_digits and n.phone_number == default_digits and selected_id is None):
            selected_id = str(n.id)
        items.append({
            "id": str(n.id),
            "phone_number": n.phone_number,
            "label": n.label,
            "allow_outbound": bool(n.allow_outbound),
            "agent_config_id": str(n.agent_config_id) if n.agent_config_id else None,
            "agent_name": agent_names.get(n.agent_config_id),
            "bound_to_this_agent": bound_here,
        })
    return {
        "phone_numbers": items,
        "has_numbers": bool(items),
        "selected_number_id": selected_id,
        # Устаревшее поле старого фронтенда: номер для caller ID
        "default_caller_id": agent.default_caller_id if agent else None,
    }


@router.get("/phone-numbers")
async def get_phone_numbers(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Номера собственной телефонии (SIP-шлюз, sip_phone_numbers) пользователя.

    Агента можно создать без номера (работают Telegram, Instagram, чат); пока номеров
    нет, has_numbers=false — страница показывает «подключите номер в настройках агента»,
    а выбор номера разблокируется, когда номер появится в базе.
    """
    agent = _resolve_agent(db, current_user, agent_id)
    return _agent_phone_payload(db, current_user, agent)


@router.put("/phone-number")
async def set_agent_phone_number(
    body: AgentPhoneNumberRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Выбрать номер агента: входящие на него идут через голосового ассистента агента
    (с PostCall), исходящие агента звонят с него (default_caller_id).
    number_id=null — отвязать номера агента и сбросить номер для исходящих.
    """
    from backend.models.sip_gateway import SipPhoneNumber
    from backend.services.sip_gateway_service import SipGatewayService

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    # Прежние номера агента отвязываем: у агента один номер.
    SipGatewayService.unbind_agent_numbers(db, agent.id)

    if body.number_id:
        try:
            number_uuid = uuid.UUID(body.number_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_number_id")
        number = db.query(SipPhoneNumber).filter(
            SipPhoneNumber.id == number_uuid,
            SipPhoneNumber.user_id == current_user.id,
            SipPhoneNumber.is_active == True,  # noqa: E712
        ).first()
        if number is None:
            raise HTTPException(status_code=404, detail="number_not_found")
        try:
            SipGatewayService.bind_number_to_agent(number, agent)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        agent.default_caller_id = number.phone_number
    else:
        agent.default_caller_id = None

    db.commit()
    logger.info(f"[AGENT] Agent {agent.id} phone number set to {body.number_id or '—'}")
    return _agent_phone_payload(db, current_user, agent)


# ============================================================================
# ENDPOINTS — CONTACTS
# ============================================================================


@router.get("/contacts")
async def list_agent_contacts(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Поиск по name или phone (ILIKE)"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List agent contacts with optional status and search filter."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    q = db.query(AgentContact).filter(AgentContact.agent_config_id == agent.id)
    if status:
        q = q.filter(AgentContact.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        q = q.filter(
            or_(
                AgentContact.name.ilike(pattern),
                AgentContact.phone.ilike(pattern),
                AgentContact.alt_phones.ilike(pattern),  # второй номер клиента
                AgentContact.company.ilike(pattern),
            )
        )

    total = q.count()
    contacts = q.order_by(AgentContact.created_at.desc()).offset(offset).limit(limit).all()

    # Аннотация для карточек воронки: по каким каналам клиент уже писал
    # и сколько у него запланированных задач (всё одной выборкой на страницу,
    # не N+1). Канбан-карточка показывает маленькие иконки WhatsApp/Telegram/
    # Instagram и бейдж «есть задача».
    contact_ids = [c.id for c in contacts]
    wa_ids = tg_ids = ig_ids = set()
    pending_tasks_by_contact: dict = {}
    if contact_ids:
        wa_ids = {row[0] for row in db.query(AgentWhatsAppMessage.agent_contact_id)
                 .filter(AgentWhatsAppMessage.agent_contact_id.in_(contact_ids)).distinct().all()}
        tg_ids = {row[0] for row in db.query(AgentTelegramMessage.agent_contact_id)
                 .filter(AgentTelegramMessage.agent_contact_id.in_(contact_ids)).distinct().all()}
        ig_ids = {row[0] for row in db.query(AgentInstagramMessage.agent_contact_id)
                 .filter(AgentInstagramMessage.agent_contact_id.in_(contact_ids)).distinct().all()}
        rows = (
            db.query(Task.agent_contact_id, func.count(Task.id))
              .filter(Task.agent_contact_id.in_(contact_ids))
              .filter(Task.status == TaskStatus.SCHEDULED)
              .group_by(Task.agent_contact_id)
              .all()
        )
        pending_tasks_by_contact = {cid: int(n or 0) for cid, n in rows}

    out = []
    for c in contacts:
        d = c.to_dict()
        d["channels"] = {
            "whatsapp": c.id in wa_ids,
            "telegram": c.id in tg_ids,
            "instagram": c.id in ig_ids,
        }
        d["pending_tasks"] = pending_tasks_by_contact.get(c.id, 0)
        out.append(d)

    return {
        "total": total,
        "contacts": out,
    }


@router.get("/contacts/{contact_id}")
async def get_agent_contact_details(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Детали контакта + последние 20 звонков с транскриптами и размышлениями.
    Используется в модалке детального просмотра контакта.
    """
    contact = db.query(AgentContact).filter(
        AgentContact.id == contact_id,
        AgentContact.user_id == current_user.id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="not_found")

    calls = (
        db.query(AgentCall)
        .filter(AgentCall.agent_contact_id == contact_id)
        .order_by(AgentCall.created_at.desc())
        .limit(20)
        .all()
    )

    # Запланированные задачи этого контакта (для блока «Задачи» в карточке).
    tasks = (
        db.query(Task)
        .filter(
            Task.agent_contact_id == contact_id,
            Task.is_agent_task == True,
            Task.status == TaskStatus.SCHEDULED,
        )
        .order_by(Task.scheduled_time.asc())
        .all()
    )

    contact_data = contact.to_dict()
    from backend.services.agent_call_finalizer import sip_calls_for
    contact_sip = sip_calls_for(db, calls)
    contact_data["calls"] = []
    for c in calls:
        cd = c.to_dict()
        sip_call = contact_sip.get(str(c.call_session_id))
        cd["record_url"] = sip_call.recording_url if sip_call is not None else None
        contact_data["calls"].append(cd)
    contact_data["tasks"] = [_agent_task_dict(t) for t in tasks]

    # SMS-переписка с контактом (входящие + исходящие), резолв по номеру.
    sms = []
    try:
        from backend.models.voximplant_child import VoximplantChildAccount
        from backend.services.sms_history import get_sms_thread, sms_thread_to_dicts
        child = db.query(VoximplantChildAccount).filter(
            VoximplantChildAccount.user_id == current_user.id
        ).first()
        if child:
            sms = sms_thread_to_dicts(get_sms_thread(db, child.id, contact.phone, limit=20))
    except Exception as e:
        logger.warning(f"[AGENT] failed to load sms thread for contact {contact_id}: {e}")
    contact_data["sms"] = sms

    # Telegram-переписка (личный аккаунт владельца) с контактом.
    telegram = []
    try:
        from backend.services.telegram_user_service import get_thread as tg_get_thread
        telegram = [m.to_dict() for m in tg_get_thread(db, contact.id, limit=30)]
    except Exception as e:
        logger.warning(f"[AGENT] failed to load telegram thread for contact {contact_id}: {e}")
    contact_data["telegram"] = telegram

    # Instagram-переписка (DM бизнес-аккаунта владельца) с контактом.
    instagram = []
    try:
        from backend.services.instagram_service import get_thread as ig_get_thread
        instagram = [m.to_dict() for m in ig_get_thread(db, contact.id, limit=30)]
    except Exception as e:
        logger.warning(f"[AGENT] failed to load instagram thread for contact {contact_id}: {e}")
    contact_data["instagram"] = instagram

    # WhatsApp-переписка (номер владельца, Evolution API) с контактом.
    whatsapp = []
    try:
        from backend.services.whatsapp_service import get_thread as wa_get_thread
        whatsapp = [m.to_dict() for m in wa_get_thread(db, contact.id, limit=30)]
    except Exception as e:
        logger.warning(f"[AGENT] failed to load whatsapp thread for contact {contact_id}: {e}")
    contact_data["whatsapp"] = whatsapp
    return contact_data


@router.post("/contacts/{contact_id}/tasks")
async def create_agent_contact_task(
    contact_id: str,
    body: AgentTaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ручное создание задачи (запланированного звонка) для контакта из UI."""
    # Тариф Старт: агент сам не звонит и не пишет — задачи только на Profi
    if current_user.agent_mode() == "reactive":
        raise HTTPException(status_code=402, detail="reactive_mode")
    contact = db.query(AgentContact).filter(
        AgentContact.id == contact_id,
        AgentContact.user_id == current_user.id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="not_found")

    agent = db.query(AgentConfig).filter(
        AgentConfig.id == contact.agent_config_id,
        AgentConfig.user_id == current_user.id,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title_required")
    scheduled_time = _parse_scheduled_time(body.scheduled_time)

    task = Task(
        is_agent_task=True,
        agent_contact_id=contact.id,
        user_id=current_user.id,
        contact_id=None,
        status=TaskStatus.SCHEDULED,
        scheduled_time=scheduled_time,
        title=title,
        description="",
        **assistant_task_kwargs(agent),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    logger.info(f"[AGENT] Created manual task {task.id} for contact {contact_id}")
    return _agent_task_dict(task)


@router.post("/contacts")
async def create_agent_contact(
    body: AgentContactCreateRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Manually add a contact and auto-schedule a first call in 1 hour."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")

    contact = AgentContact(
        agent_config_id=agent.id,
        user_id=current_user.id,
        name=body.name,
        phone=body.phone,
        company=body.company,
        position=body.position,
        notes=body.notes,
        status="new",
        memory={},
    )
    db.add(contact)
    db.flush()

    # Auto-create first task in 1 hour, но прогоняем через ту же проверку
    # рабочих часов (Бишкек), что и массовый импорт — для унификации.
    # Тариф Старт («только отвечает»): агент первым не звонит — без автозадачи
    if current_user.agent_mode() == "reactive":
        db.commit()
        db.refresh(contact)
        logger.info(f"[AGENT] Created contact {contact.id} (reactive mode, no auto-task) for user {current_user.id}")
        return contact.to_dict()

    scheduled_time, _shifted = adjust_to_working_hours(
        now_utc() + timedelta(hours=1),
        agent.working_hours_start,
        agent.working_hours_end,
    )

    # Auto-create first task (route assistant FK by agent type)
    task = Task(
        is_agent_task=True,
        agent_contact_id=contact.id,
        user_id=current_user.id,
        contact_id=None,
        status=TaskStatus.SCHEDULED,
        scheduled_time=scheduled_time,
        title=f"Первый звонок: {body.name or body.phone}",
        description=body.notes or "",
        **assistant_task_kwargs(agent),
    )
    db.add(task)
    db.commit()
    db.refresh(contact)

    logger.info(f"[AGENT] Created contact {contact.id} with auto-task for user {current_user.id}")
    return contact.to_dict()


@router.put("/contacts/{contact_id}")
async def update_agent_contact(
    contact_id: str,
    body: AgentContactUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Обновление полей контакта (вручную из UI или через тулзу агента)."""
    contact = db.query(AgentContact).filter(
        AgentContact.id == contact_id,
        AgentContact.user_id == current_user.id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="not_found")

    update_data = body.dict(exclude_unset=True)
    # Нормализация CRM v2 полей:
    #   tags: List[str] → "a,b,c" (нижний регистр, пробелы, уникальные, max 10)
    #   next_step_at: ISO-строка → datetime (naive UTC); пустая → None
    if 'tags' in update_data:
        raw = update_data.pop('tags') or []
        seen = []
        for t in raw:
            v = (t or '').strip().lower()[:32]
            if v and v not in seen:
                seen.append(v)
            if len(seen) >= 10: break
        contact.tags = ",".join(seen) if seen else None
    if 'next_step_at' in update_data:
        v = update_data.pop('next_step_at')
        if not v:
            contact.next_step_at = None
        else:
            try:
                from datetime import datetime as _dt
                contact.next_step_at = _dt.fromisoformat(str(v).replace('Z', '+00:00')).replace(tzinfo=None)
            except Exception:
                pass  # игнорируем битый ISO
    for field, value in update_data.items():
        setattr(contact, field, value)

    db.commit()
    db.refresh(contact)
    logger.info(f"[AGENT] Updated contact {contact_id} fields: {list(body.dict(exclude_unset=True).keys())}")
    return contact.to_dict()


@router.delete("/contacts/{contact_id}")
async def delete_agent_contact(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an agent contact (cascade deletes AgentCalls)."""
    contact = db.query(AgentContact).filter(
        AgentContact.id == contact_id,
        AgentContact.user_id == current_user.id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="not_found")

    # Задачи контакта удаляем явно. Иначе FK ondelete=SET NULL обнулил бы у них
    # agent_contact_id, и они остались бы висеть без контакта — планировщику
    # бесполезны, из списка задач агента не убираются, а их FK на голосового
    # ассистента блокирует последующее удаление агента.
    deleted_tasks = db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.agent_contact_id == contact.id,
    ).delete(synchronize_session=False)

    db.delete(contact)
    db.commit()

    logger.info(f"[AGENT] Deleted contact {contact_id} ({deleted_tasks} tasks removed)")
    return {"detail": "deleted", "tasks_removed": deleted_tasks}


@router.get("/pipeline/stages")
async def get_pipeline_stages(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Стадии воронки агента: кастомные (CRM v2) или дефолтные."""
    agent = _resolve_agent(db, current_user, agent_id)
    stages = get_agent_stages(agent)
    return {"stages": stages, "custom": bool(agent and agent.pipeline_stages)}


class PipelineStagesSaveRequest(BaseModel):
    """
    Сохранение кастомных стадий воронки для текущего агента.
    stages: массив { key: str (lower a-z0-9_), label, color (#RRGGBB), kind (open|won|lost) }.
    """
    stages: List[dict] = Field(..., min_items=2, max_items=20)


@router.put("/pipeline/stages")
async def save_pipeline_stages(
    body: PipelineStagesSaveRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Записать кастомные стадии воронки для агента. Валидация в normalize_stages_list."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    saved = save_agent_stages(agent, body.stages)
    if saved is None:
        raise HTTPException(status_code=400, detail="invalid_stages")
    db.commit()
    logger.info(f"[AGENT] Agent {agent.id} pipeline_stages updated ({len(saved)} stages)")
    return {"stages": saved, "custom": True}


@router.delete("/pipeline/stages")
async def reset_pipeline_stages(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Сбросить кастомные стадии и вернуться к дефолтным."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")
    agent.pipeline_stages = None
    db.commit()
    return {"stages": AGENT_CONTACT_STAGES, "custom": False}


@router.patch("/contacts/{contact_id}/status")
async def update_agent_contact_status(
    contact_id: str,
    body: AgentContactStatusRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ручной перевод контакта на стадию воронки (drag-drop в канбане / select в карточке)."""
    contact = db.query(AgentContact).filter(
        AgentContact.id == contact_id,
        AgentContact.user_id == current_user.id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="not_found")
    # Валидация с учётом КАСТОМНЫХ стадий агента контакта (CRM v2)
    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first() if contact.agent_config_id else None
    if not is_valid_stage_for_agent(agent, body.status):
        raise HTTPException(status_code=400, detail="invalid_stage")

    old_stage = contact.status
    contact.status = body.status
    db.commit()
    db.refresh(contact)
    logger.info(f"[AGENT] Contact {contact_id} stage {old_stage} -> {body.status} (manual)")
    return contact.to_dict()


@router.post("/contacts/{contact_id}/ai-pause")
async def pause_agent_for_contact(
    contact_id: str,
    body: AgentContactPauseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """«Остановить агента» в переписке с клиентом (все мессенджеры; звонки не трогает)."""
    if not body.forever and not body.minutes:
        raise HTTPException(status_code=400, detail="minutes_or_forever_required")
    contact = db.query(AgentContact).filter(
        AgentContact.id == contact_id,
        AgentContact.user_id == current_user.id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="not_found")
    agent_pause.set_pause(contact, -1 if body.forever else body.minutes, "manual")
    db.commit()
    db.refresh(contact)
    return contact.to_dict()


@router.delete("/contacts/{contact_id}/ai-pause")
async def resume_agent_for_contact(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """«Вернуть агента»: снять паузу, агент ответит на следующее сообщение клиента."""
    contact = db.query(AgentContact).filter(
        AgentContact.id == contact_id,
        AgentContact.user_id == current_user.id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="not_found")
    agent_pause.clear_pause(contact)
    db.commit()
    db.refresh(contact)
    return contact.to_dict()


# ============================================================================
# ENDPOINTS — CALLS
# ============================================================================


@router.get("/calls")
async def list_agent_calls(
    agent_contact_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List agent calls with optional contact filter (scoped to one agent)."""
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="not_found")

    # Показываем только ФИНАЛИЗИРОВАННЫЕ звонки — у которых PostCall завершился и
    # есть достоверный результат. Промежуточные статусы ('calling' — звонок идёт,
    # 'finalizing' — идёт пост-обработка) скрываем, чтобы в списке не появлялись
    # звонки без подтверждённой информации.
    # Исключение — звонки через собственный SIP-шлюз: у них итог известен по событиям
    # моста, поэтому идущий звонок показываем сразу («в очереди», «набираем», «разговор»).
    from sqlalchemy import cast, String as SAString
    from backend.models.sip_gateway import SipCall
    from backend.services.agent_call_finalizer import call_outcome, sip_calls_for
    FINALIZED_STATUSES = ["answered", "no_answer", "failed"]
    q = db.query(AgentCall).filter(
        AgentCall.agent_config_id == agent.id,
        or_(
            AgentCall.status.in_(FINALIZED_STATUSES),
            and_(
                AgentCall.status.in_(["calling", "finalizing"]),
                AgentCall.call_session_id.in_(db.query(cast(SipCall.id, SAString))),
            ),
        ),
    )
    if agent_contact_id:
        q = q.filter(AgentCall.agent_contact_id == agent_contact_id)

    total = q.count()
    calls = q.order_by(AgentCall.created_at.desc()).offset(offset).limit(limit).all()
    sip_map = sip_calls_for(db, calls)

    result = []
    for c in calls:
        d = c.to_dict()
        # Что случилось со звонком: ответил / не взял / занято / проблема со связью / идёт
        sip_call = sip_map.get(str(c.call_session_id))
        d["outcome"] = call_outcome(c, sip_call)
        d["record_url"] = sip_call.recording_url if sip_call is not None else None
        # Add contact info
        if c.contact:
            d["contact_name"] = c.contact.name
            d["contact_phone"] = c.contact.phone
        result.append(d)

    return {
        "total": total,
        "calls": result,
    }


@router.get("/calls/{call_id}")
async def get_agent_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a single agent call with full transcript."""
    call = db.query(AgentCall).filter(
        AgentCall.id == call_id,
        AgentCall.user_id == current_user.id,
    ).first()
    if not call:
        raise HTTPException(status_code=404, detail="not_found")

    d = call.to_dict()
    from backend.services.agent_call_finalizer import call_outcome, sip_calls_for
    sip_call = sip_calls_for(db, [call]).get(str(call.call_session_id))
    d["outcome"] = call_outcome(call, sip_call)
    d["record_url"] = sip_call.recording_url if sip_call is not None else None
    if call.contact:
        d["contact_name"] = call.contact.name
        d["contact_phone"] = call.contact.phone
    return d


# ============================================================================
# ENDPOINTS — CONTACTS BULK IMPORT (xlsx/csv)
# ============================================================================

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@router.get("/contacts/import/template")
async def import_contacts_template(
    current_user: User = Depends(get_current_user),
):
    """Скачать xlsx-шаблон для импорта контактов (генерируется на лету)."""
    from backend.services.contact_import_service import generate_template_xlsx

    content = generate_template_xlsx()
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="contacts_template.xlsx"'},
    )


@router.post("/contacts/import/preview")
async def import_contacts_preview(
    file: UploadFile = File(...),
    agent_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Загрузка файла, парсинг и валидация БЕЗ записи в БД."""
    from backend.services.contact_import_service import (
        parse_file, assign_schedule, save_preview,
        MAX_IMPORT_ROWS, CREDITS_PER_CONTACT,
    )

    agent = _resolve_agent(db, current_user, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")

    content = await file.read()
    try:
        parsed = parse_file(file.filename or "", content)
    except ValueError:
        raise HTTPException(status_code=400, detail="unsupported_format")
    except Exception as e:
        logger.error(f"[AGENT-IMPORT] Parse failed for user {current_user.id}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="parse_failed")

    total_rows = parsed["total_rows"]
    if total_rows > MAX_IMPORT_ROWS:
        raise HTTPException(status_code=400, detail="exceed_limit")

    rows = parsed["rows"]
    errors = list(parsed["errors"])

    # Дубликаты: в пределах файла + уже существующие в базе этого агента
    existing_phones = {
        row[0] for row in db.query(AgentContact.phone).filter(
            AgentContact.agent_config_id == agent.id
        ).all()
    }

    duplicates: List[dict] = []
    unique_rows: List[dict] = []
    seen = set()
    for r in rows:
        ph = r["phone"]
        if ph in existing_phones or ph in seen:
            duplicates.append({"row": r["row"], "phone": ph})
            continue
        seen.add(ph)
        unique_rows.append(r)

    # Распределение времени + проверка рабочих часов
    shifted = assign_schedule(
        unique_rows,
        agent.working_hours_start,
        agent.working_hours_end,
        base_utc=now_utc(),
    )

    valid_rows = len(unique_rows)
    credits_required = valid_rows * CREDITS_PER_CONTACT
    credits_available = current_user.credits_balance or 0

    blocked_reasons: List[str] = []
    if valid_rows == 0:
        blocked_reasons.append("no_valid_rows")
    if credits_required > credits_available:
        blocked_reasons.append("insufficient_credits")
    can_proceed = len(blocked_reasons) == 0

    token = save_preview({
        "agent_id": str(agent.id),
        "user_id": str(current_user.id),
        "rows": unique_rows,
        "errors": errors,
        "duplicates": duplicates,
    })

    logger.info(
        f"[AGENT-IMPORT] Preview for user {current_user.id}: total={total_rows}, "
        f"valid={valid_rows}, errors={len(errors)}, duplicates={len(duplicates)}, shifted={shifted}"
    )

    return {
        "preview_token": token,
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "errors": errors,
        "duplicates": duplicates,
        "shifted_to_working_hours": shifted,
        "credits_required_estimate": credits_required,
        "credits_available": credits_available,
        "can_proceed": can_proceed,
        "blocked_reasons": blocked_reasons,
    }


def _row_has_explicit_task(r: dict) -> bool:
    """
    Строка несёт явную задачу из файла, если заполнены «Задача» (task_title)
    и/или «Когда звонить» (scheduled_dt). После json-сериализации превью
    scheduled_dt приходит строкой (или None, если время не задано).
    """
    if r.get("task_title"):
        return True
    sd = r.get("scheduled_dt")
    return bool(sd) and str(sd).strip().lower() not in ("", "none", "null")


async def _run_contacts_import(
    preview_token: str,
    agent_id: str,
    user_id: str,
    create_tasks: bool = True,
):
    """
    Фоновый импорт: создаёт AgentContact + Task пачками по 50.
    Открывает собственную сессию БД — безопасно для BackgroundTasks.

    create_tasks=False — авто-задачи оркестратора не создаются; задача
    ставится только для строк с явно заданными «Задача»/«Когда звонить».
    """
    from backend.services.contact_import_service import load_preview, delete_preview

    db = SessionLocal()
    try:
        data = load_preview(preview_token)
        if not data:
            logger.error(f"[AGENT-IMPORT] Preview {preview_token} expired/not found")
            return
        if data.get("user_id") != str(user_id):
            logger.error(f"[AGENT-IMPORT] Preview ownership mismatch for {preview_token}")
            return
        # Тариф Старт («только отвечает»): контакты импортируем, звонки не планируем
        _owner = db.query(User).filter(User.id == user_id).first()
        reactive = bool(_owner and _owner.agent_mode() == "reactive")

        agent = db.query(AgentConfig).filter(
            AgentConfig.id == agent_id,
            AgentConfig.user_id == user_id,
        ).first()
        if not agent:
            logger.error(f"[AGENT-IMPORT] Agent {agent_id} not found for import")
            return

        rows = data.get("rows", [])
        task_kwargs = assistant_task_kwargs(agent)

        # Повторный дедуп против БД (на случай изменений между preview и execute)
        existing_phones = {
            row[0] for row in db.query(AgentContact.phone).filter(
                AgentContact.agent_config_id == agent.id
            ).all()
        }

        created_contacts = 0
        created_tasks = 0
        batch = 0

        for r in rows:
            phone = r["phone"]
            if phone in existing_phones:
                continue
            existing_phones.add(phone)

            name = r.get("name")
            notes = r.get("notes")
            contact = AgentContact(
                agent_config_id=agent.id,
                user_id=user_id,
                name=name,
                phone=phone,
                company=r.get("company"),
                position=r.get("position"),
                notes=notes,
                status="new",
                memory={},
            )
            db.add(contact)
            db.flush()
            created_contacts += 1

            # Авто-задачи выключены → создаём задачу только если она явно задана в файле.
            if not reactive and (create_tasks or _row_has_explicit_task(r)):
                # scheduled_time_utc — ISO-строка с UTC-маркером
                try:
                    scheduled_time = datetime.fromisoformat(r["scheduled_time_utc"])
                except (ValueError, KeyError, TypeError):
                    scheduled_time = now_utc() + timedelta(hours=1)

                task = Task(
                    is_agent_task=True,
                    agent_contact_id=contact.id,
                    user_id=user_id,
                    contact_id=None,
                    status=TaskStatus.SCHEDULED,
                    scheduled_time=scheduled_time,
                    title=r.get("task_title") or f"Первый звонок: {name or phone}",
                    description=r.get("task_description") or notes or "",
                    **task_kwargs,
                )
                db.add(task)
                created_tasks += 1

            batch += 1
            if batch >= 50:
                db.commit()
                batch = 0

        db.commit()
        delete_preview(preview_token)

        logger.info(
            f"[AGENT-IMPORT] ✅ Import done for user {user_id}: "
            f"{created_contacts} contacts, {created_tasks} tasks"
        )

        # Telegram-уведомление владельцу (если настроен личный бот)
        user = db.query(User).filter(User.id == user_id).first()
        if user and user.telegram_bot_token and user.telegram_chat_id:
            try:
                from backend.services.telegram_notification import TelegramNotificationService
                text = (
                    f"🤖 <b>Voksy AI Agent</b>\n\n"
                    f"Импорт контактов завершён.\n"
                    f"Создано контактов: <b>{created_contacts}</b>\n"
                    f"Запланировано звонков: <b>{created_tasks}</b>"
                )
                await TelegramNotificationService.send_message(
                    user.telegram_bot_token, user.telegram_chat_id, text
                )
            except Exception as te:
                logger.warning(f"[AGENT-IMPORT] Telegram notify failed: {te}")

    except Exception as e:
        db.rollback()
        logger.error(f"[AGENT-IMPORT] Import failed for user {user_id}: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/contacts/import/execute")
async def import_contacts_execute(
    body: ImportExecuteRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Подтверждение импорта — запускает создание контактов в фоне."""
    from backend.services.contact_import_service import load_preview, CREDITS_PER_CONTACT

    data = load_preview(body.preview_token)
    if not data:
        raise HTTPException(status_code=404, detail="preview_expired")
    if data.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=403, detail="forbidden")

    agent = _resolve_agent(db, current_user, body.agent_id or data.get("agent_id"))
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")

    rows = data.get("rows", [])
    if not rows:
        raise HTTPException(status_code=400, detail="no_valid_rows")

    # Финальная проверка баланса кредитов
    credits_required = len(rows) * CREDITS_PER_CONTACT
    if credits_required > (current_user.credits_balance or 0):
        raise HTTPException(status_code=402, detail={
            "error": "insufficient_credits",
            "required": credits_required,
            "available": current_user.credits_balance or 0,
        })

    background_tasks.add_task(
        _run_contacts_import,
        body.preview_token,
        str(agent.id),
        str(current_user.id),
        body.create_tasks,
    )

    estimated = max(5, len(rows) // 10)
    logger.info(
        f"[AGENT-IMPORT] Execute started for user {current_user.id}: "
        f"{len(rows)} rows, create_tasks={body.create_tasks}"
    )
    return {"status": "started", "estimated_seconds": estimated, "total": len(rows)}


@router.get("/contacts/import/errors/{preview_token}")
async def import_contacts_errors(
    preview_token: str,
    current_user: User = Depends(get_current_user),
):
    """Скачать xlsx с проблемными строками для исправления."""
    from backend.services.contact_import_service import load_preview, generate_errors_xlsx

    data = load_preview(preview_token)
    if not data:
        raise HTTPException(status_code=404, detail="preview_expired")
    if data.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=403, detail="forbidden")

    content = generate_errors_xlsx(data.get("errors", []))
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="import_errors.xlsx"'},
    )
