"""
Сервис для работы с Voximplant Partner API.

Реализует создание и управление дочерними аккаунтами:
- Создание/клонирование аккаунтов
- Создание SubUsers для верификации и биллинга
- Получение ссылок на верификацию и биллинг
- Работа с номерами телефонов
- Проверка статуса верификации
- Управление сценариями и правилами маршрутизации
- Запуск исходящих звонков
- Service Account для JWT авторизации (secure records)

Документация Voximplant API:
https://voximplant.com/docs/references/httpapi/accounts

✅ v1.0: Базовый функционал Partner API
✅ v1.3: Исправлены параметры API (parent_account_id, parent_account_api_key, account_password)
✅ v1.4: Исправлен check_verification_status - теперь использует GetAccountDocuments
✅ v1.5: Комбинированная проверка верификации (по номерам + GetAccountDocuments)
✅ v1.6: Добавлен set_account_callback для webhook-уведомлений
✅ v1.7: Добавлено управление сценариями (get_scenarios, add_scenario, add_application, add_rule)
✅ v1.8: ИСПРАВЛЕНО копирование сценариев - запрос кода для каждого сценария отдельно
         (Voximplant API quirk: with_script=true не возвращает код при массовом запросе)
✅ v2.1: Добавлен fallback для получения phone_id через GetPhoneNumbers если AttachPhoneNumber не вернул ID
✅ v2.2: Добавлен метод update_scenario для массового обновления сценариев на дочерних аккаунтах
✅ v3.0: OUTBOUND CALLS - добавлена поддержка исходящих звонков:
         - setup_child_account_scenarios() теперь создаёт Rules для outbound
         - Новый метод start_outbound_call() для запуска исходящих звонков
         - Новый метод setup_outbound_rules() для создания Rules на существующих аккаунтах
✅ v3.1: CRM INTEGRATION - расширена передача контекста в start_outbound_call():
         - contact_name, task_title, task_description, custom_greeting, timezone
         - Полная поддержка Task-based звонков из CRM
✅ v3.2: SERVICE ACCOUNT - добавлена поддержка JWT авторизации для secure записей:
         - create_key() - создание Service Account через CreateKey API
         - get_keys() - получение списка Service Accounts
         - delete_key() - удаление Service Account
         - setup_service_account() - комплексная настройка с сохранением credentials
✅ v3.3: BUGFIX - исправлен NameError: true → True в add_sip_registration
✅ v3.3: BUGFIX - URL-энкодинг subuser_login/subuser_password в get_billing_url и get_verification_url
"""

import httpx
import json
import secrets
import string
from typing import Optional, Dict, Any, List, Union
from urllib.parse import quote  # ✅ v3.3: для URL-энкодинга паролей

from backend.core.logging import get_logger
from backend.core.config import settings

logger = get_logger(__name__)

# =============================================================================
# SIP PROVIDER CONSTANTS
# =============================================================================

SIP_PROVIDER_IPS = {
    "novofon": ["185.189.255.0/24", "5.101.153.0/24"],
    "mango":   ["89.188.22.0/24",  "217.182.61.0/24"],
    "sipuni":  ["62.109.0.0/24"],
    "other":   []
}

SIP_PROVIDER_PROXIES = {
    "novofon": "sip.novofon.ru",
    "mango":   "sip.mango-office.ru",
    "sipuni":  "sip.sipuni.ru",
}


class VoximplantPartnerService:
    """
    Сервис для работы с Voximplant Partner API.
    
    Использует credentials родительского аккаунта для создания
    и управления дочерними аккаунтами.
    """
    
    # Базовые URL API Voximplant (для России)
    API_BASE_URL = "https://api.voximplant.com/platform_api"
    MANAGE_API_URL = "https://api-ru-manage.voximplant.com/api"
    VERIFICATION_URL = "https://verify.voximplant.com/verification_ru"
    BILLING_URL = "https://billing.voximplant.com/login.php"
    
    # ✅ v1.6: URL для webhook
    WEBHOOK_URL = "https://voicyfy.ru/api/telephony/webhook/verification-status"
    
    # ✅ v3.0: Типы сценариев для outbound
    OUTBOUND_SCENARIO_TYPES = ["outbound_openai", "outbound_gemini", "outbound_cartesia", "outbound_yandex", "outbound_crm", "outbound_cascade", "outbound_fish"]
    
    def __init__(
        self,
        parent_account_id: Optional[str] = None,
        parent_api_key: Optional[str] = None,
        template_account_id: Optional[str] = None
    ):
        """
        Инициализация сервиса.
        
        Args:
            parent_account_id: ID родительского аккаунта (по умолчанию из settings)
            parent_api_key: API ключ родительского аккаунта (по умолчанию из settings)
            template_account_id: ID эталонного аккаунта для клонирования (опционально)
        """
        self.parent_account_id = parent_account_id or settings.VOXIMPLANT_PARENT_ACCOUNT_ID
        self.parent_api_key = parent_api_key or settings.VOXIMPLANT_PARENT_API_KEY
        self.template_account_id = template_account_id or getattr(settings, 'VOXIMPLANT_TEMPLATE_ACCOUNT_ID', None)
        
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать HTTP клиент"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def close(self):
        """Закрыть HTTP клиент"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    # =========================================================================
    # СОЗДАНИЕ АККАУНТА
    # =========================================================================
    
    async def create_child_account(
        self,
        account_name: str,
        account_email: str,
        account_password: Optional[str] = None,
        use_template: bool = True
    ) -> Dict[str, Any]:
        """
        Создать дочерний аккаунт.
        
        Если есть эталонный аккаунт и use_template=True, использует CloneAccount.
        Иначе создаёт пустой аккаунт через AddAccount.
        """
        if not account_password:
            account_password = self._generate_secure_password()
        
        if use_template and self.template_account_id:
            return await self._clone_account(account_name, account_email, account_password)
        else:
            return await self._add_account(account_name, account_email, account_password)
    
    async def _add_account(self, account_name: str, account_email: str, account_password: str) -> Dict[str, Any]:
        """Создать пустой дочерний аккаунт"""
        url = f"{self.API_BASE_URL}/AddAccount"
        
        params = {
            "parent_account_id": self.parent_account_id,
            "parent_account_api_key": self.parent_api_key,
            "account_name": account_name,
            "account_email": account_email,
            "account_password": account_password,
            "active": "true",
        }
        
        logger.info(f"[VOXIMPLANT] Creating child account: {account_name}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to create account: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        logger.info(f"[VOXIMPLANT] ✅ Account created: {result.get('account_id')}")
        
        return {
            "success": True,
            "account_id": str(result.get("account_id")),
            "account_name": account_name,
            "account_email": account_email,
            "account_password": account_password,
            "api_key": result.get("api_key"),
        }
    
    async def _clone_account(self, account_name: str, account_email: str, account_password: str) -> Dict[str, Any]:
        """Клонировать эталонный аккаунт"""
        url = f"{self.API_BASE_URL}/CloneAccount"
        
        params = {
            "parent_account_id": self.parent_account_id,
            "parent_account_api_key": self.parent_api_key,
            "account_id": self.template_account_id,
            "new_account_name": account_name,
            "new_account_email": account_email,
            "new_account_password": account_password,
        }
        
        logger.info(f"[VOXIMPLANT] Cloning template account to: {account_name}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to clone account: {result}")
            logger.info(f"[VOXIMPLANT] Falling back to AddAccount...")
            return await self._add_account(account_name, account_email, account_password)
        
        cloned = result.get("result", {})
        
        return {
            "success": True,
            "account_id": str(cloned.get("account_id")),
            "account_name": account_name,
            "account_email": account_email,
            "account_password": account_password,
            "api_key": cloned.get("api_key"),
        }
    
    # =========================================================================
    # SUBUSERS
    # =========================================================================
    
    async def create_subuser(
        self,
        child_account_id: str,
        child_api_key: str,
        subuser_name: str,
        roles: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Создать SubUser для дочернего аккаунта."""
        url = f"{self.API_BASE_URL}/AddSubUser"
        
        password = self._generate_secure_password()
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "new_subuser_name": subuser_name,
            "new_subuser_password": password,
            "role_id": "7;9",
        }
        
        logger.info(f"[VOXIMPLANT] Creating subuser {subuser_name} for account {child_account_id}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to create subuser: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        return {
            "success": True,
            "subuser_id": result.get("subuser_id"),
            "subuser_name": subuser_name,
            "subuser_password": password,
        }
    
    def _generate_secure_password(self, length: int = 16) -> str:
        """Генерирует безопасный пароль"""
        password = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!@#$%"),
        ]
        alphabet = string.ascii_letters + string.digits + "!@#$%"
        password += [secrets.choice(alphabet) for _ in range(length - 4)]
        secrets.SystemRandom().shuffle(password)
        return ''.join(password)
    
    def generate_unique_email(self, user_email: str, user_id: str) -> str:
        """Генерирует уникальный email для дочернего аккаунта Voximplant."""
        import time
        timestamp = int(time.time())
        
        if "@gmail.com" in user_email.lower():
            local_part = user_email.split("@")[0]
            return f"{local_part}+vox{timestamp}@gmail.com"
        
        return f"vox-{user_id[:8]}-{timestamp}@voicyfy.ru"
    
    # =========================================================================
    # ВЕРИФИКАЦИЯ
    # =========================================================================
    
    async def get_verification_url(
        self,
        child_account_id: str,
        child_api_key: str,
        subuser_login: str,
        subuser_password: str,
        verification_type: str = "legal_entity"
    ) -> Dict[str, Any]:
        """Получить URL для страницы верификации."""
        logger.info(f"[VOXIMPLANT] Getting verification URL for account {child_account_id}")
        
        client = await self._get_client()
        
        # Шаг 1: Logon
        logon_url = f"{self.MANAGE_API_URL}/Logon"
        logon_params = {
            "account_id": child_account_id,
            "subuser_login": subuser_login,
            "subuser_password": subuser_password,
        }
        
        response = await client.post(logon_url, data=logon_params)
        logon_result = response.json()
        
        if "error" in logon_result:
            return {"success": False, "error": logon_result.get("error", {}).get("msg", "Logon failed")}
        
        temp_session = logon_result.get("result")
        
        # Шаг 2: GetSessionID
        session_url = f"{self.MANAGE_API_URL}/GetSessionID"
        session_params = {
            "account_id": child_account_id,
            "subuser_login": subuser_login,
            "session_id": temp_session,
        }
        
        response = await client.post(session_url, data=session_params)
        session_result = response.json()
        
        if "error" in session_result:
            return {"success": False, "error": session_result.get("error", {}).get("msg", "Session failed")}
        
        session_id = session_result.get("result")
        
        # Шаг 3: Формируем URL
        # ✅ v3.3: URL-энкодинг логина и session_id на случай спецсимволов
        encoded_login = quote(subuser_login, safe='')
        encoded_session = quote(str(session_id), safe='')

        verification_url = (
            f"{self.VERIFICATION_URL}"
            f"?account_id={child_account_id}"
            f"&subuser_login={encoded_login}"
            f"&session_id={encoded_session}"
            f"&tab={verification_type}"
            f"&_lang=RU"
        )
        
        return {"success": True, "url": verification_url, "session_id": session_id}
    
    async def check_verification_status(
        self,
        child_account_id: str,
        child_api_key: str
    ) -> Dict[str, Any]:
        """
        Проверить статус верификации аккаунта.
        
        ✅ v1.5: Комбинированный подход:
        1. Сначала проверяем доступность номеров (надёжный индикатор)
        2. Если номера недоступны - проверяем GetAccountDocuments для детального статуса
        """
        logger.info(f"[VOXIMPLANT] Checking verification status for account {child_account_id}")
        
        client = await self._get_client()
        
        # ШАГ 1: Проверяем доступность номеров
        try:
            check_url = f"{self.API_BASE_URL}/GetNewPhoneNumbers"
            check_params = {
                "account_id": child_account_id,
                "api_key": child_api_key,
                "country_code": "RU",
                "count": 1
            }
            
            response = await client.post(check_url, data=check_params)
            result = response.json()
            
            logger.info(f"[VOXIMPLANT] GetNewPhoneNumbers response: total_count={result.get('total_count', 0)}")
            
            if "result" in result and len(result.get("result", [])) > 0:
                total_available = result.get("total_count", len(result.get("result", [])))
                logger.info(f"[VOXIMPLANT] ✅ Account {child_account_id} is VERIFIED (numbers available: {total_available})")
                return {
                    "success": True,
                    "verification_status": "VERIFIED",
                    "is_verified": True,
                    "raw_status": "VERIFIED_BY_NUMBERS_CHECK",
                }
                
        except Exception as e:
            logger.warning(f"[VOXIMPLANT] Numbers check failed: {e}")
        
        # ШАГ 2: Проверяем детальный статус
        url = f"{self.API_BASE_URL}/GetAccountDocuments"
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
        }
        
        response = await client.post(url, data=params)
        result = response.json()
        
        logger.info(f"[VOXIMPLANT] GetAccountDocuments response: {result}")
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] GetAccountDocuments failed: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        documents = result.get("result", [])
        verification_status = "NOT_STARTED"
        is_verified = False
        raw_status = None
        
        if documents:
            for doc in documents:
                verifications = doc.get("verifications", [])
                if verifications:
                    verification = verifications[0]
                    raw_status = verification.get("verification_status", "")
                    
                    status_mapping = {
                        "IN_PROGRESS": "AWAITING_VERIFICATION",
                        "VERIFIED": "VERIFIED",
                        "REJECTED": "REJECTED",
                        "PENDING": "AWAITING_DOCUMENTS_UPLOADING",
                        "AWAITING_DOCUMENTS": "AWAITING_DOCUMENTS_UPLOADING",
                        "AWAITING_AGREEMENT": "AWAITING_AGREEMENT_UPLOADING",
                    }
                    
                    verification_status = status_mapping.get(raw_status, raw_status or "NOT_STARTED")
                    is_verified = raw_status == "VERIFIED"
                    break
        
        logger.info(f"[VOXIMPLANT] Final status: {verification_status} (raw: {raw_status})")
        
        return {
            "success": True,
            "verification_status": verification_status,
            "is_verified": is_verified,
            "raw_status": raw_status,
        }
    
    # =========================================================================
    # WEBHOOK (CALLBACK)
    # =========================================================================
    
    async def set_account_callback(
        self,
        child_account_id: str,
        child_api_key: str,
        callback_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Установить webhook URL для уведомлений."""
        url = f"{self.API_BASE_URL}/SetAccountCallback"
        
        if callback_url is None:
            callback_url = self.WEBHOOK_URL
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "callback_url": callback_url,
            "account_document_status_updated": "true",
            "incoming_sms_notification_url": "https://voicyfy.ru/api/telephony/webhook/sms",
        }
        
        logger.info(f"[VOXIMPLANT] Setting callback URL for account {child_account_id}: {callback_url}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] SetAccountCallback failed: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        logger.info(f"[VOXIMPLANT] ✅ Callback URL set successfully for account {child_account_id}")
        
        return {"success": True, "result": result.get("result", 1)}
    
    async def get_account_callback(
        self,
        child_account_id: str,
        child_api_key: str
    ) -> Dict[str, Any]:
        """Получить текущие настройки callback."""
        url = f"{self.API_BASE_URL}/GetAccountCallback"
        params = {"account_id": child_account_id, "api_key": child_api_key}
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        return {"success": True, "callback_info": result.get("result", {})}
    
    # =========================================================================
    # БИЛЛИНГ
    # =========================================================================
    
    async def get_billing_url(
        self,
        child_account_id: str,
        child_api_key: str,
        subuser_login: Optional[str] = None,
        subuser_password: Optional[str] = None,
        start_page: str = "card"
    ) -> Dict[str, Any]:
        """Получить URL для страницы биллинга."""
        if subuser_login and subuser_password:
            # ✅ v3.3: URL-энкодинг логина и пароля — спецсимволы (#, %, & и т.д.) сломают URL без этого
            encoded_login = quote(subuser_login, safe='')
            encoded_password = quote(subuser_password, safe='')

            billing_url = (
                f"{self.BILLING_URL}"
                f"?account_id={child_account_id}"
                f"&subuser_login={encoded_login}"
                f"&subuser_password={encoded_password}"
                f"&_start_page={start_page}"
                f"&_lang=RU"
                f"&hide_account_name=false"
            )
        else:
            billing_url = (
                f"{self.BILLING_URL}"
                f"?account_id={child_account_id}"
                f"&api_key={child_api_key}"
                f"&_start_page={start_page}"
                f"&_lang=RU"
                f"&hide_account_name=false"
            )
        return {"success": True, "url": billing_url}
    
    async def get_account_balance(
        self,
        child_account_id: str,
        child_api_key: str,
        service_account_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Получить баланс аккаунта (JWT -> api_key -> мастер-аккаунт fallback)"""
        url = f"{self.API_BASE_URL}/GetAccountInfo"
        client = await self._get_client()

        # 1. Попытка через JWT (service account)
        if service_account_key:
            try:
                import jwt as pyjwt
                import time

                creds = json.loads(service_account_key)
                payload = {
                    "iss": str(creds["account_id"]),
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 3600,
                }
                token = pyjwt.encode(
                    payload,
                    creds["private_key"],
                    algorithm="RS256",
                    headers={"kid": creds["key_id"]}
                )

                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"}
                )
                result = response.json()

                if "error" not in result:
                    account_info = result.get("result", {})
                    return {
                        "success": True,
                        "balance": account_info.get("live_balance", 0),
                        "currency": account_info.get("currency", "RUR"),
                    }

                logger.warning(
                    f"[VOX_PARTNER] GetAccountInfo JWT failed for account {child_account_id}: "
                    f"{result.get('error', {}).get('msg')}, falling back to api_key..."
                )
            except Exception as e:
                logger.warning(
                    f"[VOX_PARTNER] GetAccountInfo JWT error for account {child_account_id}: {e}, "
                    f"falling back to api_key..."
                )

        # 2. Попытка через child api_key
        params = {"account_id": child_account_id, "api_key": child_api_key}
        response = await client.post(url, data=params)
        result = response.json()

        if "error" in result:
            error_code = result.get("error", {}).get("code")
            if error_code == 100:
                # 3. Fallback через мастер-аккаунт
                logger.warning(
                    f"[VOX_PARTNER] GetAccountInfo: child api_key failed (code 100) "
                    f"for account {child_account_id}, trying parent..."
                )
                parent_params = {
                    "account_id": self.parent_account_id,
                    "api_key": self.parent_api_key,
                    "child_account_id": child_account_id,
                }
                response = await client.post(url, data=parent_params)
                result = response.json()
                if "error" in result:
                    return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
            else:
                return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}

        account_info = result.get("result", {})
        return {
            "success": True,
            "balance": account_info.get("live_balance", 0),
            "currency": account_info.get("currency", "RUR"),
        }

    async def get_call_history_for_account(
        self,
        child_account_id: str,
        child_api_key: str,
        from_date: str,
        to_date: str,
        count: int = 5,
        with_calls: str = "true",
        desc_order: str = "true",
    ) -> Dict[str, Any]:
        """Получить историю звонков (с fallback через мастер-аккаунт при ошибке 100)"""
        url = f"{self.API_BASE_URL}/GetCallHistory"
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "from_date": from_date,
            "to_date": to_date,
            "count": count,
            "with_calls": with_calls,
            "desc_order": desc_order,
        }

        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()

        if "error" in result:
            error_code = result.get("error", {}).get("code")
            if error_code == 100:
                logger.warning(
                    f"[VOX_PARTNER] GetCallHistory: child api_key failed (code 100) "
                    f"for account {child_account_id}, trying parent..."
                )
                parent_params = {
                    "account_id": self.parent_account_id,
                    "api_key": self.parent_api_key,
                    "child_account_id": child_account_id,
                    "from_date": from_date,
                    "to_date": to_date,
                    "count": count,
                    "with_calls": with_calls,
                    "desc_order": desc_order,
                }
                response = await client.post(url, data=parent_params)
                result = response.json()
                if "error" in result:
                    return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
            else:
                return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}

        return {"success": True, "result": result.get("result", [])}
    
    # =========================================================================
    # ТЕЛЕФОННЫЕ НОМЕРА
    # =========================================================================
    
    async def get_phone_regions(
        self,
        child_account_id: str,
        child_api_key: str,
        country_code: str = "RU",
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """Получить список доступных регионов для покупки номеров."""
        url = f"{self.API_BASE_URL}/GetPhoneNumberRegions"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "country_code": country_code,
        }
        
        if category:
            params["phone_category_name"] = category
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        regions = result.get("result", [])
        
        return {
            "success": True,
            "regions": [
                {
                    "region_id": r.get("phone_region_id"),
                    "region_name": r.get("phone_region_name"),
                    "region_code": r.get("phone_region_code"),
                    "phone_count": r.get("phone_count", 0),
                    "phone_price": r.get("phone_price"),
                    "phone_installation_price": r.get("phone_installation_price", 0),
                }
                for r in regions
            ],
            "total": len(regions),
        }

    async def get_available_numbers(
        self,
        child_account_id: str,
        child_api_key: str,
        country_code: str = "RU",
        region_id: Optional[int] = None,
        category: Optional[str] = None,
        count: int = 20,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Получить список доступных номеров для покупки."""
        url = f"{self.API_BASE_URL}/GetNewPhoneNumbers"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "country_code": country_code,
            "count": count,
            "offset": offset,
        }
        
        if region_id is not None and region_id > 0:
            params["phone_region_id"] = int(region_id)
        
        if category:
            params["phone_category_name"] = category
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        numbers = result.get("result", [])
        
        return {
            "success": True,
            "numbers": [
                {
                    "phone_number": n.get("phone_number"),
                    "phone_price": n.get("phone_price"),
                    "phone_installation_price": n.get("phone_installation_price"),
                    "phone_region_name": n.get("phone_region_name"),
                    "phone_period": n.get("phone_period"),
                    "phone_category_name": n.get("phone_category_name"),
                }
                for n in numbers
            ],
            "total": result.get("total_count", len(numbers)),
        }
    
    async def buy_phone_number(
        self,
        child_account_id: str,
        child_api_key: str,
        phone_number: str
    ) -> Dict[str, Any]:
        """Купить телефонный номер."""
        url = f"{self.API_BASE_URL}/AttachPhoneNumber"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "phone_number": phone_number,
        }
        
        logger.info(f"[VOXIMPLANT] Buying phone number {phone_number}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        logger.info(f"[VOXIMPLANT] AttachPhoneNumber response: {result}")
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to buy number: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        phone_id = result.get("phone_id")
        
        # Fallback если phone_id не вернулся
        if not phone_id:
            logger.warning(f"[VOXIMPLANT] ⚠️ No phone_id in response, fetching via GetPhoneNumbers...")
            phone_id = await self.find_phone_id_by_number(
                child_account_id=child_account_id,
                child_api_key=child_api_key,
                phone_number=phone_number
            )
        
        if not phone_id:
            logger.error(f"[VOXIMPLANT] ❌ Could not get phone_id for {phone_number}")
            return {"success": False, "error": "Номер куплен, но не удалось получить ID."}
        
        logger.info(f"[VOXIMPLANT] ✅ Phone number purchased: {phone_number} (id: {phone_id})")
        
        return {"success": True, "phone_id": phone_id, "phone_number": phone_number}
    
    async def find_phone_id_by_number(
        self,
        child_account_id: str,
        child_api_key: str,
        phone_number: str
    ) -> Optional[int]:
        """Найти phone_id по номеру через GetPhoneNumbers."""
        try:
            result = await self.get_phone_numbers(
                child_account_id=child_account_id,
                child_api_key=child_api_key
            )
            
            if not result.get("success"):
                return None
            
            normalized_search = ''.join(filter(str.isdigit, phone_number))
            
            for num in result.get("numbers", []):
                normalized_num = ''.join(filter(str.isdigit, num.get("phone_number", "")))
                if normalized_num == normalized_search or normalized_num.endswith(normalized_search[-10:]):
                    return num.get("phone_id")
            
            return None
            
        except Exception as e:
            logger.error(f"[VOXIMPLANT] Error finding phone_id: {e}")
            return None
    
    async def bind_phone_to_application(
        self,
        child_account_id: str,
        child_api_key: str,
        phone_id: str,
        application_id: str,
        rule_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Привязать номер к приложению."""
        url = f"{self.API_BASE_URL}/BindPhoneNumberToApplication"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "phone_id": phone_id,
            "application_id": application_id,
        }
        
        if rule_id:
            params["rule_id"] = rule_id
        
        logger.info(f"[VOXIMPLANT] Binding phone {phone_id} to app {application_id}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to bind phone: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        logger.info(f"[VOXIMPLANT] ✅ Phone bound to application")
        return {"success": True, "result": result.get("result")}
    
    async def get_phone_numbers(
        self,
        child_account_id: str,
        child_api_key: str
    ) -> Dict[str, Any]:
        """Получить список купленных номеров."""
        url = f"{self.API_BASE_URL}/GetPhoneNumbers"
        params = {"account_id": child_account_id, "api_key": child_api_key}
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        numbers = result.get("result", [])
        
        return {
            "success": True,
            "numbers": [
                {
                    "phone_id": n.get("phone_id"),
                    "phone_number": n.get("phone_number"),
                    "phone_region_name": n.get("phone_region_name"),
                    "phone_next_renewal": n.get("phone_next_renewal"),
                    "phone_price": n.get("phone_price"),
                    "application_id": n.get("application_id"),
                    "application_name": n.get("application_name"),
                }
                for n in numbers
            ],
            "total": len(numbers),
        }
    
    async def deactivate_phone_number(
        self,
        child_account_id: str,
        child_api_key: str,
        phone_id: str
    ) -> Dict[str, Any]:
        """
        Деактивировать (удалить) телефонный номер.
        Требует роль Owner. Возврат средств невозможен.
        API: DeactivatePhoneNumber
        """
        url = f"{self.API_BASE_URL}/DeactivatePhoneNumber"
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "phone_id": phone_id,
        }

        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()

        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to deactivate phone {phone_id}: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}

        logger.info(f"[VOXIMPLANT] Phone deactivated: {phone_id}")
        return {"success": True}

    # =========================================================================
    # ПРИЛОЖЕНИЯ (APPLICATIONS)
    # =========================================================================
    
    async def get_applications(
        self,
        child_account_id: str,
        child_api_key: str
    ) -> Dict[str, Any]:
        """Получить список приложений"""
        url = f"{self.API_BASE_URL}/GetApplications"
        params = {"account_id": child_account_id, "api_key": child_api_key}
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        return {"success": True, "applications": result.get("result", [])}
    
    async def add_application(
        self,
        child_account_id: str,
        child_api_key: str,
        application_name: str
    ) -> Dict[str, Any]:
        """Создать приложение."""
        url = f"{self.API_BASE_URL}/AddApplication"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "application_name": application_name,
        }
        
        logger.info(f"[VOXIMPLANT] Creating application '{application_name}' for account {child_account_id}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to create application: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        application_id = result.get("application_id")
        logger.info(f"[VOXIMPLANT] ✅ Application created: {application_id}")
        
        return {
            "success": True,
            "application_id": application_id,
            "application_name": application_name,
        }
    
    # =========================================================================
    # СЦЕНАРИИ (SCENARIOS)
    # =========================================================================
    
    async def get_scenarios(
        self,
        account_id: str,
        api_key: str,
        with_script: bool = False,
        scenario_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Получить список сценариев."""
        url = f"{self.API_BASE_URL}/GetScenarios"
        
        params = {"account_id": account_id, "api_key": api_key}
        
        if with_script:
            params["with_script"] = "true"
        
        if scenario_id:
            params["scenario_id"] = scenario_id
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        scenarios = result.get("result", [])
        
        return {
            "success": True,
            "scenarios": [
                {
                    "scenario_id": s.get("scenario_id"),
                    "scenario_name": s.get("scenario_name"),
                    "scenario_script": s.get("scenario_script"),
                    "application_id": s.get("application_id"),
                    "application_name": s.get("application_name"),
                    "parent": s.get("parent", False),
                }
                for s in scenarios
            ],
            "total": len(scenarios),
        }
    
    async def get_parent_scenarios(self, with_script: bool = False) -> Dict[str, Any]:
        """Получить сценарии с родительского аккаунта."""
        return await self.get_scenarios(
            account_id=self.parent_account_id,
            api_key=self.parent_api_key,
            with_script=with_script
        )
    
    async def get_scenario_with_script(
        self,
        account_id: str,
        api_key: str,
        scenario_id: int
    ) -> Dict[str, Any]:
        """Получить конкретный сценарий С КОДОМ."""
        result = await self.get_scenarios(
            account_id=account_id,
            api_key=api_key,
            with_script=True,
            scenario_id=scenario_id
        )
        
        if not result.get("success"):
            return result
        
        scenarios = result.get("scenarios", [])
        if not scenarios:
            return {"success": False, "error": f"Scenario {scenario_id} not found"}
        
        return {"success": True, "scenario": scenarios[0]}
    
    async def add_scenario(
        self,
        child_account_id: str,
        child_api_key: str,
        scenario_name: str,
        scenario_script: str
    ) -> Dict[str, Any]:
        """Создать сценарий."""
        url = f"{self.API_BASE_URL}/AddScenario"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "scenario_name": scenario_name,
            "scenario_script": scenario_script,
        }
        
        logger.info(f"[VOXIMPLANT] Creating scenario '{scenario_name}' for account {child_account_id}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to create scenario: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        scenario_id = result.get("scenario_id")
        logger.info(f"[VOXIMPLANT] ✅ Scenario created: {scenario_name} (ID: {scenario_id})")
        
        return {
            "success": True,
            "scenario_id": scenario_id,
            "scenario_name": scenario_name,
        }
    
    async def set_scenario_info(
        self,
        child_account_id: str,
        child_api_key: str,
        scenario_id: int,
        scenario_script: Optional[str] = None,
        scenario_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Обновить сценарий."""
        url = f"{self.API_BASE_URL}/SetScenarioInfo"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "scenario_id": scenario_id,
        }
        
        if scenario_script:
            params["scenario_script"] = scenario_script
        
        if scenario_name:
            params["scenario_name"] = scenario_name
        
        logger.info(f"[VOXIMPLANT] Updating scenario {scenario_id} for account {child_account_id}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to update scenario: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        logger.info(f"[VOXIMPLANT] ✅ Scenario updated: {scenario_id}")
        
        return {"success": True, "result": result.get("result")}
    
    async def update_scenario(
        self,
        child_account_id: str,
        child_api_key: str,
        scenario_id: int,
        scenario_script: str
    ) -> Dict[str, Any]:
        """Обновить код сценария (алиас для set_scenario_info)."""
        return await self.set_scenario_info(
            child_account_id=child_account_id,
            child_api_key=child_api_key,
            scenario_id=scenario_id,
            scenario_script=scenario_script
        )
    
    async def copy_scenarios_from_parent(
        self,
        child_account_id: str,
        child_api_key: str
    ) -> Dict[str, Any]:
        """Скопировать все сценарии с родителя."""
        logger.info(f"[VOXIMPLANT] Copying scenarios from parent to child {child_account_id}")
        
        # Получаем список сценариев
        parent_result = await self.get_parent_scenarios(with_script=False)
        
        if not parent_result.get("success"):
            return {"success": False, "error": "Failed to get parent scenarios"}
        
        parent_scenarios = parent_result.get("scenarios", [])
        
        if not parent_scenarios:
            logger.warning("[VOXIMPLANT] No scenarios found on parent account")
            return {"success": True, "scenario_ids": {}, "copied": 0}
        
        logger.info(f"[VOXIMPLANT] Found {len(parent_scenarios)} scenarios on parent account")
        
        scenario_ids = {}
        copied_count = 0
        errors = []
        
        # Для каждого сценария получаем код отдельным запросом
        for scenario in parent_scenarios:
            scenario_name = scenario.get("scenario_name")
            scenario_id = scenario.get("scenario_id")
            
            if not scenario_name or not scenario_id:
                continue
            
            # Получаем код сценария
            script_result = await self.get_scenario_with_script(
                account_id=self.parent_account_id,
                api_key=self.parent_api_key,
                scenario_id=scenario_id
            )
            
            if not script_result.get("success"):
                errors.append(f"{scenario_name}: не удалось получить код")
                continue
            
            scenario_script = script_result.get("scenario", {}).get("scenario_script")
            
            if not scenario_script:
                errors.append(f"{scenario_name}: код пустой")
                continue
            
            logger.info(f"[VOXIMPLANT] Got script for {scenario_name}: {len(scenario_script)} chars")
            
            # Создаём сценарий на дочке
            result = await self.add_scenario(
                child_account_id=child_account_id,
                child_api_key=child_api_key,
                scenario_name=scenario_name,
                scenario_script=scenario_script
            )
            
            if result.get("success"):
                scenario_ids[scenario_name] = result.get("scenario_id")
                copied_count += 1
                logger.info(f"[VOXIMPLANT] ✅ Copied {scenario_name}")
            else:
                errors.append(f"{scenario_name}: {result.get('error')}")
        
        logger.info(f"[VOXIMPLANT] ✅ Copied {copied_count}/{len(parent_scenarios)} scenarios")
        
        return {
            "success": True,
            "scenario_ids": scenario_ids,
            "copied": copied_count,
            "total": len(parent_scenarios),
            "errors": errors if errors else None
        }
    
    # =========================================================================
    # ПРАВИЛА МАРШРУТИЗАЦИИ (RULES)
    # =========================================================================
    
    async def get_rules(
        self,
        child_account_id: str,
        child_api_key: str,
        application_id: str
    ) -> Dict[str, Any]:
        """Получить правила роутинга."""
        url = f"{self.API_BASE_URL}/GetRules"
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "application_id": application_id,
        }
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        return {"success": True, "rules": result.get("result", [])}
    
    async def add_rule(
        self,
        child_account_id: str,
        child_api_key: str,
        application_id: str,
        rule_name: str,
        rule_pattern: str,
        scenario_id: Union[int, List[int]]
    ) -> Dict[str, Any]:
        """Создать правило маршрутизации."""
        url = f"{self.API_BASE_URL}/AddRule"

        # Voximplant API принимает scenario_id как "id1;id2" для sequenced scenarios
        if isinstance(scenario_id, list):
            formatted_scenario_id = ";".join(str(s) for s in scenario_id)
        else:
            formatted_scenario_id = scenario_id

        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "application_id": application_id,
            "rule_name": rule_name,
            "rule_pattern": rule_pattern,
            "scenario_id": formatted_scenario_id,
        }
        
        logger.info(f"[VOXIMPLANT] Creating rule '{rule_name}' (pattern: {rule_pattern})")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to create rule: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        rule_id = result.get("rule_id")
        logger.info(f"[VOXIMPLANT] ✅ Rule created: {rule_name} (ID: {rule_id})")
        
        return {"success": True, "rule_id": rule_id, "rule_name": rule_name}
    
    async def set_rule_info(
        self,
        child_account_id: str,
        child_api_key: str,
        rule_id: str,
        scenario_id: Optional[Union[int, List[int]]] = None,
        rule_name: Optional[str] = None,
        rule_pattern: Optional[str] = None
    ) -> Dict[str, Any]:
        """Обновить правило маршрутизации."""
        url = f"{self.API_BASE_URL}/SetRuleInfo"

        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "rule_id": rule_id,
        }

        if scenario_id is not None:
            if isinstance(scenario_id, list):
                params["scenario_id"] = ";".join(str(s) for s in scenario_id)
            else:
                params["scenario_id"] = scenario_id
        if rule_name:
            params["rule_name"] = rule_name
        if rule_pattern:
            params["rule_pattern"] = rule_pattern
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        return {"success": True, "result": result.get("result")}
    
    async def delete_rule(
        self,
        child_account_id: str,
        child_api_key: str,
        rule_id: str
    ) -> Dict[str, Any]:
        """Удалить правило маршрутизации."""
        url = f"{self.API_BASE_URL}/DelRule"
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "rule_id": rule_id,
        }
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        return {"success": True, "result": result.get("result")}
    
    # =========================================================================
    # 🆕 v3.0 + v3.1: ИСХОДЯЩИЕ ЗВОНКИ (OUTBOUND CALLS)
    # =========================================================================
    
    async def start_scenarios(
        self,
        child_account_id: str,
        child_api_key: str,
        rule_id: int,
        script_custom_data: Optional[str] = None
    ) -> Dict[str, Any]:
        """Запустить сценарий через StartScenarios API."""
        url = f"{self.API_BASE_URL}/StartScenarios"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "rule_id": rule_id,
        }
        
        if script_custom_data:
            params["script_custom_data"] = script_custom_data
        
        logger.info(f"[VOXIMPLANT] Starting scenario via rule {rule_id}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to start scenario: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        call_session_id = result.get("call_session_history_id")
        media_url = result.get("media_session_access_url")
        
        logger.info(f"[VOXIMPLANT] ✅ Scenario started: session={call_session_id}")
        
        return {
            "success": True,
            "call_session_history_id": call_session_id,
            "media_session_access_url": media_url,
            "result": result.get("result", 1)
        }
    
    async def start_outbound_call(
        self,
        child_account_id: str,
        child_api_key: str,
        rule_id: int,
        phone_number: str,
        assistant_id: str,
        caller_id: str,
        first_phrase: Optional[str] = None,
        mute_duration_ms: int = 3000,
        contact_name: Optional[str] = None,
        task_title: Optional[str] = None,
        task_description: Optional[str] = None,
        custom_greeting: Optional[str] = None,
        timezone: str = "Europe/Moscow",
        task: Optional[str] = None,
        assistant_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Запустить исходящий звонок с полным контекстом CRM."""
        custom_data = {
            "phone_number": phone_number,
            "assistant_id": assistant_id,
            "assistant_type": assistant_type or "gemini",
            "caller_id": caller_id,
            "mute_duration_ms": mute_duration_ms,
            "contact_name": contact_name or "",
            "task_title": task_title or task or "",
            "task_description": task_description or task or "",
            "custom_greeting": custom_greeting or "",
            "timezone": timezone,
            "task": task or "",
        }
        
        if first_phrase and not custom_greeting:
            custom_data["custom_greeting"] = first_phrase
        
        script_custom_data = json.dumps(custom_data, ensure_ascii=False)
        
        logger.info(f"[VOXIMPLANT] 📞 Starting outbound call:")
        logger.info(f"[VOXIMPLANT]    Target: {phone_number}")
        logger.info(f"[VOXIMPLANT]    Caller ID: {caller_id}")
        logger.info(f"[VOXIMPLANT]    Assistant: {assistant_id}")
        
        result = await self.start_scenarios(
            child_account_id=child_account_id,
            child_api_key=child_api_key,
            rule_id=rule_id,
            script_custom_data=script_custom_data
        )
        
        if result.get("success"):
            logger.info(f"[VOXIMPLANT] ✅ Outbound call started to {phone_number}")
        else:
            logger.error(f"[VOXIMPLANT] ❌ Failed to start outbound call: {result.get('error')}")
        
        return result
    
    async def setup_outbound_rules(
        self,
        child_account_id: str,
        child_api_key: str,
        application_id: str,
        scenario_ids: Dict[str, int]
    ) -> Dict[str, Any]:
        """Создать Rules для исходящих сценариев."""
        logger.info(f"[VOXIMPLANT] Setting up outbound rules for account {child_account_id}")
        
        rule_ids = {}
        errors = []
        
        for scenario_type in self.OUTBOUND_SCENARIO_TYPES:
            scenario_id = scenario_ids.get(scenario_type)

            if not scenario_id:
                continue

            # Каскад требует цепочку [vox-turn-taking, outbound_cascade]:
            # vox-turn-taking объявляет глобальный VoxTurnTaking, который
            # использует outbound_cascade (как в inbound-привязке).
            effective_scenario_id = scenario_id
            if scenario_type == "outbound_cascade":
                tt_id = scenario_ids.get("vox-turn-taking")
                if tt_id:
                    effective_scenario_id = [int(tt_id), int(scenario_id)]

            rule_result = await self.add_rule(
                child_account_id=child_account_id,
                child_api_key=child_api_key,
                application_id=application_id,
                rule_name=scenario_type,
                rule_pattern=scenario_type,
                scenario_id=effective_scenario_id
            )
            
            if rule_result.get("success"):
                rule_ids[scenario_type] = rule_result.get("rule_id")
            else:
                errors.append(f"{scenario_type}: {rule_result.get('error')}")
        
        return {
            "success": len(rule_ids) > 0,
            "rule_ids": rule_ids,
            "created": len(rule_ids),
            "errors": errors if errors else None
        }
    
    # =========================================================================
    # 🆕 v3.2: SERVICE ACCOUNT ДЛЯ JWT АВТОРИЗАЦИИ
    # =========================================================================
    
    async def create_key(
        self,
        child_account_id: str,
        child_api_key: str,
        key_name: str = "voicyfy_service_account",
        description: str = "Service account for Voksy AI platform",
        roles: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        🆕 v3.2: Создать Service Account (ключевую пару).
        
        ВАЖНО: Voximplant возвращает private_key ТОЛЬКО ОДИН РАЗ!
        
        API: https://voximplant.com/docs/references/httpapi/rolesystem#createkey
        """
        url = f"{self.API_BASE_URL}/CreateKey"
        
        if roles is None:
            roles = ["Owner", "Accountant"]
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "key_name": key_name,
            "description": description,
            "role_name": ";".join(roles),
        }
        
        logger.info(f"[VOXIMPLANT] Creating Service Account for child {child_account_id}")
        logger.info(f"[VOXIMPLANT]    Key name: {key_name}")
        logger.info(f"[VOXIMPLANT]    Roles: {roles}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to create Service Account: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        key_data = result.get("result", result)
        key_id = key_data.get("key_id")
        private_key = key_data.get("private_key")
        
        if not key_id or not private_key:
            logger.error(f"[VOXIMPLANT] CreateKey response missing key_id or private_key: {result}")
            return {"success": False, "error": "Invalid response from CreateKey API"}
        
        logger.info(f"[VOXIMPLANT] ✅ Service Account created:")
        logger.info(f"[VOXIMPLANT]    Key ID: {key_id}")
        logger.info(f"[VOXIMPLANT]    Private key length: {len(private_key)} chars")
        
        return {
            "success": True,
            "key_id": str(key_id),
            "private_key": private_key,
            "account_id": child_account_id,
        }
    
    async def get_keys(
        self,
        child_account_id: str,
        child_api_key: str
    ) -> Dict[str, Any]:
        """Получить список Service Accounts."""
        url = f"{self.API_BASE_URL}/GetKeys"
        params = {"account_id": child_account_id, "api_key": child_api_key}
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        keys = result.get("result", [])
        
        return {
            "success": True,
            "keys": [
                {
                    "key_id": k.get("key_id"),
                    "key_name": k.get("key_name"),
                    "description": k.get("description"),
                    "roles": k.get("roles", []),
                }
                for k in keys
            ],
            "total": len(keys),
        }
    
    async def delete_key(
        self,
        child_account_id: str,
        child_api_key: str,
        key_id: str
    ) -> Dict[str, Any]:
        """Удалить Service Account."""
        url = f"{self.API_BASE_URL}/DeleteKey"
        
        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "key_id": key_id,
        }
        
        logger.info(f"[VOXIMPLANT] Deleting Service Account key {key_id}")
        
        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()
        
        if "error" in result:
            logger.error(f"[VOXIMPLANT] Failed to delete key: {result}")
            return {"success": False, "error": result.get("error", {}).get("msg", "Unknown error")}
        
        logger.info(f"[VOXIMPLANT] ✅ Service Account key deleted: {key_id}")
        
        return {"success": True, "result": result.get("result")}
    
    async def setup_service_account(
        self,
        child_account_id: str,
        child_api_key: str
    ) -> Dict[str, Any]:
        """
        🆕 v3.2: Комплексная настройка Service Account.
        
        Создаёт Service Account и возвращает данные для сохранения в БД.
        """
        logger.info(f"[VOXIMPLANT] Setting up Service Account for child {child_account_id}")
        
        result = await self.create_key(
            child_account_id=child_account_id,
            child_api_key=child_api_key,
            key_name="voicyfy_records_access",
            description="Service account for accessing call recordings",
            roles=["Owner", "Accountant"]
        )
        
        if not result.get("success"):
            return result
        
        credentials = {
            "account_id": int(child_account_id),
            "key_id": result.get("key_id"),
            "private_key": result.get("private_key"),
        }
        
        logger.info(f"[VOXIMPLANT] ✅ Service Account setup complete for {child_account_id}")
        
        return {
            "success": True,
            "service_account_id": result.get("key_id"),
            "service_account_key": json.dumps(credentials),
            "credentials": credentials,
        }
    
    # =========================================================================
    # КОМПЛЕКСНАЯ НАСТРОЙКА ДОЧЕРНЕГО АККАУНТА
    # =========================================================================
    
    async def setup_child_account_scenarios(
        self,
        child_account_id: str,
        child_api_key: str,
        application_name: str = "voicyfy"
    ) -> Dict[str, Any]:
        """Комплексная настройка сценариев для дочернего аккаунта."""
        logger.info(f"[VOXIMPLANT] Setting up scenarios for child account {child_account_id}")
        
        # Создаём приложение
        app_result = await self.add_application(
            child_account_id=child_account_id,
            child_api_key=child_api_key,
            application_name=application_name
        )
        
        if not app_result.get("success"):
            return {"success": False, "error": f"Failed to create application: {app_result.get('error')}"}
        
        application_id = app_result.get("application_id")
        
        # Копируем сценарии
        copy_result = await self.copy_scenarios_from_parent(
            child_account_id=child_account_id,
            child_api_key=child_api_key
        )
        
        if not copy_result.get("success"):
            return {"success": False, "error": f"Failed to copy scenarios: {copy_result.get('error')}"}
        
        scenario_ids = copy_result.get("scenario_ids", {})
        
        # Создаём Rules для outbound
        rule_ids = {}
        outbound_errors = []
        
        outbound_result = await self.setup_outbound_rules(
            child_account_id=child_account_id,
            child_api_key=child_api_key,
            application_id=str(application_id),
            scenario_ids=scenario_ids
        )
        
        if outbound_result.get("success"):
            rule_ids = outbound_result.get("rule_ids", {})
        else:
            outbound_errors = outbound_result.get("errors", [])
        
        all_errors = copy_result.get("errors", []) or []
        if outbound_errors:
            all_errors.extend(outbound_errors)
        
        logger.info(f"[VOXIMPLANT] ✅ Child account setup complete:")
        logger.info(f"[VOXIMPLANT]    Application: {application_id}")
        logger.info(f"[VOXIMPLANT]    Scenarios: {list(scenario_ids.keys())}")
        logger.info(f"[VOXIMPLANT]    Outbound Rules: {list(rule_ids.keys())}")
        
        return {
            "success": True,
            "application_id": application_id,
            "application_name": application_name,
            "scenario_ids": scenario_ids,
            "rule_ids": rule_ids,
            "scenarios_copied": copy_result.get("copied", 0),
            "scenarios_total": copy_result.get("total", 0),
            "outbound_rules_created": len(rule_ids),
            "errors": all_errors if all_errors else None,
        }


    # =========================================================================
    # SIP ТРАНКИ (ВХОДЯЩИЕ ЗВОНКИ)
    # =========================================================================

    async def add_sip_registration(
        self,
        child_account_id: str,
        child_api_key: str,
        sip_proxy: str,
        sip_login: str,
        sip_password: str,
        application_id: str,
        rule_id: str
    ) -> Dict[str, Any]:
        """
        Создать SIP регистрацию на дочернем аккаунте.

        Регистрирует SIP транк для приёма входящих звонков от внешнего провайдера.

        Args:
            child_account_id: ID дочернего аккаунта
            child_api_key: API ключ дочернего аккаунта
            sip_proxy: Адрес SIP сервера провайдера (напр. sip.novofon.ru)
            sip_login: Логин SIP учётной записи у провайдера
            sip_password: Пароль SIP учётной записи
            application_id: ID приложения Voximplant
            rule_id: ID правила маршрутизации (для привязки к входящему сценарию)

        Returns:
            {"success": True, "sip_registration_id": "123"} или ошибка
        """
        url = f"{self.API_BASE_URL}/CreateSipRegistration"

        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "proxy": sip_proxy,
            "sip_username": sip_login,
            "sip_password": sip_password,
            "is_persistent": True,  # ✅ v3.3: исправлен NameError (было: true)
            "application_id": application_id,
            "rule_id": rule_id,
        }

        logger.info(f"[VOXIMPLANT] Adding SIP registration: proxy={sip_proxy}, login={sip_login}")

        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()

        if "error" in result:
            error_msg = result.get("error", {}).get("msg", "Unknown error")
            logger.error(f"[VOXIMPLANT] Failed to add SIP registration: {result}")
            return {"success": False, "error": error_msg}

        sip_registration_id = result.get("sip_registration_id")
        logger.info(f"[VOXIMPLANT] ✅ SIP registration created: ID={sip_registration_id}")

        return {"success": True, "sip_registration_id": str(sip_registration_id)}

    async def delete_sip_registration(
        self,
        child_account_id: str,
        child_api_key: str,
        sip_registration_id: str
    ) -> Dict[str, Any]:
        """
        Удалить SIP регистрацию.

        Args:
            child_account_id: ID дочернего аккаунта
            child_api_key: API ключ дочернего аккаунта
            sip_registration_id: ID SIP регистрации для удаления

        Returns:
            {"success": True} или ошибка
        """
        url = f"{self.API_BASE_URL}/DeleteSipRegistration"

        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "sip_registration_id": sip_registration_id,
        }

        logger.info(f"[VOXIMPLANT] Deleting SIP registration: ID={sip_registration_id}")

        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()

        if "error" in result:
            error_msg = result.get("error", {}).get("msg", "Unknown error")
            logger.error(f"[VOXIMPLANT] Failed to delete SIP registration: {result}")
            return {"success": False, "error": error_msg}

        logger.info(f"[VOXIMPLANT] ✅ SIP registration deleted: ID={sip_registration_id}")
        return {"success": True}

    async def add_authorized_ip(
        self,
        child_account_id: str,
        child_api_key: str,
        ip: str,
        allowed: bool = True
    ) -> Dict[str, Any]:
        """
        Добавить авторизованный IP адрес на дочернем аккаунте.

        Используется для whitelist IP адресов SIP провайдеров.

        Args:
            child_account_id: ID дочернего аккаунта
            child_api_key: API ключ дочернего аккаунта
            ip: IP адрес или подсеть (напр. 185.189.255.0/24)
            allowed: Разрешить или заблокировать

        Returns:
            {"success": True} или ошибка
        """
        url = f"{self.API_BASE_URL}/AddAuthorizedAccountIP"

        params = {
            "account_id": child_account_id,
            "api_key": child_api_key,
            "authorized_ip": ip,
            "allowed": allowed,
        }

        logger.info(f"[VOXIMPLANT] Adding authorized IP: {ip} (allowed={allowed})")

        client = await self._get_client()
        response = await client.post(url, data=params)
        result = response.json()

        if "error" in result:
            error_msg = result.get("error", {}).get("msg", "Unknown error")
            logger.error(f"[VOXIMPLANT] Failed to add authorized IP: {result}")
            return {"success": False, "error": error_msg}

        logger.info(f"[VOXIMPLANT] ✅ Authorized IP added: {ip}")
        return {"success": True}


# =============================================================================
# SINGLETON
# =============================================================================

_service_instance: Optional[VoximplantPartnerService] = None


def get_voximplant_partner_service() -> VoximplantPartnerService:
    """Получить экземпляр сервиса Voximplant Partner."""
    global _service_instance
    
    if _service_instance is None:
        _service_instance = VoximplantPartnerService()
    
    return _service_instance
