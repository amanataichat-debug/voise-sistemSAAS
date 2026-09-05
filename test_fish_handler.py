"""
Проверка Fish-хендлера на подставных сокетах: клиент (виджет/SIP-адаптер),
OpenAI Realtime и Fish Audio. Сеть и БД не нужны.

Запуск: DATABASE_URL=postgresql://u:p@localhost/db JWT_SECRET_KEY=x HOST_URL=https://voksyai.online python3 test_fish_handler.py

Проверяется:
  1. приветствие уходит в Fish напрямую и в контекст OpenAI как реплика ассистента;
  2. текстовые дельты режутся по предложениям и уходят в Fish с flush, аудио Fish
     доходит до клиента как response.audio.delta (PCM16 24 кГц, base64);
  3. перебивание: speech_started при говорящем ассистенте → response.cancel,
     смена поколения Fish (старое аудио отбрасывается), клиенту conversation.interrupted;
  4. вызов функции → function_call.executing (с полем function для SIP-адаптера)
     → function_call_output и response.create в OpenAI;
  5. конец реплики по тишине → assistant.speech.ended;
  6. FishLLMClient.update_session шлёт текстовый режим с server VAD.
"""
import asyncio
import base64
import json
import logging
import sys
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

import msgpack

import backend.websockets.fish_tts_client as tts_mod
import backend.websockets.fish_llm_client as llm_mod
from backend.websockets.fish_tts_client import FishTTSClient
from backend.websockets.fish_llm_client import FishLLMClient
from backend.websockets.handler_fish import FishVoiceSession
from backend.models.fish_assistant import FishAssistantConfig

tts_mod.UTTERANCE_IDLE_MS = 150  # ускоряем тест


# ----------------------------------------------------------------------------- фейки
class FakeClientWS:
    """Клиент хендлера (виджет или HandlerSocket): копит send_json, отдаёт очередь receive()."""
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.headers = {"user-agent": "test"}

    async def send_json(self, data, mode="text"):
        self.sent.append(data)

    async def receive(self):
        return await self.incoming.get()

    def types(self):
        return [m["type"] for m in self.sent]


class FakeUpstreamWS:
    """Подставной сокет провайдера: пишет всё отправленное, отдаёт заранее заданные сообщения."""
    def __init__(self):
        self.sent = []
        self.queue = asyncio.Queue()
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        item = await self.queue.get()
        if item is None:
            from websockets.exceptions import ConnectionClosed
            raise ConnectionClosed(None, None)
        return item

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True


class Connector:
    """Заменяет websockets.connect: каждое подключение — новый FakeUpstreamWS."""
    def __init__(self):
        self.sockets = []

    async def __call__(self, *args, **kwargs):
        ws = FakeUpstreamWS()
        self.sockets.append(ws)
        return ws


def make_assistant():
    a = FishAssistantConfig(
        name="Тест", system_prompt="Ты тестовый ассистент.", greeting_message="Здравствуйте, это тест.",
        fish_voice_id="voice123", fish_model="s2.1-pro-free", fish_latency="balanced",
        sample_rate=24000, voice_speed=1.0, temperature=0.7, llm_model="gpt-realtime-2", language="ru",
        functions=[{"name": "get_current_time", "description": "Текущее время"}],
    )
    import uuid
    a.id = uuid.uuid4()
    a.user_id = uuid.uuid4()
    return a


def openai_msgs(fake_openai):
    return [json.loads(x) for x in fake_openai.sent]


def fish_msgs(fake_fish):
    return [msgpack.unpackb(x, raw=False) for x in fake_fish.sent]


# ----------------------------------------------------------------------------- тест
async def main():
    # Оба модуля используют один объект websockets — подменяем его целиком в каждом,
    # иначе второй патч перекроет первый.
    import types
    import websockets as _ws
    fish_conn = Connector()
    openai_conn = Connector()
    tts_mod.websockets = types.SimpleNamespace(connect=fish_conn, exceptions=_ws.exceptions)    # type: ignore
    llm_mod.websockets = types.SimpleNamespace(connect=openai_conn, exceptions=_ws.exceptions)  # type: ignore

    assistant = make_assistant()
    client = FakeClientWS()

    llm = FishLLMClient("sk-test", assistant, "client-1", db_session=None, user_agent="test")
    assert await llm.connect(), "llm connect"
    openai_ws = openai_conn.sockets[0]

    # 6. session.update — текстовый режим + server VAD + инструмент
    sess = json.loads(openai_ws.sent[0])
    assert sess["type"] == "session.update"
    assert sess["session"]["output_modalities"] == ["text"], sess
    assert sess["session"]["audio"]["input"]["turn_detection"]["type"] == "server_vad"
    assert sess["session"]["tools"] and sess["session"]["tools"][0]["name"] == "get_current_time"
    assert "output" not in sess["session"]["audio"]
    print("ok  session.update: text mode, server VAD, tools")

    session = FishVoiceSession(client, assistant, llm, None, None, "client-1")
    tts = FishTTSClient("fish-key", assistant, 24000, on_audio=session.on_tts_audio,
                        on_speech_started=session.on_tts_speech_started,
                        on_speech_ended=session.on_tts_speech_ended, label="t")
    session.tts = tts
    await tts.connect()
    fish_ws = fish_conn.sockets[0]
    start = fish_msgs(fish_ws)[0]
    assert start["event"] == "start" and start["request"]["sample_rate"] == 24000
    assert start["request"]["reference_id"] == "voice123" and start["request"]["format"] == "pcm"
    print("ok  Fish start request: pcm 24000, voice")

    run_task = asyncio.create_task(session.run())
    await asyncio.sleep(0.05)

    # 1. приветствие
    fm = fish_msgs(fish_ws)
    assert [m["event"] for m in fm[1:3]] == ["text", "flush"] and fm[1]["text"] == "Здравствуйте, это тест.", fm
    om = openai_msgs(openai_ws)
    greet = [m for m in om if m["type"] == "conversation.item.create"]
    assert greet and greet[0]["item"]["role"] == "assistant" and greet[0]["item"]["content"][0]["text"] == "Здравствуйте, это тест."
    print("ok  greeting: Fish text+flush, OpenAI assistant item")

    # аудио приветствия от Fish → клиенту
    pcm = b"\x01\x00" * 480
    await fish_ws.queue.put(msgpack.packb({"event": "audio", "audio": pcm}))
    await asyncio.sleep(0.05)
    assert "assistant.speech.started" in client.types()
    deltas = [m for m in client.sent if m["type"] == "response.audio.delta"]
    assert deltas and base64.b64decode(deltas[0]["delta"]) == pcm
    print("ok  Fish audio → response.audio.delta")

    # 5. конец реплики по тишине
    await asyncio.sleep(0.3)
    assert "assistant.speech.ended" in client.types(), client.types()
    print("ok  assistant.speech.ended after idle")

    # аудио абонента → OpenAI
    await client.incoming.put({"type": "websocket.receive", "text": json.dumps(
        {"type": "input_audio_buffer.append", "audio": base64.b64encode(b"\x00" * 960).decode()})})
    await asyncio.sleep(0.02)
    assert any(m["type"] == "input_audio_buffer.append" for m in openai_msgs(openai_ws))
    print("ok  client audio → input_audio_buffer.append")

    # 2. ответ модели текстом → предложения в Fish
    fish_ws.sent.clear()
    for ev in [
        {"type": "input_audio_buffer.speech_started"},
        {"type": "input_audio_buffer.speech_stopped"},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "Какая погода?"},
        {"type": "response.created", "response": {"id": "resp_1"}},
        {"type": "response.output_text.delta", "delta": "Сегодня солнечно и тепло. "},
        {"type": "response.output_text.delta", "delta": "Ветер слабый, дождя не будет. "},
        {"type": "response.output_text.delta", "delta": "Хорошего дня!"},
        {"type": "response.output_text.done", "text": "Сегодня солнечно и тепло. Ветер слабый, дождя не будет. Хорошего дня!"},
    ]:
        await openai_ws.queue.put(json.dumps(ev))
    await asyncio.sleep(0.1)
    fm = fish_msgs(fish_ws)
    texts = [m["text"] for m in fm if m["event"] == "text"]
    assert texts == ["Сегодня солнечно и тепло.", "Ветер слабый, дождя не будет.", "Хорошего дня!"], texts
    assert sum(1 for m in fm if m["event"] == "flush") == 3
    assert "speech.started" in client.types() and "speech.stopped" in client.types()
    assert any(m.get("type") == "input.transcription" and m["transcript"] == "Какая погода?" for m in client.sent)
    assert "".join(m["delta"] for m in client.sent if m["type"] == "response.text.delta") == \
        "Сегодня солнечно и тепло. Ветер слабый, дождя не будет. Хорошего дня!"
    print("ok  text deltas → sentences → Fish text+flush, text deltas to client")

    # Fish играет ответ
    await fish_ws.queue.put(msgpack.packb({"event": "audio", "audio": pcm}))
    await asyncio.sleep(0.03)
    assert tts.speaking

    # 3. перебивание во время речи
    client.sent.clear()
    openai_ws.sent.clear()
    await openai_ws.queue.put(json.dumps({"type": "input_audio_buffer.speech_started"}))
    await asyncio.sleep(0.1)
    assert any(m["type"] == "response.cancel" for m in openai_msgs(openai_ws)), openai_msgs(openai_ws)
    assert "conversation.interrupted" in client.types() and "speech.started" in client.types()
    assert tts.generation == 1 and len(fish_conn.sockets) == 2 and fish_ws.closed
    # аудио от старого соединения отбрасывается
    client.sent.clear()
    await fish_ws.queue.put(msgpack.packb({"event": "audio", "audio": pcm}))
    await asyncio.sleep(0.03)
    assert not [m for m in client.sent if m["type"] == "response.audio.delta"]
    print("ok  barge-in: response.cancel, Fish reconnect (gen=1), stale audio dropped, client notified")
    await openai_ws.queue.put(json.dumps({"type": "response.done", "response": {"id": "resp_1", "status": "cancelled"}}))

    # 4. вызов функции
    fish_ws2 = fish_conn.sockets[1]
    openai_ws.sent.clear()
    client.sent.clear()
    for ev in [
        {"type": "response.created", "response": {"id": "resp_2"}},
        {"type": "response.output_item.added", "item": {"id": "item_fc", "type": "function_call",
                                                          "name": "get_current_time", "call_id": "call_1"}},
        {"type": "response.function_call_arguments.done", "item_id": "item_fc", "call_id": "call_1",
         "name": "get_current_time", "arguments": "{}"},
        {"type": "response.done", "response": {"id": "resp_2", "status": "completed", "output": [],
                                               "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]:
        await openai_ws.queue.put(json.dumps(ev))
    await asyncio.sleep(0.5)
    executing = [m for m in client.sent if m["type"] == "function_call.executing"]
    assert executing and executing[0]["function"] == "get_current_time", client.types()
    om = openai_msgs(openai_ws)
    fco = [m for m in om if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]
    assert fco and fco[0]["item"]["call_id"] == "call_1"
    assert any(m["type"] == "response.create" and m["response"]["output_modalities"] == ["text"] for m in om)
    assert "function_call.completed" in client.types(), client.types()
    assert session.tokens_in == 10 and session.tokens_out == 5
    print("ok  function call: executing event, function_call_output + response.create (text)")

    # ответ после функции идёт уже в новое соединение Fish
    for ev in [
        {"type": "response.created", "response": {"id": "resp_3"}},
        {"type": "response.output_text.done", "text": "Сейчас три часа дня."},
        {"type": "response.done", "response": {"id": "resp_3", "status": "completed", "output": []}},
    ]:
        await openai_ws.queue.put(json.dumps(ev))
    await asyncio.sleep(0.1)
    assert [m["text"] for m in fish_msgs(fish_ws2) if m["event"] == "text"] == ["Сейчас три часа дня."]
    print("ok  reply after function → new Fish connection")

    # завершение
    await client.incoming.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(run_task, timeout=3)
    assert fish_ws2.closed and openai_ws.closed
    print("ok  shutdown closes OpenAI and Fish")
    print("\nALL FISH HANDLER TESTS PASSED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
