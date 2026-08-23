"""
AgentConnector — подключённый внешний коннектор агента (через Composio).

Один ряд = один toolkit (google_calendar / gmail), подключённый к конкретному
агенту. Сырые OAuth-токены здесь НЕ хранятся — их держит Composio. Мы храним
только состояние подключения для UI и идентификаторы Composio.

composio_user_id — идентификатор «пользователя» в Composio. Per-agent identity
(вариант A): f"agent_{agent_config_id}", т.е. у каждого агента своё изолированное
подключение. И оркестратор, и голосовой агент резолвят этот id из своего контекста
(по агенту-владельцу).

state_token — одноразовый секрет, который мы добавляем в callback_url при старте
OAuth-флоу. Composio сохраняет наши query-параметры и возвращает их в callback,
поэтому по нему мы находим нужный ряд без авторизации (callback открывает браузер
пользователя, а не наш фронт).
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


# Поддерживаемые toolkit'ы (ключ → человекочитаемое имя). Slug Composio
# резолвится в composio_service.TOOLKIT_SLUGS.
CONNECTOR_TOOLKITS = ("google_calendar", "gmail", "instagram")


class AgentConnector(Base):
    __tablename__ = "agent_connectors"
    __table_args__ = (
        UniqueConstraint("agent_config_id", "toolkit", name="uq_agent_connector_toolkit"),
        Index("ix_agent_connectors_state_token", "state_token"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # google_calendar | gmail
    toolkit = Column(String(32), nullable=False)

    # pending (флоу начат, ждём callback) | connected | error
    status = Column(String(20), default="pending", nullable=False)

    # Идентификаторы Composio
    composio_user_id = Column(String(128), nullable=True)
    connected_account_id = Column(String(128), nullable=True)

    # Одноразовый токен для сопоставления callback'а с этим рядом
    state_token = Column(String(64), nullable=True)

    # Email/аккаунт, который пользователь подключил (для отображения в UI)
    connected_email = Column(String(255), nullable=True)

    # Instagram: числовой IG User ID подключённого Business-аккаунта — нужен
    # поллеру, чтобы отличать свои сообщения от входящих (from.id == свой id).
    # Заполняется лениво поллером через INSTAGRAM_GET_USER_INFO('me').
    external_account_id = Column(String(64), nullable=True)

    # Instagram: метка последнего опроса входящих DM. Одновременно служит
    # атомарным claim'ом в мультиворкере (см. backend/core/instagram_poller.py,
    # та же идея, что last_poll_at у agent_telegram_accounts).
    last_poll_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agent_config = relationship("AgentConfig", foreign_keys=[agent_config_id])

    def is_connected(self) -> bool:
        return self.status == "connected" and bool(self.connected_account_id)
