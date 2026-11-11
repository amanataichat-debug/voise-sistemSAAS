"""
Планировщик задач: каждые 30 секунд берёт наступившие задачи и выполняет их.

Звонки идут только через собственный SIP-шлюз (Asterisk + мост, infra/sip-gateway): задача без номера с
исходящими завершается с понятной причиной. Запасные пути через Voximplant удалены (ветка 1111-clean-system).
Задачу выполняет ровно один из воркеров gunicorn (_claim_task — атомарный UPDATE).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, Tuple

from backend.core.logging import get_logger
from backend.core.spawn import spawn
from backend.models.task import Task, TaskStatus
from backend.models.contact import Contact
from backend.models.user import User
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.yandex_assistant import YandexAssistantConfig
from backend.models.grok_assistant import GrokAssistantConfig
from backend.models.fish_assistant import FishAssistantConfig
from backend.models.eleven_assistant import ElevenAssistantConfig
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.agent_call import AgentCall
from backend.services.agent_orchestrator import PreCallOrchestrator, PostCallOrchestrator

logger = get_logger(__name__)


# Timezone по умолчанию
DEFAULT_TIMEZONE = "Asia/Bishkek"

# Анти-долбёж (карта, баг B1): минимальный интервал между дозвонами ОДНОМУ
# контакту. Если звонили меньше этого времени назад — повторный звонок не
# набираем сейчас, а переносим. Клиент сбросил/не ответил → агент не долбит
# подряд, перезвонит позже (во второй половине дня, в рабочем окне).
CALL_RETRY_COOLDOWN = timedelta(hours=3)
# После СОСТОЯВШЕГОСЯ разговора (клиент попросил перезвонить «через час») пауза
# короткая: 3 часа держали бы «через 2 часа» до вечера (0311-call-guard).
CALLBACK_AFTER_TALK_COOLDOWN = timedelta(minutes=15)

# Звонок агента без номера собственной телефонии (sip_phone_numbers с allow_outbound).
NO_OUTBOUND_NUMBER_MESSAGE = (
    "Нет номера для исходящих звонков. Подключите номер в настройках агента."
)

class TaskScheduler:
    """
    Планировщик задач: звонки агента и обычные задачи — через SIP-шлюз, Telegram-задачи — сообщением.
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
        # Импорт здесь, а не наверху файла — см. комментарий в backend/core/scheduler.py
        # (02.11.2026): цикл backend.core ↔ backend.db.session рвался по-разному в
        # зависимости от того, какой модуль процесс импортировал первым.
        from backend.db.session import SessionLocal
        db = SessionLocal()

        try:
            now = datetime.utcnow()

            # 1. AGENT TASKS (is_agent_task=True)
            # channel="owner" (B3) — это задача-напоминание ВЛАДЕЛЬЦУ (handoff:
            # позвонить/подключить клиента), а не действие агента. Планировщик
            # её НЕ исполняет: она просто висит в «Ближайшие задачи», владелец
            # закрывает её сам. Поэтому исключаем из выборки на исполнение.
            agent_tasks = db.query(Task).filter(
                Task.status == TaskStatus.SCHEDULED,
                Task.scheduled_time <= now,
                Task.is_agent_task == True,
                Task.channel != "owner",
            ).all()

            # 2. REGULAR TASKS (is_agent_task=False or NULL)
            regular_tasks = db.query(Task).filter(
                Task.status == TaskStatus.SCHEDULED,
                Task.scheduled_time <= now,
                Task.is_agent_task != True
            ).all()

            total = len(agent_tasks) + len(regular_tasks)

            # Прогрев клиентов за 25–35 мин до запланированных звонков
            # (ветка 1810-anri-sales-logic). Делается на каждом тике, независимо
            # от того, есть ли сейчас задачи «на выполнение».
            try:
                from backend.services import call_warmup
                await call_warmup.scan_and_send(db)
            except Exception as e:
                logger.warning(f"[TASK-SCHEDULER] call warmup scan failed: {e}")

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
        elif task.eleven_assistant_id:
            # Eleven Assistant (OpenAI Realtime текстом + ElevenLabs TTS), только через SIP-шлюз
            eleven_assistant = db.query(ElevenAssistantConfig).filter(
                ElevenAssistantConfig.id == task.eleven_assistant_id
            ).first()
            if eleven_assistant:
                assistant_id = str(task.eleven_assistant_id)
                assistant_name = eleven_assistant.name
                assistant_type = "eleven"
                logger.info(f"   Assistant: {eleven_assistant.name} (Eleven)")

        return assistant_id, assistant_name, assistant_type

    def _dial_gate(self, agent_contact, now_utc: datetime, agent_config=None, db=None) -> Optional[datetime]:
        """
        Детерминированный предохранитель перед исходящим дозвоном (карта, баги B1+B2).
        НЕ зависит от решений оркестратора — работает всегда, на уровне кода:
          * B2 — окно звонков агента (настройка «Звонки», по умолчанию 09–20 Бишкек;
            выключено — без ограничения по времени) — при исполнении задачи, не только при создании;
          * B1 — анти-долбёж: если контакту уже звонили менее CALL_RETRY_COOLDOWN
            назад, повтор не набираем сейчас.

        Возвращает None, если звонить можно прямо сейчас. Иначе — наивный UTC
        datetime, на который нужно перенести задачу (оставив её SCHEDULED).
        """
        from backend.core.timezone_utils import BISHKEK
        from backend.services.agent_tools import call_window

        now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(BISHKEK)
        earliest = now_local

        # B1 — анти-долбёж: не раньше, чем через CALL_RETRY_COOLDOWN после прошлого звонка.
        last = getattr(agent_contact, "last_called_at", None)
        cooldown = CALL_RETRY_COOLDOWN
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if db is not None and last + CALL_RETRY_COOLDOWN > now_utc.replace(tzinfo=timezone.utc):
                # Сверка с фактом (0311-call-guard): last_called_at мог поставить
                # разбор сообщения из мессенджера — тогда это не звонок; а если
                # последний звонок — состоявшийся разговор, перезвон по просьбе
                # клиента ждёт только короткую паузу.
                from backend.services.agent_contact_phones import voice_calls_since
                rows = voice_calls_since(db, agent_contact, agent_contact.user_id, last - timedelta(hours=2))
                if rows is not None and not rows:
                    last = None
                elif rows and rows[-1].answered_at is not None:
                    cooldown = CALLBACK_AFTER_TALK_COOLDOWN
        if last is not None:
            cooldown_until = last.astimezone(BISHKEK) + cooldown
            if cooldown_until > earliest:
                earliest = cooldown_until

        # B2 — поджать к окну звонков агента (None — окно выключено владельцем).
        window = call_window(agent_config)
        if window is not None:
            w_start, w_end = window
            if earliest.hour < w_start:
                earliest = earliest.replace(hour=w_start, minute=0, second=0, microsecond=0)
            elif earliest.hour >= w_end:
                earliest = (earliest + timedelta(days=1)).replace(
                    hour=w_start, minute=0, second=0, microsecond=0
                )

        if earliest <= now_local:
            return None
        return earliest.astimezone(timezone.utc).replace(tzinfo=None)

    def _missed_today_gate(self, agent_contact, task, agent_config, db) -> Optional[datetime]:
        """
        Подстраховка при исполнении (ветка 2110-client-memory): сегодня этому
        клиенту уже не дозвонились (строки sip_calls, в том числе ручной звонок со
        страницы «Телефония») и он с тех пор на связь не выходил — второй звонок в
        тот же день не набираем, переносим на завтра к началу окна звонков.
        Задачи на сегодня тулзы агента уже не ставят; это ловит те, что попали на
        сегодня иначе (перенос времени, задача, поставленная до недозвона).
        """
        from backend.core.timezone_utils import BISHKEK
        from backend.services.agent_contact_phones import missed_call_today
        from backend.services.agent_tools import call_window, CALL_WINDOW_START_HOUR

        today = datetime.now(timezone.utc).astimezone(BISHKEK).date()
        missed = missed_call_today(db, agent_contact, task.user_id, today)
        if missed is None:
            return None
        # Клиент после недозвона сам написал / позвонил, или звонок разминулся с его
        # сообщением «позвоните после 18» — та же проверка, что при постановке задачи
        # (agent_tools.missed_call_excused). Раньше планировщик переносил на завтра и такие.
        from backend.services.agent_tools import missed_call_excused
        if missed_call_excused(db, agent_contact, missed.created_at, datetime.now(timezone.utc)):
            return None
        window = call_window(agent_config)
        start_hour = window[0] if window is not None else CALL_WINDOW_START_HOUR
        tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=BISHKEK)
        logger.info(
            f"[TASK-SCHEDULER] Агентский звонок {task.id}: сегодня клиенту {agent_contact.id} уже "
            f"не дозвонились (звонок {missed.id}) — второй в тот же день не набираем"
        )
        return tomorrow.replace(hour=start_hour).astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _claim_task(db: Session, task: Task) -> bool:
        """
        Атомарно занять задачу: SCHEDULED → PENDING одним UPDATE (ветка 1111-clean-system).

        Планировщик запущен в каждом из 4 воркеров gunicorn. Раньше задача
        помечалась «в работе» обычным присваиванием после чтения — два воркера,
        прочитавшие её в один тик, набирали клиента дважды. Теперь выполняет
        только тот воркер, чей UPDATE изменил строку.
        """
        claimed = db.query(Task).filter(
            Task.id == task.id,
            Task.status == TaskStatus.SCHEDULED,
        ).update({"status": TaskStatus.PENDING}, synchronize_session=False)
        db.commit()
        if not claimed:
            return False
        db.refresh(task)
        return True

    @staticmethod
    def _release_task(db: Session, task: Task, when: Optional[datetime] = None) -> None:
        """Вернуть занятую задачу в очередь (перенос по окну звонков, агент выключен)."""
        task.status = TaskStatus.SCHEDULED
        if when is not None:
            task.scheduled_time = when
        db.commit()

    async def execute_agent_task(self, task: Task, db: Session):
        """
        Execute an agent task: create AgentCall, run PreCall with AgentContact,
        then initiate the call and launch PostCall.
        """
        try:
            if not self._claim_task(db, task):
                logger.info(f"[TASK-SCHEDULER] Agent task {task.id} already taken by another worker")
                return
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
                self._release_task(db, task)
                return  # не помечаем как failed — просто пропускаем, ждём активации

            # B1+B2 (карта): детерминированный предохранитель перед дозвоном —
            # окно 09–20 Бишкек + анти-долбёж. Только для звонков; telegram-задачи
            # идут своим путём (сообщение, а не звонок). Задача остаётся SCHEDULED
            # и переносится на разрешённое время.
            # Звонок по прямой команде владельца (кнопка «Позвонить» в инбоксе, ветка
            # 0411-inbox) — владелец сам решил звонить сейчас: окно звонков агента и
            # анти-долбёж его не переносят (они ограничивают инициативу агента).
            owner_command = getattr(task, "created_by", None) == "owner"
            if (task.channel or "call") != "telegram" and not owner_command:
                postpone_to = self._dial_gate(agent_contact, datetime.utcnow(), agent_config, db)
                if postpone_to is None:
                    postpone_to = self._missed_today_gate(agent_contact, task, agent_config, db)
                if postpone_to is not None:
                    self._release_task(db, task, postpone_to)
                    logger.info(
                        f"[TASK-SCHEDULER] ⏳ Agent task {task.id} перенесена на {postpone_to} UTC "
                        f"(окно звонков агента / анти-долбёж, контакт {agent_contact.id})"
                    )
                    return

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
            # Старт (reactive) ставит отложенные сообщения в Telegram: для них достаточно доступа
            # к чату агента, звонки — при полном доступе, а звонок по команде владельца
            # (кнопка «Позвонить» в инбоксе) — тоже при доступе к боту.
            has_access = (
                user.has_agent_chat_access() if (task.channel or "call") == "telegram"
                else (user.has_active_agent_subscription()
                      or (owner_command and user.has_agent_chat_access()))
            )
            if not has_access:
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

            call_session_id = None
            call_success = False

            # Звонки идут только через собственный SIP-шлюз (Voximplant выключен, ветка 1111-clean-system
            # убрала его запасные пути: раньше задача без SIP-номера пыталась звонить через Voximplant).
            sip_number = self._sip_number_for(
                user, task, assistant_type, db,
                preferred=getattr(task, "caller_id", None) or (agent_config.default_caller_id if agent_config else None),
            )
            if sip_number is None:
                logger.warning(f"[TASK-SCHEDULER] ❌ No SIP number with outbound for agent task {task.id}")
                task.status = TaskStatus.FAILED
                task.call_result = NO_OUTBOUND_NUMBER_MESSAGE
                agent_call.status = "failed"
                agent_call.call_result = NO_OUTBOUND_NUMBER_MESSAGE
                db.commit()
                return
            call_session_id, call_success = await self._agent_call_via_sip_gateway(
                task, agent_contact, sip_number, assistant_id, assistant_name, assistant_type, db
            )

            if call_success and call_session_id and sip_number is not None:
                # SIP-шлюз: звонок только в очереди. Итог (COMPLETED/FAILED) проставит событие моста,
                # PostCall запустит AgentCallFinalizer по концу звонка — опрашивать базу не нужно.
                agent_call.call_session_id = str(call_session_id)
                task.call_session_id = str(call_session_id)
                task.status = TaskStatus.PENDING  # 'в работе' (CALLING в проде в enum может не быть)
                task.call_result = f"Agent call queued on SIP gateway. Session: {call_session_id}"
                db.commit()
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
                postcall_log={"call_direction": "telegram_outbound"},  # не звонок (0311-call-guard)
            )
            db.add(agent_call)
            db.flush()
            task.agent_call_id = agent_call.id
            db.commit()

            # Владелец ведёт переписку с клиентом сам (или остановил агента):
            # сообщение не отправляем, в истории агента — пометка почему.
            from backend.services import agent_pause
            if agent_pause.is_paused(agent_contact):
                note = (f"Не отправлено: {agent_pause.describe(agent_contact)} "
                        f"(владелец ведёт переписку с клиентом сам).")
                agent_call.status = "answered"
                agent_call.post_call_decision = "SUCCESS"
                agent_call.transcript = (task.description or task.title or "").strip()
                agent_call.call_result = note
                agent_call.completed_at = datetime.utcnow()
                agent_call.postcall_log = {
                    "call_direction": "telegram_outbound",
                    "message_sent": False,
                    "skipped_reason": "ai_paused",
                    "note": note,
                    "tool_calls": [],
                    "final_decision": "SUCCESS",
                    "analyzed_at": datetime.utcnow().isoformat(),
                }
                task.status = TaskStatus.COMPLETED
                task.call_result = json.dumps({"skipped": "ai_paused", "note": note}, ensure_ascii=False)
                task.call_completed_at = datetime.utcnow()
                db.commit()
                logger.info(f"[TASK-SCHEDULER] ✉️ Telegram task {task.id} skipped: contact {agent_contact.id} paused")
                return

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

    # =========================================================================
    # ✅ Собственный SIP-шлюз (infra/sip-gateway): исходящие без Voximplant
    # =========================================================================

    def _sip_number_for(self, user, task, assistant_type: str, db, preferred: Optional[str] = None):
        """
        Номер оператора для исходящего через SIP-шлюз, либо None (тогда Voximplant).
        Условия: у пользователя есть активный номер с разрешёнными исходящими и
        тип ассистента поддерживается телефонным хендлером (openai, gemini, fish, eleven).
        """
        try:
            from backend.models.sip_gateway import SIP_SUPPORTED_ASSISTANT_TYPES
            from backend.services.sip_gateway_service import SipGatewayService
            if assistant_type not in SIP_SUPPORTED_ASSISTANT_TYPES:
                return None
            return SipGatewayService.outbound_number_for_user(db, user.id, preferred or getattr(task, "caller_id", None))
        except Exception as e:
            logger.warning(f"[TASK-SCHEDULER] SIP gateway lookup failed: {e}")
            return None

    def _sip_call_metadata(self, task, contact_name: str) -> dict:
        return {
            k: v for k, v in {
                "contact_name": contact_name or "",
                "task_title": task.title or "",
                "task_description": task.description or "",
                "custom_greeting": task.custom_greeting or "",
                "source": "task_scheduler",
            }.items() if v
        }

    async def _agent_call_via_sip_gateway(
        self, task, agent_contact, sip_number, assistant_id, assistant_name, assistant_type, db
    ) -> Tuple[Optional[str], bool]:
        """Поставить исходящий агентский звонок в очередь SIP-шлюза. Итог звонка проставит событие моста."""
        from backend.services.sip_gateway_service import SipGatewayService
        try:
            metadata = self._sip_call_metadata(task, agent_contact.name or "")
            # Стратегия PreCall доходит до голосового агента (в контекст звонка), не только до PostCall
            agent_call = db.get(AgentCall, task.agent_call_id) if task.agent_call_id else None
            if agent_call is not None and agent_call.call_strategy:
                metadata["call_strategy"] = agent_call.call_strategy[:1500]
            # Номер для звонка: второй номер на O!, если клиент дал его в переписке (ветка 2110-client-memory).
            from backend.services.agent_contact_phones import dial_number
            to_number = dial_number(agent_contact)
            call = SipGatewayService.queue_outbound_call(
                db,
                user_id=task.user_id,
                to_number=to_number,
                caller_number=sip_number,
                assistant_type=assistant_type,
                assistant_id=assistant_id,
                metadata=metadata,
                task_id=task.id,
            )
            logger.info(f"[TASK-SCHEDULER] 📞 SIP call queued: {sip_number.phone_number} -> {to_number} ({assistant_name}), call {call.id}")
            return str(call.id), True
        except ValueError as e:
            # Номер не проходит через транк (не O!) — причина для пользователя, не сбой
            logger.warning(f"[TASK-SCHEDULER] SIP call rejected for task {task.id}: {e}")
            task.call_result = str(e)
            return None, False
        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] ❌ SIP gateway queue failed: {e}", exc_info=True)
            task.call_result = f"SIP gateway error: {e}"
            return None, False

    async def _execute_via_sip_gateway(self, task, contact, sip_number, assistant_id, assistant_type, db) -> None:
        """Обычная (не агентская) задача через SIP-шлюз. Статус задачи доводит событие моста."""
        from backend.services.sip_gateway_service import SipGatewayService
        try:
            call = SipGatewayService.queue_outbound_call(
                db,
                user_id=task.user_id,
                to_number=contact.phone,
                caller_number=sip_number,
                assistant_type=assistant_type,
                assistant_id=assistant_id,
                metadata=self._sip_call_metadata(task, getattr(contact, "name", "") or ""),
                task_id=task.id,
            )
            task.call_session_id = str(call.id)
            task.status = TaskStatus.COMPLETED
            task.call_completed_at = datetime.utcnow()
            task.call_result = f"Call queued on SIP gateway. Session: {call.id}"
            db.commit()
        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] ❌ SIP gateway queue failed: {e}", exc_info=True)
            task.status = TaskStatus.FAILED
            task.call_result = f"SIP gateway error: {e}"
            db.commit()

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
            if not self._claim_task(db, task):
                return
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
            
            # Только собственный SIP-шлюз (Asterisk + мост); Voximplant выключен.
            sip_number = self._sip_number_for(user, task, assistant_type, db)
            if sip_number is None:
                logger.warning(f"[TASK-SCHEDULER] ❌ No SIP number with outbound for task {task.id}")
                task.status = TaskStatus.FAILED
                task.call_result = NO_OUTBOUND_NUMBER_MESSAGE
                db.commit()
                return
            logger.info(f"[TASK-SCHEDULER] 📞 Using SIP GATEWAY integration (number {sip_number.phone_number})")
            await self._execute_via_sip_gateway(
                task=task,
                contact=contact,
                sip_number=sip_number,
                assistant_id=assistant_id,
                assistant_type=assistant_type,
                db=db
            )

        except Exception as e:
            logger.error(f"[TASK-SCHEDULER] Error executing task {task.id}: {e}", exc_info=True)
            
            # Помечаем задачу как failed
            try:
                task.status = TaskStatus.FAILED
                task.call_result = f"Internal error: {str(e)}"
                db.commit()
            except Exception as commit_error:
                logger.error(f"[TASK-SCHEDULER] Failed to update task status: {commit_error}")
    
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
