"""
Безопасные исходящие запросы на адреса, которые вписывает пользователь (вебхук агента).

Зачем (07.11.2026): адрес вебхука задаёт владелец агента (кабинет или API-ключ),
а запрос шлёт НАШ сервер. Без проверки сервер можно было направить во внутреннюю
сеть хостинга (localhost, 10.x, 192.168.x, 169.254.169.254 …), а начало ответа
модель видит в результате функции — классическая SSRF-дыра.

Правила:
  * только http:// и https://;
  * хост — публичный адрес интернета: внутренние имена (localhost, *.local,
    *.internal …) и внутренние IP запрещены;
  * DNS проверяется на КАЖДОМ соединении (резолвер aiohttp), а не только при
    сохранении — иначе домен можно перенаправить на внутренний IP позже;
  * редиректы идут вручную, каждый новый адрес проходит те же проверки.

Обычные вебхуки (n8n, Make, Zapier, свой сервер в интернете) это не задевает.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Optional, Tuple
from urllib.parse import urljoin, urlsplit

ALLOWED_SCHEMES = ("http", "https")
BLOCKED_HOSTS = {"localhost", "metadata", "metadata.google.internal"}
BLOCKED_HOST_SUFFIXES = (
    ".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet", ".private",
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 3


class BlockedAddressError(OSError):
    """Адрес ведёт во внутреннюю сеть — соединение запрещено."""


def is_public_ip(value: Any) -> bool:
    """True, если IP — обычный публичный адрес интернета."""
    try:
        ip = ipaddress.ip_address(str(value).split("%", 1)[0].strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4, спрятанный внутри IPv6, проверяем как IPv4.
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip.teredo is not None:
            ip = ip.teredo[1]
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.is_multicast or ip.is_unspecified:
        return False
    return bool(ip.is_global)


def normalize_webhook_url(raw: Any) -> str:
    """Убрать пробелы; без схемы («hook.make.com/…») — считать https://."""
    url = str(raw or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


def webhook_url_problem(url: str) -> Optional[str]:
    """None — адрес допустим; иначе понятный текст, почему нельзя."""
    if not url:
        return "Адрес вебхука пустой"
    try:
        parts = urlsplit(url)
        port = parts.port  # кидает ValueError на кривом порту
    except ValueError:
        return "Адрес вебхука записан с ошибкой"
    if (parts.scheme or "").lower() not in ALLOWED_SCHEMES:
        return "Адрес вебхука должен начинаться с https:// или http://"
    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host:
        return "В адресе вебхука нет домена"
    if port is not None and not (0 < port < 65536):
        return "В адресе вебхука неверный порт"
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_HOST_SUFFIXES):
        return "Вебхук не может вести на внутренний адрес сервера — укажите адрес в интернете"
    try:
        ipaddress.ip_address(host)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if is_ip_literal and not is_public_ip(host):
        return "Вебхук не может вести на внутренний адрес сервера — укажите адрес в интернете"
    if not is_ip_literal and "." not in host:
        # Однословное имя («db», «redis») — это имя внутри сети хостинга.
        return "Вебхук не может вести на внутренний адрес сервера — укажите адрес в интернете"
    return None


def _public_only_resolver():
    """Резолвер aiohttp, который отказывает, если домен ведёт на внутренний IP."""
    from aiohttp.abc import AbstractResolver
    from aiohttp.resolver import DefaultResolver

    class PublicOnlyResolver(AbstractResolver):
        def __init__(self) -> None:
            self._inner = DefaultResolver()

        async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
            infos = await self._inner.resolve(host, port, family)
            if not infos or any(not is_public_ip(info.get("host")) for info in infos):
                raise BlockedAddressError(f"{host} ведёт на внутренний адрес")
            return infos

        async def close(self) -> None:
            await self._inner.close()

    return PublicOnlyResolver()


async def post_json_to_public_url(url: str, data: Any, timeout_seconds: float = 10.0) -> Tuple[int, str]:
    """
    POST JSON на публичный адрес. Возвращает (status, текст ответа).
    Кидает BlockedAddressError, если адрес (или адрес редиректа) ведёт во внутреннюю сеть.
    """
    import aiohttp

    current = url
    last_status = 0
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    connector = aiohttp.TCPConnector(resolver=_public_only_resolver())
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            for _ in range(MAX_REDIRECTS + 1):
                problem = webhook_url_problem(current)
                if problem:
                    raise BlockedAddressError(problem)
                async with session.post(current, json=data, allow_redirects=False) as response:
                    last_status = response.status
                    location = response.headers.get("Location")
                    if response.status in REDIRECT_STATUSES and location:
                        current = urljoin(current, location)
                        continue
                    return response.status, await response.text()
            return last_status, "too_many_redirects"
    except aiohttp.ClientConnectorError as e:
        if isinstance(getattr(e, "os_error", None), BlockedAddressError):
            raise e.os_error
        raise
