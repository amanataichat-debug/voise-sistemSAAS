"""Свой голос GPT-Live по описанию (prompt-to-voice) — создать и подключить к платформе.

Запуск в Render Shell (нужен OPENAI_API_KEY и DATABASE_URL, как у сервера):

    python scripts/create_live_voice.py --list                 # свои голоса в базе
    python scripts/create_live_voice.py --preset all           # все четыре: azim, azim2, aliya, aliya2 (v2, харизма)
    python scripts/create_live_voice.py --preset azim aliya    # только выбранные
    python scripts/voice_samples.py                            # послушать: ссылки на mp3 всех голосов (см. там)
    python scripts/create_live_voice.py --preset aliya --dry-run   # показать запрос, ничего не создавать
    python scripts/create_live_voice.py --preset aliya --voice-id voice_…   # подключить УЖЕ созданный голос (без нового)
    python scripts/create_live_voice.py --disable aliya        # убрать голос из выбора (звонки уйдут на голос по умолчанию)
    python scripts/create_live_voice.py --key mira --title "Мира" --gender f --prompt "..." --script "..."

Как это работает (OpenAI, docs «Custom voices», 21.11.2026): POST /v1/audio/voices {type: "prompt", name,
prompt, script_hint, model: "auto"} — создание бесплатное, минуты звонка по обычной цене GPT-Live, до 100
голосов на организацию. Каждый запуск создаёт НОВЫЙ голос (у OpenAI), в базе key обновляется на новый id.
После записи голос в течение минуты появляется в выборе «Голос Voksy Live» карточки и агента под подписью
title. ВАЖНО: свой голос привязан к проекту OpenAI — создавайте его ТЕМ ЖЕ ключом, на котором идут звонки (этот
скрипт берёт OPENAI_API_KEY сервера). Голос с чужого ключа на звонке не откроется, и звонок пойдёт голосом
по умолчанию (24.11.2026: «Азим» звучал женским marin). Перед звонками клиентов — послушайте его тест-звонком по-русски и по-кыргызски: качество акцента
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

# Описание — по таблице качеств OpenAI (docs «Custom voices»: accent, age, presentation, pitch, timbre, texture,
# loudness, speech rate, distinctiveness) в форме рецептов live-prompting: строка-теги + описание словами.
# v2 (24.11.2026, ветка 2411-live-voices): v1 был «calm / moderate / moderately distinctive» — вежливый, но плоский.
# Теперь харизма, улыбка в голосе, живая смена высоты, естественные вдохи и смех; без пародии на акцент.
# Звучание описывает голос; поведение (что говорить, когда шутить) — промпт платформы, он общий для всех голосов.
_SOUND = (
    "Clean, full-bandwidth recording with intelligible consonants, natural breaths and complete word endings. "
    "Lively conversational pitch variation and enough vocal flexibility for both calm explanations and animated, "
    "playful moments; an audible smile in the voice; capable of a natural, warm, unforced laugh or chuckle in the "
    "middle of speech. Avoid announcer, radio-host, narrator or call-center tone; avoid synthetic buzz, muffled or "
    "lo-fi sound, exaggerated vocal fry and any caricature."
)
_BISHKEK = (
    "Bilingual, from Bishkek, Kyrgyzstan. Russian: native, standard Russian pronunciation with correct stress and "
    "natural lively Russian intonation, no English or foreign accent. Kyrgyz: native urban Bishkek speaker, clear "
    "Kyrgyz vowels ө, ү, ы and the ң sound, natural Kyrgyz stress (usually on the last syllable) and sentence "
    "melody, everyday colloquial city register; Russian business words inside Kyrgyz speech (заказ, тариф, "
    "доставка, менеджер) sound natural, not foreign. Keep the same speaker identity, warmth and pitch in both "
    "languages. Make the regional pronunciation audible without an impression or caricature."
)


def _script(glad: str) -> str:
    """Образец речи для создания голоса: оба языка, живые эмоции (совет OpenAI для двуязычного голоса)."""
    return (
        "Алло, здравствуйте! Да-да, слышу вас отлично. Ой, ну это вы прямо в точку, ха-ха, у всех так начинается! "
        "Смотрите, всё просто: клиент пишет ночью, а ответ уже через пять секунд, и никто не потерялся. Серьёзно? "
        "Сорок заявок в день? Вот это да! Понимаю, когда менеджер один, он физически не успевает, и это обидно, "
        "деньги же уходят. Стоимость — семь тысяч пятьсот сомов в месяц, без платы за подключение. Давайте так: "
        f"завтра в одиннадцать созвонимся, я всё покажу на ваших товарах. Договорились? Отлично, очень {glad}, "
        "что познакомились!\n"
        "Саламатсызбы! Ооба, угуп турам. Ой, ха-ха, туура айтасыз, көпчүлүктө ошондой болот! Карасаңыз, баары "
        "жөнөкөй: кардар түнкүсүн жазса деле, беш секундда жооп барат, эч ким жоголбойт. Чын элеби? Күнүнө кырк "
        "заявка? Мына сонун! Түшүнөм, менеджер жалгыз болсо, үлгүрбөй калат да, акча кетип жатат. Баасы айына жети "
        "миң беш жүз сом, подключение үчүн өзүнчө төлөм жок. Анан мындай кылалы: эртең саат он бирде чалышып, "
        "баарын сиздин товарлар менен көрсөтүп берем. Макулбу? Сонун, таанышканыма абдан кубанычтамын!"
    )


PRESETS = {
    "azim": {
        "title": "Азим — Бишкек, харизма (рус + кырг)",
        "gender": "m",
        "prompt": ("Bishkek Russian and Kyrgyz. approximately 30. masculine voice. warm velvety baritone timbre. "
                   "smooth texture with a little natural grain. lower-middle pitch. moderate-to-lively loudness. "
                   "medium-fast speech rate. high distinctiveness. A charismatic, confident young man who loves "
                   "talking to people: easy charm, playful humour and genuine curiosity, relaxed self-assurance, "
                   "every word clear. " + _SOUND + " " + _BISHKEK),
        "script": _script("рад"),
    },
    "azim2": {
        "title": "Азим 2 — Бишкек, энергичный (рус + кырг)",
        "gender": "m",
        "prompt": ("Bishkek Russian and Kyrgyz. mid-20s to 30. masculine voice. bright, energetic baritone timbre. "
                   "clear texture with light natural raspiness. middle pitch. lively loudness. fast but "
                   "easy-to-follow speech rate. high distinctiveness. A witty, upbeat, quick-thinking guy with "
                   "infectious energy and a quick laugh; sounds like a friend who is also an expert. "
                   + _SOUND + " " + _BISHKEK),
        "script": _script("рад"),
    },
    "aliya": {
        "title": "Алия — Бишкек, обаятельная (рус + кырг)",
        "gender": "f",
        "prompt": ("Bishkek Russian and Kyrgyz. approximately 28 to 32. feminine voice. warm, bright mezzo timbre. "
                   "smooth, clear texture. mid pitch. lively loudness. medium-fast speech rate. high "
                   "distinctiveness. A charming, sparkling, friendly woman with a ready, ringing laugh, playful "
                   "warmth and quick wit; confident and clear. " + _SOUND + " " + _BISHKEK),
        "script": _script("рада"),
    },
    "aliya2": {
        "title": "Алия 2 — Бишкек, бархатная (рус + кырг)",
        "gender": "f",
        "prompt": ("Bishkek Russian and Kyrgyz. early 30s. feminine voice. lower, velvety alto timbre. smooth "
                   "texture with soft breathiness. lower-middle pitch. moderate loudness. medium speech rate. high "
                   "distinctiveness. Elegant, self-assured and warm, with a clever, gentle sense of humour and a "
                   "soft low chuckle; calm charisma, every word clear. " + _SOUND + " " + _BISHKEK),
        "script": _script("рада"),
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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--disable")
    ap.add_argument("--preset", nargs="+", choices=sorted(PRESETS) + ["all"],
                    help="один или несколько готовых голосов; all — все четыре")
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
    presets = sorted(PRESETS) if a.preset and "all" in a.preset else (a.preset or [])
    if len(presets) > 1:
        if a.voice_id or a.key:
            ap.error("--voice-id и --key — только для одного голоса")
        for name in presets:
            _run_one(ap, a, name, dict(PRESETS[name]))
        return
    _run_one(ap, a, presets[0] if presets else None, dict(PRESETS.get(presets[0]) or {}) if presets else {})


def _run_one(ap, a, preset, p) -> None:
    from backend.services.live_voices import KEY_RE
    key = (a.key or preset or "").strip().lower()
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
