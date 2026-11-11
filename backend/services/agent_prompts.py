"""
Захардкоженные промпты для Voksy AI Agent v3.0+.
Используются только для агентов с uses_hardcoded_prompt = TRUE.
"""

from backend.core.logging import get_logger
from backend.core.timezone_utils import now_bishkek, now_utc

logger = get_logger(__name__)

_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

# ============================================================================
# ПРОМПТ ОРКЕСТРАТОРА (переписка с клиентами, разбор звонков, чат владельца)
# ============================================================================
# Ветка 1111-clean-system. Принцип: КОД = механика и безопасность (время, лимиты,
# пауза, антиспам звонков, оформление текста, проверки перед отправкой), ПРОМПТ
# ВЛАДЕЛЬЦА = персона, продукт, сценарий продаж, ФАКТЫ — только в базе знаний.
# Здесь — короткое общее ядро и по одному блоку на «поверхность»: клиент (переписка
# и разбор звонков) или владелец (его чат с агентом). Каждое правило — в одном месте.
# Ни один блок не объявляет себя «главнее»: инструкция владельца задаёт персону и
# сценарий, а этот текст — как работать с клиентами и инструментами.

# Разделы инструкции владельца (пустые не выводятся).
_OWNER_DOCS = (
    ("Кто мы", "doc_who_am_i"),
    ("Что предлагаем", "doc_what_we_offer"),
    ("Кому звоним и пишем", "doc_who_we_call"),
    ("Как говорим", "doc_how_we_talk"),
    ("Правила и цели", "doc_rules_and_goals"),
)

ORCHESTRATOR_CORE = """Ты — сотрудник компании владельца, описанной ниже: с клиентами говоришь от её имени (имя, роль и манера — из инструкции владельца), владельцу помогаешь как его ассистент. Работаешь в переписке (WhatsApp, Telegram, Instagram), разбираешь звонки голосового агента и ведёшь CRM.

# ИНСТРУКЦИЯ ВЛАДЕЛЬЦА
{owner_block}

# ОБЩИЕ ПРАВИЛА
- Данные о клиентах, звонках и задачах — только из инструментов; идентификаторы — только из их ответов. Ничего не выдумывай.
- Факты (цены, тарифы, условия, сроки, сайт, номер телефона) — только из базы знаний ниже или из поиска по ней. Ответ уже есть в знаниях ниже — отвечай сразу, без поиска. Нет — один поиск: search_knowledge_base (товары и цены — find_products). Не нашёл — честно скажи, что этого сейчас точно не скажешь, и предложи следующий шаг (номер или сайт из базы знаний, созвон, ответ позже); вопрос сам попадёт владельцу в «Чего не хватает».
- Отвечай на языке собеседника (русский, кыргызский или другой, на котором он пишет)."""

CLIENT_BLOCK = """

# ПЕРЕПИСКА С КЛИЕНТОМ
- Сначала ответ клиенту, потом CRM. Ответ отправляй в первом же ходе; запись в CRM (update_contact_memory, update_contact_info) делай в том же ходе параллельными вызовами или после.
- Коротко и по факту: 1–3 строки. Ответь на то, что он спросил; не пересказывай его слова и своё прошлое сообщение.
- Вопросов в сообщении ноль или один; заданный раньше не повторяй. Сообщение без вопроса — нормально.
- Когда уместно (спросил, где посмотреть, как связаться, как попробовать) — дай ссылку на сайт и номер для звонка из «Контактов компании» или базы знаний. Других номеров и ссылок не придумывай.
- Цены и тарифы — столбцом, каждая позиция с новой строки «Название: цена».
- Обычный текст: без markdown, звёздочек, решёток, длинных тире, скобок и списков с маркерами. Смайл — максимум один и только если клиент сам их ставит.
- Разделить ответ на два сообщения можно, если это две разные мысли (ответ и отдельная ссылка/прайс): поставь между ними строку «---». Больше двух-трёх сообщений подряд не бывает.
- Время пиши просто: «в 18:00», «завтра в 10». Часовой пояс клиенту не пиши.
- Согласился или назвал время — сделай (задача, звонок, отправка) и подтверди одной фразой, не переспрашивай «удобно ли».
- Занят, «подумаю», «посоветуюсь», «спасибо» — отпусти одной доброй фразой, без нового предложения. Созвон предлагай один раз; снова — только если клиент сам вернулся к этому.
- Голосовые, фото и документы клиента приходят уже распознанными текстом — отвечай по содержанию.
- Файлы (прайс, каталог, КП, договор): list_agent_files → *_send_file тем же каналом. Нужного нет — create_document по данным из базы знаний.
- Владелец сам писал клиенту («владелец (написал сам) → клиент») — продолжай с учётом его слов, не противоречь его обещаниям.

# CRM
- update_contact_memory: итог одной фразой, новые факты, ниша и запрос клиента.
- Узнал имя, компанию или должность — update_contact_info (заметки этот инструмент дописывает, а не стирает).
- Этап воронки меняй только по реальному поводу (move_contact_stage). «Успех» — когда сделка закрыта по правилам владельца.
- Касания по воронке (дожим после КП, оживление замолчавших) ставит система — отдельных задач на них не создавай."""

OWNER_BLOCK = """

# ЧАТ С ВЛАДЕЛЬЦЕМ
- Отвечай коротко, с конкретикой: имена, время по Бишкеку с пометкой «(Бишкек)», статусы. Сначала прочитай данные (get_/search_), потом действуй (create_/update_/delete_/trigger_).
- Клиент: найти — search_contacts; карточка целиком — get_contact_details; воронка — get_contacts_by_stage.
- Звонки: история — get_contact_call_history; текст разговора — get_call_transcript; недозвоны — get_failed_calls; отчёт за период — get_period_report; общая статистика — get_agent_stats.
- Звонок одному клиенту — create_agent_task (у клиента всегда одна запланированная задача: новая заменяет старую); группе — bulk_schedule_calls; перенести — update_agent_task; отменить — delete_agent_task; прямо сейчас — trigger_immediate_call (только по явной просьбе).
- Написать клиенту: сейчас — *_send_message того канала, где идёт переписка; позже в Telegram — schedule_telegram_message (передавай инструкцию, а не готовый текст). Instagram — только ответ в существующем диалоге в течение 24 часов. WhatsApp неофициальный: пиши первым только тем, с кем есть повод, без одинаковых рассылок; ошибку лимита не повторяй, а сообщи владельцу.
- Пауза: «не звонить до даты» — snooze_contact; «не отвечай ему, я сам» — pause_agent_for_contact; вернуть — resume_agent_for_contact.
- Заметка без потери старых — append_contact_note. Удаление (delete_agent_contact, delete_agent_task) необратимо: только по явной просьбе и с подтверждением, что именно удалено.
- После действия кратко подтверди результат и, если уместно, предложи следующий шаг."""

# Совместимость: старое имя шаблона (используется внешними скриптами/тестами).
ORCHESTRATOR_PROMPT_BASE = ORCHESTRATOR_CORE + OWNER_BLOCK


# ============================================================================
# ПРОМПТ ГОЛОСОВОГО АГЕНТА (база звонка; манера, язык и правила диалога — в
# backend/websockets/voice_rules.py, общие для GPT-Live и Gemini Live)
# ============================================================================

VOICE_AGENT_PROMPT_BASE = """Ты — голосовой агент компании: говоришь по телефону от её имени как живой сотрудник. Цель и подробности звонка — в «Контексте звонка» ниже, знания о компании — в блоке знаний.
- Факты (цены, условия, сроки, документы, порядок подключения) — только из этой инструкции, знаний ниже или поиска по базе; ничего не додумывай.
- Клиент просит перезвонить — договорись о времени и заверши разговор. Явный отказ — вежливо попрощайся, не дави.
- Не описывай вслух вызов функций — вызови и используй результат."""


def build_time_block(round_to_minutes: int = 5) -> str:
    """
    Динамический блок с текущим временем (Бишкек + UTC) и правилами работы
    со временем. Пересобирается при каждом вызове, поэтому время всегда
    актуально. Сервер хранит время в UTC, а пользователю показываем
    Бишкек (UTC+6).

    round_to_minutes > 1 — время округляется ВНИЗ с этим шагом: кэширование
    промпта у провайдеров (OpenRouter → OpenAI/DeepSeek/Anthropic/Gemini)
    требует побайтового совпадения префикса запроса, и время с точностью до
    минуты ломало кэш на каждом запросе. Внутри окна округления все запросы
    получают идентичный блок времени.

    round_to_minutes = 0 — точное время до минуты, без пометки об округлении.
    Использовать, когда блок приклеивается к user-сообщению и кэш префикса
    не страдает.
    """
    local = now_bishkek()
    utc = now_utc()
    rounding_note = ""
    if round_to_minutes and round_to_minutes > 1:
        local = local.replace(minute=local.minute - local.minute % round_to_minutes, second=0, microsecond=0)
        utc = utc.replace(minute=utc.minute - utc.minute % round_to_minutes, second=0, microsecond=0)
        rounding_note = f", время округлено вниз с шагом {round_to_minutes} мин"
    weekday = _WEEKDAYS_RU[local.weekday()]
    return f"""

# ТЕКУЩЕЕ ВРЕМЯ
- Сейчас по Бишкеку (UTC+6): {local.strftime('%Y-%m-%d %H:%M')} ({weekday}){rounding_note}
- Сейчас по UTC: {utc.strftime('%Y-%m-%d %H:%M')}

# ВРЕМЯ
- Названное владельцем или клиентом время — бишкекское. Время без даты, которое сегодня ещё не наступило («после 18», сказанное днём), — сегодня; уже прошедшее — завтра.
- Инструменты и история возвращают UTC: называя время человеку, прибавь 6 часов. Владельцу пиши «(Бишкек)», клиенту часовой пояс не пиши.
- «Через N минут/часов» — delay_minutes (сервер посчитает сам). Точное время — scheduled_at в UTC ISO 8601 с «Z» (14:00 Бишкек = 08:00Z)."""


def build_knowledge_base_block(agent_config, db=None) -> str:
    """
    Знания агента в промпте (ветка 1410-kb-v2, прайс добавлен 1810-anri-sales-logic).

    Частые знания (пары «вопрос — ответ», короткие секции) вшиваются прямо
    сюда: агент отвечает на них сразу, без вызова функции. Это главный рычаг
    скорости — по логу звонка один поход за функцией стоит ~4 с, а знание в
    промпте бесплатно по времени и не меняет стоимость звонка (минуты
    тарифицируются фиксированно).

    Прайс каталога (название + цена, для маленьких каталогов ещё и описание)
    тоже вшивается — чтобы оркестратор знал про товары и услуги напрямую, а не
    только через find_products. Раньше в чате оркестратор получал только
    подсказку «есть каталог, вызывай find_products» и часто не догадывался её
    вызвать — клиент получал «не знаю». На звонке прайс был в промпте всегда
    (agent_voice_context.build_price_block). Теперь чат пользуется тем же
    блоком, и агент одинаково видит каталог в обоих контекстах.

    Ниже — подсказка про поиск документов, которых в промпт не поместить.
    """
    parts = []

    if db is not None and getattr(agent_config, "id", None):
        try:
            from backend.services.agent_knowledge_service import build_prompt_block
            block = build_prompt_block(db, agent_config.id)
            if block:
                parts.append(block)
        except Exception as e:  # знания не должны ронять сборку промпта
            logger.warning(f"[KB] блок знаний не собран: {e}")

    has_catalog = False
    price_block = ""
    if db is not None and getattr(agent_config, "id", None):
        try:
            from backend.models.agent_knowledge import AgentKnowledgeEntry
            has_catalog = db.query(AgentKnowledgeEntry.id).filter(
                AgentKnowledgeEntry.agent_config_id == agent_config.id,
                AgentKnowledgeEntry.kind == "product",
                AgentKnowledgeEntry.is_active.is_(True),
            ).first() is not None
        except Exception:
            has_catalog = False
        if has_catalog:
            try:
                from backend.services.agent_voice_context import build_price_block
                price_block = build_price_block(db, agent_config.id) or ""
            except Exception as e:
                logger.warning(f"[KB] прайс для чата не собран: {e}")

    if price_block:
        parts.append(price_block)

    if has_catalog:
        parts.append("""

# КАТАЛОГ
Прайс выше ты знаешь — название и цену называй сразу. Фото, характеристики или подбор по цене («что до 5000?») — find_products (вернёт id фото; отправь фото *_send_file тем же каналом). Товаров вне каталога не придумывай.""")

    if getattr(agent_config, "kb_namespace", None) or parts:
        kb_name = getattr(agent_config, "kb_name", None) or "база знаний компании"
        parts.append(f"""

# ПОИСК ПО ДОКУМЕНТАМ
Чего нет в знаниях выше и в каталоге — ищи search_knowledge_base («{kb_name}»): там документы и длинные материалы.""")

    return "".join(parts)


def build_webhook_block(agent_config) -> str:
    """
    Блок про вебхук. Добавляется только если у агента задан webhook_url.
    Показывает URL и подсказывает оркестратору вызывать send_webhook, когда нужно
    передать данные/событие во внешнюю систему.
    """
    url = (getattr(agent_config, "webhook_url", None) or "").strip()
    if not url:
        return ""
    return f"""

# ВЕБХУК (отправка событий во внешнюю систему)
У владельца настроен вебхук: {url}
Когда по итогу разговора или сообщения нужно передать данные во внешнюю систему
(заявка, бронирование, лид, важное событие или результат звонка) — вызывай
инструмент send_webhook. URL подставляется автоматически, передавать его не нужно:
укажи только event (код события) и payload (данные). Не вызывай вебхук без причины
и не дублируй одно и то же событие."""


def build_pipeline_stages_block(agent_config) -> str:
    """
    Блок промпта: актуальные стадии воронки этого агента (CRM v2).
    Включается всегда — если у агента кастомные стадии, модель про них знает;
    если дефолт — просто перечислены стандартные 5. Используется move_contact_stage.
    """
    try:
        from backend.core.pipeline_stages import get_agent_stages
        stages = get_agent_stages(agent_config)
    except Exception:
        return ""
    if not stages:
        return ""
    kind_label = {"open": "в работе", "won": "успешная сделка", "lost": "потеря/отказ"}
    lines = [f"- {s['key']} — {s['label']} ({kind_label.get(s.get('kind'), 'в работе')})" for s in stages]
    return (
        "\n\n## Стадии воронки продаж (актуальные для этого агента)\n"
        "При смене стадии через move_contact_stage используй ТОЛЬКО эти ключи:\n"
        + "\n".join(lines)
        + "\nКлиентов, которые ещё думают, оставляй в текущей стадии."
    )


def owner_instruction_block(agent_config) -> str:
    """Инструкция владельца: заполненные разделы «Кто мы…» + дополнительные инструкции."""
    parts = []
    for title, field in _OWNER_DOCS:
        text = (getattr(agent_config, field, None) or "").strip()
        if text:
            parts.append(f"## {title}\n{text}")
    extra = (getattr(agent_config, "additional_instructions", None) or "").strip()
    if extra:
        parts.append(extra)
    return "\n\n".join(parts) if parts else "(владелец пока не заполнил инструкцию)"


def company_contacts_block(agent_config) -> str:
    """
    Сайт и номер компании — одно поле в настройках агента (brand_contacts, «Контакты компании»).
    Его читают переписка, звонок и документы; в коде ни одного номера и сайта нет.
    """
    name = (getattr(agent_config, "brand_company_name", None) or "").strip()
    contacts = (getattr(agent_config, "brand_contacts", None) or "").strip()
    if not contacts:
        return ""
    head = f"{name}: " if name else ""
    return ("\n\n# КОНТАКТЫ КОМПАНИИ (можно давать клиенту: где посмотреть, куда позвонить)\n"
            f"{head}{contacts}")


def build_orchestrator_prompt(agent_config, include_time_block: bool = True, db=None,
                              surface: str = "owner") -> str:
    """
    Системный промпт оркестратора: ядро + блок поверхности + воронка + знания + вебхук + режим тарифа.

    surface: "client" — переписка с клиентом и разбор звонков (PostCall, PreCall);
             "owner"  — чат владельца с агентом (кабинет, Telegram, голос, публичный приём заявок).
    include_time_block=False — блок времени кладётся в user-сообщение (системный
    промпт остаётся статичным и целиком попадает в кэш провайдера).
    """
    base = ORCHESTRATOR_CORE.format(owner_block=owner_instruction_block(agent_config))
    base += CLIENT_BLOCK if surface == "client" else OWNER_BLOCK
    base += company_contacts_block(agent_config)
    prompt = (
        base
        + build_pipeline_stages_block(agent_config)
        + build_knowledge_base_block(agent_config, db)
        + build_webhook_block(agent_config)
        + _reactive_mode_block(agent_config, db)
    )
    if include_time_block:
        prompt += build_time_block()
    return prompt


def _reactive_mode_block(agent_config, db) -> str:
    """Тариф Старт: правила режима «только отвечает» (services/agent_mode.py)."""
    if db is None:
        return ""
    from backend.services import agent_mode
    if agent_mode.mode_for_agent(db, agent_config) != agent_mode.REACTIVE:
        return ""
    return agent_mode.PROMPT_BLOCK


def get_voice_agent_prompt() -> str:
    """Возвращает базовый промпт голосового агента (без подстановок)."""
    return VOICE_AGENT_PROMPT_BASE


def build_voice_agent_prompt(voice_additional_instructions: str = None) -> str:
    """
    Собирает system_prompt голосового агента: базовый промпт + (опционально)
    дополнительные инструкции от владельца по поведению в живом звонке.
    """
    prompt = VOICE_AGENT_PROMPT_BASE
    extra = (voice_additional_instructions or "").strip()
    if extra:
        prompt += (
            "\n\n# ДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ ОТ ВЛАДЕЛЬЦА\n"
            f"{extra}"
        )
    return prompt
