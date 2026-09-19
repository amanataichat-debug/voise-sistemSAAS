# backend/api/eleven_assistants.py
"""
REST API Eleven-ассистентов (OpenAI Realtime текстом + озвучка ElevenLabs).

Ключи серверные: settings.OPENAI_API_KEY и settings.ELEVENLABS_API_KEY.
Голоса берутся из аккаунта ElevenLabs по серверному ключу (/voices) и из
публичной библиотеки (/voices/library, фильтр по языку); библиотечный голос
перед использованием добавляется в аккаунт (/voices/library/add).
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.dependencies import check_assistant_limit, get_current_user
from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.eleven_assistant import (
    DEFAULT_ELEVEN_GREETING,
    DEFAULT_ELEVEN_LANGUAGE,
    DEFAULT_ELEVEN_LLM_MODEL,
    DEFAULT_ELEVEN_STABILITY,
    DEFAULT_ELEVEN_TTS_MODEL,
    ELEVEN_LANGUAGES,
    ELEVEN_LLM_MODELS,
    ELEVEN_STABILITY_LEVELS,
    ELEVEN_TTS_MODEL_IDS,
    ELEVEN_TTS_MODELS,
    ElevenAssistantConfig,
)
from backend.models.user import User
from backend.services.assistant_limit_service import exclude_agent_owned

logger = get_logger(__name__)
router = APIRouter()

ELEVEN_API = "https://api.elevenlabs.io/v1"
LANGUAGE_CODES = {lang["code"] for lang in ELEVEN_LANGUAGES}
# Коды, под которыми ElevenLabs помечает язык у голосов (verified_languages / labels / library)
LANGUAGE_ALIASES = {
    "ky": {"ky", "kir", "kyrgyz", "kirghiz"},
    "kk": {"kk", "kaz", "kazakh"},
    "uz": {"uz", "uzb", "uzbek"},
    "ru": {"ru", "rus", "russian"},
    "en": {"en", "eng", "english"},
    "tr": {"tr", "tur", "turkish"},
}


# ============================================================================
# SCHEMAS
# ============================================================================

class ElevenAssistantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = None
    voice_id: Optional[str] = Field(None, max_length=255, description="ElevenLabs voice_id из аккаунта")
    voice_name: Optional[str] = Field(None, max_length=255)
    tts_model: str = Field(default=DEFAULT_ELEVEN_TTS_MODEL, description=f"Модель синтеза: {ELEVEN_TTS_MODEL_IDS}")
    stability: float = Field(default=DEFAULT_ELEVEN_STABILITY, ge=0.0, le=1.0)
    llm_model: str = Field(default=DEFAULT_ELEVEN_LLM_MODEL, max_length=100)
    language: str = Field(default=DEFAULT_ELEVEN_LANGUAGE, max_length=10)
    greeting_message: Optional[str] = Field(default=DEFAULT_ELEVEN_GREETING, max_length=500)
    google_sheet_id: Optional[str] = Field(None, max_length=255)
    functions: Optional[List[Dict]] = None


class ElevenAssistantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = None
    voice_id: Optional[str] = Field(None, max_length=255)
    voice_name: Optional[str] = Field(None, max_length=255)
    tts_model: Optional[str] = None
    stability: Optional[float] = Field(None, ge=0.0, le=1.0)
    llm_model: Optional[str] = Field(None, max_length=100)
    language: Optional[str] = Field(None, max_length=10)
    greeting_message: Optional[str] = Field(None, max_length=500)
    google_sheet_id: Optional[str] = Field(None, max_length=255)
    functions: Optional[List[Dict]] = None
    is_active: Optional[bool] = None


class ElevenAssistantResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    system_prompt: Optional[str]
    voice_id: Optional[str]
    voice_name: Optional[str]
    tts_model: str
    stability: Optional[float]
    llm_model: str
    language: str
    greeting_message: Optional[str]
    google_sheet_id: Optional[str]
    functions: Optional[Any]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ElevenServerStatus(BaseModel):
    openai_key: bool
    elevenlabs_key: bool
    ready: bool
    ws_path: str = "/ws/eleven/"


class LibraryVoiceAdd(BaseModel):
    public_owner_id: str = Field(..., min_length=1)
    voice_id: str = Field(..., min_length=1)
    name: Optional[str] = Field(None, max_length=255)


# ============================================================================
# HELPERS
# ============================================================================

def _validate(data) -> None:
    tts_model = getattr(data, "tts_model", None)
    if tts_model is not None and tts_model not in ELEVEN_TTS_MODEL_IDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown TTS model. Available: {ELEVEN_TTS_MODEL_IDS}")
    llm_model = getattr(data, "llm_model", None)
    if llm_model is not None and llm_model not in ELEVEN_LLM_MODELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown LLM model. Available: {ELEVEN_LLM_MODELS}")
    language = getattr(data, "language", None)
    if language is not None and language.lower() not in LANGUAGE_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown language. Available: {sorted(LANGUAGE_CODES)}")


def to_response(a: ElevenAssistantConfig) -> ElevenAssistantResponse:
    return ElevenAssistantResponse(
        id=str(a.id), name=a.name, description=a.description, system_prompt=a.system_prompt,
        voice_id=a.voice_id, voice_name=a.voice_name,
        tts_model=a.tts_model or DEFAULT_ELEVEN_TTS_MODEL,
        stability=a.stability if a.stability is not None else DEFAULT_ELEVEN_STABILITY,
        llm_model=a.llm_model or DEFAULT_ELEVEN_LLM_MODEL,
        language=a.language or DEFAULT_ELEVEN_LANGUAGE,
        greeting_message=a.greeting_message, google_sheet_id=a.google_sheet_id,
        functions=a.functions, is_active=a.is_active, created_at=a.created_at,
    )


async def verify_assistant_access(assistant_id: str, user_id: str, db: Session) -> ElevenAssistantConfig:
    try:
        assistant_uuid = uuid.UUID(assistant_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid assistant ID format")
    assistant = db.query(ElevenAssistantConfig).filter(ElevenAssistantConfig.id == assistant_uuid).first()
    if not assistant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eleven assistant not found")
    if str(assistant.user_id) != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized to access this assistant")
    return assistant


def _require_key() -> str:
    if not settings.ELEVENLABS_API_KEY:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ELEVENLABS_API_KEY is not configured on the server")
    return settings.ELEVENLABS_API_KEY


async def _eleven_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    key = _require_key()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{ELEVEN_API}{path}", params=params, headers={"xi-api-key": key})
    if resp.status_code >= 400:
        logger.warning(f"[ELEVEN-API] GET {path} → {resp.status_code}: {resp.text[:300]}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ElevenLabs API error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _voice_matches_language(voice: Dict[str, Any], language: str) -> bool:
    aliases = LANGUAGE_ALIASES.get(language, {language})
    for vl in voice.get("verified_languages") or []:
        code = str((vl or {}).get("language") or "").lower()
        if code in aliases:
            return True
    labels = voice.get("labels") or {}
    if str(labels.get("language") or "").lower() in aliases:
        return True
    if str(voice.get("language") or "").lower() in aliases:
        return True
    return False


def _account_voice(voice: Dict[str, Any], language: str) -> Dict[str, Any]:
    labels = voice.get("labels") or {}
    return {
        "voice_id": voice.get("voice_id"),
        "name": voice.get("name"),
        "category": voice.get("category"),
        "description": voice.get("description"),
        "preview_url": voice.get("preview_url"),
        "gender": labels.get("gender"),
        "accent": labels.get("accent"),
        "age": labels.get("age"),
        "languages": [str((vl or {}).get("language") or "") for vl in (voice.get("verified_languages") or [])],
        "recommended": _voice_matches_language(voice, language),
    }


def _library_voice(voice: Dict[str, Any], language: str) -> Dict[str, Any]:
    return {
        "public_owner_id": voice.get("public_owner_id"),
        "voice_id": voice.get("voice_id"),
        "name": voice.get("name"),
        "category": voice.get("category"),
        "description": voice.get("description"),
        "preview_url": voice.get("preview_url"),
        "gender": voice.get("gender"),
        "accent": voice.get("accent"),
        "age": voice.get("age"),
        "language": voice.get("language"),
        "use_case": voice.get("use_case"),
        "free_users_allowed": voice.get("free_users_allowed"),
        "cloned_by_count": voice.get("cloned_by_count"),
        "is_added": voice.get("is_added_by_user"),
        "recommended": _voice_matches_language(voice, language),
    }


# ============================================================================
# META (фиксированные пути идут до /{assistant_id})
# ============================================================================

@router.get("/options")
async def get_eleven_options():
    """Справочник: модели синтеза с ценами, языки, уровни стабильности, диалоговые модели."""
    return {
        "tts_models": ELEVEN_TTS_MODELS,
        "default_tts_model": DEFAULT_ELEVEN_TTS_MODEL,
        "languages": ELEVEN_LANGUAGES,
        "default_language": DEFAULT_ELEVEN_LANGUAGE,
        "stability_levels": ELEVEN_STABILITY_LEVELS,
        "default_stability": DEFAULT_ELEVEN_STABILITY,
        "llm_models": ELEVEN_LLM_MODELS,
        "default_llm_model": DEFAULT_ELEVEN_LLM_MODEL,
        "default_greeting": DEFAULT_ELEVEN_GREETING,
    }


@router.get("/status", response_model=ElevenServerStatus)
async def get_eleven_server_status(current_user: User = Depends(get_current_user)):
    openai_ok = bool(settings.OPENAI_API_KEY)
    eleven_ok = bool(settings.ELEVENLABS_API_KEY)
    return ElevenServerStatus(openai_key=openai_ok, elevenlabs_key=eleven_ok, ready=openai_ok and eleven_ok)


@router.get("/voices")
async def list_account_voices(
    language: str = Query(DEFAULT_ELEVEN_LANGUAGE, max_length=10),
    current_user: User = Depends(get_current_user),
):
    """
    Голоса аккаунта ElevenLabs (серверный ключ). recommended=true — у голоса
    подтверждён нужный язык (verified_languages / labels); такие идут первыми.
    """
    language = language.lower()
    voices: List[Dict[str, Any]] = []
    next_token = None
    for _ in range(5):  # до 500 голосов
        params: Dict[str, Any] = {"page_size": 100}
        if next_token:
            params["next_page_token"] = next_token
        data = await _eleven_get("/voices", params)
        voices.extend(data.get("voices") or [])
        next_token = data.get("next_page_token")
        if not data.get("has_more") or not next_token:
            break
    items = [_account_voice(v, language) for v in voices if v.get("voice_id")]
    items.sort(key=lambda v: (not v["recommended"], (v.get("name") or "").lower()))
    return {"voices": items, "language": language, "recommended_count": sum(1 for v in items if v["recommended"])}


@router.get("/voices/library")
async def search_library_voices(
    language: str = Query(DEFAULT_ELEVEN_LANGUAGE, max_length=10),
    search: Optional[str] = Query(None, max_length=100),
    gender: Optional[str] = Query(None, max_length=20),
    page: int = Query(0, ge=0),
    page_size: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """
    Публичная библиотека голосов ElevenLabs с фильтром по языку (рекомендованные
    для кыргызского и т.д.). Чтобы использовать голос, его нужно добавить в
    аккаунт: POST /voices/library/add.
    """
    language = language.lower()
    tried = []
    for code in [language] + sorted(LANGUAGE_ALIASES.get(language, set()) - {language}):
        params: Dict[str, Any] = {"language": code, "page_size": page_size, "page": page, "sort": "trending"}
        if search:
            params["search"] = search
        if gender:
            params["gender"] = gender
        data = await _eleven_get("/shared-voices", params)
        tried.append(code)
        voices = data.get("voices") or []
        if voices or page > 0:
            return {
                "voices": [_library_voice(v, language) for v in voices],
                "has_more": bool(data.get("has_more")),
                "page": page,
                "language": language,
                "language_code_used": code,
            }
    return {"voices": [], "has_more": False, "page": page, "language": language, "language_code_used": tried[-1] if tried else language}


@router.post("/voices/library/add")
async def add_library_voice(body: LibraryVoiceAdd, current_user: User = Depends(get_current_user)):
    """Добавить голос из библиотеки в аккаунт ElevenLabs; вернуть voice_id для карточки."""
    key = _require_key()
    payload = {"new_name": (body.name or body.voice_id)[:100]}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{ELEVEN_API}/voices/add/{body.public_owner_id}/{body.voice_id}",
            json=payload, headers={"xi-api-key": key},
        )
    if resp.status_code >= 400:
        detail = resp.text[:300]
        # Голос уже есть в аккаунте — ElevenLabs отвечает ошибкой, но использовать его можно
        if "already" in detail.lower():
            return {"voice_id": body.voice_id, "name": payload["new_name"], "already_added": True}
        logger.warning(f"[ELEVEN-API] add voice → {resp.status_code}: {detail}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ElevenLabs API error {resp.status_code}: {detail[:200]}")
    data = resp.json()
    return {"voice_id": data.get("voice_id") or body.voice_id, "name": payload["new_name"], "already_added": False}


# ============================================================================
# CRUD
# ============================================================================

@router.get("")
async def get_eleven_assistants(
    include_agent_voices: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ElevenAssistantConfig).filter(ElevenAssistantConfig.user_id == current_user.id)
    if not include_agent_voices:
        query = exclude_agent_owned(query, ElevenAssistantConfig, db, current_user.id)
    assistants = query.order_by(ElevenAssistantConfig.created_at.desc()).all()
    return {
        "assistants": [to_response(a) for a in assistants],
        "server_ready": bool(settings.OPENAI_API_KEY and settings.ELEVENLABS_API_KEY),
    }


@router.post("", response_model=ElevenAssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_eleven_assistant(
    data: ElevenAssistantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_assistant_limit),
):
    _validate(data)
    try:
        assistant = ElevenAssistantConfig(
            user_id=current_user.id,
            name=data.name,
            description=data.description,
            system_prompt=data.system_prompt,
            voice_id=(data.voice_id or "").strip() or None,
            voice_name=data.voice_name,
            tts_model=data.tts_model,
            stability=data.stability,
            llm_model=data.llm_model,
            language=data.language.lower(),
            greeting_message=data.greeting_message,
            google_sheet_id=data.google_sheet_id,
            functions=data.functions,
            is_active=True,
        )
        db.add(assistant)
        db.commit()
        db.refresh(assistant)
        logger.info(f"[ELEVEN-API] assistant created: {assistant.id} for user {current_user.id}")
        return to_response(assistant)
    except Exception as e:
        db.rollback()
        logger.error(f"[ELEVEN-API] create failed: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to create Eleven assistant")


@router.get("/{assistant_id}", response_model=ElevenAssistantResponse)
async def get_eleven_assistant(assistant_id: str, db: Session = Depends(get_db),
                               current_user: User = Depends(get_current_user)):
    return to_response(await verify_assistant_access(assistant_id, str(current_user.id), db))


@router.put("/{assistant_id}", response_model=ElevenAssistantResponse)
async def update_eleven_assistant(assistant_id: str, data: ElevenAssistantUpdate, db: Session = Depends(get_db),
                                  current_user: User = Depends(get_current_user)):
    assistant = await verify_assistant_access(assistant_id, str(current_user.id), db)
    _validate(data)
    try:
        update = data.model_dump(exclude_unset=True)
        if "language" in update and update["language"]:
            update["language"] = update["language"].lower()
        if "voice_id" in update:
            update["voice_id"] = (update["voice_id"] or "").strip() or None
        for field, value in update.items():
            setattr(assistant, field, value)
        db.commit()
        db.refresh(assistant)
        return to_response(assistant)
    except Exception as e:
        db.rollback()
        logger.error(f"[ELEVEN-API] update failed {assistant_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update Eleven assistant")


@router.delete("/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_eleven_assistant(assistant_id: str, db: Session = Depends(get_db),
                                  current_user: User = Depends(get_current_user)):
    assistant = await verify_assistant_access(assistant_id, str(current_user.id), db)
    try:
        db.delete(assistant)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[ELEVEN-API] delete failed {assistant_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to delete Eleven assistant")
