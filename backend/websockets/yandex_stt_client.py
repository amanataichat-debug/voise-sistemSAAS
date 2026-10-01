# backend/websockets/yandex_stt_client.py
"""
Потоковое распознавание речи абонента через Yandex SpeechKit (API v3, gRPC).
Тот же интерфейс, что у ScribeSTTClient / OpenAISTTClient: connect / send_audio /
close, колбэки on_partial / on_committed / on_closed, поля sample_rate, silence_ms,
language, secondary_languages, provider_label.

    stt.api.cloud.yandex.net:443  speechkit.stt.v3.Recognizer/RecognizeStreaming
    metadata: authorization: Api-Key <YANDEX_SPEECHKIT_API_KEY>, x-folder-id: <YANDEX_FOLDER_ID>
    → StreamingRequest(session_options) → chunk(PCM16 8/16 кГц) … silence_chunk
    ← partial (текст по ходу речи) / final (итог куска) / eou_update (конец фразы)

Конец фразы определяет сам SpeechKit (DefaultEouClassifier, подсказка паузы
silence_ms); на eou_update собранные final уходят в on_committed.

Ограничения потоковой сессии SpeechKit (5 минут звука на сессию) — сессия
перезапускается заранее, в паузе между фразами. Пока клиент молчит (виджет во
время речи ассистента), шлём silence_chunk, чтобы поток не простаивал.

Языки — YANDEX_STT_LANGUAGES (по умолчанию ky-KG,ru-RU). Если SpeechKit отверг
код языка — повтор с автоопределением (auto).
"""

import asyncio
import base64
import time
from typing import Awaitable, Callable, List, Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

YANDEX_STT_HOST = "stt.api.cloud.yandex.net:443"
SESSION_ROTATE_SEC = 270.0     # лимит SpeechKit — 5 мин звука на сессию; перезапуск заранее, в паузе
FILLER_TICK_SEC = 0.2
FILLER_AFTER_SEC = 0.4
CONNECT_CHECK_SEC = 0.8        # столько ждём ошибку настроек (неверный язык и т.п.) после открытия

TextCallback = Callable[[str], Awaitable[None]]


class YandexSTTClient:
    provider = "yandex"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        languages: List[str],
        sample_rate: int = 8000,
        silence_ms: int = 500,
        model: str = "general",
        on_partial: Optional[TextCallback] = None,
        on_committed: Optional[TextCallback] = None,
        on_closed: Optional[Callable[[bool], Awaitable[None]]] = None,
        label: str = "",
    ) -> None:
        self.api_key = api_key
        self.folder_id = folder_id
        self.languages = [l for l in languages if l] or ["auto"]
        self.language = self.languages[0]
        self.secondary_languages = self.languages[1:]
        self.sample_rate = sample_rate if sample_rate in (8000, 16000, 48000) else 16000
        self.silence_ms = max(200, int(silence_ms))
        self.model = model or "general"
        self.on_partial = on_partial
        self.on_committed = on_committed
        self.on_closed = on_closed
        self.label = label

        self.is_connected = False
        self.fatal_error: Optional[str] = None
        self.audio_seconds = 0.0
        self.commits = 0
        self.transcribe_ms: List[int] = []
        self._closing = False
        self._channel = None
        self._call = None
        self._queue: "asyncio.Queue" = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._filler_task: Optional[asyncio.Task] = None
        self._last_audio_at = 0.0
        self._session_audio = 0.0
        self._finals: List[str] = []
        self._in_phrase = False
        self._rotating = False

    @property
    def provider_label(self) -> str:
        return (f"Yandex SpeechKit {self.model} ({'+'.join(self.languages)}, {self.sample_rate // 1000} кГц, "
                f"пауза {self.silence_ms} мс)")

    def _log(self, message: str, level: str = "INFO") -> None:
        text = f"[YANDEX-STT {self.label}] {message}"
        getattr(logger, {"ERROR": "error", "WARNING": "warning"}.get(level, "info"))(text)

    # ------------------------------------------------------------------ соединение
    def _options(self):
        from yandex.cloud.ai.stt.v3 import stt_pb2 as s
        return s.StreamingRequest(session_options=s.StreamingOptions(
            recognition_model=s.RecognitionModelOptions(
                model=self.model,
                audio_format=s.AudioFormatOptions(raw_audio=s.RawAudio(
                    audio_encoding=s.RawAudio.LINEAR16_PCM, sample_rate_hertz=self.sample_rate,
                    audio_channel_count=1)),
                text_normalization=s.TextNormalizationOptions(
                    text_normalization=s.TextNormalizationOptions.TEXT_NORMALIZATION_ENABLED,
                    profanity_filter=False, literature_text=False),
                language_restriction=s.LanguageRestrictionOptions(
                    restriction_type=s.LanguageRestrictionOptions.WHITELIST, language_code=self.languages),
                audio_processing_type=s.RecognitionModelOptions.REAL_TIME,
            ),
            eou_classifier=s.EouClassifierOptions(default_classifier=s.DefaultEouClassifier(
                type=s.DefaultEouClassifier.DEFAULT, max_pause_between_words_hint_ms=self.silence_ms)),
        ))

    async def connect(self) -> bool:
        if not self.api_key:
            self._log("YANDEX_SPEECHKIT_API_KEY is not configured", "ERROR")
            return False
        try:
            import grpc  # noqa: F401
            from yandex.cloud.ai.stt.v3 import stt_service_pb2_grpc  # noqa: F401
        except ImportError as exc:
            self._log(f"yandexcloud SDK is not installed: {exc}", "ERROR")
            return False
        self._closing = False
        if await self._open_stream():
            return True
        if self.fatal_error == "invalid_language" and self.languages != ["auto"]:
            self._log(f"languages {self.languages} rejected, retrying with auto", "WARNING")
            self.languages = ["auto"]
            self.language, self.secondary_languages = "auto", []
            self.fatal_error = None
            return await self._open_stream()
        return False

    async def _open_stream(self) -> bool:
        import grpc
        from yandex.cloud.ai.stt.v3 import stt_service_pb2_grpc
        self._queue = asyncio.Queue()
        self._queue.put_nowait(self._options())
        if self._channel is None:
            self._channel = grpc.aio.secure_channel(YANDEX_STT_HOST, grpc.ssl_channel_credentials())
        stub = stt_service_pb2_grpc.RecognizerStub(self._channel)
        metadata = [("authorization", f"Api-Key {self.api_key}")]
        if self.folder_id:
            metadata.append(("x-folder-id", self.folder_id))

        async def requests():
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                yield item

        try:
            self._call = stub.RecognizeStreaming(requests(), metadata=metadata)
            # Ошибку настроек (ключ, язык) сервер возвращает сразу — ждём её недолго
            try:
                await asyncio.wait_for(asyncio.shield(self._call.wait_for_connection()), timeout=5)
            except AttributeError:
                pass
            reader = asyncio.create_task(self._reader(self._call))
            done, _ = await asyncio.wait({reader}, timeout=CONNECT_CHECK_SEC)
            if done:
                return False  # поток завершился сразу — ошибка уже в журнале
        except Exception as exc:
            self._log(f"connect failed: {exc}", "ERROR")
            return False
        self._reader_task = reader
        self.is_connected = True
        self._session_audio = 0.0
        self._last_audio_at = time.monotonic()
        if self._filler_task is None or self._filler_task.done():
            self._filler_task = asyncio.create_task(self._filler())
        self._log(f"stream ready: {self.provider_label}")
        return True

    # ------------------------------------------------------------------ аудио
    async def send_audio(self, audio_b64: str) -> bool:
        from yandex.cloud.ai.stt.v3 import stt_pb2 as s
        if not self.is_connected:
            return False
        pcm = base64.b64decode(audio_b64)
        dur = len(pcm) / (self.sample_rate * 2)
        self.audio_seconds += dur
        self._session_audio += dur
        self._last_audio_at = time.monotonic()
        self._queue.put_nowait(s.StreamingRequest(chunk=s.AudioChunk(data=pcm)))
        if self._session_audio >= SESSION_ROTATE_SEC and not self._in_phrase and not self._rotating:
            asyncio.create_task(self._rotate())
        return True

    async def _filler(self) -> None:
        from yandex.cloud.ai.stt.v3 import stt_pb2 as s
        try:
            while not self._closing:
                await asyncio.sleep(FILLER_TICK_SEC)
                if self.is_connected and time.monotonic() - self._last_audio_at >= FILLER_AFTER_SEC:
                    self._session_audio += FILLER_TICK_SEC
                    self._queue.put_nowait(s.StreamingRequest(
                        silence_chunk=s.SilenceChunk(duration_ms=int(FILLER_TICK_SEC * 1000))))
        except asyncio.CancelledError:
            pass

    async def _rotate(self) -> None:
        """Лимит звука на сессию: открыть новую сессию в паузе между фразами, старую закрыть."""
        self._rotating = True
        try:
            old_queue, old_reader = self._queue, self._reader_task
            self._reader_task = None
            self.is_connected = False
            old_queue.put_nowait(None)
            if old_reader:
                old_reader.cancel()
            if not await self._open_stream():
                self._log("session rotation failed", "WARNING")
                if self.on_closed:
                    await self.on_closed(False)
        finally:
            self._rotating = False

    # ------------------------------------------------------------------ ответы
    async def _reader(self, call) -> None:
        fatal = False
        try:
            async for resp in call:
                kind = resp.WhichOneof("Event")
                if kind == "partial":
                    alts = resp.partial.alternatives
                    text = alts[0].text.strip() if alts else ""
                    if text:
                        self._in_phrase = True
                        if self.on_partial:
                            await self.on_partial(text)
                elif kind == "final":
                    alts = resp.final.alternatives
                    text = alts[0].text.strip() if alts else ""
                    if text:
                        self._finals.append(text)
                elif kind == "eou_update":
                    text = " ".join(self._finals).strip()
                    self._finals = []
                    self._in_phrase = False
                    self.commits += 1
                    if self.on_committed:
                        await self.on_committed(text)
                elif kind == "status_code" and resp.status_code.code_type == 2:  # WARNING
                    self._log(f"warning: {resp.status_code.message}", "WARNING")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            details = getattr(exc, "details", lambda: str(exc))()
            code = getattr(exc, "code", lambda: None)()
            code_name = getattr(code, "name", str(code))
            if not self._closing:
                self._log(f"stream error {code_name}: {details}", "ERROR")
            if code_name in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
                self.fatal_error, fatal = "auth_error", True
            elif code_name == "INVALID_ARGUMENT" and "lang" in (details or "").lower():
                self.fatal_error, fatal = "invalid_language", True
            elif code_name == "INVALID_ARGUMENT":
                self.fatal_error, fatal = "invalid_request", True
        finally:
            if self._reader_task is asyncio.current_task() and not self._closing and not self._rotating:
                self.is_connected = False
                if self.on_closed:
                    try:
                        await self.on_closed(fatal)
                    except Exception as exc:
                        self._log(f"on_closed failed: {exc}", "ERROR")

    async def close(self) -> None:
        self._closing = True
        self.is_connected = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        for task in (self._filler_task, self._reader_task):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
        if self._call is not None:
            try:
                self._call.cancel()
            except Exception:
                pass
        if self._channel is not None:
            try:
                await self._channel.close()
            except Exception:
                pass
            self._channel = None
