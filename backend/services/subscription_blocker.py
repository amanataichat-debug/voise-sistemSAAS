"""
Subscription blocker — фоновая задача отмены запланированных звонков агента
у пользователей, потерявших доступ к агенту (v3.1).

Доступ к агенту в модели v3.1 динамический: тестовый период (3 дня) ИЛИ тариф
`profi` ИЛИ legacy-тариф `agent` ИЛИ админ. Поэтому блокер НЕ ставит
постоянный флаг agent_subscription_blocked (иначе апгрейд на profi не разблокирует
юзера). Вместо этого он просто отменяет SCHEDULED agent-задачи у тех, кто прямо
сейчас доступа не имеет — чтобы звонки не выполнялись и не копились.

Флаг agent_subscription_blocked остаётся ручным kill-switch (админ/саппорт).
Кредиты при отмене задач НЕ сгорают.

Запускается каждые 5 минут.
"""

import asyncio

from backend.core.logging import get_logger
from backend.db.session import SessionLocal
from backend.models.user import User
from backend.models.task import Task, TaskStatus

logger = get_logger(__name__)

CHECK_INTERVAL_SEC = 5 * 60  # каждые 5 минут


async def check_expired_agent_subscriptions():
    """Один проход: отмена SCHEDULED agent-задач у юзеров без доступа к агенту."""
    db = SessionLocal()
    try:
        # Уникальные пользователи, у которых есть запланированные agent-звонки
        user_ids = [
            row[0]
            for row in (
                db.query(Task.user_id)
                .filter(
                    Task.is_agent_task == True,  # noqa: E712
                    Task.status == TaskStatus.SCHEDULED,
                )
                .distinct()
                .all()
            )
            if row[0] is not None
        ]
        if not user_ids:
            return

        users = db.query(User).filter(User.id.in_(user_ids)).all()

        total_cancelled = 0
        affected = 0
        for user in users:
            if user.has_agent_access():
                continue

            tasks_cancelled = (
                db.query(Task)
                .filter(
                    Task.user_id == user.id,
                    Task.is_agent_task == True,  # noqa: E712
                    Task.status == TaskStatus.SCHEDULED,
                )
                .update({"status": TaskStatus.CANCELLED}, synchronize_session=False)
            )
            if tasks_cancelled:
                affected += 1
                total_cancelled += tasks_cancelled
                logger.warning(
                    f"[BLOCKER] User {user.id} lost agent access — cancelled "
                    f"{tasks_cancelled} scheduled tasks"
                )

        if total_cancelled:
            db.commit()
            logger.info(
                f"[BLOCKER] Cancelled {total_cancelled} scheduled agent task(s) "
                f"for {affected} user(s) without access"
            )

    except Exception as e:
        db.rollback()
        logger.error(f"[BLOCKER] Error cancelling tasks for users without agent access: {e}", exc_info=True)
    finally:
        db.close()


async def start_subscription_blocker():
    """Фоновый раннер — запускает проверку каждые 5 минут."""
    logger.info("[BLOCKER] Starting agent subscription blocker (every 5 min)")
    # Небольшая задержка, чтобы дать приложению подняться
    await asyncio.sleep(60)
    while True:
        try:
            await check_expired_agent_subscriptions()
        except Exception as e:
            logger.error(f"[BLOCKER] Unhandled error: {e}", exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_SEC)
