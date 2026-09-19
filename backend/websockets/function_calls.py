# backend/websockets/function_calls.py
"""
Общий асинхронный исполнитель AI-функций для голосовых хендлеров.

Используется хендлерами OpenAI GPT-Live (handler_live) и Fish (handler_fish):
функция выполняется в фоне, не блокируя речь ассистента, результат уходит
провайдеру через `client.send_function_result(call_id, result)`, а в
function_logs пишется запись с привязкой к `client.conversation_record_id`.

Ожидаемый интерфейс `client`:
    assistant_config, client_id, db_session, conversation_record_id,
    async send_function_result(call_id, result) -> {"success": bool, "error": str|None}

Клиенту (виджет / SIP-адаптер) уходят события протокола виджета:
    llm_result (для query_llm), function_call.completed,
    function_call.delivery_error, function_call.error
"""

import asyncio
import json
import time
import traceback
from typing import Any, Dict, Optional

from backend.core.logging import get_logger
from backend.functions import execute_function
from backend.services.conversation_service import ConversationService
from backend.services.function_log_service import FunctionLogService
from backend.services.google_sheets_service import GoogleSheetsService

logger = get_logger(__name__)


async def async_save_to_google_sheets(sheet_id: str, user_message: str, assistant_message: str,
                                      function_result=None, conversation_id: Optional[str] = None,
                                      context: str = "") -> None:
    """Запись строки в Google Sheets ассистента (в фоне, ошибки только в лог)."""
    if not sheet_id:
        return
    try:
        started = time.time()
        ok = await GoogleSheetsService.log_conversation(
            sheet_id=sheet_id,
            user_message=user_message,
            assistant_message=assistant_message,
            function_result=function_result,
            conversation_id=conversation_id,
        )
        logger.info(f"[FUNC] Google Sheets {'ok' if ok else 'failed'} ({time.time() - started:.2f}s) - {context}")
    except Exception as exc:
        logger.error(f"[FUNC] Google Sheets error ({context}): {exc}\n{traceback.format_exc()}")


async def async_save_dialog_to_db(assistant_id: str, user_message: str, assistant_message: str,
                                  session_id: str, client_info: Optional[Dict[str, Any]] = None,
                                  audio_duration: Optional[float] = None) -> None:
    """Запись хода диалога отдельной сессией БД (исходная сессия хендлера может быть закрыта)."""
    from backend.db.session import SessionLocal

    if not user_message and not assistant_message:
        return
    db = None
    try:
        db = SessionLocal()
        await ConversationService.save_conversation(
            db=db,
            assistant_id=assistant_id,
            user_message=user_message or "",
            assistant_message=assistant_message or "",
            session_id=session_id,
            caller_number=None,
            client_info=client_info,
            audio_duration=audio_duration,
            tokens_used=0,
        )
    except Exception as exc:
        logger.error(f"[FUNC] dialog save failed: {exc}\n{traceback.format_exc()}")
    finally:
        if db:
            db.close()


async def async_save_function_log(function_name: str, arguments: dict, result: dict, status: str,
                                  execution_time_ms: float, user_id: Optional[str] = None,
                                  assistant_id: Optional[str] = None, conversation_id: Optional[str] = None,
                                  error_message: Optional[str] = None) -> None:
    """Запись в function_logs отдельной сессией БД."""
    from backend.db.session import SessionLocal

    db = None
    try:
        db = SessionLocal()
        entry = await FunctionLogService.log_function_call(
            db=db,
            function_name=function_name,
            arguments=arguments,
            result=result,
            status=status,
            execution_time_ms=execution_time_ms,
            user_id=user_id,
            assistant_id=assistant_id,
            conversation_id=conversation_id,
            error_message=error_message,
        )
        if entry is None:
            logger.warning(f"[FUNC-LOG] save returned None for {function_name}")
    except Exception as exc:
        logger.error(f"[FUNC-LOG] error saving {function_name}: {exc}\n{traceback.format_exc()}")
    finally:
        if db:
            db.close()


async def _send(websocket, payload: Dict[str, Any]) -> None:
    try:
        await websocket.send_json(payload)
    except Exception as exc:
        logger.warning(f"[FUNC] send to client failed ({payload.get('type')}): {exc}")


async def execute_and_send_function_result(
    openai_client: Any,
    websocket,
    function_call_id: str,
    function_name: str,
    arguments: dict,
    context: dict,
    user_transcript: str = "",
) -> None:
    """
    Выполнить функцию в фоне и вернуть результат провайдеру.

    Ход:
      1. execute_function (может занимать секунды, ассистент тем временем говорит)
      2. query_llm → сразу llm_result на фронт
      3. Google Sheets + function_logs в фоне (conversation_id из клиента)
      4. client.send_function_result → провайдер продолжает ответ
      5. фронту function_call.completed / function_call.delivery_error
    """
    started = time.time()
    config = getattr(openai_client, "assistant_config", None)
    conversation_id = getattr(openai_client, "conversation_record_id", None)
    user_id = str(config.user_id) if config is not None and getattr(config, "user_id", None) else None
    assistant_id = str(config.id) if config is not None else None

    try:
        logger.info(f"[FUNC] {function_name}({json.dumps(arguments, ensure_ascii=False)[:200]}) "
                    f"call_id={function_call_id} conversation={conversation_id}")
        result = await execute_function(name=function_name, arguments=arguments, context=context)
        execution_time = time.time() - started
        logger.info(f"[FUNC] {function_name} done in {execution_time:.2f}s: {str(result)[:200]}")

        if function_name == "query_llm":
            if isinstance(result, dict):
                content = result.get("full_response", result.get("response", result.get("answer", str(result))))
                model = result.get("model_used", result.get("model", "gpt-4"))
            else:
                content, model = str(result), "gpt-4"
            await _send(websocket, {
                "type": "llm_result", "content": content, "model": model, "function": function_name,
                "execution_time": execution_time, "timestamp": time.time(), "async_execution": True,
            })

        sheet_id = getattr(config, "google_sheet_id", None) if config is not None else None
        if sheet_id:
            asyncio.create_task(async_save_to_google_sheets(
                sheet_id=sheet_id,
                user_message=user_transcript or f"[Function call: {function_name}]",
                assistant_message=f"[Async function executed: {function_name}]",
                function_result=result,
                conversation_id=conversation_id,
                context="function call",
            ))

        asyncio.create_task(async_save_function_log(
            function_name=function_name,
            arguments=arguments,
            result=result if isinstance(result, dict) else {"result": str(result)},
            status="success",
            execution_time_ms=execution_time * 1000,
            user_id=user_id,
            assistant_id=assistant_id,
            conversation_id=conversation_id,
        ))

        delivery = await openai_client.send_function_result(function_call_id, result)
        if delivery.get("success"):
            await _send(websocket, {
                "type": "function_call.completed", "function": function_name,
                "function_call_id": function_call_id, "result": result,
                "execution_time": execution_time, "async_execution": True,
            })
        else:
            logger.error(f"[FUNC] result delivery failed for {function_name}: {delivery.get('error')}")
            await _send(websocket, {
                "type": "function_call.delivery_error", "function_call_id": function_call_id,
                "error": delivery.get("error"), "async_execution": True,
            })

    except Exception as exc:
        execution_time = time.time() - started
        logger.error(f"[FUNC] {function_name} failed: {exc}\n{traceback.format_exc()}")
        asyncio.create_task(async_save_function_log(
            function_name=function_name,
            arguments=arguments,
            result={"error": str(exc)},
            status="error",
            execution_time_ms=execution_time * 1000,
            user_id=user_id,
            assistant_id=assistant_id,
            conversation_id=conversation_id,
            error_message=str(exc),
        ))
        # Провайдер ждёт результат — отдадим ошибку, иначе бэкенд зависнет в ожидании
        try:
            await openai_client.send_function_result(function_call_id, {"error": str(exc), "status": "error"})
        except Exception:
            pass
        await _send(websocket, {
            "type": "function_call.error", "function": function_name,
            "function_call_id": function_call_id, "error": str(exc), "async_execution": True,
        })
