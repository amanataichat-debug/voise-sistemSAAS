"""
🚀 LLM Stream WebSocket Handler v3.0
=====================================

Отдельный WebSocket эндпоинт для LLM текстового стриминга.
Изолирован от голосового канала для предотвращения искажений аудио.

🔧 v2.0: OpenAI API key from User model via assistant_id chain:
    assistant_id → GeminiAssistantConfig → user_id → User → openai_api_key

🔧 v3.0: Chat history support (5 pairs = 10 messages context)

АРХИТЕКТУРА:
┌─────────────┐         ┌──────────────────┐
│   Browser   │   WS    │   LLM Stream     │
│             │◄───────►│   Handler        │
│  (text UI)  │         │  (OpenAI API)    │
└─────────────┘         └──────────────────┘

СОБЫТИЯ:
Client → Server:
- llm.query: Запрос к LLM (с опциональной историей)
  {
    "type": "llm.query",
    "query": "текущий вопрос",
    "history": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ],
    "request_id": "text_123"
  }

Server → Client:
- llm.stream.start: Начало стриминга
- llm.stream.delta: Chunk текста
- llm.stream.done: Завершение
- llm.stream.error: Ошибка
"""

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
import json
import asyncio
import uuid
import time
import os
import aiohttp
from typing import Optional, Dict, Any, List

from backend.core.logging import get_logger
from backend.models.gemini_assistant import GeminiAssistantConfig
from backend.models.user import User
from backend.models.agent_config import AgentConfig
from backend.functions.registry import execute_function

logger = get_logger(__name__)


# ============================================================================
# AGENT ORCHESTRATOR - DEFAULT PROMPTS
# ============================================================================

DEFAULT_ORCHESTRATOR_PROMPT = """Ты — интеллектуальный планировщик задач. Получив задачу, создай пошаговый план.
Доступные инструменты: {available_functions}
Верни ТОЛЬКО JSON без markdown:
{{
  "steps": [
    {{"step": 1, "title": "Краткое название", "description": "Что делаем", "tool": null}},
    {{"step": 2, "title": "Поиск", "description": "Ищем данные", "tool": "search"}}
  ]
}}
Максимум {max_steps} шагов. tool=null = LLM-рассуждение, tool="имя" = функция."""


# ============================================================================
# AGENT ORCHESTRATOR - OPENAI HELPER FUNCTIONS
# ============================================================================

async def call_openai_for_plan(
    task: str,
    orchestrator_prompt: str,
    model: str,
    available_functions: list,
    max_steps: int,
    api_key: str
) -> dict:
    """Phase 1: Build execution plan via orchestrator model."""
    prompt = orchestrator_prompt or DEFAULT_ORCHESTRATOR_PROMPT
    prompt = prompt.replace("{available_functions}", ", ".join(available_functions) if available_functions else "нет")
    prompt = prompt.replace("{max_steps}", str(max_steps))

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Задача: {task}"}
    ]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    content = ""
    try:
        timeout = aiohttp.ClientTimeout(total=30.0, connect=10.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"[AGENT] Plan API error: {response.status} - {error_text[:200]}")
                    return {"steps": [{"step": 1, "title": "Выполнение", "description": task, "tool": None}]}

                data = await response.json()
                content = data["choices"][0]["message"]["content"].strip()
                logger.error(f"[AGENT DEBUG] RAW={repr(content[:300])}")

                # Parse JSON from response — aggressive markdown cleanup
                import re as _re
                content = content.strip()
                content = _re.sub(r'```(?:json)?\s*', '', content).strip()

                # Fallback — extract JSON object even if surrounded by text
                match = _re.search(r'\{.*\}', content, _re.DOTALL)
                if match:
                    content = match.group(0)

                logger.error(f"[AGENT DEBUG] CLEANED={repr(content[:300])}")

                plan = json.loads(content)
                if "steps" not in plan or not plan["steps"]:
                    return {"steps": [{"step": 1, "title": "Выполнение", "description": task, "tool": None}]}
                return plan

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f"[AGENT] JSON parse failed. Error: {e}. Content after cleanup: {repr(content[:300])}")
        return {"steps": [{"step": 1, "title": "Выполнение", "description": task, "tool": None}]}
    except Exception as e:
        logger.error(f"[AGENT] Plan error: {e}")
        return {"steps": [{"step": 1, "title": "Выполнение", "description": task, "tool": None}]}


async def call_openai_for_step(
    task: str,
    step: dict,
    previous_results: list,
    model: str,
    api_key: str
) -> str:
    """Phase 2: Execute a reasoning step (no tool) via agent model."""
    context_parts = []
    for pr in previous_results:
        context_parts.append(f"Шаг {pr['step']}: {str(pr['result'])[:300]}")
    context = "\n".join(context_parts) if context_parts else "Нет предыдущих результатов."

    messages = [
        {"role": "system", "content": "Ты — исполнитель задач. Выполни указанный шаг, используя контекст предыдущих шагов."},
        {"role": "user", "content": f"Задача: {task}\n\nТекущий шаг: {step['title']} — {step['description']}\n\nКонтекст:\n{context}"}
    ]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=30.0, connect=10.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    return f"Ошибка API: {response.status}"
                data = await response.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"[AGENT] Step error: {e}")
        return f"Ошибка: {e}"


async def call_openai_for_args(
    task: str,
    step: dict,
    previous_results: list,
    model: str,
    api_key: str,
    context_prompt: str = None
) -> dict:
    """Determine function arguments via OpenAI Function Calling."""
    from backend.functions.registry import registry

    tool_name = step.get("tool", "")

    # Get function schema from registry
    func_class = registry.get_function(tool_name)
    if not func_class:
        logger.error(f"[AGENT] Function '{tool_name}' not found in registry")
        return {}

    func_def = func_class.get_definition()
    openai_function = {
        "name": func_def["name"],
        "description": func_def["description"],
        "parameters": func_def["parameters"],
    }

    context_parts = []
    for pr in previous_results:
        context_parts.append(f"Шаг {pr['step']}: {str(pr['result'])[:200]}")
    context = "\n".join(context_parts) if context_parts else "Нет контекста."

    system = "Ты определяешь аргументы для вызова функции. Используй предоставленную функцию."
    if context_prompt:
        system += f"\n\nКонтекст агента (содержит важные параметры — URL, токены, ID):\n{context_prompt}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Задача: {task}\nШаг: {step['title']} — {step['description']}\nКонтекст:\n{context}"}
    ]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": model,
        "messages": messages,
        "tools": [{"type": "function", "function": openai_function}],
        "tool_choice": {"type": "function", "function": {"name": func_def["name"]}},
        "max_tokens": 512,
        "temperature": 0.1,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=20.0, connect=10.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"[AGENT] Args API error: {response.status} - {error_text[:200]}")
                    return {}
                data = await response.json()
                tool_call = data["choices"][0]["message"]["tool_calls"][0]
                return json.loads(tool_call["function"]["arguments"])
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f"[AGENT] Args parse error: {e}")
        return {}
    except Exception as e:
        logger.error(f"[AGENT] Args error: {e}")
        return {}


async def call_openai_for_final(
    task: str,
    steps: list,
    results: list,
    model: str,
    api_key: str
) -> str:
    """Phase 3: Synthesize final answer from all steps and results."""
    steps_summary = []
    for s in steps:
        result_entry = next((r for r in results if r["step"] == s["step"]), None)
        result_text = str(result_entry["result"])[:300] if result_entry else "Нет результата"
        steps_summary.append(f"Шаг {s['step']}: {s['title']} → {result_text}")

    messages = [
        {"role": "system", "content": "Ты — финальный синтезатор. На основе результатов всех шагов дай чистый, понятный ответ на русском языке. Без технических деталей выполнения — только суть для пользователя."},
        {"role": "user", "content": f"Задача: {task}\n\nРезультаты:\n" + "\n".join(steps_summary)}
    ]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.3,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=30.0, connect=10.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    return "Не удалось синтезировать финальный ответ."
                data = await response.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"[AGENT] Final synthesis error: {e}")
        return f"Ошибка синтеза: {e}"


# ============================================================================
# AGENT QUERY HANDLER
# ============================================================================

async def handle_agent_query(
    websocket,
    task: str,
    request_id: str,
    agent_config_id: str,
    db: Session
) -> None:
    """
    Handle agent.query message: plan → execute steps → synthesize final answer.
    Sends agent.* events to the WebSocket client in real-time.
    """
    import os as _os

    # Load AgentConfig from DB
    try:
        config_uuid = uuid.UUID(agent_config_id) if agent_config_id else None
    except ValueError:
        await websocket.send_json({"type": "agent.error", "request_id": request_id, "error": "Invalid config ID"})
        return

    agent_cfg = db.query(AgentConfig).filter(AgentConfig.id == config_uuid).first() if config_uuid else None
    if not agent_cfg:
        await websocket.send_json({"type": "agent.error", "request_id": request_id, "error": "Agent config not found"})
        return

    # Get OpenAI API key
    user = db.query(User).filter(User.id == agent_cfg.user_id).first()
    api_key = (user.openai_api_key if user else None) or _os.environ.get('OPENAI_API_KEY')
    if not api_key:
        await websocket.send_json({"type": "agent.error", "request_id": request_id, "error": "No OpenAI API key"})
        return

    logger.error(f"[AGENT DEBUG] ━━━ START task={task[:50]}")
    logger.error(f"[AGENT DEBUG] Config: {agent_cfg.name}, model={agent_cfg.orchestrator_model}, functions={agent_cfg.agent_functions}")

    # ── Phase 1: Planning ──
    await websocket.send_json({
        "type": "agent.plan.start",
        "request_id": request_id,
        "task": task
    })

    plan = await call_openai_for_plan(
        task=task,
        orchestrator_prompt=agent_cfg.orchestrator_prompt,
        model=agent_cfg.orchestrator_model,
        available_functions=agent_cfg.agent_functions or [],
        max_steps=agent_cfg.max_steps,
        api_key=api_key
    )

    await websocket.send_json({
        "type": "agent.plan.ready",
        "request_id": request_id,
        "steps": plan["steps"]
    })

    logger.info(f"[AGENT]   Plan: {len(plan['steps'])} steps")

    # ── Phase 2: Execute steps ──
    step_results = []

    for step in plan["steps"]:
        step_num = step["step"]

        await websocket.send_json({
            "type": "agent.step.start",
            "request_id": request_id,
            "step": step_num,
            "title": step["title"],
            "description": step.get("description", ""),
            "tool": step.get("tool")
        })

        try:
            if step.get("tool"):
                # Notify about function call
                await websocket.send_json({
                    "type": "agent.function.call",
                    "request_id": request_id,
                    "step": step_num,
                    "fn": step["tool"]
                })
                # Determine arguments
                args = await call_openai_for_args(
                    task, step, step_results,
                    agent_cfg.agent_model, api_key,
                    context_prompt=agent_cfg.orchestrator_prompt
                )
                # Execute function from registry
                fn_result = await asyncio.wait_for(
                    execute_function(step["tool"], args, {}),
                    timeout=agent_cfg.step_timeout_sec
                )
                await websocket.send_json({
                    "type": "agent.function.result",
                    "request_id": request_id,
                    "step": step_num,
                    "fn": step["tool"],
                    "result": str(fn_result)[:500]
                })
                step_result = fn_result
            else:
                step_result = await asyncio.wait_for(
                    call_openai_for_step(
                        task, step, step_results,
                        agent_cfg.agent_model, api_key
                    ),
                    timeout=agent_cfg.step_timeout_sec
                )

            step_results.append({"step": step_num, "result": step_result})
            await websocket.send_json({
                "type": "agent.step.done",
                "request_id": request_id,
                "step": step_num,
                "summary": str(step_result)[:300]
            })

        except asyncio.TimeoutError:
            await websocket.send_json({
                "type": "agent.step.error",
                "request_id": request_id,
                "step": step_num,
                "error": f"Таймаут {agent_cfg.step_timeout_sec}с"
            })
            step_results.append({"step": step_num, "result": "timeout"})

        except Exception as e:
            logger.error(f"[AGENT] Step {step_num} error: {e}")
            await websocket.send_json({
                "type": "agent.step.error",
                "request_id": request_id,
                "step": step_num,
                "error": str(e)[:200]
            })
            step_results.append({"step": step_num, "result": f"error: {e}"})

    # ── Phase 3: Final synthesis ──
    final_answer = await call_openai_for_final(
        task=task,
        steps=plan["steps"],
        results=step_results,
        model=agent_cfg.agent_model,
        api_key=api_key
    )

    await websocket.send_json({
        "type": "agent.plan.done",
        "request_id": request_id,
        "final_answer": final_answer
    })

    logger.info(f"[AGENT] ━━━ Agent query complete ━━━")


# ============================================================================
# CONFIGURATION
# ============================================================================

class LLMStreamConfig:
    """Конфигурация LLM Stream Handler"""
    MODEL = "gpt-4o-mini"
    MAX_TOKENS = 4096
    TEMPERATURE = 0.1
    REQUEST_TIMEOUT = 60.0
    CONNECT_TIMEOUT = 10.0
    OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
    
    # Буферизация для плавного вывода
    BUFFER_MIN_CHARS = 30
    BUFFER_MAX_WAIT = 0.2
    
    # 🆕 v3.0: Ограничение истории
    MAX_HISTORY_MESSAGES = 10  # 5 пар


SYSTEM_PROMPT = """Ты — умный и полезный ассистент. Отвечай подробно, структурированно и по существу.

Правила форматирования:
- Используй markdown для структурирования ответа
- Заголовки: ## для основных разделов, ### для подразделов
- Списки: - или 1. 2. 3. для перечислений
- Код: ```язык для блоков кода, `код` для inline
- Выделяй **важные термины** жирным

Правила ответов:
- Отвечай на языке вопроса
- Будь конкретным и информативным
- Приводи примеры где уместно
- Учитывай контекст предыдущих сообщений в диалоге"""


# ============================================================================
# API KEY RESOLUTION
# ============================================================================

def get_openai_api_key_from_assistant(
    db: Session,
    assistant_id: Optional[str]
) -> Optional[str]:
    """
    Получает OpenAI API ключ из модели User через цепочку:
    assistant_id → GeminiAssistantConfig → user_id → User → openai_api_key
    
    Args:
        db: Database session
        assistant_id: UUID of Gemini assistant
        
    Returns:
        OpenAI API key or None if not found
    """
    if not assistant_id or not db:
        logger.warning("[LLM-WS] No assistant_id or db provided, falling back to env")
        return os.environ.get('OPENAI_API_KEY')
    
    try:
        # 1. Загружаем Gemini ассистента
        try:
            assistant_uuid = uuid.UUID(assistant_id)
            assistant = db.query(GeminiAssistantConfig).get(assistant_uuid)
        except ValueError:
            # Если не UUID, пробуем как строку
            assistant = db.query(GeminiAssistantConfig).filter(
                GeminiAssistantConfig.id.cast(str) == assistant_id
            ).first()
        
        if not assistant:
            logger.warning(f"[LLM-WS] Assistant not found: {assistant_id}")
            return os.environ.get('OPENAI_API_KEY')
        
        logger.info(f"[LLM-WS] Found assistant: {getattr(assistant, 'name', assistant_id)}")
        
        # 2. Получаем user_id из ассистента
        if not assistant.user_id:
            logger.warning(f"[LLM-WS] Assistant has no user_id")
            return os.environ.get('OPENAI_API_KEY')
        
        # 3. Загружаем пользователя
        user = db.query(User).get(assistant.user_id)
        
        if not user:
            logger.warning(f"[LLM-WS] User not found: {assistant.user_id}")
            return os.environ.get('OPENAI_API_KEY')
        
        logger.info(f"[LLM-WS] Found user: {user.email}")
        
        # 4. Получаем OpenAI ключ
        api_key = user.openai_api_key
        
        if api_key:
            logger.info(f"[LLM-WS] ✅ OpenAI API key loaded from User model: {api_key[:10]}...{api_key[-4:]}")
            return api_key
        else:
            logger.warning(f"[LLM-WS] User {user.email} has no OpenAI API key configured")
            # Fallback to environment variable
            env_key = os.environ.get('OPENAI_API_KEY')
            if env_key:
                logger.info(f"[LLM-WS] ⚠️ Falling back to environment OPENAI_API_KEY")
            return env_key
            
    except Exception as e:
        logger.error(f"[LLM-WS] Error getting API key: {e}")
        return os.environ.get('OPENAI_API_KEY')


# ============================================================================
# HISTORY PROCESSING (v3.0)
# ============================================================================

def process_chat_history(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Обрабатывает и валидирует историю чата.
    
    Args:
        history: Список сообщений от клиента
        
    Returns:
        Очищенный список сообщений для OpenAI API
    """
    if not history:
        return []
    
    processed = []
    
    for msg in history:
        if not isinstance(msg, dict):
            continue
            
        role = msg.get("role", "").strip().lower()
        content = msg.get("content", "").strip()
        
        # Валидация role
        if role not in ("user", "assistant"):
            continue
            
        # Пропускаем пустые сообщения
        if not content:
            continue
            
        processed.append({
            "role": role,
            "content": content
        })
    
    # Ограничиваем количество сообщений
    if len(processed) > LLMStreamConfig.MAX_HISTORY_MESSAGES:
        processed = processed[-LLMStreamConfig.MAX_HISTORY_MESSAGES:]
        logger.info(f"[LLM-WS] History trimmed to {LLMStreamConfig.MAX_HISTORY_MESSAGES} messages")
    
    return processed


# ============================================================================
# HANDLER
# ============================================================================

async def handle_openai_streaming_websocket(
    websocket: WebSocket,
    assistant_id: Optional[str] = None,
    db: Optional[Session] = None
) -> None:
    """
    WebSocket handler для LLM текстового стриминга.

    API key lookup is deferred until the first actual query arrives,
    so the WS connection always succeeds immediately.
    """
    client_id = str(uuid.uuid4())[:8]

    logger.info(f"[LLM-WS] 🔌 NEW CONNECTION")
    logger.info(f"[LLM-WS]    Client ID: {client_id}")
    logger.info(f"[LLM-WS]    Assistant ID: {assistant_id}")

    # API key resolved lazily on first query
    api_key = None
    api_key_resolved = False

    def resolve_api_key():
        nonlocal api_key, api_key_resolved
        if not api_key_resolved:
            api_key_resolved = True
            api_key = get_openai_api_key_from_assistant(db, assistant_id)
        return api_key

    try:
        await websocket.accept()
        logger.info(f"[LLM-WS] ✅ Connected: {client_id}")

        await websocket.send_json({
            "type": "connection_status",
            "status": "connected",
            "client_id": client_id,
            "history_support": True,
            "max_history": LLMStreamConfig.MAX_HISTORY_MESSAGES
        })

        # Main loop
        while True:
            try:
                data = await websocket.receive_json()
                msg_type = data.get("type")
                logger.info(f"[LLM-WS] Message received: type={msg_type}, keys={list(data.keys())}")

                if msg_type == "llm.query":
                    query = data.get("query", "")
                    request_id = data.get("request_id", f"req_{uuid.uuid4().hex[:8]}")

                    # Resolve API key on first query
                    key = resolve_api_key()
                    if not key:
                        await websocket.send_json({
                            "type": "llm.stream.error",
                            "request_id": request_id,
                            "error": "OpenAI API key not configured. Add your key in Settings.",
                            "error_code": "no_api_key"
                        })
                        continue

                    # 🆕 v3.0: Получаем историю
                    raw_history = data.get("history", [])
                    history = process_chat_history(raw_history)

                    if query:
                        await stream_llm_response(
                            websocket=websocket,
                            query=query,
                            request_id=request_id,
                            api_key=key,
                            history=history
                        )

                elif msg_type == "agent.query":
                    # 🆕 Agent Mode: handle orchestrated multi-step queries
                    logger.error(f"[LLM-WS] 🤖 Agent query received, task: {str(data.get('task', ''))[:50]}, config_id: {data.get('agent_config_id')}")
                    try:
                        await handle_agent_query(
                            websocket=websocket,
                            task=data.get("task", ""),
                            request_id=data.get("request_id", f"agent_{uuid.uuid4().hex[:8]}"),
                            agent_config_id=data.get("agent_config_id"),
                            db=db
                        )
                    except Exception as agent_err:
                        logger.error(f"[LLM-WS] Agent query error: {agent_err}")
                        try:
                            await websocket.send_json({"type": "agent.error", "error": str(agent_err)[:200]})
                        except:
                            pass

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    
            except WebSocketDisconnect:
                logger.info(f"[LLM-WS] Disconnected: {client_id}")
                break
            except json.JSONDecodeError as e:
                logger.warning(f"[LLM-WS] Invalid JSON: {e}")
                continue
            except Exception as e:
                logger.error(f"[LLM-WS] Error in main loop: {e}")
                try:
                    await websocket.send_json({
                        "type": "error",
                        "error": str(e)[:200]
                    })
                except:
                    break
                    
    except Exception as e:
        logger.error(f"[LLM-WS] Connection error: {e}")
    finally:
        logger.info(f"[LLM-WS] 👋 Closed: {client_id}")


async def stream_llm_response(
    websocket: WebSocket,
    query: str,
    request_id: str,
    api_key: str,
    history: List[Dict[str, str]] = None  # 🆕 v3.0
) -> None:
    """
    Стримит ответ от OpenAI на WebSocket.
    
    Args:
        websocket: WebSocket connection
        query: User query
        request_id: Request ID for tracking
        api_key: OpenAI API key (from User model)
        history: Chat history (list of {role, content} dicts)
    """
    if history is None:
        history = []
    
    start_time = time.time()
    full_content = ""
    buffer = ""
    last_flush = time.time()
    messages_sent = 0
    
    logger.info(f"[LLM-WS] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"[LLM-WS] 🚀 STREAM START")
    logger.info(f"[LLM-WS]    Request ID: {request_id}")
    logger.info(f"[LLM-WS]    Query: {query[:100]}{'...' if len(query) > 100 else ''}")
    logger.info(f"[LLM-WS]    History: {len(history)} messages")  # 🆕
    logger.info(f"[LLM-WS]    Model: {LLMStreamConfig.MODEL}")
    logger.info(f"[LLM-WS] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    try:
        # Start event
        await websocket.send_json({
            "type": "llm.stream.start",
            "request_id": request_id,
            "query": query,
            "model": LLMStreamConfig.MODEL,
            "history_count": len(history)  # 🆕
        })
        
        # 🆕 v3.0: Формируем messages с историей
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        
        # Добавляем историю
        for msg in history:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # Добавляем текущий запрос
        messages.append({
            "role": "user",
            "content": query
        })
        
        logger.info(f"[LLM-WS]    Total messages to API: {len(messages)} (1 system + {len(history)} history + 1 current)")
        
        # Stream from OpenAI
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        payload = {
            "model": LLMStreamConfig.MODEL,
            "messages": messages,  # 🆕 Теперь с историей
            "max_tokens": LLMStreamConfig.MAX_TOKENS,
            "temperature": LLMStreamConfig.TEMPERATURE,
            "stream": True,
            "stream_options": {"include_usage": True}
        }
        
        timeout = aiohttp.ClientTimeout(
            total=LLMStreamConfig.REQUEST_TIMEOUT,
            connect=LLMStreamConfig.CONNECT_TIMEOUT
        )
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                LLMStreamConfig.OPENAI_API_URL,
                headers=headers,
                json=payload
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"[LLM-WS] ❌ OpenAI API error: {response.status}")
                    logger.error(f"[LLM-WS]    Response: {error_text[:500]}")
                    
                    # Parse OpenAI error
                    try:
                        error_data = json.loads(error_text)
                        error_message = error_data.get("error", {}).get("message", error_text)
                    except:
                        error_message = error_text[:200]
                    
                    raise Exception(f"OpenAI API error ({response.status}): {error_message}")
                
                tokens_used = 0
                
                logger.info(f"[LLM-WS] 📥 Streaming response...")
                
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    
                    if not line or not line.startswith("data: "):
                        continue
                    
                    data_str = line[6:]
                    
                    if data_str == "[DONE]":
                        logger.info(f"[LLM-WS] 📥 Stream finished")
                        break
                    
                    try:
                        data = json.loads(data_str)
                        
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            
                            if content:
                                full_content += content
                                buffer += content
                                
                                # Flush buffer conditions
                                current_time = time.time()
                                should_flush = (
                                    len(buffer) >= LLMStreamConfig.BUFFER_MIN_CHARS or
                                    (current_time - last_flush) >= LLMStreamConfig.BUFFER_MAX_WAIT or
                                    buffer.rstrip().endswith(('.', '!', '?', '\n', '。', '！', '？'))
                                )
                                
                                if should_flush and buffer:
                                    await websocket.send_json({
                                        "type": "llm.stream.delta",
                                        "request_id": request_id,
                                        "content": buffer
                                    })
                                    messages_sent += 1
                                    buffer = ""
                                    last_flush = current_time
                                    
                                    # Small delay to prevent browser overload
                                    await asyncio.sleep(0.01)
                        
                        usage = data.get("usage")
                        if usage:
                            tokens_used = usage.get("total_tokens", 0)
                            
                    except json.JSONDecodeError:
                        continue
        
        # Flush remaining buffer
        if buffer:
            await websocket.send_json({
                "type": "llm.stream.delta",
                "request_id": request_id,
                "content": buffer
            })
            messages_sent += 1
        
        # Done event
        duration_ms = int((time.time() - start_time) * 1000)
        
        await websocket.send_json({
            "type": "llm.stream.done",
            "request_id": request_id,
            "full_content": full_content,
            "tokens_used": tokens_used,
            "duration_ms": duration_ms,
            "messages_sent": messages_sent,
            "model": LLMStreamConfig.MODEL,
            "history_count": len(history)  # 🆕
        })
        
        logger.info(f"[LLM-WS] ✅ STREAM COMPLETE")
        logger.info(f"[LLM-WS]    Duration: {duration_ms}ms")
        logger.info(f"[LLM-WS]    Content: {len(full_content)} chars")
        logger.info(f"[LLM-WS]    Tokens: {tokens_used}")
        logger.info(f"[LLM-WS]    Messages: {messages_sent}")
        logger.info(f"[LLM-WS]    History used: {len(history)} msgs")  # 🆕
        
    except asyncio.TimeoutError:
        error_msg = "Request timeout - OpenAI API did not respond in time"
        logger.error(f"[LLM-WS] ❌ TIMEOUT: {error_msg}")
        await websocket.send_json({
            "type": "llm.stream.error",
            "request_id": request_id,
            "error": error_msg,
            "error_code": "timeout"
        })
        
    except aiohttp.ClientError as e:
        error_msg = f"Connection error: {str(e)}"
        logger.error(f"[LLM-WS] ❌ CONNECTION ERROR: {error_msg}")
        await websocket.send_json({
            "type": "llm.stream.error",
            "request_id": request_id,
            "error": error_msg,
            "error_code": "connection_error"
        })
        
    except Exception as e:
        error_msg = str(e)[:200]
        logger.error(f"[LLM-WS] ❌ ERROR: {e}")
        await websocket.send_json({
            "type": "llm.stream.error",
            "request_id": request_id,
            "error": error_msg,
            "error_code": "internal_error"
        })
