"""
Composio service — внешние коннекторы агента (Google Calendar, Gmail).

Две фазы:
  1) Подключение аккаунта (OAuth) — link() отдаёт redirect_url, Composio берёт на
     себя весь OAuth-танец и хранение токенов. Сырые токены к нам не приходят.
  2) Вызов инструментов — get_tools() отдаёт определения tools под user_id,
     execute() исполняет вызов под подключённым аккаунтом этого пользователя.

composio_user_id — per-agent identity (вариант A): f"agent_{agent_config_id}".
Каждый агент — отдельный «пользователь» Composio, подключения изолированы между
агентами одного владельца. И оркестратор (OpenRouter), и голосовой агент
(registry-функции) резолвят этот id из своего контекста (по агенту-владельцу), так
что внутри одного агента подключение шарится между чатом и голосом.

SDK Composio синхронный — все вызовы уводим в thread executor, чтобы не блокировать
event loop FastAPI. Клиент создаётся лениво (singleton).
"""

import time
import asyncio
from typing import Optional, Dict, Any, List

from backend.core.logging import get_logger
from backend.core.config import settings

logger = get_logger(__name__)


# Ключ коннектора (как в БД/UI) → slug toolkit в Composio.
TOOLKIT_SLUGS: Dict[str, str] = {
    "google_calendar": "GOOGLECALENDAR",
    "gmail": "GMAIL",
}

# Обратный маппинг slug → ключ коннектора.
SLUG_TO_TOOLKIT: Dict[str, str] = {v: k for k, v in TOOLKIT_SLUGS.items()}

# Статусы connected account в Composio, которые считаем «рабочими» (tools.execute
# реально сможет выполниться). Всё остальное (INITIATED/INITIALIZING/EXPIRED/
# FAILED/INACTIVE/unknown) — НЕ подключено, даже если у нас в БД стоит 'connected'.
ACTIVE_STATES = {"ACTIVE", "CONNECTED", "ENABLED"}


def _is_active_status(status) -> bool:
    return str(status or "").upper() in ACTIVE_STATES

# Функции, которые включаются у ГОЛОСОВОГО ассистента при подключении toolkit'а.
# Имена совпадают с registry-функциями в backend/functions/ (google_calendar.py,
# gmail.py). agent.py инжектит/убирает их из assistant.functions при connect/disconnect.
TOOLKIT_VOICE_FUNCTIONS: Dict[str, list] = {
    "google_calendar": [
        {"name": "google_calendar_create_event", "description": "Создать событие/встречу в Google Календаре владельца."},
        {"name": "google_calendar_find_events", "description": "Найти события в Google Календаре владельца."},
    ],
    "gmail": [
        {"name": "gmail_send_email", "description": "Отправить письмо с Gmail владельца."},
        {"name": "gmail_fetch_emails", "description": "Прочитать последние письма из Gmail владельца."},
    ],
}


def voice_function_names(toolkit: str) -> list:
    """Имена голосовых функций для toolkit'а."""
    return [f["name"] for f in TOOLKIT_VOICE_FUNCTIONS.get(toolkit, [])]


# Явный allowlist slug'ов Composio-тулзов для ЧАТ/Telegram-оркестратора.
# ВАЖНО: client.tools.get(toolkits=[...]) уходит в постраничный список Composio
# (tools.list) и без explicit `tools=[...]` тихо возвращает только первую страницу —
# на toolkit'ах с большим числом тулзов (GOOGLECALENDAR=48, GMAIL=63) это обрезает
# набор до алфавитно-первых ~20 slug'ов, из-за чего до модели не долетают, например,
# CREATE_EVENT/FIND_EVENT (GOOGLECALENDAR) без всякой ошибки в логах. Поэтому здесь —
# заранее выбранный конечный список slug'ов под конкретные бизнес-сценарии, который
# запрашивается через tools=[...] (не toolkits=[...]) и не подвержен пагинации.
TOOLKIT_CHAT_TOOLS: Dict[str, list] = {
    "google_calendar": [
        "GOOGLECALENDAR_CREATE_EVENT",
        "GOOGLECALENDAR_FIND_EVENT",
        "GOOGLECALENDAR_PATCH_EVENT",
        "GOOGLECALENDAR_DELETE_EVENT",
        "GOOGLECALENDAR_FIND_FREE_SLOTS",
        "GOOGLECALENDAR_GET_CURRENT_DATE_TIME",
    ],
    "gmail": [
        "GMAIL_SEND_EMAIL",
        "GMAIL_FETCH_EMAILS",
        "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
        "GMAIL_REPLY_TO_THREAD",
        "GMAIL_FORWARD_MESSAGE",
    ],
}


def chat_tool_slugs(toolkit: str) -> list:
    """Явный список slug'ов Composio-тулзов для чат-оркестратора по этому toolkit'у."""
    return list(TOOLKIT_CHAT_TOOLS.get(toolkit, []))


# Кэш определений tools: {(composio_user_id, slug_csv): (expires_at, tools)}.
# Composio.tools.get ходит по сети — не хотим дёргать его на каждый ход чата.
_TOOLS_CACHE: Dict[str, Any] = {}
_TOOLS_CACHE_TTL = 300  # секунд

# Версия тулкита для tools.execute. С Composio SDK >=0.9 manual execute требует
# версию: "latest" НЕ принимается. Варианты:
#   - пустое значение (по умолчанию) → dangerously_skip_version_check=True:
#     Composio берёт текущую версию тулза в рантайме (нам подходит — вывод читает
#     LLM, версии не пиним).
#   - конкретная дата-версия в env COMPOSIO_TOOLKIT_VERSION (например 20251027_00)
#     → передаём её как version=... для прод-стабильности.
import os as _os
TOOLKIT_VERSION = _os.getenv("COMPOSIO_TOOLKIT_VERSION", "").strip()

_client = None  # ленивый singleton Composio


def is_configured() -> bool:
    """True, если задан серверный API-ключ Composio."""
    return bool(settings.COMPOSIO_API_KEY)


def auth_config_for(toolkit: str) -> Optional[str]:
    """Auth Config ID Composio для toolkit (из настроек). None, если не задан."""
    mapping = {
        "google_calendar": settings.COMPOSIO_AUTH_CONFIG_GOOGLECALENDAR,
        "gmail": settings.COMPOSIO_AUTH_CONFIG_GMAIL,
    }
    return mapping.get(toolkit)


def toolkit_available(toolkit: str) -> bool:
    """True, если toolkit поддерживается и у него настроен auth_config."""
    return toolkit in TOOLKIT_SLUGS and bool(auth_config_for(toolkit))


def is_composio_tool(slug: str) -> bool:
    """
    True, если имя тулзы — это slug Composio (например GOOGLECALENDAR_CREATE_EVENT).
    Используется диспетчером оркестратора для маршрутизации исполнения.
    """
    if not slug:
        return False
    return any(slug.startswith(f"{s}_") for s in TOOLKIT_SLUGS.values())


def _get_client():
    """Ленивая инициализация клиента Composio. Бросает, если не настроен."""
    global _client
    if _client is not None:
        return _client
    if not is_configured():
        raise RuntimeError("Composio is not configured (COMPOSIO_API_KEY missing)")
    from composio import Composio  # импорт внутри — пакет опционален
    _client = Composio(api_key=settings.COMPOSIO_API_KEY)
    logger.info("[COMPOSIO] Client initialized")
    return _client


async def _run(fn, *args, **kwargs):
    """Выполнить синхронный вызов SDK в thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ============================================================================
# ФАЗА 1 — ПОДКЛЮЧЕНИЕ АККАУНТА
# ============================================================================

async def initiate_connection(
    composio_user_id: str,
    toolkit: str,
    callback_url: str,
) -> Dict[str, Any]:
    """
    Старт OAuth-флоу для toolkit. Возвращает {redirect_url, connection_id}.

    link() — актуальный (не устаревающий) путь для Composio-managed и custom
    OAuth. callback_url может нести наши query-параметры — Composio их сохранит и
    допишет свои (status, connected_account_id) при возврате.
    """
    auth_config_id = auth_config_for(toolkit)
    if not auth_config_id:
        raise RuntimeError(f"auth_config not set for toolkit '{toolkit}'")

    client = _get_client()

    def _do():
        accounts = client.connected_accounts
        # link() — актуальный путь. На старых SDK его может не быть — тогда
        # пробуем legacy initiate() (тот же возврат redirect_url).
        method = getattr(accounts, "link", None) or getattr(accounts, "initiate", None)
        if method is None:
            raise RuntimeError("composio SDK has neither connected_accounts.link nor initiate")
        # allow_multiple=False: identity per-agent, поэтому на пару
        # (agent_user_id, auth_config) нужен РОВНО ОДИН connected account.
        # Иначе connect→disconnect→reconnect плодит дубли, а tools.execute
        # выбирает из них недетерминированно → «аккаунт не подключён».
        return method(
            user_id=composio_user_id,
            auth_config_id=auth_config_id,
            callback_url=callback_url,
            allow_multiple=False,
        )

    try:
        req = await _run(_do)
    except Exception as e:
        # Подробный лог с типом исключения — чтобы видеть реальную причину
        # (сеть до Composio, неверный auth_config, версия SDK и т.п.).
        logger.error(
            f"[COMPOSIO] link() failed toolkit={toolkit} user={composio_user_id} "
            f"auth_config={auth_config_id}: {type(e).__name__}: {e}",
            exc_info=True,
        )
        raise
    redirect_url = (
        getattr(req, "redirect_url", None)
        or getattr(req, "redirectUrl", None)
    )
    connection_id = getattr(req, "id", None) or getattr(req, "connection_id", None)
    logger.info(f"[COMPOSIO] link() toolkit={toolkit} user={composio_user_id} conn={connection_id} redirect={'yes' if redirect_url else 'NO'}")
    return {"redirect_url": redirect_url, "connection_id": connection_id}


def _extract_list_items(resp) -> list:
    """Достать список аккаунтов из ответа connected_accounts.list (разные формы)."""
    if resp is None:
        return []
    if isinstance(resp, list):
        return resp
    for attr in ("items", "data", "results"):
        val = getattr(resp, attr, None)
        if val is None and isinstance(resp, dict):
            val = resp.get(attr)
        if isinstance(val, list):
            return val
    return []


async def find_active_connection(
    composio_user_id: str, toolkit: str, require_active: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Найти уже существующее подключение ДЛЯ ЭТОГО composio_user_id и toolkit.

    Вариант A: identity по агенту, поэтому переиспользуем подключение только если
    оно строго принадлежит запрошенному composio_user_id (иначе новый агент мог бы
    захватить коннект другого агента). Best-effort: ошибка/неизвестная форма → None.

    require_active=True — возвращаем только аккаунт в статусе ACTIVE (для reuse и
    резолва после OAuth, чтобы не привязать протухший/полу-отозванный аккаунт).
    """
    auth_config_id = auth_config_for(toolkit)
    if not auth_config_id or not is_configured():
        return None
    try:
        client = _get_client()

        def _do():
            accounts = client.connected_accounts
            # Пробуем наиболее вероятную сигнатуру list(); подстраховка ниже.
            try:
                return accounts.list(user_ids=[composio_user_id], auth_config_ids=[auth_config_id])
            except TypeError:
                return accounts.list(user_id=composio_user_id)

        resp = await _run(_do)
    except Exception as e:
        logger.warning(f"[COMPOSIO] find_active_connection list failed: {e}")
        return None

    items = _extract_list_items(resp)

    def _ac_id(it):
        return (getattr(it, "auth_config_id", None)
                or (it.get("auth_config_id") if isinstance(it, dict) else None)
                or (getattr(getattr(it, "auth_config", None), "id", None)))

    def _uid(it):
        return (getattr(it, "user_id", None)
                or (it.get("user_id") if isinstance(it, dict) else None)
                or getattr(it, "userId", None)
                or (it.get("userId") if isinstance(it, dict) else None))

    def _status(it):
        s = getattr(it, "status", None) or (it.get("status") if isinstance(it, dict) else None)
        return str(s or "").upper()

    def _id(it):
        return getattr(it, "id", None) or (it.get("id") if isinstance(it, dict) else None)

    # СТРОГО: только аккаунты, явно принадлежащие этому composio_user_id и
    # auth_config. Если list вернул шире (или поле user_id отсутствует) — не
    # переиспользуем, лучше пройти OAuth заново, чем привязать чужой коннект.
    def _matches(it):
        if _ac_id(it) not in (None, auth_config_id):
            return False
        uid = _uid(it)
        return uid is not None and str(uid) == str(composio_user_id)

    candidates = [it for it in items if _matches(it)]
    active = [it for it in candidates if _is_active_status(_status(it))]
    if require_active:
        pick = (active or [None])[0]
    else:
        pick = (active or candidates or [None])[0]
    if not pick:
        logger.info(f"[COMPOSIO] no reusable connection for user={composio_user_id} toolkit={toolkit} (items seen: {len(items)}, require_active={require_active})")
        return None
    cid = _id(pick)
    if not cid:
        return None
    logger.info(f"[COMPOSIO] reusing connection {cid} for user={composio_user_id} toolkit={toolkit}")
    return {"connected_account_id": cid, "email": _extract_email(pick), "status": _status(pick)}


async def connection_state(composio_user_id: str, toolkit: str) -> Dict[str, Any]:
    """
    Авторитетное состояние подключения по (user_id, toolkit) через LIST — надёжнее
    get() по id (который на удалённом аккаунте может просто бросить 404).

    Возвращает {ok, active_id, email, status}:
      • ok=False — Composio недоступен / список не получен. ВАЖНО: при ok=False
        НЕЛЬЗЯ делать вывод «не подключено» (иначе временный сбой отключит рабочий
        коннектор). Вызывающий должен в этом случае ничего не трогать.
      • ok=True, active_id=None — список получен, активного аккаунта нет → реально
        не подключено.
      • ok=True, active_id=<id> — есть активный аккаунт.
    """
    auth_config_id = auth_config_for(toolkit)
    if not is_configured():
        return {"ok": False, "active_id": None, "email": None, "status": None}
    try:
        client = _get_client()

        def _do():
            accounts = client.connected_accounts
            try:
                if auth_config_id:
                    return accounts.list(user_ids=[composio_user_id], auth_config_ids=[auth_config_id])
                return accounts.list(user_ids=[composio_user_id])
            except TypeError:
                return accounts.list(user_id=composio_user_id)

        resp = await _run(_do)
    except Exception as e:
        logger.warning(f"[COMPOSIO] connection_state list failed (user={composio_user_id}, {toolkit}): {e}")
        return {"ok": False, "active_id": None, "email": None, "status": None}

    items = _extract_list_items(resp)

    def _ac_id(it):
        return (getattr(it, "auth_config_id", None)
                or (it.get("auth_config_id") if isinstance(it, dict) else None)
                or getattr(getattr(it, "auth_config", None), "id", None))

    def _uid(it):
        return (getattr(it, "user_id", None)
                or (it.get("user_id") if isinstance(it, dict) else None)
                or getattr(it, "userId", None)
                or (it.get("userId") if isinstance(it, dict) else None))

    def _status(it):
        s = getattr(it, "status", None) or (it.get("status") if isinstance(it, dict) else None)
        return str(s or "").upper()

    def _id(it):
        return getattr(it, "id", None) or (it.get("id") if isinstance(it, dict) else None)

    def _matches(it):
        if _ac_id(it) not in (None, auth_config_id):
            return False
        uid = _uid(it)
        return uid is not None and str(uid) == str(composio_user_id)

    candidates = [it for it in items if _matches(it)]
    active = [it for it in candidates if _is_active_status(_status(it))]
    pick = (active or [None])[0]
    if not pick:
        return {"ok": True, "active_id": None, "email": None, "status": None}
    return {
        "ok": True,
        "active_id": _id(pick),
        "email": _extract_email(pick),
        "status": _status(pick),
    }


async def get_connection(connection_id: str) -> Dict[str, Any]:
    """
    Получить состояние подключённого аккаунта по id. Best-effort: ошибки не
    роняют вызов, возвращаем {status: 'unknown', active: False}.
    """
    try:
        client = _get_client()
        acc = await _run(client.connected_accounts.get, connection_id)
        status = getattr(acc, "status", None)
        return {
            "status": status,
            "active": _is_active_status(status),
            "connected_account_id": getattr(acc, "id", None) or connection_id,
            "email": _extract_email(acc),
        }
    except Exception as e:
        logger.warning(f"[COMPOSIO] get_connection({connection_id}) failed: {e}")
        return {"status": "unknown", "active": False, "connected_account_id": connection_id, "email": None}


# Кэш верификации статуса аккаунта: {connected_account_id: (expires_at, info)}.
# Нужен, чтобы реконсиляция в GET /connectors не ходила в Composio на каждый рендер.
_VERIFY_CACHE: Dict[str, Any] = {}
_VERIFY_CACHE_TTL = 120  # секунд


async def verify_connection(connected_account_id: str, use_cache: bool = True) -> Dict[str, Any]:
    """
    Проверить, что connected account реально ACTIVE в Composio.
    Возвращает get_connection() + поле active. Кэшируется на _VERIFY_CACHE_TTL.
    """
    if not connected_account_id:
        return {"status": "unknown", "active": False, "connected_account_id": None, "email": None}
    if use_cache:
        cached = _VERIFY_CACHE.get(connected_account_id)
        if cached and cached[0] > time.time():
            return cached[1]
    info = await get_connection(connected_account_id)
    # Кэшируем только осмысленный результат (не сетевую ошибку 'unknown').
    if info.get("status") not in (None, "unknown"):
        _VERIFY_CACHE[connected_account_id] = (time.time() + _VERIFY_CACHE_TTL, info)
    return info


def _invalidate_verify_cache(connected_account_id: Optional[str]) -> None:
    if connected_account_id:
        _VERIFY_CACHE.pop(connected_account_id, None)


async def wait_for_active(
    composio_user_id: str,
    toolkit: str,
    connected_account_id: Optional[str] = None,
    attempts: int = 5,
    delay: float = 0.6,
) -> Optional[Dict[str, Any]]:
    """
    Дождаться, пока подключение станет ACTIVE (учитываем eventual consistency
    Composio сразу после OAuth-возврата). Возвращает {connected_account_id, email,
    status} активного аккаунта или None.

    На каждой попытке: если знаем connected_account_id — проверяем его напрямую;
    иначе (или если он ещё не активен) ищем самый свежий ACTIVE-аккаунт по user_id.
    """
    for i in range(attempts):
        if connected_account_id:
            info = await verify_connection(connected_account_id, use_cache=False)
            if info.get("active"):
                return {
                    "connected_account_id": connected_account_id,
                    "email": info.get("email"),
                    "status": info.get("status"),
                }
        found = await find_active_connection(composio_user_id, toolkit, require_active=True)
        if found and found.get("connected_account_id"):
            return found
        if i < attempts - 1:
            await asyncio.sleep(delay)
    return None


async def delete_all_connections(composio_user_id: str, toolkit: str) -> int:
    """
    Удалить ВСЕ connected accounts этого (composio_user_id, toolkit) в Composio.
    Нужно при disconnect и при чистке дублей/висяков. Возвращает число удалённых.
    Best-effort: ошибки логируются, не бросаются.
    """
    if not is_configured():
        return 0
    auth_config_id = auth_config_for(toolkit)
    try:
        client = _get_client()

        def _list():
            accounts = client.connected_accounts
            try:
                if auth_config_id:
                    return accounts.list(user_ids=[composio_user_id], auth_config_ids=[auth_config_id])
                return accounts.list(user_ids=[composio_user_id])
            except TypeError:
                return accounts.list(user_id=composio_user_id)

        resp = await _run(_list)
    except Exception as e:
        logger.warning(f"[COMPOSIO] delete_all_connections list failed: {e}")
        return 0

    items = _extract_list_items(resp)

    def _ac_id(it):
        return (getattr(it, "auth_config_id", None)
                or (it.get("auth_config_id") if isinstance(it, dict) else None)
                or getattr(getattr(it, "auth_config", None), "id", None))

    def _id(it):
        return getattr(it, "id", None) or (it.get("id") if isinstance(it, dict) else None)

    ids = []
    for it in items:
        # Если поле auth_config есть — фильтруем по нему; если его нет, не рискуем
        # (list уже сужен по user_id, а user_id у нас per-agent).
        if auth_config_id and _ac_id(it) not in (None, auth_config_id):
            continue
        cid = _id(it)
        if cid:
            ids.append(cid)

    deleted = 0
    for cid in ids:
        if await delete_connection(cid):
            deleted += 1
    if ids:
        logger.info(f"[COMPOSIO] delete_all_connections user={composio_user_id} toolkit={toolkit} removed={deleted}/{len(ids)}")
    return deleted


async def delete_connection(connected_account_id: str) -> bool:
    """
    Удалить connected account в Composio (при отключении коннектора). Best-effort:
    ошибки не роняют отключение — локальная строка всё равно удаляется.
    """
    if not connected_account_id or not is_configured():
        return False
    try:
        client = _get_client()
        method = getattr(client.connected_accounts, "delete", None)
        if method is None:
            logger.warning("[COMPOSIO] connected_accounts.delete not available in SDK")
            return False
        await _run(method, connected_account_id)
        _invalidate_verify_cache(connected_account_id)
        logger.info(f"[COMPOSIO] deleted connected account {connected_account_id}")
        return True
    except Exception as e:
        logger.warning(f"[COMPOSIO] delete_connection({connected_account_id}) failed: {e}")
        return False


def _extract_email(acc) -> Optional[str]:
    """Достать email/идентификатор аккаунта из ответа Composio (best-effort)."""
    for attr in ("email", "user_email"):
        val = getattr(acc, attr, None)
        if val:
            return val
    data = getattr(acc, "data", None) or getattr(acc, "metadata", None)
    if isinstance(data, dict):
        for key in ("email", "user_email", "login"):
            if data.get(key):
                return data[key]
    return None


# ============================================================================
# ФАЗА 2 — ИНСТРУМЕНТЫ
# ============================================================================

async def get_tools(composio_user_id: str, tool_slugs: List[str]) -> List[Dict[str, Any]]:
    """
    Определения tools для пользователя по ЯВНОМУ списку slug'ов тулзов (не toolkit'ов).

    Намеренно НЕ принимает toolkit slug (например "GOOGLECALENDAR") — вызов
    client.tools.get(toolkits=[...]) уходит в постраничный tools.list на стороне
    Composio и без явного tools=[...] тихо возвращает только первую страницу,
    обрезая набор на toolkit'ах с большим числом тулзов. Вызывающий должен сам
    прислать конкретные slug'и (см. TOOLKIT_CHAT_TOOLS / chat_tool_slugs()).

    Возвращает формат Chat Completions ([{"type":"function","function":{...}}]),
    который совпадает с тем, что ждёт оркестратор на OpenRouter. Кэшируется на
    _TOOLS_CACHE_TTL секунд. При любой ошибке возвращает [] (best-effort — не
    ломаем чат, если Composio недоступен).
    """
    if not tool_slugs or not is_configured():
        return []

    cache_key = f"{composio_user_id}|{','.join(sorted(tool_slugs))}"
    cached = _TOOLS_CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        client = _get_client()
        tools = await _run(
            client.tools.get,
            user_id=composio_user_id,
            tools=list(tool_slugs),
        )
        tools = _normalize_tools(tools)
        _TOOLS_CACHE[cache_key] = (time.time() + _TOOLS_CACHE_TTL, tools)
        return tools
    except Exception as e:
        logger.error(f"[COMPOSIO] get_tools failed (user={composio_user_id}, {tool_slugs}): {e}")
        return []


def _normalize_tools(tools) -> List[Dict[str, Any]]:
    """
    Привести вывод SDK к списку dict в формате Chat Completions.
    Default-провайдер Composio уже отдаёт нужный формат, но подстрахуемся.
    """
    result = []
    for t in (tools or []):
        if isinstance(t, dict):
            result.append(t)
        elif hasattr(t, "model_dump"):
            result.append(t.model_dump())
        elif hasattr(t, "to_dict"):
            result.append(t.to_dict())
    return result


async def execute(slug: str, arguments: Dict[str, Any], composio_user_id: str) -> Dict[str, Any]:
    """
    Исполнить инструмент Composio под аккаунтом пользователя.

    Возвращает плоский dict {ok, data?, error?} — удобный и для оркестратора, и
    для голосового агента.
    """
    if not is_configured():
        return {"ok": False, "error": "composio_not_configured"}
    try:
        client = _get_client()

        def _do():
            exec_kwargs = {"arguments": arguments or {}, "user_id": composio_user_id}
            if TOOLKIT_VERSION:
                # Зафиксированная дата-версия (прод-стабильность).
                exec_kwargs["version"] = TOOLKIT_VERSION
            else:
                # Без пина — пропускаем проверку версии, Composio возьмёт текущую.
                exec_kwargs["dangerously_skip_version_check"] = True
            return client.tools.execute(slug, **exec_kwargs)

        resp = await _run(_do)
        norm = _normalize_execution(resp)
        if not norm.get("ok"):
            # Частый кейс: «аккаунт не подключён» приходит БЕЗ исключения — Composio
            # отдаёт successful=false. Логируем тело, чтобы это было видно в проде.
            logger.warning(
                f"[COMPOSIO] execute({slug}) user={composio_user_id} not ok: "
                f"{str(norm.get('error'))[:300]}"
            )
        return norm
    except Exception as e:
        logger.error(f"[COMPOSIO] execute({slug}) user={composio_user_id} failed: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


def _normalize_execution(resp) -> Dict[str, Any]:
    """Привести ToolExecutionResponse к {ok, data, error}."""
    if isinstance(resp, dict):
        successful = resp.get("successful", resp.get("success"))
        return {
            "ok": bool(successful) if successful is not None else True,
            "data": resp.get("data"),
            "error": resp.get("error"),
        }
    successful = getattr(resp, "successful", None)
    if successful is None:
        successful = getattr(resp, "success", None)
    return {
        "ok": bool(successful) if successful is not None else True,
        "data": getattr(resp, "data", None),
        "error": getattr(resp, "error", None),
    }


# ============================================================================
# ГОЛОСОВОЙ АГЕНТ — динамический резолв коннекторов по голосовому ассистенту
# ============================================================================
# Голос не полагается на разовый снимок функций в конфиге: при старте сессии
# смотрим, какие коннекторы у владеющего агента в статусе connected, и
# домешиваем их голосовые функции в список — аналогично тому, как оркестратор
# берёт инструменты через tools.get на каждый запрос.

def composio_user_id_for_agent(agent_config_id) -> str:
    """
    Идентичность агента в Composio (вариант A). Каждый агент — отдельный
    «пользователь» Composio, поэтому подключения изолированы между агентами
    одного владельца. Единая точка правды для connect/get_tools/execute/voice.
    """
    return f"agent_{agent_config_id}"


def _resolve_owner_agent(db, assistant_config):
    """AgentConfig, владеющий данным голосовым ассистентом (любого провайдера), или None."""
    aid = getattr(assistant_config, "id", None)
    if not aid:
        return None
    from sqlalchemy import or_ as _or
    from backend.models.agent_config import AgentConfig
    return db.query(AgentConfig).filter(_or(
        AgentConfig.gemini_assistant_id == aid,
        AgentConfig.openai_assistant_id == aid,
        AgentConfig.cartesia_assistant_id == aid,
        AgentConfig.yandex_assistant_id == aid,
        AgentConfig.cascade_assistant_id == aid,
        AgentConfig.fish_assistant_id == aid,
    )).first()


def composio_user_id_for_assistant(db, assistant_config) -> Optional[str]:
    """Агентная identity Composio по голосовому ассистенту (или None, если агент не найден)."""
    try:
        agent = _resolve_owner_agent(db, assistant_config)
    except Exception as e:
        logger.warning(f"[COMPOSIO] composio_user_id_for_assistant failed: {e}")
        return None
    return composio_user_id_for_agent(agent.id) if agent else None


def connected_toolkits_for_assistant(db, assistant_config) -> list:
    """
    Ключи toolkit'ов (google_calendar/gmail), подключённых к АГЕНТУ, которому
    принадлежит данный голосовой ассистент (gemini/openai/cartesia).
    Best-effort: при любой ошибке возвращает [].
    """
    if assistant_config is None or not is_configured():
        return []
    try:
        from backend.models.agent_connector import AgentConnector

        agent = _resolve_owner_agent(db, assistant_config)
        if not agent:
            return []
        rows = db.query(AgentConnector).filter(
            AgentConnector.agent_config_id == agent.id,
            AgentConnector.status == "connected",
        ).all()
        return [r.toolkit for r in rows if r.toolkit in TOOLKIT_VOICE_FUNCTIONS]
    except Exception as e:
        logger.warning(f"[COMPOSIO] connected_toolkits_for_assistant failed: {e}")
        return []


def merge_voice_connector_functions(db, assistant_config, functions):
    """
    Домешать в `functions` голосового ассистента функции подключённых коннекторов.

    Сохраняет форму входа: dict {"enabled_functions":[...]} → dict; list/None → list.
    Дубли по имени убираются. Если коннекторов нет — возвращает functions как есть.
    """
    toolkits = connected_toolkits_for_assistant(db, assistant_config)
    if not toolkits:
        return functions

    extra = []
    for tk in toolkits:
        extra.extend(TOOLKIT_VOICE_FUNCTIONS.get(tk, []))
    if not extra:
        return functions
    extra_names = {f["name"] for f in extra}

    # dict-форма {"enabled_functions": [...имена...]}
    if isinstance(functions, dict) and "enabled_functions" in functions:
        names = [n for n in functions.get("enabled_functions", []) if n not in extra_names]
        names.extend(sorted(extra_names))
        return {"enabled_functions": names}

    # list-форма [{"name","description"}, ...] (или None)
    items = list(functions) if isinstance(functions, list) else []
    items = [f for f in items if (f.get("name") if isinstance(f, dict) else f) not in extra_names]
    items.extend(extra)
    return items


def connector_voice_prompt_note(db, assistant_config) -> str:
    """
    Короткая подсказка для system-промпта голоса о подключённых сервисах, чтобы
    модель знала, что может ими пользоваться. Пусто, если коннекторов нет.
    """
    toolkits = connected_toolkits_for_assistant(db, assistant_config)
    if not toolkits:
        return ""
    labels = {
        "google_calendar": "Google Календарь — можешь создавать события/встречи и проверять занятость",
        "gmail": "Gmail — можешь отправлять письма клиенту",
    }
    lines = [f"- {labels.get(tk, tk)}" for tk in toolkits]
    return (
        "\n\nПОДКЛЮЧЁННЫЕ СЕРВИСЫ (вызывай соответствующие функции, когда это уместно по ходу разговора):\n"
        + "\n".join(lines)
    )


def ensure_connector_functions_persisted(db, assistant_config) -> bool:
    """
    Гарантирует, что голосовые функции подключённых коннекторов присутствуют в
    assistant_config.functions, и ПЕРСИСТИТ изменение (self-heal).

    Нужно для телефонии: функции голосу отдаёт сценарий Voximplant из
    /api/telephony/config (по индексу function_id), а исполняет другой эндпоинт,
    читающий тот же assistant.functions. Поэтому список должен жить в самом поле —
    иначе индексы config↔execute разъедутся. Порядок добавления детерминирован
    (toolkits отсортированы), существующие функции не трогаем — только дописываем.

    Возвращает True, если поле изменилось (вызывающий должен сделать commit).
    """
    from sqlalchemy.orm.attributes import flag_modified

    toolkits = sorted(connected_toolkits_for_assistant(db, assistant_config))
    extra = []
    for tk in toolkits:
        extra.extend(TOOLKIT_VOICE_FUNCTIONS.get(tk, []))
    if not extra:
        return False

    funcs = getattr(assistant_config, "functions", None)

    # dict-форма {"enabled_functions": [...имена...]}
    if isinstance(funcs, dict) and "enabled_functions" in funcs:
        current = list(funcs.get("enabled_functions", []))
        missing = [f["name"] for f in extra if f["name"] not in current]
        if not missing:
            return False
        funcs = {"enabled_functions": current + missing}
        assistant_config.functions = funcs
        flag_modified(assistant_config, "functions")
        return True

    # list-форма [{"name","description"}, ...] (или None/прочее → список)
    items = list(funcs) if isinstance(funcs, list) else []
    existing_names = {(f.get("name") if isinstance(f, dict) else f) for f in items}
    missing = [f for f in extra if f["name"] not in existing_names]
    if not missing:
        return False
    items = items + missing
    assistant_config.functions = items
    flag_modified(assistant_config, "functions")
    return True


