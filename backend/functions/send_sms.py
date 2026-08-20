# backend/functions/send_sms.py
"""
Функция отправки SMS через Voximplant Management API.

Gemini вызывает эту функцию во время звонка, чтобы отправить SMS клиенту
с того номера Voximplant, на который он позвонил.
"""

import httpx
import uuid
from typing import Dict, Any
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function
from backend.core.logging import get_logger

logger = get_logger(__name__)


@register_function
class SendSmsFunction(FunctionBase):
    """
    Отправка SMS клиенту через Voximplant.

    Gemini знает оба номера из system prompt:
    - caller_number (номер клиента) → параметр "to"
    - called_number (наш номер Voximplant) → параметр "from_number"
    """

    @classmethod
    def get_name(cls) -> str:
        return "send_sms"

    @classmethod
    def get_display_name(cls) -> str:
        return "Отправить SMS"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Отправить SMS-сообщение клиенту во время телефонного звонка. "
            "Используй эту функцию когда клиент просит отправить информацию по SMS "
            "(адрес, ссылку, код, подтверждение и т.д.). "
            "Параметр 'to' — это caller_number (номер клиента) из информации о звонке. "
            "Параметр 'from_number' — это called_number (номер Voximplant, на который позвонил клиент) из информации о звонке. "
            "Оба номера доступны в системном промпте."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Текст SMS. До 160 символов — одно сообщение.",
                    "maxLength": 500
                },
                "to": {
                    "type": "string",
                    "description": "Номер получателя — caller_number из информации о звонке"
                },
                "from_number": {
                    "type": "string",
                    "description": "Номер отправителя — called_number из информации о звонке (номер Voximplant на который позвонил клиент)"
                }
            },
            "required": ["text", "to", "from_number"]
        }

    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p><strong>📱 Отправка SMS клиенту во время звонка</strong></p>

<p>Используй функцию <code>send_sms</code> когда клиент просит отправить информацию по SMS.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>Клиент просит отправить адрес, ссылку, контакты</li>
    <li>Нужно отправить код подтверждения или номер заказа</li>
    <li>Клиент просит продублировать информацию в SMS</li>
    <li>Любая ситуация когда клиент говорит "пришли на SMS" / "отправь сообщением"</li>
</ul>

<p><strong>Параметры:</strong></p>
<ul>
    <li><code>text</code> — текст SMS (обязательно, до 500 символов)</li>
    <li><code>to</code> — номер клиента = <strong>caller_number</strong> из информации о звонке (обязательно)</li>
    <li><code>from_number</code> — наш номер = <strong>called_number</strong> из информации о звонке (обязательно)</li>
</ul>

<p><strong>Пример вызова:</strong></p>
<pre>{
  "text": "Адрес: ул. Ленина 5, офис 301. Время работы: 9:00-18:00",
  "to": "79161234567",
  "from_number": "79011471908"
}</pre>

<p><strong>⚠️ Важно:</strong></p>
<ul>
    <li>Оба номера (to и from_number) доступны в системном промпте</li>
    <li>SMS отправляется с номера на который позвонил клиент</li>
    <li>Функция работает только при телефонных звонках через Voximplant</li>
    <li>До 160 символов — одно SMS, больше — будет разбито на несколько</li>
</ul>
"""

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Отправляет SMS через Voximplant Management API.

        Цепочка: assistant_id → user_id → VoximplantChildAccount → vox_account_id + vox_api_key
        """
        # Импорты внутри функции для избежания circular imports
        from backend.db.session import SessionLocal
        from backend.models.voximplant_child import VoximplantChildAccount
        from backend.api.telephony import find_assistant_by_id

        text = arguments.get("text", "").strip()

        # Номера берём в первую очередь из call_data (их проставляет сам сценарий
        # по данным звонка), а не из аргументов модели — модель нередко
        # галлюцинирует и подставляет строки вроде "caller_number"/"called_number".
        # Аргументы модели — только фолбэк для обратной совместимости.
        context = context or {}
        call_data = context.get("call_data", {}) or {}

        def _clean(num):
            return str(num or "").replace("+", "").strip()

        to_number = _clean(call_data.get("caller_number")) or _clean(arguments.get("to"))
        from_number = _clean(call_data.get("called_number")) or _clean(arguments.get("from_number"))

        # Валидация
        if not text:
            return {"success": False, "error": "Текст SMS не может быть пустым"}

        if not to_number:
            return {"success": False, "error": "Не указан номер получателя (to)"}

        if not from_number:
            return {"success": False, "error": "Не указан номер отправителя (from_number)"}

        # Получаем assistant_id из context или arguments
        assistant_id_str = call_data.get("assistant_id") or arguments.get("assistant_id")

        if not assistant_id_str:
            return {"success": False, "error": "assistant_id не найден в контексте звонка"}

        try:
            assistant_id = uuid.UUID(str(assistant_id_str))
        except (ValueError, TypeError):
            return {"success": False, "error": f"Невалидный assistant_id: {assistant_id_str}"}

        db = SessionLocal()
        try:
            # Находим ассистента и его user_id
            assistant, assistant_type, user_id = find_assistant_by_id(db, assistant_id)

            if not assistant:
                return {"success": False, "error": f"Ассистент {assistant_id} не найден"}

            # Находим VoximplantChildAccount по user_id
            child_account = db.query(VoximplantChildAccount).filter(
                VoximplantChildAccount.user_id == user_id
            ).first()

            if not child_account:
                return {"success": False, "error": "Voximplant аккаунт не найден для данного пользователя"}

            if not child_account.vox_account_id or not child_account.vox_api_key:
                return {"success": False, "error": "Voximplant credentials не настроены"}

            # Отправляем SMS через Voximplant API
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.voximplant.com/platform_api/SendSmsMessage/",
                    params={
                        "account_id": child_account.vox_account_id,
                        "api_key": child_account.vox_api_key,
                        "source": from_number,
                        "destination": to_number,
                        "sms_body": text
                    }
                )

            data = resp.json()

            if data.get("result") == 1:
                transaction_id = data.get("transaction_id")
                logger.info(f'[SMS] ✅ {from_number} → {to_number}: "{text[:50]}" (tx: {transaction_id})')
                # Сохраняем исходящее SMS в общий тред (sms_messages) — единый
                # механизм с агентом: полная переписка для контекста и карточки.
                from backend.services.sms_history import store_outbound_sms
                store_outbound_sms(db, child_account.id, child_account.vox_account_id, from_number, to_number, text)
                return {
                    "success": True,
                    "message": "SMS успешно отправлено",
                    "transaction_id": transaction_id
                }
            else:
                error_msg = data.get("error", {}).get("msg", str(data))
                logger.error(f'[SMS] ❌ {from_number} → {to_number}: "{text[:50]}" — {error_msg}')
                return {"success": False, "error": f"Ошибка Voximplant: {error_msg}"}

        except Exception as e:
            logger.error(f"[SMS] execute error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            db.close()
