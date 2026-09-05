# backend/websockets/fish_llm_client.py
"""
Текстовый клиент OpenAI Realtime для Fish-хендлера.

Realtime здесь — не голосовой стек, а «мозг» каскада: принимает аудио
абонента (PCM16 24 кГц), сам детектирует конец реплики (server VAD),
транскрибирует речь и отвечает ТЕКСТОМ (output_modalities=["text"]).
Озвучивает текст Fish Audio (fish_tts_client.py).

Интерфейс повторяет то подмножество OpenAIRealtimeClientNew, которое трогает
общий код исполнения функций (execute_and_send_function_result из
handler_realtime_new): assistant_config, client_id, db_session, session_id,
conversation_record_id, enabled_functions, send_function_result().
Запись для привязки function_logs создаётся в fish_conversations.
"""

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional

import websockets

from backend.core.logging import get_logger
from backend.functions import normalize_function_name
from backend.models.fish_assistant import DEFAULT_FISH_LLM_MODEL, FISH_LLM_MODELS
from backend.websockets.openai_client_new import normalize_functions, get_device_vad_settings

logger = get_logger(__name__)

# VAD для телефонной линии: шум и эхо не должны считаться началом речи, а
# пауза до ответа чуть длиннее, чем в наушниках. Значения из проверенного на
# звонках Fish-сценария (500/300/0.5).
TELEPHONY_VAD = {"threshold": 0.5, "prefix_padding_ms": 300, "silence_duration_ms": 500}

INPUT_RATE = 24000
MAX_OUTPUT_TOKENS = 2000


class FishLLMClient:
    def __init__(
        self,
        api_key: str,
        assistant_config,
        client_id: str,
        db_session: Any = None,
        user_agent: str = "",
        telephony: bool = False,
    ) -> None:
        self.api_key = api_key
        self.assistant_config = assistant_config
        self.client_id = client_id
        self.db_session = db_session
        self.user_agent = user_agent or ""
        self.telephony = telephony

        model = getattr(assistant_config, "llm_model", None) or DEFAULT_FISH_LLM_MODEL
        if model not in FISH_LLM_MODELS:
            logger.warning(f"[FISH-LLM] unsupported llm_model '{model}', using {DEFAULT_FISH_LLM_MODEL}")
            model = DEFAULT_FISH_LLM_MODEL
        self.model = model
        self.url = f"wss://api.openai.com/v1/realtime?model={model}"

        self.ws = None
        self.is_connected = False
        self.session_id = str(uuid.uuid4())
        self.conversation_record_id: Optional[str] = None
        self.enabled_functions: List[str] = []
        self.vad_settings = TELEPHONY_VAD if telephony else get_device_vad_settings(self.user_agent)

    # ------------------------------------------------------------------ соединение
    async def connect(self) -> bool:
        if not self.api_key:
            logger.error("[FISH-LLM] OPENAI_API_KEY is not configured")
            return False
        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    self.url,
                    extra_headers=[
                        ("Authorization", f"Bearer {self.api_key}"),
                        ("User-Agent", "Voksy-Fish/1.0"),
                    ],
                    max_size=15 * 1024 * 1024,
                    ping_interval=30,
                    ping_timeout=120,
                    close_timeout=15,
                ),
                timeout=30,
            )
        except Exception as exc:
            logger.error(f"[FISH-LLM] connect failed: {exc}")
            return False

        self.is_connected = True
        if not await self.update_session():
            await self.close()
            return False
        self._create_conversation_record()
        logger.info(f"[FISH-LLM] session ready: model={self.model} tools={self.enabled_functions} vad={self.vad_settings}")
        return True

    def _build_tools(self) -> List[Dict[str, Any]]:
        functions = getattr(self.assistant_config, "functions", None)
        tools = []
        for func_def in normalize_functions(functions):
            tools.append({
                "type": "function",
                "name": func_def["name"],
                "description": func_def["description"],
                "parameters": func_def["parameters"],
            })
        self.enabled_functions = [normalize_function_name(t["name"]) for t in tools]
        return tools

    async def update_session(self) -> bool:
        tools = self._build_tools()
        instructions = getattr(self.assistant_config, "system_prompt", None) or "Ты вежливый голосовой помощник."
        payload = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": self.model,
                "output_modalities": ["text"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": INPUT_RATE},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": self.vad_settings["threshold"],
                            "prefix_padding_ms": self.vad_settings["prefix_padding_ms"],
                            "silence_duration_ms": self.vad_settings["silence_duration_ms"],
                            "create_response": True,
                            "interrupt_response": True,
                        },
                        "transcription": {"model": "whisper-1"},
                    },
                },
                "instructions": instructions,
                "tools": tools,
                "tool_choice": "auto" if tools else "none",
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            },
        }
        try:
            await self.ws.send(json.dumps(payload))
            return True
        except Exception as exc:
            logger.error(f"[FISH-LLM] session.update failed: {exc}")
            return False

    def _create_conversation_record(self) -> None:
        """Пустая запись сессии в fish_conversations — к ней привязываются function_logs."""
        if self.db_session is None:
            return
        try:
            from backend.models.fish_assistant import FishConversation
            conv = FishConversation(
                assistant_id=self.assistant_config.id,
                session_id=self.session_id,
                user_message="",
                assistant_message="",
            )
            self.db_session.add(conv)
            self.db_session.commit()
            self.db_session.refresh(conv)
            self.conversation_record_id = str(conv.id)
        except Exception as exc:
            logger.error(f"[FISH-LLM] conversation record failed: {exc}")
            try:
                self.db_session.rollback()
            except Exception:
                pass

    # ------------------------------------------------------------------ команды
    async def _send(self, payload: Dict[str, Any]) -> bool:
        if not self.is_connected or not self.ws:
            return False
        try:
            await self.ws.send(json.dumps(payload))
            return True
        except Exception as exc:
            logger.warning(f"[FISH-LLM] send failed ({payload.get('type')}): {exc}")
            return False

    async def process_audio(self, audio_b64: str) -> bool:
        """Аудио абонента уже в base64 PCM16 24 кГц — так его шлёт виджет и адаптер SIP."""
        return await self._send({"type": "input_audio_buffer.append", "audio": audio_b64})

    async def clear_audio_buffer(self) -> bool:
        return await self._send({"type": "input_audio_buffer.clear"})

    async def cancel_response(self) -> bool:
        return await self._send({"type": "response.cancel", "event_id": f"cancel_{int(time.time() * 1000)}"})

    async def create_response(self) -> bool:
        return await self._send({
            "type": "response.create",
            "response": {"output_modalities": ["text"], "max_output_tokens": MAX_OUTPUT_TOKENS},
        })

    async def add_assistant_message(self, text: str) -> bool:
        """Положить в контекст реплику, которую ассистент уже произнёс (приветствие)."""
        return await self._send({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        })

    async def add_user_text(self, text: str) -> bool:
        return await self._send({
            "type": "conversation.item.create",
            "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        })

    async def send_function_result(self, function_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Результат функции + автоматический response.create (как у OpenAI-клиента)."""
        if not self.is_connected or not self.ws:
            return {"success": False, "error": "not connected"}
        ok = await self._send({
            "type": "conversation.item.create",
            "event_id": f"funcres_{int(time.time() * 1000)}",
            "item": {
                "type": "function_call_output",
                "call_id": function_call_id,
                "output": json.dumps(result, ensure_ascii=False),
            },
        })
        if not ok:
            return {"success": False, "error": "send failed"}
        await self.create_response()
        return {"success": True, "error": None}

    async def close(self) -> None:
        self.is_connected = False
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
