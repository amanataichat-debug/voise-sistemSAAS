"""
Стадии воронки (pipeline) для AgentContact — единый источник правды.

Набор стадий фиксированный (6 штук) и бэк-совместим со старыми значениями
status в таблице agent_contacts (new / calling / active / success / rejected /
do_not_call). Этот модуль используется в API, agent_tools и оркестраторе,
чтобы логика смены стадий и её валидация были в одном месте.
"""

# Порядок элементов = порядок колонок в канбане на фронте.
#
# Важно: "calling" больше НЕ является стадией контакта. Факт «идёт звонок»
# хранится в AgentCall.status / Task.status. Стадия контакта во время звонка
# не меняется — иначе она затирала бы реальную стадию воронки.
AGENT_CONTACT_STAGES = [
    {"key": "new",         "label": "Новый",      "color": "#6B7280", "terminal": False},
    {"key": "active",      "label": "В работе",   "color": "#3B82F6", "terminal": False},
    {"key": "success",     "label": "Успех",      "color": "#10B981", "terminal": True},
    {"key": "rejected",    "label": "Отказ",      "color": "#EF4444", "terminal": True},
    {"key": "do_not_call", "label": "Не звонить", "color": "#1F2937", "terminal": True},
]

AGENT_CONTACT_STAGE_KEYS = [s["key"] for s in AGENT_CONTACT_STAGES]
_TERMINAL_KEYS = {s["key"] for s in AGENT_CONTACT_STAGES if s["terminal"]}

DEFAULT_STAGE = "new"

# Детерминированный маппинг решения PostCall-оркестратора → стадия воронки.
_DECISION_TO_STAGE = {
    "SUCCESS": "success",
    "REJECTED": "rejected",
    "DO_NOT_CALL": "do_not_call",
    "FOLLOWUP": "active",
}


def is_valid_stage(stage) -> bool:
    """True, если stage — один из допустимых ключей воронки."""
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
