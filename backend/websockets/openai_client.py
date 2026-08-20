import asyncio
import json
import uuid
import base64
import time
import websockets
import re
from websockets.exceptions import ConnectionClosed

from typing import Optional, List, Dict, Any, Union, AsyncGenerator

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.assistant import AssistantConfig
from backend.models.conversation import Conversation
from backend.functions import get_function_definitions, get_enabled_functions, normalize_function_name, execute_function

logger = get_logger(__name__)

DEFAULT_VOICE = "alloy"
DEFAULT_SYSTEM_MESSAGE = "You are a helpful voice assistant."

def normalize_functions(assistant_functions):
    """
    Преобразует список функций из UI в полные определения с параметрами.
    """
    if not assistant_functions:
        return []
    
    enabled_names = []
    
    if isinstance(assistant_functions, dict) and "enabled_functions" in assistant_functions:
        enabled_names = [normalize_function_name(name) for name in assistant_functions.get("enabled_functions", [])]
    else:
        enabled_names = [normalize_function_name(func.get("name")) for func in assistant_functions if func.get("name")]
        
    return get_enabled_functions(enabled_names)

def extract_webhook_url_from_prompt(prompt: str) -> Optional[str]:
    """
    Извлекает URL вебхука из системного промпта ассистента.
    """
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
    """
    Генерирует короткий уникальный идентификатор длиной до 32 символов.
    """
    raw_id = str(uuid.uuid4()).replace("-", "")
    max_id_len = 32 - len(prefix)
    return f"{prefix}{raw_id[:max_id_len]}"

def get_device_vad_settings(user_agent: str = "") -> Dict[str, Any]:
    """
    Возвращает оптимальные настройки VAD в зависимости от устройства.
    ОБНОВЛЕНЫ настройки с учетом проблем iOS аудио воспроизведения.
    """
    user_agent_lower = user_agent.lower()
    
    # Настройки для iOS - специально настроены для работы с iOS Audio API
    if "iphone" in user_agent_lower or "ipad" in user_agent_lower:
        return {
            "threshold": 0.35,        # Немного повышен для стабильности iOS
            "prefix_padding_ms": 250, # Увеличен для лучшей синхронизации с iOS аудио
            "silence_duration_ms": 400 # Увеличен для избежания обрывов на iOS
        }
    
    # Настройки для Android - оптимизированы для перебивания
    elif "android" in user_agent_lower:
        return {
            "threshold": 0.25,       # Более чувствительный
            "prefix_padding_ms": 150, # Быстрое реагирование
            "silence_duration_ms": 250 # Короткая пауза
        }
    
    # Настройки для десктопа - максимально быстрое перебивание
    else:
        return {
            "threshold": 0.2,        # Очень чувствительный
            "prefix_padding_ms": 100, # Минимальная задержка
            "silence_duration_ms": 200 # Быстрое перебивание
        }

def get_ios_optimized_session_config(base_config: Dict[str, Any], user_agent: str = "") -> Dict[str, Any]:
    """
    Возвращает оптимизированную конфигурацию сессии для iOS устройств.
    """
    user_agent_lower = user_agent.lower()
    
    if "iphone" in user_agent_lower or "ipad" in user_agent_lower:
        # Специальные настройки для iOS
        ios_config = base_config.copy()
        
        # Более консервативные настройки VAD для iOS
        ios_config["turn_detection"]["threshold"] = 0.4
        ios_config["turn_detection"]["prefix_padding_ms"] = 300
        ios_config["turn_detection"]["silence_duration_ms"] = 500
        
        # Настройки аудио оптимизированные для iOS
        ios_config["input_audio_format"] = "pcm16"
        ios_config["output_audio_format"] = "pcm16"
        
        # Ограничиваем длину ответа для лучшей производительности на iOS
        ios_config["max_response_output_tokens"] = 300
        
        # Немного снижаем температуру для более предсказуемого поведения на iOS
        ios_config["temperature"] = 0.6
        
        logger.info(f"[iOS] Применены оптимизированные настройки для iOS устройства")
        return ios_config
    
    return base_config

class OpenAIRealtimeClient:
    """
    Client for interacting with OpenAI's Realtime API through WebSockets.
    ОБНОВЛЕН для поддержки постоянно активного микрофона, быстрого перебивания и оптимизации для iOS.
    """
    
    def __init__(
        self,
        api_key: str,
        assistant_config: AssistantConfig,
        client_id: str,
        db_session: Any = None,
        user_agent: str = ""
    ):
        """
        Initialize the OpenAI Realtime client.
        """
        self.api_key = api_key
        self.assistant_config = assistant_config
        self.client_id = client_id
        self.db_session = db_session
        self.user_agent = user_agent
        self.ws = None
        self.is_connected = False
        self.openai_url = settings.REALTIME_WS_URL
        self.session_id = str(uuid.uuid4())
        self.conversation_record_id: Optional[str] = None
        self.webhook_url = None
        self.last_function_name = None
        self.enabled_functions = []
        
        # УПРОЩЕННЫЕ состояния для обработки перебивания
        self.is_assistant_speaking = False
        self.current_response_id: Optional[str] = None
        self.current_audio_samples = 0
        self.interruption_occurred = False
        self.last_interruption_time = 0
        
        # Получаем УЛУЧШЕННЫЕ настройки VAD с учетом iOS
        self.vad_settings = get_device_vad_settings(user_agent)
        logger.info(f"[VAD] Настройки для устройства ({user_agent[:50]}): {self.vad_settings}")
        
        # Определяем тип устройства для специальной обработки
        self.is_ios = "iphone" in user_agent.lower() or "ipad" in user_agent.lower()
        self.is_android = "android" in user_agent.lower()
        self.is_mobile = self.is_ios or self.is_android
        
        if self.is_ios:
            logger.info(f"[iOS] Обнаружено iOS устройство, будут применены специальные оптимизации")
        
        # Извлекаем список разрешенных функций
        if hasattr(assistant_config, "functions"):
            functions = assistant_config.functions
            if isinstance(functions, list):
                self.enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
            elif isinstance(functions, dict) and "enabled_functions" in functions:
                self.enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
            
            logger.info(f"Извлечены разрешенные функции: {self.enabled_functions}")
        
        # Извлекаем URL вебхука из промпта только если функция send_webhook разрешена
        if "send_webhook" in self.enabled_functions and hasattr(assistant_config, "system_prompt") and assistant_config.system_prompt:
            self.webhook_url = extract_webhook_url_from_prompt(assistant_config.system_prompt)
            if self.webhook_url:
                logger.info(f"Извлечен URL вебхука из промпта: {self.webhook_url}")

    async def connect(self) -> bool:
        """
        Establish WebSocket connection to OpenAI Realtime API.
        ОБНОВЛЕН с улучшенными настройками VAD для перебивания и поддержкой iOS.
        """
        if not self.api_key:
            logger.error("OpenAI API key not provided")
            return False

        headers = [
            ("Authorization", f"Bearer {self.api_key}"),
            ("OpenAI-Beta", "realtime=v1"),
            ("User-Agent", "WellcomeAI/2.1-iOS-Optimized")
        ]
        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    self.openai_url,
                    extra_headers=headers,
                    max_size=15*1024*1024,
                    ping_interval=30,
                    ping_timeout=120,
                    close_timeout=15
                ),
                timeout=30
            )
            self.is_connected = True
            logger.info(f"Connected to OpenAI for client {self.client_id} (iOS: {self.is_ios})")

            # Получаем свежие настройки
            voice = self.assistant_config.voice or DEFAULT_VOICE
            system_message = getattr(self.assistant_config, "system_prompt", None) or DEFAULT_SYSTEM_MESSAGE
            functions = getattr(self.assistant_config, "functions", None)
            
            # Обновляем список разрешенных функций
            if functions:
                if isinstance(functions, list):
                    self.enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
                elif isinstance(functions, dict) and "enabled_functions" in functions:
                    self.enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
                
                logger.info(f"Обновлены разрешенные функции: {self.enabled_functions}")

            # Проверяем URL вебхука в промпте
            if "send_webhook" in self.enabled_functions:
                self.webhook_url = extract_webhook_url_from_prompt(system_message)
                if self.webhook_url:
                    logger.info(f"Извлечен URL вебхука из промпта: {self.webhook_url}")

            # Отправляем обновленные настройки сессии с поддержкой iOS
            if not await self.update_session(
                voice=voice,
                system_message=system_message,
                functions=functions
            ):
                logger.error("Failed to update session settings")
                await self.close()
                return False

            return True
        except asyncio.TimeoutError:
            logger.error(f"Connection timeout to OpenAI for client {self.client_id}")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to OpenAI: {e}")
            return False

    async def reconnect(self) -> bool:
        """
        Пытается переподключиться к OpenAI Realtime API после потери соединения.
        """
        logger.info(f"Попытка переподключения к OpenAI для клиента {self.client_id}")
        try:
            if self.ws:
                try:
                    await self.ws.close()
                except:
                    pass
            
            self.is_connected = False
            self.ws = None
            
            # Сбрасываем состояния
            self.is_assistant_speaking = False
            self.current_response_id = None
            self.current_audio_samples = 0
            self.interruption_occurred = False
            
            return await self.connect()
        except Exception as e:
            logger.error(f"Ошибка при переподключении к OpenAI: {e}")
            return False

    async def update_session(
        self,
        voice: str = DEFAULT_VOICE,
        system_message: str = DEFAULT_SYSTEM_MESSAGE,
        functions: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
    ) -> bool:
        """
        Update session settings on the OpenAI Realtime API side.
        ОБНОВЛЕН с улучшенными настройками VAD для быстрого перебивания и поддержкой iOS.
        """
        if not self.is_connected or not self.ws:
            logger.error("Cannot update session: not connected")
            return False
            
        # УЛУЧШЕННЫЕ настройки VAD для максимально быстрого перебивания
        turn_detection = {
            "type": "server_vad",
            "threshold": self.vad_settings["threshold"],
            "prefix_padding_ms": self.vad_settings["prefix_padding_ms"],
            "silence_duration_ms": self.vad_settings["silence_duration_ms"],
            "create_response": True,
        }
        
        logger.info(f"[VAD] Настройки для быстрого перебивания: {turn_detection}")
        
        # Получаем нормализованные определения функций
        normalized_functions = normalize_functions(functions)
        
        # Формируем tools для API
        tools = []
        for func_def in normalized_functions:
            tools.append({
                "type": "function",
                "name": func_def["name"],
                "description": func_def["description"],
                "parameters": func_def["parameters"]
            })
        
        # Обновляем список разрешенных функций
        self.enabled_functions = [normalize_function_name(tool["name"]) for tool in tools]
        logger.info(f"[FUNCTION] Активированные функции для сессии: {self.enabled_functions}")
        
        tool_choice = "auto" if tools else "none"
        
        logger.info(f"Setting up session with {len(tools)} tools, tool_choice={tool_choice}")
        
        # Включение транскрипции аудио
        input_audio_transcription = {
            "model": "whisper-1"
        }
            
        payload = {
            "type": "session.update",
            "session": {
                "turn_detection": turn_detection,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "voice": voice,
                "instructions": system_message,
                "modalities": ["text", "audio"],
                "temperature": 0.7,
                "max_response_output_tokens": 500,
                "tools": tools,
                "tool_choice": tool_choice,
                "input_audio_transcription": input_audio_transcription
            }
        }
        
        # НОВОЕ: Применяем iOS-специфичные настройки если это iOS устройство
        payload["session"] = get_ios_optimized_session_config(payload["session"], self.user_agent)
        
        try:
            await self.ws.send(json.dumps(payload))
            device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
            logger.info(f"Session settings sent for {device_info} (voice={voice}, tools={len(tools)}, VAD optimized for interruption)")
            
            if tools:
                for tool in tools:
                    logger.info(f"[FUNCTION] Enabled function: {tool['name']}")
        except Exception as e:
            logger.error(f"Error sending session.update: {e}")
            return False

        # Создаем запись в БД
        if self.db_session:
            try:
                conv = Conversation(
                    assistant_id=self.assistant_config.id,
                    session_id=self.session_id,
                    user_message="",
                    assistant_message="",
                )
                self.db_session.add(conv)
                self.db_session.commit()
                self.db_session.refresh(conv)
                self.conversation_record_id = str(conv.id)
                logger.info(f"Created conversation record: {self.conversation_record_id}")
            except Exception as e:
                logger.error(f"Error creating Conversation in DB: {e}")

        return True

    async def handle_interruption(self) -> bool:
        """
        УЛУЧШЕННАЯ обработка событий перебивания для быстрого реагирования.
        """
        try:
            current_time = time.time()
            
            # Сокращаем время защиты от повторных перебиваний для iOS
            protection_time = 0.15 if self.is_ios else 0.2
            
            if current_time - self.last_interruption_time < protection_time:
                logger.info(f"[INTERRUPTION] Игнорируем повторное перебивание (защита от дребезга: {protection_time}s)")
                return True
                
            self.last_interruption_time = current_time
            self.interruption_occurred = True
            
            logger.info(f"[INTERRUPTION] БЫСТРАЯ обработка перебивания для клиента {self.client_id} ({'iOS' if self.is_ios else 'другое устройство'})")
            
            # Мгновенно отменяем текущий ответ если ассистент говорит
            if self.is_assistant_speaking and self.current_response_id:
                await self.cancel_current_response(self.current_response_id, self.current_audio_samples)
            
            # Мгновенно сбрасываем состояние
            self.is_assistant_speaking = False
            self.current_response_id = None
            self.current_audio_samples = 0
            
            logger.info("[INTERRUPTION] Быстрое перебивание обработано успешно")
            return True
            
        except Exception as e:
            logger.error(f"[INTERRUPTION] Ошибка при обработке перебивания: {e}")
            return False

    async def cancel_current_response(self, item_id: str = None, sample_count: int = 0) -> bool:
        """
        УЛУЧШЕННАЯ отмена текущего ответа ассистента при перебивании.
        """
        if not self.is_connected or not self.ws:
            logger.error("[INTERRUPTION] Нельзя отменить ответ: нет соединения")
            return False
            
        try:
            logger.info(f"[INTERRUPTION] МГНОВЕННАЯ отмена ответа: item_id={item_id}, samples={sample_count}")
            
            # Отправляем команду отмены БЕЗ задержек
            cancel_payload = {
                "type": "response.cancel",
                "event_id": f"cancel_{int(time.time() * 1000)}"  # Более точный timestamp
            }
            
            if item_id:
                cancel_payload["item_id"] = item_id
            if sample_count > 0:
                cancel_payload["sample_count"] = sample_count
                
            await self.ws.send(json.dumps(cancel_payload))
            logger.info("[INTERRUPTION] Команда мгновенной отмены отправлена")
            
            return True
            
        except Exception as e:
            logger.error(f"[INTERRUPTION] Ошибка при отмене ответа: {e}")
            return False

    async def clear_audio_buffer_on_interruption(self) -> bool:
        """
        Очищает буфер аудио при перебивании.
        """
        if not self.is_connected or not self.ws:
            return False
            
        try:
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.clear",
                "event_id": f"clear_interrupt_{int(time.time() * 1000)}"
            }))
            logger.info("[INTERRUPTION] Буфер аудио очищен после перебивания")
            return True
        except Exception as e:
            logger.error(f"[INTERRUPTION] Ошибка очистки буфера: {e}")
            return False

    def set_assistant_speaking(self, speaking: bool, response_id: str = None) -> None:
        """
        Устанавливает состояние говорения ассистента.
        УПРОЩЕН для лучшей синхронизации.
        """
        self.is_assistant_speaking = speaking
        if speaking:
            self.current_response_id = response_id
            self.current_audio_samples = 0
            device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
            logger.info(f"[SPEECH {device_info}] Ассистент начал говорить: response_id={response_id}")
        else:
            self.current_response_id = None
            self.current_audio_samples = 0
            device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
            logger.info(f"[SPEECH {device_info}] Ассистент закончил говорить")

    def increment_audio_samples(self, sample_count: int) -> None:
        """
        Увеличивает счетчик аудио-семплов для точной отмены.
        """
        self.current_audio_samples += sample_count

    async def handle_function_call(self, function_call_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a function call from OpenAI.
        """
        try:
            function_name = function_call_data.get("function", {}).get("name")
            arguments = function_call_data.get("function", {}).get("arguments", {})
            
            self.last_function_name = function_name
            
            normalized_function_name = normalize_function_name(function_name) or function_name
            logger.info(f"[FUNCTION] Нормализация имени функции: {function_name} -> {normalized_function_name}")
            
            if normalized_function_name not in self.enabled_functions:
                error_msg = f"Попытка вызвать неразрешенную функцию: {normalized_function_name}. Разрешены только: {self.enabled_functions}"
                logger.warning(error_msg)
                return {
                    "error": error_msg,
                    "status": "error",
                    "message": f"Функция {normalized_function_name} не активирована для этого ассистента"
                }
            
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse function arguments as JSON: {arguments}")
                    arguments = {}
            
            context = {
                "assistant_config": self.assistant_config,
                "client_id": self.client_id,
                "db_session": self.db_session
            }
            
            result = await execute_function(
                name=normalized_function_name,
                arguments=arguments,
                context=context
            )
            
            return result
        except Exception as e:
            logger.error(f"Error processing function call: {e}")
            return {"error": str(e)}

    async def send_function_result(self, function_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send the result of a function execution back to OpenAI.
        """
        if not self.is_connected or not self.ws:
            error_msg = "Cannot send function result: not connected"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "payload": None
            }
        
        try:
            logger.info(f"[FUNCTION] Начало отправки результата функции: {function_call_id}")
            
            short_item_id = generate_short_id("func_")
            
            result_json = json.dumps(result)
            
            payload = {
                "type": "conversation.item.create",
                "event_id": f"funcres_{int(time.time() * 1000)}",
                "item": {
                    "id": short_item_id,
                    "type": "function_call_output",
                    "call_id": function_call_id,
                    "output": result_json
                }
            }
            
            logger.info(f"Отправка результата функции: {function_call_id}")
            
            await self.ws.send(json.dumps(payload))
            logger.info(f"Результат функции отправлен: {function_call_id}")
            
            # Небольшая задержка перед запросом нового ответа
            delay = 0.2 if self.is_ios else 0.3  # Меньше для iOS
            await asyncio.sleep(delay)
            
            # Запрашиваем новый ответ от модели
            await self.create_response_after_function()
            
            logger.info(f"[FUNCTION] Результат функции отправлен и запрос на новый ответ выполнен")
            
            return {
                "success": True,
                "error": None,
                "payload": payload
            }
            
        except Exception as e:
            error_msg = f"Error sending function result: {e}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "payload": None
            }

    async def create_response_after_function(self) -> bool:
        """
        Явно запрашивает новый ответ от модели после выполнения функции.
        """
        if not self.is_connected or not self.ws:
            logger.error("Cannot create response: not connected")
            return False
            
        try:
            logger.info(f"[FUNCTION] Создание нового ответа после выполнения функции")
            
            # iOS-оптимизированные настройки ответа
            max_tokens = 200 if self.is_ios else 300
            temperature = 0.6 if self.is_ios else 0.7
            
            response_payload = {
                "type": "response.create",
                "event_id": f"resp_after_func_{int(time.time() * 1000)}",
                "response": {
                    "modalities": ["text", "audio"],
                    "voice": self.assistant_config.voice or DEFAULT_VOICE,
                    "instructions": getattr(self.assistant_config, "system_prompt", None) or DEFAULT_SYSTEM_MESSAGE,
                    "temperature": temperature,
                    "max_output_tokens": max_tokens
                }
            }
            
            await self.ws.send(json.dumps(response_payload))
            device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
            logger.info(f"Запрошен новый ответ после выполнения функции ({device_info})")
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating response after function: {e}")
            return False

    async def process_audio(self, audio_buffer: bytes) -> bool:
        """
        Process and send audio data to the OpenAI API.
        УПРОЩЕН для лучшей производительности.
        """
        if not self.is_connected or not self.ws or not audio_buffer:
            return False
        try:
            data_b64 = base64.b64encode(audio_buffer).decode("utf-8")
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": data_b64,
                "event_id": f"audio_{int(time.time() * 1000)}"
            }))
            return True
        except ConnectionClosed:
            logger.error("Connection closed while sending audio data")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"Error processing audio: {e}")
            return False

    async def commit_audio(self) -> bool:
        """
        Commit the audio buffer, indicating that the user has finished speaking.
        """
        if not self.is_connected or not self.ws:
            return False
        try:
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.commit",
                "event_id": f"commit_{int(time.time() * 1000)}"
            }))
            return True
        except ConnectionClosed:
            logger.error("Connection closed while committing audio")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"Error committing audio: {e}")
            return False

    async def clear_audio_buffer(self) -> bool:
        """
        Clear the audio buffer, removing any pending audio data.
        """
        if not self.is_connected or not self.ws:
            return False
        try:
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.clear",
                "event_id": f"clear_{int(time.time() * 1000)}"
            }))
            return True
        except ConnectionClosed:
            logger.error("Connection closed while clearing audio buffer")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"Error clearing audio buffer: {e}")
            return False

    async def close(self) -> None:
        """
        Close the WebSocket connection.
        """
        if self.ws:
            try:
                await self.ws.close()
                device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
                logger.info(f"WebSocket connection closed for client {self.client_id} ({device_info})")
            except Exception as e:
                logger.error(f"Error closing OpenAI WebSocket: {e}")
        self.is_connected = False
        
        # Сбрасываем состояния
        self.is_assistant_speaking = False
        self.current_response_id = None
        self.current_audio_samples = 0
        self.interruption_occurred = False

    async def receive_messages(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Receive and yield messages from the OpenAI WebSocket.
        """
        if not self.is_connected or not self.ws:
            return
            
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    yield data
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode message: {message[:100]}...")
        except ConnectionClosed:
            device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
            logger.info(f"WebSocket connection closed for client {self.client_id} ({device_info})")
            self.is_connected = False
        except Exception as e:
            logger.error(f"Error receiving messages: {e}")
            self.is_connected = False
