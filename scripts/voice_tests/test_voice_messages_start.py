"""Голосовые клиента (распознавание ru/ky) и голосовые ответы бота — в т.ч. на Старте.

15.11.2026: клиент Старта — «бот не понимает голосовые на русском и кыргызском».
Scribe теперь первым, Whisper запасным; Whisper, вернувший подсказку вместо речи,
не принимается. Голос ответа: ElevenLabs без выбранного голоса → GPT-Live по умолчанию.
Без сети и БД.
"""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, sys, types

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false",
                  OPENAI_API_KEY="k", ELEVENLABS_API_KEY="e")

from backend.core.config import settings
from backend.services import agent_media_service as ms
from backend.services import agent_voice_service as avs

failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1

calls = []
def setup(scribe=None, whisper=None):
    calls.clear()
    async def _s(*a):
        calls.append("scribe")
        if isinstance(scribe, Exception):
            raise scribe
        return dict(scribe)
    async def _w(*a):
        calls.append("whisper")
        if isinstance(whisper, Exception):
            raise whisper
        return dict(whisper)
    ms._scribe, ms._whisper = _s, _w

run = lambda: asyncio.run(ms.transcribe(b"x", "voice.ogg", "audio/ogg"))
KY = {"text": "Саламатсызбы, канча турат?", "language": "kir", "duration": 3.0, "provider": "scribe_v2"}
RU_GARBLED = {"text": "Сала мать сызбы канча туры", "language": "russian", "duration": 3.0, "provider": "whisper-1"}
RU = {"text": "Здравствуйте, сколько стоит?", "language": "russian", "duration": 2.0, "provider": "whisper-1"}

settings.AGENT_STT_PRIMARY = "scribe"
settings.AGENT_STT_SCRIBE_FALLBACK = True
settings.ELEVENLABS_API_KEY = "e"

setup(scribe=KY, whisper=RU_GARBLED)
r = run()
check("кыргызское голосовое: Scribe первым, Whisper не нужен", r["text"] == KY["text"] and calls == ["scribe"], calls)

setup(scribe=RuntimeError("down"), whisper=RU)
r = run()
check("Scribe упал → Whisper", r["ok"] and r["text"] == RU["text"] and calls == ["scribe", "whisper"])

prompt_echo = dict(RU, text="Голосовое сообщение клиента на русском или кыргызском языке.")
setup(scribe=dict(KY, text=""), whisper=prompt_echo)
r = run()
check("Whisper вернул подсказку вместо речи — не принимаем", r["ok"] and r["text"] == "", r)

setup(scribe=RuntimeError("down"), whisper=prompt_echo)
r = run()
check("только подсказка от Whisper → ошибка распознавания, а не выдуманный текст", not r["ok"], r)

check("обычная фраза — не подсказка", not ms._echoes_prompt("Здравствуйте, сколько стоит доставка?"))
check("подсказка по-кыргызски распознана", ms._echoes_prompt("Кардарлын кыргыз же орус тилиндеги үн билдирүүсү"))

settings.AGENT_STT_PRIMARY = "whisper"
setup(scribe=KY, whisper=RU_GARBLED)
r = run()
check("старый порядок (AGENT_STT_PRIMARY=whisper) по-прежнему доступен", calls[0] == "whisper")
settings.AGENT_STT_PRIMARY = "scribe"

settings.ELEVENLABS_API_KEY = ""
setup(scribe=KY, whisper=RU)
r = run()
check("без ключа ElevenLabs — только Whisper", calls == ["whisper"] and r["text"] == RU["text"])
settings.ELEVENLABS_API_KEY = "e"

# ---------- голос ответа ----------
agent = types.SimpleNamespace(voice_msg_engine=None, voice_msg_voice_id=None, voice_msg_voice_name=None,
                              voice_msg_language=None, voice_msg_stability=None, voice_msg_openai_voice=None,
                              eleven_assistant_id=None)
v = avs.resolve_voice(None, agent)
check("ElevenLabs без голоса (бот Старта без карточки) → GPT-Live по умолчанию",
      v and v["engine"] == avs.ENGINE_OPENAI and v["source"] == "default", v)
check("без запасного (предпрослушка ElevenLabs) → None", avs.resolve_voice(None, agent, allow_fallback=False) is None)
agent.voice_msg_openai_voice = "cedar"
check("выбранный голос GPT-Live сохраняется в запасном", avs.resolve_voice(None, agent)["voice_id"] == "cedar")
agent.voice_msg_voice_id = "abc"
check("выбран голос ElevenLabs → ElevenLabs", avs.resolve_voice(None, agent)["engine"] == avs.ENGINE_ELEVEN)

from backend.services import agent_mode
check("send_voice_message не инициатива — на Старте доступна", "send_voice_message" not in agent_mode.INITIATIVE_TOOLS)

print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
sys.exit(1 if failures else 0)
