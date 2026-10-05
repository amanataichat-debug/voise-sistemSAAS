# backend/websockets/google_stt_client.py
"""
Потоковое распознавание речи абонента через Google Cloud Speech-to-Text V2, модель Chirp 3.
Тот же интерфейс, что у ScribeSTTClient / OpenAISTTClient / YandexSTTClient: connect /
send_audio / close, колбэки on_partial / on_committed / on_closed, поля sample_rate,
silence_ms, language, secondary_languages, provider_label, fatal_error.

    {location}-speech.googleapis.com  Speech.StreamingRecognize (gRPC, google-cloud-speech)
    recognizer: projects/{project}/locations/{location}/recognizers/_  (без создания ресурса)
    → streaming_config (chirp_3, LINEAR16 8/16 кГц, language_codes ky-KG+ru-RU, denoise,
      interim_results, voice activity events, endpointing_sensitivity SHORT) → audio …
    ← промежуточные результаты (partial → barge-in) / is_final (конец фразы → on_committed)

Конец фразы определяет сам Google (endpointing_sensitivity: standard | short | supershort), но
финал он отдаёт на каждой паузе между предложениями. Поэтому финалы копятся и уходят одной
репликой: через COMMIT_HOLD_MS после финала, если клиент молчит; если Google сообщил о начале
речи (SPEECH_ACTIVITY_BEGIN) — ждём следующий финал (не дольше MAX_DEFER_MS).
Chirp 3 в потоке может не присылать промежуточных результатов — тогда перебивание срабатывает
на готовой фразе (FishVoiceSession._send_user_turn), а не на первых словах.

Ключ: сервисный аккаунт (GOOGLE_SPEECH_CREDENTIALS_JSON, иначе GOOGLE_SERVICE_ACCOUNT_JSON —
тот же, что у Google Sheets; нужна роль Cloud Speech Client и включённый Speech-to-Text API в
его проекте) или API-ключ (GOOGLE_SPEECH_API_KEY + GOOGLE_SPEECH_PROJECT_ID).

Ограничения потока: ~5 минут звука — поток перезапускается заранее, в паузе между фразами
(новый поток открывается до закрытия старого, звук не теряется). Пока звук не приходит
(виджет во время речи ассистента), шлём тишину, чтобы Google не закрыл поток по таймауту.

Если Google отверг часть настроек (кыргызский в Preview, шумоподавление, словарь, endpointing
для этой модели/региона) — настройки снимаются по одной и поток открывается заново:
словарь → шумоподавление → регион eu→us → language_codes=auto → endpointing
(отказ языка в eu сначала пробуется в us).
"""

import asyncio
import base64
import json
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from backend.core.logging import get_logger

logger = get_logger(__name__)

SESSION_ROTATE_SEC = 240.0     # лимит Google ~5 мин звука на поток; перезапуск заранее, в паузе
SESSION_FORCE_ROTATE_SEC = 285.0  # даже посреди фразы
FILLER_TICK_SEC = 0.2
FILLER_AFTER_SEC = 0.4
CONNECT_CHECK_SEC = 1.0        # столько ждём ошибку настроек (ключ, язык, модель) после открытия
MAX_DEGRADE_STEPS = 6
COMMIT_HOLD_MS = 250           # после финала: вдруг клиент продолжит (Google режет реплику по предложениям)
MAX_DEFER_MS = 2500            # клиент снова заговорил после финала — ждём следующий финал не дольше
END_FLUSH_MS = 1000            # речь кончилась, а нового финала нет (шум) — отдать накопленное
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

TextCallback = Callable[[str], Awaitable[None]]

# Кэш учётных данных и gRPC-клиентов: токен сервисного аккаунта и TLS-соединение
# переиспользуются между звонками (клиент привязан к event loop воркера)
_CREDENTIALS: Dict[str, object] = {}
_CLIENTS: Dict[Tuple[int, str, str], object] = {}


def parse_credentials_json(raw: Optional[str]) -> Optional[dict]:
    """JSON сервисного аккаунта из переменной окружения (как в google_sheets_service)."""
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except Exception:
        try:
            info = json.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception:
            return None
    if not isinstance(info, dict) or "private_key" not in info:
        return None
    info["private_key"] = info["private_key"].replace("\\n", "\n")
    return info


class GoogleSTTClient:
    provider = "google"

    def __init__(
        self,
        credentials_info: Optional[dict] = None,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        location: str = "eu",
        languages: Optional[List[str]] = None,
        sample_rate: int = 8000,
        silence_ms: int = 500,
        model: str = "chirp_3",
        endpointing: str = "short",
        denoise: bool = True,
        phrases: Optional[List[str]] = None,
        commit_hold_ms: int = COMMIT_HOLD_MS,
        debug: bool = False,
        on_partial: Optional[TextCallback] = None,
        on_committed: Optional[TextCallback] = None,
        on_closed: Optional[Callable[[bool], Awaitable[None]]] = None,
        label: str = "",
    ) -> None:
        self.credentials_info = credentials_info
        self.api_key = api_key
        self.project_id = project_id or (credentials_info or {}).get("project_id")
        self.location = (location or "eu").strip().lower()
        self.languages = [l for l in (languages or []) if l] or ["auto"]
        self.language = self.languages[0]
        self.secondary_languages = self.languages[1:]
        self.sample_rate = sample_rate if sample_rate in (8000, 16000, 24000, 48000) else 16000
        self.default_silence_ms = max(100, int(silence_ms))
        self.silence_ms = self.default_silence_ms  # перед on_committed — измеренная пауза до конца фразы
        self.model = model or "chirp_3"
        self.endpointing = (endpointing or "").strip().lower()
        self.denoise = bool(denoise)
        self.phrases = [p.strip() for p in (phrases or []) if p and p.strip()][:1000]
        self.commit_hold_ms = max(0, int(commit_hold_ms))
        self.debug = debug
        self.partials = 0
        self.vad_events = 0
        self.on_partial = on_partial
        self.on_committed = on_committed
        self.on_closed = on_closed
        self.label = label

        self.is_connected = False
        self.fatal_error: Optional[str] = None
        self.audio_seconds = 0.0
        self.commits = 0
        self.transcribe_ms: List[int] = []
        self.detected_language: Optional[str] = None
        self._closing = False
        self._client = None
        self._queue: "asyncio.Queue" = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._filler_task: Optional[asyncio.Task] = None
        self._checking = False
        self._restarting = False
        self._last_error: Tuple[str, str] = ("", "")
        self._last_audio_at = 0.0
        self._session_audio = 0.0
        self._finals: List[str] = []
        self._partial = ""
        self._in_phrase = False
        self._last_word_at = 0.0
        self._speech_end_at = 0.0
        self._rotating = False
        self._vad_speaking = False
        self._commit_task: Optional[asyncio.Task] = None

    @property
    def provider_label(self) -> str:
        extras = [f"endpointing {self.endpointing}" if self.endpointing else "endpointing standard"]
        if self.denoise:
            extras.append("denoise")
        if self.phrases:
            extras.append(f"словарь {len(self.phrases)}")
        return (f"Google {self.model} ({'+'.join(self.languages)}, {self.location}, "
                f"{self.sample_rate // 1000} кГц, {', '.join(extras)})")

    @property
    def recognizer(self) -> str:
        return f"projects/{self.project_id}/locations/{self.location}/recognizers/_"

    def _log(self, message: str, level: str = "INFO") -> None:
        text = f"[GOOGLE-STT {self.label}] {message}"
        getattr(logger, {"ERROR": "error", "WARNING": "warning"}.get(level, "info"))(text)

    # ------------------------------------------------------------------ клиент и настройки
    def _get_client(self):
        from google.api_core.client_options import ClientOptions
        from google.cloud import speech_v2

        endpoint = "speech.googleapis.com" if self.location == "global" else f"{self.location}-speech.googleapis.com"
        auth = f"key:{self.api_key[-6:]}" if self.api_key else f"sa:{(self.credentials_info or {}).get('client_email')}"
        key = (id(asyncio.get_running_loop()), endpoint, auth)
        client = _CLIENTS.get(key)
        if client is not None:
            return client
        if self.api_key:
            client = speech_v2.SpeechAsyncClient(
                client_options=ClientOptions(api_endpoint=endpoint, api_key=self.api_key))
        else:
            creds = _CREDENTIALS.get(auth)
            if creds is None:
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_info(self.credentials_info, scopes=SCOPES)
                _CREDENTIALS[auth] = creds
            client = speech_v2.SpeechAsyncClient(credentials=creds, client_options=ClientOptions(api_endpoint=endpoint))
        _CLIENTS[key] = client
        return client

    def _config_request(self):
        from google.cloud.speech_v2.types import cloud_speech as c

        config = c.RecognitionConfig(
            explicit_decoding_config=c.ExplicitDecodingConfig(
                encoding=c.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self.sample_rate, audio_channel_count=1),
            model=self.model,
            language_codes=self.languages,
            features=c.RecognitionFeatures(enable_automatic_punctuation=True),
        )
        if self.denoise:
            config.denoiser_config = c.DenoiserConfig(denoise_audio=True)
        if self.phrases:
            config.adaptation = c.SpeechAdaptation(phrase_sets=[c.SpeechAdaptation.AdaptationPhraseSet(
                inline_phrase_set=c.PhraseSet(phrases=[c.PhraseSet.Phrase(value=p, boost=10) for p in self.phrases]))])
        features = c.StreamingRecognitionFeatures(interim_results=True, enable_voice_activity_events=True)
        sensitivity = {
            "standard": c.StreamingRecognitionFeatures.EndpointingSensitivity.ENDPOINTING_SENSITIVITY_STANDARD,
            "short": c.StreamingRecognitionFeatures.EndpointingSensitivity.ENDPOINTING_SENSITIVITY_SHORT,
            "supershort": c.StreamingRecognitionFeatures.EndpointingSensitivity.ENDPOINTING_SENSITIVITY_SUPERSHORT,
        }.get(self.endpointing)
        if sensitivity is not None:
            features.endpointing_sensitivity = sensitivity
        return c.StreamingRecognizeRequest(
            recognizer=self.recognizer,
            streaming_config=c.StreamingRecognitionConfig(config=config, streaming_features=features),
        )

    def _degrade(self) -> bool:
        """Google отверг настройки: снять одну необязательную (по тексту ошибки, иначе по порядку)."""
        details = (self._last_error[1] or "").lower()
        steps = [
            ("phrases", bool(self.phrases), ("adaptation", "phrase")),
            ("denoise", self.denoise, ("denois",)),
            ("location", self.location == "eu", ("location", "region")),
            ("languages", self.languages != ["auto"], ("language",)),
            ("endpointing", bool(self.endpointing), ("endpointing",)),
        ]
        hinted = [s for s in steps if s[1] and any(k in details for k in s[2])]
        candidates = hinted or [s for s in steps if s[1]]
        if not candidates:
            return False
        name = candidates[0][0]
        if name == "languages" and self.location == "eu":
            name = "location"  # кыргызский (Preview) может быть доступен только в us — сначала регион
        if name == "phrases":
            self.phrases = []
        elif name == "denoise":
            self.denoise = False
        elif name == "location":
            self.location = "us"
            self._client = None
        elif name == "languages":
            self.languages = ["auto"]
            self.language, self.secondary_languages = "auto", []
        elif name == "endpointing":
            self.endpointing = ""
        self._log(f"settings rejected ({self._last_error[1][:200]}), retrying without «{name}»: "
                  f"{self.provider_label}", "WARNING")
        return True

    # ------------------------------------------------------------------ соединение
    async def connect(self) -> bool:
        if not (self.api_key or self.credentials_info):
            self._log("GOOGLE_SPEECH_CREDENTIALS_JSON / GOOGLE_SPEECH_API_KEY is not configured", "ERROR")
            self.fatal_error = "no_credentials"
            return False
        if not self.project_id:
            self._log("GOOGLE_SPEECH_PROJECT_ID is not configured", "ERROR")
            self.fatal_error = "no_project"
            return False
        try:
            import google.cloud.speech_v2  # noqa: F401
        except ImportError as exc:
            self._log(f"google-cloud-speech is not installed: {exc}", "ERROR")
            self.fatal_error = "no_sdk"
            return False
        self._closing = False
        return await self._open_with_degrade()

    async def _open_with_degrade(self) -> bool:
        for _ in range(MAX_DEGRADE_STEPS):
            self.fatal_error = None
            if await self._open_stream():
                return True
            if self.fatal_error != "invalid_request" or not self._degrade():
                return False
        return False

    async def _open_stream(self) -> bool:
        queue: "asyncio.Queue" = asyncio.Queue()
        queue.put_nowait(self._config_request())
        old_queue, self._queue = self._queue, queue  # звук, пришедший пока поток открывается, ждёт в очереди

        async def requests():
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        self._checking = True
        try:
            if self._client is None:
                self._client = self._get_client()
            stream = await self._client.streaming_recognize(requests=requests())
            reader = asyncio.create_task(self._reader(stream, queue))
            done, _ = await asyncio.wait({reader}, timeout=CONNECT_CHECK_SEC)
        except Exception as exc:
            self._classify(exc)
            self._log(f"connect failed: {self._last_error[0]}: {self._last_error[1][:300]}", "ERROR")
            self._queue = old_queue if self.is_connected else queue
            return False
        finally:
            self._checking = False
        if done:
            # поток закрылся сразу — ошибка настроек/ключа (уже классифицирована в _reader)
            if not self.fatal_error:
                self.fatal_error = "closed"
            self._log(f"stream rejected: {self._last_error[0]}: {self._last_error[1][:300]}",
                      "WARNING" if self.fatal_error == "invalid_request" else "ERROR")
            return False
        self._reader_task = reader
        if old_queue is not queue:
            old_queue.put_nowait(None)  # старый поток (ротация) дорасшифрует хвост и закроется сам
        self.is_connected = True
        self._session_audio = 0.0
        self._last_audio_at = time.monotonic()
        if self._filler_task is None or self._filler_task.done():
            self._filler_task = asyncio.create_task(self._filler())
        self._log(f"stream ready: {self.provider_label}")
        return True

    def _classify(self, exc: Exception) -> None:
        name = type(exc).__name__
        details = str(getattr(exc, "message", "") or exc)
        self._last_error = (name, details)
        if name in ("Unauthenticated", "PermissionDenied", "Forbidden", "RefreshError", "DefaultCredentialsError"):
            self.fatal_error = "auth_error"
        elif name == "NotFound":
            self.fatal_error = "not_found"
        elif name in ("InvalidArgument", "BadRequest", "FailedPrecondition"):
            self.fatal_error = "invalid_request"
        elif name in ("OutOfRange", "DeadlineExceeded", "Aborted", "Cancelled"):
            self.fatal_error = "stream_limit"
        else:
            self.fatal_error = "connection_lost"

    # ------------------------------------------------------------------ аудио
    async def send_audio(self, audio_b64: str) -> bool:
        from google.cloud.speech_v2.types import cloud_speech as c
        if not self.is_connected:
            return False
        pcm = base64.b64decode(audio_b64)
        dur = len(pcm) / (self.sample_rate * 2)
        self.audio_seconds += dur
        self._session_audio += dur
        self._last_audio_at = time.monotonic()
        self._queue.put_nowait(c.StreamingRecognizeRequest(audio=pcm))
        self._maybe_rotate()
        return True

    def _maybe_rotate(self) -> None:
        if self._rotating or self._restarting:
            return
        if (self._session_audio >= SESSION_ROTATE_SEC and not self._in_phrase) \
                or self._session_audio >= SESSION_FORCE_ROTATE_SEC:
            self._rotating = True
            asyncio.create_task(self._rotate())

    async def _filler(self) -> None:
        from google.cloud.speech_v2.types import cloud_speech as c
        silence = b"\x00" * int(self.sample_rate * 2 * FILLER_TICK_SEC)
        try:
            while not self._closing:
                await asyncio.sleep(FILLER_TICK_SEC)
                if self.is_connected and time.monotonic() - self._last_audio_at >= FILLER_AFTER_SEC:
                    self._session_audio += FILLER_TICK_SEC
                    self._queue.put_nowait(c.StreamingRecognizeRequest(audio=silence))
                    self._maybe_rotate()
        except asyncio.CancelledError:
            pass

    async def _rotate(self) -> None:
        """Лимит звука на поток: открыть новый поток (звук сразу идёт в него), старый закрыть."""
        try:
            if not await self._open_with_degrade():
                self._log("stream rotation failed", "WARNING")
                await self._lost()
        finally:
            self._rotating = False

    async def _restart(self) -> None:
        """Поток оборвался посреди разговора: открыть заново, иначе сообщить сессии."""
        if self._restarting or self._closing:
            return
        self._restarting = True
        try:
            if self.fatal_error not in ("auth_error", "not_found") and await self._open_with_degrade():
                self._log("stream reopened")
                return
            await self._lost()
        finally:
            self._restarting = False

    async def _lost(self) -> None:
        if self._closing:
            return
        self.is_connected = False
        if self.on_closed:
            try:
                await self.on_closed(self.fatal_error in ("auth_error", "not_found", "no_credentials"))
            except Exception as exc:
                self._log(f"on_closed failed: {exc}", "ERROR")

    # ------------------------------------------------------------------ ответы
    async def _reader(self, stream, queue) -> None:
        error: Optional[Exception] = None
        try:
            async for resp in stream:
                await self._handle(resp)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = exc
        if self._closing or queue is not self._queue:
            return  # старый поток после ротации или закрытие
        if error is not None:
            self._classify(error)
            if not self._checking:
                level = "WARNING" if self.fatal_error == "stream_limit" else "ERROR"
                self._log(f"stream error {self._last_error[0]}: {self._last_error[1][:300]}", level)
        else:
            self.fatal_error = self.fatal_error or "closed"
            self._last_error = ("closed", "server closed the stream")
        if self._checking:
            return  # ошибку обработает _open_stream
        self._flush_finals_on_close()
        asyncio.create_task(self._restart())

    def _flush_finals_on_close(self) -> None:
        self._cancel_commit()
        self._finals, self._partial, self._in_phrase, self._vad_speaking = [], "", False, False

    # ------------------------------------------------------------------ сборка реплики из финалов
    def _cancel_commit(self) -> None:
        if self._commit_task is not None and not self._commit_task.done() \
                and self._commit_task is not asyncio.current_task():
            self._commit_task.cancel()
        self._commit_task = None

    def _schedule_commit(self, delay_ms: int) -> None:
        self._cancel_commit()
        self._commit_task = asyncio.create_task(self._delayed_commit(delay_ms / 1000))

    async def _delayed_commit(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._commit_task = None
        await self._commit(time.monotonic())

    async def _handle(self, resp) -> None:
        from google.cloud.speech_v2.types import cloud_speech as c
        now = time.monotonic()
        event = resp.speech_event_type
        E = c.StreamingRecognizeResponse.SpeechEventType
        if self.debug:
            items = [f"{'FINAL' if r.is_final else 'interim'}({r.stability:.2f},{r.language_code}) "
                     f"«{r.alternatives[0].transcript if r.alternatives else ''}»" for r in resp.results]
            self._log(f"resp event={E(event).name if event else '-'} {' | '.join(items)}")
        if event == E.SPEECH_ACTIVITY_END:
            self.vad_events += 1
            self._vad_speaking = False
            self._speech_end_at = now
            if self._finals:
                self._schedule_commit(END_FLUSH_MS)
        elif event == E.SPEECH_ACTIVITY_BEGIN:
            self.vad_events += 1
            self._vad_speaking = True
            self._in_phrase = True
            if self._finals:
                self._schedule_commit(MAX_DEFER_MS)  # продолжение реплики — ждём следующий финал

        interim: List[str] = []
        got_final = False
        for result in resp.results:
            text = result.alternatives[0].transcript.strip() if result.alternatives else ""
            if result.is_final:
                got_final = True
                if text:
                    self._finals.append(text)
                if result.language_code:
                    self.detected_language = result.language_code
            elif text:
                interim.append(text)

        if interim:
            partial = " ".join(self._finals + interim).strip()
            if partial != self._partial:
                self._partial = partial
                self.partials += 1
                self._last_word_at = now
                self._in_phrase = True
                if self._finals:
                    self._schedule_commit(MAX_DEFER_MS)
                if self.on_partial:
                    await self.on_partial(partial)
        if got_final and self._finals:
            self._schedule_commit(MAX_DEFER_MS if self._vad_speaking else self.commit_hold_ms)

    async def _commit(self, now: float) -> None:
        text = " ".join(self._finals).strip()
        last_word_at = self._last_word_at if self._in_phrase else 0.0
        self._finals, self._partial, self._in_phrase = [], "", False
        if not text:
            return
        # Сколько прошло от конца речи до готовой фразы: по событию Google о конце речи,
        # иначе от последнего промежуточного текста, иначе — настроенная пауза
        if self._speech_end_at and not self._vad_speaking:
            self.silence_ms = int(min(3000, (now - self._speech_end_at) * 1000))
            self.transcribe_ms.append(self.silence_ms)
        elif last_word_at:
            self.silence_ms = int(min(3000, (now - last_word_at) * 1000))
        else:
            self.silence_ms = self.default_silence_ms
        self.commits += 1
        self._log(f"final [{self.detected_language or '?'}] after {self.silence_ms} ms: {text}")
        if self.on_committed:
            await self.on_committed(text)

    async def close(self) -> None:
        self._closing = True
        self.is_connected = False
        self._cancel_commit()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        for task in (self._filler_task, self._reader_task):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
