"""
Scheduler module for background tasks in WellcomeAI application.
Contains background tasks for checking subscription expirations and other recurring tasks.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text
from backend.db.session import SessionLocal
from backend.models.user import User
from backend.core.logging import get_logger

logger = get_logger(__name__)

# Глобальная переменная для контроля работы планировщика
_scheduler_running = False

async def check_expired_subscriptions():
    """
    Background task to check and update expired subscription statuses with protection against duplication
    """
    global _scheduler_running
    
    # Предотвращаем множественные запуски на уровне процесса
    if _scheduler_running:
        logger.warning("Subscription checker is already running in this process, skipping...")
        return
    
    _scheduler_running = True
    db = SessionLocal()
    lock_acquired = False
    
    try:
        # Используем PostgreSQL advisory lock для предотвращения запуска в других воркерах
        try:
            # Пытаемся получить эксклюзивную блокировку (12345 - произвольное число для нашей блокировки)
            result = db.execute(text("SELECT pg_try_advisory_lock(12345)")).scalar()
            lock_acquired = result
            
            if not lock_acquired:
                logger.info("Another worker is already checking subscriptions, skipping...")
                return
            
            logger.info("Acquired lock for subscription checking")
            
            now = datetime.now(timezone.utc)
            processed_count = 0
            already_processed = 0
            
            # Получаем истекшие подписки пакетами для экономии памяти
            batch_size = 50
            offset = 0
            
            while True:
                # Находим пользователей с истекшими подписками, которые еще не были обработаны
                expired_users_query = db.query(User).filter(
                    and_(
                        User.subscription_end_date.isnot(None),
                        User.subscription_end_date <= now,
                        or_(User.is_trial == True, User.subscription_plan_id.isnot(None))
                    )
                ).limit(batch_size).offset(offset)
                
                expired_users = expired_users_query.all()
                
                if not expired_users:
                    break
                
                for user in expired_users:
                    try:
                        # Проверяем, не обработали ли мы уже эту подписку за последние 24 часа
                        from backend.models.subscription import SubscriptionEventLog
                        recent_expire_event = db.query(SubscriptionEventLog).filter(
                            and_(
                                SubscriptionEventLog.user_id == user.id,
                                SubscriptionEventLog.event_type == "expire",
                                SubscriptionEventLog.created_at > now - timedelta(hours=24)
                            )
                        ).first()
                        
                        if recent_expire_event:
                            already_processed += 1
                            logger.debug(f"User {user.id} already processed in last 24h, skipping")
                            continue
                        
                        # Log the expiration event
                        from backend.services.subscription_service import SubscriptionService
                        await SubscriptionService.log_subscription_event(
                            db=db,
                            user_id=str(user.id),
                            action="expire",
                            plan_id=str(user.subscription_plan_id) if user.subscription_plan_id else None,
                            details=f"Subscription expired on {now.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        
                        # Сбрасываем флаги активности, но СОХРАНЯЕМ даты для истории
                        user.is_trial = False
                        # НЕ ТРОГАЕМ subscription_end_date - оставляем для истории!
                        # НЕ ТРОГАЕМ subscription_start_date - оставляем для истории!
                        
                        # Send notification about expiration
                        try:
                            from backend.services.notification_service import NotificationService
                            await NotificationService.send_subscription_expired_notice(user)
                            logger.info(f"Subscription expired for user {user.id}, email: {user.email}")
                        except Exception as notif_error:
                            logger.error(f"Error sending expiration notification for user {user.id}: {notif_error}")
                        
                        processed_count += 1
                        
                    except Exception as user_error:
                        logger.error(f"Error processing user {user.id}: {user_error}")
                        continue
                
                # Коммитим после каждого пакета
                if processed_count > 0:
                    db.commit()
                
                offset += batch_size
                
                # Даем системе передохнуть между пакетами
                await asyncio.sleep(0.1)
            
            if processed_count > 0:
                logger.info(f"✅ Processed {processed_count} expired subscriptions")
            if already_processed > 0:
                logger.info(f"ℹ️ Skipped {already_processed} already processed subscriptions")
            
            # Check for subscriptions that are about to expire (за 3 дня)
            try:
                from backend.services.notification_service import NotificationService
                await NotificationService.check_subscription_expirations(db)
            except Exception as notif_error:
                logger.error(f"Error checking subscription expirations: {notif_error}")
        
        finally:
            # ОБЯЗАТЕЛЬНО освобождаем блокировку
            if lock_acquired:
                try:
                    db.execute(text("SELECT pg_advisory_unlock(12345)"))
                    db.commit()
                    logger.info("Released subscription checker lock")
                except Exception as unlock_error:
                    logger.error(f"Error releasing lock: {unlock_error}")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Critical error during subscription check: {str(e)}", exc_info=True)
    finally:
        _scheduler_running = False
        db.close()

async def start_subscription_checker():
    """
    Start the subscription checker background task
    """
    logger.info("Starting subscription checker background task")
    
    # Ждем 30 секунд перед первой проверкой, чтобы дать приложению полностью запуститься
    await asyncio.sleep(30)
    
    while True:
        try:
            logger.info("🔄 Running scheduled subscription check...")
            await check_expired_subscriptions()
            logger.info("✅ Scheduled subscription check completed")
        except Exception as e:
            logger.error(f"Unhandled error in subscription checker: {str(e)}", exc_info=True)
        
        # Проверяем раз в час (3600 секунд)
        logger.info("💤 Sleeping for 1 hour until next check...")
        await asyncio.sleep(3600)
