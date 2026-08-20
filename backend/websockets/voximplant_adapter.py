# backend/websockets/voximplant_adapter.py - v2.1 с логированием номера телефона

import asyncio
import json
import uuid
import base64
import time
import struct
from typing import Dict, Optional, Any
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.models.assistant import AssistantConfig
from backend.models.conversation import Conversation
from backend.models.user import User
from backend.utils.audio_utils import base64_to_audio_buffer, audio_buffer_to_base64
from backend.services.user_service import UserService
from backend.services.google_sheets_service import GoogleSheetsService  # 🆕 ДОБАВЛЕНО

logger = get_logger(__name__)

class MockWebSocket:
    """
    Мок WebSocket для создания внутренней связи между Voximplant адаптером 
    и существующим обработчиком ассистента
    """
    def __init__(self, partner=None):
        self.partner = partner
        self.message_queue = asyncio.Queue()
        self.is_closed = False
        self.client_state = type('ClientState', (), {'CONNECTED': 1})()
        
    async def accept(self):
        """Имитирует принятие WebSocket соединения"""
        pass
        
    async def send_json(self, data: dict):
        """Отправляет JSON сообщение партнеру"""
        if not self.is_closed and self.partner:
            await self.partner.message_queue.put({"type": "json", "data": data})
            
    async def send_bytes(self, data: bytes):
        """Отправляет байтовые данные партнеру"""
        if not self.is_closed and self.partner:
            await self.partner.message_queue.put({"type": "bytes", "data": data})
            
    async def send_text(self, data: str):
        """Отправляет текстовые данные партнеру"""
        if not self.is_closed and self.partner:
            await self.partner.message_queue.put({"type": "text", "data": data})
            
    async def receive(self):
        """Получает сообщение из очереди"""
        if self.is_closed:
            raise WebSocketDisconnect()
        
        try:
            message = await asyncio.wait_for(self.message_queue.get(), timeout=0.1)
            if message["type"] == "json":
                return {"text": json.dumps(message["data"])}
            elif message["type"] == "bytes":
                return {"bytes": message["data"]}
            else:
                return {"text": message["data"]}
        except asyncio.TimeoutError:
            await asyncio.sleep(0.01)
            return await self.receive()
                    
    async def close(self):
        """Закрывает WebSocket соединение"""
        self.is_closed = True
        if self.partner:
            self.partner.is_closed = True

class VoximplantAdapter:
    """
    Адаптер для интеграции Voximplant с ассистентом v2.1
    Правильно обрабатывает PCM16 аудио и логирует номер телефона.
    """
    
    def __init__(self, voximplant_ws: WebSocket, assistant_id: str, db: Session):
        self.voximplant_ws = voximplant_ws
        self.assistant_id = assistant_id
        self.db = db
        self.client_id = str(uuid.uuid4())
        self.is_connected = False
        self.assistant_ws = None
        self.server_ws = None
        
        # 🆕 v2.1: Caller information
        self.caller_number = "unknown"
        self.call_id = "unknown"
        
        # Правильные параметры для PCM16
        self.audio_buffer = bytearray()
        self.last_audio_time = time.time()
        self.audio_chunk_size = 1280  # 40мс при 16kHz, 16bit mono = 1280 байт
        self.sample_rate = 16000  # 16kHz как ожидает OpenAI
        self.is_assistant_speaking = False
        
        # 🆕 v2.1: Транскрипции для логирования
        self.user_transcript = ""
        self.assistant_transcript = ""
        self.function_result = None
        
        # Задачи для фонового выполнения
        self.background_tasks = []
        
        logger.info(f"[VOXIMPLANT-v2.1] Создан адаптер для assistant_id={assistant_id}, client_id={self.client_id}")
        logger.info(f"[VOXIMPLANT-v2.1] Аудио настройки: PCM16, {self.sample_rate}Hz, chunk_size={self.audio_chunk_size}")

    async def start(self):
        """Запускает адаптер и устанавливает соединения"""
        try:
            await self.voximplant_ws.accept()
            self.is_connected = True
            logger.info(f"[VOXIMPLANT-v2.1] WebSocket соединение принято от Voximplant")
            
            # Создаем внутреннее соединение с обработчиком ассистента
            await self.create_internal_connection()
            
            # Отправляем правильный статус подключения
            await self.send_to_voximplant({
                "type": "connection_status",
                "status": "connected",
                "message": "Connection established"
            })
            
            # Запускаем обработку сообщений от Voximplant
            await self.handle_voximplant_messages()
            
        except Exception as e:
            logger.error(f"[VOXIMPLANT-v2.1] Ошибка запуска адаптера: {e}")
            await self.cleanup()

    async def create_internal_connection(self):
        """Создает внутреннее соединение с обработчиком ассистента"""
        try:
            # Создаем пару WebSocket соединений для внутренней связи
            self.server_ws, self.assistant_ws = self.create_websocket_pair()
            
            # Импортируем обработчик здесь, чтобы избежать циклических импортов
            from backend.websockets.handler_realtime_new import handle_websocket_connection_new
            
            # Запускаем обработчик ассистента в отдельной задаче
            assistant_task = asyncio.create_task(
                handle_websocket_connection_new(self.server_ws, self.assistant_id, self.db)
            )
            self.background_tasks.append(assistant_task)
            
            # Запускаем обработку ответов от ассистента
            response_task = asyncio.create_task(self.handle_assistant_responses())
            self.background_tasks.append(response_task)
            
            logger.info(f"[VOXIMPLANT-v2.1] Внутреннее соединение с ассистентом установлено")
            
        except Exception as e:
            logger.error(f"[VOXIMPLANT-v2.1] Ошибка создания внутреннего соединения: {e}")
            raise

    def create_websocket_pair(self):
        """Создает пару связанных WebSocket соединений для внутренней связи"""
        server_ws = MockWebSocket()
        client_ws = MockWebSocket(server_ws)
        server_ws.partner = client_ws
        
        return server_ws, client_ws

    async def handle_voximplant_messages(self):
        """Обрабатывает сообщения от Voximplant"""
        try:
            while self.is_connected:
                message = await self.voximplant_ws.receive()
                
                if "text" in message:
                    try:
                        data = json.loads(message["text"])
                        await self.handle_text_message(data)
                    except json.JSONDecodeError:
                        logger.warning(f"[VOXIMPLANT-v2.1] Некорректный JSON: {message['text'][:100]}")
                elif "bytes" in message:
                    await self.handle_audio_message(message["bytes"])
                    
        except WebSocketDisconnect:
            logger.info(f"[VOXIMPLANT-v2.1] Соединение закрыто")
        except Exception as e:
            logger.error(f"[VOXIMPLANT-v2.1] Ошибка обработки сообщений: {e}")
        finally:
            await self.cleanup()

    async def handle_text_message(self, data: dict):
        """Обрабатывает текстовые сообщения от Voximplant"""
        msg_type = data.get("type", "")
        
        if msg_type:
            logger.info(f"[VOXIMPLANT-v2.1] Получено сообщение: {msg_type}")
        else:
            logger.warning(f"[VOXIMPLANT-v2.1] Получено сообщение без типа: {data}")
            return
        
        if msg_type == "call_started":
            # 🆕 v2.1: Сохраняем номер телефона и call_id
            self.caller_number = data.get("caller_number", "unknown")
            self.call_id = data.get("call_id", "unknown")
            
            logger.info(f"[VOXIMPLANT-v2.1] Звонок начат: caller={self.caller_number}, call_id={self.call_id}")
            logger.info(f"[VOXIMPLANT-v2.1] 📞 Сохранен номер телефона: {self.caller_number}")
            
        elif msg_type == "audio_ready":
            audio_format = data.get("format", "pcm16")
            sample_rate = data.get("sample_rate", 16000)
            channels = data.get("channels", 1)
            
            logger.info(f"[VOXIMPLANT-v2.1] Аудио готово: {audio_format}, {sample_rate}Hz, {channels} канал(ов)")
            
        elif msg_type == "call_ended":
            logger.info(f"[VOXIMPLANT-v2.1] Звонок завершен: {self.call_id}")
            await self.cleanup()
            
        elif msg_type == "interruption.manual":
            # Ручное перебивание
            await self.send_to_assistant({
                "type": "interruption.manual",
                "event_id": f"voximplant_interrupt_{int(time.time() * 1000)}"
            })
            
        elif msg_type == "microphone.state":
            mic_enabled = data.get("enabled", True)
            logger.info(f"[VOXIMPLANT-v2.1] Микрофон: {'включен' if mic_enabled else 'выключен'}")

    async def handle_audio_message(self, audio_data: bytes):
        """Правильная обработка PCM16 аудио данных от Voximplant"""
        try:
            # Voximplant отправляет PCM16 data с частотой 16kHz
            self.audio_buffer.extend(audio_data)
            self.last_audio_time = time.time()
            
            # Отправляем аудио чанками оптимального размера для 16kHz PCM16
            while len(self.audio_buffer) >= self.audio_chunk_size:
                chunk = bytes(self.audio_buffer[:self.audio_chunk_size])
                self.audio_buffer = self.audio_buffer[self.audio_chunk_size:]
                
                # Конвертируем PCM16 в base64 для OpenAI
                audio_b64 = audio_buffer_to_base64(chunk)
                
                # Отправляем в формате OpenAI Realtime API
                await self.send_to_assistant({
                    "type": "input_audio_buffer.append",
                    "audio": audio_b64,
                    "event_id": f"voximplant_audio_{int(time.time() * 1000)}"
                })
            
            # Запускаем автоматический коммит через паузу
            asyncio.create_task(self.auto_commit_audio())
            
        except Exception as e:
            logger.error(f"[VOXIMPLANT-v2.1] Ошибка обработки аудио: {e}")

    async def auto_commit_audio(self):
        """Автоматически коммитит аудио буфер после паузы"""
        await asyncio.sleep(0.6)
        
        if time.time() - self.last_audio_time >= 0.5:
            await self.send_to_assistant({
                "type": "input_audio_buffer.commit",
                "event_id": f"voximplant_commit_{int(time.time() * 1000)}"
            })

    async def handle_assistant_responses(self):
        """Обрабатывает ответы от ассистента"""
        try:
            while self.is_connected and self.assistant_ws and not self.assistant_ws.is_closed:
                try:
                    message = await self.assistant_ws.receive()
                    
                    if "text" in message:
                        response = json.loads(message["text"])
                        await self.handle_assistant_response(response)
                    elif "bytes" in message:
                        await self.send_audio_to_voximplant(message["bytes"])
                        
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"[VOXIMPLANT-v2.1] Ошибка чтения ответа ассистента: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"[VOXIMPLANT-v2.1] Ошибка обработки ответов ассистента: {e}")

    async def handle_assistant_response(self, response: dict):
        """Обрабатывает конкретный ответ от ассистента"""
        msg_type = response.get("type", "")
        
        # Пробрасываем важные события в Voximplant
        if msg_type in [
            "error", 
            "function_call.start", 
            "function_call.completed",
            "session.interrupted",
            "conversation.interrupted"
        ]:
            await self.send_to_voximplant(response)
        
        # Специальная обработка аудио событий
        elif msg_type == "response.audio.delta":
            delta_audio = response.get("delta", "")
            if delta_audio:
                try:
                    audio_bytes = base64_to_audio_buffer(delta_audio)
                    await self.send_audio_to_voximplant(audio_bytes)
                except Exception as e:
                    logger.error(f"[VOXIMPLANT-v2.1] Ошибка отправки аудио: {e}")
        
        # 🆕 v2.1: Сохраняем транскрипции для логирования
        elif msg_type == "conversation.item.input_audio_transcription.completed":
            self.user_transcript = response.get("transcript", "")
            logger.info(f"[VOXIMPLANT-v2.1] 👤 User: {self.user_transcript}")
            
        elif msg_type == "response.audio_transcript.done":
            self.assistant_transcript = response.get("transcript", "")
            logger.info(f"[VOXIMPLANT-v2.1] 🤖 Assistant: {self.assistant_transcript}")
        
        # 🆕 v2.1: Сохраняем результат функции
        elif msg_type == "response.function_call_arguments.done":
            function_name = response.get("name", "")
            logger.info(f"[VOXIMPLANT-v2.1] 🔧 Function called: {function_name}")
        
        # 🆕 v2.1: При завершении ответа - логируем в Google Sheets
        elif msg_type == "response.done":
            await self.log_conversation()
        
        # Отслеживание состояния речи ассистента
        elif msg_type == "assistant.speech.started":
            self.is_assistant_speaking = True
            await self.send_to_voximplant({
                "type": "assistant_speaking",
                "speaking": True,
                "timestamp": time.time()
            })
            
        elif msg_type == "assistant.speech.ended":
            self.is_assistant_speaking = False
            await self.send_to_voximplant({
                "type": "assistant_speaking", 
                "speaking": False,
                "timestamp": time.time()
            })
            
        elif msg_type in ["conversation.interrupted", "response.cancelled"]:
            self.is_assistant_speaking = False
            await self.send_to_voximplant({
                "type": "conversation.interrupted",
                "timestamp": time.time()
            })

    async def log_conversation(self):
        """🆕 v2.1: Логирование разговора в Google Sheets"""
        try:
            # Получаем конфигурацию ассистента для доступа к google_sheet_id
            assistant = self.db.query(AssistantConfig).filter(
                AssistantConfig.id == uuid.UUID(self.assistant_id)
            ).first()
            
            if not assistant:
                logger.warning(f"[VOXIMPLANT-v2.1] Ассистент {self.assistant_id} не найден для логирования")
                return
            
            if not hasattr(assistant, 'google_sheet_id') or not assistant.google_sheet_id:
                logger.info(f"[VOXIMPLANT-v2.1] Google Sheet ID не настроен для ассистента {self.assistant_id}")
                return
            
            # Логируем в Google Sheets с номером телефона
            await GoogleSheetsService.log_conversation(
                sheet_id=assistant.google_sheet_id,
                user_message=self.user_transcript,
                assistant_message=self.assistant_transcript,
                function_result=self.function_result,
                conversation_id=self.call_id,  # Используем call_id как conversation_id
                caller_number=self.caller_number  # 🆕 Передаем номер телефона
            )
            
            logger.info(f"[VOXIMPLANT-v2.1] ✅ Разговор записан в Google Sheets с номером: {self.caller_number}")
            
            # Сбрасываем транскрипции
            self.user_transcript = ""
            self.assistant_transcript = ""
            self.function_result = None
            
        except Exception as e:
            logger.error(f"[VOXIMPLANT-v2.1] Ошибка логирования в Google Sheets: {e}")

    async def send_audio_to_voximplant(self, audio_bytes: bytes):
        """Отправляет аудио данные в Voximplant в правильном формате"""
        try:
            if self.is_connected and audio_bytes:
                await self.voximplant_ws.send_bytes(audio_bytes)
                
                # Периодическое логирование для отладки
                if hasattr(self, '_audio_log_counter'):
                    self._audio_log_counter += 1
                else:
                    self._audio_log_counter = 1
                    
                if self._audio_log_counter % 50 == 0:
                    logger.info(f"[VOXIMPLANT-v2.1] Отправлено аудио пакетов: {self._audio_log_counter}, размер: {len(audio_bytes)} байт")
                    
        except Exception as e:
            logger.error(f"[VOXIMPLANT-v2.1] Ошибка отправки аудио в Voximplant: {e}")

    async def send_to_assistant(self, message: dict):
        """Отправляет сообщение ассистенту"""
        if self.assistant_ws and not self.assistant_ws.is_closed:
            try:
                await self.assistant_ws.send_json(message)
            except Exception as e:
                logger.error(f"[VOXIMPLANT-v2.1] Ошибка отправки сообщения ассистенту: {e}")

    async def send_to_voximplant(self, message: dict):
        """Отправляет сообщение в Voximplant"""
        if self.is_connected:
            try:
                await self.voximplant_ws.send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"[VOXIMPLANT-v2.1] Ошибка отправки сообщения в Voximplant: {e}")

    async def cleanup(self):
        """Очищает ресурсы"""
        logger.info(f"[VOXIMPLANT-v2.1] Начало очистки адаптера для client_id={self.client_id}")
        logger.info(f"[VOXIMPLANT-v2.1] 📞 Номер звонившего: {self.caller_number}")
        
        self.is_connected = False
        self.is_assistant_speaking = False
        
        # Отменяем все фоновые задачи
        for task in self.background_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"[VOXIMPLANT-v2.1] Ошибка при отмене задачи: {e}")
        
        # Закрываем внутренние WebSocket соединения
        if self.assistant_ws and not self.assistant_ws.is_closed:
            await self.assistant_ws.close()
            
        if self.server_ws and not self.server_ws.is_closed:
            await self.server_ws.close()
            
        logger.info(f"[VOXIMPLANT-v2.1] Адаптер очищен для client_id={self.client_id}")


async def handle_voximplant_websocket(websocket: WebSocket, assistant_id: str, db: Session):
    """
    Основная функция для обработки WebSocket соединений от Voximplant v2.1
    """
    adapter = None
    try:
        # Проверяем существование ассистента
        if assistant_id == "demo":
            assistant = db.query(AssistantConfig).filter(AssistantConfig.is_public.is_(True)).first()
            if not assistant:
                assistant = db.query(AssistantConfig).first()
            logger.info(f"[VOXIMPLANT-v2.1] Использование ассистента {assistant.id if assistant else 'None'} для демо")
        else:
            try:
                uuid_obj = uuid.UUID(assistant_id)
                assistant = db.query(AssistantConfig).get(uuid_obj)
            except ValueError:
                assistant = db.query(AssistantConfig).filter(AssistantConfig.id.cast(str) == assistant_id).first()

        if not assistant:
            logger.error(f"[VOXIMPLANT-v2.1] Ассистент {assistant_id} не найден")
            await websocket.accept()
            await websocket.send_text(json.dumps({
                "type": "error",
                "error": {"code": "assistant_not_found", "message": "Assistant not found"}
            }))
            await websocket.close(code=1008)
            return

        # Проверяем подписку пользователя
        if assistant.user_id and not assistant.is_public:
            try:
                db.refresh(assistant)
                user = db.query(User).get(assistant.user_id)
                if user and not user.is_admin and user.email != "well96well@gmail.com":
                    subscription_status = await UserService.check_subscription_status(db, str(user.id))
                    
                    if not subscription_status["active"]:
                        logger.warning(f"[VOXIMPLANT-v2.1] Доступ заблокирован для пользователя {user.id} - подписка истекла")
                        
                        await websocket.accept()
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "error": {
                                "code": "SUBSCRIPTION_EXPIRED",
                                "message": "Подписка истекла. Обновите подписку для продолжения использования.",
                                "subscription_status": subscription_status,
                                "requires_payment": True
                            }
                        }))
                        await websocket.close(code=1008)
                        return
            except Exception as db_error:
                logger.error(f"[VOXIMPLANT-v2.1] Ошибка проверки подписки: {db_error}")

        # Создаем копию сессии БД для адаптера
        try:
            from backend.db.session import SessionLocal
            adapter_db = SessionLocal()
            
            # Создаем и запускаем адаптер
            adapter = VoximplantAdapter(websocket, assistant_id, adapter_db)
            await adapter.start()
            
        finally:
            if 'adapter_db' in locals():
                adapter_db.close()
        
    except Exception as e:
        logger.error(f"[VOXIMPLANT-v2.1] Ошибка обработки WebSocket: {e}")
        
        try:
            if not adapter:
                await websocket.accept()
            await websocket.send_text(json.dumps({
                "type": "error",
                "error": {
                    "code": "server_error",
                    "message": "Внутренняя ошибка сервера"
                }
            }))
        except:
            pass
        
        if adapter:
            await adapter.cleanup()
        else:
            try:
                await websocket.close()
            except:
                pass
