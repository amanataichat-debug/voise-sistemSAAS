"""
Антиспам в переписке и на звонке (ветка 0811-voice-jarvis, 09.11.2026).

Владелец 09.11.2026: «пишут не клиенты, а любопытные: какой API, какая модель,
какая база данных, какая у вас архитектура, как вы это сделали. И те, кто
троллит: „наберите мне 500 тысяч звонков в день“. На таких бот должен один раз
попрощаться, дать контакт администратора и выключиться в этом чате навсегда».

Решает МОДЕЛЬ (инструмент block_spam_contact в разборе входящего), а не список
слов: формулировки у таких людей каждый раз разные, а код по словам отключал бы
и живого клиента, который попутно спросил «а на чём это работает». Цена решения —
один разбор кредитов на спамера; дальше он уже не доходит до модели, потому что
пауза переписки проверяется до её запуска (handle_inbound_* в agent_orchestrator).

Что делает блокировка:
  1. ОДНО последнее сообщение клиенту — текст собирает этот модуль, не модель:
     контакт администратора, без объяснений про технологию и без спора;
  2. пауза переписки навсегда (`agent_pause`, причина "spam") — сразу во всех
     мессенджерах, потому что пауза живёт на карточке клиента;
  3. карточка в «Не звонить» (терминальная стадия: ни звонков, ни рассылок);
  4. строка в заметки карточки — владельцу видно, за что и когда.

Снять может только владелец вручную (карточка клиента, инбокс). На звонке то же
самое делает разбор звонка (PostCall) после того, как голосовой агент закончил
разговор через hangup_call(reason="spam_caller").
"""

from datetime import datetime
from typing import Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

# Причина блокировки → как она пишется в заметку карточки владельцу.
REASONS = {
    "tech_probe": "выспрашивал устройство системы (API, модель, база, архитектура)",
    "troll": "троллинг, несерьёзные объёмы",
    "spam_ad": "реклама или рассылка в наш адрес",
    "abuse": "оскорбления",
}
DEFAULT_REASON = "tech_probe"

PAUSE_REASON = "spam"


def reason_text(reason: str) -> str:
    return REASONS.get(reason or "", REASONS[DEFAULT_REASON])


def admin_contact(agent, explicit: Optional[str] = None) -> str:
    """
    Контакт администратора для последнего сообщения: то, что модель нашла в базе
    знаний, иначе контакты из «Фирменного стиля» агента (brand_contacts).
    Пустая строка — контакта нет, тогда сообщение идёт без него.
    """
    text = (explicit or "").strip()
    if not text:
        text = (getattr(agent, "brand_contacts", None) or "").strip()
    # В brand_contacts может быть несколько строк (адрес, сайт, телефон) — берём
    # первую непустую: в прощальном сообщении нужен один контакт, не визитка.
    if "\n" in text:
        text = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
    return text[:120]


def farewell_text(agent, explicit_contact: Optional[str] = None) -> str:
    """
    Последнее сообщение спамеру. Короткое, вежливое, без объяснений, почему не
    подойдёт, и без единого слова о технологиях — иначе разговор продолжится.
    """
    contact = admin_contact(agent, explicit_contact)
    if contact:
        return ("Наша система вам не подойдёт.\n"
                f"По остальным вопросам напишите администратору: {contact}\n"
                "Всего доброго.")
    return "Наша система вам не подойдёт. Всего доброго."


def do_not_call_stage_key(agent) -> Optional[str]:
    """Ключ стадии «Не звонить» у этого агента (у кастомных воронок — любая lost)."""
    from backend.core.pipeline_stages import get_agent_stages
    stages = get_agent_stages(agent)
    keys = [s["key"] for s in stages]
    if "do_not_call" in keys:
        return "do_not_call"
    for s in stages:
        if s.get("kind") == "lost":
            return s["key"]
    return None


def note_line(reason: str, detail: str = "", now: Optional[datetime] = None) -> str:
    when = (now or datetime.utcnow()).strftime("%d.%m.%Y")
    line = f"[{when}] Бот отключён в этом чате навсегда: спам, {reason_text(reason)}"
    detail = (detail or "").strip().replace("\n", " ")
    if detail:
        line += f". Написал: «{detail[:200]}»"
    return line


def block(contact, agent, reason: str, detail: str = "") -> dict:
    """
    Пауза навсегда + «Не звонить» + заметка. Без commit (его делает вызывающий),
    без отправки сообщения — его шлёт инструмент тем каналом, где идёт переписка.
    """
    from backend.services import agent_pause

    agent_pause.set_pause(contact, -1, PAUSE_REASON)
    stage = do_not_call_stage_key(agent)
    old_stage = contact.status
    if stage:
        contact.status = stage
    if not (contact.lost_reason or "").strip():
        contact.lost_reason = f"спам: {reason_text(reason)}"
    line = note_line(reason, detail)
    contact.notes = (f"{contact.notes}\n{line}" if (contact.notes or "").strip() else line)
    # Следующего шага у такого клиента нет: он не в работе.
    contact.next_step_text = None
    contact.next_step_at = None
    logger.info(f"[SPAM-GUARD] contact {contact.id}: blocked forever ({reason}), stage {old_stage} -> {stage}")
    return {"stage": stage, "old_stage": old_stage, "note": line}
