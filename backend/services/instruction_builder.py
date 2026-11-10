"""
Генератор и редактор инструкций агента (10.11.2026).

Владелец: «клиент пишет своими словами, а система должна написать готовый промпт под нашу платформу, под его
систему продаж; клиент проверяет и жмёт «Готово». И отдельная кнопка «Добавить инструкцию»: клиент пишет, что
убрать или добавить, — правка уходит в основной промпт, чтобы он не копался в целом тексте».

Две операции на двух полях агента:
  * kind="chat"  — главная инструкция (переписка в WhatsApp / Instagram / Telegram, разбор звонков, воронка),
                   поле agent_configs.additional_instructions;
  * kind="voice" — инструкция для звонков, поле agent_configs.voice_additional_instructions.

generate(): описание владельца + профиль агента (о бизнесе, этапы воронки, что лежит в базе знаний, может ли
агент звонить сам) → готовая инструкция + список фактов «добавьте в базу знаний».
refine(): текущая инструкция + правка владельца → новая инструкция целиком + «что изменено».

Ничего не сохраняет: владелец видит результат и сам жмёт «Готово» (обычный PUT /api/agent/).
Мета-промпты знают, что платформа добавляет сама (манеру, стиль, знания, отправку материалов, дожим), и не
повторяют этого: инструкция владельца — про ЕГО бизнес и ЕГО воронку (CLAUDE.md, «два слоя правил»).
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.core.logging import get_logger

logger = get_logger(__name__)

# Пишет промпты: важнее качество текста, чем скорость. Одна генерация ≈ 3 тыс. токенов на вход и 2,5 на выход.
MODEL = os.getenv("INSTRUCTION_BUILDER_MODEL", "anthropic/claude-sonnet-4.6")
MAX_BRIEF_CHARS = 8000
MAX_CURRENT_CHARS = 24000
MAX_CHANGE_CHARS = 3000
KINDS = ("chat", "voice")
FIELD = {"chat": "additional_instructions", "voice": "voice_additional_instructions"}

KB_MARK = "=== ДОБАВЬТЕ В БАЗУ ЗНАНИЙ ==="
CHANGES_MARK = "=== ЧТО ИЗМЕНЕНО ==="


class BuilderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ─────────────────────────────── мета-промпты ───────────────────────────────

_COMMON_TEXT_RULES = (
    "ПРАВИЛА ТЕКСТА:\n"
    "- Пиши во втором лице («Ты — …», «Спроси…»), простым понятным русским языком, как объясняешь новому сильному "
    "сотруднику. Без канцелярита.\n"
    "- Цены, адреса, график, сроки, условия и другие факты НЕ вписывай в инструкцию: они в базе знаний, агент "
    "отвечает ими сам. Пиши «цену называй из базы знаний». Если владелец в описании дал факты, после инструкции "
    f"выведи строку «{KB_MARK}» и под ней эти факты по одному в строке в виде «Вопрос клиента? — Ответ.»\n"
    "- Не выдумывай того, чего владелец не говорил: ни скидок, ни гарантий, ни сроков, ни акций. Где данных нет — "
    "пиши общо, без конкретики.\n"
    "- Не упоминай платформу Voksy AI, если это не компания владельца. Не называй модели ИИ.\n"
    "- Без markdown: никаких #, **, таблиц и списков с тире. Названия разделов — отдельной строкой обычным "
    "текстом, пункты — с новой строки цифрой «1.». Примеры реплик — в кавычках «…».\n"
    "- Верни ТОЛЬКО текст инструкции (и при необходимости блок для базы знаний). Без вступлений, пояснений и "
    "комментариев к своей работе.\n"
)

VOICE_SYSTEM = (
    "Ты — сильный продажник и автор промптов для голосовых ИИ-агентов. Напиши ИНСТРУКЦИЮ ДЛЯ ЗВОНКОВ голосового "
    "агента компании владельца. Голосовая модель читает её на каждом телефонном звонке.\n\n"
    "ЧТО ПЛАТФОРМА УЖЕ ДЕЛАЕТ САМА — в инструкции это НЕ повторяй:\n"
    "- манера: одна-две фразы за ход, без «одну секунду», не повторять за клиентом, перебили — замолчать, "
    "живой русский и разговорный бишкекский кыргызский, произношение, один голос;\n"
    "- знания: цены, услуги, адреса и условия из базы знаний уже лежат в промпте звонка (раздел «ЧТО ТЫ ЗНАЕШЬ "
    "НАИЗУСТЬ»), остальное агент ищет функцией сам;\n"
    "- материалы (прайс, КП, фото, ссылки) система отправляет в WhatsApp или Telegram сразу после звонка, агенту "
    "достаточно пообещать;\n"
    "- после звонка итог пишется в CRM, задачи ставятся сами; спамеров и любопытных агент отсекает сам.\n\n"
    "ЧТО ДОЛЖНО БЫТЬ В ИНСТРУКЦИИ, по порядку:\n"
    "1. Кто агент: имя, компания, роль продавца-консультанта и цель звонка одной фразой.\n"
    "2. Звонок по шагам именно для ЭТОГО бизнеса: первая фраза входящего и исходящего звонка; один главный вопрос "
    "о задаче клиента и одно-два уточнения по ходу (не анкета); как показать пользу на деталях клиента короткой "
    "живой сценой; цена — сразу цифрой из базы знаний; что у этого бизнеса значит «оформить» (запись на время, "
    "заказ, выезд, ссылка на оплату — по словам владельца); шаг со временем; прощание.\n"
    "3. Сомнения: четыре–шесть типичных для этой ниши возражений и короткий спокойный ответ на каждое.\n"
    "4. Правила: чего нельзя обещать и говорить (из слов владельца); когда передать человеку и что сказать клиенту.\n"
    "5. Кыргызча: четыре–шесть коротких реплик-образцов под этот бизнес на разговорном бишкекском кыргызском "
    "(кыргызская грамматика, деловые слова по-русски: заказ, заявка, доставка, скидка, запись).\n\n"
    "Объём 2500–5000 символов.\n\n" + _COMMON_TEXT_RULES
)

CHAT_SYSTEM = (
    "Ты — сильный продажник и автор промптов для ИИ-агентов по продажам. Напиши ГЛАВНУЮ ИНСТРУКЦИЮ агента компании "
    "владельца. По ней агент отвечает клиентам в WhatsApp, Instagram и Telegram, разбирает звонки, ведёт карточки "
    "клиентов по этапам воронки, ставит задачи и зовёт владельца.\n\n"
    "ЧТО ПЛАТФОРМА УЖЕ ДЕЛАЕТ САМА — в инструкции это НЕ повторяй:\n"
    "- стиль: одно сообщение в 1–3 строки, один вопрос, без кавычек, тире, списков и смайлов, не хвалить и не "
    "пересказывать клиента, цена — цифрой из базы знаний;\n"
    "- знания: вопросы-ответы, каталог с фото и файлы из базы знаний агент видит и ищет сам;\n"
    "- замолчавшему клиенту — одно напоминание назавтра; после отправки КП — дожим в три касания по дням и перевод "
    "в отказ, если молчит;\n"
    "- пауза агента, когда владелец пишет клиенту сам; отсечение спама.\n\n"
    "ЧТО ДОЛЖНО БЫТЬ В ИНСТРУКЦИИ, разделами:\n"
    "1. Кто агент и что продаёт: имя, компания, товары или услуги одной-двумя фразами, главная цель (запись, заказ, "
    "встреча, оплата).\n"
    "2. Первое сообщение новому клиенту: приветствие одной строкой и ОДИН лёгкий вопрос, который сразу двигает к "
    "продаже в этом бизнесе. Дай готовый пример.\n"
    "3. Воронка по этапам владельца — используй ТОЧНЫЕ названия этапов из профиля: что агент делает на каждом этапе, "
    "когда переводит клиента дальше (move_contact_stage), что у этого бизнеса значит успех и отказ.\n"
    "4. Как довести до покупки: какие одно-два уточнения нужны (например модель авто, дата, размер, бюджет); как "
    "подобрать вариант из каталога (find_products) и отправить фото или прайс одним сообщением; что спросить для "
    "оформления (имя, телефон, адрес, время).\n"
    "5. Звонок: {call_rule}\n"
    "6. Возражения: четыре–шесть типичных для ниши и короткий ответ на каждое.\n"
    "7. Когда звать владельца (notify_owner_handoff): готов купить, просит человека, жалоба, крупный или нестандартный "
    "заказ — и что написать клиенту, с конкретным временем.\n"
    "8. Чего нельзя (из слов владельца).\n\n"
    "Объём 3000–7000 символов.\n\n" + _COMMON_TEXT_RULES
)

CALL_RULE_FULL = ("агент может сам позвонить клиенту (исходящие только на номера О!, перед этим check_phone_operator): "
                  "когда это уместно предложить и как поставить звонок на названное клиентом время (create_agent_task).")
CALL_RULE_REACTIVE = ("агент сам не звонит: когда клиенту удобнее поговорить голосом, дай номер компании из базы знаний "
                      "или передай владельцу, чтобы он перезвонил.")

REFINE_SYSTEM = (
    "Ты — аккуратный редактор инструкции ИИ-агента. Владелец хочет поправить инструкцию. Внеси ЕГО правку:\n"
    "- меняй только то, что касается правки; остальной текст сохрани дословно и в том же порядке;\n"
    "- правка противоречит какому-то пункту — замени этот пункт, не оставляй противоречия;\n"
    "- новое правило поставь в подходящий раздел, а не просто в конец;\n"
    "- ничего не добавляй от себя, не сокращай и не переписывай остальное;\n"
    "- если правка — это факт (цена, адрес, график, срок), в инструкцию его не вписывай: оставь текст как был и в "
    "отчёте напиши «Это факт — добавьте его в базу знаний»;\n"
    "- без markdown (#, **, таблиц).\n"
    f"Ответ: полный новый текст инструкции, затем отдельной строкой «{CHANGES_MARK}» и одна–три короткие строки, "
    "что именно изменено."
)


# ─────────────────────────────── профиль агента ───────────────────────────────

def _clip(text: Optional[str], limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def agent_profile(db, agent, user=None) -> str:
    """Что генератор должен знать об агенте: бизнес, этапы воронки, что лежит в базе знаний, может ли звонить."""
    lines: List[str] = [f"Агент: «{getattr(agent, 'name', '') or 'без имени'}»."]
    company = getattr(agent, "brand_company_name", None)
    if company:
        lines.append(f"Компания: {company}.")
    for label, field in (("Кто мы", "doc_who_am_i"), ("Что предлагаем", "doc_what_we_offer"),
                         ("Кому звоним / кто клиенты", "doc_who_we_call"), ("Как говорим", "doc_how_we_talk"),
                         ("Правила и цели", "doc_rules_and_goals")):
        value = _clip(getattr(agent, field, None), 700)
        if value:
            lines.append(f"{label}: {value}")

    try:
        from backend.core.pipeline_stages import get_agent_stages
        kinds = {"open": "в работе", "won": "успех", "lost": "потеря"}
        stages = ", ".join(f"{s.get('label')} ({kinds.get(s.get('kind'), s.get('kind'))})"
                           for s in get_agent_stages(agent))
        lines.append(f"Этапы воронки (точные названия): {stages}.")
    except Exception as e:
        logger.warning(f"[INSTR-BUILDER] этапы не прочитаны: {e}")

    if db is not None and getattr(agent, "id", None) is not None:
        try:
            from backend.models.agent_knowledge import AgentKnowledgeEntry as E
            rows = (db.query(E.kind, E.title).filter(E.agent_config_id == agent.id, E.is_active.is_(True))
                    .order_by(E.position.asc()).limit(400).all())
            qa = [t for k, t in rows if k == "qa" and t]
            products = [t for k, t in rows if k == "product" and t]
            sections = [t for k, t in rows if k == "section" and t]
            docs = [t for k, t in rows if k == "document" and t]
            if rows:
                lines.append(f"В базе знаний: вопросов-ответов {len(qa)}, разделов о компании {len(sections)}, "
                             f"позиций каталога {len(products)}, файлов-источников {len(docs)}.")
            if qa:
                lines.append("Вопросы, на которые ответ уже есть в базе: " + "; ".join(_clip(t, 80) for t in qa[:25]))
            if products:
                lines.append("Примеры позиций каталога: " + "; ".join(_clip(t, 60) for t in products[:12]))
        except Exception as e:
            db.rollback()
            logger.warning(f"[INSTR-BUILDER] база знаний не прочитана: {e}")
        try:
            from backend.models.agent_file import AgentFile
            files = (db.query(AgentFile.filename).filter(AgentFile.agent_config_id == agent.id,
                                                         AgentFile.source == "library").limit(15).all())
            names = [f[0] for f in files if f[0]]
            if names:
                lines.append("Файлы для отправки клиентам: " + "; ".join(_clip(n, 60) for n in names))
        except Exception as e:
            db.rollback()
            logger.info(f"[INSTR-BUILDER] файлы не прочитаны: {e}")

    lines.append("Агент может сам звонить клиентам: " + ("да" if can_call_first(user) else "нет") + ".")
    return "\n".join(lines)


def can_call_first(user) -> bool:
    try:
        return user is not None and user.agent_mode() == "full"
    except Exception:
        return False


# ─────────────────────────────── сборка запросов ───────────────────────────────

def generate_messages(kind: str, brief: str, profile: str, current: str = "", call_first: bool = False
                      ) -> List[Dict[str, str]]:
    if kind == "voice":
        system = VOICE_SYSTEM
    else:
        system = CHAT_SYSTEM.replace("{call_rule}", CALL_RULE_FULL if call_first else CALL_RULE_REACTIVE)
    user = (f"ПРОФИЛЬ АГЕНТА (из кабинета):\n{profile}\n\n"
            f"ОПИСАНИЕ ВЛАДЕЛЬЦА СВОИМИ СЛОВАМИ:\n{brief.strip()}")
    if current.strip():
        user += ("\n\nТЕКУЩАЯ ИНСТРУКЦИЯ (возьми из неё то, что владелец явно хотел сохранить, — его правила и "
                 f"запреты; остальное перепиши по схеме):\n{current.strip()[:MAX_CURRENT_CHARS]}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def refine_messages(kind: str, current: str, change: str) -> List[Dict[str, str]]:
    what = "инструкция для звонков голосового агента" if kind == "voice" else "главная инструкция агента (переписка и воронка)"
    user = (f"Это {what}.\n\nТЕКУЩАЯ ИНСТРУКЦИЯ:\n{current.strip()[:MAX_CURRENT_CHARS]}\n\n"
            f"ПРАВКА ВЛАДЕЛЬЦА:\n{change.strip()}")
    return [{"role": "system", "content": REFINE_SYSTEM}, {"role": "user", "content": user}]


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _strip_markdown(text: str) -> str:
    """Модели всё равно ставят ## и **: в инструкции это шум для голосовой модели."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def split_generated(raw: str) -> Tuple[str, List[str]]:
    """Текст инструкции и список фактов «добавьте в базу знаний»."""
    raw = _strip_fences(raw)
    if KB_MARK in raw:
        text, tail = raw.split(KB_MARK, 1)
        facts = [re.sub(r"^\s*(?:[-•*]|\d+[.)])\s*", "", l).strip() for l in tail.splitlines()]
        return _strip_markdown(text), [f for f in facts if f][:30]
    return _strip_markdown(raw), []


def split_refined(raw: str) -> Tuple[str, List[str]]:
    raw = _strip_fences(raw)
    if CHANGES_MARK in raw:
        text, tail = raw.split(CHANGES_MARK, 1)
        notes = [re.sub(r"^\s*(?:[-•*]|\d+[.)])\s*", "", l).strip() for l in tail.splitlines()]
        return _strip_markdown(text), [n for n in notes if n][:5]
    return _strip_markdown(raw), []


# ─────────────────────────────── вызов модели ───────────────────────────────

async def _complete(db, user, messages: List[Dict[str, str]], *, temperature: float, note: str) -> str:
    from backend.models.user import User
    from backend.services.credit_service import CreditService
    from backend.services.openrouter_client import get_openrouter_client

    fresh = db.query(User).filter(User.id == user.id).first()
    if fresh is None or ((fresh.credits_balance or 0) <= 0 and not getattr(fresh, "is_admin", False)):
        raise BuilderError("insufficient_credits", "Не хватает кредитов агента, чтобы написать инструкцию")
    try:
        resp = await get_openrouter_client().chat_completion(
            MODEL, messages, temperature=temperature, max_tokens=6000, timeout=120.0, tools=None, tool_choice=None,
        )
    except Exception as e:
        logger.warning(f"[INSTR-BUILDER] {note} failed: {e}")
        raise BuilderError("builder_failed", "Не получилось написать инструкцию — попробуйте ещё раз через минуту")
    out = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage = resp.get("usage") or {}
    try:
        CreditService.charge(db, user.id, MODEL, int(usage.get("prompt_tokens") or 0),
                             int(usage.get("completion_tokens") or 0), ref_type="instruction_builder", notes=note)
    except Exception as e:
        db.rollback()
        logger.warning(f"[INSTR-BUILDER] charge failed: {e}")
    if not out.strip():
        raise BuilderError("builder_empty", "Модель вернула пустой текст — попробуйте ещё раз")
    return out


async def generate(db, user, agent, *, kind: str, brief: str) -> Dict[str, Any]:
    if kind not in KINDS:
        raise BuilderError("invalid_kind", "Неизвестный вид инструкции")
    brief = (brief or "").strip()
    if len(brief) < 20:
        raise BuilderError("brief_too_short", "Опишите подробнее: чем занимаетесь, что продаёте, как обычно продаёте")
    brief = brief[:MAX_BRIEF_CHARS]
    current = (getattr(agent, FIELD[kind], None) or "").strip()
    messages = generate_messages(kind, brief, agent_profile(db, agent, user), current=current,
                                 call_first=can_call_first(user))
    raw = await _complete(db, user, messages, temperature=0.4, note=f"generate_{kind}")
    text, facts = split_generated(raw)
    if len(text) < 300:
        raise BuilderError("builder_empty", "Инструкция получилась слишком короткой — добавьте подробностей и повторите")
    return {"kind": kind, "text": text, "kb_facts": facts, "chars": len(text), "model": MODEL}


async def refine(db, user, agent, *, kind: str, change: str, current: Optional[str] = None) -> Dict[str, Any]:
    if kind not in KINDS:
        raise BuilderError("invalid_kind", "Неизвестный вид инструкции")
    change = (change or "").strip()
    if len(change) < 3:
        raise BuilderError("change_empty", "Напишите, что добавить, убрать или поменять")
    change = change[:MAX_CHANGE_CHARS]
    current = (current if current is not None else getattr(agent, FIELD[kind], None) or "").strip()
    if not current:
        raise BuilderError("nothing_to_refine", "Инструкции пока нет — сначала сгенерируйте или вставьте шаблон")
    raw = await _complete(db, user, refine_messages(kind, current, change), temperature=0.2, note=f"refine_{kind}")
    text, notes = split_refined(raw)
    # Страховка от «пересказа вместо правки»: модель вернула огрызок вместо всей инструкции
    if len(text) < max(200, int(len(current) * 0.5)):
        raise BuilderError("refine_truncated", "Правка вернула неполный текст — попробуйте сформулировать короче")
    return {"kind": kind, "text": text, "changes": notes, "chars": len(text),
            "changed": text.strip() != current.strip(), "model": MODEL}
