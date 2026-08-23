# backend/api/grok_assistants.py
"""
REST API endpoints for xAI Grok Voice assistants management.
Handles CRUD operations for Grok Voice Agent API assistants.

PRODUCTION VERSION 1.3
- Complete CRUD operations
- Authorization validation
- Subscription validation
- Error handling
- Grok-specific features (web_search, x_search)
- Fixed functions field type (List[dict] instead of dict)
- v1.2: Cascade assistants (OpenRouter + Voximplant ASR/TTS)
- v1.3: Cascade API keys endpoints + route order fix
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Any
import uuid

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.db.session import get_db
from backend.models.user import User
from backend.models.grok_assistant import GrokAssistantConfig, GrokConversation
from backend.core.dependencies import (
    get_current_user, get_current_user_flexible,
    check_assistant_limit, check_assistant_limit_flexible,
)
from backend.services.assistant_limit_service import exclude_agent_owned

logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# PYDANTIC SCHEMAS - GROK
# ============================================================================

from pydantic import BaseModel, Field
from datetime import datetime


class GrokAssistantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Assistant name")
    description: Optional[str] = Field(None, max_length=500, description="Assistant description")
    system_prompt: str = Field(..., min_length=1, description="System instructions for assistant")
    voice: str = Field(default="Ara", description="Grok voice: Ara, Rex, Sal, Eve, Leo")
    language: str = Field(default="ru", description="Language code")
    sample_rate: int = Field(default=24000, description="Audio sample rate (8000-48000)")
    audio_format: str = Field(default="audio/pcm", description="Audio format: audio/pcm, audio/pcmu, audio/pcma")
    greeting_message: Optional[str] = Field(
        default="Здравствуйте! Чем я могу вам помочь?",
        max_length=500,
        description="Greeting message"
    )
    google_sheet_id: Optional[str] = Field(None, description="Google Sheets ID for logging")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Model temperature")
    max_tokens: int = Field(default=4096, ge=1, le=8192, description="Maximum tokens")
    functions: Optional[List[dict]] = Field(default=None, description="Enabled functions config")
    enable_web_search: bool = Field(default=False, description="Enable native Grok web search")
    enable_x_search: bool = Field(default=False, description="Enable X (Twitter) search")
    x_allowed_handles: Optional[List[str]] = Field(default=None, description="Allowed X handles for search")
    collection_ids: Optional[List[str]] = Field(default=None, description="Vector store IDs for file_search")
    is_telephony_enabled: bool = Field(default=False, description="Enable telephony mode")


class GrokAssistantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = Field(None, min_length=1)
    voice: Optional[str] = None
    language: Optional[str] = None
    sample_rate: Optional[int] = Field(None, ge=8000, le=48000)
    audio_format: Optional[str] = None
    greeting_message: Optional[str] = Field(None, max_length=500)
    google_sheet_id: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=8192)
    functions: Optional[List[dict]] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    enable_web_search: Optional[bool] = None
    enable_x_search: Optional[bool] = None
    x_allowed_handles: Optional[List[str]] = None
    collection_ids: Optional[List[str]] = None
    is_telephony_enabled: Optional[bool] = None


class GrokAssistantResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str]
    system_prompt: str
    voice: str
    language: str
    sample_rate: int
    audio_format: str
    greeting_message: Optional[str]
    google_sheet_id: Optional[str]
    functions: Optional[Any]
    is_active: bool
    is_public: bool
    created_at: datetime
    updated_at: datetime
    total_conversations: int
    temperature: float
    max_tokens: int
    enable_web_search: bool
    enable_x_search: bool
    x_allowed_handles: Optional[List[str]]
    collection_ids: Optional[List[str]]
    is_telephony_enabled: bool

    class Config:
        from_attributes = True


class GrokConversationResponse(BaseModel):
    id: str
    assistant_id: str
    session_id: str
    user_message: Optional[str]
    assistant_message: Optional[str]
    function_name: Optional[str]
    caller_number: Optional[str]
    call_direction: Optional[str]
    tokens_used: int
    audio_duration_ms: Optional[int]
    latency_ms: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class EmbedCodeResponse(BaseModel):
    embed_code: str
    assistant_id: str


class GrokVoicesResponse(BaseModel):
    voices: List[dict]


# ============================================================================
# PYDANTIC SCHEMAS - CASCADE
# ============================================================================

class CascadeAssistantCreate(BaseModel):
    name:              str            = Field(..., min_length=1, max_length=255)
    description:       Optional[str]  = Field(None, max_length=500)
    system_prompt:     str            = Field(..., min_length=1)
    greeting_message:  Optional[str]  = Field(
        default="Здравствуйте! Чем я могу вам помочь?", max_length=500
    )
    openrouter_model:  str            = Field(default="meta-llama/llama-3.3-70b-instruct")
    temperature:       float          = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens:        int            = Field(default=1024, ge=1, le=8192)
    tts_provider:      str            = Field(default="voxtts")
    tts_voice:         str            = Field(default="Anna")
    tts_lang:          str            = Field(default="ru")
    asr_lang:          str            = Field(default="ru")
    # Пауза перед ответом: 300 (быстрая) / 650 (сбалансированная) / 1000 (терпеливая)
    silence_duration_ms: int          = Field(default=300, ge=200, le=1500)
    functions:         Optional[List[dict]] = None
    google_sheet_id:   Optional[str]  = None
    is_telephony_enabled: bool        = Field(default=True)


class CascadeAssistantUpdate(BaseModel):
    name:              Optional[str]   = Field(None, min_length=1, max_length=255)
    description:       Optional[str]   = Field(None, max_length=500)
    system_prompt:     Optional[str]   = Field(None, min_length=1)
    greeting_message:  Optional[str]   = Field(None, max_length=500)
    openrouter_model:  Optional[str]   = None
    temperature:       Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens:        Optional[int]   = Field(None, ge=1, le=8192)
    tts_provider:      Optional[str]   = None
    tts_voice:         Optional[str]   = None
    tts_lang:          Optional[str]   = None
    asr_lang:          Optional[str]   = None
    silence_duration_ms: Optional[int] = Field(None, ge=200, le=1500)
    functions:         Optional[List[dict]] = None
    google_sheet_id:   Optional[str]   = None
    is_active:         Optional[bool]  = None
    is_telephony_enabled: Optional[bool] = None


class CascadeAssistantResponse(BaseModel):
    id:               str
    user_id:          str
    name:             str
    description:      Optional[str]
    system_prompt:    str
    greeting_message: Optional[str]
    openrouter_model: Optional[str]
    temperature:      float
    max_tokens:       int
    tts_provider:     Optional[str]
    tts_voice:        Optional[str]
    tts_lang:         str
    asr_lang:         str
    silence_duration_ms: Optional[int] = None
    functions:        Optional[Any]
    google_sheet_id:  Optional[str]
    is_active:        bool
    is_telephony_enabled: bool
    total_conversations: int
    created_at:       datetime
    updated_at:       datetime

    class Config:
        from_attributes = True


class CascadeApiKeysUpdate(BaseModel):
    openrouter_api_key: Optional[str] = None


class CascadeApiKeysStatus(BaseModel):
    has_openrouter_key: bool
    openrouter_key_preview: Optional[str] = None


# ============================================================================
# CONSTANTS
# ============================================================================

TTS_PROVIDERS = {
    "voxtts": {
        "name": "VoxTTS (Voximplant)",
        "voices": [
            {"id": "Anna",   "name": "Анна",   "gender": "female"},
            {"id": "Sergey", "name": "Сергей", "gender": "male"},
        ]
    },
    "yandex": {
        "name": "Яндекс",
        "voices": [
            {"id": "alena",   "name": "Алёна",    "gender": "female"},
            {"id": "filipp",  "name": "Филипп",   "gender": "male"},
            {"id": "jane",    "name": "Джейн",    "gender": "female"},
            {"id": "madirus", "name": "Мадирус",  "gender": "male"},
            {"id": "omazh",   "name": "Омаж",     "gender": "female"},
            {"id": "zahar",   "name": "Захар",    "gender": "male"},
        ]
    },
    "tinkoff": {
        "name": "Т-Банк",
        "voices": [
            {"id": "ru_female_tanya",  "name": "Таня",     "gender": "female"},
            {"id": "ru_male_boris",    "name": "Борис",    "gender": "male"},
            {"id": "ru_female_alyona", "name": "Алёна",    "gender": "female"},
            {"id": "ru_male_dorofeev", "name": "Дорофеев", "gender": "male"},
        ]
    },
    "sber": {
        "name": "Сбер Салют",
        "voices": [
            {"id": "Nadia", "name": "Надя", "gender": "female"},
            {"id": "Bys_n", "name": "Бас",  "gender": "male"},
            {"id": "May_n", "name": "Майя", "gender": "female"},
            {"id": "Tur_n", "name": "Тур",  "gender": "male"},
        ]
    },
}

GROK_VOICES = [
    {"id": "Ara", "name": "Ara", "gender": "Female", "tone": "Warm, friendly", "description": "Default voice, balanced and conversational"},
    {"id": "Rex", "name": "Rex", "gender": "Male", "tone": "Confident, clear", "description": "Professional and articulate, ideal for business"},
    {"id": "Sal", "name": "Sal", "gender": "Neutral", "tone": "Smooth, balanced", "description": "Versatile voice suitable for various contexts"},
    {"id": "Eve", "name": "Eve", "gender": "Female", "tone": "Energetic, upbeat", "description": "Engaging and enthusiastic, great for interactive experiences"},
    {"id": "Leo", "name": "Leo", "gender": "Male", "tone": "Authoritative, strong", "description": "Decisive and commanding, suitable for instructional content"},
]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def mask_api_key(key: Optional[str]) -> Optional[str]:
    if not key or len(key) < 8:
        return None
    return f"{key[:6]}...{key[-4:]}"


async def verify_assistant_access(
    assistant_id: str, user_id: str, db: Session, require_ownership: bool = True
) -> GrokAssistantConfig:
    try:
        assistant_uuid = uuid.UUID(assistant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid assistant ID format")

    assistant = db.query(GrokAssistantConfig).filter(GrokAssistantConfig.id == assistant_uuid).first()
    if not assistant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grok assistant not found")
    if require_ownership and str(assistant.user_id) != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this assistant")
    return assistant


def validate_voice(voice: str) -> str:
    valid_voices = ["Ara", "Rex", "Sal", "Eve", "Leo"]
    for v in valid_voices:
        if v.lower() == voice.lower():
            return v
    return "Ara"


def validate_audio_format(fmt: str) -> str:
    valid_formats = ["audio/pcm", "audio/pcmu", "audio/pcma"]
    return fmt if fmt in valid_formats else "audio/pcm"


def validate_cascade_tts(provider: Optional[str], voice: Optional[str]) -> None:
    """
    Проверить пару TTS-провайдер + голос каскад-ассистента.

    Без этой проверки неизвестный голос молча заменялся дефолтным уже на
    звонке — интеграция по API-ключу получала не тот голос, который просила.
    """
    if provider is not None and provider not in TTS_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown TTS provider. Available: {list(TTS_PROVIDERS.keys())}",
        )
    if voice is None:
        return
    provider_voices = TTS_PROVIDERS.get(provider or "voxtts", {}).get("voices", [])
    valid = [v["id"] for v in provider_voices]
    if voice not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown TTS voice for provider '{provider or 'voxtts'}'. Available: {valid}",
        )


def get_cascade_or_404(assistant_id: str, user_id: str, db: Session) -> GrokAssistantConfig:
    try:
        uid = uuid.UUID(assistant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid assistant ID format")
    a = db.query(GrokAssistantConfig).filter(
        GrokAssistantConfig.id == uid,
        GrokAssistantConfig.assistant_type == "cascade"
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Cascade assistant not found")
    if str(a.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return a


def cascade_to_response(a: GrokAssistantConfig) -> CascadeAssistantResponse:
    return CascadeAssistantResponse(
        id=str(a.id), user_id=str(a.user_id), name=a.name, description=a.description,
        system_prompt=a.system_prompt, greeting_message=a.greeting_message,
        openrouter_model=a.openrouter_model, temperature=a.temperature, max_tokens=a.max_tokens,
        tts_provider=a.tts_provider, tts_voice=a.tts_voice,
        tts_lang=a.tts_lang or "ru", asr_lang=a.asr_lang or "ru",
        silence_duration_ms=a.silence_duration_ms or 300,
        functions=a.functions, google_sheet_id=a.google_sheet_id,
        is_active=a.is_active, is_telephony_enabled=a.is_telephony_enabled,
        total_conversations=a.total_conversations, created_at=a.created_at, updated_at=a.updated_at,
    )


def grok_to_response(a: GrokAssistantConfig) -> GrokAssistantResponse:
    return GrokAssistantResponse(
        id=str(a.id), user_id=str(a.user_id), name=a.name, description=a.description,
        system_prompt=a.system_prompt, voice=a.voice, language=a.language,
        sample_rate=a.sample_rate, audio_format=a.audio_format,
        greeting_message=a.greeting_message, google_sheet_id=a.google_sheet_id,
        functions=a.functions, is_active=a.is_active, is_public=a.is_public,
        created_at=a.created_at, updated_at=a.updated_at,
        total_conversations=a.total_conversations, temperature=a.temperature, max_tokens=a.max_tokens,
        enable_web_search=a.enable_web_search, enable_x_search=a.enable_x_search,
        x_allowed_handles=a.x_allowed_handles, collection_ids=a.collection_ids,
        is_telephony_enabled=a.is_telephony_enabled,
    )


# ============================================================================
# ROUTE ORDER MATTERS!
# Fixed paths (/voices, /cascade/...) MUST come before
# parameterised paths (/{assistant_id}) so FastAPI doesn't
# try to parse "cascade" or "voices" as a UUID.
# ============================================================================


# ============================================================================
# 1. GROK VOICES (fixed path)
# ============================================================================

@router.get("/voices", response_model=GrokVoicesResponse)
async def get_grok_voices():
    return GrokVoicesResponse(voices=GROK_VOICES)


# ============================================================================
# 2. CASCADE - API KEYS (fixed paths)
# ============================================================================

@router.get("/cascade/api-keys", response_model=CascadeApiKeysStatus)
async def get_cascade_api_keys_status(
    current_user: User = Depends(get_current_user)
):
    return CascadeApiKeysStatus(
        has_openrouter_key=bool(current_user.openrouter_api_key),
        openrouter_key_preview=mask_api_key(current_user.openrouter_api_key),
    )


@router.put("/cascade/api-keys", response_model=CascadeApiKeysStatus)
async def update_cascade_api_keys(
    keys_data: CascadeApiKeysUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        if keys_data.openrouter_api_key is not None:
            if keys_data.openrouter_api_key == "":
                current_user.openrouter_api_key = None
            else:
                current_user.openrouter_api_key = keys_data.openrouter_api_key
        db.commit()
        db.refresh(current_user)
        logger.info(f"[CASCADE] OpenRouter API key updated for user {current_user.id}")
        return CascadeApiKeysStatus(
            has_openrouter_key=bool(current_user.openrouter_api_key),
            openrouter_key_preview=mask_api_key(current_user.openrouter_api_key),
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[CASCADE] Error updating API key: {e}")
        raise HTTPException(status_code=500, detail="Failed to update API key")


# ============================================================================
# 3. CASCADE - TTS PROVIDERS (fixed path)
# ============================================================================

@router.get("/cascade/tts-providers")
async def get_cascade_tts_providers():
    return {"providers": TTS_PROVIDERS}


# ============================================================================
# 3.1 CASCADE - CREDITS (fixed paths /cascade/credits/*)
# ============================================================================
# Отдельный кошелёк кредитов каскада (LLM gpt-realtime-2.1-mini на серверном ключе).
# Независим от кредитов оркестратора и подписки agent — доступен на всех тарифах.

class CascadePurchaseRequest(BaseModel):
    package_code: str


@router.get("/cascade/credits/balance")
async def get_cascade_credits_balance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_flexible),
):
    """Баланс кредитов каскада. Лениво выдаёт разовый тестовый грант
    (CascadeCreditService.TRIAL_CREDITS), если юзер его ещё не получал."""
    from backend.services.cascade_credit_service import (
        CascadeCreditService, INPUT_CREDITS_PER_1K, OUTPUT_CREDITS_PER_1K,
    )
    # Ленивая идемпотентная выдача тестового гранта.
    if not current_user.cascade_trial_granted:
        try:
            CascadeCreditService.grant_trial(db, current_user)
        except Exception as e:
            logger.warning(f"[CASCADE-CREDITS] lazy trial grant failed for {current_user.id}: {e}")

    balance = CascadeCreditService.get_balance(db, current_user.id)
    return {
        "cascade_credits_balance": balance,
        "trial_granted": bool(current_user.cascade_trial_granted),
        "input_credits_per_1k": INPUT_CREDITS_PER_1K,
        "output_credits_per_1k": OUTPUT_CREDITS_PER_1K,
    }


@router.get("/cascade/credits/packages")
async def get_cascade_credits_packages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Активные пакеты докупки кредитов каскада (product='cascade')."""
    from backend.models.credit_package import CreditPackage
    packages = (
        db.query(CreditPackage)
        .filter(CreditPackage.is_active == True,
                CreditPackage.product == "cascade")
        .order_by(CreditPackage.sort_order.asc())
        .all()
    )
    return {"packages": [p.to_dict() for p in packages]}


@router.get("/cascade/credits/transactions")
async def get_cascade_credits_transactions(
    limit: int = 50,
    offset: int = 0,
    type_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """История транзакций кредитов каскада."""
    from backend.services.cascade_credit_service import CascadeCreditService
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    rows, total = CascadeCreditService.get_transactions(
        db, current_user.id, limit=limit, offset=offset, type_filter=type_filter
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "transactions": [t.to_dict() for t in rows],
    }


@router.post("/cascade/credits/purchase")
async def purchase_cascade_credits(
    body: CascadePurchaseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Создать Finik-платёж для покупки пакета кредитов каскада.
    Доступно на всех тарифах — подписка не требуется."""
    from backend.models.credit_package import CreditPackage
    from backend.api.credits import create_finik_payment_transaction

    package = db.query(CreditPackage).filter(
        CreditPackage.code == body.package_code,
        CreditPackage.is_active == True,
        CreditPackage.product == "cascade",
    ).first()
    if not package:
        raise HTTPException(status_code=404, detail="package_not_found")

    payment = await create_finik_payment_transaction(
        db=db,
        user=current_user,
        amount=float(package.price_rub),
        description=f"Пакет кредитов Cascade {package.name} ({package.credits} кредитов)",
        details={"type": "cascade_package", "package_code": package.code, "credits": package.credits},
    )

    logger.info(
        f"[CASCADE-CREDITS] Purchase payment created for user {current_user.id}, "
        f"package {package.code}, payment {payment['payment_id']}"
    )

    return {
        **payment,
        "package": package.to_dict(),
    }


# ============================================================================
# 4. CASCADE - LIST & CREATE (fixed path /cascade)
# ============================================================================

@router.get("/cascade", response_model=List[CascadeAssistantResponse])
async def list_cascade_assistants(
    include_agent_voices: bool = Query(
        False,
        description="Показать голосовых ассистентов агентов обзвона (по умолчанию скрыты)",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_flexible)
):
    query = db.query(GrokAssistantConfig).filter(
        GrokAssistantConfig.user_id == current_user.id,
        GrokAssistantConfig.assistant_type == "cascade"
    )
    if not include_agent_voices:
        query = exclude_agent_owned(query, GrokAssistantConfig, db, current_user.id)
    assistants = query.order_by(GrokAssistantConfig.created_at.desc()).all()
    return [cascade_to_response(a) for a in assistants]


@router.post("/cascade", response_model=CascadeAssistantResponse, status_code=201)
async def create_cascade_assistant(
    data: CascadeAssistantCreate,
    db: Session = Depends(get_db),
    # check_assistant_limit_flexible проверяет активность подписки + общий
    # лимит ассистентов по всем провайдерам; авторизация — JWT или X-Api-Key
    current_user: User = Depends(check_assistant_limit_flexible)
):
    # ✅ Каскад работает на СЕРВЕРНОМ ключе OpenAI (settings.OPENAI_API_KEY),
    # пользовательский ключ больше не требуется. Расход LLM оплачивается
    # кредитами каскада (cascade_credits_balance).
    validate_cascade_tts(data.tts_provider, data.tts_voice)

    assistant = GrokAssistantConfig(
        user_id=current_user.id, assistant_type="cascade",
        name=data.name, description=data.description,
        system_prompt=data.system_prompt, greeting_message=data.greeting_message,
        openrouter_model=data.openrouter_model, temperature=data.temperature, max_tokens=data.max_tokens,
        tts_provider=data.tts_provider, tts_voice=data.tts_voice,
        tts_lang=data.tts_lang, asr_lang=data.asr_lang,
        silence_duration_ms=data.silence_duration_ms,
        functions=data.functions, google_sheet_id=data.google_sheet_id,
        is_active=True, is_public=False, is_telephony_enabled=data.is_telephony_enabled,
    )
    db.add(assistant)
    db.commit()
    db.refresh(assistant)
    logger.info(f"[CASCADE] Created: {assistant.id} for user {current_user.id}")
    return cascade_to_response(assistant)


# ============================================================================
# 5. CASCADE - GET / UPDATE / DELETE by ID (/cascade/{assistant_id})
# ============================================================================

@router.get("/cascade/{assistant_id}", response_model=CascadeAssistantResponse)
async def get_cascade_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_flexible)
):
    a = get_cascade_or_404(assistant_id, str(current_user.id), db)
    return cascade_to_response(a)


@router.put("/cascade/{assistant_id}", response_model=CascadeAssistantResponse)
async def update_cascade_assistant(
    assistant_id: str,
    data: CascadeAssistantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_flexible)
):
    a = get_cascade_or_404(assistant_id, str(current_user.id), db)
    # Голос проверяем против провайдера, который будет у ассистента после апдейта
    validate_cascade_tts(data.tts_provider or a.tts_provider, data.tts_voice)
    for key, value in data.dict(exclude_unset=True).items():
        setattr(a, key, value)
    db.commit()
    db.refresh(a)
    logger.info(f"[CASCADE] Updated: {assistant_id}")
    return cascade_to_response(a)


@router.delete("/cascade/{assistant_id}", status_code=204)
async def delete_cascade_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_flexible)
):
    a = get_cascade_or_404(assistant_id, str(current_user.id), db)
    db.delete(a)
    db.commit()
    logger.info(f"[CASCADE] Deleted: {assistant_id}")


# ============================================================================
# 6. GROK - LIST (root path)
# ============================================================================

@router.get("", response_model=List[GrokAssistantResponse])
async def get_grok_assistants(
    include_agent_voices: bool = Query(
        False,
        description="Показать голосовых ассистентов агентов обзвона (по умолчанию скрыты)",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        logger.info(f"[GROK-API] Fetching assistants for user {current_user.id}")
        # Агент никогда не создаёт assistant_type='grok' (только 'cascade'),
        # так что фильтр здесь — на будущее, для единообразия с остальными.
        query = db.query(GrokAssistantConfig).filter(
            GrokAssistantConfig.user_id == current_user.id,
            GrokAssistantConfig.assistant_type == "grok"
        )
        if not include_agent_voices:
            query = exclude_agent_owned(query, GrokAssistantConfig, db, current_user.id)
        assistants = query.order_by(GrokAssistantConfig.created_at.desc()).all()
        logger.info(f"[GROK-API] Found {len(assistants)} Grok assistants")
        return [grok_to_response(a) for a in assistants]
    except Exception as e:
        logger.error(f"[GROK-API] Error fetching assistants: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch Grok assistants")


# ============================================================================
# 7. GROK - CREATE (root path)
# ============================================================================

@router.post("", response_model=GrokAssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_grok_assistant(
    assistant_data: GrokAssistantCreate,
    db: Session = Depends(get_db),
    # check_assistant_limit проверяет активность подписки + общий лимит
    # ассистентов по всем провайдерам
    current_user: User = Depends(check_assistant_limit)
):
    try:
        logger.info(f"[GROK-API] Creating Grok assistant for user {current_user.id}")
        if not current_user.grok_api_key:
            raise HTTPException(status_code=400, detail="xAI Grok API key is required. Please add it in settings.")

        validated_voice = validate_voice(assistant_data.voice)
        validated_format = validate_audio_format(assistant_data.audio_format)

        assistant = GrokAssistantConfig(
            user_id=current_user.id,
            name=assistant_data.name, description=assistant_data.description,
            system_prompt=assistant_data.system_prompt, voice=validated_voice,
            language=assistant_data.language, sample_rate=assistant_data.sample_rate,
            audio_format=validated_format, greeting_message=assistant_data.greeting_message,
            google_sheet_id=assistant_data.google_sheet_id,
            temperature=assistant_data.temperature, max_tokens=assistant_data.max_tokens,
            functions=assistant_data.functions, is_active=True, is_public=False,
            enable_web_search=assistant_data.enable_web_search,
            enable_x_search=assistant_data.enable_x_search,
            x_allowed_handles=assistant_data.x_allowed_handles,
            collection_ids=assistant_data.collection_ids,
            is_telephony_enabled=assistant_data.is_telephony_enabled,
        )
        db.add(assistant)
        db.commit()
        db.refresh(assistant)
        logger.info(f"[GROK-API] Grok assistant created: {assistant.id}")
        return grok_to_response(assistant)

    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[GROK-API] Database integrity error: {e}")
        raise HTTPException(status_code=400, detail="Failed to create assistant due to database constraint")
    except Exception as e:
        db.rollback()
        logger.error(f"[GROK-API] Error creating assistant: {e}")
        raise HTTPException(status_code=500, detail="Failed to create Grok assistant")


# ============================================================================
# 8. GROK - GET / UPDATE / DELETE by ID (/{assistant_id})
#    MUST be after all /cascade/* and /voices routes
# ============================================================================

@router.get("/{assistant_id}", response_model=GrokAssistantResponse)
async def get_grok_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        assistant = await verify_assistant_access(assistant_id, str(current_user.id), db, True)
        return grok_to_response(assistant)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GROK-API] Error fetching assistant {assistant_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch Grok assistant")


@router.put("/{assistant_id}", response_model=GrokAssistantResponse)
async def update_grok_assistant(
    assistant_id: str,
    assistant_data: GrokAssistantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        assistant = await verify_assistant_access(assistant_id, str(current_user.id), db, True)
        update_data = assistant_data.dict(exclude_unset=True)
        if "voice" in update_data:
            update_data["voice"] = validate_voice(update_data["voice"])
        if "audio_format" in update_data:
            update_data["audio_format"] = validate_audio_format(update_data["audio_format"])
        for key, value in update_data.items():
            setattr(assistant, key, value)
        db.commit()
        db.refresh(assistant)
        logger.info(f"[GROK-API] Grok assistant updated: {assistant_id}")
        return grok_to_response(assistant)

    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Failed to update assistant due to database constraint")
    except Exception as e:
        db.rollback()
        logger.error(f"[GROK-API] Error updating assistant {assistant_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update Grok assistant")


@router.delete("/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_grok_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        assistant = await verify_assistant_access(assistant_id, str(current_user.id), db, True)
        db.delete(assistant)
        db.commit()
        logger.info(f"[GROK-API] Grok assistant deleted: {assistant_id}")
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[GROK-API] Error deleting assistant {assistant_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete Grok assistant")


# ============================================================================
# 9. GROK - SUB-RESOURCES (/{assistant_id}/conversations, etc.)
# ============================================================================

@router.get("/{assistant_id}/conversations", response_model=List[GrokConversationResponse])
async def get_grok_conversations(
    assistant_id: str,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        await verify_assistant_access(assistant_id, str(current_user.id), db, True)
        conversations = db.query(GrokConversation).filter(
            GrokConversation.assistant_id == uuid.UUID(assistant_id)
        ).order_by(GrokConversation.created_at.desc()).offset(skip).limit(limit).all()
        return [
            GrokConversationResponse(
                id=str(conv.id), assistant_id=str(conv.assistant_id),
                session_id=conv.session_id, user_message=conv.user_message,
                assistant_message=conv.assistant_message, function_name=conv.function_name,
                caller_number=conv.caller_number, call_direction=conv.call_direction,
                tokens_used=conv.tokens_used, audio_duration_ms=conv.audio_duration_ms,
                latency_ms=conv.latency_ms, created_at=conv.created_at
            )
            for conv in conversations
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GROK-API] Error fetching conversations: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch conversations")


@router.get("/{assistant_id}/embed-code", response_model=EmbedCodeResponse)
async def get_grok_embed_code(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        assistant = await verify_assistant_access(assistant_id, str(current_user.id), db, True)
        if not assistant.is_active:
            raise HTTPException(status_code=400, detail="Assistant must be active to generate embed code")

        host_url = settings.HOST_URL
        embed_code = f"""<!-- Voksy AI Grok Voice Assistant -->
<script>
    (function() {{
        var script = document.createElement('script');
        script.src = '{host_url}/static/grok-widget.js';
        script.dataset.assistantId = '{assistant_id}';
        script.dataset.server = '{host_url}';
        script.dataset.position = 'bottom-right';
        script.dataset.provider = 'grok';
        script.async = true;
        document.head.appendChild(script);
    }})();
</script>
<!-- End Voksy AI Grok -->"""
        return EmbedCodeResponse(embed_code=embed_code, assistant_id=assistant_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GROK-API] Error generating embed code: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate embed code")


@router.post("/{assistant_id}/verify-sheet")
async def verify_grok_google_sheet(
    assistant_id: str,
    sheet_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        sheet_id = sheet_data.get("sheet_id")
        if not sheet_id:
            raise HTTPException(status_code=400, detail="sheet_id is required")
        if assistant_id != "new":
            await verify_assistant_access(assistant_id, str(current_user.id), db, True)

        from backend.services.google_sheets_service import GoogleSheetsService
        result = await GoogleSheetsService.verify_sheet_access(sheet_id)
        if result:
            return {"success": True, "message": "Google Sheet подключена успешно"}
        else:
            return {"success": False, "message": "Не удалось подключиться к таблице. Проверьте права доступа."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GROK-API] Error verifying Google Sheet: {e}")
        return {"success": False, "message": f"Ошибка проверки: {str(e)}"}
