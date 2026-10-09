"""
WalletMeter — оплата минут голосовой модели из кошелька владельца ассистента.

Один экземпляр на разговор (виджет или звонок, в т.ч. агента обзвона):
  1. prepare()  — до подключения провайдеров: бесплатный ли владелец, баланс (с подарком
     на тест при первом обращении). Пустой кошелёк → разговор не начинается.
  2. start()    — с момента «соединение установлено»: сторож раз в WATCH_SEC считает
     стоимость прошедших секунд; когда она догоняет баланс на старте — пишет в журнал,
     шлёт клиенту ошибку WALLET_EMPTY и закрывает сокет (телефон — кладёт трубку).
  3. finish()   — одно списание за фактические секунды (WalletService.charge_call).

БД — в потоке (asyncio.to_thread) своей сессией: синхронные запросы в event loop
останавливали звук звонка.
"""

import asyncio
import time
from typing import Optional

from backend.core.logging import get_logger
from backend.websockets.call_log import CallLogRecorder

logger = get_logger(__name__)

WATCH_SEC = 10
EMPTY_MESSAGE = "Баланс кошелька исчерпан. Пополните кошелёк, чтобы продолжить разговор."


class WalletMeter:
    def __init__(self, user_id, engine: str, assistant_id: str, telephony: bool) -> None:
        self.user_id = user_id
        self.engine = engine
        self.assistant_id = assistant_id
        self.channel = "phone" if telephony else "widget"
        self.free = True
        self.balance = 0           # тыйын на старте
        self.charged = 0
        self.started_at: Optional[float] = None
        self.stopped_by_wallet = False
        self._task: Optional[asyncio.Task] = None
        self._done = False

    # ------------------------------------------------------------------ до старта
    async def prepare(self) -> bool:
        """True — можно говорить. Ошибка БД не блокирует разговор (звонок важнее)."""
        if not self.user_id:
            return True

        def run():
            from backend.db.session import SessionLocal
            from backend.models.user import User
            from backend.services.wallet_service import WalletService, is_free_user
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == self.user_id).first()
                if user is None or is_free_user(user):
                    return True, 0
                return False, WalletService.get_balance(db, self.user_id)
            finally:
                db.close()

        try:
            self.free, self.balance = await asyncio.to_thread(run)
        except Exception as exc:
            logger.error(f"[WALLET] balance check failed for user {self.user_id}: {exc}")
            self.free = True
            return True
        return self.free or self.balance > 0

    # ------------------------------------------------------------------ разговор
    def start(self, websocket) -> None:
        self.started_at = time.monotonic()
        if not self.free:
            self._task = asyncio.create_task(self._watch(websocket))

    def elapsed(self) -> float:
        return 0.0 if self.started_at is None else time.monotonic() - self.started_at

    async def _watch(self, websocket) -> None:
        from backend.services.wallet_service import cost_tyiyn
        try:
            while True:
                await asyncio.sleep(WATCH_SEC)
                if cost_tyiyn(self.engine, self.elapsed() + WATCH_SEC / 2) < self.balance:
                    continue
                self.stopped_by_wallet = True
                log = CallLogRecorder.current()
                if log is not None:
                    log.add("wallet", f"Баланс кошелька исчерпан через {self.elapsed():.0f} с — разговор завершён",
                            level="warning")
                logger.warning(f"[WALLET] user {self.user_id}: balance exhausted after {self.elapsed():.0f}s, closing")
                try:
                    await websocket.send_json({"type": "error", "error": {
                        "code": "WALLET_EMPTY", "message": EMPTY_MESSAGE, "requires_payment": True}})
                except Exception:
                    pass
                try:
                    await websocket.close(code=1000)
                except Exception:
                    pass
                return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ после
    async def finish(self, session_id: Optional[str]) -> None:
        if self._done:
            return
        self._done = True
        if self._task is not None:
            self._task.cancel()
        if self.started_at is None or not self.user_id:
            return
        seconds = self.elapsed()

        def run():
            from backend.db.session import SessionLocal
            from backend.services.wallet_service import WalletService
            db = SessionLocal()
            try:
                return WalletService.charge_call(db, self.user_id, engine=self.engine, seconds=seconds,
                                                 session_id=session_id, assistant_id=self.assistant_id,
                                                 channel=self.channel)
            finally:
                db.close()

        try:
            self.charged, balance_after = await asyncio.to_thread(run)
        except Exception as exc:
            logger.error(f"[WALLET] charge failed for user {self.user_id} ({seconds:.0f}s, {self.engine}): {exc}")
            return
        log = CallLogRecorder.current()
        if log is not None and self.charged:
            log.add("wallet", f"Списано с кошелька {self.charged / 100:.2f} сом за {seconds:.0f} с "
                              f"({self.engine}), остаток {balance_after / 100:.2f} сом")
