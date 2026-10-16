"""
Знания агента на звонке (ветка 1610-kb-calls).

ЗАЧЕМ. До этой ветки голосовая модель на звонке получала только общие правила
разговора и дополнительные инструкции владельца — 1535 символов против 11619
у оркестратора (замер: печать настоящего промпта на живой базе). Ни документов
агента, ни пар «вопрос — ответ», ни секций «о компании», ни каталога в промпте
не было. Единственным знанием был инструмент search_pinecone, который ищет по
документам в Pinecone и в принципе не видит ни пар «вопрос — ответ», ни секций,
ни позиций каталога. На входящем звонке агент не знал даже названия компании.

ЧТО ЗДЕСЬ. Блок, который голосовой агент получает ДО первого слова и поэтому
отвечает без вызова функции:

  1. КТО МЫ      — выжимка из документов агента (doc_who_am_i и остальные)
  2. ЗНАНИЯ      — пары «вопрос — ответ» и короткие секции базы знаний
  3. ПРАЙС       — названия и цены позиций каталога, без описаний и фото
  4. МАТЕРИАЛЫ   — что лежит в библиотеке файлов агента

ПОЧЕМУ ИМЕННО В ПРОМПТ, А НЕ ПОИСКОМ. По логу звонка владельца один поход за
функцией стоит ≈4 секунды: голосовая модель GPT-Live передаёт запрос в
делегирующий бэкенд и ждёт ответа. Знание в промпте стоит 0 секунд. Минуты
звонка тарифицируются фиксированно (5,47 сом/мин), поэтому длинный промпт
звонок не удорожает, а секунды ожидания — удорожают: в разобранном звонке
~59 с из 179 с ушло в тишину. Отсюда правило: всё частое — в промпт, поиском
достаём только то, что в промпт не влезает (документы, характеристики и фото
позиций).

БЮДЖЕТ ЖЁСТКИЙ. Голосовому слою GPT-Live достаётся только НАЧАЛО промпта —
первые LIVE_VOICE_INSTRUCTIONS_MAX_CHARS символов (6000, см.
backend/websockets/live_client.py); остальное видит лишь делегирующий бэкенд,
то есть снова через функцию и снова ≈4 секунды. Поэтому блоки собираются по
приоритету и режутся по бюджету: сначала «кто мы» (без него входящий звонок
бессмыслен), потом готовые ответы, потом цены, потом материалы.

Карточки ElevenLabs на каскадном движке жёсткого лимита не имеют (промпт целиком
уходит текстовому «мозгу»), но бюджет общий — чтобы поведение на обоих движках
было одинаковым и предсказуемым.
"""

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_

from backend.core.logging import get_logger

logger = get_logger(__name__)

# Сколько символов знаний максимум уходит в промпт звонка.
# 1289 (базовые правила) + ~350 (контекст звонка) + 4000 = 5639 < 6000,
# то есть всё перечисленное доезжает до голосового слоя GPT-Live.
VOICE_KNOWLEDGE_BUDGET = 4000

# Доли внутри бюджета. Сумма намеренно больше целого: блоки берут своё по
# очереди, и если один короткий — остаток достаётся следующим.
COMPANY_BUDGET = 1100
QA_BUDGET = 1800
PRICE_BUDGET = 600
FILES_BUDGET = 400

# Сколько позиций каталога показываем в прайсе. Больше — промпт раздувается,
# а длинные перечисления на звонке всё равно бесполезны: остальное агент
# достанет инструментом find_products.
PRICE_MAX_ITEMS = 40

# Сколько файлов библиотеки перечисляем.
FILES_MAX_ITEMS = 15

# Документы агента в порядке важности для разговора: кто мы и что предлагаем
# нужны в любом звонке, «кому звоним» и «как говорим» — реже и короче.
_COMPANY_DOCS: Tuple[Tuple[str, str, int], ...] = (
    ("Кто мы", "doc_who_am_i", 450),
    ("Что предлагаем", "doc_what_we_offer", 450),
    ("Правила и цели", "doc_rules_and_goals", 300),
    ("Как говорим", "doc_how_we_talk", 200),
    ("Кому звоним", "doc_who_we_call", 200),
)

# Колонки AgentConfig со ссылкой на голосовую карточку — по ним находим агента
# по ассистенту, который сейчас на линии.
_VOICE_ASSISTANT_COLUMNS = (
    "eleven_assistant_id", "fish_assistant_id", "openai_assistant_id",
    "gemini_assistant_id", "cascade_assistant_id", "cartesia_assistant_id",
    "yandex_assistant_id",
)


# ============================================================================
# КТО ЗВОНИТ: АГЕНТ ПО ГОЛОСОВОЙ КАРТОЧКЕ
# ============================================================================

def resolve_agent_for_voice(db, assistant_id) -> Optional[Any]:
    """
    Агент обзвона, которому принадлежит эта голосовая карточка.

    Ищем по самой карточке, а не по номеру и не по задаче: так один и тот же
    код работает и на входящем звонке, и на исходящем, и в браузерном тесте
    агента. У каждого агента своя карточка голоса, поэтому совпадение одно.
    """
    if not assistant_id:
        return None
    try:
        from backend.models.agent_config import AgentConfig
        conditions = [
            getattr(AgentConfig, name) == assistant_id
            for name in _VOICE_ASSISTANT_COLUMNS
            if hasattr(AgentConfig, name)
        ]
        if not conditions:
            return None
        return db.query(AgentConfig).filter(or_(*conditions)).first()
    except Exception as e:
        logger.warning(f"[VOICE-KB] агент по карточке {assistant_id} не найден: {e}")
        return None


# ============================================================================
# ХЕЛПЕРЫ
# ============================================================================

def _clip(text: str, limit: int) -> str:
    """
    Обрезать текст до limit символов по границе слова.

    Рубить посреди слова нельзя: модель читает это как опечатку и начинает
    переспрашивать. Многоточие показывает, что продолжение есть в поиске.
    """
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:—-") + "…"


class _Budget:
    """Счётчик символов: блоки берут своё по очереди, пока бюджет не кончился."""

    def __init__(self, total: int):
        self.left = max(0, int(total))

    def take(self, want: int) -> int:
        """Сколько символов доступно блоку, который хочет want."""
        return max(0, min(want, self.left))

    def spend(self, text: str) -> None:
        self.left = max(0, self.left - len(text or ""))


# ============================================================================
# БЛОК 1: КТО МЫ (ДОКУМЕНТЫ АГЕНТА)
# ============================================================================

def build_company_block(agent, budget: int = COMPANY_BUDGET) -> str:
    """
    Выжимка из инструкций агента (бывшие «документы агента»).

    Эти пять текстов владелец заполняет при создании агента, и до сих они
    уходили только оркестратору. На звонке без них агент не знает даже, как
    называется компания, — что и ловилось на входящих.
    """
    if agent is None or budget <= 0:
        return ""
    bud = _Budget(budget)
    lines: List[str] = []
    for label, field, cap in _COMPANY_DOCS:
        raw = (getattr(agent, field, None) or "").strip()
        if not raw:
            continue
        room = bud.take(cap)
        if room < 60:          # меньше строки — смысла нет
            continue
        piece = f"{label}: {_clip(raw, room)}"
        lines.append(piece)
        bud.spend(piece)
    if not lines:
        return ""
    return "\n\n# О КОМПАНИИ И ЗАДАЧЕ\n" + "\n".join(lines)


# ============================================================================
# БЛОК 2: ПРАЙС КАТАЛОГА
# ============================================================================

def build_price_block(db, agent_config_id, budget: int = PRICE_BUDGET) -> str:
    """
    Названия и цены позиций каталога — без описаний, характеристик и фото.

    Цена — самое частое, что спрашивают, и самое дорогое для поиска: пока
    агент ходит за функцией, в трубке 4 секунды тишины. Строка «Название —
    цена» стоит ~40 символов, поэтому весь прайс небольшого магазина
    укладывается в промпт целиком и называется мгновенно. Характеристики и
    фото остаются за find_products.
    """
    if not agent_config_id or budget <= 0:
        return ""
    try:
        from backend.models.agent_knowledge import AgentKnowledgeEntry
        from backend.services import agent_knowledge_service as kb_fmt
        rows = (
            db.query(AgentKnowledgeEntry)
            .filter(AgentKnowledgeEntry.agent_config_id == agent_config_id,
                    AgentKnowledgeEntry.kind == "product",
                    AgentKnowledgeEntry.is_active.is_(True))
            .order_by(AgentKnowledgeEntry.position.asc(),
                      AgentKnowledgeEntry.created_at.asc())
            .limit(PRICE_MAX_ITEMS + 1)
            .all()
        )
    except Exception as e:
        logger.warning(f"[VOICE-KB] прайс не собран: {e}")
        return ""
    if not rows:
        return ""

    total = (
        db.query(AgentKnowledgeEntry.id)
        .filter(AgentKnowledgeEntry.agent_config_id == agent_config_id,
                AgentKnowledgeEntry.kind == "product",
                AgentKnowledgeEntry.is_active.is_(True))
        .count()
    )

    bud = _Budget(budget)
    lines: List[str] = []
    shown = 0
    for row in rows[:PRICE_MAX_ITEMS]:
        title = (row.title or "").strip()
        if not title:
            continue
        price = kb_fmt.format_price(row.price, row.currency)
        mark = "" if row.in_stock else " (нет в наличии)"
        piece = f"- {_clip(title, 70)} — {price}{mark}"
        if bud.take(len(piece) + 1) < len(piece) + 1:
            break
        lines.append(piece)
        bud.spend(piece + "\n")
        shown += 1
    if not lines:
        return ""

    tail = ""
    if total > shown:
        tail = (f"\nПоказаны {shown} позиций из {total}. Остальные, а также "
                f"характеристики и фото — инструментом find_products.")
    return ("\n\n# ПРАЙС (цены знаешь наизусть, называй сразу)\n"
            + "\n".join(lines) + tail)


# ============================================================================
# БЛОК 3: МАТЕРИАЛЫ (ФАЙЛЫ АГЕНТА)
# ============================================================================

def build_files_block(db, agent_config_id, budget: int = FILES_BUDGET) -> str:
    """
    Что лежит в библиотеке агента: прайсы, презентации, договоры.

    На звонке агент сам файл отправить не может (в трубку файл не положишь),
    но должен знать, что материалы есть, и уметь это пообещать — отправит их
    после звонка разбор звонка или переписка в мессенджере. Содержимое файлов
    достаётся поиском, здесь только названия.
    """
    if not agent_config_id or budget <= 0:
        return ""
    try:
        from backend.models.agent_file import AgentFile
        rows = (
            db.query(AgentFile)
            .filter(AgentFile.agent_config_id == agent_config_id,
                    AgentFile.source == "library")
            .order_by(AgentFile.created_at.asc())
            .limit(FILES_MAX_ITEMS)
            .all()
        )
    except Exception as e:
        logger.warning(f"[VOICE-KB] список файлов не собран: {e}")
        return ""
    if not rows:
        return ""

    bud = _Budget(budget)
    lines: List[str] = []
    for row in rows:
        name = (row.title or row.filename or "").strip()
        if not name:
            continue
        note = (row.description or "").strip()
        piece = f"- {_clip(name, 60)}" + (f" — {_clip(note, 70)}" if note else "")
        if bud.take(len(piece) + 1) < len(piece) + 1:
            break
        lines.append(piece)
        bud.spend(piece + "\n")
    if not lines:
        return ""
    return ("\n\n# МАТЕРИАЛЫ, КОТОРЫЕ МОЖНО ОТПРАВИТЬ КЛИЕНТУ\n"
            + "\n".join(lines)
            + "\nЕсли клиент просит прислать подробности — скажи, что отправим "
              "в WhatsApp или Telegram, и уточни, куда удобнее. Сам файл в "
              "разговоре не зачитывай.")


# ============================================================================
# СБОРКА БЛОКА ЗНАНИЙ ДЛЯ ЗВОНКА
# ============================================================================

def build_voice_knowledge_block(db, agent, budget: int = VOICE_KNOWLEDGE_BUDGET
                                ) -> Tuple[str, Dict[str, int]]:
    """
    Всё, что агент знает на звонке наизусть. Возвращает блок и его состав
    (для журнала звонка — владельцу видно, что именно уехало в промпт).

    Порядок — он же приоритет при нехватке бюджета: компания → готовые ответы
    и секции → прайс → материалы.
    """
    if agent is None:
        return "", {}

    agent_id = getattr(agent, "id", None)
    bud = _Budget(budget)
    parts: List[str] = []
    stats: Dict[str, int] = {}

    company = build_company_block(agent, bud.take(COMPANY_BUDGET))
    if company:
        parts.append(company)
        bud.spend(company)
        stats["company"] = len(company)

    if agent_id is not None:
        try:
            from backend.services.agent_knowledge_service import build_prompt_block
            kb = build_prompt_block(db, agent_id, budget=bud.take(QA_BUDGET))
        except Exception as e:
            logger.warning(f"[VOICE-KB] знания не собраны: {e}")
            kb = ""
        if kb:
            parts.append(kb)
            bud.spend(kb)
            stats["knowledge"] = len(kb)

        price = build_price_block(db, agent_id, bud.take(PRICE_BUDGET))
        if price:
            parts.append(price)
            bud.spend(price)
            stats["price"] = len(price)

        files = build_files_block(db, agent_id, bud.take(FILES_BUDGET))
        if files:
            parts.append(files)
            bud.spend(files)
            stats["files"] = len(files)

    if not parts:
        return "", {}

    # Главное правило: то, что выше, уже у тебя в голове. Голосовой слой
    # GPT-Live по умолчанию инструктирован делегировать всё «про базу знаний»
    # бэкенду (VOICE_DELEGATION_HINT), поэтому проговариваем исключение прямо.
    parts.append(
        "\n\n# КАК ПОЛЬЗОВАТЬСЯ ЭТИМ НА ЗВОНКЕ\n"
        "Всё перечисленное выше ты уже знаешь: отвечай сразу, своими словами, "
        "не делегируй бэкенду и не вызывай поиск. В прайсе выше — только "
        "название и цена; ЛЮБАЯ ПОДРОБНОСТЬ про позицию каталога, которой нет "
        "в прайсе буквально (что входит, какие варианты, условия, номера, "
        "сроки, характеристики, фото) — вызывай find_products, не отвечай "
        "«не знаю» не проверив там. Всё остальное, чего нет ни в этом блоке, "
        "ни в прайсе — search_knowledge_base. Если ответа нет нигде — не "
        "выдумывай: скажи, что уточнишь, и предложи перезвонить или прислать "
        "в мессенджер."
    )
    block = "".join(parts)
    stats["total"] = len(block)
    return block, stats


def build_call_system_prompt(db, assistant, agent, call_context: str = "",
                             max_chars: Optional[int] = None,
                             budget: int = VOICE_KNOWLEDGE_BUDGET
                             ) -> Tuple[str, Dict[str, int]]:
    """
    Итоговый system_prompt звонка: промпт карточки + знания агента + контекст.

    max_chars — жёсткий предел для всего промпта (для GPT-Live это
    LIVE_VOICE_INSTRUCTIONS_MAX_CHARS: длиннее голосовой слой не увидит).
    Режем при нехватке только знания: правила разговора и контекст звонка
    обязательны.
    """
    base = (getattr(assistant, "system_prompt", None) or "").strip()
    context = (call_context or "").strip()
    tail = f"\n\n[Контекст звонка] {context}" if context else ""

    block, stats = build_voice_knowledge_block(db, agent, budget=budget)

    if max_chars and block:
        room = max_chars - len(base) - len(tail)
        if room < len(block):
            if room < 400:
                # Промпт карточки сам съел бюджет (длинные доп. инструкции
                # владельца). Знания не влезают — честно пишем в лог, иначе
                # потом не понять, почему агент «забыл» базу.
                logger.warning(
                    f"[VOICE-KB] знания не влезли в {max_chars} символов: "
                    f"правила {len(base)}, контекст {len(tail)}; "
                    f"сократите дополнительные инструкции агента"
                )
                block, stats = "", {}
            else:
                logger.warning(
                    f"[VOICE-KB] знания обрезаны с {len(block)} до {room} символов "
                    f"(лимит голосового слоя {max_chars})"
                )
                block = _clip(block, room)
                stats["total"] = len(block)
                stats["trimmed"] = 1

    return f"{base}{block}{tail}".strip(), stats


# ============================================================================
# ФУНКЦИИ ЗНАНИЙ НА КАРТОЧКЕ ГОЛОСА — СЧИТАЕМ ЖИВЬЁМ НА КАЖДОМ ЗВОНКЕ
# ============================================================================
#
# ЗАЧЕМ ЭТОТ БЛОК ПОЯВИЛСЯ. Раньше список функций (search_knowledge_base,
# find_products) синхронизировался с составом базы только РЕАКТИВНО — при
# каждой правке базы через кабинет (api/agent.py:_voice_sync_kb_functions).
# Это хрупко: любая запись в agent_configs.eleven_assistant_configs.functions,
# сделанная в обход этого кода пути — старые данные, оставшиеся с прошлых
# версий (search_pinecone), ручная правка в БД, миграция, — так и останется
# неисправленной до следующей правки базы владельцем. На живом звонке
# (сессия 40049857, 16.10.2026) это проявилось ровно так: в списке функций
# был только старый search_pinecone, хотя search_knowledge_base/find_products
# уже существовали в коде — колонка functions этой карточки просто ни разу
# не пересчитывалась после того, как агент обзавёлся базой знаний.
#
# Поэтому функции знаний считаются заново ПРЯМО ПЕРЕД звонком (там же, где
# подменяется system_prompt — sip_gateway.py и handler_eleven.py) и
# подставляются в память, как и промпт: хранимая колонка значения не имеет,
# правильный список гарантирован на каждом звонке независимо от истории.
# Реактивный пересчёт в api/agent.py при правке базы остаётся — это только
# для того, чтобы кабинет и раздел «Функции» показывали актуальную картину
# сразу, без ожидания следующего звонка.

# Какие функции знаний вообще существуют и их описания для карточки.
KB_VOICE_FUNCTIONS = {
    "search_knowledge_base": "Ищет ответ в базе знаний компании: вопросы-ответы, "
                             "разделы о компании и содержимое документов.",
    "find_products": "Подбирает позиции каталога по названию, характеристикам и цене, "
                     "возвращает цену, наличие и id фото.",
}
# Старое имя снимаем с карточек везде, где пересчитываем функции: модель не
# должна выбирать между двумя разными инструментами поиска.
KB_VOICE_LEGACY = ("search_pinecone",)


def kb_function_names(db, agent) -> set:
    """
    Какие функции знаний нужны агенту ПРЯМО СЕЙЧАС, по текущему состоянию базы.

    Лишний инструмент не бесплатен: на каждом ответе модель перебирает весь
    список, и это одна из причин задержки на звонке. Поэтому каталог
    подключаем только когда в базе есть позиции, а поиск — когда в базе есть
    хоть что-то (записи или документы в Pinecone).
    """
    names = set()
    agent_id = getattr(agent, "id", None)
    if agent_id is None:
        return names
    try:
        from backend.models.agent_knowledge import AgentKnowledgeEntry
        kinds = {
            row[0] for row in
            db.query(AgentKnowledgeEntry.kind)
            .filter(AgentKnowledgeEntry.agent_config_id == agent_id,
                    AgentKnowledgeEntry.is_active.is_(True))
            .distinct().all()
        }
    except Exception as e:
        logger.warning(f"[VOICE-KB] состав базы не прочитан: {e}")
        kinds = set()
    if kinds or getattr(agent, "kb_namespace", None):
        names.add("search_knowledge_base")
    if "product" in kinds:
        names.add("find_products")
    return names


def merge_kb_functions(functions, wanted: set):
    """
    Чистая функция: список/словарь функций карточки → тот же формат, но с
    KB_VOICE_LEGACY и чужими KB_VOICE_FUNCTIONS выброшенными и `wanted`
    добавленными. Общая реализация для реактивной правки кабинета
    (api/agent.py) и живого пересчёта на звонке (ниже).
    """
    drop = set(KB_VOICE_FUNCTIONS) | set(KB_VOICE_LEGACY)
    if isinstance(functions, dict) and "enabled_functions" in functions:
        names = [n for n in list(functions.get("enabled_functions", [])) if n not in drop]
        names.extend(sorted(wanted))
        return {"enabled_functions": names}
    items = list(functions) if isinstance(functions, list) else []
    items = [f for f in items if (f.get("name") if isinstance(f, dict) else f) not in drop]
    items.extend({"name": n, "description": KB_VOICE_FUNCTIONS[n]} for n in sorted(wanted))
    return items


def apply_live_kb_functions(db, assistant, agent) -> None:
    """
    Подставить в карточку голоса (В ПАМЯТИ, не в базу) функции знаний,
    пересчитанные по текущему состоянию базы — вызывать прямо перед стартом
    звонка/сессии, рядом с подменой system_prompt.

    Использует set_committed_value, тот же приём, что и для system_prompt и
    greeting_message в api/sip_gateway.py: карточка в БД не меняется, сессия
    получает верные функции независимо от того, что хранится в колонке.
    """
    if agent is None:
        return
    try:
        from sqlalchemy.orm.attributes import set_committed_value
        wanted = kb_function_names(db, agent)
        merged = merge_kb_functions(getattr(assistant, "functions", None), wanted)
        set_committed_value(assistant, "functions", merged)
    except Exception as e:
        logger.warning(f"[VOICE-KB] функции знаний не пересчитаны: {e}")
