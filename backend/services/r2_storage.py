# backend/services/r2_storage.py
"""
Cloudflare R2 Storage Service для сохранения записей звонков.
✅ v1.0: Загрузка аудиофайлов от Voximplant в R2
✅ v2.0: JWT авторизация для secure записей Voximplant
✅ v2.1: Исправлена генерация JWT - правильный формат kid в header

Использование:
    from backend.services.r2_storage import R2StorageService
    
    # Проверка настроек
    if R2StorageService.is_configured():
        # Для secure записей нужны credentials дочернего аккаунта
        url = await R2StorageService.upload_recording(
            record_url, 
            call_id, 
            assistant_id,
            voximplant_credentials={
                "account_id": 12345,
                "key_id": "abc123",
                "private_key": "-----BEGIN RSA PRIVATE KEY-----..."
            }
        )
"""

import boto3
import httpx
import jwt
import time
from datetime import datetime
from typing import Optional, Dict, Any

from backend.core.logging import get_logger
from backend.core.config import settings

logger = get_logger(__name__)


class VoximplantAuth:
    """
    Генератор JWT токенов для авторизации в Voximplant API.
    
    Используется для скачивания secure записей звонков.
    Требует credentials Service Account дочернего аккаунта.
    
    JWT формат:
    - Header: {"typ": "JWT", "alg": "RS256", "kid": key_id}
    - Payload: {"iat": timestamp, "iss": account_id, "exp": timestamp+60}
    """
    
    def __init__(self, credentials: Dict[str, Any]):
        """
        Args:
            credentials: Dict с полями:
                - account_id: ID аккаунта Voximplant (int или str)
                - key_id: ID ключа Service Account (str)
                - private_key: RSA приватный ключ в PEM формате (str)
        """
        self.account_id = credentials.get("account_id")
        self.key_id = credentials.get("key_id")
        self.private_key = credentials.get("private_key")
        
        # Валидация
        if not self.account_id:
            raise ValueError("Missing account_id in Voximplant credentials")
        if not self.key_id:
            raise ValueError("Missing key_id in Voximplant credentials")
        if not self.private_key:
            raise ValueError("Missing private_key in Voximplant credentials")
        
        # Проверяем формат private_key
        if not self.private_key.startswith("-----BEGIN"):
            logger.warning("[VoximplantAuth] private_key doesn't look like PEM format")
    
    def build_auth_header(self) -> str:
        """
        Генерирует JWT токен для авторизации.
        
        Returns:
            Строка вида "Bearer <jwt_token>"
        """
        ts = int(time.time())
        
        # Payload с account_id в iss
        payload = {
            "iss": int(self.account_id),  # Voximplant ожидает int
            "iat": ts - 5,                # Небольшой запас на рассинхрон времени
            "exp": ts + 60                # Токен действителен 60 секунд
        }
        
        # Header с key_id в kid
        headers = {
            "kid": str(self.key_id)
        }
        
        try:
            token = jwt.encode(
                payload,
                self.private_key,
                algorithm='RS256',
                headers=headers
            )
            
            logger.debug(f"[VoximplantAuth] JWT generated successfully")
            logger.debug(f"[VoximplantAuth]   iss (account_id): {self.account_id}")
            logger.debug(f"[VoximplantAuth]   kid (key_id): {self.key_id}")
            
            return f'Bearer {token}'
            
        except Exception as e:
            logger.error(f"[VoximplantAuth] Failed to generate JWT: {e}")
            raise


class R2StorageService:
    """Сервис для работы с Cloudflare R2"""
    
    _client = None
    
    @classmethod
    def _get_client(cls):
        """Получить или создать S3 клиент для R2"""
        if cls._client is None:
            if not settings.R2_ACCESS_KEY or not settings.R2_SECRET_KEY:
                logger.warning("[R2] Credentials not configured")
                return None
            
            try:
                cls._client = boto3.client(
                    's3',
                    endpoint_url=settings.R2_ENDPOINT,
                    aws_access_key_id=settings.R2_ACCESS_KEY,
                    aws_secret_access_key=settings.R2_SECRET_KEY,
                    region_name='auto'
                )
                logger.info("[R2] S3 client initialized successfully")
            except Exception as e:
                logger.error(f"[R2] Failed to initialize S3 client: {e}")
                return None
        
        return cls._client
    
    @classmethod
    def _is_secure_url(cls, url: str) -> bool:
        """
        Проверяет, является ли URL secure записью Voximplant.
        
        Secure записи имеют характерные признаки в URL:
        - voximplant-records-secure
        - securerecords
        - records-secure
        """
        if not url:
            return False
        
        url_lower = url.lower()
        
        secure_indicators = [
            'voximplant-records-secure',
            'securerecords',
            'records-secure',
            '-secure.',
            '/secure/',
            'secure-records'
        ]
        
        is_secure = any(indicator in url_lower for indicator in secure_indicators)
        
        if is_secure:
            logger.debug(f"[R2] URL identified as SECURE: {url[:60]}...")
        else:
            logger.debug(f"[R2] URL identified as NON-SECURE: {url[:60]}...")
        
        return is_secure
    
    @classmethod
    async def upload_recording(
        cls,
        record_url: str,
        call_id: str,
        assistant_id: str,
        voximplant_credentials: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Скачивает запись от Voximplant и загружает в R2.
        
        ✅ v2.0: Поддержка secure записей с JWT авторизацией
        ✅ v2.1: Исправлена генерация JWT
        
        Args:
            record_url: Временный URL записи от Voximplant
            call_id: ID звонка
            assistant_id: ID ассистента
            voximplant_credentials: Опционально - credentials для secure записей:
                {
                    "account_id": int,     # ID аккаунта Voximplant
                    "key_id": str,         # ID ключа Service Account
                    "private_key": str     # RSA приватный ключ (PEM)
                }
            
        Returns:
            Публичный URL записи в R2 или None при ошибке
        """
        try:
            client = cls._get_client()
            if not client:
                logger.error("[R2] Client not available - check R2 credentials")
                return None
            
            logger.info(f"[R2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[R2] 📥 Downloading recording from Voximplant...")
            logger.info(f"[R2]    Source URL: {record_url[:80]}...")
            
            # =====================================================================
            # ✅ v2.0: Определяем нужна ли авторизация
            # =====================================================================
            headers = {}
            is_secure = cls._is_secure_url(record_url)
            
            if is_secure:
                logger.info(f"[R2] 🔐 Detected SECURE recording URL")
                
                if voximplant_credentials:
                    try:
                        auth = VoximplantAuth(voximplant_credentials)
                        headers['Authorization'] = auth.build_auth_header()
                        logger.info(f"[R2] ✅ JWT authorization header generated")
                        logger.info(f"[R2]    Account ID: {voximplant_credentials.get('account_id')}")
                        logger.info(f"[R2]    Key ID: {voximplant_credentials.get('key_id')}")
                    except ValueError as auth_error:
                        logger.error(f"[R2] ❌ Invalid credentials: {auth_error}")
                        logger.warning(f"[R2] ⚠️ Attempting download without auth (will likely fail)...")
                    except Exception as auth_error:
                        logger.error(f"[R2] ❌ Failed to generate JWT: {auth_error}")
                        logger.warning(f"[R2] ⚠️ Attempting download without auth (will likely fail)...")
                else:
                    logger.warning(f"[R2] ⚠️ Secure URL but no credentials provided!")
                    logger.warning(f"[R2] ⚠️ Download will likely fail with 403...")
            else:
                logger.info(f"[R2] 📂 Non-secure recording URL (no auth needed)")
            
            # =====================================================================
            # Скачиваем файл
            # =====================================================================
            async with httpx.AsyncClient(timeout=120.0) as http_client:
                response = await http_client.get(
                    record_url, 
                    headers=headers,
                    follow_redirects=True
                )
                
                # Проверяем статус
                if response.status_code == 403:
                    logger.error(f"[R2] ❌ 403 Forbidden - authorization failed!")
                    if is_secure:
                        if not voximplant_credentials:
                            logger.error(f"[R2] ❌ Secure recording requires Service Account credentials!")
                            logger.error(f"[R2] ❌ Run admin/setup-service-accounts to create them")
                        else:
                            logger.error(f"[R2] ❌ JWT token was rejected - check credentials")
                    return None
                
                if response.status_code == 404:
                    logger.error(f"[R2] ❌ 404 Not Found - recording may have expired")
                    return None
                
                if response.status_code == 401:
                    logger.error(f"[R2] ❌ 401 Unauthorized - invalid JWT token")
                    return None
                
                response.raise_for_status()
                audio_data = response.content
            
            file_size_kb = len(audio_data) / 1024
            file_size_mb = file_size_kb / 1024
            
            if file_size_mb >= 1:
                logger.info(f"[R2] ✅ Downloaded: {file_size_mb:.2f} MB")
            else:
                logger.info(f"[R2] ✅ Downloaded: {file_size_kb:.2f} KB")
            
            # =====================================================================
            # Формируем путь и загружаем в R2
            # =====================================================================
            now = datetime.utcnow()
            
            # Очищаем call_id от потенциально проблемных символов
            safe_call_id = "".join(c for c in call_id if c.isalnum() or c in "-_")
            if not safe_call_id:
                safe_call_id = f"call_{int(now.timestamp())}"
            
            key = f"recordings/{assistant_id}/{now.year}/{now.month:02d}/{now.day:02d}/{safe_call_id}.mp3"
            
            logger.info(f"[R2] 📤 Uploading to R2...")
            logger.info(f"[R2]    Bucket: {settings.R2_BUCKET}")
            logger.info(f"[R2]    Key: {key}")
            
            # Загружаем в R2
            client.put_object(
                Bucket=settings.R2_BUCKET,
                Key=key,
                Body=audio_data,
                ContentType='audio/mpeg'
            )
            
            # Формируем публичный URL
            public_url = f"{settings.R2_PUBLIC_URL}/{key}"
            
            logger.info(f"[R2] ✅ Upload successful!")
            logger.info(f"[R2]    Public URL: {public_url}")
            logger.info(f"[R2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            
            return public_url
            
        except httpx.HTTPStatusError as e:
            logger.error(f"[R2] ❌ Failed to download from Voximplant: HTTP {e.response.status_code}")
            logger.error(f"[R2]    URL was: {record_url[:80]}...")
            return None
            
        except httpx.TimeoutException:
            logger.error(f"[R2] ❌ Timeout downloading from Voximplant")
            return None
            
        except Exception as e:
            logger.error(f"[R2] ❌ Error uploading recording: {e}")
            import traceback
            logger.error(f"[R2] Traceback: {traceback.format_exc()}")
            return None
    
    @classmethod
    def is_configured(cls) -> bool:
        """Проверяет, настроен ли R2"""
        configured = bool(
            getattr(settings, 'R2_ACCESS_KEY', None) and 
            getattr(settings, 'R2_SECRET_KEY', None) and 
            getattr(settings, 'R2_ENDPOINT', None) and 
            getattr(settings, 'R2_BUCKET', None) and
            getattr(settings, 'R2_PUBLIC_URL', None)
        )
        
        if not configured:
            logger.debug("[R2] Not configured - missing one or more settings")
        
        return configured
    
    @classmethod
    async def delete_recording(cls, key: str) -> bool:
        """
        Удаляет запись из R2.
        
        Args:
            key: Путь к файлу в R2 (например: recordings/xxx/2025/01/18/call123.mp3)
            
        Returns:
            True если удаление успешно
        """
        try:
            client = cls._get_client()
            if not client:
                return False
            
            client.delete_object(
                Bucket=settings.R2_BUCKET,
                Key=key
            )
            
            logger.info(f"[R2] ✅ Deleted: {key}")
            return True
            
        except Exception as e:
            logger.error(f"[R2] ❌ Error deleting {key}: {e}")
            return False
    
    @classmethod
    async def list_recordings(cls, assistant_id: str, limit: int = 100) -> list:
        """
        Получает список записей для ассистента.
        
        Args:
            assistant_id: ID ассистента
            limit: Максимальное количество записей
            
        Returns:
            Список объектов с информацией о записях
        """
        try:
            client = cls._get_client()
            if not client:
                return []
            
            prefix = f"recordings/{assistant_id}/"
            
            response = client.list_objects_v2(
                Bucket=settings.R2_BUCKET,
                Prefix=prefix,
                MaxKeys=limit
            )
            
            recordings = []
            for obj in response.get('Contents', []):
                recordings.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'url': f"{settings.R2_PUBLIC_URL}/{obj['Key']}"
                })
            
            logger.info(f"[R2] Found {len(recordings)} recordings for assistant {assistant_id}")
            return recordings
            
        except Exception as e:
            logger.error(f"[R2] ❌ Error listing recordings: {e}")
            return []
