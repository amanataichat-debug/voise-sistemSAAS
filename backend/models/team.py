"""
Сотрудники кабинета (ветка 1811-niche-admin-speed).

Владелец (users) добавляет до 10 сотрудников: администратор салона, менеджер, продавец. У каждого
свой email и пароль, вход — на той же форме, что у владельца. Сотрудник работает с ДАННЫМИ
владельца (чаты, клиенты, записи, каталог), но только в тех разделах, которые владелец открыл
(permissions), и без денег, тарифов, настроек бота и ключей.

Токен сотрудника — обычный JWT кабинета с sub = id владельца и claim'ами staff (id сотрудника) и
stv (версия токена: смена пароля, выключение и удаление сотрудника делают старые токены
недействительными). Какие запросы разрешены — services/team_service.py (STAFF_RULES), проверка —
в middleware app.staff_access_guard, до эндпоинта.

Место сотрудника платное: TEAM_SEAT_SOM (150 сом) за TEAM_SEAT_DAYS (30 дней) с кошелька владельца,
продление — фоновый цикл team_service.start_team_seats_loop. Не хватило денег — сотрудник
выключается (status_reason='unpaid') до пополнения и ручного включения.

Таблицу создаёт app.ensure_team_tables() в начале старта.
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=False, unique=True)        # строчными, без пробелов
    name = Column(String(120), nullable=False)
    phone = Column(String(32), nullable=True)
    role_title = Column(String(80), nullable=True)                  # «Администратор», «Мастер», «Менеджер»
    password_hash = Column(String(255), nullable=False)
    permissions = Column(JSONB, nullable=False, default=list)       # ["inbox", "crm", "booking", ...]
    prefs = Column(JSONB, nullable=True)                            # личные настройки (инбокс: язык, звук, Enter)
    is_active = Column(Boolean, nullable=False, default=True)
    status_reason = Column(String(32), nullable=True)               # owner (выключил владелец) | unpaid
    paid_until = Column(DateTime(timezone=True), nullable=True)     # место оплачено до
    token_version = Column(Integer, nullable=False, default=1)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
