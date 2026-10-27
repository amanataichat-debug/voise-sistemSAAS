"""
Стадии воронки (pipeline) для AgentContact — единый источник правды.

Было: фиксированные 5 стадий, зашитые в коде. Теперь (CRM v2, ветка
2510-crm-v2) владелец может настроить свои стадии: сохранены в
agent_configs.pipeline_stages (JSON TEXT). Если там NULL — используется
дефолт из AGENT_CONTACT_STAGES ниже.

Этот модуль используется в API, agent_tools и оркестраторе, чтобы логика
смены стадий и её валидация была в одном месте.
"""

import json
import logging

logger = logging.getLogger(__name__)

# Дефолтный набор стадий — используется, когда у агента нет кастомных.
# Порядок элементов = порядок колонок в канбане на фронте.
# kind: "open" | "won" | "lost" — заменяет старый флаг terminal.
AGENT_CONTACT_STAGES = [
    {"key": "new",         "label": "Новый",      "color": "#6B7280", "kind": "open"},
    {"key": "active",      "label": "В работе",   "color": "#3B82F6", "kind": "open"},
    {"key": "success",     "label": "Успех",      "color": "#10B981", "kind": "won"},
    {"key": "rejected",    "label": "Отказ",      "color": "#EF4444", "kind": "lost"},
    {"key": "do_not_call", "label": "Не звонить", "color": "#1F2937", "kind": "lost"},
]

AGENT_CONTACT_STAGE_KEYS = [s["key"] for s in AGENT_CONTACT_STAGES]
_TERMINAL_KEYS = {s["key"] for s in AGENT_CONTACT_STAGES if s.get("kind") in ("won", "lost")}

DEFAULT_STAGE = "new"


# ── Настраиваемые стадии (CRM v2) ──────────────────────────────────────

def _normalize_stage_item(raw):
    """Приводит пришедший объект стадии к канонической форме, или None если битый."""
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or "").strip().lower()[:40]
    label = str(raw.get("label") or "").strip()[:60]
    color = str(raw.get("color") or "").strip()[:16]
    kind = str(raw.get("kind") or raw.get("type") or "open").strip().lower()
    if kind not in ("open", "won", "lost"):
        kind = "open"
    if not key or not label:
        return None
    # Разрешены только латиница, цифры и _
    import re as _re
    if not _re.match(r'^[a-z0-9_]+$', key):
        return None
    if color and not _re.match(r'^#[0-9a-fA-F]{3,8}$', color):
        color = ""
    return {"key": key, "label": label, "color": color or "#6B7280", "kind": kind}


def normalize_stages_list(raw_list):
    """Чистит список стадий от владельца: дубли ключей выкидывает, минимум 2 стадии."""
    if not isinstance(raw_list, list):
        return None
    cleaned = []
    seen = set()
    for item in raw_list:
        norm = _normalize_stage_item(item)
        if not norm:
            continue
        if norm["key"] in seen:
            continue
        seen.add(norm["key"])
        cleaned.append(norm)
        if len(cleaned) >= 20:  # разумный предел
            break
    if len(cleaned) < 2:
        return None
    return cleaned


def get_agent_stages(agent):
    """
    Возвращает список стадий для агента: кастомные (если сохранены и валидны)
    или дефолт AGENT_CONTACT_STAGES. Никогда не возвращает None/пустой.
    """
    raw = getattr(agent, "pipeline_stages", None) if agent is not None else None
    if raw:
        try:
            parsed = json.loads(raw)
            normalized = normalize_stages_list(parsed)
            if normalized:
                return normalized
        except Exception as e:
            logger.warning(f"[pipeline_stages] agent {getattr(agent, 'id', '?')}: битый JSON — {e}")
    return list(AGENT_CONTACT_STAGES)


def get_agent_stage_keys(agent):
    return [s["key"] for s in get_agent_stages(agent)]


def is_valid_stage_for_agent(agent, stage):
    """Валидация ключа для стадии — учитывает кастомные стадии конкретного агента."""
    if not stage:
        return False
    return stage in get_agent_stage_keys(agent)


def save_agent_stages(agent, stages_list):
    """Сохраняет кастомные стадии в agent_configs.pipeline_stages. Возвращает список или None."""
    normalized = normalize_stages_list(stages_list)
    if normalized is None:
        return None
    agent.pipeline_stages = json.dumps(normalized, ensure_ascii=False)
    return normalized

# Детерминированный маппинг решения PostCall-оркестратора → стадия воронки.
_DECISION_TO_STAGE = {
    "SUCCESS": "success",
    "REJECTED": "rejected",
    "DO_NOT_CALL": "do_not_call",
    "FOLLOWUP": "active",
}


def is_valid_stage(stage) -> bool:
    """
    True, если stage — один из ДЕФОЛТНЫХ ключей воронки.
    Для проверки с учётом кастомных стадий агента используйте
    is_valid_stage_for_agent(agent, stage).
    """
    return stage in AGENT_CONTACT_STAGE_KEYS


def stage_from_decision(decision, current_stage=None):
    """
    Маппинг post_call_decision → целевая стадия воронки (fallback, когда
    оркестратор не вызвал move_contact_stage явно).

    Смена стадии НЕ обязательна. Возвращает None, если звонок не даёт основания
    менять стадию — тогда контакт остаётся в текущей стадии:
      - NO_ANSWER (не дозвонились) → None (оставляем как был);
      - клиент ещё думает / ничего не изменилось → None;
      - контакт уже в терминальной стадии (success/rejected/do_not_call) — не
        двигаем его автоматически, только вручную или явным tool агента.

    Возвращает ключ стадии только при определённом исходе:
      SUCCESS → success, REJECTED → rejected, DO_NOT_CALL → do_not_call,
      FOLLOWUP → active.
    """
    if current_stage in _TERMINAL_KEYS:
        return None
    return _DECISION_TO_STAGE.get(decision)
