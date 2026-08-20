# backend/schemas/translate_assistant.py
"""
Pydantic схемы для ассистента-переводчика (OpenAI Realtime Translation API).

✅ v1.0: Initial Translate Assistant schemas
"""

from typing import Optional
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# Поддерживаемые выходные языки OpenAI Realtime Translation (13)
SUPPORTED_OUTPUT_LANGUAGES = [
    "en", "es", "fr", "de", "it", "pt", "nl", "pl", "ru", "ja", "ko", "zh", "ar"
]


class TranslateAssistantBase(BaseModel):
    """Базовые поля переводчика."""
    name: str = Field(..., min_length=1, max_length=255, description="Название переводчика")
    description: Optional[str] = Field(None, max_length=500, description="Описание")
    output_language: str = Field(default="ru", description="Целевой язык перевода (один из 13)")
    source_language_hint: Optional[str] = Field(None, max_length=10, description="Подсказка исходного языка")
    enable_source_transcript: bool = Field(default=True, description="Показывать транскрипт исходника")
    greeting_message: Optional[str] = Field(None, max_length=500, description="Приветственное сообщение")

    @field_validator("output_language")
    @classmethod
    def validate_output_language(cls, v: str) -> str:
        """output_language должен быть одним из 13 поддерживаемых языков."""
        if v not in SUPPORTED_OUTPUT_LANGUAGES:
            raise ValueError(
                f"Неподдерживаемый язык перевода '{v}'. "
                f"Допустимые: {', '.join(SUPPORTED_OUTPUT_LANGUAGES)}"
            )
        return v


class TranslateAssistantCreate(TranslateAssistantBase):
    """Схема создания переводчика (POST)."""
    pass


class TranslateAssistantUpdate(BaseModel):
    """Схема обновления переводчика (PUT) — все поля Optional."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    output_language: Optional[str] = Field(None, description="Целевой язык перевода")
    source_language_hint: Optional[str] = Field(None, max_length=10)
    enable_source_transcript: Optional[bool] = None
    greeting_message: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None

    @field_validator("output_language")
    @classmethod
    def validate_output_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in SUPPORTED_OUTPUT_LANGUAGES:
            raise ValueError(
                f"Неподдерживаемый язык перевода '{v}'. "
                f"Допустимые: {', '.join(SUPPORTED_OUTPUT_LANGUAGES)}"
            )
        return v


class TranslateAssistantResponse(BaseModel):
    """Схема ответа API."""
    id: str
    user_id: Optional[str]
    name: str
    description: Optional[str]
    output_language: str
    source_language_hint: Optional[str]
    enable_source_transcript: bool
    greeting_message: Optional[str]
    is_active: bool
    is_public: bool
    total_conversations: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TranslateEmbedCodeResponse(BaseModel):
    """Схема ответа с embed-кодом."""
    embed_code: str
    assistant_id: str
