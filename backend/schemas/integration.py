from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import datetime
import re

class IntegrationBase(BaseModel):
    """Базовая схема для интеграции."""
    name: str = Field(..., description="Название интеграции")
    type: str = Field(..., description="Тип интеграции")
    webhook_url: str = Field(..., description="URL вебхука")
    
    @validator('webhook_url')
    def validate_webhook_url(cls, v):
        """Проверка формата URL вебхука."""
        url_pattern = r'^https?://[\w.-]+(:\d+)?(/[\w./\-?=%&]*)?$'
        if not re.match(url_pattern, v):
            raise ValueError('Некорректный формат URL')
        return v

class IntegrationCreate(IntegrationBase):
    """Схема для создания интеграции."""
    pass

class IntegrationUpdate(BaseModel):
    """Схема для обновления интеграции."""
    name: Optional[str] = Field(None, description="Название интеграции")
    webhook_url: Optional[str] = Field(None, description="URL вебхука")
    is_active: Optional[bool] = Field(None, description="Активна ли интеграция")
    
    @validator('webhook_url')
    def validate_webhook_url(cls, v):
        if v is None:
            return v
        url_pattern = r'^https?://[\w.-]+(:\d+)?(/[\w./\-?=%&]*)?$'
        if not re.match(url_pattern, v):
            raise ValueError('Некорректный формат URL')
        return v

class IntegrationResponse(IntegrationBase):
    """Схема для ответа с интеграцией."""
    id: str = Field(..., description="ID интеграции")
    assistant_id: str = Field(..., description="ID ассистента")
    is_active: bool = Field(..., description="Активна ли интеграция")
    created_at: datetime = Field(..., description="Дата создания")
    updated_at: Optional[datetime] = Field(None, description="Дата обновления")
    
    class Config:
        orm_mode = True
