# backend/websockets/voice_hints.py
"""
Механика промпта Gemini Live (ветка 1111-clean-system).

Правила разговора (манера, язык, кыргызский, продажа, завершение) — общие для всех движков и живут в
backend/websockets/voice_rules.py. Здесь только:
  * split_call_context / call_context_block — контекст звонка (кто звонит, карточка, хронология) переносится
    из конца промпта в начало: в середине длинной инструкции Gemini его не держал (звонок d08c06f4);
  * persona_gender_conflict — предупреждение в журнал звонка, если инструкция написана от мужского лица,
    а голос женский (или наоборот): Gemini начинал «играть» персонажа и менять тембр (звонок d4229a5f).

Справочник по Gemini 3.8 Live: docs/gemini_live_3_8_reference.md (поля languageCode нет — язык держит
промпт; affective dialog удалён; proactive audio всегда включён).
"""

from typing import Optional

from backend.websockets.voice_rules import voice_gender

CALL_CONTEXT_MARK = "[Контекст звонка]"


def split_call_context(prompt: str):
    """
    Промпт звонка = карточка + знания + «[Контекст звонка] …» в самом конце (agent_voice_context.build_call_system_prompt):
    кто звонит, что о нём известно, хронология звонков и переписки. Для Gemini этот хвост переносим В НАЧАЛО — в
    середине 30 тыс. символов модель его не держала («как будто нет памяти», звонок d08c06f4). Возвращает
    (промпт без контекста, текст контекста).
    """
    text = prompt or ""
    i = text.rfind(CALL_CONTEXT_MARK)
    if i < 0:
        return text, ""
    return text[:i].rstrip(), text[i + len(CALL_CONTEXT_MARK):].strip()


def call_context_block(context: str) -> str:
    if not (context or "").strip():
        return ""
    return "# КОНТЕКСТ ЗВОНКА И КЛИЕНТ (что ты знаешь об этом звонке и человеке)\n" + context.strip() + "\n\n"


def persona_gender_conflict(prompt: str, voice: str) -> Optional[str]:
    """
    Инструкция написана от мужского персонажа, а голос женский (или наоборот) — Gemini начинает «играть»
    персонажа и менять тембр. Возвращает текст предупреждения для журнала звонка или None.
    """
    import re
    gender = voice_gender(voice)
    if not gender or not prompt:
        return None
    text = prompt.lower()
    male = len(re.findall(r"\bя\s+(?:рад|готов|хотел|понял|записал|отправил|уточнил|звонил|сказал)\b", text))
    male += 3 * len(re.findall(r"мужск\w*\s+род", text))
    female = len(re.findall(r"\bя\s+(?:рада|готова|хотела|поняла|записала|отправила|уточнила|звонила|сказала)\b", text))
    female += 3 * len(re.findall(r"женск\w*\s+род", text))
    if gender == "f" and male > female:
        return (f"Голос {voice} женский, а инструкция написана от мужского лица — модель может менять тембр. "
                "Выберите мужской голос (Puck, Charon, Fenrir, Orus) или перепишите инструкцию в женском роде")
    if gender == "m" and female > male:
        return (f"Голос {voice} мужской, а инструкция написана от женского лица — модель может менять тембр. "
                "Выберите женский голос (Aoede, Kore, Leda, Zephyr) или перепишите инструкцию в мужском роде")
    return None
