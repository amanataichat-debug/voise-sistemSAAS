"""
Профиль бизнеса клиента: ниша → модули, каталог, воронка, бот (ветка 1811-niche-admin-speed).

Хранится в users.business_niche / business_niche_other и users.business_profile (JSONB):
    {"answers": {...ответы на 2–3 вопроса...}, "modules": {...переопределения владельца...},
     "applied_at": ISO}
Пресеты ниш — services/niche_presets.py. Здесь:
  • resolve(user)              — итоговая конфигурация для кабинета (модули, каталог, вкладки телефона);
  • setup(db, user, …)          — экран «Чем занимается ваш бизнес?»: сохранить и применить;
  • apply_to_agent(db, user, a) — настроить бота/агента: воронка, коллекции каталога, инструкции
                                  переписки и звонков, частые вопросы клиентов в «Чего не хватает».

Правило: применение НЕ перезаписывает то, что владелец уже настроил сам — воронка ставится только
поверх стандартной, коллекции — только если их нет, инструкции — только если поле пустое, вопросы —
без дублей. Повторный вызов безопасен (идемпотентен). Смена ниши в «Настройках» применяет то же
самое (force=True меняет и воронку, если в ней ещё нет клиентов на своих этапах).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.services import niche_presets as presets
from backend.services.niche_catalog import is_known_niche, niche_title

logger = logging.getLogger(__name__)

MODULE_KEYS = ("booking", "crm", "orders")

_STATIC = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.dirname(
    __import__("os").path.abspath(__file__))), "static")


def page_ready(name: str) -> bool:
    """Страница модуля уже есть в сборке (вкладку «Записи» / «Заказы» показываем только тогда)."""
    import os
    return os.path.exists(os.path.join(_STATIC, name))


def _profile(user) -> Dict[str, Any]:
    raw = getattr(user, "business_profile", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    return dict(raw or {})


def modules_of(user) -> Dict[str, bool]:
    prof = _profile(user)
    mods = presets.modules_for(getattr(user, "business_niche", None), prof.get("answers"))
    for k, v in (prof.get("modules") or {}).items():
        if k in MODULE_KEYS and isinstance(v, bool):
            mods[k] = v
    return mods


def tabs_for(mode: Optional[str], modules: Dict[str, bool], catalog_title: str) -> List[Dict[str, str]]:
    """
    Нижние вкладки телефона (как в WhatsApp). Profi (full): Чаты · Звонки · Агент · Клиенты · Ещё
    (решение владельца 18.11.2026); у ниш с записью «Звонки» → «Записи». Старт и пробный:
    Главная · Чаты · (Записи | Заказы | каталог ниши) · Клиенты · Ещё.
    """
    chats = {"id": "inbox", "href": "/static/inbox.html", "icon": "inbox", "label": "Чаты", "ky": "Чаттар"}
    clients_href = "/static/start-crm.html"   # CRM клиентов агента — одна для Старта и Profi
    clients = {"id": "clients", "href": clients_href, "icon": "users", "label": "Клиенты", "ky": "Кардарлар"}
    more = {"id": "more", "icon": "menu", "label": "Ещё", "ky": "Дагы"}
    booking = {"id": "booking", "href": "/static/booking.html", "icon": "calendar", "label": "Записи", "ky": "Жазуулар"}
    orders = {"id": "orders", "href": "/static/orders.html", "icon": "shopping-cart", "label": "Заказы", "ky": "Заказдар"}
    catalog = {"id": "catalog", "href": "/static/start-bot.html#catalog" if mode != "full" else "/static/agent-knowledge.html#catalog",
               "icon": "package", "label": catalog_title or "Каталог", "ky": catalog_title or "Каталог"}
    if not page_ready("booking.html"):
        modules = {**modules, "booking": False}
    if not page_ready("orders.html"):
        modules = {**modules, "orders": False}
    if mode == "full":
        second = booking if modules.get("booking") else {"id": "calls", "href": "/static/conversations.html", "icon": "phone",
                                                          "label": "Звонки", "ky": "Чалуулар"}
        agent = {"id": "agent", "href": "/static/agent.html", "icon": "headset", "label": "Агент", "ky": "Агент"}
        return [chats, second, agent, clients, more]
    home = {"id": "home", "href": "/static/dashboard.html", "icon": "house", "label": "Главная", "ky": "Башкы"}
    third = booking if modules.get("booking") else (orders if modules.get("orders") else catalog)
    return [home, chats, third, clients, more]


def resolve(user) -> Dict[str, Any]:
    """Конфигурация кабинета под бизнес клиента (отдаётся в /api/users/me → business)."""
    niche = getattr(user, "business_niche", None)
    p = presets.preset(niche)
    prof = _profile(user)
    mods = modules_of(user)
    mode = None
    try:
        mode = user.agent_mode()
    except Exception:
        mode = None
    cat = p["catalog"]
    return {
        "niche": niche,
        "niche_title": niche_title(niche, getattr(user, "business_niche_other", None)),
        "group": p["group"] if niche else None,
        "icon": p["icon"] if niche else None,
        "needs_setup": not niche and not getattr(user, "onboarded_at", None),
        "answers": prof.get("answers") or {},
        "modules": mods,
        "catalog": {k: cat.get(k) for k in ("kind", "title", "one", "many", "stock", "duration", "attr_keys",
                                              "size_presets", "colors", "variants_label")},
        "collections_hint": p["collections"],
        "tabs": tabs_for(mode, mods, cat.get("title")),
    }


# ───────────────────────────── тексты для бота (слой ниши) ─────────────────────────────

def chat_instructions(niche_key: str, modules: Dict[str, bool], answers: Dict[str, Any]) -> str:
    """Инструкция переписки под нишу — пишется в additional_instructions, только если там пусто."""
    p = presets.preset(niche_key)
    lines = [f"Сфера бизнеса: {p['title']}. Главная цель в переписке — {p['goal']}.", "",
             "Что выяснить у клиента — по одному вопросу за раз и только то, чего ещё не знаешь:"]
    lines += [f"• {x}" for x in p["ask"]]
    lines += ["", "Как вести разговор:",
              "• Сначала ответь на вопрос клиента по сути, потом задай один вопрос.",
              f"• Цены, {p['catalog']['many']}, наличие и условия — только из каталога и базы знаний. "
              "Если ответа нет — честно скажи, что уточнишь, и передай вопрос владельцу."]
    if modules.get("booking"):
        lines.append("• Когда клиент готов записаться — предложи 2–3 ближайших свободных времени и оформи запись. "
                     "Если записать сам не можешь — передай владельцу заявку с услугой и временем, которое хочет клиент.")
        if str(answers.get("reminders", "yes")) == "yes":
            lines.append("• После записи коротко подтверди дату, время и адрес — напоминание клиенту придёт само.")
    if modules.get("orders"):
        lines.append("• Когда клиент выбрал — повтори заказ одной строкой (что, вариант, цена, доставка или самовывоз) "
                     "и оформи заказ. Не предлагай то, чего нет в наличии.")
        if str(answers.get("delivery", "yes")) == "yes":
            lines.append("• Для доставки уточни адрес и удобное время.")
    if p["group"] == "deals":
        lines.append("• Когда задача понятна — предложи встречу или звонок с конкретным временем.")
    lines.append("• Договорились — подтверди одной фразой и не задавай лишних вопросов.")
    return "\n".join(lines)


def voice_instructions(niche_key: str, modules: Dict[str, bool]) -> str:
    """Инструкция звонков под нишу — пишется в voice_additional_instructions, только если там пусто."""
    p = presets.preset(niche_key)
    ask = "; ".join(p["ask"])
    text = (f"Сфера бизнеса: {p['title']}. Цель разговора — {p['goal']}. "
            f"По ходу разговора, без анкеты, выясни: {ask}. "
            "Цены и условия называй только из того, что знаешь наизусть, или найди в базе знаний.")
    if modules.get("booking"):
        text += " Когда клиент готов — назови 2–3 свободных времени и запиши его; договорились — подтверди время одной фразой."
    return text


# ───────────────────────────── применение к боту ─────────────────────────────

def _stages_customized(agent) -> bool:
    return bool(getattr(agent, "pipeline_stages", None))


def apply_to_agent(db: Session, user, agent, force: bool = False) -> List[str]:
    """
    Настроить бота/агента под нишу владельца. Возвращает список «что сделано» для экрана «Готово».
    Ничего, что владелец уже заполнил сам, не перезаписывает (воронку — только при force и только
    если на «чужих» этапах ещё нет клиентов).
    """
    niche = getattr(user, "business_niche", None)
    if not niche or agent is None:
        return []
    p = presets.preset(niche)
    mods = modules_of(user)
    answers = _profile(user).get("answers") or {}
    done: List[str] = []

    # 1. Воронка CRM
    from backend.core.pipeline_stages import save_agent_stages, get_agent_stage_keys
    can_replace = not _stages_customized(agent)
    if force and not can_replace:
        try:
            from backend.models.agent_contact import AgentContact
            new_keys = {s["key"] for s in p["pipeline"]}
            busy = (db.query(AgentContact.status).filter(AgentContact.agent_config_id == agent.id)
                    .distinct().all())
            can_replace = all((st or "new") in new_keys for (st,) in busy)
        except Exception:
            can_replace = False
    if can_replace and [s["key"] for s in p["pipeline"]] != get_agent_stage_keys(agent):
        if save_agent_stages(agent, p["pipeline"]):
            done.append("Воронка: " + " → ".join(s["label"] for s in p["pipeline"] if s["kind"] == "open")
                        + " → " + next(s["label"] for s in p["pipeline"] if s["kind"] == "won"))

    # 2. Коллекции каталога
    if p["collections"] and not (getattr(agent, "catalog_collections", None) or []):
        agent.catalog_collections = list(p["collections"])
        done.append(f"{p['catalog']['title']}: разделы " + ", ".join(p["collections"][:5]))

    # 3. Инструкции бота (слой ниши → инструкции владельца, только если пусто)
    if not (getattr(agent, "additional_instructions", None) or "").strip():
        agent.additional_instructions = chat_instructions(niche, mods, answers)
        done.append("Бот: что спрашивать у клиента и как вести переписку")
    # Голосовая карточка агента собирает промпт звонка из voice_additional_instructions на каждом
    # звонке (agent_voice_context.fresh_card_prompt) — достаточно заполнить поле агента.
    if not (getattr(agent, "voice_additional_instructions", None) or "").strip():
        agent.voice_additional_instructions = voice_instructions(niche, mods)
        done.append("Звонки: цель разговора и что выяснить")

    # 4. Частые вопросы клиентов ниши → «Чего не хватает» в базе знаний (без выдуманных ответов)
    added = 0
    try:
        from backend.models.agent_knowledge import AgentKnowledgeGap
        from backend.services.agent_knowledge_service import normalize
        existing = {row[0] for row in db.query(AgentKnowledgeGap.query_norm)
                    .filter(AgentKnowledgeGap.agent_config_id == agent.id).all()}
        for q in p["kb"]:
            norm = normalize(q)[:500]
            if norm in existing:
                continue
            db.add(AgentKnowledgeGap(agent_config_id=agent.id, query=q[:500], query_norm=norm, channel="niche"))
            existing.add(norm)
            added += 1
    except Exception as e:
        logger.warning(f"[BUSINESS] niche questions skipped for agent {agent.id}: {e}")
    if added:
        done.append(f"База знаний: {added} частых вопросов клиентов — заполните ответы")

    agent.niche_key = niche
    return done


def setup(db: Session, user, niche: str, niche_other: Optional[str] = None,
          answers: Optional[Dict[str, Any]] = None, phone: Optional[str] = None,
          company_name: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Экран «Чем занимается ваш бизнес?» и «Настройки → Мой бизнес»: сохранить нишу и применить."""
    if not is_known_niche(niche):
        raise ValueError("Выберите сферу бизнеса из списка")
    if phone is not None and str(phone).strip():
        from backend.utils.phone import normalize_phone_e164
        e164 = normalize_phone_e164(phone)
        if not e164:
            raise ValueError("Проверьте номер телефона: например, +996 700 123 456")
        user.phone = e164
    if company_name is not None and str(company_name).strip():
        user.company_name = " ".join(str(company_name).split())[:100]
    p = presets.preset(niche)
    valid = {q["key"]: {o[0] for o in q["options"]} for q in p["questions"]}
    clean = {k: str(v) for k, v in (answers or {}).items() if k in valid and str(v) in valid[k]}
    changed_niche = getattr(user, "business_niche", None) != niche
    user.business_niche = niche
    user.business_niche_other = (" ".join(str(niche_other or "").split())[:120] or None) if niche == "other" else None
    prof = _profile(user)
    if changed_niche:
        prof.pop("modules", None)          # переопределения прошлой ниши не тащим в новую
    prof["answers"] = {**presets.default_answers(niche), **clean}
    prof["applied_at"] = datetime.now(timezone.utc).isoformat()
    user.business_profile = prof
    if not getattr(user, "onboarded_at", None):
        user.onboarded_at = datetime.now(timezone.utc)

    done: List[str] = []
    try:
        from backend.models.agent_config import AgentConfig
        agents = db.query(AgentConfig).filter(AgentConfig.user_id == user.id).all()
        for agent in agents:
            for line in apply_to_agent(db, user, agent, force=force or changed_niche):
                if line not in done:
                    done.append(line)
    except Exception as e:
        db.rollback()
        logger.error(f"[BUSINESS] apply to agents failed for user {user.id}: {e}", exc_info=True)
        raise
    db.commit()
    mods = modules_of(user)
    summary = [f"Сфера: {niche_title(niche, user.business_niche_other)}"]
    if mods.get("booking"):
        summary.append("Онлайн-запись: включена")
    if mods.get("orders"):
        summary.append("Заказы: включены")
    summary.append("CRM-воронка: " + ("включена" if mods.get("crm") else "не нужна — клиентская база с историей"))
    summary.append(f"Каталог: «{p['catalog']['title']}» с полями под вашу сферу")
    if not done:
        done.append("Бот настроится под вашу сферу сразу после создания")
    return {"summary": summary + done, "business": resolve(user)}


def set_modules(db: Session, user, modules: Dict[str, Any]) -> Dict[str, Any]:
    """«Настройки → Мой бизнес»: включить/выключить запись, воронку, заказы вручную."""
    prof = _profile(user)
    overrides = dict(prof.get("modules") or {})
    for k, v in (modules or {}).items():
        if k in MODULE_KEYS and isinstance(v, bool):
            overrides[k] = v
    prof["modules"] = overrides
    user.business_profile = prof
    db.commit()
    return resolve(user)
