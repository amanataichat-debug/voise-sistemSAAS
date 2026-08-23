"""
Task Scheduler для автоматического выполнения запланированных задач.
Проверяет каждые 30 секунд наличие задач, которые нужно выполнить.

✅ ВЕРСИЯ v4.0 - PARTNER INTEGRATION + LEGACY FALLBACK
✅ v4.0: Поддержка партнёрской интеграции Voximplant (VoximplantChildAccount)
✅ v4.0: Обратная совместимость со старой интеграцией (user.get_voximplant_config())
✅ v3.4: Восстановлена правильная логика user.get_voximplant_config()
✅ v3.3: Исправлено получение assistant_id (использует task.assistant_id)
✅ v3.2: Исправлена обработка response от Voximplant API
✅ v3.1: Добавлена передача custom_greeting (персонализированное приветствие)
✅ v3.0: Передача контекста задачи в Voximplant
"""

import asyncio
import json
import httpx
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, Tuple

from backend.core.logging import get_logger
from backend.db.session import SessionLocal
from backend.models.task import Task, TaskStatus
from backend.models.contact import Contact
from backend.models.user import User
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.yandex_assistant import YandexAssistantConfig
from backend.models.grok_assistant import GrokAssistantConfig
from backend.models.fish_assistant import FishAssistantConfig
from backend.models.voximplant_child import VoximplantChildAccount
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.agent_call import AgentCall
from backend.services.voximplant_partner import get_voximplant_partner_service
from backend.services.agent_orchestrator import PreCallOrchestrator, PostCallOrchestrator

logger = get_logger(__name__)

# Voximplant API endpoint (для LEGACY интеграции)
VOXIMPLANT_API_URL = "https://api.voximplant.com/platform_api/StartScenarios/"

# Timezone по умолчанию
DEFAULT_TIMEZONE = "Europe/Moscow"

# Провайдеры со своим исходящим сценарием. Каскаду нужна цепочка
# vox-turn-taking + outbound_cascade, Fish — прокси синтеза (/ws/fish/tts/…);
# общий outbound_crm ни того, ни другого не умеет.
OUTBOUND_RULE_BY_TYPE = {
    "cascade": "outbound_cascade",
    "fish": "outbound_fish",
}
DEFAULT_OUTBOUND_RULE = "outbound_crm"


def _outbound_rule_name(assistant_type: Optional[str]) -> str:
    """Имя правила Voximplant для исходящего звонка этим типом ассистента."""
    return OUTBOUND_RULE_BY_TYPE.get(assistant_type, DEFAULT_OUTBOUND_RULE)


class TaskScheduler:
    """
    Планировщик задач для автоматических звонков.
    
    ✅ v4.0: Поддержка двух типов интеграции:
        1. НОВАЯ: VoximplantChildAccount (партнёрская интеграция)
        2. LEGACY: user.get_voximplant_config() (старая интеграция)
    
    ✅ v3.4: Правильная логика user.get_voximplant_config()
    ✅ v3.3: Правильное получение assistant_id из task
    ✅ v3.2: Корректная обработка response
    ✅ v3.1: Поддержка custom_greeting
    ✅ v3.0: Передача контекста задачи
    """
    
    def __init__(self, check_interval: int = 30):
        """
        Args:
            check_interval: Интервал проверки в секундах (по умолчанию 30 сек)
        """
        self.check_interval = check_interval
        self.is_running = False
        
    async def start(self):
        """Запуск планировщика"""
        if self.is_running:
            logger.warning("[TASK-SCHEDULER] Already running")
            return
            
        self.is_running = True
        logger.info(f"[TASK-SCHEDULER] Started (check every {self.check_interval}s)")
        logger.info(f"[TASK-SCHEDULER] ✅ v4.0: Partner + Legacy integration support")
        
        while self.is_running:
            try:
                await self.check_and_execute_tasks()
            except Exception as e:
                logger.error(f"[TASK-SCHEDULER] Error: {e}", exc_info=True)
            
            # Ждём перед следующей проверкой
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Остановка планировщика"""
        self.is_running = False
        logger.info("[TASK-SCHEDULER] Stopped")
    
    async def check_and_execute_tasks(self):
        """Проверка и выполнение задач (agent + regular)"""
        db = SessionLocal()

        try:
            now = datetime.utcnow()

            # 1. AGENT TASKS (is_agent_task=True)
            agent_tasks = db.query(Task).filter(
                Task.status == TaskStatus.SCHEDULED,
                Task.scheduled_time <= now,
                Task.is_agent_task == True
            ).all()

            # 2. REGULAR TASKS (is_agent_task=False or NULL)
            regular_tasks = db.query(Task).filter(
                Task.status == TaskStatus.SCHEDULED,
                Task.scheduled_time <= now,
                Task.is_agent_task != True
            ).all()

            total = len(agent_tasks) + len(regular_tasks)
            if not total:
                logger.debug(f"[TASK-SCHEDULER] No pending tasks at {now}")
                return

            logger.info(f"[TASK-SCHEDULER] Found {total} tasks ({len(agent_tasks)} agent, {len(regular_tasks)} regular)")

            for task in agent_tasks:
                await self.execute_agent_task(task, db)

            for task in regular_tasks:
                await self.execute_task(task, db)

        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] Error checking tasks: {e}", exc_info=True)
        finally:
            db.close()
    
    def _get_assistant_info(self, task: Task, db: Session) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Получить информацию об ассистенте из задачи.
        
        Returns:
            Tuple[assistant_id, assistant_name, assistant_type]
        """
        assistant_id = None
        assistant_name = "Unknown"
        assistant_type = None
        
        if task.assistant_id:
            # OpenAI Assistant
            assistant = db.query(AssistantConfig).filter(
                AssistantConfig.id == task.assistant_id
            ).first()
            if assistant:
                assistant_id = str(task.assistant_id)
                assistant_name = assistant.name
                assistant_type = "openai"
                logger.info(f"   Assistant: {assistant.name} (OpenAI)")
        elif task.gemini_assistant_id:
            # Gemini Assistant
            gemini_assistant = db.query(GeminiAssistantConfig).filter(
                GeminiAssistantConfig.id == task.gemini_assistant_id
            ).first()
            if gemini_assistant:
                assistant_id = str(task.gemini_assistant_id)
                assistant_name = gemini_assistant.name
                assistant_type = "gemini"
                logger.info(f"   Assistant: {gemini_assistant.name} (Gemini)")
        elif task.cartesia_assistant_id:
            # Cartesia Assistant
            cartesia_assistant = db.query(CartesiaAssistantConfig).filter(
                CartesiaAssistantConfig.id == task.cartesia_assistant_id
            ).first()
            if cartesia_assistant:
                assistant_id = str(task.cartesia_assistant_id)
                assistant_name = cartesia_assistant.name
                assistant_type = "cartesia"
                logger.info(f"   Assistant: {cartesia_assistant.name} (Cartesia)")
        elif task.yandex_assistant_id:
            # Yandex Assistant
            yandex_assistant = db.query(YandexAssistantConfig).filter(
                YandexAssistantConfig.id == task.yandex_assistant_id
            ).first()
            if yandex_assistant:
                assistant_id = str(task.yandex_assistant_id)
                assistant_name = yandex_assistant.name
                assistant_type = "yandex"
                logger.info(f"   Assistant: {yandex_assistant.name} (Yandex)")
        elif task.cascade_assistant_id:
            # Cascade Assistant (grok_assistant_configs, assistant_type='cascade')
            cascade_assistant = db.query(GrokAssistantConfig).filter(
                GrokAssistantConfig.id == task.cascade_assistant_id
            ).first()
            if cascade_assistant:
                assistant_id = str(task.cascade_assistant_id)
                assistant_name = cascade_assistant.name
                assistant_type = "cascade"
                logger.info(f"   Assistant: {cascade_assistant.name} (Cascade)")
        elif task.fish_assistant_id:
            # Fish Assistant (OpenAI Realtime в сценарии + озвучка Fish Audio)
            fish_assistant = db.query(FishAssistantConfig).filter(
                FishAssistantConfig.id == task.fish_assistant_id
            ).first()
            if fish_assistant:
                assistant_id = str(task.fish_assistant_id)
                assistant_name = fish_assistant.name
                assistant_type = "fish"
                logger.info(f"   Assistant: {fish_assistant.name} (Fish)")

        return assistant_id, assistant_name, assistant_type
    
    async def execute_agent_task(self, task: Task, db: Session):
        """
        Execute an agent task: create AgentCall, run PreCall with AgentContact,
        then initiate the call and launch PostCall.
        """
        try:
            logger.info(f"[TASK-SCHEDULER] 🤖 Executing AGENT task {task.id}: {task.title}")

            # Get AgentContact first — оно определяет, какому агенту принадлежит звонок.
            agent_contact = None
            if task.agent_contact_id:
                agent_contact = db.query(AgentContact).filter(
                    AgentContact.id == task.agent_contact_id
                ).first()

            if not agent_contact:
                logger.error(f"[TASK-SCHEDULER] AgentContact not found for agent task {task.id}")
                task.status = TaskStatus.FAILED
                task.call_result = "AgentContact not found"
                db.commit()
                return

            # ✅ v3.1: резолвим КОНКРЕТНОГО агента по контакту (multi-agent).
            #   Fallback на первого агента юзера — для legacy-контактов без
            #   agent_config_id.
            agent_config = None
            if agent_contact.agent_config_id:
                agent_config = db.query(AgentConfig).filter(
                    AgentConfig.id == agent_contact.agent_config_id,
                    AgentConfig.user_id == task.user_id,
                ).first()
            if not agent_config:
                agent_config = db.query(AgentConfig).filter(
                    AgentConfig.user_id == task.user_id,
                ).order_by(AgentConfig.created_at.asc()).first()

            # Skip if the agent is missing or inactive (toggle off).
            # Leave the task SCHEDULED so it runs once the agent is re-activated.
            if not agent_config or not agent_config.is_active:
                logger.info(f"[TASK-SCHEDULER] Skipping agent task {task.id}: agent missing or inactive")
                return  # не помечаем как failed — просто пропускаем, ждём активации

            # Lock task immediately
            task.status = TaskStatus.PENDING
            task.call_started_at = datetime.utcnow()
            db.commit()

            # Get user
            user = db.query(User).filter(User.id == task.user_id).first()
            if not user:
                logger.error(f"[TASK-SCHEDULER] User not found for agent task {task.id}")
                task.status = TaskStatus.FAILED
                task.call_result = "User not found"
                db.commit()
                return

            # ✅ Блокировка звонков при отсутствии доступа к агенту (триал истёк и
            #   тариф не profi). Дублирует scheduler-блокер.
            if not user.has_active_agent_subscription():
                task.status = TaskStatus.CANCELLED
                task.call_result = json.dumps({"error": "subscription_expired"})
                db.commit()
                logger.warning(f"[TASK-SCHEDULER] Skipping agent task {task.id} — no agent access")
                return

            # 🆕 Telegram-задача: вместо звонка — отложенное сообщение с личного
            # Telegram-аккаунта (текст составит оркестратор в момент отправки).
            if (task.channel or "call") == "telegram":
                await self.execute_agent_telegram_task(task, agent_contact, agent_config, user, db)
                return

            # Get assistant info
            assistant_id, assistant_name, assistant_type = self._get_assistant_info(task, db)
            if not assistant_id or not assistant_type:
                logger.error(f"[TASK-SCHEDULER] No valid assistant for agent task {task.id}")
                task.status = TaskStatus.FAILED
                task.call_result = "Assistant not found"
                db.commit()
                return

            # Create AgentCall record
            agent_call = AgentCall(
                agent_contact_id=agent_contact.id,
                agent_config_id=agent_config.id if agent_config else None,
                user_id=user.id,
                source_task_id=task.id,
                status="calling",
                scheduled_at=task.scheduled_time,
                started_at=datetime.utcnow(),
            )
            db.add(agent_call)
            db.flush()

            # Link task to agent_call
            task.agent_call_id = agent_call.id

            # Контакт остаётся в своей стадии воронки во время звонка. Факт
            # «идёт звонок» фиксируется в AgentCall.status / Task.status, а
            # стадию контакта меняет только PostCall по итогу — и только если
            # для этого есть основание (см. stage_from_decision).
            db.commit()

            # PreCall with AgentContact.
            # v3 agents use OpenRouter (system key) → run regardless of user's OpenAI key.
            # v2 (legacy) agents require the user's OpenAI key.
            can_orchestrate = agent_config and (
                agent_config.uses_hardcoded_prompt or user.openai_api_key
            )
            if can_orchestrate:
                try:
                    pre_call = PreCallOrchestrator()
                    pre_result = await pre_call.run(task, agent_contact, agent_call, agent_config, user, db)
                    logger.info(f"[TASK-SCHEDULER] ✅ Agent PreCall completed: {pre_result.get('call_strategy', '')[:80]}")
                except Exception as e:
                    logger.error(f"[TASK-SCHEDULER] ⚠️ Agent PreCall failed (continuing): {e}")

            # Determine which integration to use for the call
            # Use agent_contact.phone as the number to call
            phone_number = agent_contact.phone
            contact_name = agent_contact.name or ""

            child_account: Optional[VoximplantChildAccount] = None
            if hasattr(user, 'voximplant_child_account') and user.voximplant_child_account:
                child_account = user.voximplant_child_account

            call_session_id = None
            call_success = False

            if child_account and child_account.can_make_outbound_calls:
                call_session_id, call_success = await self._agent_call_via_partner(
                    task, agent_contact, child_account, assistant_id, assistant_name, assistant_type, db
                )
            elif user.has_voximplant_config():
                call_session_id, call_success = await self._agent_call_via_legacy(
                    task, agent_contact, user, assistant_id, assistant_name, assistant_type, db
                )
            else:
                logger.error(f"[TASK-SCHEDULER] ❌ No Voximplant config for agent task {task.id}")
                task.status = TaskStatus.FAILED
                task.call_result = "No Voximplant configuration found."
                agent_call.status = "failed"
                db.commit()
                return

            if call_success and call_session_id:
                agent_call.call_session_id = str(call_session_id)
                task.call_session_id = str(call_session_id)
                task.status = TaskStatus.COMPLETED
                task.call_completed_at = datetime.utcnow()
                task.call_result = f"Agent call initiated. Session: {call_session_id}"
                db.commit()

                # Launch PostCall with agent_call_id (v3 → OpenRouter, v2 → user OpenAI key)
                if can_orchestrate:
                    asyncio.create_task(
                        PostCallOrchestrator.poll_and_run(
                            agent_call_id=str(agent_call.id),
                            agent_config_id=str(agent_config.id),
                            user_openai_key=user.openai_api_key or "",
                        )
                    )
                    logger.info(f"[TASK-SCHEDULER] 🤖 Agent PostCall started for call {agent_call.id}")
            else:
                task.status = TaskStatus.FAILED
                task.call_result = task.call_result or "Call failed"
                agent_call.status = "failed"
                db.commit()

        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] Error in agent task {task.id}: {e}", exc_info=True)
            try:
                task.status = TaskStatus.FAILED
                task.call_result = f"Internal error: {str(e)}"
                db.commit()
            except Exception:
                pass

    async def execute_agent_telegram_task(self, task: Task, agent_contact, agent_config, user, db: Session):
        """
        Исполнить агентскую задачу с channel="telegram": один прогон оркестратора,
        который по памяти контакта, хронологии и инструкции из task.description
        составляет сообщение и отправляет его с личного Telegram-аккаунта агента
        (PostCallOrchestrator.run_for_scheduled_telegram). Звонилка не участвует.

        Вызывается из execute_agent_task ПОСЛЕ общих проверок (контакт найден,
        агент активен, подписка активна); задача уже залочена (PENDING).
        """
        from backend.services import telegram_user_service
        from backend.services.agent_tools import fn_send_telegram_notification

        agent_call = None
        try:
            # Личный TG-аккаунт мог отвалиться между постановкой и исполнением.
            # Не роняем задачу молча: FAILED + уведомление владельцу через бота.
            account_ok = (
                telegram_user_service.is_configured()
                and telegram_user_service.account_connected(db, agent_config.id)
            )
            # Прогон реализован только для v3-агентов (OpenRouter): тул
            # schedule_telegram_message домешивается только им.
            if not getattr(agent_config, "uses_hardcoded_prompt", False):
                account_ok = False

            if not account_ok:
                task.status = TaskStatus.FAILED
                task.call_result = json.dumps(
                    {"error": "telegram_account_unavailable"}, ensure_ascii=False
                )
                task.call_completed_at = datetime.utcnow()
                db.commit()
                logger.warning(
                    f"[TASK-SCHEDULER] ✉️ Telegram task {task.id} failed: personal TG account unavailable"
                )
                try:
                    await fn_send_telegram_notification(
                        {
                            "message": (
                                f"⚠️ Не смог отправить запланированное сообщение в Telegram "
                                f"контакту {agent_contact.name or agent_contact.phone}: "
                                f"личный Telegram-аккаунт не подключён. "
                                f"Задача: «{task.title}». Подключите аккаунт и создайте задачу заново."
                            )
                        },
                        agent_config, db,
                    )
                except Exception as ne:
                    logger.warning(f"[TASK-SCHEDULER] Owner notify failed: {ne}")
                return

            # Запись в истории агента (лента/модалка на agent.html); канал события
            # определится по postcall_log.call_direction="telegram_outbound".
            agent_call = AgentCall(
                agent_contact_id=agent_contact.id,
                agent_config_id=agent_config.id,
                user_id=user.id,
                source_task_id=task.id,
                status="calling",
                direction="outbound",
                scheduled_at=task.scheduled_time,
                started_at=datetime.utcnow(),
            )
            db.add(agent_call)
            db.flush()
            task.agent_call_id = agent_call.id
            db.commit()

            orchestrator = PostCallOrchestrator()
            await orchestrator.run_for_scheduled_telegram(
                agent_call, agent_contact, agent_config, user, task, db
            )
            # Статусы task/agent_call проставил прогон (_analyze_v3_openrouter);
            # фиксируем время завершения задачи.
            task.call_completed_at = datetime.utcnow()
            db.commit()
            logger.info(f"[TASK-SCHEDULER] ✉️ Telegram task {task.id} completed")

        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] Error in telegram task {task.id}: {e}", exc_info=True)
            try:
                task.status = TaskStatus.FAILED
                task.call_result = f"Internal error: {str(e)}"
                # Не оставляем событие вечно в 'calling' — иначе оно навсегда
                # скроется из истории (список показывает только финализированные).
                if agent_call is not None and agent_call.status == "calling":
                    agent_call.status = "failed"
                    agent_call.completed_at = datetime.utcnow()
                db.commit()
            except Exception:
                pass

    async def _agent_call_via_partner(
        self, task, agent_contact, child_account, assistant_id, assistant_name, assistant_type, db
    ) -> Tuple[Optional[str], bool]:
        """Initiate agent call via partner API. Returns (session_id, success)."""
        try:
            outbound_rule_name = _outbound_rule_name(assistant_type)
            rule_id = child_account.get_rule_id(outbound_rule_name)
            if not rule_id:
                task.call_result = f"Outbound rule '{outbound_rule_name}' not configured"
                return None, False

            caller_id = None

            # 1. Task-level caller_id
            if task.caller_id:
                if child_account.phone_numbers:
                    for phone in child_account.phone_numbers:
                        if phone.phone_number == task.caller_id and phone.is_active:
                            caller_id = task.caller_id
                            break

            # 2. AgentConfig.default_caller_id
            if not caller_id and agent_contact:
                agent_cfg = db.query(AgentConfig).filter(
                    AgentConfig.id == agent_contact.agent_config_id
                ).first()
                if agent_cfg and agent_cfg.default_caller_id:
                    if child_account.phone_numbers:
                        for phone in child_account.phone_numbers:
                            if phone.phone_number == agent_cfg.default_caller_id and phone.is_active:
                                caller_id = agent_cfg.default_caller_id
                                logger.info(f"   📞 Using agent default_caller_id: {caller_id}")
                                break

            # 3. First active number (fallback)
            if not caller_id and child_account.phone_numbers:
                for phone in child_account.phone_numbers:
                    if phone.is_active:
                        caller_id = phone.phone_number
                        break
            if not caller_id:
                task.call_result = "No active phone numbers"
                return None, False

            service = get_voximplant_partner_service()
            result = await service.start_outbound_call(
                child_account_id=child_account.vox_account_id,
                child_api_key=child_account.vox_api_key,
                rule_id=int(rule_id),
                phone_number=agent_contact.phone,
                assistant_id=assistant_id,
                caller_id=caller_id,
                contact_name=agent_contact.name or "",
                task_title=task.title or "",
                task_description=task.description or "",
                custom_greeting=task.custom_greeting or "",
                timezone=DEFAULT_TIMEZONE,
                assistant_type=assistant_type,
            )

            if result.get("success"):
                return result.get("call_session_history_id"), True
            else:
                task.call_result = f"Partner API error: {result.get('error', 'Unknown')}"
                return None, False
        except Exception as e:
            task.call_result = f"Partner API exception: {str(e)}"
            return None, False

    async def _agent_call_via_legacy(
        self, task, agent_contact, user, assistant_id, assistant_name, assistant_type, db
    ) -> Tuple[Optional[str], bool]:
        """Initiate agent call via legacy API. Returns (session_id, success)."""
        try:
            voximplant_config = user.get_voximplant_config()
            if not voximplant_config:
                task.call_result = "No Voximplant settings"
                return None, False

            final_assistant_id = assistant_id
            if assistant_type == "gemini":
                final_assistant_id = f"gemini_{assistant_id}"

            script_custom_data = json.dumps({
                "phone_number": agent_contact.phone,
                "assistant_id": final_assistant_id,
                "caller_id": voximplant_config["caller_id"],
                "task_title": task.title or "",
                "task_description": task.description or "",
                "contact_name": agent_contact.name or "",
                "custom_greeting": task.custom_greeting or "",
                "timezone": DEFAULT_TIMEZONE,
            }, ensure_ascii=False)

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    VOXIMPLANT_API_URL,
                    data={
                        "account_id": voximplant_config["account_id"],
                        "api_key": voximplant_config["api_key"],
                        "rule_id": voximplant_config["rule_id"],
                        "script_custom_data": script_custom_data,
                    }
                )
                response_data = response.json()

                if response.status_code == 200 and response_data.get("result") == 1:
                    raw = response_data.get("call_session_history_id")
                    session_id = raw[0] if isinstance(raw, list) and raw else raw
                    return session_id, True
                else:
                    error_msg = response_data.get("error", {}).get("msg", "Unknown error")
                    task.call_result = f"Voximplant error: {error_msg}"
                    return None, False
        except Exception as e:
            task.call_result = f"Legacy API exception: {str(e)}"
            return None, False

    async def execute_task(self, task: Task, db: Session):
        """
        Выполнение конкретной задачи (инициация звонка).

        ✅ v4.0: Выбор интеграции:
            1. Проверяем VoximplantChildAccount (НОВАЯ партнёрская интеграция)
            2. Fallback на user.get_voximplant_config() (СТАРАЯ интеграция)
        
        Args:
            task: Задача для выполнения
            db: Сессия БД
        """
        try:
            logger.info(f"[TASK-SCHEDULER] 🚀 Executing task {task.id}: {task.title}")
            
            # Обновляем статус на PENDING
            task.status = TaskStatus.PENDING
            task.call_started_at = datetime.utcnow()
            db.commit()
            
            # Получаем контакт
            contact = db.query(Contact).filter(Contact.id == task.contact_id).first()
            if not contact:
                logger.error(f"[TASK-SCHEDULER] Contact not found for task {task.id}")
                task.status = TaskStatus.FAILED
                task.call_result = "Contact not found"
                db.commit()
                return
            
            # Получаем пользователя через relationship
            user = contact.user
            if not user:
                logger.error(f"[TASK-SCHEDULER] User not found for contact {contact.id}")
                task.status = TaskStatus.FAILED
                task.call_result = "User not found"
                db.commit()
                return
            
            # Получаем информацию об ассистенте
            assistant_id, assistant_name, assistant_type = self._get_assistant_info(task, db)
            
            if not assistant_id or not assistant_type:
                logger.error(f"[TASK-SCHEDULER] No valid assistant found for task {task.id}")
                task.status = TaskStatus.FAILED
                task.call_result = "Assistant not found"
                db.commit()
                return

            # =====================================================================
            # ✅ v3.0: PreCall убран из обычных задач — Voksy AI Agent работает
            # только через agent tasks (execute_agent_task). Для обычных Task'ов
            # PreCall не нужен.
            # =====================================================================

            # =====================================================================
            # ✅ v4.0: ВЫБОР ИНТЕГРАЦИИ
            # =====================================================================
            
            # Проверяем наличие партнёрской интеграции (VoximplantChildAccount)
            child_account: Optional[VoximplantChildAccount] = None
            
            # Используем backref relationship
            if hasattr(user, 'voximplant_child_account') and user.voximplant_child_account:
                child_account = user.voximplant_child_account
            
            # Определяем какую интеграцию использовать
            if child_account and child_account.can_make_outbound_calls:
                # ✅ НОВАЯ партнёрская интеграция
                logger.info(f"[TASK-SCHEDULER] 🆕 Using PARTNER integration (VoximplantChildAccount)")
                await self._execute_via_partner_api(
                    task=task,
                    contact=contact,
                    child_account=child_account,
                    assistant_id=assistant_id,
                    assistant_name=assistant_name,
                    assistant_type=assistant_type,
                    db=db
                )
            elif user.has_voximplant_config():
                # ✅ LEGACY интеграция (fallback)
                logger.info(f"[TASK-SCHEDULER] 📦 Using LEGACY integration (user.get_voximplant_config)")
                await self._execute_via_legacy_api(
                    task=task,
                    contact=contact,
                    user=user,
                    assistant_id=assistant_id,
                    assistant_name=assistant_name,
                    assistant_type=assistant_type,
                    db=db
                )
            else:
                # ❌ Нет конфигурации
                logger.error(f"[TASK-SCHEDULER] ❌ No Voximplant configuration found for user {user.id}")
                task.status = TaskStatus.FAILED
                task.call_result = "No Voximplant configuration found. Please configure telephony in Settings or connect Partner integration."
                db.commit()
            
        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] Error executing task {task.id}: {e}", exc_info=True)
            
            # Помечаем задачу как failed
            try:
                task.status = TaskStatus.FAILED
                task.call_result = f"Internal error: {str(e)}"
                db.commit()
            except Exception as commit_error:
                logger.error(f"[TASK-SCHEDULER] Failed to update task status: {commit_error}")
    
    async def _execute_via_partner_api(
        self,
        task: Task,
        contact: Contact,
        child_account: VoximplantChildAccount,
        assistant_id: str,
        assistant_name: str,
        assistant_type: str,
        db: Session
    ):
        """
        ✅ v4.0: Выполнение звонка через НОВУЮ партнёрскую интеграцию.
        
        Использует VoximplantPartnerService для запуска звонка.
        """
        try:
            logger.info(f"[TASK-SCHEDULER] 🆕 PARTNER API CALL")
            logger.info(f"   Contact: {contact.name or contact.phone}")
            logger.info(f"   Assistant: {assistant_name} ({assistant_type})")
            logger.info(f"   Task: {task.title}")
            if task.custom_greeting:
                logger.info(f"   💬 Custom Greeting: {task.custom_greeting[:80]}...")
            
            # Сценарий исходящего: общий outbound_crm для провайдеров, которые он
            # умеет, и свой сценарий у каскада и Fish (см. _outbound_rule_name).
            rule_name = _outbound_rule_name(assistant_type)
            rule_id = child_account.get_rule_id(rule_name)

            if not rule_id:
                logger.error(f"[TASK-SCHEDULER] ❌ Rule '{rule_name}' not found in child account")
                logger.error(f"   Available rules: {list(child_account.vox_rule_ids.keys()) if child_account.vox_rule_ids else 'None'}")
                task.status = TaskStatus.FAILED
                task.call_result = (
                    f"Outbound rule '{rule_name}' not configured. Run the matching admin "
                    f"endpoint (/api/telephony/admin/setup-crm-rules for outbound_crm, "
                    f"/admin/setup-fish-scenarios or /admin/setup-cascade-scenarios for "
                    f"provider rules) to create it."
                )
                db.commit()
                return

            # Активный агент пользователя: его default_caller_id служит запасным
            # номером, а после успешного старта звонка — ключом для PostCall.
            # Читать эту переменную ДО присваивания (как было раньше) нельзя:
            # любая задача без явного task.caller_id падала с UnboundLocalError.
            agent_config = db.query(AgentConfig).filter(
                AgentConfig.user_id == contact.user_id,
                AgentConfig.is_active == True
            ).first()

            # ✅ v4.1: Выбор caller_id — из задачи, агента или автоматически
            caller_id = None

            # 1. Если в задаче указан конкретный caller_id — используем его
            if task.caller_id:
                caller_id_valid = False
                if child_account.phone_numbers:
                    for phone in child_account.phone_numbers:
                        if phone.phone_number == task.caller_id and phone.is_active:
                            caller_id = task.caller_id
                            caller_id_valid = True
                            logger.info(f"   ✅ Using task-specified caller_id: {caller_id}")
                            break

                if not caller_id_valid:
                    logger.warning(f"[TASK-SCHEDULER] ⚠️ Task caller_id '{task.caller_id}' is not active or not found")
                    logger.warning(f"   Falling back to agent/auto-select...")

            # 2. AgentConfig.default_caller_id (если есть активный агент)
            if not caller_id and agent_config and agent_config.default_caller_id:
                if child_account.phone_numbers:
                    for phone in child_account.phone_numbers:
                        if phone.phone_number == agent_config.default_caller_id and phone.is_active:
                            caller_id = agent_config.default_caller_id
                            logger.info(f"   📞 Using agent default_caller_id: {caller_id}")
                            break

            # 3. Если caller_id не указан или не валиден — берём первый активный
            if not caller_id:
                if child_account.phone_numbers:
                    for phone in child_account.phone_numbers:
                        if phone.is_active:
                            caller_id = phone.phone_number
                            logger.info(f"   📞 Auto-selected caller_id: {caller_id}")
                            break

            # 4. Если вообще нет активных номеров — ошибка
            if not caller_id:
                logger.error(f"[TASK-SCHEDULER] ❌ No active phone numbers for caller_id")
                task.status = TaskStatus.FAILED
                task.call_result = "No active phone numbers available for caller ID. Check that your phone numbers are not expired."
                db.commit()
                return
            
            logger.info(f"   Rule ID: {rule_id}")
            logger.info(f"   Caller ID: {caller_id}")
            
            # Вызываем VoximplantPartnerService
            service = get_voximplant_partner_service()
            
            result = await service.start_outbound_call(
                child_account_id=child_account.vox_account_id,
                child_api_key=child_account.vox_api_key,
                rule_id=int(rule_id),
                phone_number=contact.phone,
                assistant_id=assistant_id,
                caller_id=caller_id,
                # ✅ v3.1: CRM контекст
                contact_name=contact.name or "",
                task_title=task.title or "",
                task_description=task.description or "",
                custom_greeting=task.custom_greeting or "",
                timezone=DEFAULT_TIMEZONE,
                assistant_type=assistant_type
            )
            
            if result.get("success"):
                call_session_id = result.get("call_session_history_id")
                
                task.status = TaskStatus.COMPLETED
                task.call_completed_at = datetime.utcnow()
                task.call_session_id = str(call_session_id) if call_session_id else None
                task.call_result = f"Call initiated successfully via Partner API. Session ID: {call_session_id}"
                
                logger.info(f"[TASK-SCHEDULER] ✅ Task {task.id} completed successfully (Partner API)")
                logger.info(f"   Call session ID: {call_session_id}")

                # ✅ v5.0: Launch PostCall if agent is active (agent_config найден выше)
                if agent_config and task.pre_call_response_id:
                    user = db.query(User).filter(User.id == contact.user_id).first()
                    if user and user.openai_api_key:
                        asyncio.create_task(
                            PostCallOrchestrator.poll_and_run(
                                task_id=str(task.id),
                                agent_config_id=str(agent_config.id),
                                user_openai_key=user.openai_api_key
                            )
                        )
                        logger.info(f"[TASK-SCHEDULER] 🤖 PostCall polling started for task {task.id}")
            else:
                error_msg = result.get("error", "Unknown error")
                task.status = TaskStatus.FAILED
                task.call_result = f"Partner API error: {error_msg}"

                logger.error(f"[TASK-SCHEDULER] ❌ Partner API error for task {task.id}: {error_msg}")

            db.commit()
            
        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] Partner API exception: {e}", exc_info=True)
            task.status = TaskStatus.FAILED
            task.call_result = f"Partner API exception: {str(e)}"
            db.commit()
    
    async def _execute_via_legacy_api(
        self,
        task: Task,
        contact: Contact,
        user: User,
        assistant_id: str,
        assistant_name: str,
        assistant_type: str,
        db: Session
    ):
        """
        ✅ v4.0: Выполнение звонка через СТАРУЮ (Legacy) интеграцию.
        
        Использует прямой вызов Voximplant API с настройками из user модели.
        Это оригинальный код из v3.4, вынесенный в отдельный метод.
        """
        try:
            logger.info(f"[TASK-SCHEDULER] 📦 LEGACY API CALL")
            logger.info(f"   Contact: {contact.name or contact.phone}")
            logger.info(f"   Assistant: {assistant_name} ({assistant_type})")
            logger.info(f"   Task: {task.title}")
            if task.custom_greeting:
                logger.info(f"   💬 Custom Greeting: {task.custom_greeting[:80]}...")
            
            # Получаем настройки Voximplant из user модели
            voximplant_config = user.get_voximplant_config()
            
            if not voximplant_config:
                logger.error(f"[TASK-SCHEDULER] ❌ User {user.id} has no Voximplant settings")
                task.status = TaskStatus.FAILED
                task.call_result = "User has no Voximplant settings. Please configure in Settings."
                db.commit()
                return
            
            # Для Gemini добавляем префикс к assistant_id
            final_assistant_id = assistant_id
            if assistant_type == "gemini":
                final_assistant_id = f"gemini_{assistant_id}"
            
            # Формируем script_custom_data с контекстом задачи
            script_custom_data_dict = {
                "phone_number": contact.phone,
                "assistant_id": final_assistant_id,
                "caller_id": voximplant_config["caller_id"],
                # Контекст задачи
                "task_title": task.title or "",
                "task_description": task.description or "",
                "contact_name": contact.name or "",
                # Персонализированное приветствие
                "custom_greeting": task.custom_greeting or "",
                # Timezone
                "timezone": DEFAULT_TIMEZONE
            }
            
            logger.info(f"[TASK-SCHEDULER] 📦 Script custom data:")
            logger.info(f"   phone_number: {script_custom_data_dict['phone_number']}")
            logger.info(f"   assistant_id: {script_custom_data_dict['assistant_id']}")
            logger.info(f"   caller_id: {script_custom_data_dict['caller_id']}")
            logger.info(f"   task_title: {script_custom_data_dict['task_title']}")
            logger.info(f"   contact_name: {script_custom_data_dict['contact_name']}")
            if script_custom_data_dict['custom_greeting']:
                logger.info(f"   💬 custom_greeting: {script_custom_data_dict['custom_greeting'][:80]}...")
            
            script_custom_data = json.dumps(script_custom_data_dict, ensure_ascii=False)
            
            # Отправляем запрос в Voximplant API
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    VOXIMPLANT_API_URL,
                    data={
                        "account_id": voximplant_config["account_id"],
                        "api_key": voximplant_config["api_key"],
                        "rule_id": voximplant_config["rule_id"],
                        "script_custom_data": script_custom_data
                    }
                )
                
                response_data = response.json()
                
                logger.info(f"[TASK-SCHEDULER] Voximplant API response: {response_data}")
                
                if response.status_code == 200 and response_data.get("result") == 1:
                    # Успешно запущен звонок
                    call_session_raw = response_data.get("call_session_history_id")
                    
                    if isinstance(call_session_raw, list):
                        call_session_id = call_session_raw[0] if call_session_raw else None
                    else:
                        call_session_id = call_session_raw
                    
                    task.status = TaskStatus.COMPLETED
                    task.call_completed_at = datetime.utcnow()
                    task.call_session_id = str(call_session_id) if call_session_id else None
                    task.call_result = f"Call initiated successfully via Legacy API. Session ID: {call_session_id}"

                    logger.info(f"[TASK-SCHEDULER] ✅ Task {task.id} completed successfully (Legacy API)")
                    logger.info(f"   Call session ID: {call_session_id}")

                    # ✅ v5.0: Launch PostCall if agent is active
                    agent_config = db.query(AgentConfig).filter(
                        AgentConfig.user_id == user.id,
                        AgentConfig.is_active == True
                    ).first()
                    if agent_config and task.pre_call_response_id:
                        if user.openai_api_key:
                            asyncio.create_task(
                                PostCallOrchestrator.poll_and_run(
                                    task_id=str(task.id),
                                    agent_config_id=str(agent_config.id),
                                    user_openai_key=user.openai_api_key
                                )
                            )
                            logger.info(f"[TASK-SCHEDULER] 🤖 PostCall polling started for task {task.id}")
                else:
                    # Ошибка от Voximplant
                    error_msg = response_data.get("error", {}).get("msg", "Unknown Voximplant error")
                    error_code = response_data.get("error", {}).get("code", "N/A")
                    
                    task.status = TaskStatus.FAILED
                    task.call_result = f"Voximplant error [{error_code}]: {error_msg}"
                    
                    logger.error(f"[TASK-SCHEDULER] ❌ Voximplant error for task {task.id}")
                    logger.error(f"   Error code: {error_code}")
                    logger.error(f"   Error message: {error_msg}")
                
                db.commit()
                
        except httpx.TimeoutException as e:
            logger.error(f"[TASK-SCHEDULER] Timeout calling Voximplant API: {e}")
            task.status = TaskStatus.FAILED
            task.call_result = f"Timeout error: Request to Voximplant took too long"
            db.commit()
            
        except httpx.RequestError as e:
            logger.error(f"[TASK-SCHEDULER] Network error calling Voximplant API: {e}")
            task.status = TaskStatus.FAILED
            task.call_result = f"Network error: {str(e)}"
            db.commit()
            
        except json.JSONDecodeError as e:
            logger.error(f"[TASK-SCHEDULER] Invalid JSON response from Voximplant: {e}")
            task.status = TaskStatus.FAILED
            task.call_result = f"Invalid response from Voximplant API"
            db.commit()
        
        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] Legacy API exception: {e}", exc_info=True)
            task.status = TaskStatus.FAILED
            task.call_result = f"Legacy API exception: {str(e)}"
            db.commit()


# Глобальный экземпляр планировщика
_task_scheduler: Optional[TaskScheduler] = None


async def start_task_scheduler(check_interval: int = 30):
    """
    Запуск планировщика задач.
    
    Args:
        check_interval: Интервал проверки в секундах
    """
    global _task_scheduler
    
    if _task_scheduler is not None:
        logger.warning("[TASK-SCHEDULER] Already running")
        return
    
    _task_scheduler = TaskScheduler(check_interval=check_interval)
    
    try:
        await _task_scheduler.start()
    except Exception as e:
        logger.error(f"[TASK-SCHEDULER] Fatal error: {e}", exc_info=True)
        _task_scheduler = None


def stop_task_scheduler():
    """Остановка планировщика задач"""
    global _task_scheduler
    
    if _task_scheduler is not None:
        _task_scheduler.stop()
        _task_scheduler = None
