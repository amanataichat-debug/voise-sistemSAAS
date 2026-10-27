"""Тариф Старт: агент «только отвечает» (services/agent_mode.py) и режим пользователя."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, json, sys, types
from datetime import datetime, timedelta

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")

from backend.services import agent_mode
from backend.services import agent_tools
from backend.models.user import User

failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ---------- режим пользователя ----------
def make_user(plan_code=None, days=30, trial=False, admin=False, blocked=False):
    u = User()
    u.is_admin = admin
    u.agent_subscription_blocked = blocked
    u.agent_trial_started_at = datetime.utcnow() if trial else None
    u.subscription_end_date = datetime.utcnow() + timedelta(days=days)
    u.subscription_plan_rel = types.SimpleNamespace(code=plan_code) if plan_code else None
    return u

check("Profi → full", make_user("profi").agent_mode() == "full")
check("Старт → reactive", make_user("start").agent_mode() == "reactive")
check("Старт истёк → нет доступа", make_user("start", days=-1).agent_mode() is None)
check("AI Voice → нет доступа", make_user("ai_voice").agent_mode() is None)
check("Старт на пробном периоде → full", make_user("start", trial=True).agent_mode() == "full")
check("Админ → full", make_user(admin=True).agent_mode() == "full")
check("Блокировка перекрывает Старт", make_user("start", blocked=True).agent_mode() is None)
check("Старт: чат-доступ есть, автономного нет",
      make_user("start").has_agent_chat_access() and not make_user("start").has_agent_access())

# ---------- набор инструментов ----------
def T(name): return {"type": "function", "function": {"name": name}}
tools = [T(n) for n in ("create_agent_task", "trigger_immediate_call", "bulk_schedule_calls",
                        "create_document", "whatsapp_send_message", "send_voice_message",
                        "append_contact_note", "move_contact_stage", "get_period_report",
                        "search_contacts", "delete_agent_contact", "notify_owner_handoff")]
names = lambda ts: {t["function"]["name"] for t in ts}

pc = names(agent_mode.filter_tools(tools, "postcall"))
check("PostCall: нет планирования и документов",
      not pc & {"create_agent_task", "trigger_immediate_call", "bulk_schedule_calls", "create_document"}, sorted(pc))
check("PostCall: отвечать и вести CRM можно",
      {"whatsapp_send_message", "send_voice_message", "append_contact_note", "move_contact_stage"} <= pc)
oc = names(agent_mode.filter_tools(tools, "owner_chat"))
check("Чат владельца: только чтение",
      oc == {"get_period_report", "search_contacts", "notify_owner_handoff"}, sorted(oc))

# ---------- проверка при выполнении ----------
reactive_user = make_user("start")
full_user = make_user("profi")
recent = {"c-recent"}
agent_mode.client_contacted_recently = lambda db, cid: str(cid) in recent

ctx_pc = {"surface": "postcall", "current_contact_id": "c-recent", "agent_config_id": "a1"}
check("Profi: проверка ничего не запрещает",
      agent_mode.check_tool(full_user, "trigger_immediate_call", {}, {}, None) is None)
check("Старт: звонок агентом запрещён",
      agent_mode.check_tool(reactive_user, "trigger_immediate_call", {}, ctx_pc, None) == agent_mode.REFUSAL_INITIATIVE)
check("Старт: ответ клиенту, который написал, разрешён",
      agent_mode.check_tool(reactive_user, "whatsapp_send_message", {"agent_contact_id": "c-recent"}, ctx_pc, None) is None)
check("Старт: ответ без id — текущему клиенту, разрешён",
      agent_mode.check_tool(reactive_user, "whatsapp_send_message", {"text": "ok"}, ctx_pc, None) is None)
check("Старт: написать другому клиенту из PostCall нельзя",
      agent_mode.check_tool(reactive_user, "telegram_send_message", {"agent_contact_id": "c-other"}, ctx_pc, None)
      == agent_mode.REFUSAL_FIRST_MESSAGE)
recent.add("c-old")
check("Старт: даже недавнему клиенту не из своего диалога — нельзя",
      agent_mode.check_tool(reactive_user, "whatsapp_send_file", {"agent_contact_id": "c-old"}, ctx_pc, None)
      == agent_mode.REFUSAL_FIRST_MESSAGE)
ctx_silent = {"surface": "postcall", "current_contact_id": "c-silent"}
check("Старт: клиент молчит 24 ч — первым не пишем",
      agent_mode.check_tool(reactive_user, "whatsapp_send_message", {}, ctx_silent, None) == agent_mode.REFUSAL_FIRST_MESSAGE)
check("Старт: чат владельца — команда отправки запрещена",
      agent_mode.check_tool(reactive_user, "whatsapp_send_message", {"agent_contact_id": "c-recent"},
                            {"surface": "owner_chat"}, None) == agent_mode.REFUSAL_OWNER_CHAT)
check("Старт: чат владельца — отчёт разрешён",
      agent_mode.check_tool(reactive_user, "get_period_report", {}, {"surface": "owner_chat"}, None) is None)

# execute_tool отдаёт отказ модели и не трогает БД
res = asyncio.run(agent_tools.execute_tool("bulk_schedule_calls", {}, {"user": reactive_user, "surface": "postcall"}, None))
check("execute_tool: отказ в JSON для модели", json.loads(res).get("error") == agent_mode.REFUSAL_INITIATIVE, res)

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
