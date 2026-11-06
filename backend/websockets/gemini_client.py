# backend/websockets/gemini_client.py
"""
Клиент Google Gemini Live API (v2.0) — модель gemini-3.8-live.

Протокол: WebSocket BidiGenerateContent (v1beta), первое сообщение `setup`,
готовность — `setupComplete`. Аудио PCM16 16 кГц на вход (`realtimeInput.audio`),
24 кГц на выход (`serverContent.modelTurn.parts[].inlineData`). VAD Gemini
(`realtimeInputConfig.automaticActivityDetection`, профиль из env GEMINI_VAD_*).

Особенности gemini-3.8-live (сентябрь 2026):
- У gemini-3.8-live `thinkingConfig` не передаётся (не поддерживается). Если GEMINI_LIVE_MODEL =
  gemini-3.8-live-extended-thinking — в generationConfig уходит thinkingConfig.thinkingLevel (GEMINI_THINKING_LEVEL,
  по умолчанию low; карточки ElevenLabs, отказ Google → повтор без него).
- Функции асинхронные по умолчанию (`behavior: NON_BLOCKING`): модель продолжает
  говорить, пока функция выполняется; в `toolResponse.functionResponses[]` нужны
  `id`, `name`, `response` и `scheduling` (INTERRUPT | WHEN_IDLE | SILENT,
  env GEMINI_TOOL_SCHEDULING, по умолчанию WHEN_IDLE — договорить и озвучить результат).
- Proactive audio включён всегда: модель сама решает, обращались ли к ней (false → ошибка setup).
- Affective dialog в 3.8 удалён из API: `enableAffectiveDialog` в setup не передаём. Поля языка (`languageCode`)
  у native-audio моделей тоже нет — язык держит промпт. Справочник: docs/gemini_live_3_8_reference.md.
- Одно событие serverContent может нести несколько parts (аудио + текст).
- Лимиты без сжатия контекста: аудио-сессия 15 мин, соединение ~10 мин. Для карточек ElevenLabs
  (await_setup=True) включены sessionResumption (ручка сессии: после обрыва соединения разговор
  продолжается с того же места, приветствие не повторяется) и contextWindowCompression.slidingWindow
  (сессия не упирается в 15 минут). Эти поля не проверены на живом Google, поэтому при отказе setup
  клиент сам откатывается на урезанный набор (EXTRAS_FULL → EXTRAS_BASIC → EXTRAS_NONE), а не молчит.

Модель задаётся env GEMINI_LIVE_MODEL (backend/core/config.py).
"""

import asyncio
import os
import json
import uuid
import base64
import time
import websockets
import re
import traceback
from collections import Counter
from websockets.exceptions import ConnectionClosed
from typing import Optional, List, Dict, Any, Union, AsyncGenerator

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.functions import get_function_definitions, get_enabled_functions, normalize_function_name, execute_function

logger = get_logger(__name__)

DEFAULT_VOICE = "Aoede"
STABLE_LIVE_MODEL = "gemini-3.8-live"   # запасная модель, если выбранная в env не открылась
GEMINI_LIVE_MODEL = getattr(settings, "GEMINI_LIVE_MODEL", None) or "gemini-3.8-live"
# Как модель озвучивает результат асинхронной функции: WHEN_IDLE — договорить и
# затем озвучить, INTERRUPT — сразу, SILENT — молча учесть.
def language_note_for_gemini(language: str) -> str:
    from backend.websockets.voice_hints import language_note
    return language_note(language)


# Уровни «расширенных» полей setup для долгих звонков (если Google отверг setup — спускаемся ниже)
EXTRAS_NONE, EXTRAS_BASIC, EXTRAS_FULL = 0, 1, 2
RECONNECT_ATTEMPTS = 3


def input_transcription_languages() -> List[str]:
    """Языки распознавания речи клиента для ТЕКСТА диалога (на понимание модели не влияют): env GEMINI_INPUT_TRANSCRIPTION_LANGUAGES,
    по умолчанию «ru,ky»; «none» — выключить. Без подсказки языков живой звонок 01.11.2026 (b9f35de7) записал русскую и
    кыргызскую речь как испанскую, немецкую и хинди («estuche», «Schön, schön», «मुझे जाता»), хотя модель сама слышала
    правильно и отвечала по делу: а этот текст идёт в «Диалоги», итог звонка, память клиента и защиту hangup_call.
    Если Google отвергнет поле — connect() повторит setup без него (уровень EXTRAS_BASIC), звонок не падает."""
    raw = os.getenv("GEMINI_INPUT_TRANSCRIPTION_LANGUAGES", "ru,ky")
    return [x.strip() for x in raw.split(",") if x.strip() and x.strip().lower() not in ("none", "off", "0")]


def thinking_level(model: str) -> Optional[str]:
    """Уровень рассуждений — ТОЛЬКО для gemini-3.8-live-extended-thinking (у gemini-3.8-live поле thinking_level
    не поддерживается, docs/gemini_live_3_8_reference.md). env GEMINI_THINKING_LEVEL: low (по умолчанию) | medium | high.
    Больше рассуждений = умнее ответы, но дольше пауза перед ответом."""
    if "thinking" not in (model or "").lower():
        return None
    level = os.getenv("GEMINI_THINKING_LEVEL", "low").strip().lower()
    return level.upper() if level in ("low", "medium", "high") else "LOW"


# Схема параметров функций для Gemini: он принимает только подмножество OpenAPI. Любое чужое поле (examples,
# additionalProperties, $schema, …) Google считает ошибкой всего setup и закрывает сессию (код 1007) — звонок
# рвётся через секунды. 01.11.2026: у «Анри» подключён hangup_call с `examples` у farewell_message, звонок
# сбрасывался через 3 с; у тестового агента этой функции не было, и там всё открывалось.
_GEMINI_SCHEMA_KEYS = {"type", "format", "description", "nullable", "enum", "items", "properties", "required",
                       "minimum", "maximum", "minItems", "maxItems"}


def gemini_schema(node: Any) -> Any:
    """JSON Schema функции → то, что принимает Gemini. Смысл не теряется: примеры, значение по умолчанию и предел
    длины уходят в description, а не выбрасываются."""
    if not isinstance(node, dict):
        return node
    out: Dict[str, Any] = {}
    notes: List[str] = []
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {name: gemini_schema(sub) for name, sub in value.items()}
        elif key == "items":
            out["items"] = gemini_schema(value)
        elif key in _GEMINI_SCHEMA_KEYS:
            out[key] = value
        elif key == "examples" and isinstance(value, list) and value:
            notes.append("Примеры: " + "; ".join(str(x) for x in value[:3]) + ".")
        elif key == "default" and value is not None:
            notes.append(f"По умолчанию: {value}.")
        elif key == "maxLength":
            notes.append(f"Не длиннее {value} символов.")
    # type: ["string", "null"] → "string" + nullable
    if isinstance(out.get("type"), list):
        kinds = [t for t in out["type"] if t != "null"]
        if "null" in out["type"]:
            out["nullable"] = True
        out["type"] = kinds[0] if kinds else "string"
    if notes:
        out["description"] = " ".join(x for x in [str(out.get("description") or "").strip(), *notes] if x)
    return out


GEMINI_TOOL_SCHEDULING = (getattr(settings, "GEMINI_TOOL_SCHEDULING", None) or "WHEN_IDLE").upper()
if GEMINI_TOOL_SCHEDULING not in ("INTERRUPT", "WHEN_IDLE", "SILENT"):
    GEMINI_TOOL_SCHEDULING = "WHEN_IDLE"
DEFAULT_SYSTEM_MESSAGE = "Ты мой умный помощник. Ты веселый и приятный. Отвечай по существу и с энергией."


def normalize_functions(assistant_functions):
    """Convert UI function list to full definitions with parameters."""
    if not assistant_functions:
        return []
    
    enabled_names = []
    
    if isinstance(assistant_functions, dict) and "enabled_functions" in assistant_functions:
        enabled_names = [normalize_function_name(name) for name in assistant_functions.get("enabled_functions", [])]
    else:
        enabled_names = [normalize_function_name(func.get("name")) for func in assistant_functions if func.get("name")]
        
    return get_enabled_functions(enabled_names)


def extract_webhook_url_from_prompt(prompt: str) -> Optional[str]:
    """Extract webhook URL from assistant system prompt."""
    if not prompt:
        return None
        
    pattern1 = r'URL\s+(?:вебхука|webhook):\s*(https?://[^\s"\'<>]+)'
    pattern2 = r'(?:вебхука|webhook)\s+URL:\s*(https?://[^\s"\'<>]+)'
    pattern3 = r'https?://[^\s"\'<>]+'
    
    for pattern in [pattern1, pattern2, pattern3]:
        matches = re.findall(pattern, prompt, re.IGNORECASE)
        if matches:
            return matches[0]
            
    return None


def generate_short_id(prefix: str = "") -> str:
    """Generate short unique identifier."""
    raw_id = str(uuid.uuid4()).replace("-", "")
    max_id_len = 32 - len(prefix)
    return f"{prefix}{raw_id[:max_id_len]}"


class GeminiLiveClient:
    """
    🚀 PRODUCTION v1.6 - Client for Google Gemini Live API
    
    Key features:
    - Pure Gemini VAD (automatic voice activity detection)
    - Continuous audio streaming (no manual commit needed)
    - Native audio processing (16kHz input, 24kHz output)
    - Audio transcription (input + output)
    - Manual function calling (handler controls execution)
    - Thinking mode support
    - Screen context support
    - Auto-greeting on connect
    - Reliable error handling
    - Performance monitoring
    """
    
    def __init__(
        self,
        api_key: str,
        assistant_config: GeminiAssistantConfig,
        client_id: str,
        db_session: Any = None,
        user_agent: str = "",
        voice_override: Optional[str] = None,
        record_conversation: bool = True,
        language: Optional[str] = None,
        realtime_text: bool = False,
        await_setup: bool = False,
        voice_hints: bool = False,
        telephony: bool = False,
        blocking_tools: bool = False,
    ):
        """Initialize the Gemini Live API client.

        voice_override / language / record_conversation — для карточек ElevenLabs с движком
        «Gemini Live» (handler_gemini_live.py): голос из live_voice, инструкция языка в промпт,
        диалоги пишет сама сессия в eleven_conversations (не в gemini_conversations).
        """
        self.api_key = api_key
        self.voice_override = (voice_override or "").strip() or None
        self.record_conversation = record_conversation
        # Текстовые реплики (приветствие) — через realtimeInput.text: у 3.x Live-моделей clientContent
        # рассчитан на начальную историю. Включается только для карточек ElevenLabs; Gemini-ассистенты
        # остаются на прежнем clientContent.
        self.realtime_text = realtime_text
        # await_setup: connect() дожидается ответа Google на setup. Иначе неверный ключ / модель / поле
        # настройки открывают сокет и тут же закрываются, звонок идёт в полной тишине (так и было).
        self.await_setup = await_setup
        # voice_hints: дописать к промпту те же правила манеры и диалога, что читает GPT-Live (voice_hints.py)
        self.voice_hints = voice_hints
        self.telephony = telephony
        # blocking_tools: функции BLOCKING — модель ждёт результата и только потом отвечает. При NON_BLOCKING
        # она отвечает сразу и, запрещённая «сейчас посмотрю», говорила «не знаю» ещё до результата поиска.
        self.blocking_tools = blocking_tools
        self.system_prompt_chars = 0   # длина итогового промпта сессии (карточка + знания + правила) — для журнала звонка
        self.connect_error: Optional[str] = None   # причина отказа Google (код и текст) для сообщения клиенту
        self._pending: list = []                   # сообщения, прочитанные при ожидании setup (первым читает цикл событий)
        # Долгие звонки: ручка возобновления сессии, уровень расширенных полей setup, счётчики обрывов
        self.resume_handle: Optional[str] = None
        self.tools_enabled = True          # Google отверг функции целиком → звонок идёт без них (см. connect)
        self.extras_level = EXTRAS_FULL if (await_setup and os.getenv("GEMINI_LONG_CALLS", "1") != "0") else EXTRAS_NONE
        self._initial_extras_level = self.extras_level
        self.sent_transcription_languages: List[str] = []   # что ушло в inputAudioTranscription.languageCodes
        self._model_fallback_done = False   # повтор на стабильной модели уже был (см. connect)
        self.setup_closed = False        # Google закрыл сокет в ответ на setup (а не промолчал)
        self.reconnect_count = 0
        self.go_away_seen = 0
        self.function_gate = None          # callable(name, args) -> текст отказа | None (защита hangup_call, call_guard.py)
        self.census: Counter = Counter()   # какие события и сколько раз прислал Google (для журнала звонка)
        self.language = (language or "").strip().lower() or None
        # Если задан — реплики (user, assistant) уходят сюда вместо ConversationService/Sheets
        self.turn_saver = None
        self.assistant_config = assistant_config
        self.client_id = client_id
        self.db_session = db_session
        self.user_agent = user_agent
        self.ws = None
        self.is_connected = False
        
        self.model = GEMINI_LIVE_MODEL
        self.base_url = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        self.gemini_url = f"{self.base_url}?key={self.api_key}"
        
        self.session_id = str(uuid.uuid4())
        self.conversation_record_id: Optional[str] = None
        self.webhook_url = None
        self.last_function_name = None
        self.enabled_functions = []
        
        # Interruption states
        self.is_assistant_speaking = False
        self.current_response_id: Optional[str] = None
        self.current_audio_samples = 0
        self.interruption_occurred = False
        self.last_interruption_time = 0
        
        # ✅ NEW: Greeting state
        self.greeting_sent = False
        # Имена функций по id вызова: нужны для toolResponse (name обязателен у 3.8)
        self.call_names: Dict[str, str] = {}
        
        # Device detection
        self.is_ios = "iphone" in user_agent.lower() or "ipad" in user_agent.lower()
        self.is_android = "android" in user_agent.lower()
        self.is_mobile = self.is_ios or self.is_android
        
        if self.is_ios:
            logger.info(f"[GEMINI-CLIENT] iOS device detected")
        
        # Extract functions
        if hasattr(assistant_config, "functions"):
            functions = assistant_config.functions
            if isinstance(functions, list):
                self.enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
            elif isinstance(functions, dict) and "enabled_functions" in functions:
                self.enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
            
            logger.info(f"[GEMINI-CLIENT] Enabled functions: {self.enabled_functions}")
        
        # Webhook URL
        if "send_webhook" in self.enabled_functions and hasattr(assistant_config, "system_prompt") and assistant_config.system_prompt:
            self.webhook_url = extract_webhook_url_from_prompt(assistant_config.system_prompt)
            if self.webhook_url:
                logger.info(f"[GEMINI-CLIENT] Webhook configured")

    @property
    def use_blocking_tools(self) -> bool:
        """BLOCKING-функции, если просили и модель их умеет. gemini-3.8-live-extended-thinking их не принимает
        (звонок 02.11.2026: «BLOCKING function calls are not supported for this model», код 1007)."""
        return bool(self.blocking_tools) and not thinking_level(self.model)

    async def connect(self) -> bool:
        """Подключение к Gemini Live. Если выбранная в env модель (например gemini-3.8-live-extended-thinking)
        не открылась — один повтор на стабильной gemini-3.8-live: звонок не должен обрываться из-за эксперимента
        с моделью (01.11.2026: GEMINI_LIVE_MODEL=…extended-thinking → звонок сбросился сразу)."""
        ok = await self._connect_once()
        if (ok or not self.await_setup or self.model == STABLE_LIVE_MODEL or self._model_fallback_done
                or "api key" in (self.connect_error or "").lower()):
            return ok
        self._model_fallback_done = True
        failed_model, reason = self.model, self.connect_error
        self.model = STABLE_LIVE_MODEL
        self.extras_level = self._initial_extras_level
        self.tools_enabled = True
        self.resume_handle = None
        self._pending.clear()
        self.connect_error = None
        self._note_session(f"Модель {failed_model} не открылась ({reason or 'без причины от Google'}); "
                           f"звонок идёт на {STABLE_LIVE_MODEL}", "warning")
        return await self._connect_once()

    async def _connect_once(self) -> bool:
        """Establish WebSocket connection to Gemini Live API."""
        if not self.api_key:
            logger.error("[GEMINI-CLIENT] ❌ Google API key not provided")
            return False

        logger.info(f"[GEMINI-CLIENT] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"[GEMINI-CLIENT] 🔌 CONNECTION ATTEMPT")
        logger.info(f"[GEMINI-CLIENT] Model: {self.model}")
        logger.info(f"[GEMINI-CLIENT] Endpoint: {self.base_url}")
        logger.info(f"[GEMINI-CLIENT] API Key: {self.api_key[:15]}...{self.api_key[-8:]}")
        logger.info(f"[GEMINI-CLIENT] VAD Mode: Pure Gemini (automatic)")
        logger.info(f"[GEMINI-CLIENT] Transcription: ENABLED (input + output)")
        logger.info(f"[GEMINI-CLIENT] Auto-greeting: {bool(getattr(self.assistant_config, 'greeting_message', None))}")
        logger.info(f"[GEMINI-CLIENT] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        try:
            logger.info(f"[GEMINI-CLIENT] Opening WebSocket connection (timeout: 30s)...")
            
            # Заголовки: в websockets < 14 параметр extra_headers, в >= 14 — additional_headers.
            # На Render (Python 3.14) старый pin не ставится и приходит новая версия: с extra_headers
            # websockets.connect падал TypeError → КАЖДЫЙ звонок Gemini обрывался за 3 с (GPT-Live жил,
            # потому что у него этот запасной путь уже был — live_client.py). Теперь так же у Gemini.
            _ws_kwargs = dict(max_size=15 * 1024 * 1024, ping_interval=30, ping_timeout=120, close_timeout=15)
            _headers = {"User-Agent": "Voksy AI/2.0"}
            try:
                self.ws = await asyncio.wait_for(
                    websockets.connect(self.gemini_url, extra_headers=_headers, **_ws_kwargs), timeout=30)
            except TypeError as te:
                if "extra_headers" not in str(te):
                    raise
                self.ws = await asyncio.wait_for(
                    websockets.connect(self.gemini_url, additional_headers=_headers, **_ws_kwargs), timeout=30)
            
            self.is_connected = True
            logger.info(f"[GEMINI-CLIENT] ✅ WebSocket CONNECTED successfully")
            logger.info(f"[GEMINI-CLIENT] Model: {self.model}")

            # Get settings
            voice = self.voice_override or getattr(self.assistant_config, "voice", None) or DEFAULT_VOICE
            system_message = getattr(self.assistant_config, "system_prompt", None) or DEFAULT_SYSTEM_MESSAGE
            if self.voice_hints:
                # Карточка ElevenLabs на Gemini Live: правило языка (язык карточки = язык приветствия, явная
                # просьба клиента держится до конца звонка) идёт в НАЧАЛО промпта вместе с голосом —
                # gemini_prompt_head ниже, при сборке подсказок.
                pass
            elif self.language:
                try:
                    from backend.websockets.fish_llm_client import LANGUAGE_INSTRUCTIONS
                    if self.language in LANGUAGE_INSTRUCTIONS:
                        system_message = f"{system_message}\n\n{LANGUAGE_INSTRUCTIONS[self.language]}"
                except Exception as e:
                    logger.warning(f"[GEMINI-CLIENT] language instruction skipped: {e}")
            functions = getattr(self.assistant_config, "functions", None)

            # Динамически домешиваем функции подключённых коннекторов (Composio):
            # голос не полагается на разовый снимок в конфиге, читает connected-
            # коннекторы агента из БД — так же, как оркестратор берёт tools.get.
            try:
                from backend.services import composio_service as _composio
                functions = _composio.merge_voice_connector_functions(
                    self.db_session, self.assistant_config, functions
                )
                _note = _composio.connector_voice_prompt_note(self.db_session, self.assistant_config)
                if _note:
                    system_message = (system_message or "") + _note
            except Exception as _e:
                logger.warning(f"[GEMINI-CLIENT] connector merge failed: {_e}")

            # Update functions
            if functions:
                if isinstance(functions, list):
                    self.enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
                elif isinstance(functions, dict) and "enabled_functions" in functions:
                    self.enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
                
                logger.info(f"[GEMINI-CLIENT] Functions loaded: {self.enabled_functions}")

            # Webhook URL
            if "send_webhook" in self.enabled_functions:
                self.webhook_url = extract_webhook_url_from_prompt(system_message)

            if self.voice_hints:
                try:
                    from backend.websockets.voice_hints import build_gemini_voice_hints, gemini_prompt_head
                    # 06.11.2026: голос и язык — в начале, до инструкции карточки (модель держит начало и конец
                    # промпта; в середине 34 тыс. символов эти правила терялись)
                    system_message = (gemini_prompt_head(voice, self.language, getattr(self.assistant_config, "greeting_message", None))
                                      + system_message
                                      + build_gemini_voice_hints(normalize_functions(functions), voice, self.telephony))
                except Exception as e:
                    logger.warning(f"[GEMINI-CLIENT] voice hints skipped: {e}")

            self.system_prompt_chars = len(system_message)

            # Send setup message
            if not await self.setup_session(
                voice=voice,
                system_message=system_message,
                functions=functions
            ):
                logger.error("[GEMINI-CLIENT] ❌ Failed to setup session")
                await self.close()
                return False

            if self.await_setup and not await self._wait_setup_reply():
                await self.close()
                if (self.setup_closed and (self.extras_level > EXTRAS_NONE or self.tools_enabled or self.resume_handle)
                        and "api key" not in (self.connect_error or "").lower()):
                    # Google закрыл сессию на setup, возможно из-за расширенных полей: пробуем проще
                    rejected = self.connect_error
                    reason = (rejected or "").lower()
                    about_tools = any(k in reason for k in ("tools", "function_declarations", "functiondeclaration", "parameters"))
                    about_extras = any(k in reason for k in ("sessionresumption", "session_resumption", "contextwindow",
                                                             "context_window", "inputaudiotranscription", "input_audio_transcription"))
                    if self.resume_handle:
                        # вероятнее всего устарела ручка возобновления — не отказываемся из-за неё от самой функции
                        self.resume_handle = None
                        self._note_session(f"Google не принял ручку возобновления ({rejected}); начинаю сессию заново", "warning")
                    elif about_tools and not about_extras and self.tools_enabled:
                        # Google ругается на функции: лучше звонок без функций (знания и прощание работают через промпт
                        # и страж завершения), чем сброс через пару секунд
                        self.tools_enabled = False
                        self._note_session(f"Google не принял функции ({rejected}); звонок идёт БЕЗ функций", "error")
                    elif self.extras_level > EXTRAS_NONE:
                        self.extras_level -= 1
                        self._note_session(f"Google не принял настройку сессии ({rejected}); "
                                           f"повторяю с урезанным набором полей (уровень {self.extras_level})", "warning")
                    else:
                        self.tools_enabled = False
                        self._note_session(f"Google не принял настройку сессии ({rejected}); последняя попытка без функций", "error")
                    self._pending.clear()
                    self.connect_error = None
                    return await self._connect_once()
                return False

            logger.info(f"[GEMINI-CLIENT] ✅ Session initialized successfully")

            return True
            
        except asyncio.TimeoutError:
            logger.error(f"[GEMINI-CLIENT] ❌ CONNECTION TIMEOUT (30s)")
            logger.error(f"[GEMINI-CLIENT] Возможные причины:")
            logger.error(f"[GEMINI-CLIENT]   1. Google API недоступен из вашей страны/сервера")
            logger.error(f"[GEMINI-CLIENT]   2. Требуется VPN/proxy")
            logger.error(f"[GEMINI-CLIENT]   3. Проблемы с сетью")
            logger.error(f"[GEMINI-CLIENT]   4. Firewall блокирует WebSocket")
            return False
            
        except websockets.exceptions.InvalidStatusCode as e:
            logger.error(f"[GEMINI-CLIENT] ❌ INVALID HTTP STATUS: {e.status_code}")
            
            if hasattr(e, 'headers'):
                logger.error(f"[GEMINI-CLIENT] Response headers: {dict(e.headers)}")
            
            if e.status_code == 400:
                logger.error(f"[GEMINI-CLIENT] ⚠️ BAD REQUEST - проверьте формат запроса")
            elif e.status_code == 401:
                logger.error(f"[GEMINI-CLIENT] ⚠️ UNAUTHORIZED - проверьте API ключ")
                logger.error(f"[GEMINI-CLIENT]   Ключ: {self.api_key[:20]}...")
            elif e.status_code == 403:
                logger.error(f"[GEMINI-CLIENT] ⚠️ FORBIDDEN - нет доступа к Live API")
                logger.error(f"[GEMINI-CLIENT]   Проверьте, что API ключ имеет доступ к Gemini Live API")
            elif e.status_code == 404:
                logger.error(f"[GEMINI-CLIENT] ⚠️ NOT FOUND - неверный endpoint или модель")
                logger.error(f"[GEMINI-CLIENT]   Endpoint: {self.base_url}")
                logger.error(f"[GEMINI-CLIENT]   Model: {self.model}")
            elif e.status_code >= 500:
                logger.error(f"[GEMINI-CLIENT] ⚠️ SERVER ERROR на стороне Google")
            
            return False
            
        except OSError as e:
            logger.error(f"[GEMINI-CLIENT] ❌ NETWORK ERROR: {e}")
            if hasattr(e, 'errno'):
                logger.error(f"[GEMINI-CLIENT] Error code: {e.errno}")
            logger.error(f"[GEMINI-CLIENT] Возможно требуется VPN/proxy для доступа к Google API")
            return False
            
        except Exception as e:
            logger.error(f"[GEMINI-CLIENT] ❌ UNEXPECTED ERROR: {type(e).__name__}")
            logger.error(f"[GEMINI-CLIENT] Message: {str(e)}")
            logger.error(f"[GEMINI-CLIENT] Traceback:")
            logger.error(traceback.format_exc())
            return False

    def _note_session(self, text: str, level: str = "info") -> None:
        """Строка в журнал звонка (если он есть) и в обычный лог."""
        (logger.warning if level == "warning" else logger.info)(f"[GEMINI-CLIENT] {text}")
        try:
            from backend.websockets.call_log import CallLogRecorder
            rec = CallLogRecorder.current()
            if rec is not None:
                rec.add("session", text, level=level)
        except Exception:
            pass

    async def _wait_setup_reply(self, timeout: float = 15.0) -> bool:
        """
        Дождаться первого ответа Google на setup (setupComplete). Закрытие сессии или тишина —
        отказ: причина (код и текст Google) в connect_error и в журнале как ERROR. Прочитанное
        сообщение кладётся в _pending: цикл событий хендлера разберёт его первым (setupComplete → приветствие).
        """
        self.setup_closed = False
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            self.connect_error = f"Google не ответил на настройку сессии за {int(timeout)} с (модель {self.model})"
        except ConnectionClosed as e:
            code = getattr(e, "code", None) or getattr(getattr(e, "rcvd", None), "code", None)
            reason = getattr(e, "reason", None) or getattr(getattr(e, "rcvd", None), "reason", None)
            self.setup_closed = True
            self.connect_error = f"Google закрыл сессию: код {code}, причина «{reason}» (модель {self.model})"
        else:
            self._pending.append(raw)
            return True
        logger.error(f"[GEMINI-CLIENT] ❌ {self.connect_error}")
        return False

    async def reconnect(self) -> bool:
        """
        Переподключиться к Gemini посреди разговора (Google обрывает соединение примерно раз в ~10 минут).
        Есть ручка возобновления — разговор продолжается с того же места; приветствие второй раз не звучит
        (клиент его уже слышал). Старая ручка не принята — следующая попытка идёт без неё.
        """
        greeted = self.greeting_sent    # close() при неудачной попытке сбрасывает флаг: восстанавливаем
        for attempt in range(1, RECONNECT_ATTEMPTS + 1):
            logger.info(f"[GEMINI-CLIENT] Attempting reconnection {attempt}/{RECONNECT_ATTEMPTS} "
                        f"(resume={'yes' if self.resume_handle else 'no'})...")
            try:
                if self.ws:
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                self.is_connected = False
                self.ws = None
                self._pending.clear()

                # Reset states
                self.is_assistant_speaking = False
                self.current_response_id = None
                self.current_audio_samples = 0
                self.interruption_occurred = False
                # карточки ElevenLabs: клиент приветствие уже слышал; Gemini-ассистенты — прежнее поведение
                self.greeting_sent = greeted if self.await_setup else False

                if await self.connect():
                    self.greeting_sent = greeted if self.await_setup else False
                    self.reconnect_count += 1
                    self._note_session(
                        f"Соединение с Gemini восстановлено (попытка {attempt}, "
                        f"{'с продолжением разговора' if self.resume_handle else 'без ручки возобновления — контекст начат заново'})",
                        "info" if self.resume_handle else "warning")
                    return True
            except Exception as e:
                logger.error(f"[GEMINI-CLIENT] Reconnection error: {e}")
            self.resume_handle = None       # возможно, ручка устарела: дальше — с чистой сессии
            await asyncio.sleep(0.4 * attempt)
        self._note_session("Не удалось восстановить соединение с Gemini", "warning")
        return False

    def handle_service_event(self, data: Dict[str, Any]) -> bool:
        """
        Служебные сообщения Google в течение разговора. True — сообщение разобрано.
        sessionResumptionUpdate: запоминаем свежую ручку (если сессию можно возобновить).
        goAway: Google предупреждает, что соединение скоро закроется — ручка уже есть, переподключимся после закрытия.
        """
        update = data.get("sessionResumptionUpdate")
        if update is not None:
            if update.get("resumable") and update.get("newHandle"):
                if self.resume_handle is None:
                    logger.info("[GEMINI-CLIENT] Session resumption handle received")
                self.resume_handle = update["newHandle"]
            return True
        if "goAway" in data:
            self.go_away_seen += 1
            self._note_session(f"Gemini предупредил о закрытии соединения (осталось {data['goAway'].get('timeLeft', '?')}); "
                               f"{'разговор продолжится по ручке возобновления' if self.resume_handle else 'ручки возобновления нет'}",
                               "info")
            return True
        return False

    async def setup_session(
        self,
        voice: str = DEFAULT_VOICE,
        system_message: str = DEFAULT_SYSTEM_MESSAGE,
        functions: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
    ) -> bool:
        """Send BidiGenerateContentSetup to configure session."""
        if not self.is_connected or not self.ws:
            logger.error("[GEMINI-CLIENT] Cannot setup session: not connected")
            return False
        
        # Build tools
        normalized_functions = normalize_functions(functions) if self.tools_enabled else []
        tools = []
        
        for func_def in normalized_functions:
            tools.append({
                "function_declarations": [{
                    "name": func_def["name"],
                    "description": func_def["description"],
                    "parameters": gemini_schema(func_def["parameters"]),
                    # gemini-3.8-live: NON_BLOCKING — модель продолжает говорить, пока функция работает;
                    # BLOCKING (карточки ElevenLabs) — ждёт результата и отвечает по нему
                    "behavior": "BLOCKING" if self.use_blocking_tools else "NON_BLOCKING",
                }]
            })
        
        self.enabled_functions = [normalize_function_name(tool["function_declarations"][0]["name"]) for tool in tools]
        logger.info(f"[GEMINI-CLIENT] Functions activated: {self.enabled_functions}")
        
        # Speech config
        speech_config = {
            "voiceConfig": {
                "prebuiltVoiceConfig": {
                    "voiceName": voice
                }
            }
        }
        
        # ✅ Generation config - ТОЛЬКО модальности и голос (БЕЗ транскрипции)
        generation_config = {
            "responseModalities": ["AUDIO"],
            "speechConfig": speech_config
        }
        
        # System instruction
        system_instruction = {
            "parts": [{
                "text": system_message
            }]
        }
        
        # ✅ Build correct setup payload for Live API
        # Транскрипция на верхнем уровне setup, НЕ в generation_config!
        setup_payload = {
            "setup": {
                "model": f"models/{self.model}",
                "generationConfig": generation_config,
                "systemInstruction": system_instruction,
                "outputAudioTranscription": {},  # ✅ На верхнем уровне setup
                "inputAudioTranscription": {}     # ✅ На верхнем уровне setup
            }
        }
        
        # Детектор речи (VAD) Gemini. По умолчанию Gemini ждёт долгой чистой тишины и
        # отвечает с большой задержкой, на телефонном звуке с шумом — иногда не отвечает вовсе.
        # Один профиль для виджета и телефонии. Настраивается через окружение:
        #   GEMINI_VAD_PROFILE=default        — отключить и вернуть поведение Gemini по умолчанию
        #   GEMINI_VAD_SILENCE_MS             — пауза, после которой фраза считается законченной (800: 500 перебивало
        #                                       человека на паузе посреди фразы, 01.11.2026; Google советует 500–800)
        #   GEMINI_VAD_START_SENSITIVITY      — low|high: насколько легко Gemini решает, что человек
        #                                       НАЧАЛ говорить. high на телефонной линии срабатывает
        #                                       на шум и обрывает приветствие, поэтому по умолчанию low
        #   GEMINI_VAD_END_SENSITIVITY        — low|high: насколько быстро считает, что человек ЗАКОНЧИЛ (low — ждёт дольше)
        vad_profile = os.getenv("GEMINI_VAD_PROFILE", "fast").lower()
        if vad_profile != "default":
            try:
                silence_ms = int(os.getenv("GEMINI_VAD_SILENCE_MS", "800"))
            except ValueError:
                silence_ms = 800
            start_sens = os.getenv("GEMINI_VAD_START_SENSITIVITY", "low").strip().lower()
            end_sens = os.getenv("GEMINI_VAD_END_SENSITIVITY", "low").strip().lower()
            # Сколько речи нужно, чтобы Google засчитал «собеседник начал говорить». С 02.11.2026 начало речи
            # обрывает и доигрывание ответа агента (handler_gemini_live.on_voice_activity), поэтому 100 мс мало —
            # кашель, «м» или щелчок линии обрывали бы агента; 200 мс — короткое слово («стоп», «подождите») проходит.
            try:
                # 200 → 300 (02.11.2026, звонок 6811616a): шум/невнятная фраза клиента во время
                # ответа агента («in der Sitze») засчиталась началом речи и оборвала 4 с
                # настоящего ответа; 300 мс пока оставляет запас против кашля/щелчка линии.
                prefix_ms = max(20, min(1000, int(os.getenv("GEMINI_VAD_PREFIX_MS", "300"))))
            except ValueError:
                prefix_ms = 200
            start_value = "START_SENSITIVITY_HIGH" if start_sens == "high" else "START_SENSITIVITY_LOW"
            end_value = "END_SENSITIVITY_HIGH" if end_sens == "high" else "END_SENSITIVITY_LOW"
            setup_payload["setup"]["realtimeInputConfig"] = {
                "automaticActivityDetection": {
                    "disabled": False,
                    "startOfSpeechSensitivity": start_value,
                    "endOfSpeechSensitivity": end_value,
                    "prefixPaddingMs": prefix_ms,
                    "silenceDurationMs": silence_ms,
                }
            }
            logger.info(f"[GEMINI-CLIENT] VAD profile '{vad_profile}': start={start_value}, "
                        f"end={end_value}, {silence_ms} ms silence")

        # Долгие звонки (карточки ElevenLabs). Уровень можно снизить, если Google отверг setup (см. connect()).
        if self.extras_level >= EXTRAS_BASIC:
            resumption: Dict[str, Any] = {}
            if self.resume_handle:
                resumption["handle"] = self.resume_handle
            setup_payload["setup"]["sessionResumption"] = resumption
            setup_payload["setup"]["contextWindowCompression"] = {"slidingWindow": {}}
        # Думающей модели уровень рассуждений ОБЯЗАТЕЛЕН («Thinking level must be specified for this model»,
        # 02.11.2026) — поэтому не зависит от уровня расширенных полей и не снимается лестницей отката.
        level = thinking_level(self.model)
        if level:
            generation_config["thinkingConfig"] = {"thinkingLevel": level}
        self.sent_transcription_languages = []
        if self.extras_level >= EXTRAS_FULL:
            langs = input_transcription_languages()
            if langs:
                # только текст диалога: модель слышит звук сама; подсказка языков помогает кыргызскому в записи
                setup_payload["setup"]["inputAudioTranscription"] = {"languageCodes": langs}
                self.sent_transcription_languages = list(langs)

        # Add tools if any
        if tools:
            setup_payload["setup"]["tools"] = tools
        
        try:
            logger.info(f"[GEMINI-CLIENT] Sending setup message...")
            logger.info(f"[GEMINI-CLIENT] Setup payload keys: {list(setup_payload['setup'].keys())}")
            logger.info(f"[GEMINI-CLIENT] ℹ️ Gemini will use automatic VAD (no manual commit needed)")
            logger.info(f"[GEMINI-CLIENT] ℹ️ Transcription enabled for both input and output audio")
            
            await self.ws.send(json.dumps(setup_payload))
            
            logger.info(f"[GEMINI-CLIENT] ✅ Session setup sent successfully")
            logger.info(f"[GEMINI-CLIENT]   Model: {self.model}")
            logger.info(f"[GEMINI-CLIENT]   Voice: {voice}")
            logger.info(f"[GEMINI-CLIENT]   Tools: {len(tools)}")
            logger.info(f"[GEMINI-CLIENT]   Transcription: ENABLED")
        except Exception as e:
            logger.error(f"[GEMINI-CLIENT] ❌ Error sending setup: {e}")
            logger.error(traceback.format_exc())
            return False

        # Create conversation record (карточки ElevenLabs пишут диалог сами — в eleven_conversations)
        if self.db_session and self.record_conversation:
            try:
                conv = GeminiConversation(
                    assistant_id=self.assistant_config.id,
                    session_id=self.session_id,
                    user_message="",
                    assistant_message="",
                )
                self.db_session.add(conv)
                self.db_session.commit()
                self.db_session.refresh(conv)
                self.conversation_record_id = str(conv.id)
                logger.info(f"[GEMINI-CLIENT] Conversation record created: {self.conversation_record_id}")
            except Exception as e:
                logger.error(f"[GEMINI-CLIENT] Error creating conversation: {e}")

        return True

    def _text_payload(self, text: str) -> Dict[str, Any]:
        """Текстовая реплика пользователя в разговор (приветствие, send_text_message)."""
        if self.realtime_text:
            return {"realtimeInput": {"text": text}}
        return {"clientContent": {"turns": [{"role": "user", "parts": [{"text": text}]}], "turnComplete": True}}

    async def send_initial_greeting(self) -> bool:
        """
        Send initial greeting prompt to trigger voice greeting from Gemini.
        Uses greeting_message from assistant config.
        
        Returns:
            True if sent successfully
        """
        if not self.is_connected or not self.ws:
            logger.error("[GEMINI-CLIENT] Cannot send greeting: not connected")
            return False
        
        if self.greeting_sent:
            logger.info("[GEMINI-CLIENT] Greeting already sent, skipping")
            return True
        
        # Get greeting from config
        greeting_message = getattr(self.assistant_config, "greeting_message", None)
        
        if not greeting_message:
            logger.info("[GEMINI-CLIENT] No greeting_message configured, skipping auto-greet")
            return False
        
        try:
            logger.info(f"[GEMINI-CLIENT] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[GEMINI-CLIENT] 👋 SENDING INITIAL GREETING")
            logger.info(f"[GEMINI-CLIENT]    Message: {greeting_message[:100]}...")
            logger.info(f"[GEMINI-CLIENT] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            
            # Send as user message to trigger response
            # We instruct Gemini to say the greeting
            greeting_text = f"Поприветствуй пользователя голосом. Скажи именно это: \"{greeting_message}\""
            payload = self._text_payload(greeting_text)
            
            await self.ws.send(json.dumps(payload))
            self.greeting_sent = True
            
            logger.info(f"[GEMINI-CLIENT] ✅ Greeting prompt sent successfully")
            logger.info(f"[GEMINI-CLIENT]    Gemini will now speak the greeting")
            
            return True
            
        except Exception as e:
            logger.error(f"[GEMINI-CLIENT] ❌ Error sending greeting: {e}")
            logger.error(traceback.format_exc())
            return False

    async def send_text_message(self, text: str) -> bool:
        """
        Send a text message to Gemini (triggers voice response).
        
        Args:
            text: Text message to send
            
        Returns:
            True if sent successfully
        """
        if not self.is_connected or not self.ws:
            logger.error("[GEMINI-CLIENT] Cannot send message: not connected")
            return False
        
        try:
            logger.info(f"[GEMINI-CLIENT] 💬 Sending text message: {text[:100]}...")
            
            payload = self._text_payload(text)
            
            await self.ws.send(json.dumps(payload))
            logger.info(f"[GEMINI-CLIENT] ✅ Text message sent")
            return True
            
        except Exception as e:
            logger.error(f"[GEMINI-CLIENT] ❌ Error sending text message: {e}")
            return False

    async def handle_interruption(self) -> bool:
        """Handle interruption events."""
        try:
            current_time = time.time()
            
            # Debounce protection
            protection_time = 0.15 if self.is_ios else 0.2
            
            if current_time - self.last_interruption_time < protection_time:
                logger.info(f"[GEMINI-CLIENT] Ignoring duplicate interruption (debounce: {protection_time}s)")
                return True
                
            self.last_interruption_time = current_time
            self.interruption_occurred = True
            
            logger.info(f"[GEMINI-CLIENT] Handling interruption")
            
            # Gemini handles interruption automatically via VAD
            # Just update our state
            self.is_assistant_speaking = False
            self.current_response_id = None
            self.current_audio_samples = 0
            
            logger.info("[GEMINI-CLIENT] Interruption handled successfully")
            return True
            
        except Exception as e:
            logger.error(f"[GEMINI-CLIENT] Error handling interruption: {e}")
            return False

    def set_assistant_speaking(self, speaking: bool, response_id: str = None) -> None:
        """Set assistant speaking state."""
        self.is_assistant_speaking = speaking
        if speaking:
            self.current_response_id = response_id
            self.current_audio_samples = 0
        else:
            self.current_response_id = None
            self.current_audio_samples = 0

    def increment_audio_samples(self, sample_count: int) -> None:
        """Increment audio sample count."""
        self.current_audio_samples += sample_count

    def register_function_call(self, function_call_id: str, name: str) -> None:
        """Запомнить имя функции по id вызова (для name в toolResponse)."""
        if function_call_id and name:
            self.call_names[function_call_id] = name
            self.last_function_name = name

    async def send_function_result(self, function_call_id: str, result: Any) -> Dict[str, Any]:
        """
        Результат функции → toolResponse (BidiGenerateContentToolResponse).
        clientContent для ответов на вызовы использовать нельзя.

        gemini-3.8-live: функции NON_BLOCKING, поэтому в ответе передаём name и
        scheduling (GEMINI_TOOL_SCHEDULING) — когда модели озвучить результат.
        """
        if not self.is_connected or not self.ws:
            error_msg = "Cannot send function result: not connected"
            logger.error(f"[GEMINI-CLIENT] {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }
        
        try:
            logger.info(f"[GEMINI-CLIENT] 📤 Sending toolResponse for call ID: {function_call_id}")
            logger.info(f"[GEMINI-CLIENT]    Function: {self.last_function_name}")
            
            # ✅ CORRECT FORMAT per Live API WebSocket documentation
            # https://ai.google.dev/api/live#BidiGenerateContentToolResponse
            if not isinstance(result, dict):
                result = {"result": result}
            function_response: Dict[str, Any] = {
                "id": function_call_id,
                "response": result,
            }
            if not self.use_blocking_tools:
                function_response["scheduling"] = GEMINI_TOOL_SCHEDULING  # у BLOCKING-функций его нет
            name = self.call_names.pop(function_call_id, None) or self.last_function_name
            if name:
                function_response["name"] = name
            result_payload = {"toolResponse": {"functionResponses": [function_response]}}
            
            # Log payload preview
            payload_preview = json.dumps(result_payload, ensure_ascii=False)[:300]
            logger.info(f"[GEMINI-CLIENT] Payload: {payload_preview}...")
            
            await self.ws.send(json.dumps(result_payload))
            logger.info(f"[GEMINI-CLIENT] ✅ toolResponse sent successfully")
            
            return {
                "success": True,
                "error": None
            }
            
        except Exception as e:
            error_msg = f"Error sending toolResponse: {e}"
            logger.error(f"[GEMINI-CLIENT] {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }

    async def send_screen_context(self, image_base64: str, silent: bool = True) -> bool:
        """
        Send screen capture to conversation context WITHOUT triggering a response.
        
        Args:
            image_base64: Base64-encoded image (data:image/jpeg;base64,...)
            silent: If True, don't trigger response (default: True)
        
        Returns:
            True if sent successfully
        """
        if not self.is_connected or not self.ws:
            logger.error("[GEMINI-CLIENT] Cannot send context: not connected")
            return False
        
        try:
            image_size_kb = len(image_base64) // 1024
            logger.info(f"[GEMINI-CLIENT] Sending screen context silently ({image_size_kb}KB)")
            
            # Extract base64 data (remove data:image/jpeg;base64, prefix if present)
            if "base64," in image_base64:
                image_base64 = image_base64.split("base64,")[1]
            
            # Send as realtime_input with inline_data
            payload = {
                "realtimeInput": {
                    "video": {
                        "mimeType": "image/jpeg",
                        "data": image_base64
                    }
                }
            }
            
            await self.ws.send(json.dumps(payload))
            logger.info("[GEMINI-CLIENT] ✅ Context image added to conversation")
            
            if not silent:
                logger.info("[GEMINI-CLIENT] Non-silent mode - model will process image")
            else:
                logger.info("[GEMINI-CLIENT] ⏸️ Silent mode - no response requested")
            
            return True
            
        except Exception as e:
            logger.error(f"[GEMINI-CLIENT] Error sending screen context: {e}")
            return False

    async def process_audio(self, audio_buffer: bytes) -> bool:
        """
        Process and send audio data to Gemini API.
        
        ✅ Pure Gemini VAD mode:
        - Just sends audio continuously
        - Gemini decides when to respond
        - No manual commit needed
        """
        if not self.is_connected or not self.ws or not audio_buffer:
            return False
        try:
            # Gemini expects base64-encoded PCM16 audio at 16kHz
            data_b64 = base64.b64encode(audio_buffer).decode("utf-8")
            
            payload = {
                "realtimeInput": {
                    "audio": {
                        "mimeType": "audio/pcm;rate=16000",
                        "data": data_b64
                    }
                }
            }
            
            await self.ws.send(json.dumps(payload))
            return True
        except ConnectionClosed:
            logger.error("[GEMINI-CLIENT] Connection closed while sending audio")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"[GEMINI-CLIENT] Error processing audio: {e}")
            return False

    async def close(self) -> None:
        """Close WebSocket connection."""
        if self.ws:
            try:
                await self.ws.close()
                device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
                logger.info(f"[GEMINI-CLIENT] WebSocket closed ({device_info})")
            except Exception as e:
                logger.error(f"[GEMINI-CLIENT] Error closing WebSocket: {e}")
        self.is_connected = False
        
        self.is_assistant_speaking = False
        self.current_response_id = None
        self.current_audio_samples = 0
        self.interruption_occurred = False
        self.greeting_sent = False

    async def receive_messages(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Receive and yield messages from Gemini WebSocket."""
        if not self.is_connected or not self.ws:
            return
            
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    yield data
                except json.JSONDecodeError:
                    logger.error(f"[GEMINI-CLIENT] Failed to decode: {message[:100]}...")
        except ConnectionClosed:
            device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
            logger.info(f"[GEMINI-CLIENT] WebSocket closed ({device_info})")
            self.is_connected = False
        except Exception as e:
            logger.error(f"[GEMINI-CLIENT] Error receiving messages: {e}")
            self.is_connected = False
