"""
🧪 ЭКСПЕРИМЕНТАЛЬНЫЙ WebSocket Handler для оптимизированного стриминга

Особенности:
- Sentence-based TTS streaming для минимальной latency
- Parallel processing: LLM генерирует → ElevenLabs сразу озвучивает готовые фразы
- Aggressive interruption handling
- Realtime streaming: audio chunks отправляются клиенту по мере генерации
"""

import asyncio
import json
import logging
import httpx
from fastapi import WebSocket, WebSocketDisconnect
from typing import Optional
import time

from .openai_client_streaming import OpenAIRealtimeClientStreaming
from backend.config import settings

logger = logging.getLogger(__name__)


class RealtimeStreamingHandler:
    """
    Обработчик для optimized realtime streaming с sentence detection
    """
    
    def __init__(
        self,
        websocket: WebSocket,
        use_external_tts: bool = False  # True = ElevenLabs, False = OpenAI встроенный
    ):
        self.websocket = websocket
        self.use_external_tts = use_external_tts
        
        # OpenAI клиент
        self.openai_client: Optional[OpenAIRealtimeClientStreaming] = None
        
        # Состояние
        self.is_connected = False
        self.session_active = False
        
        # Таймеры для метрик
        self.start_time = None
        self.first_audio_time = None
        
        # Для external TTS
        self.tts_queue = asyncio.Queue()  # Очередь предложений для TTS
        self.tts_task: Optional[asyncio.Task] = None
    
    async def handle_connection(self):
        """Основной метод обработки WebSocket соединения"""
        try:
            # Принимаем соединение
            await self.websocket.accept()
            self.is_connected = True
            logger.info("🔌 WebSocket connected (streaming mode)")
            
            # Отправляем приветствие
            await self._send_event("connection.established", {
                "mode": "streaming",
                "use_external_tts": self.use_external_tts,
                "tts_provider": "elevenlabs" if self.use_external_tts else "openai"
            })
            
            # Инициализируем OpenAI клиент
            await self._initialize_openai_client()
            
            # Запускаем TTS процессор если нужен внешний TTS
            if self.use_external_tts:
                self.tts_task = asyncio.create_task(self._tts_processor())
            
            # Основной цикл обработки сообщений
            await self._message_loop()
            
        except WebSocketDisconnect:
            logger.info("👋 Client disconnected")
        except Exception as e:
            logger.error(f"❌ Handler error: {e}", exc_info=True)
            await self._send_event("error", {"message": str(e)})
        finally:
            await self._cleanup()
    
    async def _initialize_openai_client(self):
        """Инициализация OpenAI Realtime клиента"""
        try:
            self.openai_client = OpenAIRealtimeClientStreaming(
                api_key=settings.openai_api_key,
                model="gpt-4o-realtime-preview-2024-12-17",
                voice="alloy",
                language="ru",
                temperature=0.8,
                max_tokens=300  # Короткие ответы для лучшей latency
            )
            
            # Настраиваем колбэки
            self.openai_client.on_audio_delta = self._on_audio_delta
            self.openai_client.on_text_delta = self._on_text_delta
            self.openai_client.on_sentence_ready = self._on_sentence_ready
            self.openai_client.on_error = self._on_error
            self.openai_client.on_interruption = self._on_interruption
            
            # Подключаемся
            await self.openai_client.connect()
            self.session_active = True
            
            await self._send_event("session.ready", {
                "session_id": self.openai_client.session_id
            })
            
            logger.info("✅ OpenAI client initialized and ready")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize OpenAI client: {e}")
            raise
    
    async def _message_loop(self):
        """Основной цикл обработки сообщений от клиента"""
        while self.is_connected and self.session_active:
            try:
                # Получаем сообщение от клиента
                message = await asyncio.wait_for(
                    self.websocket.receive(),
                    timeout=60.0  # 60 секунд таймаут
                )
                
                # Обрабатываем в зависимости от типа
                if "text" in message:
                    await self._handle_text_message(message["text"])
                elif "bytes" in message:
                    await self._handle_audio_message(message["bytes"])
                    
            except asyncio.TimeoutError:
                # Отправляем ping для поддержания соединения
                await self._send_event("ping", {})
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"❌ Message loop error: {e}")
                break
    
    async def _handle_text_message(self, text: str):
        """Обработка текстовых команд от клиента"""
        try:
            data = json.loads(text)
            event_type = data.get("type")
            
            if event_type == "audio.commit":
                # Клиент закончил отправлять аудио
                self.start_time = time.time()
                await self.openai_client.commit_audio_buffer()
                logger.info("🎤 Audio buffer committed, starting response")
            
            elif event_type == "interrupt":
                # Клиент прерывает текущий ответ
                await self.openai_client.cancel_response()
                logger.info("🛑 Response interrupted by client")
            
            elif event_type == "get.stats":
                # Запрос статистики
                stats = self.openai_client.get_stats()
                await self._send_event("stats", stats)
            
            elif event_type == "ping":
                # Pong ответ
                await self._send_event("pong", {})
            
            else:
                logger.warning(f"⚠️ Unknown event type: {event_type}")
                
        except json.JSONDecodeError:
            logger.error(f"❌ Invalid JSON: {text}")
        except Exception as e:
            logger.error(f"❌ Error handling text message: {e}")
    
    async def _handle_audio_message(self, audio_bytes: bytes):
        """Обработка аудио данных от клиента"""
        try:
            # Отправляем аудио в OpenAI
            await self.openai_client.send_audio_chunk(audio_bytes)
            
        except Exception as e:
            logger.error(f"❌ Error handling audio: {e}")
    
    # ==================== КОЛБЭКИ ОТ OPENAI ====================
    
    async def _on_audio_delta(self, audio_bytes: bytes):
        """
        Колбэк: получен аудио chunk от OpenAI
        Используется только если use_external_tts=False
        """
        if not self.use_external_tts:
            # Отправляем встроенное аудио от OpenAI напрямую клиенту
            try:
                await self.websocket.send_bytes(audio_bytes)
                
                # Засекаем первый аудио chunk для метрик
                if self.first_audio_time is None and self.start_time:
                    self.first_audio_time = time.time()
                    latency = (self.first_audio_time - self.start_time) * 1000
                    logger.info(f"⚡ First audio latency: {latency:.0f}ms")
                    await self._send_event("metrics.first_audio", {
                        "latency_ms": latency
                    })
                    
            except Exception as e:
                logger.error(f"❌ Error sending audio: {e}")
    
    async def _on_text_delta(self, text_delta: str):
        """
        Колбэк: получен текстовый delta от OpenAI
        Отправляем клиенту для отображения транскрипта
        """
        try:
            await self._send_event("text.delta", {
                "delta": text_delta
            })
        except Exception as e:
            logger.error(f"❌ Error sending text delta: {e}")
    
    async def _on_sentence_ready(self, sentence: str):
        """
        🔥 Колбэк: готово целое предложение для TTS
        Это ключевая оптимизация для минимальной latency
        """
        logger.info(f"📤 Sentence ready: {sentence[:60]}...")
        
        if self.use_external_tts:
            # Добавляем в очередь для ElevenLabs TTS
            await self.tts_queue.put(sentence)
        else:
            # При use_external_tts=False просто логируем
            # (аудио уже идет через _on_audio_delta)
            await self._send_event("sentence.ready", {
                "text": sentence
            })
        
        # Засекаем время первого предложения
        if self.start_time and not self.first_audio_time:
            first_sentence_time = time.time()
            latency = (first_sentence_time - self.start_time) * 1000
            logger.info(f"⚡ First sentence latency: {latency:.0f}ms")
    
    async def _on_error(self, error_message: str):
        """Колбэк: ошибка от OpenAI"""
        logger.error(f"❌ OpenAI error: {error_message}")
        await self._send_event("openai.error", {
            "message": error_message
        })
    
    async def _on_interruption(self):
        """Колбэк: произошла interruption"""
        logger.info("🛑 Interruption detected")
        
        # Очищаем TTS очередь если используем external TTS
        if self.use_external_tts:
            while not self.tts_queue.empty():
                try:
                    self.tts_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        
        await self._send_event("interruption", {})
    
    # ==================== ELEVENLABS TTS STREAMING ====================
    
    async def _tts_processor(self):
        """
        Обработчик очереди для ElevenLabs TTS
        Работает параллельно, обрабатывая готовые предложения
        Использует realtime streaming для минимальной latency
        """
        logger.info("🎵 TTS processor started (ElevenLabs streaming)")
        
        while self.session_active:
            try:
                # Ждем предложение из очереди
                sentence = await asyncio.wait_for(
                    self.tts_queue.get(),
                    timeout=1.0
                )
                
                # 🔥 Генерируем и стримим аудио через ElevenLabs
                await self._generate_tts_elevenlabs_streaming(sentence)
                logger.info(f"✅ Sentence streamed to client")
                
                # Засекаем первый TTS для метрик
                if self.first_audio_time is None and self.start_time:
                    self.first_audio_time = time.time()
                    latency = (self.first_audio_time - self.start_time) * 1000
                    logger.info(f"⚡ First TTS latency: {latency:.0f}ms")
                    await self._send_event("metrics.first_audio", {
                        "latency_ms": latency,
                        "provider": "elevenlabs"
                    })
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"❌ TTS processor error: {e}")
                await asyncio.sleep(0.1)
        
        logger.info("🎵 TTS processor stopped")
    
    async def _generate_tts_elevenlabs_streaming(self, text: str) -> None:
        """
        🔥 ElevenLabs TTS с realtime streaming
        Отправляет audio chunks клиенту СРАЗУ по мере получения от API
        Минимальная latency - не ждет полной генерации!
        """
        
        if not settings.elevenlabs_api_key:
            logger.error("❌ ElevenLabs API key not configured!")
            await self._send_event("tts.error", {
                "message": "ElevenLabs API key not configured"
            })
            return
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}/stream"
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": settings.elevenlabs_api_key
        }
        
        data = {
            "text": text,
            "model_id": settings.elevenlabs_model,  # eleven_turbo_v2_5 - fastest
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True
            },
            "optimize_streaming_latency": 4,  # Максимальная оптимизация (0-4)
            "output_format": "mp3_44100_128"  # Хороший баланс качества и размера
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("POST", url, json=data, headers=headers) as response:
                    
                    if response.status_code != 200:
                        error_text = await response.aread()
                        logger.error(f"❌ ElevenLabs API error: {response.status_code} - {error_text}")
                        await self._send_event("tts.error", {
                            "message": f"ElevenLabs error: {response.status_code}"
                        })
                        return
                    
                    first_chunk = True
                    total_bytes = 0
                    chunk_count = 0
                    
                    # 🔥 Отправляем chunks СРАЗУ по мере получения
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        if chunk:
                            # Сразу отправляем клиенту - не ждем полной генерации!
                            await self.websocket.send_bytes(chunk)
                            total_bytes += len(chunk)
                            chunk_count += 1
                            
                            if first_chunk:
                                logger.info(f"⚡ First ElevenLabs chunk sent: {len(chunk)} bytes")
                                first_chunk = False
                    
                    logger.info(f"✅ ElevenLabs streaming complete: {total_bytes} bytes in {chunk_count} chunks")
                    
                    # Отправляем событие о завершении предложения
                    await self._send_event("sentence.audio_complete", {
                        "text": text[:100],
                        "bytes": total_bytes,
                        "chunks": chunk_count
                    })
                    
        except httpx.TimeoutException:
            logger.error("❌ ElevenLabs request timeout")
            await self._send_event("tts.error", {
                "message": "ElevenLabs timeout"
            })
        except httpx.RequestError as e:
            logger.error(f"❌ ElevenLabs request error: {e}")
            await self._send_event("tts.error", {
                "message": f"Network error: {str(e)}"
            })
        except Exception as e:
            logger.error(f"❌ ElevenLabs streaming error: {e}", exc_info=True)
            await self._send_event("tts.error", {
                "message": str(e)
            })
    
    # ==================== УТИЛИТЫ ====================
    
    async def _send_event(self, event_type: str, data: dict = None):
        """Отправка события клиенту"""
        try:
            message = {
                "type": event_type,
                "timestamp": time.time(),
                **(data or {})
            }
            await self.websocket.send_json(message)
        except Exception as e:
            logger.error(f"❌ Error sending event {event_type}: {e}")
    
    async def _cleanup(self):
        """Очистка ресурсов"""
        logger.info("🧹 Cleaning up resources...")
        
        self.session_active = False
        self.is_connected = False
        
        # Останавливаем TTS processor
        if self.tts_task and not self.tts_task.done():
            self.tts_task.cancel()
            try:
                await self.tts_task
            except asyncio.CancelledError:
                pass
        
        # Отключаемся от OpenAI
        if self.openai_client:
            await self.openai_client.disconnect()
        
        # Закрываем WebSocket если еще открыт
        try:
            if self.websocket.client_state.name != "DISCONNECTED":
                await self.websocket.close()
        except:
            pass
        
        logger.info("✅ Cleanup complete")


# ==================== MAIN HANDLER FUNCTIONS ====================

async def handle_websocket_connection_streaming(
    websocket: WebSocket,
    use_external_tts: bool = False
):
    """
    🧪 Экспериментальный WebSocket handler с оптимизированным стримингом
    
    Args:
        websocket: FastAPI WebSocket
        use_external_tts: True = использовать ElevenLabs TTS с streaming
                         False = использовать встроенный TTS от OpenAI
    
    Протокол клиента:
        Отправка аудио: websocket.send(audio_bytes)
        Команды: websocket.send(json.dumps({"type": "audio.commit"}))
        Прерывание: websocket.send(json.dumps({"type": "interrupt"}))
    
    События от сервера:
        - connection.established: соединение установлено
        - session.ready: сессия OpenAI готова
        - text.delta: текстовый delta от LLM
        - sentence.ready: готово предложение
        - sentence.audio_complete: аудио для предложения отправлено
        - audio: bytes - аудио данные
        - metrics.first_audio: метрики latency
        - interruption: произошла interruption
        - tts.error: ошибка TTS
        - error: общая ошибка
    """
    handler = RealtimeStreamingHandler(
        websocket=websocket,
        use_external_tts=use_external_tts
    )
    
    await handler.handle_connection()


async def handle_websocket_connection_streaming_openai_tts(websocket: WebSocket):
    """
    Handler со встроенным TTS от OpenAI
    Плюсы: минимальная latency, меньше API calls
    Минусы: голоса OpenAI менее натуральные
    """
    await handle_websocket_connection_streaming(websocket, use_external_tts=False)


async def handle_websocket_connection_streaming_elevenlabs_tts(websocket: WebSocket):
    """
    Handler с ElevenLabs TTS streaming
    Плюсы: максимально натуральные голоса, поддержка русского
    Минусы: дополнительный API call, +50-100ms latency
    """
    await handle_websocket_connection_streaming(websocket, use_external_tts=True)
