"""
AgentKnowledgeEntry — база знаний агента v2 (ветка 1410-kb-v2).

Одна таблица на все виды знаний (kind), потому что агенту нужен один поиск, а
владельцу — одна вкладка. Разделение только в UI и в том, как знание попадает
к агенту:

  qa       — пара «вопрос клиента → ответ». САМЫЙ БЫСТРЫЙ вид: такие записи
             целиком уходят в system prompt, агент отвечает сразу, без вызова
             функции. На звонке это решающее: по логу владельца один вызов
             функции стоит ~4 с (путь до бэкенда gpt-5.6-terra), а знание в
             промпте — 0 с.
  section  — блок «О компании», «Цены», «Доставка»: заголовок + текст.
             Короткие тоже уходят в промпт, длинные — только заголовком
             (тело достаётся поиском).
  product  — позиция каталога: название, описание, цена, характеристики и
             прикреплённые фото/видео (media_file_ids → agent_files).
             Ищется по названию/цене/характеристикам прямо в Postgres —
             по числам и моделям это точнее и быстрее векторов.
  document — файл библиотеки (PDF, DOCX, XLSX): текст извлекается
             agent_media_service и векторизуется в Pinecone. Используется,
             когда в промпте и в быстром поиске ответа не нашлось.

Поиск строится слоями «от дешёвого к дорогому»: промпт (0 мс) → Postgres
(единицы мс) → Pinecone (сотни мс). Документы — единственный слой, где нужны
эмбеддинги, поэтому они и единственные, кто платит задержкой.

Таблицу создаёт стартовый create_all; колонки для уже существующих баз
добавляет app.ensure_knowledge_v2_columns().
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base

# Виды записей базы знаний.
KB_KINDS = ("qa", "section", "product", "document")

# Что по умолчанию уходит в промпт целиком (без поиска).
KB_PROMPT_KINDS = ("qa", "section")

# Сколько символов знаний максимум вшиваем в system prompt. Больше — промпт
# раздувается, модель дольше думает и каждый ответ дороже; остальное
# достаётся поиском.
KB_PROMPT_CHAR_BUDGET = 8000

# Записи «вопрос — ответ» сверх этого числа в промпт не попадают (идут в
# поиск): у владельца должен быть стимул держать быстрый раздел коротким.
KB_PROMPT_MAX_QA = 60


class AgentKnowledgeEntry(Base):
    __tablename__ = "agent_knowledge_entries"
    __table_args__ = (
        Index("ix_agent_kb_agent_kind", "agent_config_id", "kind", "is_active"),
        Index("ix_agent_kb_file", "file_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    agent_config_id = Column(UUID(as_uuid=True), ForeignKey("agent_configs.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    kind = Column(String(16), nullable=False, default="section")

    # qa: вопрос клиента | section: заголовок блока | product: название товара
    # | document: имя файла. По нему же идёт быстрый поиск, поэтому заголовок
    # должен быть человеческим («Доставка по Бишкеку»), а не «Раздел 4».
    title = Column(String(500), nullable=False, default="")
    # qa: ответ | section: текст | product: описание | document: краткое описание
    body = Column(Text, nullable=False, default="")

    # Синонимы и слова клиента, которых нет в заголовке («почём», «сколько стоит»).
    # Поиск смотрит и сюда — дёшево и заметно повышает попадание.
    keywords = Column(Text, nullable=True)

    # ── Каталог (kind=product) ──
    price = Column(Float, nullable=True)
    currency = Column(String(8), nullable=True)            # KGS по умолчанию
    attributes = Column(JSONB, nullable=True)              # {"цвет": "чёрный", "память": "128 ГБ"}
    in_stock = Column(Boolean, nullable=False, default=True)
    # Фото и видео позиции: id файлов из agent_files. Агент отправляет их
    # клиенту вместе с ответом («вот эта модель»).
    media_file_ids = Column(JSONB, nullable=True)

    # ── Документ (kind=document) ──
    file_id = Column(UUID(as_uuid=True), nullable=True)    # agent_files.id
    # Состояние векторизации: pending | ready | failed | skipped
    vector_status = Column(String(16), nullable=True)
    vector_error = Column(String(500), nullable=True)
    chunk_count = Column(Integer, nullable=False, default=0)
    char_count = Column(Integer, nullable=False, default=0)

    # ── Поведение ──
    # Вшивать запись в промпт (быстрый путь). Для qa/section включено, для
    # больших документов и каталога — нет.
    in_prompt = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    position = Column(Integer, nullable=False, default=0)

    # Сколько раз знание реально пригодилось — владелец видит, что работает,
    # а что лежит мёртвым грузом.
    hit_count = Column(Integer, nullable=False, default=0)
    last_hit_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self, with_body: bool = True) -> dict:
        out = {
            "id": str(self.id),
            "kind": self.kind,
            "title": self.title,
            "keywords": self.keywords,
            "is_active": self.is_active,
            "in_prompt": self.in_prompt,
            "position": self.position,
            "hit_count": self.hit_count,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if with_body:
            out["body"] = self.body
        if self.kind == "product":
            out.update({
                "price": self.price,
                "currency": self.currency or "KGS",
                "attributes": self.attributes or {},
                "in_stock": self.in_stock,
                "media_file_ids": self.media_file_ids or [],
            })
        if self.kind == "document":
            out.update({
                "file_id": str(self.file_id) if self.file_id else None,
                "vector_status": self.vector_status,
                "vector_error": self.vector_error,
                "chunk_count": self.chunk_count,
                "char_count": self.char_count,
            })
        return out


class AgentKnowledgeGap(Base):
    """
    Вопрос клиента, на который агент не нашёл ответа.

    Владелец видит список «что у меня спрашивают, а база не знает» и
    дополняет базу по реальным дырам, а не по догадкам. Без этого база
    отлаживается только случайными жалобами.
    """

    __tablename__ = "agent_knowledge_gaps"
    __table_args__ = (
        Index("ix_agent_kb_gaps_agent", "agent_config_id", "resolved", "last_asked_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(UUID(as_uuid=True), ForeignKey("agent_configs.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    query = Column(String(500), nullable=False)
    # Нормализованный запрос (нижний регистр, без лишних пробелов) — по нему
    # считаем повторы, чтобы один и тот же вопрос не плодил строки.
    query_norm = Column(String(500), nullable=False, index=True)
    channel = Column(String(16), nullable=True)            # call | whatsapp | telegram | instagram | chat
    ask_count = Column(Integer, nullable=False, default=1)
    resolved = Column(Boolean, nullable=False, default=False)
    first_asked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_asked_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "query": self.query,
            "channel": self.channel,
            "ask_count": self.ask_count,
            "resolved": self.resolved,
            "last_asked_at": self.last_asked_at.isoformat() if self.last_asked_at else None,
        }
