# backend/services/finik_service.py

"""
Finik (finik.kg) QR-acquiring integration for Voksy AI.

Валюта — только KGS (кыргызский сом).

Каждый запрос к API Finik подписывается RSA-SHA256 (PKCS#1 v1.5, Base64)
приватным ключом мерчанта. Формат канонической строки воспроизводит
официальный Node-пакет @mancho.devs/authorizer (Signer.getData()):

    <http-method в нижнем регистре>\n
    <path без query, URL-decoded>\n
    host:<Host>&<x-api-* заголовки, отсортированные, key:value через &>\n
    [<query-параметры, отсортированные, key=value через &>\n]   # только если есть
    <тело: JSON.stringify с сортировкой ключей ВЕРХНЕГО уровня, или пустая строка>

Создание платежа: POST {FINIK_API_URL}/v1/payment. Успешный ответ — 302 Found,
URL платёжной страницы в заголовке Location (авто-follow редиректов ОТКЛЮЧЁН).

Входящий webhook подписан тем же способом публичным ключом Finik
(заголовок `signature`), проверяется verify_webhook_signature().
"""

import base64
import json
import time
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote, unquote, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from backend.core.config import settings
from backend.core.logging import get_logger

logger = get_logger(__name__)

# Символы, которые JS encodeURI() НЕ экранирует (unreserved + reserved)
_ENCODE_URI_SAFE = ";,/?:@&=+$-_.!~*'()#"


class FinikError(Exception):
    """Ошибка взаимодействия с API Finik."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _canonical_json_body(body: Optional[Dict[str, Any]]) -> str:
    """
    Аналог Signer.getJsonBody(): JSON.stringify c сортировкой ключей
    ТОЛЬКО верхнего уровня (вложенные объекты сохраняют порядок ключей).
    """
    if not body:
        return ""
    sorted_body = dict(sorted(body.items(), key=lambda kv: kv[0]))
    return json.dumps(sorted_body, separators=(",", ":"), ensure_ascii=False)


def build_canonical_string(
    http_method: str,
    path: str,
    headers: Mapping[str, Any],
    query_params: Optional[Mapping[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Каноническая строка для подписи/проверки — точная копия алгоритма
    @mancho.devs/authorizer. `headers` должны содержать Host (в любом регистре)
    и все x-api-* заголовки запроса.
    """
    # 1. Метод в нижнем регистре
    parts = [http_method.lower()]

    # 2. Путь (URL-decoded, без query)
    parts.append(unquote(path or ""))

    # 3. Заголовки: host + отсортированные x-api-*
    host = None
    for key, value in headers.items():
        if key.lower() == "host":
            host = str(value)
            break
    if not host:
        raise FinikError("Header 'Host' is required for signature")

    header_items = ["host:" + host]
    x_api_keys = sorted(k for k in headers.keys() if k.lower().startswith("x-api-"))
    for key in x_api_keys:
        value = headers[key]
        if value is None:
            raise FinikError(f"Header '{key}' contains invalid value")
        header_items.append(key.lower() + ":" + str(value))
    parts.append("&".join(header_items))

    # 4. Query-параметры (только если есть): sorted, encodeURI(decodeURI(v))
    if query_params:
        query_items = []
        for key in sorted(query_params.keys()):
            value = query_params.get(key)
            value = "" if value is None else str(value)
            encoded_key = quote(unquote(str(key)), safe=_ENCODE_URI_SAFE)
            encoded_value = quote(unquote(value), safe=_ENCODE_URI_SAFE)
            query_items.append(encoded_key + "=" + encoded_value)
        parts.append("&".join(query_items))

    # 5. Тело
    parts.append(_canonical_json_body(body))

    return "\n".join(parts)


def sign_canonical_string(canonical: str, private_pem: str) -> str:
    """RSA-SHA256 (PKCS#1 v1.5) подпись канонической строки, Base64."""
    private_key = serialization.load_pem_private_key(
        private_pem.encode("utf-8"), password=None
    )
    signature = private_key.sign(
        canonical.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def verify_canonical_string(canonical: str, signature_b64: str, public_pem: str) -> bool:
    """Проверка Base64 RSA-SHA256 подписи канонической строки публичным ключом."""
    try:
        public_key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
        public_key.verify(
            base64.b64decode(signature_b64),
            canonical.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        return False
    except Exception as e:
        logger.error(f"❌ [FINIK] Signature verification error: {e}")
        return False


class FinikService:
    """Клиент API Finik: создание платежа и проверка webhook-подписи."""

    CARD_TYPE = "FINIK_QR"
    CURRENCY = "KGS"

    # Пути на нашей стороне
    WEBHOOK_PATH = "/api/payments/finik-webhook"
    SUCCESS_PATH = "/api/payments/success"

    @classmethod
    def is_configured(cls) -> bool:
        return bool(
            settings.FINIK_API_KEY
            and settings.FINIK_PRIVATE_PEM
            and settings.FINIK_ACCOUNT_ID
            and settings.FINIK_API_URL
        )

    @classmethod
    def webhook_url(cls) -> str:
        return f"{settings.HOST_URL.rstrip('/')}{cls.WEBHOOK_PATH}"

    @classmethod
    def redirect_url(cls) -> str:
        return f"{settings.HOST_URL.rstrip('/')}{cls.SUCCESS_PATH}"

    @classmethod
    async def create_payment(
        cls,
        amount: float,
        payment_id: str,
        description: str,
        name_en: str = "Voksy AI",
        lang: str = "ru",
    ) -> str:
        """
        Создать платёж в Finik. Возвращает URL платёжной страницы (из
        заголовка Location ответа 302). Любая ошибка → FinikError.

        amount — сумма в сомах (KGS). payment_id — наш уникальный UUID,
        по нему потом сопоставляем webhook (fields.paymentId).
        """
        if not cls.is_configured():
            raise FinikError(
                "Finik is not configured: check FINIK_API_KEY / FINIK_PRIVATE_PEM / FINIK_ACCOUNT_ID"
            )

        api_url = settings.FINIK_API_URL.rstrip("/")
        host = urlsplit(api_url).netloc
        path = "/v1/payment"

        # Amount: целые сомы отправляем как int (каноничный JSON без ".0")
        amount_value: Any = int(amount) if float(amount).is_integer() else round(float(amount), 2)

        body: Dict[str, Any] = {
            "Amount": amount_value,
            "CardType": cls.CARD_TYPE,
            "PaymentId": payment_id,
            "RedirectUrl": cls.redirect_url(),
            "Data": {
                "accountId": settings.FINIK_ACCOUNT_ID,
                "name_en": name_en,
                "webhookUrl": cls.webhook_url(),
                "description": description,
                "Lang": lang,
            },
        }

        # Один и тот же timestamp — и в подписи, и в заголовке
        timestamp = str(int(time.time() * 1000))
        sign_headers = {
            "Host": host,
            "x-api-key": settings.FINIK_API_KEY,
            "x-api-timestamp": timestamp,
        }
        canonical = build_canonical_string("POST", path, sign_headers, None, body)
        signature = sign_canonical_string(canonical, settings.FINIK_PRIVATE_PEM)

        request_headers = {
            "Content-Type": "application/json",
            "x-api-key": settings.FINIK_API_KEY,
            "x-api-timestamp": timestamp,
            "signature": signature,
        }

        # Тело на проводе = канонизированный JSON (тот же, что подписан)
        raw_body = _canonical_json_body(body)

        logger.info(f"💳 [FINIK] Creating payment {payment_id}: amount={amount_value} KGS")
        logger.info(f"   [FINIK] POST {api_url}{path}, webhook={cls.webhook_url()}")

        try:
            # ⚠️ КРИТИЧНО: авто-follow редиректов отключён — успех приходит
            # как 302 Found с URL платёжной страницы в Location.
            async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
                response = await client.post(
                    f"{api_url}{path}",
                    content=raw_body.encode("utf-8"),
                    headers=request_headers,
                )
        except httpx.HTTPError as e:
            logger.error(f"❌ [FINIK] HTTP error creating payment {payment_id}: {e}")
            raise FinikError(f"Finik request failed: {e}")

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location") or response.headers.get("location")
            if not location:
                logger.error(f"❌ [FINIK] 302 without Location for payment {payment_id}")
                raise FinikError("Finik returned redirect without Location header", 302)
            logger.info(f"✅ [FINIK] Payment {payment_id} created, payment_url={location}")
            return location

        # Не 302 — парсим JSON-ошибку {"StatusCode": ..., "ErrorMessage": ...}
        error_message = f"Unexpected Finik response: HTTP {response.status_code}"
        try:
            payload = response.json()
            error_message = (
                f"Finik error {payload.get('StatusCode', response.status_code)}: "
                f"{payload.get('ErrorMessage', response.text[:300])}"
            )
        except Exception:
            if response.text:
                error_message += f" — {response.text[:300]}"

        logger.error(f"❌ [FINIK] Payment {payment_id} failed: {error_message}")
        raise FinikError(error_message, response.status_code)

    @classmethod
    def verify_webhook_signature(
        cls,
        http_method: str,
        path: str,
        headers: Mapping[str, str],
        query_params: Mapping[str, Any],
        body: Dict[str, Any],
        signature_b64: Optional[str],
    ) -> bool:
        """
        Проверка подписи входящего webhook'а публичным ключом Finik.

        Возвращает True, если подпись валидна ЛИБО проверка осознанно
        отключена (флаг FINIK_VERIFY_WEBHOOK_SIGNATURE=False или публичный
        ключ ещё не выдан — тогда пишем предупреждение в лог).
        """
        if not settings.FINIK_VERIFY_WEBHOOK_SIGNATURE:
            logger.warning("⚠️ [FINIK] Webhook signature verification is DISABLED by flag")
            return True

        if not settings.FINIK_PUBLIC_KEY:
            logger.warning(
                "⚠️ [FINIK] FINIK_PUBLIC_KEY is not set — webhook signature NOT verified. "
                "Запросите публичный ключ у Finik и добавьте его в env."
            )
            return True

        if not signature_b64:
            logger.error("❌ [FINIK] Webhook has no 'signature' header")
            return False

        try:
            canonical = build_canonical_string(
                http_method, path, headers, query_params or None, body
            )
        except FinikError as e:
            logger.error(f"❌ [FINIK] Cannot build canonical string for webhook: {e}")
            return False

        is_valid = verify_canonical_string(canonical, signature_b64, settings.FINIK_PUBLIC_KEY)
        if not is_valid:
            logger.error("❌ [FINIK] Invalid webhook signature")
        return is_valid


def is_success_status(status_value: Optional[str]) -> bool:
    """Статус из webhook — регистронезависимо, по вхождению success/succeeded."""
    if not status_value:
        return False
    normalized = str(status_value).lower()
    return "success" in normalized or "succeed" in normalized
