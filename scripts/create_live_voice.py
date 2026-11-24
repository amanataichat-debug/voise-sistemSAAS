"""Свой голос GPT-Live по описанию (prompt-to-voice) — создать и подключить к платформе.

Запуск в Render Shell (нужен OPENAI_API_KEY и DATABASE_URL, как у сервера):

    python scripts/create_live_voice.py --list                 # свои голоса в базе
    python scripts/create_live_voice.py --preset aliya         # женский голос «Бишкек», русский + кыргызский
    python scripts/create_live_voice.py --preset azim        # мужской голос «Бишкек»
    python scripts/create_live_voice.py --preset aliya --dry-run   # показать запрос, ничего не создавать
    python scripts/create_live_voice.py --preset aliya --voice-id voice_…   # подключить УЖЕ созданный голос (без нового)
    python scripts/create_live_voice.py --disable aliya        # убрать голос из выбора (звонки уйдут на голос по умолчанию)
    python scripts/create_live_voice.py --key mira --title "Мира" --gender f --prompt "..." --script "..."

Как это работает (OpenAI, docs «Custom voices», 21.11.2026): POST /v1/audio/voices {type: "prompt", name,
prompt, script_hint, model: "auto"} — создание бесплатное, минуты звонка по обычной цене GPT-Live, до 100
голосов на организацию. Каждый запуск создаёт НОВЫЙ голос (у OpenAI), в базе key обновляется на новый id.
После записи голос в течение минуты появляется в выборе «Голос Voksy Live» карточки и агента под подписью
title. Перед звонками клиентов — послушайте его тест-звонком по-русски и по-кыргызски: качество акцента
OpenAI не гарантирует, его надо проверить на слух.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backend.core.config  # noqa: F401  (первым: иначе циклический импорт backend.db.session)

API_URL = "https://api.openai.com/v1/audio/voices"

# Описание — по таблице качеств OpenAI (акцент, возраст, подача, высота, тембр, текстура, громкость, темп,
# узнаваемость). Двуязычный голос: в script_hint оба языка (совет OpenAI для bilingual voice).
_RU_KY_ACCENT = (
    "Bilingual. Speaks Russian like a native resident of Bishkek: standard Russian pronunciation, correct stress "
    "and natural Russian intonation, no English or foreign accent. Speaks Kyrgyz like a native urban Bishkek "
    "speaker: clear Kyrgyz vowels ө, ү, ы, natural Kyrgyz stress (usually on the last syllable) and sentence "
    "melody, everyday colloquial city register; Russian business words inside Kyrgyz speech (заказ, тариф, "
    "доставка) sound natural, not foreign. Keep the same speaker identity, warmth and pitch in both languages. "
    "Sounds like a friendly, confident sales consultant talking on the phone, not an announcer or a narrator."
)

_SCRIPT_RU_KY = (
    "Здравствуйте! Да, слушаю вас. Смотрите, всё просто: вы оставляете заявку, а мы сами перезваниваем в течение "
    "пяти минут. Стоимость — пять тысяч пятьсот сомов в месяц, доставка по Бишкеку бесплатная. А у вас сейчас "
    "кто отвечает клиентам — вы сами? Понимаю, это много времени съедает. Давайте так: завтра в десять созвонимся "
    "и всё покажу. Договорились, хорошего дня!\n"
    "Саламатсызбы! Ооба, угуп жатам. Мына, баары жөнөкөй: сиз заявка калтырасыз, биз беш мүнөттүн ичинде өзүбүз "
    "чалабыз. Баасы айына беш миң беш жүз сом, Бишкек боюнча доставка бекер. Азыр кардарларга ким жооп берет — "
    "өзүңүзбү? Түшүнөм, бул көп убакытты алат. Анан мындай кылалы: эртең саат ондо чалып, баарын көрсөтүп берем. "
    "Макул, сүйлөштүк, жакшы күн болсун!"
)

PRESETS = {
    "aliya": {
        "title": "Алия — Бишкек (рус + кырг)",
        "gender": "f",
        "prompt": ("An original adult feminine voice, perceived age 28–35, from Bishkek, Kyrgyzstan. Warm, clear "
                   "mid-range pitch, lightly bright timbre, smooth natural texture, moderate vocal effort, relaxed "
                   "conversational pace, moderately distinctive. " + _RU_KY_ACCENT),
        "script": _SCRIPT_RU_KY,
    },
    "azim": {
        "title": "Азим — Бишкек (рус + кырг)",
        "gender": "m",
        "prompt": ("An original adult masculine voice, perceived age 30–40, from Bishkek, Kyrgyzstan. Calm, "
                   "confident lower-middle pitch, warm resonant timbre, clean texture with a little natural grain, "
                   "moderate vocal effort, steady easy conversational pace, moderately distinctive. " + _RU_KY_ACCENT),
        "script": _SCRIPT_RU_KY,
    },
}


def create_voice(api_key: str, name: str, prompt: str, script: str) -> dict:
    import httpx
    body = {"type": "prompt", "name": name, "prompt": prompt, "model": "auto"}
    if script:
        body["script_hint"] = script[:2000]
    r = httpx.post(API_URL, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                   json=body, timeout=120)
    if r.status_code >= 400:
        raise SystemExit(f"OpenAI отклонил создание голоса ({r.status_code}): {r.text[:800]}")
    data = r.json()
    if not str(data.get("id") or "").startswith("voice"):
        raise SystemExit(f"Неожиданный ответ OpenAI: {json.dumps(data, ensure_ascii=False)[:800]}")
    return data


def save_voice(key: str, voice_id: str, title: str, gender: str, prompt: str, script: str) -> None:
    from backend.db.session import SessionLocal, engine
    from backend.models.base import Base
    from backend.models.live_voice import LiveCustomVoice
    Base.metadata.create_all(bind=engine, tables=[LiveCustomVoice.__table__], checkfirst=True)
    db = SessionLocal()
    try:
        row = db.query(LiveCustomVoice).filter(LiveCustomVoice.key == key).first()
        if row is None:
            row = LiveCustomVoice(key=key)
            db.add(row)
        row.voice_id, row.title, row.gender = voice_id, title, gender
        row.description, row.script_hint, row.active = prompt, script, True
        db.commit()
    finally:
        db.close()


def list_voices() -> None:
    from backend.db.session import SessionLocal
    from backend.models.live_voice import LiveCustomVoice
    db = SessionLocal()
    try:
        rows = db.query(LiveCustomVoice).order_by(LiveCustomVoice.id).all()
        if not rows:
            print("Своих голосов нет. Создать: python scripts/create_live_voice.py --preset aliya")
        for r in rows:
            print(f"{'✓' if r.active else '✗'} {r.key:12} {r.voice_id:30} {r.gender or '-'}  {r.title}")
    finally:
        db.close()


def disable(key: str) -> None:
    from backend.db.session import SessionLocal
    from backend.models.live_voice import LiveCustomVoice
    db = SessionLocal()
    try:
        row = db.query(LiveCustomVoice).filter(LiveCustomVoice.key == key).first()
        if not row:
            raise SystemExit(f"Голоса {key} нет")
        row.active = False
        db.commit()
        print(f"Голос {key} выключен: карточки с ним звонят голосом по умолчанию.")
    finally:
        db.close()


def main() -> None:
    from backend.services.live_voices import KEY_RE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--disable")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--key")
    ap.add_argument("--title")
    ap.add_argument("--gender", choices=["m", "f"])
    ap.add_argument("--prompt")
    ap.add_argument("--script", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--voice-id", help="id уже созданного голоса OpenAI (voice_…): только записать в базу")
    a = ap.parse_args()
    if a.list:
        return list_voices()
    if a.disable:
        return disable(a.disable.strip().lower())
    p = dict(PRESETS.get(a.preset) or {})
    key = (a.key or a.preset or "").strip().lower()
    title = a.title or p.get("title")
    gender = a.gender or p.get("gender")
    prompt = a.prompt or p.get("prompt")
    script = a.script or p.get("script", "")
    if not (key and title and prompt):
        ap.error("нужен --preset или --key, --title и --prompt")
    if not KEY_RE.match(key):
        ap.error("key — латиница в нижнем регистре, цифры и _, 2–40 символов (например aliya)")
    from backend.websockets.live_client import LIVE_VOICE_SET
    if key in LIVE_VOICE_SET:
        ap.error(f"{key} — имя встроенного голоса OpenAI, выберите другое")
    if a.dry_run:
        print(json.dumps({"type": "prompt", "name": title, "prompt": prompt, "script_hint": script[:2000],
                          "model": "auto"}, ensure_ascii=False, indent=2))
        return
    if a.voice_id:
        vid = a.voice_id.strip()
        if not vid.startswith("voice_"):
            ap.error("--voice-id — id вида voice_…")
        save_voice(key, vid, title, gender or "", prompt, script)
        print(f"Готово: голос «{title}» = {vid} подключён, в карточке — «{key}» (новый голос у OpenAI не создавался).")
        return
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Нет OPENAI_API_KEY")
    data = create_voice(api_key, title, prompt, script)
    save_voice(key, data["id"], title, gender or "", prompt, script)
    print(f"Готово: голос «{title}» = {data['id']}, в карточке — «{key}».")
    print("Через минуту он появится в выборе «Голос Voksy Live». Проверьте тест-звонком по-русски и по-кыргызски.")


if __name__ == "__main__":
    main()
