"""
Восстановить воронку CRM агента после случайного «Сбросить к стандартным».

Сброс (DELETE /api/agent/pipeline/stages) стирает agent_configs.pipeline_stages,
копии прежних этапов на сервере нет. Но клиенты остаются на своих этапах:
в agent_contacts.status лежат ключи старых этапов. Скрипт находит ключи, которых
нет в текущей воронке, и собирает воронку обратно:

  Новый → В работе → <ваши этапы> → Думает → Успех → <ваши этапы-потери> → Отказ → Не звонить

Название этапа по ключу угадывается (клиенты с дожимом после КП → «Думает»,
с причиной отказа → этап-потеря); точное название можно задать --label.

Запуск в Render Shell:
  python scripts/restore_pipeline_stages.py --email <почта владельца>            # только показать
  python scripts/restore_pipeline_stages.py --name Анри --apply                  # записать
  python scripts/restore_pipeline_stages.py --name Анри --label new_stage="Думает" --label new_stage_2="КП отправлено" --apply
  --no-thinking — не добавлять этап «Думает», если его не нашлось среди клиентов.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PALETTE = ["#8B5CF6", "#F59E0B", "#06B6D4", "#EC4899", "#84CC16", "#F97316", "#14B8A6"]
LOST_COLOR = "#B91C1C"
THINK_COLOR = "#F59E0B"

_GUESS = {
    "thinking": "Думает", "think": "Думает", "dumaet": "Думает",
    "proposal": "КП отправлено", "kp": "КП отправлено", "offer": "КП отправлено",
    "meeting": "Встреча", "demo": "Демо", "call": "Созвон", "callback": "Перезвонить",
    "negotiation": "Переговоры", "invoice": "Счёт", "payment": "Оплата", "paid": "Оплачено",
    "contacted": "Связались", "qualified": "Квалифицирован", "booked": "Записан",
    "lost": "Отказ", "no_answer": "Не отвечает", "spam": "Спам",
}


def find_agent(db, args):
    from backend.models.agent_config import AgentConfig
    from backend.models.user import User

    q = db.query(AgentConfig)
    if args.agent:
        q = q.filter(AgentConfig.id == args.agent)
    if args.email:
        q = q.join(User, User.id == AgentConfig.user_id).filter(User.email.ilike(args.email.strip()))
    if args.name:
        q = q.filter(AgentConfig.name.ilike(f"%{args.name.strip()}%"))
    rows = q.order_by(AgentConfig.created_at.asc()).all()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        print("Агент не найден. Укажите --name, --agent или --email.")
    else:
        print("Подходит несколько агентов — уточните --agent <id>:")
        for r in rows[:20]:
            print(f"  {r.id}  «{r.name}»")
    return None


def build_plan(agent, contacts, labels: dict, add_thinking: bool):
    """contacts: [(status, memory, lost_reason)]. Возвращает (stages, notes)."""
    from backend.core.pipeline_stages import AGENT_CONTACT_STAGES

    base = {s["key"]: dict(s) for s in AGENT_CONTACT_STAGES}
    counts = Counter(c[0] for c in contacts)
    nurture = defaultdict(int)
    lost = defaultdict(int)
    for status, memory, lost_reason in contacts:
        if isinstance(memory, dict) and "nurture" in memory:
            nurture[status] += 1
        if lost_reason:
            lost[status] += 1

    notes = []
    open_extra, lost_extra, thinking = [], [], None
    for key in sorted((k for k in counts if k and k not in base), key=lambda k: -counts[k]):
        n = counts[key]
        label = labels.get(key)
        is_think = nurture[key] * 2 >= n or "dum" in key or "think" in key
        is_lost = not is_think and lost[key] * 2 >= n
        if not label:
            label = "Думает" if is_think else _GUESS.get(key) or key.replace("_", " ").capitalize()
        if "дума" in label.lower():
            is_think, is_lost = True, False
        stage = {"key": key, "label": label, "kind": "lost" if is_lost else "open"}
        why = "дожим после КП" if is_think else ("причина отказа" if is_lost else "ключ")
        notes.append(f"  {key}: {n} клиентов → «{label}» ({stage['kind']}, по: {why})")
        if is_think and thinking is None:
            stage["color"] = THINK_COLOR
            thinking = stage
        elif is_lost:
            stage["color"] = LOST_COLOR
            lost_extra.append(stage)
        else:
            stage["color"] = PALETTE[len(open_extra) % len(PALETTE)]
            open_extra.append(stage)

    if thinking is None and add_thinking:
        thinking = {"key": "thinking", "label": labels.get("thinking", "Думает"), "color": THINK_COLOR, "kind": "open"}
        notes.append("  thinking: клиентов нет → «Думает» добавлен (этап дожима после КП; --no-thinking — не добавлять)")

    for key, label in labels.items():
        if key in base:
            base[key]["label"] = label

    stages = [base["new"], base["active"], *open_extra]
    if thinking:
        stages.append(thinking)
    stages += [base["success"], *lost_extra, base["rejected"], base["do_not_call"]]
    return stages, notes


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name")
    p.add_argument("--agent")
    p.add_argument("--email")
    p.add_argument("--label", action="append", default=[], help='ключ="Название", можно несколько раз')
    p.add_argument("--no-thinking", action="store_true")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    labels = {}
    for item in args.label:
        if "=" not in item:
            sys.exit(f"--label {item!r}: нужно ключ=\"Название\"")
        k, v = item.split("=", 1)
        labels[k.strip().lower()] = v.strip().strip('"')

    import backend.core.config  # noqa: F401  (порядок импорта, см. voice_selfcheck.py)
    from backend.db.session import SessionLocal
    from backend.models.agent_contact import AgentContact
    from backend.core.pipeline_stages import get_agent_stages, save_agent_stages

    db = SessionLocal()
    try:
        agent = find_agent(db, args)
        if not agent:
            return 1
        print(f"Агент «{agent.name}» ({agent.id})")
        print("Сейчас: " + " → ".join(s["label"] for s in get_agent_stages(agent))
              + ("  (свои)" if agent.pipeline_stages else "  (стандартные)"))

        rows = db.query(AgentContact.status, AgentContact.memory, AgentContact.lost_reason) \
                 .filter(AgentContact.agent_config_id == agent.id).all()
        counts = Counter(r[0] for r in rows)
        print(f"Клиентов: {len(rows)}; по этапам: " + ", ".join(f"{k}={v}" for k, v in counts.most_common()))

        add_thinking = not args.no_thinking
        stages, notes = build_plan(agent, rows, labels, add_thinking)
        print("Найдено в CRM:" if notes else "Своих ключей среди клиентов не нашлось.")
        for n in notes:
            print(n)
        print("\nБудет: " + " → ".join(f"{s['label']} [{s['key']}]" for s in stages))

        if not args.apply:
            print("\nНичего не записано. Проверьте названия (--label ключ=\"Название\") и запустите с --apply.")
            return 0
        if agent.pipeline_stages:
            backup = f"/tmp/pipeline_stages_{agent.id}.json"
            with open(backup, "w", encoding="utf-8") as f:
                f.write(agent.pipeline_stages)
            print(f"Текущие свои этапы сохранены в {backup}")
        saved = save_agent_stages(agent, stages)
        if not saved:
            print("Ошибка: этапы не прошли проверку.")
            return 1
        db.commit()
        print(f"Готово: записано {len(saved)} этапов. Обновите страницу CRM.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
