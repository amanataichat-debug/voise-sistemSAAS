"""
Склейка двух карточек одного клиента (ветка 2110-client-memory).

Клиент писал в WhatsApp с одного номера, звонит с другого — у агента две
карточки, история разделена, и следующий разговор снова «с нуля». Склейка
переносит всё из лишней карточки в основную и удаляет лишнюю:

  * все строки, ссылающиеся на agent_contacts (звонки, задачи, чаты и сообщения
    WhatsApp / Telegram / Instagram, файлы) — перепривязываются. Список таблиц
    читается из схемы базы (information_schema), так что новая таблица со
    ссылкой на клиента не потеряется при склейке, даже если про неё здесь не знают;
  * номер лишней карточки и её дополнительные номера становятся вторыми
    номерами основной;
  * память, заметки, имя/компания (если в основной пусто), счётчики — объединяются;
  * запланированный звонок остаётся один (самый поздно поставленный).

Операция необратимая: пишем в лог, что с чем склеено, и одной строкой в
заметки основной карточки (её видит владелец).
"""

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.logging import get_logger
from backend.services.agent_contact_phones import add_alt_phone, alt_phone_list, contact_phone_digits

logger = get_logger(__name__)

def _referencing_columns(db: Session) -> List[tuple]:
    """(таблица, колонка) всех внешних ключей на agent_contacts.id."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        rows = db.execute(text(
            """
            SELECT kcu.table_name, kcu.column_name
            FROM information_schema.referential_constraints rc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = rc.constraint_name
             AND kcu.constraint_schema = rc.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = rc.unique_constraint_name
             AND ccu.constraint_schema = rc.unique_constraint_schema
            WHERE ccu.table_name = 'agent_contacts' AND ccu.column_name = 'id'
            """
        )).fetchall()
        found = sorted({(r[0], r[1]) for r in rows})
        if found:
            return found
    # Не Postgres (или схема не прочиталась) — по моделям SQLAlchemy.
    from backend.models.base import Base
    out = set()
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "agent_contacts":
                out.add((table.name, fk.parent.name))
    return sorted(out)


def _merge_memory(main: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(main or {})
    for key, value in (extra or {}).items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if current in (None, "", [], {}):
            merged[key] = value
        elif key == "summary" and isinstance(value, str) and value.strip() not in str(current):
            merged[key] = f"{current} / {value}"[:1500]
        elif key == "key_facts" and isinstance(value, list) and isinstance(current, list):
            merged[key] = list(dict.fromkeys(current + value))
        elif key == "tone_history" and isinstance(value, list) and isinstance(current, list):
            merged[key] = (current + value)[-10:]
        elif key == "attempts" and isinstance(value, int) and isinstance(current, int):
            merged[key] = current + value
    return merged


def _later(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def merge_contacts(db: Session, main, extra, *, reason: str = "") -> Dict[str, Any]:
    """
    Слить карточку extra в main (одного агента). Коммитит. Возвращает сводку:
    что перенесено, какие номера добавлены, строку для владельца.
    """
    from backend.models.task import Task, TaskStatus

    if main is None or extra is None or str(main.id) == str(extra.id):
        raise ValueError("Нужны две разные карточки")
    if str(main.agent_config_id) != str(extra.agent_config_id):
        raise ValueError("Карточки принадлежат разным агентам — склеивать нельзя")

    main_id, extra_id = main.id, extra.id
    moved: Dict[str, int] = {}
    for table, column in _referencing_columns(db):
        res = db.execute(
            text(f'UPDATE "{table}" SET "{column}" = :main WHERE "{column}" = :extra'),
            {"main": main_id, "extra": extra_id},
        )
        if res.rowcount:
            moved[table] = moved.get(table, 0) + res.rowcount

    # Номера лишней карточки — вторыми номерами основной.
    added_phones = []
    for phone in [extra.phone] + alt_phone_list(extra):
        if phone and ":" not in str(phone) and add_alt_phone(main, phone):
            added_phones.append(phone)
    if not main.call_phone and extra.call_phone:
        main.call_phone = extra.call_phone

    for field in ("name", "company", "position"):
        if not (getattr(main, field) or "").strip() and (getattr(extra, field) or "").strip():
            setattr(main, field, getattr(extra, field))
    if (extra.notes or "").strip() and (extra.notes or "").strip() not in (main.notes or ""):
        main.notes = f"{main.notes}\n{extra.notes}" if main.notes else extra.notes
    main.memory = _merge_memory(main.memory or {}, extra.memory or {})
    flag_modified(main, "memory")
    main.attempts_count = (main.attempts_count or 0) + (extra.attempts_count or 0)
    main.last_called_at = _later(main.last_called_at, extra.last_called_at)
    main.ai_paused_until = _later(main.ai_paused_until, extra.ai_paused_until)
    if main.ai_paused_until is not None and main.ai_paused_until == extra.ai_paused_until:
        main.ai_pause_reason = extra.ai_pause_reason
    if (main.status or "new") == "new" and (extra.status or "new") != "new":
        main.status = extra.status  # стадия воронки — с той карточки, где разговор продвинулся

    # Одна запланированная задача на звонок у клиента: оставляем поставленную последней.
    calls = (
        db.query(Task)
        .filter(Task.agent_contact_id == main_id, Task.status == TaskStatus.SCHEDULED,
                Task.is_agent_task == True, Task.channel == "call")  # noqa: E712
        .order_by(Task.created_at.desc())
        .all()
    )
    for t in calls[1:]:
        t.status = TaskStatus.CANCELLED
        logger.info(f"[CONTACT-MERGE] при склейке отменён лишний звонок {t.id} на {t.scheduled_time}")

    extra_label = extra.name or extra.phone
    main_label = main.name or main.phone
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    owner_line = (
        f"Склеены карточки одного клиента: «{extra_label}» ({extra.phone}) → «{main_label}». "
        f"Номера в карточке: {', '.join('+' + p for p in contact_phone_digits(main))}."
        + (f" Причина: {reason}." if reason else "")
    )
    note = f"[{stamp}] {owner_line}"
    main.notes = f"{main.notes}\n{note}" if main.notes else note

    db.flush()
    db.expunge(extra)
    db.execute(text('DELETE FROM "agent_contacts" WHERE id = :extra'), {"extra": extra_id})
    db.commit()
    logger.info(
        f"[CONTACT-MERGE] карточка {extra_id} ({extra.phone}) склеена в {main_id} ({main.phone}): "
        f"перенесено {moved}, номера {added_phones}, причина: {reason or '—'}"
    )
    return {
        "ok": True,
        "main_contact_id": str(main_id),
        "removed_contact_id": str(extra_id),
        "moved": moved,
        "added_phones": ["+" + p for p in added_phones],
        "owner_line": owner_line,
    }


class _BotTarget:
    """Снимок настроек бота агента: уведомление уходит в фоновом loop'е, без сессии БД."""

    def __init__(self, agent_config):
        self.id = agent_config.id
        self.telegram_enabled = agent_config.telegram_enabled
        self.telegram_bot_token = agent_config.telegram_bot_token
        self.telegram_webhook_secret = agent_config.telegram_webhook_secret
        self.telegram_chat_ids = list(agent_config.telegram_chat_ids or [])

    def has_telegram_bot(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_webhook_secret)

    def get_telegram_chat_ids_list(self) -> list:
        return [c.get("chat_id") for c in self.telegram_chat_ids if isinstance(c, dict) and c.get("chat_id")]


def notify_owner(agent_config, line: str) -> None:
    """Одна строка владельцу в Telegram-бот агента (best-effort, в фоновом loop)."""
    if agent_config is None or not line:
        return
    try:
        target = _BotTarget(agent_config)
        if not (target.telegram_enabled and target.has_telegram_bot()):
            return
        from backend.core.background_loop import submit
        from backend.services.agent_tools import fn_send_telegram_notification
        submit(fn_send_telegram_notification({"message": "🔗 " + line}, target, None))
    except Exception as e:
        logger.warning(f"[CONTACT-MERGE] уведомление владельцу не отправлено: {e}")
