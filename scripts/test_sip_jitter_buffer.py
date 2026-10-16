"""
Проверка адаптивного запаса исходящего звука SIP-адаптера без сети.
Запуск: HOST_URL=https://voksyai.online DATABASE_URL=sqlite:///:memory: python scripts/test_sip_jitter_buffer.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.websockets import sip_media_adapter as m  # noqa: E402


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_bytes(self, b):
        self.sent.append((time.time(), len(b)))

    async def send_text(self, t):
        pass


def chunk(ms):  # 8 кГц PCM16
    return b"\0" * int(8000 * 2 * ms / 1000)


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)


async def main():
    m.ADAPTIVE_CUSHION = True
    sock = m.HandlerSocket.__new__(m.HandlerSocket)
    sock.ws = FakeWS(); sock.provider = "openai"; sock.call_id = "t"; sock.closed = False
    sock._cushion_bytes = int(8000 * 2 * m.OUTBOUND_CUSHION_MS["openai"] / 1000)
    sock._cushion_max_bytes = int(8000 * 2 * m.OUTBOUND_CUSHION_MAX_MS / 1000)
    sock._out_hold = bytearray(); sock._out_streaming = False; sock._out_last_at = 0.0
    sock._play_end = 0.0; sock.underruns = 0; sock.underrun_ms = 0; sock._cushion_task = None

    check("запас по умолчанию 200 мс", sock._cushion_bytes == 3200)
    sock._cushion_bytes = 4800  # дальше проверяем адаптивный режим со стартовым запасом 300 мс

    # старт реплики: копим 300 мс, потом отдаём
    for _ in range(16):
        await sock._send_audio(chunk(20))
    check("реплика пошла после накопления запаса", sock._out_streaming and sock.ws.sent)

    # звук идёт ровно — разрывов нет
    for _ in range(5):
        await sock._send_audio(chunk(20))
    check("ровный поток — без разрывов", sock.underruns == 0)

    # сеть замерла на 150 мс при запасе в мосту ≈ 300 мс → разрыва ещё нет
    sock._play_end = time.time() + 0.3
    await asyncio.sleep(0.15)
    await sock._send_audio(chunk(20))
    check("пауза короче запаса — не разрыв", sock.underruns == 0)

    # мост опустел: звук доигран 100 мс назад
    sock._play_end = time.time() - 0.1
    await sock._send_audio(chunk(20))
    check("опустевший мост — разрыв засчитан", sock.underruns == 1)
    check("запас вырос на шаг (400 мс)", sock._cushion_bytes == 6400)
    check("после разрыва звук копится заново", not sock._out_streaming and len(sock._out_hold) > 0)

    # длинная пауза между репликами — не разрыв
    sock._out_streaming = True; sock._play_end = time.time() - 0.8
    await sock._send_audio(chunk(20))
    check("пауза между репликами не считается разрывом", sock.underruns == 1)

    # потолок
    for _ in range(10):
        sock._out_streaming = True; sock._play_end = time.time() - 0.1
        await sock._send_audio(chunk(20))
    check("запас не выше потолка 700 мс", sock._cushion_bytes == sock._cushion_max_bytes)

    # по умолчанию адаптация выключена: разрыв только считается, запас и поток не меняются
    m.ADAPTIVE_CUSHION = False
    sock._cushion_bytes = 3200; sock._out_streaming = True; sock.underruns = 0; sock._out_hold.clear()
    sock._play_end = time.time() - 0.1
    await sock._send_audio(chunk(20))
    check("адаптация выключена — разрыв посчитан", sock.underruns == 1)
    check("адаптация выключена — запас не растёт", sock._cushion_bytes == 3200)
    check("адаптация выключена — звук не задерживается", sock._out_streaming and not sock._out_hold)
    print("все проверки пройдены")


asyncio.run(main())
