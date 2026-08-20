"""Проверка прокси Voximplant ⇄ Fish на подставных сокетах."""
import asyncio, base64, json, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)

import msgpack
from backend.websockets.handler_fish_tts import _FishTTSSession, _frame_bytes
from backend.models.fish_assistant import FishAssistantConfig


class FakeVoxWS:
    """Приёмник со стороны Voximplant."""
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class FakeFishWS:
    """Подставной сокет Fish: пишет всё, что ему шлют, и отдаёт заданное аудио."""
    def __init__(self, audio_chunks=()):
        self.received = []
        self._queue = asyncio.Queue()
        self.closed = False
        for chunk in audio_chunks:
            self._queue.put_nowait(msgpack.packb({"event": "audio", "audio": chunk}))

    async def send(self, data):
        self.received.append(msgpack.unpackb(data, raw=False))

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=0.3)
        except asyncio.TimeoutError:
            raise StopAsyncIteration


def make_session(vox, fish, sample_rate=8000):
    a = FishAssistantConfig(
        name="t", fish_voice_id="voice123", fish_model="s2.1-pro",
        fish_latency="balanced", sample_rate=sample_rate,
    )
    a.id = "abc"
    s = _FishTTSSession(vox, a, "fish_key")
    s.fish = fish
    return s


async def test_framing():
    """PCM режется ровно на кадры по 20 мс и уходит base64 в конверте Voximplant."""
    vox, fish = FakeVoxWS(), FakeFishWS()
    s = make_session(vox, fish)

    frame = _frame_bytes(8000)
    assert frame == 320, frame

    await s._send_start()
    # 3.5 кадра — последние пол-кадра должны остаться в буфере
    s.audio_buffer.extend(b"\x01\x02" * (frame * 3 // 2 + frame // 4))

    pump = asyncio.create_task(s._pump_to_call())
    await asyncio.sleep(0.05)
    s.closing = True
    await pump

    start = vox.sent[0]
    assert start["event"] == "start", start
    # 8 кГц PCM16 в терминах WebSocketAudioEncoding называется "PCM8"
    assert start["start"]["mediaFormat"] == {"encoding": "PCM8", "sampleRate": 8000}, start
    # tag не шлём: sendMediaTo(call) в сценарии вызывается без тега
    assert "tag" not in start["start"], start

    media = [m for m in vox.sent if m["event"] == "media"]
    assert len(media) == 3, f"ожидалось 3 кадра, отправлено {len(media)}"
    for i, m in enumerate(media):
        payload = base64.b64decode(m["media"]["payload"])
        assert len(payload) == frame, len(payload)
        assert m["media"]["chunk"] == i
    # sequenceNumber монотонно растёт и не повторяется
    # (служебные speech_* фреймы нумерации не имеют — они вне медиапотока)
    seqs = [m["sequenceNumber"] for m in vox.sent if "sequenceNumber" in m]
    assert seqs == sorted(set(seqs)) == seqs, seqs
    # timestamp — счётчик сэмплов (RTP), а не миллисекунды: 320 байт = 160 сэмплов
    assert [m["media"]["timestamp"] for m in media] == [0, 160, 320]
    print("✅ нарезка кадров, конверт и нумерация")


async def test_text_and_flush():
    vox, fish = FakeVoxWS(), FakeFishWS()
    s = make_session(vox, fish)

    await s.handle_command({"event": "text", "text": "Привет"})
    await s.handle_command({"event": "flush"})
    assert fish.received == [
        {"event": "text", "text": "Привет"},
        {"event": "flush"},
    ], fish.received
    assert await s.handle_command({"event": "stop"}) is False
    print("✅ проброс text/flush в Fish и завершение по stop")


async def test_barge_in():
    """Barge-in гасит буфер, рвёт старый сокет и копит текст до нового."""
    vox, old_fish = FakeVoxWS(), FakeFishWS()
    s = make_session(vox, old_fish)
    s.audio_buffer.extend(b"\xff" * 5000)

    reconnected = {}

    async def fake_connect():
        await asyncio.sleep(0.02)          # соединение поднимается не мгновенно
        new_fish = FakeFishWS()
        s.fish = new_fish
        reconnected["sock"] = new_fish
        pending, s.pending_text = s.pending_text, []
        for t in pending:
            await new_fish.send(msgpack.packb({"event": "text", "text": t}))

    s.connect_fish = fake_connect

    await s.handle_command({"event": "clear"})
    assert len(s.audio_buffer) == 0, "буфер не очищен"
    assert s.generation == 1
    assert s.fish is None, "старый сокет должен быть отцеплен сразу"

    # текст, пришедший до готовности нового сокета, не теряется
    await s.handle_command({"event": "text", "text": "новая реплика"})
    assert s.pending_text == ["новая реплика"], s.pending_text

    await asyncio.sleep(0.1)
    assert old_fish.closed, "старый сокет Fish не закрыт"
    assert reconnected["sock"].received == [
        {"event": "text", "text": "новая реплика"}
    ], reconnected["sock"].received
    print("✅ barge-in: буфер, разрыв старого сокета, докат отложенного текста")


async def test_stale_audio_dropped():
    """Аудио прерванной реплики не попадает в звонок."""
    vox = FakeVoxWS()
    stale = FakeFishWS(audio_chunks=[b"\xaa" * 640])
    s = make_session(vox, stale)

    s.generation = 1                       # поколение ушло вперёд — сокет устарел
    await s._read_fish(stale, generation=0)
    assert len(s.audio_buffer) == 0, "аудио прерванной реплики просочилось в буфер"

    fresh = FakeFishWS(audio_chunks=[b"\xbb" * 640])
    await s._read_fish(fresh, generation=1)
    assert len(s.audio_buffer) == 640, len(s.audio_buffer)
    print("✅ устаревшее аудио отбрасывается, актуальное принимается")


async def test_lead_throttle():
    """Кадры уходят сразу, но не убегают дальше LEAD_LIMIT_MS."""
    from backend.websockets.handler_fish_tts import LEAD_LIMIT_MS, FRAME_MS
    vox, fish = FakeVoxWS(), FakeFishWS()
    s = make_session(vox, fish)
    frame = _frame_bytes(8000)

    # 12 секунд аудио разом — больше 10-секундного буфера Voximplant
    s.audio_buffer.extend(b"\x00" * (frame * 600))

    pump = asyncio.create_task(s._pump_to_call())
    await asyncio.sleep(0.25)
    s.closing = True
    await pump

    media = [m for m in vox.sent if m["event"] == "media"]
    pushed_ms = len(media) * FRAME_MS
    assert pushed_ms > 1000, f"троттлинг душит поток: отдано всего {pushed_ms} мс"
    assert pushed_ms <= LEAD_LIMIT_MS + 500, (
        f"убежали на {pushed_ms} мс — буфер Voximplant переполнится"
    )
    print(f"✅ троттлинг: отдано {pushed_ms} мс за 250 мс, предел {LEAD_LIMIT_MS} мс")


async def test_utterance_boundaries():
    """Прокси сам размечает границы реплики: speech_started / speech_done."""
    from backend.websockets.handler_fish_tts import UTTERANCE_IDLE_MS
    vox, fish = FakeVoxWS(), FakeFishWS()
    s = make_session(vox, fish)
    frame = _frame_bytes(8000)

    await s.handle_command({"event": "flush"})        # ход закрыт сценарием
    s.last_audio_at = __import__("time").monotonic()
    s.audio_buffer.extend(b"\x00" * (frame * 5))     # 100 мс речи

    pump = asyncio.create_task(s._pump_to_call())
    await asyncio.sleep((UTTERANCE_IDLE_MS + 200) / 1000.0)
    s.closing = True
    await pump

    events = [m["event"] for m in vox.sent]
    assert events[0] == "speech_started", events[:3]
    assert "speech_done" in events, events
    assert events.index("speech_started") < events.index("speech_done")

    done = [m for m in vox.sent if m["event"] == "speech_done"][0]
    assert "remaining_ms" in done, done
    assert done["remaining_ms"] >= 0, done
    # ровно одна пара границ на реплику
    assert events.count("speech_started") == 1, events
    assert events.count("speech_done") == 1, events
    print("✅ границы реплики: speech_started/speech_done + остаток буфера")


async def test_single_speech_done_per_turn():
    """
    Одна реплика — один speech_done, даже если Fish отдаёт её пачками.

    Регрессия с боевого звонка: Fish присылал длинный ответ тремя порциями
    с паузами, и прокси рапортовал три конца реплики (remaining_ms
    56 → 3403 → 4831). Сценарий по первому же кладёт трубку после прощания,
    то есть обрывал бы речь на полуслове.
    """
    import time as _t
    from backend.websockets.handler_fish_tts import UTTERANCE_IDLE_MS
    vox, fish = FakeVoxWS(), FakeFishWS()
    s = make_session(vox, fish)
    frame = _frame_bytes(8000)

    # сценарий закрыл ход
    await s.handle_command({"event": "flush"})
    assert s.flush_pending is True

    pump = asyncio.create_task(s._pump_to_call())

    # три пачки аудио с паузами длиннее старого порога в 400 мс
    for _ in range(3):
        s.last_audio_at = _t.monotonic()
        async with s.buffer_lock:
            s.audio_buffer.extend(b"\x00" * (frame * 5))
        await asyncio.sleep(0.5)

    # выдерживаем настоящую паузу — вот теперь реплика точно закончилась
    await asyncio.sleep((UTTERANCE_IDLE_MS + 250) / 1000.0)
    s.closing = True
    await pump

    done = [m for m in vox.sent if m["event"] == "speech_done"]
    assert len(done) == 1, (
        f"на один ход пришло {len(done)} speech_done — сценарий оборвёт речь"
    )
    assert s.flush_pending is False, "флаг flush не сброшен"
    print("✅ один speech_done на реплику, отданную пачками")


async def test_no_speech_done_without_flush():
    """Без flush реплика не считается законченной: ход ещё идёт."""
    import time as _t
    from backend.websockets.handler_fish_tts import UTTERANCE_IDLE_MS
    vox, fish = FakeVoxWS(), FakeFishWS()
    s = make_session(vox, fish)
    frame = _frame_bytes(8000)

    s.last_audio_at = _t.monotonic()
    s.audio_buffer.extend(b"\x00" * (frame * 5))

    pump = asyncio.create_task(s._pump_to_call())
    await asyncio.sleep((UTTERANCE_IDLE_MS + 250) / 1000.0)
    s.closing = True
    await pump

    done = [m for m in vox.sent if m["event"] == "speech_done"]
    assert len(done) == 0, "speech_done пришёл до flush: " + str(done)
    print("✅ без flush конец реплики не объявляется")


async def test_encoding_matches_rate():
    """Имя кодека берётся из перечисления Voximplant, а не из частоты наугад."""
    from backend.websockets.handler_fish_tts import PCM_ENCODING_BY_RATE
    assert PCM_ENCODING_BY_RATE[8000] == "PCM8"
    assert PCM_ENCODING_BY_RATE[16000] == "PCM16"

    vox, fish = FakeVoxWS(), FakeFishWS()
    s16 = make_session(vox, fish, sample_rate=16000)
    assert s16.encoding == "PCM16", s16.encoding
    assert _frame_bytes(16000) == 640, _frame_bytes(16000)
    print("✅ кодек по частоте: 8000→PCM8, 16000→PCM16")


async def test_ulaw_fallback():
    """FISH_TTS_ENCODING=ULAW перекодирует кадр и вдвое ужимает payload."""
    import base64 as _b64
    import backend.websockets.handler_fish_tts as h

    vox, fish = FakeVoxWS(), FakeFishWS()
    s = make_session(vox, fish)
    s.encoding = "ULAW"                      # как при FISH_TTS_ENCODING=ULAW

    frame = _frame_bytes(8000)
    await s._send_start()
    s.audio_buffer.extend(b"\x11\x22" * (frame // 2))

    pump = asyncio.create_task(s._pump_to_call())
    await asyncio.sleep(0.05)
    s.closing = True
    await pump

    start = vox.sent[0]
    assert start["start"]["mediaFormat"]["encoding"] == "ULAW", start

    media = [m for m in vox.sent if m["event"] == "media"][0]
    payload = _b64.b64decode(media["media"]["payload"])
    assert len(payload) == frame // 2, f"μ-law кадр должен быть вдвое короче: {len(payload)}"
    # счётчик остаётся в сэмплах, а не в байтах μ-law
    assert media["media"]["timestamp"] == 0, media
    print("✅ μ-law: кадр перекодирован, timestamp остался в сэмплах")


async def test_speed_and_temperature_reach_fish():
    """Скорость и живость доезжают до Fish, старые значения зажимаются."""
    from backend.models.fish_assistant import FishAssistantConfig as F

    a = F(name="t", sample_rate=8000, fish_latency="low",
          voice_speed=1.4, temperature=0.3, fish_voice_id="v1")
    req = a.get_fish_start_request()
    assert req["prosody"]["speed"] == 1.4, req
    assert req["temperature"] == 0.3, req
    assert req["latency"] == "low", req

    # У записей, созданных когда temperature считалась параметром LLM,
    # значение могло быть до 2 — Fish принимает только 0–1.
    old = F(name="t", sample_rate=8000, temperature=1.8, voice_speed=None)
    req = old.get_fish_start_request()
    assert req["temperature"] == 1.0, req
    assert req["prosody"]["speed"] == 1.0, req

    # и запредельная скорость тоже зажимается
    fast = F(name="t", sample_rate=8000, voice_speed=9.0)
    assert fast.get_fish_start_request()["prosody"]["speed"] == 2.0
    print("✅ speed/temperature уходят в Fish, выходы за диапазон зажимаются")


async def main():
    for t in (test_framing, test_text_and_flush, test_barge_in, test_stale_audio_dropped, test_lead_throttle,
              test_utterance_boundaries, test_single_speech_done_per_turn,
              test_no_speech_done_without_flush, test_encoding_matches_rate,
              test_ulaw_fallback, test_speed_and_temperature_reach_fish):
        await t()
    print("\nвсе проверки прокси пройдены")


asyncio.run(main())
