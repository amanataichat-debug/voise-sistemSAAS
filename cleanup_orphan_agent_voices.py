"""
Разовая чистка «сирот» — голосовых ассистентов, созданных мастером Voksy AI
Agent и оставшихся в БД после смены типа голоса или удаления агента (до фикса
в ветке 2707-deploy-cascadeV1-delete-agent).

Кандидат считается сиротой, если ВСЁ верно:
  • похож на ассистента агента (имя оканчивается на " Voice" ИЛИ system_prompt
    начинается с базового промпта голосового агента);
  • ни один agent_configs.*_assistant_id на него не ссылается;
  • ни один номер в voximplant_phone_numbers на него не привязан;
  • ни одна задача в tasks на него не ссылается.

По умолчанию — DRY RUN: только печатает таблицу, ничего не трогает.
Удаление: тот же скрипт с флагом --apply.

Запуск в Render Shell:
    python cleanup_orphan_agent_voices.py            # посмотреть
    python cleanup_orphan_agent_voices.py --apply    # удалить
"""

import sys

import backend.core  # noqa: F401  — прогревает граф импортов (иначе circular import)
from backend.db.session import SessionLocal
from backend.models.user import User
from backend.models.agent_config import AgentConfig
from backend.models.task import Task
from backend.models.voximplant_child import VoximplantPhoneNumber
from backend.models.assistant import AssistantConfig
from backend.models.conversation import Conversation
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.yandex_assistant import YandexAssistantConfig, YandexConversation
from backend.models.grok_assistant import GrokAssistantConfig, GrokConversation

# Первая строка VOICE_AGENT_PROMPT_BASE — её не напишет руками ни один юзер.
PROMPT_MARKER = "Ты — голосовой AI-агент."
NAME_SUFFIX = " Voice"

# (ярлык, модель ассистента, модель диалогов, колонка FK в agent_configs,
#  колонка FK в tasks, доп. фильтр по строкам таблицы)
PROVIDERS = [
    ("gemini", GeminiAssistantConfig, GeminiConversation,
     AgentConfig.gemini_assistant_id, Task.gemini_assistant_id, None),
    ("openai", AssistantConfig, Conversation,
     AgentConfig.openai_assistant_id, Task.assistant_id, None),
    ("cartesia", CartesiaAssistantConfig, None,
     AgentConfig.cartesia_assistant_id, Task.cartesia_assistant_id, None),
    ("yandex", YandexAssistantConfig, YandexConversation,
     AgentConfig.yandex_assistant_id, Task.yandex_assistant_id, None),
    # Каскад живёт в grok_assistant_configs — обычные Grok-ассистенты не трогаем.
    ("cascade", GrokAssistantConfig, GrokConversation,
     AgentConfig.cascade_assistant_id, Task.cascade_assistant_id,
     GrokAssistantConfig.assistant_type == "cascade"),
]


def main(apply: bool):
    db = SessionLocal()
    total_found = 0
    total_deleted = 0
    try:
        for label, model_cls, conv_cls, agent_fk, task_fk, extra_filter in PROVIDERS:
            # ID, на которые ссылается хоть один агент.
            linked = {
                row[0] for row in db.query(agent_fk).filter(agent_fk.isnot(None)).all()
            }

            query = db.query(model_cls)
            if extra_filter is not None:
                query = query.filter(extra_filter)

            for va in query.order_by(model_cls.created_at.asc()).all():
                name = va.name or ""
                prompt = va.system_prompt or ""
                by_name = name.endswith(NAME_SUFFIX)
                by_prompt = prompt.startswith(PROMPT_MARKER)
                if not (by_name or by_prompt):
                    continue
                if va.id in linked:
                    continue

                phones = db.query(VoximplantPhoneNumber).filter(
                    VoximplantPhoneNumber.assistant_id == va.id
                ).count()
                if phones:
                    print(f"  ~ SKIP  {label:9} {va.id}  «{name}» — привязан к {phones} номеру(ам)")
                    continue

                tasks = db.query(Task).filter(task_fk == va.id).count()
                if tasks:
                    print(f"  ~ SKIP  {label:9} {va.id}  «{name}» — на него ссылаются {tasks} задач")
                    continue

                convs = 0
                if conv_cls is not None:
                    convs = db.query(conv_cls).filter(conv_cls.assistant_id == va.id).count()

                user = db.query(User).filter(User.id == va.user_id).first()
                email = user.email if user else "?"
                markers = ",".join(
                    m for m, ok in (("name", by_name), ("prompt", by_prompt)) if ok
                )
                created = va.created_at.strftime("%Y-%m-%d") if va.created_at else "?"

                total_found += 1
                print(
                    f"  ORPHAN  {label:9} {va.id}  «{name}»  user={email}  "
                    f"created={created}  match={markers}  dialogs={convs}"
                )

                if apply:
                    if conv_cls is not None:
                        db.query(conv_cls).filter(
                            conv_cls.assistant_id == va.id
                        ).delete(synchronize_session=False)
                    db.query(model_cls).filter(
                        model_cls.id == va.id
                    ).delete(synchronize_session=False)
                    total_deleted += 1

        if apply:
            db.commit()
            print(f"\n✅ Удалено ассистентов: {total_deleted}")
        else:
            print(f"\nНайдено сирот: {total_found}. Ничего не удалено (dry run).")
            print("Удалить: python cleanup_orphan_agent_voices.py --apply")
    except Exception as e:
        db.rollback()
        print(f"\n❌ Ошибка, изменения откачены: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
