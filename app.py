"""
FastAPI application initialization for WellcomeAI.
This file configures all application components: routes, middleware, logging, etc.
🆕 v2.0: Added Conversations API support
✅ v2.1: Added Email Verification API support
✅ v2.2: Added Embeds API support (embeddable pages)
✅ v2.3: Added Google Gemini Live API support
✅ v2.4: Added Gemini Assistants CRUD API support
✅ v2.5: Added CRM (Contacts) API support
✅ v2.6: Fixed UTM parameters preservation in redirect
✅ v2.7: Added Task Scheduler for automated calls (simplified startup)
🆕 v3.0: Added xAI Grok Voice Agent API support
"""
import os
import asyncio
import fcntl
import time
import gc
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.core.config import settings
from backend.core.logging import setup_logging, get_logger
from backend.api import (
    auth, users, assistants, files, websocket, healthcheck, 
    subscriptions, subscription_logs, admin, 
    knowledge_base, payments, voximplant, elevenlabs, conversations,
    email_verification,
    embeds,
    gemini_ws,  # ✅ Gemini WebSocket API
    gemini_assistants,  # ✅ Gemini Assistants CRUD API
    grok_ws,  # 🆕 v3.0: Grok WebSocket API
    grok_assistants,  # 🆕 v3.0: Grok Assistants CRUD API
    cartesia_assistants,  # 🆕 v4.0: Cartesia Assistants CRUD API
    yandex_assistants,  # 🆕 Yandex Assistants CRUD API (SpeechKit Realtime)
    fish_assistants,  # 🆕 Fish Assistants CRUD API (Fish Audio TTS)
    fish_ws,  # 🆕 Fish Audio TTS proxy WebSocket
    translate_assistants,  # 🆕 v1.0: Translate Assistants CRUD API
    translate_ws,  # 🆕 v1.0: Translate WebSocket API
    contacts,  # ✅ CRM API
    functions,
    voximplant_settings,
    telephony,
    llm_streaming,  # ✅ LLM Streaming + Agent Config API
    agent,  # ✅ v5.0: Voicyfy Agent API
    agent_telegram,  # ✅ v2.2: Agent Telegram bot integration
    agent_telegram_account,  # ✅ Личный Telegram-аккаунт агента (MTProto)
    credits,  # ✅ Система кредитов оркестратора
)
from backend.models.base import create_tables
from backend.db.session import engine
from backend.core.scheduler import start_subscription_checker
from backend.core.task_scheduler import start_task_scheduler  # ✅ Task Scheduler
from backend.core.telegram_user_poller import start_telegram_user_poller  # ✅ Поллер личного Telegram агента
from backend.services.subscription_blocker import start_subscription_blocker  # ✅ Agent subscription blocker
from backend.api.partners import router as partners_router

# Alembic для миграций
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

# ✅ ОПЦИОНАЛЬНЫЙ ИМПОРТ psutil
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Инициализация модульной системы функций
from backend.functions import discover_functions
discover_functions()

# Setup logging system
setup_logging()
logger = get_logger(__name__)

# Create and configure FastAPI application
app = FastAPI(
    title="WellcomeAI - SaaS Voice Assistant",
    description="API for managing personalized voice assistants based on OpenAI, Google Gemini and xAI Grok",
    version="3.0.0",  # 🆕 Обновлена версия
    docs_url="/api/docs" if not settings.PRODUCTION else None,
    redoc_url="/api/redoc" if not settings.PRODUCTION else None
)

# ============================================================================
# EXCEPTION HANDLERS
# ============================================================================

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # pydantic v2 кладёт в ctx живые объекты исключений (ValueError) — json.dumps
    # на них падал, и клиент вместо JSON 422 получал страницу 500.
    details = []
    for err in exc.errors():
        e = dict(err)
        if e.get("ctx"):
            e["ctx"] = {k: str(v) for k, v in e["ctx"].items()}
        details.append(e)
    logger.warning(f"422 Validation error on {request.method} {request.url.path}: {details}")
    return JSONResponse(
        status_code=422,
        content={"message": "Validation error", "details": details}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "Internal server error"}
    )

# ============================================================================
# MIDDLEWARE
# ============================================================================

# Setup CORS
origins = settings.CORS_ORIGINS.split(",") if isinstance(settings.CORS_ORIGINS, str) else settings.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# ✅ DEBUG: API access log — uvicorn access-логи отключены в logging.py,
# поэтому без этого middleware в логах Render не видно даже факта прихода запроса.
@app.middleware("http")
async def api_access_log(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    start_time = time.monotonic()
    logger.info(f"➡️ {request.method} {request.url.path}")
    try:
        response = await call_next(request)
    except Exception:
        elapsed = time.monotonic() - start_time
        logger.error(f"⬅️ {request.method} {request.url.path} raised after {elapsed:.2f}s", exc_info=True)
        raise
    elapsed = time.monotonic() - start_time
    logger.info(f"⬅️ {request.method} {request.url.path} → {response.status_code} in {elapsed:.2f}s")
    return response

# Resource monitoring middleware (optional, requires psutil)
if PSUTIL_AVAILABLE:
    @app.middleware("http")
    async def monitor_resources(request: Request, call_next):
        """Monitor memory usage for each request"""
        # Пропускаем health checks и статику
        if request.url.path in ["/health", "/api/health"] or request.url.path.startswith("/static") or request.url.path.startswith("/embed"):
            return await call_next(request)
        
        try:
            # Проверяем память перед запросом
            process = psutil.Process()
            memory_before = process.memory_info().rss / 1024 / 1024  # MB
            
            response = await call_next(request)
            
            # Проверяем память после запроса
            memory_after = process.memory_info().rss / 1024 / 1024  # MB
            memory_diff = memory_after - memory_before
            
            # Логируем если использование памяти высокое
            if memory_after > 500:  # 500 MB
                logger.warning(f"High memory usage: {memory_after:.2f} MB (diff: {memory_diff:.2f} MB)")
                # Принудительная сборка мусора
                gc.collect()
            
            # Добавляем заголовок с информацией о памяти (только в development)
            if not settings.PRODUCTION:
                response.headers["X-Memory-Usage"] = f"{memory_after:.2f} MB"
            
            return response
        except Exception as e:
            # НЕ перезапускаем запрос: тело уже прочитано, повторный call_next
            # зависал в ожидании тела на ~10 минут и дублировал выполнение POST.
            logger.error(f"Error in resource monitoring: {e}")
            raise
else:
    logger.warning("psutil not available - memory monitoring disabled")

# ============================================================================
# ROUTE REGISTRATION
# ============================================================================

# Подключаем все API роутеры
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(assistants.router, prefix="/api/assistants", tags=["Assistants"])
app.include_router(gemini_assistants.router, prefix="/api/gemini-assistants", tags=["Gemini Assistants"])
app.include_router(grok_assistants.router, prefix="/api/grok-assistants", tags=["Grok Assistants"])  # 🆕 v3.0
app.include_router(cartesia_assistants.router, prefix="/api/cartesia-assistants", tags=["Cartesia Assistants"])  # 🆕 v4.0
app.include_router(yandex_assistants.router, prefix="/api/yandex-assistants", tags=["Yandex Assistants"])  # 🆕 Yandex SpeechKit Realtime
app.include_router(fish_assistants.router, prefix="/api/fish-assistants", tags=["Fish Assistants"])  # 🆕 Fish Audio TTS
app.include_router(translate_assistants.router, prefix="/api/translate-assistants", tags=["Translate Assistants"])  # 🆕 v1.0
app.include_router(files.router, prefix="/api/files", tags=["Files"])
app.include_router(gemini_ws.router, tags=["Gemini WebSocket"])  # BEFORE websocket.router — /ws/llm-stream must match before /ws/{assistant_id}
app.include_router(translate_ws.router, tags=["Translate WebSocket"])  # BEFORE websocket.router — /ws/translate/{id} must match before /ws/{assistant_id}
app.include_router(fish_ws.router, tags=["Fish TTS WebSocket"])  # BEFORE websocket.router — /ws/fish/tts/{id} must match before /ws/{assistant_id}
app.include_router(websocket.router, tags=["WebSocket"])
app.include_router(grok_ws.router, tags=["Grok WebSocket"])  # 🆕 v3.0
app.include_router(healthcheck.router, tags=["Health"])
app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["Subscriptions"])
app.include_router(subscription_logs.router, prefix="/api/subscription-logs", tags=["Subscription Logs"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(knowledge_base.router, prefix="/api/knowledge-base", tags=["Knowledge Base"])
app.include_router(payments.router, prefix="/api/payments", tags=["Payments"])
app.include_router(voximplant.router, prefix="/api/voximplant", tags=["Voximplant"])
app.include_router(elevenlabs.router, prefix="/api/elevenlabs", tags=["ElevenLabs"])
app.include_router(partners_router, prefix="/api/partners", tags=["Partners"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["Conversations"])
app.include_router(contacts.router, prefix="/api/contacts", tags=["CRM"])
app.include_router(email_verification.router, prefix="/api/email-verification", tags=["Email Verification"])
app.include_router(embeds.router, tags=["Embeds"])
app.include_router(functions.router, prefix="/api/functions", tags=["Functions"])
app.include_router(voximplant_settings.router, prefix="/api/users", tags=["Voximplant Settings"])
app.include_router(telephony.router, prefix="/api/telephony", tags=["Telephony"])
app.include_router(llm_streaming.router, tags=["LLM Streaming"])  # endpoints have /api/llm/ prefix built-in
app.include_router(agent.router, prefix="/api/agent", tags=["Agent"])  # ✅ v5.0: Voicyfy Agent
app.include_router(agent_telegram.router, prefix="/api/agent/telegram", tags=["Agent Telegram"])  # ✅ v2.2
app.include_router(agent_telegram_account.router, prefix="/api/agent/telegram-account", tags=["Agent Telegram Account"])  # ✅ Личный TG-аккаунт агента
app.include_router(credits.router, tags=["Credits"])  # ✅ Кредиты оркестратора (prefix /api/credits встроен)

# ============================================================================
# STATIC FILES
# ============================================================================

def ensure_static_directories():
    """Ensure static directories exist"""
    try:
        static_dir = os.path.join(os.getcwd(), "backend/static")
        if not os.path.exists(static_dir):
            os.makedirs(static_dir, exist_ok=True)
            logger.info(f"Created static directory at {static_dir}")

        js_dir = os.path.join(static_dir, "js")
        if not os.path.exists(js_dir):
            os.makedirs(js_dir, exist_ok=True)
            logger.info(f"Created js directory at {js_dir}")
        
        return static_dir, js_dir
    except Exception as e:
        logger.error(f"Error creating static directories: {e}")
        # Fallback to current directory
        return os.getcwd(), os.getcwd()

static_dir, js_dir = ensure_static_directories()

# Redirect old voice_llm_interface.html to new directory-based interface
@app.get("/static/voice_llm_interface.html")
async def voice_interface_redirect(request: Request):
    """Redirect old single-file URL to new directory-based interface."""
    query_string = str(request.query_params)
    url = "/static/voice_llm_interface/index.html"
    if query_string:
        url += "?" + query_string
    return RedirectResponse(url=url)

# Монтируем статику
try:
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")
    app.mount("/js", StaticFiles(directory=js_dir), name="js")
except Exception as e:
    logger.error(f"Error mounting static files: {e}")

# ============================================================================
# DATABASE INITIALIZATION FUNCTIONS
# ============================================================================

def run_migrations():
    """Run database migrations"""
    try:
        # Проверяем существование alembic.ini
        alembic_ini_path = "alembic.ini"
        if not os.path.exists(alembic_ini_path):
            logger.warning(f"alembic.ini not found at {alembic_ini_path}")
            return
            
        alembic_cfg = AlembicConfig(alembic_ini_path)
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("✅ Database migrations applied successfully")
    except Exception as e:
        logger.error(f"❌ Error applying migrations: {str(e)}")
        # В продакшене не останавливаем приложение из-за ошибок миграции
        if not settings.PRODUCTION:
            raise


def create_elevenlabs_tables():
    """
    Create ElevenLabs tables and automatically add missing columns
    """
    try:
        from backend.models.elevenlabs import ElevenLabsAgent, ElevenLabsConversation
        from backend.models.base import Base
        from sqlalchemy import text, inspect
        
        logger.info("🔄 Creating ElevenLabs tables and checking missing columns...")
        
        # Создаем таблицы ElevenLabs
        Base.metadata.create_all(engine)
        
        # Автоматическая проверка и создание недостающих колонок
        inspector = inspect(engine)
        
        # Список колонок которые должны быть в таблице users
        required_columns = {
            'elevenlabs_api_key': 'VARCHAR NULL',
            # ✅ НОВОЕ v4.0: Webhook настройки для уведомлений о завершённых диалогах
            'webhook_url': 'VARCHAR(500) NULL',
            'webhook_enabled': 'BOOLEAN NOT NULL DEFAULT FALSE',
        }
        
        # Проверяем таблицу users
        try:
            if inspector.has_table('users'):
                columns = inspector.get_columns('users')
                existing_columns = {col['name']: col for col in columns}
                
                logger.info(f"📋 Found {len(existing_columns)} columns in users table")
                
                # Проверяем каждую требуемую колонку
                for column_name, column_definition in required_columns.items():
                    if column_name not in existing_columns:
                        logger.info(f"➕ Adding missing column: {column_name}")
                        
                        try:
                            with engine.connect() as conn:
                                trans = conn.begin()
                                try:
                                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_definition}"))
                                    trans.commit()
                                    logger.info(f"✅ Successfully added column: {column_name}")
                                except Exception as e:
                                    trans.rollback()
                                    if "already exists" not in str(e).lower():
                                        logger.error(f"❌ Failed to add column {column_name}: {str(e)}")
                                    
                        except Exception as conn_error:
                            logger.error(f"❌ Connection error adding column {column_name}: {str(conn_error)}")
                    else:
                        logger.info(f"✅ Column {column_name} already exists")
                        
            else:
                logger.warning("⚠️ Table 'users' not found, skipping column checks")
                
        except Exception as table_error:
            logger.error(f"❌ Error checking users table: {str(table_error)}")
        
        # Проверяем другие возможные недостающие таблицы
        required_tables = {
            'elevenlabs_agents': ElevenLabsAgent,
            'elevenlabs_conversations': ElevenLabsConversation,
        }
        
        for table_name, model_class in required_tables.items():
            if not inspector.has_table(table_name):
                logger.info(f"➕ Creating missing table: {table_name}")
                try:
                    model_class.__table__.create(engine)
                    logger.info(f"✅ Successfully created table: {table_name}")
                except Exception as e:
                    logger.error(f"❌ Failed to create table {table_name}: {str(e)}")
            else:
                logger.info(f"✅ Table {table_name} already exists")
        
        logger.info("✅ ElevenLabs tables and columns setup completed")
        
    except Exception as e:
        logger.error(f"❌ Error creating ElevenLabs tables: {str(e)}")
        if not settings.PRODUCTION:
            raise


def create_gemini_tables():
    """
    Create Gemini assistant tables and check missing columns
    """
    try:
        from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
        from backend.models.base import Base
        from sqlalchemy import text, inspect
        
        logger.info("🤖 Creating Gemini tables and checking missing columns...")
        
        # Создаем таблицы Gemini
        Base.metadata.create_all(engine)
        
        inspector = inspect(engine)
        
        # Проверяем таблицу users для gemini_api_key
        try:
            if inspector.has_table('users'):
                columns = inspector.get_columns('users')
                existing_columns = {col['name']: col for col in columns}
                
                if 'gemini_api_key' not in existing_columns:
                    logger.info("➕ Adding gemini_api_key column to users table...")
                    
                    try:
                        with engine.connect() as conn:
                            trans = conn.begin()
                            try:
                                conn.execute(text("ALTER TABLE users ADD COLUMN gemini_api_key VARCHAR NULL"))
                                trans.commit()
                                logger.info("✅ Successfully added gemini_api_key column")
                            except Exception as e:
                                trans.rollback()
                                if "already exists" not in str(e).lower():
                                    logger.error(f"❌ Failed to add gemini_api_key: {str(e)}")
                    except Exception as conn_error:
                        logger.error(f"❌ Connection error: {str(conn_error)}")
                else:
                    logger.info("✅ Column gemini_api_key already exists")
        except Exception as table_error:
            logger.error(f"❌ Error checking users table: {str(table_error)}")
        
        # Проверяем таблицы Gemini
        required_tables = {
            'gemini_assistant_configs': GeminiAssistantConfig,
            'gemini_conversations': GeminiConversation,
        }
        
        for table_name, model_class in required_tables.items():
            if not inspector.has_table(table_name):
                logger.info(f"➕ Creating missing table: {table_name}")
                try:
                    model_class.__table__.create(engine)
                    logger.info(f"✅ Successfully created table: {table_name}")
                except Exception as e:
                    logger.error(f"❌ Failed to create table {table_name}: {str(e)}")
            else:
                logger.info(f"✅ Table {table_name} already exists")
        
        logger.info("✅ Gemini tables and columns setup completed")
        
    except Exception as e:
        logger.error(f"❌ Error creating Gemini tables: {str(e)}")
        if not settings.PRODUCTION:
            raise


def create_grok_tables():
    """
    🆕 v3.0: Create Grok assistant tables and check missing columns
    """
    try:
        from backend.models.grok_assistant import GrokAssistantConfig, GrokConversation
        from backend.models.base import Base
        from sqlalchemy import text, inspect
        
        logger.info("🤖 Creating Grok tables and checking missing columns...")
        
        # Создаем таблицы Grok
        Base.metadata.create_all(engine)
        
        inspector = inspect(engine)
        
        # Проверяем таблицу users для grok_api_key
        try:
            if inspector.has_table('users'):
                columns = inspector.get_columns('users')
                existing_columns = {col['name']: col for col in columns}
                
                if 'grok_api_key' not in existing_columns:
                    logger.info("➕ Adding grok_api_key column to users table...")
                    
                    try:
                        with engine.connect() as conn:
                            trans = conn.begin()
                            try:
                                conn.execute(text("ALTER TABLE users ADD COLUMN grok_api_key VARCHAR NULL"))
                                trans.commit()
                                logger.info("✅ Successfully added grok_api_key column")
                            except Exception as e:
                                trans.rollback()
                                if "already exists" not in str(e).lower():
                                    logger.error(f"❌ Failed to add grok_api_key: {str(e)}")
                    except Exception as conn_error:
                        logger.error(f"❌ Connection error: {str(conn_error)}")
                else:
                    logger.info("✅ Column grok_api_key already exists")
        except Exception as table_error:
            logger.error(f"❌ Error checking users table: {str(table_error)}")
        
        # Проверяем таблицы Grok
        required_tables = {
            'grok_assistant_configs': GrokAssistantConfig,
            'grok_conversations': GrokConversation,
        }
        
        for table_name, model_class in required_tables.items():
            if not inspector.has_table(table_name):
                logger.info(f"➕ Creating missing table: {table_name}")
                try:
                    model_class.__table__.create(engine)
                    logger.info(f"✅ Successfully created table: {table_name}")
                except Exception as e:
                    logger.error(f"❌ Failed to create table {table_name}: {str(e)}")
            else:
                logger.info(f"✅ Table {table_name} already exists")
        
        logger.info("✅ Grok tables and columns setup completed")

    except Exception as e:
        logger.error(f"❌ Error creating Grok tables: {str(e)}")
        if not settings.PRODUCTION:
            raise


def create_cartesia_tables():
    """
    Create Cartesia assistant tables and check missing columns
    """
    try:
        from backend.models.cartesia_assistant import CartesiaAssistantConfig
        from backend.models.base import Base
        from sqlalchemy import text, inspect

        logger.info("🎵 Creating Cartesia tables and checking missing columns...")

        # Создаем таблицы Cartesia
        Base.metadata.create_all(engine)

        inspector = inspect(engine)

        # Проверяем таблицу users для cartesia_api_key
        try:
            if inspector.has_table('users'):
                columns = inspector.get_columns('users')
                existing_columns = {col['name']: col for col in columns}

                if 'cartesia_api_key' not in existing_columns:
                    logger.info("➕ Adding cartesia_api_key column to users table...")

                    try:
                        with engine.connect() as conn:
                            trans = conn.begin()
                            try:
                                conn.execute(text("ALTER TABLE users ADD COLUMN cartesia_api_key VARCHAR NULL"))
                                trans.commit()
                                logger.info("✅ Successfully added cartesia_api_key column")
                            except Exception as e:
                                trans.rollback()
                                if "already exists" not in str(e).lower():
                                    logger.error(f"❌ Failed to add cartesia_api_key: {str(e)}")
                    except Exception as conn_error:
                        logger.error(f"❌ Connection error: {str(conn_error)}")
                else:
                    logger.info("✅ Column cartesia_api_key already exists")
        except Exception as table_error:
            logger.error(f"❌ Error checking users table: {str(table_error)}")

        # Проверяем таблицу cartesia_assistant_configs
        if not inspector.has_table('cartesia_assistant_configs'):
            logger.info("➕ Creating missing table: cartesia_assistant_configs")
            try:
                CartesiaAssistantConfig.__table__.create(engine)
                logger.info("✅ Successfully created table: cartesia_assistant_configs")
            except Exception as e:
                logger.error(f"❌ Failed to create table cartesia_assistant_configs: {str(e)}")
        else:
            logger.info("✅ Table cartesia_assistant_configs already exists")

        logger.info("✅ Cartesia tables and columns setup completed")

    except Exception as e:
        logger.error(f"❌ Error creating Cartesia tables: {str(e)}")
        if not settings.PRODUCTION:
            raise


def create_fish_tables():
    """
    Create Fish assistant tables and check missing columns.

    Fish Audio — TTS-провайдер: диалог ведёт OpenAI Realtime в сценарии
    Voximplant, озвучка идёт через прокси /ws/fish/tts/{assistant_id}.
    """
    try:
        from backend.models.fish_assistant import FishAssistantConfig
        from backend.models.base import Base
        from sqlalchemy import text, inspect

        logger.info("🐟 Creating Fish tables and checking missing columns...")

        # Создаем таблицы Fish
        Base.metadata.create_all(engine)

        inspector = inspect(engine)

        # Проверяем таблицу users для fish_api_key
        try:
            if inspector.has_table('users'):
                columns = inspector.get_columns('users')
                existing_columns = {col['name']: col for col in columns}

                if 'fish_api_key' not in existing_columns:
                    logger.info("➕ Adding fish_api_key column to users table...")

                    try:
                        with engine.connect() as conn:
                            trans = conn.begin()
                            try:
                                conn.execute(text("ALTER TABLE users ADD COLUMN fish_api_key VARCHAR NULL"))
                                trans.commit()
                                logger.info("✅ Successfully added fish_api_key column")
                            except Exception as e:
                                trans.rollback()
                                if "already exists" not in str(e).lower():
                                    logger.error(f"❌ Failed to add fish_api_key: {str(e)}")
                    except Exception as conn_error:
                        logger.error(f"❌ Connection error: {str(conn_error)}")
                else:
                    logger.info("✅ Column fish_api_key already exists")
        except Exception as table_error:
            logger.error(f"❌ Error checking users table: {str(table_error)}")

        # Проверяем таблицы Fish
        if not inspector.has_table('fish_assistant_configs'):
            logger.info("➕ Creating missing table: fish_assistant_configs")
            try:
                FishAssistantConfig.__table__.create(engine)
                logger.info("✅ Successfully created table: fish_assistant_configs")
            except Exception as e:
                logger.error(f"❌ Failed to create table fish_assistant_configs: {str(e)}")
        else:
            logger.info("✅ Table fish_assistant_configs already exists")

        logger.info("✅ Fish tables and columns setup completed")

    except Exception as e:
        logger.error(f"❌ Error creating Fish tables: {str(e)}")
        if not settings.PRODUCTION:
            raise


def create_yandex_tables():
    """
    Create Yandex assistant tables and check missing columns
    """
    try:
        from backend.models.yandex_assistant import YandexAssistantConfig, YandexConversation
        from backend.models.base import Base
        from sqlalchemy import text, inspect

        logger.info("🟡 Creating Yandex tables and checking missing columns...")

        # Создаем таблицы Yandex
        Base.metadata.create_all(engine)

        inspector = inspect(engine)

        # Проверяем таблицу users для yandex_api_key и yandex_folder_id
        try:
            if inspector.has_table('users'):
                columns = inspector.get_columns('users')
                existing_columns = {col['name']: col for col in columns}

                for column_name, column_definition in (
                    ('yandex_api_key', 'VARCHAR NULL'),
                    ('yandex_folder_id', 'VARCHAR(100) NULL'),
                ):
                    if column_name not in existing_columns:
                        logger.info(f"➕ Adding {column_name} column to users table...")

                        try:
                            with engine.connect() as conn:
                                trans = conn.begin()
                                try:
                                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_definition}"))
                                    trans.commit()
                                    logger.info(f"✅ Successfully added {column_name} column")
                                except Exception as e:
                                    trans.rollback()
                                    if "already exists" not in str(e).lower():
                                        logger.error(f"❌ Failed to add {column_name}: {str(e)}")
                        except Exception as conn_error:
                            logger.error(f"❌ Connection error: {str(conn_error)}")
                    else:
                        logger.info(f"✅ Column {column_name} already exists")
        except Exception as table_error:
            logger.error(f"❌ Error checking users table: {str(table_error)}")

        # Проверяем таблицы Yandex
        required_tables = {
            'yandex_assistant_configs': YandexAssistantConfig,
            'yandex_conversations': YandexConversation,
        }

        for table_name, model_class in required_tables.items():
            if not inspector.has_table(table_name):
                logger.info(f"➕ Creating missing table: {table_name}")
                try:
                    model_class.__table__.create(engine)
                    logger.info(f"✅ Successfully created table: {table_name}")
                except Exception as e:
                    logger.error(f"❌ Failed to create table {table_name}: {str(e)}")
            else:
                logger.info(f"✅ Table {table_name} already exists")

        logger.info("✅ Yandex tables and columns setup completed")

    except Exception as e:
        logger.error(f"❌ Error creating Yandex tables: {str(e)}")
        if not settings.PRODUCTION:
            raise


def create_crm_tables():
    """
    Create CRM (Contacts) tables and check missing columns
    """
    try:
        from backend.models.contact import Contact
        from backend.models.base import Base
        from sqlalchemy import text, inspect
        
        logger.info("📇 Creating CRM tables and checking missing columns...")
        
        # Создаем таблицы CRM
        Base.metadata.create_all(engine)
        
        inspector = inspect(engine)
        
        # Проверяем таблицу contacts
        if not inspector.has_table('contacts'):
            logger.info("➕ Creating contacts table...")
            try:
                Contact.__table__.create(engine)
                logger.info("✅ Successfully created contacts table")
            except Exception as e:
                logger.error(f"❌ Failed to create contacts table: {str(e)}")
        else:
            logger.info("✅ Table contacts already exists")
        
        # Проверяем поле contact_id в таблице conversations
        try:
            if inspector.has_table('conversations'):
                columns = inspector.get_columns('conversations')
                existing_columns = {col['name']: col for col in columns}
                
                if 'contact_id' not in existing_columns:
                    logger.info("➕ Adding contact_id column to conversations table...")
                    
                    try:
                        with engine.connect() as conn:
                            trans = conn.begin()
                            try:
                                # Добавляем колонку без FK constraint
                                conn.execute(text("ALTER TABLE conversations ADD COLUMN contact_id UUID"))
                                trans.commit()
                                logger.info("✅ Successfully added contact_id column")
                                
                                # Создаем индекс
                                trans = conn.begin()
                                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_contact_id ON conversations(contact_id)"))
                                trans.commit()
                                logger.info("✅ Successfully created index on contact_id")
                                
                                # Добавляем FK constraint с NOT VALID (для больших таблиц)
                                trans = conn.begin()
                                conn.execute(text("""
                                    ALTER TABLE conversations 
                                    ADD CONSTRAINT fk_conversations_contact_id 
                                    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
                                    NOT VALID
                                """))
                                trans.commit()
                                logger.info("✅ Successfully added FK constraint on contact_id")
                                
                            except Exception as e:
                                trans.rollback()
                                if "already exists" not in str(e).lower():
                                    logger.error(f"❌ Failed to add contact_id: {str(e)}")
                    except Exception as conn_error:
                        logger.error(f"❌ Connection error: {str(conn_error)}")
                else:
                    logger.info("✅ Column contact_id already exists in conversations")
        except Exception as table_error:
            logger.error(f"❌ Error checking conversations table: {str(table_error)}")
        
        logger.info("✅ CRM tables and columns setup completed")
        
    except Exception as e:
        logger.error(f"❌ Error creating CRM tables: {str(e)}")
        if not settings.PRODUCTION:
            raise


def check_and_fix_all_missing_columns():
    """
    Comprehensive check and fix for all missing columns across all tables
    """
    try:
        from sqlalchemy import text, inspect
        
        logger.info("🔧 Comprehensive database schema check and fix...")
        
        inspector = inspect(engine)
        
        # Карта всех таблиц и их обязательных колонок
        schema_fixes = {
            'users': {
                'elevenlabs_api_key': 'VARCHAR NULL',
                'gemini_api_key': 'VARCHAR NULL',
                'grok_api_key': 'VARCHAR NULL',  # 🆕 v3.0
                'cartesia_api_key': 'VARCHAR NULL',  # 🆕 v4.0
                'openrouter_api_key': 'VARCHAR(255) NULL',  # 🆕 Cascade
                'yandex_api_key': 'VARCHAR NULL',  # 🆕 Yandex Cloud API key
                'yandex_folder_id': 'VARCHAR(100) NULL',  # 🆕 Yandex Cloud folder ID
                'fish_api_key': 'VARCHAR NULL',  # 🆕 Fish Audio TTS API key
                'email_verified': 'BOOLEAN DEFAULT FALSE NOT NULL',
                # 🆕 Система кредитов оркестратора Voicyfy Agent
                'credits_balance': 'INTEGER DEFAULT 0 NOT NULL',
                'agent_trial_used': 'BOOLEAN DEFAULT FALSE NOT NULL',
                'agent_trial_started_at': 'TIMESTAMP WITH TIME ZONE NULL',
                'agent_subscription_blocked': 'BOOLEAN DEFAULT FALSE NOT NULL',
                # 🆕 Кредиты каскад-ассистентов (LLM gpt-realtime-2.1-mini на серверном ключе)
                'cascade_credits_balance': 'INTEGER DEFAULT 0 NOT NULL',
                'cascade_trial_granted': 'BOOLEAN DEFAULT FALSE NOT NULL',
                # 🆕 Персональный API-ключ Voicyfy (внешние интеграции, Claude Code)
                'api_key_hash': 'VARCHAR(64) NULL',
                'api_key_prefix': 'VARCHAR(20) NULL',
                'api_key_created_at': 'TIMESTAMP WITH TIME ZONE NULL',
            },
            'conversations': {
                'caller_number': 'VARCHAR(50) NULL',
                'contact_id': 'UUID NULL',
            },
            'grok_assistant_configs': {
                'assistant_type': "VARCHAR(20) DEFAULT 'grok' NOT NULL",
                'openrouter_model': 'VARCHAR(150) NULL',
                'tts_provider': 'VARCHAR(30) NULL',
                'tts_voice': 'VARCHAR(100) NULL',
                'tts_lang': "VARCHAR(10) DEFAULT 'ru' NOT NULL",
                'asr_lang': "VARCHAR(10) DEFAULT 'ru' NOT NULL",
                # 🆕 Пауза перед ответом каскад-агента (пресет 300/650/1000 мс)
                'silence_duration_ms': 'INTEGER DEFAULT 300 NULL',
            },
            'assistant_configs': {
                # Поздние колонки, которых нет в старых alembic-миграциях
                # (свежая БД клона: SELECT падал на enable_vision)
                'google_sheet_id': 'VARCHAR NULL',
                'greeting_message': 'VARCHAR NULL',
                'enable_vision': 'BOOLEAN DEFAULT FALSE NOT NULL',
                'is_public': 'BOOLEAN DEFAULT FALSE NOT NULL',
                'total_conversations': 'INTEGER DEFAULT 0 NOT NULL',
                'temperature': 'FLOAT DEFAULT 0.7 NOT NULL',
                'max_tokens': 'INTEGER DEFAULT 1000 NOT NULL',
                'functions': 'JSON NULL',
            },
            'tasks': {
                'caller_id': 'VARCHAR(20) NULL',
                # 🆕 Канал агентской задачи: call (звонок) / telegram (отложенное
                # сообщение с личного Telegram-аккаунта агента)
                'channel': "VARCHAR(20) DEFAULT 'call' NOT NULL",
            },
            'voximplant_phone_numbers': {
                # 🆕 Привязка номера к автономному агенту (PostCall для входящих)
                'agent_config_id': 'UUID NULL',
            },
            'agent_calls': {
                # 🆕 Направление звонка: outbound / inbound (для UI агента)
                'direction': "VARCHAR(20) DEFAULT 'outbound' NOT NULL",
            },
            'subscription_plans': {
                # Добавьте если нужно
            },
            # 🆕 Дискриминатор продукта для раздельного учёта кредитов
            # оркестратора ('orchestrator') и каскада ('cascade').
            'credit_transactions': {
                'product': "VARCHAR(20) DEFAULT 'orchestrator' NOT NULL",
            },
            'credit_packages': {
                'product': "VARCHAR(20) DEFAULT 'orchestrator' NOT NULL",
            },
        }
        
        for table_name, required_columns in schema_fixes.items():
            if not inspector.has_table(table_name):
                logger.warning(f"⚠️ Table {table_name} not found, skipping")
                continue
                
            logger.info(f"🔍 Checking table: {table_name}")
            
            columns = inspector.get_columns(table_name)
            existing_columns = {col['name'] for col in columns}
            
            for column_name, column_definition in required_columns.items():
                if column_name not in existing_columns:
                    logger.info(f"➕ Adding missing column {table_name}.{column_name}")
                    
                    try:
                        with engine.connect() as conn:
                            trans = conn.begin()
                            try:
                                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"))
                                trans.commit()
                                logger.info(f"✅ Successfully added {table_name}.{column_name}")
                            except Exception as e:
                                trans.rollback()
                                if "already exists" in str(e).lower():
                                    logger.info(f"ℹ️  Column {table_name}.{column_name} already exists")
                                else:
                                    logger.error(f"❌ Failed to add {table_name}.{column_name}: {str(e)}")
                                    
                    except Exception as conn_error:
                        logger.error(f"❌ Connection error adding {table_name}.{column_name}: {str(conn_error)}")
                else:
                    logger.debug(f"✅ Column {table_name}.{column_name} exists")
        
        logger.info("✅ Comprehensive schema check completed")
        
    except Exception as e:
        logger.error(f"❌ Error in comprehensive schema check: {str(e)}")


def create_email_verification_table():
    """
    Create email_verifications table if it doesn't exist
    """
    try:
        from backend.models.email_verification import EmailVerification
        from backend.models.base import Base
        from sqlalchemy import inspect
        
        logger.info("📧 Checking email_verifications table...")
        
        inspector = inspect(engine)
        
        if not inspector.has_table('email_verifications'):
            logger.info("➕ Creating email_verifications table...")
            EmailVerification.__table__.create(engine)
            logger.info("✅ email_verifications table created successfully")
        else:
            logger.info("✅ email_verifications table already exists")
            
    except Exception as e:
        logger.error(f"❌ Error creating email_verifications table: {str(e)}")
        if not settings.PRODUCTION:
            raise


def create_embed_configs_table():
    """
    Create embed_configs table if it doesn't exist
    
    This table stores configurations for embeddable pages.
    """
    try:
        from backend.models.embed_config import EmbedConfig
        from backend.models.base import Base
        from sqlalchemy import inspect, text
        
        logger.info("🎨 Checking embed_configs table...")
        
        inspector = inspect(engine)
        
        if not inspector.has_table('embed_configs'):
            logger.info("➕ Creating embed_configs table...")
            EmbedConfig.__table__.create(engine)
            logger.info("✅ embed_configs table created successfully")
            
            # Создаем функцию генерации кодов и триггер
            logger.info("➕ Creating embed_code generator function and trigger...")
            
            try:
                with engine.connect() as conn:
                    trans = conn.begin()
                    try:
                        # Функция генерации кода
                        conn.execute(text("""
                            CREATE OR REPLACE FUNCTION generate_embed_code() 
                            RETURNS TEXT AS $$
                            DECLARE
                                new_code TEXT;
                                code_exists BOOLEAN;
                            BEGIN
                                LOOP
                                    new_code := 'w_' || substr(md5(random()::text || clock_timestamp()::text), 1, 12);
                                    SELECT EXISTS(SELECT 1 FROM embed_configs WHERE embed_code = new_code) INTO code_exists;
                                    EXIT WHEN NOT code_exists;
                                END LOOP;
                                RETURN new_code;
                            END;
                            $$ LANGUAGE plpgsql;
                        """))
                        
                        # Триггер функция
                        conn.execute(text("""
                            CREATE OR REPLACE FUNCTION set_embed_code() 
                            RETURNS TRIGGER AS $$
                            BEGIN
                                IF NEW.embed_code IS NULL OR NEW.embed_code = '' THEN
                                    NEW.embed_code := generate_embed_code();
                                END IF;
                                RETURN NEW;
                            END;
                            $$ LANGUAGE plpgsql;
                        """))
                        
                        # Триггер
                        conn.execute(text("""
                            DROP TRIGGER IF EXISTS trigger_set_embed_code ON embed_configs;
                        """))
                        
                        conn.execute(text("""
                            CREATE TRIGGER trigger_set_embed_code
                            BEFORE INSERT ON embed_configs
                            FOR EACH ROW
                            EXECUTE FUNCTION set_embed_code();
                        """))
                        
                        trans.commit()
                        logger.info("✅ Embed code generator and trigger created successfully")
                        
                    except Exception as e:
                        trans.rollback()
                        logger.error(f"❌ Failed to create generator/trigger: {str(e)}")
                        
            except Exception as conn_error:
                logger.error(f"❌ Connection error creating functions: {str(conn_error)}")
                
        else:
            logger.info("✅ embed_configs table already exists")
            
    except Exception as e:
        logger.error(f"❌ Error creating embed_configs table: {str(e)}")
        if not settings.PRODUCTION:
            raise

# ============================================================================
# APPLICATION LIFECYCLE EVENTS
# ============================================================================

def seed_credits_data():
    """
    Идемпотентно засеять данные системы кредитов: тариф `agent` и пакеты докупки.
    ТЗ предполагает ручную подготовку БД, но сидинг делает фичу самовосстанавливающейся.
    """
    try:
        from sqlalchemy import text, inspect
        logger.info("🌱 Seeding credits data (agent plan + packages)...")

        inspector = inspect(engine)
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                # Тариф agent (subscription_plans уже существует).
                # ⚠️ Цена (в сомах, KGS) задаётся ТОЛЬКО при первой вставке и далее
                # управляется через БД (UPDATE subscription_plans SET price=...) —
                # сидинг её больше НЕ перезатирает. Сейчас минимальная тестовая цена.
                if inspector.has_table('subscription_plans'):
                    conn.execute(text("""
                        INSERT INTO subscription_plans (code, name, price, max_assistants, description, is_active)
                        VALUES ('agent', 'Voicyfy Agent', 50, 3, 'AI-оркестратор автономных звонков', TRUE)
                        ON CONFLICT (code) DO UPDATE SET
                            max_assistants = EXCLUDED.max_assistants
                    """))

                # Пакеты докупки кредитов оркестратора
                if inspector.has_table('credit_packages'):
                    conn.execute(text("""
                        INSERT INTO credit_packages (code, product, name, credits, price_rub, sort_order, is_active) VALUES
                            ('credits_mini', 'orchestrator', 'Mini', 5000, 490, 1, TRUE),
                            ('credits_standard', 'orchestrator', 'Standard', 15000, 1290, 2, TRUE),
                            ('credits_pro', 'orchestrator', 'Pro', 50000, 3990, 3, TRUE),
                            ('credits_business', 'orchestrator', 'Business', 150000, 9990, 4, TRUE),
                            ('credits_enterprise', 'orchestrator', 'Enterprise', 500000, 29990, 5, TRUE)
                        ON CONFLICT (code) DO NOTHING
                    """))

                    # 🆕 Пакеты докупки кредитов каскад-ассистентов (product='cascade').
                    # Та же единица кредита (1 кредит = $0.0001 ×2), те же цены.
                    conn.execute(text("""
                        INSERT INTO credit_packages (code, product, name, credits, price_rub, sort_order, is_active) VALUES
                            ('cascade_mini', 'cascade', 'Mini', 5000, 490, 1, TRUE),
                            ('cascade_standard', 'cascade', 'Standard', 15000, 1290, 2, TRUE),
                            ('cascade_pro', 'cascade', 'Pro', 50000, 3990, 3, TRUE),
                            ('cascade_business', 'cascade', 'Business', 150000, 9990, 4, TRUE),
                            ('cascade_enterprise', 'cascade', 'Enterprise', 500000, 29990, 5, TRUE)
                        ON CONFLICT (code) DO NOTHING
                    """))

                trans.commit()
                logger.info("✅ Credits data seeded")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to seed credits data: {e}")
    except Exception as e:
        logger.error(f"❌ seed_credits_data error: {e}")


def backfill_cascade_trial_credits():
    """
    Разовый массовый грант тестовых кредитов каскада (1500) ВСЕМ существующим
    пользователям. Идемпотентно по флагу users.cascade_trial_granted — уже
    начисленным повторно не выдаём. Каждое начисление фиксируется в
    credit_transactions (product='cascade', type='trial_grant').

    Новые пользователи получают грант лениво при первом обращении к балансу
    каскада (CascadeCreditService.grant_trial в /cascade/credits/balance).
    """
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        if not inspector.has_table('users') or not inspector.has_table('credit_transactions'):
            return

        user_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'cascade_trial_granted' not in user_cols or 'cascade_credits_balance' not in user_cols:
            logger.warning("⚠️ cascade credit columns not present yet, skip trial backfill")
            return

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                pending = conn.execute(text(
                    "SELECT COUNT(*) FROM users WHERE cascade_trial_granted = FALSE"
                )).scalar() or 0
                if pending == 0:
                    trans.commit()
                    logger.info("✅ Cascade trial backfill: nothing to grant")
                    return

                # 1) Транзакции начисления (balance_after = баланс ПОСЛЕ гранта).
                conn.execute(text("""
                    INSERT INTO credit_transactions
                        (id, user_id, product, type, amount, balance_after, ref_type, notes, created_at)
                    SELECT gen_random_uuid(), id, 'cascade', 'trial_grant', 300,
                           COALESCE(cascade_credits_balance, 0) + 300, 'cascade_trial',
                           'Cascade trial grant: 300 credits (mass backfill)', now()
                    FROM users
                    WHERE cascade_trial_granted = FALSE
                """))
                # 2) Начисляем баланс и ставим флаг.
                conn.execute(text("""
                    UPDATE users
                    SET cascade_credits_balance = COALESCE(cascade_credits_balance, 0) + 300,
                        cascade_trial_granted = TRUE
                    WHERE cascade_trial_granted = FALSE
                """))
                trans.commit()
                logger.info(f"✅ Cascade trial backfill: granted 300 credits to {pending} users")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Cascade trial backfill failed: {e}")
    except Exception as e:
        logger.error(f"❌ backfill_cascade_trial_credits error: {e}")


def ensure_cascade_credit_packages():
    """
    Гарантированно засеять пакеты докупки кредитов каскада (product='cascade').

    Вынесено из seed_credits_data в ОТДЕЛЬНУЮ транзакцию, чтобы вставка cascade-
    пакетов не откатывалась из-за ошибки в неродственных statements общего сидинга.
    Идемпотентно (ON CONFLICT DO NOTHING). Логирует итоговое число cascade-пакетов.
    """
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        if not inspector.has_table('credit_packages'):
            logger.warning("⚠️ credit_packages table missing, skip cascade packages seed")
            return

        # Защитно гарантируем колонку product (на случай если schema-fix не отработал).
        cols = {c['name'] for c in inspector.get_columns('credit_packages')}
        with engine.connect() as conn:
            if 'product' not in cols:
                trans = conn.begin()
                try:
                    conn.execute(text(
                        "ALTER TABLE credit_packages ADD COLUMN product VARCHAR(20) "
                        "DEFAULT 'orchestrator' NOT NULL"
                    ))
                    trans.commit()
                    logger.info("➕ Added credit_packages.product column")
                except Exception as e:
                    trans.rollback()
                    logger.info(f"ℹ️ credit_packages.product add skipped: {e}")

            trans = conn.begin()
            try:
                conn.execute(text("""
                    INSERT INTO credit_packages (code, product, name, credits, price_rub, sort_order, is_active) VALUES
                        ('cascade_mini', 'cascade', 'Mini', 5000, 490, 1, TRUE),
                        ('cascade_standard', 'cascade', 'Standard', 15000, 1290, 2, TRUE),
                        ('cascade_pro', 'cascade', 'Pro', 50000, 3990, 3, TRUE),
                        ('cascade_business', 'cascade', 'Business', 150000, 9990, 4, TRUE),
                        ('cascade_enterprise', 'cascade', 'Enterprise', 500000, 29990, 5, TRUE)
                    ON CONFLICT (code) DO NOTHING
                """))
                trans.commit()
                cnt = conn.execute(text(
                    "SELECT COUNT(*) FROM credit_packages WHERE product = 'cascade' AND is_active = TRUE"
                )).scalar() or 0
                logger.info(f"✅ Cascade packages ensured (active cascade packages: {cnt})")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to ensure cascade packages: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_cascade_credit_packages error: {e}")


def normalize_agent_contact_stages():
    """
    Разовая нормализация стадий воронки агентских контактов.

    Статус "calling" больше не является стадией контакта (факт «идёт звонок»
    хранится в AgentCall/Task). Старые записи, застрявшие в "calling" из-за
    прежней логики, переводим в "active" ("В работе"), чтобы они корректно
    отображались в канбане воронки.
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('agent_contacts'):
            return

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                result = conn.execute(text(
                    "UPDATE agent_contacts SET status = 'active' WHERE status = 'calling'"
                ))
                trans.commit()
                if result.rowcount:
                    logger.info(f"✅ Normalized {result.rowcount} agent contacts: calling → active")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to normalize agent contact stages: {e}")
    except Exception as e:
        logger.error(f"❌ normalize_agent_contact_stages error: {e}")


def ensure_agent_voice_instructions_column():
    """
    Идемпотентно добавляет колонку voice_additional_instructions в agent_configs.

    Дублирует alembic-миграцию add_voice_instructions на случай, если миграции
    не применились (в продакшене ошибки alembic проглатываются).
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('agent_configs'):
            return

        existing = {c['name'] for c in inspector.get_columns('agent_configs')}
        if 'voice_additional_instructions' in existing:
            return

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text(
                    "ALTER TABLE agent_configs "
                    "ADD COLUMN IF NOT EXISTS voice_additional_instructions TEXT"
                ))
                trans.commit()
                logger.info("✅ Added column agent_configs.voice_additional_instructions")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to add voice_additional_instructions column: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_voice_instructions_column error: {e}")


def ensure_agent_cascade_voice_columns():
    """
    Идемпотентно добавляет FK-колонки каскад-голоса:
      • agent_configs.cascade_assistant_id  → grok_assistant_configs
      • tasks.cascade_assistant_id          → grok_assistant_configs
    Позволяет автономному агенту использовать каскад как голосовой провайдер.
    """
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        stmts = []
        if inspector.has_table('agent_configs'):
            cols = {c['name'] for c in inspector.get_columns('agent_configs')}
            if 'cascade_assistant_id' not in cols:
                stmts.append(
                    "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS "
                    "cascade_assistant_id UUID REFERENCES grok_assistant_configs(id) ON DELETE SET NULL"
                )
        if inspector.has_table('tasks'):
            cols = {c['name'] for c in inspector.get_columns('tasks')}
            if 'cascade_assistant_id' not in cols:
                stmts.append(
                    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                    "cascade_assistant_id UUID REFERENCES grok_assistant_configs(id)"
                )
        if not stmts:
            return
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for s in stmts:
                    conn.execute(text(s))
                trans.commit()
                logger.info(f"✅ Added cascade voice FK columns ({len(stmts)})")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to add cascade voice FK columns: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_cascade_voice_columns error: {e}")


def ensure_agent_fish_voice_columns():
    """
    Идемпотентно добавляет FK-колонки fish-голоса:
      • agent_configs.fish_assistant_id  → fish_assistant_configs
      • tasks.fish_assistant_id          → fish_assistant_configs
    Позволяет агенту обзвона использовать Fish Audio как голосовой провайдер.
    """
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        if not inspector.has_table('fish_assistant_configs'):
            return
        stmts = []
        if inspector.has_table('agent_configs'):
            cols = {c['name'] for c in inspector.get_columns('agent_configs')}
            if 'fish_assistant_id' not in cols:
                stmts.append(
                    "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS "
                    "fish_assistant_id UUID REFERENCES fish_assistant_configs(id) ON DELETE SET NULL"
                )
        if inspector.has_table('tasks'):
            cols = {c['name'] for c in inspector.get_columns('tasks')}
            if 'fish_assistant_id' not in cols:
                stmts.append(
                    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                    "fish_assistant_id UUID REFERENCES fish_assistant_configs(id) ON DELETE SET NULL"
                )
        if not stmts:
            return
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for s in stmts:
                    conn.execute(text(s))
                trans.commit()
                logger.info(f"✅ Added fish voice FK columns ({len(stmts)})")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to add fish voice FK columns: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_fish_voice_columns error: {e}")


def ensure_task_model_columns():
    """
    Идемпотентно досоздаёт в tasks колонки, появившиеся позже старых
    alembic-миграций. Свежая БД (клон) создаёт tasks по старой схеме, а
    Base.metadata.create_all существующую таблицу не изменяет — планировщик
    падал на несуществующей tasks.cartesia_assistant_id каждые 30 секунд.
    FK-колонки cascade/fish/yandex добавляют свои ensure-шаги — здесь только
    cartesia и агентно-оркестраторные поля.
    """
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        if not inspector.has_table('tasks'):
            return
        cols = {c['name'] for c in inspector.get_columns('tasks')}
        defs = []
        if 'cartesia_assistant_id' not in cols and inspector.has_table('cartesia_assistant_configs'):
            defs.append("cartesia_assistant_id UUID REFERENCES cartesia_assistant_configs(id) ON DELETE SET NULL")
        if 'agent_contact_id' not in cols and inspector.has_table('agent_contacts'):
            defs.append("agent_contact_id UUID REFERENCES agent_contacts(id) ON DELETE SET NULL")
        if 'agent_call_id' not in cols and inspector.has_table('agent_calls'):
            defs.append("agent_call_id UUID REFERENCES agent_calls(id) ON DELETE SET NULL")
        if 'is_agent_task' not in cols:
            defs.append("is_agent_task BOOLEAN DEFAULT FALSE NOT NULL")
        if 'pre_call_response_id' not in cols:
            defs.append("pre_call_response_id VARCHAR(255)")
        if 'post_call_decision' not in cols:
            defs.append("post_call_decision VARCHAR(50)")
        if 'retry_count' not in cols:
            defs.append("retry_count INTEGER DEFAULT 0 NOT NULL")
        if 'custom_greeting' not in cols:
            defs.append("custom_greeting TEXT")
        if 'call_session_id' not in cols:
            defs.append("call_session_id VARCHAR(255)")
        if 'call_started_at' not in cols:
            defs.append("call_started_at TIMESTAMP WITH TIME ZONE")
        if 'call_completed_at' not in cols:
            defs.append("call_completed_at TIMESTAMP WITH TIME ZONE")
        if 'call_result' not in cols:
            defs.append("call_result TEXT")
        if not defs:
            return
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for d in defs:
                    conn.execute(text(f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {d}"))
                trans.commit()
                added = [d.split()[0] for d in defs]
                logger.info(f"✅ Added missing tasks columns ({len(defs)}): {added}")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to add missing tasks columns: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_task_model_columns error: {e}")


def ensure_all_model_columns():
    """
    Универсальный доводчик схемы: сравнивает колонки ВСЕХ ORM-моделей с БД
    и добавляет недостающие. Старые alembic-миграции клона создают таблицы
    без поздних колонок, а create_all существующие таблицы не меняет — из-за
    этого SELECT падал на tasks.cartesia_assistant_id, assistant_configs.
    enable_vision, gemini_assistant_configs.model и т.д. (по одной на релиз).

    NOT NULL-колонки добавляются с DEFAULT из модели, если он скалярный;
    иначе колонка добавляется nullable — цель «работает», а не идеальная
    схема. FK-констрейнты здесь не создаются (их добавляют специализированные
    ensure-шаги выше — этот запускается после них и только досоздаёт хвосты).
    """
    try:
        from sqlalchemy import text, inspect
        from backend.models.base import Base

        inspector = inspect(engine)
        added, failed = [], []

        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # таблицу целиком создаст create_all
            existing = {c['name'] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                try:
                    col_type = col.type.compile(engine.dialect)
                except Exception as e:
                    failed.append(f"{table.name}.{col.name} (type: {e})")
                    continue

                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS "{col.name}" {col_type}'

                # Скалярный default из модели → в DDL, чтобы можно было NOT NULL
                default_sql = None
                if col.default is not None and getattr(col.default, 'is_scalar', False):
                    arg = col.default.arg
                    if isinstance(arg, bool):
                        default_sql = 'TRUE' if arg else 'FALSE'
                    elif isinstance(arg, (int, float)):
                        default_sql = str(arg)
                    elif isinstance(arg, str):
                        default_sql = "'" + arg.replace("'", "''") + "'"
                if default_sql is not None:
                    ddl += f' DEFAULT {default_sql}'
                    if not col.nullable:
                        ddl += ' NOT NULL'
                # NOT NULL без дефолта не добавляем — на непустой таблице ALTER упадёт

                try:
                    with engine.connect() as conn:
                        trans = conn.begin()
                        try:
                            conn.execute(text(ddl))
                            trans.commit()
                            added.append(f"{table.name}.{col.name}")
                        except Exception:
                            trans.rollback()
                            raise
                except Exception as e:
                    failed.append(f"{table.name}.{col.name} ({e})")

        if added:
            logger.info(f"✅ ensure_all_model_columns: added {len(added)} columns: {added}")
        else:
            logger.info("✅ ensure_all_model_columns: schema already in sync with models")
        if failed:
            logger.error(f"❌ ensure_all_model_columns: failed for {failed}")
    except Exception as e:
        logger.error(f"❌ ensure_all_model_columns error: {e}")


def ensure_task_assistant_fk_on_delete():
    """
    Идемпотентно переводит FK `tasks.*_assistant_id` на ON DELETE SET NULL.

    Колонки создавались без ON DELETE, то есть с дефолтным NO ACTION: пока на
    ассистента ссылается хоть одна задача, удалить его нельзя. Из-за этого
    удаление агента падало с ForeignKeyViolation, если у задачи ранее удалили
    контакт (agent_contact_id → NULL) и её переставали находить по контактам.

    Задача без ассистента безвредна: планировщик пропускает такие задачи, а
    удаление агента чистит их явно (см. api/agent.py::delete_agent).
    """
    assistant_columns = {
        "assistant_id", "gemini_assistant_id", "cartesia_assistant_id",
        "yandex_assistant_id", "cascade_assistant_id", "fish_assistant_id",
    }
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        if not inspector.has_table('tasks'):
            return

        fixed = 0
        with engine.connect() as conn:
            # Имена constraint'ов читаем из БД, а не угадываем: часть колонок
            # добавлялась разными механизмами и могла получить своё имя.
            for fk in inspector.get_foreign_keys('tasks'):
                columns = fk.get('constrained_columns') or []
                name = fk.get('name')
                target_table = fk.get('referred_table')
                if len(columns) != 1 or columns[0] not in assistant_columns:
                    continue
                if not name or not target_table:
                    continue
                if (fk.get('options') or {}).get('ondelete', '').upper() == 'SET NULL':
                    continue  # уже починен — не берём лишний раз ACCESS EXCLUSIVE

                column = columns[0]
                # DROP и ADD в одной транзакции: если ADD не пройдёт
                # (например, есть висячая ссылка), старый constraint вернётся.
                trans = conn.begin()
                try:
                    conn.execute(text(f'ALTER TABLE tasks DROP CONSTRAINT "{name}"'))
                    conn.execute(text(
                        f'ALTER TABLE tasks ADD CONSTRAINT "{name}" '
                        f'FOREIGN KEY ({column}) REFERENCES {target_table}(id) '
                        f'ON DELETE SET NULL'
                    ))
                    trans.commit()
                    fixed += 1
                except Exception as e:
                    trans.rollback()
                    logger.error(f"❌ Failed to fix FK {name} on tasks.{column}: {e}")

        if fixed:
            logger.info(f"✅ tasks assistant FKs switched to ON DELETE SET NULL ({fixed})")
    except Exception as e:
        logger.error(f"❌ ensure_task_assistant_fk_on_delete error: {e}")


def ensure_agent_knowledge_base_columns():
    """
    Идемпотентно добавляет колонки базы знаний (Pinecone) в agent_configs.

    Дублирует alembic-миграцию add_agent_knowledge_base на случай, если миграции
    не применились (в продакшене ошибки alembic проглатываются).
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('agent_configs'):
            return

        existing = {c['name'] for c in inspector.get_columns('agent_configs')}
        statements = [
            ("kb_namespace", "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS kb_namespace VARCHAR(64)"),
            ("kb_char_count", "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS kb_char_count INTEGER NOT NULL DEFAULT 0"),
            ("kb_content", "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS kb_content TEXT"),
            ("kb_name", "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS kb_name VARCHAR(100)"),
            ("kb_updated_at", "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS kb_updated_at TIMESTAMP"),
        ]
        missing = [(col, sql) for col, sql in statements if col not in existing]
        if not missing:
            return

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for _col, sql in missing:
                    conn.execute(text(sql))
                trans.commit()
                logger.info(f"✅ Added agent_configs knowledge base columns: {[c for c, _ in missing]}")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to add knowledge base columns: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_knowledge_base_columns error: {e}")


def ensure_agent_public_access_columns():
    """
    Идемпотентно добавляет колонки публичного HTTP-канала в agent_configs.

    public_api_key — секретный ключ приёма заявок «сервер-к-серверу»,
    public_enabled — флаг включения канала.
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('agent_configs'):
            return

        existing = {c['name'] for c in inspector.get_columns('agent_configs')}
        statements = [
            ("public_api_key", "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS public_api_key VARCHAR(64)"),
            ("public_enabled", "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS public_enabled BOOLEAN NOT NULL DEFAULT FALSE"),
        ]
        missing = [(col, sql) for col, sql in statements if col not in existing]
        # Уникальный индекс на ключ (после создания колонки)
        index_sql = (
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_configs_public_api_key "
            "ON agent_configs (public_api_key)"
        )

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for _col, sql in missing:
                    conn.execute(text(sql))
                conn.execute(text(index_sql))
                trans.commit()
                if missing:
                    logger.info(f"✅ Added agent_configs public access columns: {[c for c, _ in missing]}")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to add public access columns: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_public_access_columns error: {e}")


def ensure_payment_finik_columns():
    """
    Идемпотентно добавляет колонки Finik в payment_transactions:
      - finik_transaction_id (уникальный индекс — идемпотентность webhook'ов);
      - payment_url (URL платёжной страницы Finik).
    Существующие записи не меняются (payment_system у них остаётся 'robokassa' —
    историческая маркировка старых платежей).
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('payment_transactions'):
            return

        existing = {c['name'] for c in inspector.get_columns('payment_transactions')}

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                if 'finik_transaction_id' not in existing:
                    conn.execute(text(
                        "ALTER TABLE payment_transactions "
                        "ADD COLUMN IF NOT EXISTS finik_transaction_id VARCHAR(100)"
                    ))
                    logger.info("✅ Added column payment_transactions.finik_transaction_id")
                if 'payment_url' not in existing:
                    conn.execute(text(
                        "ALTER TABLE payment_transactions "
                        "ADD COLUMN IF NOT EXISTS payment_url VARCHAR(1000)"
                    ))
                    logger.info("✅ Added column payment_transactions.payment_url")

                # Уникальный частичный индекс: один Finik transactionId — одно зачисление
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_transactions_finik_tx "
                    "ON payment_transactions(finik_transaction_id) "
                    "WHERE finik_transaction_id IS NOT NULL"
                ))

                # Новые платежи по умолчанию — Finik / KGS
                conn.execute(text(
                    "ALTER TABLE payment_transactions "
                    "ALTER COLUMN payment_system SET DEFAULT 'finik'"
                ))
                conn.execute(text(
                    "ALTER TABLE payment_transactions "
                    "ALTER COLUMN currency SET DEFAULT 'KGS'"
                ))

                trans.commit()
                logger.info("✅ payment_transactions: Finik columns/index ensured")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to ensure Finik payment columns: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_payment_finik_columns error: {e}")


def ensure_agent_webhook_columns():
    """
    Идемпотентно добавляет колонку вебхука оркестратора в agent_configs.

    webhook_url — URL, на который оркестратор (чат / PostCall) шлёт событие
    через tool send_webhook. Миграции не используем — добавляем при старте.
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('agent_configs'):
            return

        existing = {c['name'] for c in inspector.get_columns('agent_configs')}
        if 'webhook_url' in existing:
            return

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text(
                    "ALTER TABLE agent_configs "
                    "ADD COLUMN IF NOT EXISTS webhook_url VARCHAR(500)"
                ))
                trans.commit()
                logger.info("✅ Added column agent_configs.webhook_url")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to add webhook_url column: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_webhook_columns error: {e}")


def ensure_agent_inbound_first_phrase_column():
    """
    Идемпотентно добавляет колонку inbound_first_phrase в agent_configs.

    inbound_first_phrase — первая фраза голосового агента при входящем звонке
    (с опциональной переменной {name}). Миграции не используем — добавляем
    при старте.
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('agent_configs'):
            return

        existing = {c['name'] for c in inspector.get_columns('agent_configs')}
        if 'inbound_first_phrase' in existing:
            return

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text(
                    "ALTER TABLE agent_configs "
                    "ADD COLUMN IF NOT EXISTS inbound_first_phrase TEXT"
                ))
                trans.commit()
                logger.info("✅ Added column agent_configs.inbound_first_phrase")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to add inbound_first_phrase column: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_inbound_first_phrase_column error: {e}")


def ensure_agent_orchestrator_model_migration():
    """
    Переводит агентов с устаревших слагов моделей на актуальные.

    Слаг google/gemini-3.1-pro на OpenRouter не существует (там только
    -preview), поэтому агент с ним падал при первом же вызове оркестратора,
    а после ужесточения валидации у него ещё и перестали бы сохраняться
    настройки. Карта соответствий — LEGACY_MODEL_ALIASES в services/agent_models.py.

    Идемпотентно: повторный запуск не находит строк и ничего не делает.
    """
    try:
        from sqlalchemy import text, inspect
        from backend.services.agent_models import LEGACY_MODEL_ALIASES

        if not LEGACY_MODEL_ALIASES:
            return

        inspector = inspect(engine)
        if not inspector.has_table('agent_configs'):
            return

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for old_slug, new_slug in LEGACY_MODEL_ALIASES.items():
                    result = conn.execute(
                        text(
                            "UPDATE agent_configs SET orchestrator_model = :new "
                            "WHERE orchestrator_model = :old"
                        ),
                        {"new": new_slug, "old": old_slug},
                    )
                    if result.rowcount:
                        logger.info(
                            f"✅ Orchestrator model migrated: {old_slug} → {new_slug} "
                            f"({result.rowcount} agents)"
                        )
                trans.commit()
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Failed to migrate orchestrator models: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_orchestrator_model_migration error: {e}")


def ensure_voice_assistant_fk_rules():
    """
    Идемпотентно приводит правила ON DELETE для FK голосовых ассистентов к SET NULL.

    Прод создаёт схему через create_all, поэтому исторически часть FK получила
    неверное правило удаления:
      - agent_configs.gemini_assistant_id было ON DELETE CASCADE → удаление
        Gemini-ассистента каскадом сносило целого агента;
      - tasks.assistant_id было NO ACTION → блокировало удаление OpenAI-ассистента,
        если на него ссылалась задача.
    Оба случая лечатся переводом правила в SET NULL (как уже сделано у gemini/cartesia
    в tasks и у openai/cartesia в agent_configs). Без этого фикс удаления ассистентов
    «откатится» при пересоздании БД.
    """
    # (table, column, ref_table, drop_constraint_names, target_constraint_name)
    targets = [
        (
            "agent_configs", "gemini_assistant_id", "gemini_assistant_configs",
            ["agent_configs_gemini_assistant_id_fkey", "agent_configs_assistant_id_fkey"],
            "agent_configs_gemini_assistant_id_fkey",
        ),
        (
            "tasks", "assistant_id", "assistant_configs",
            ["tasks_assistant_id_fkey"],
            "tasks_assistant_id_fkey",
        ),
    ]
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        for table, column, ref_table, drop_names, target_name in targets:
            if not inspector.has_table(table) or not inspector.has_table(ref_table):
                continue
            try:
                with engine.connect() as conn:
                    # Текущее правило удаления для FK на этой колонке
                    rule = conn.execute(text("""
                        SELECT rc.delete_rule
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                             ON tc.constraint_name = kcu.constraint_name
                            AND tc.table_schema = kcu.table_schema
                        JOIN information_schema.referential_constraints rc
                             ON tc.constraint_name = rc.constraint_name
                            AND tc.table_schema = rc.constraint_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND tc.table_name = :table
                          AND kcu.column_name = :column
                        LIMIT 1
                    """), {"table": table, "column": column}).scalar()

                    if rule == "SET NULL":
                        continue  # уже корректно — ничего не делаем

                    trans = conn.begin()
                    try:
                        for name in drop_names:
                            conn.execute(text(
                                f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}'
                            ))
                        conn.execute(text(
                            f'ALTER TABLE {table} '
                            f'ADD CONSTRAINT {target_name} '
                            f'FOREIGN KEY ({column}) '
                            f'REFERENCES {ref_table}(id) ON DELETE SET NULL'
                        ))
                        trans.commit()
                        logger.info(
                            f"✅ FK {table}.{column} → {ref_table} "
                            f"set to ON DELETE SET NULL (was {rule})"
                        )
                    except Exception as e:
                        trans.rollback()
                        logger.error(f"❌ Failed to fix FK {table}.{column}: {e}")
            except Exception as e:
                logger.error(f"❌ ensure_voice_assistant_fk_rules({table}.{column}) error: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_voice_assistant_fk_rules error: {e}")


def ensure_yandex_agent_columns():
    """
    Идемпотентно добавляет FK-колонки yandex_assistant_id для поддержки
    Яндекс-ассистентов в качестве голосовых у автономного агента:
      - agent_configs.yandex_assistant_id → yandex_assistant_configs (SET NULL)
      - tasks.yandex_assistant_id → yandex_assistant_configs (SET NULL)
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('yandex_assistant_configs'):
            return

        targets = [
            ("agent_configs", "agent_configs_yandex_assistant_id_fkey"),
            ("tasks", "tasks_yandex_assistant_id_fkey"),
        ]
        for table, fk_name in targets:
            if not inspector.has_table(table):
                continue
            existing = {c['name'] for c in inspector.get_columns(table)}
            if 'yandex_assistant_id' in existing:
                continue
            with engine.connect() as conn:
                trans = conn.begin()
                try:
                    conn.execute(text(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN IF NOT EXISTS yandex_assistant_id UUID"
                    ))
                    conn.execute(text(
                        f"ALTER TABLE {table} "
                        f"ADD CONSTRAINT {fk_name} "
                        f"FOREIGN KEY (yandex_assistant_id) "
                        f"REFERENCES yandex_assistant_configs(id) ON DELETE SET NULL"
                    ))
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS ix_{table}_yandex_assistant_id "
                        f"ON {table} (yandex_assistant_id)"
                    ))
                    trans.commit()
                    logger.info(f"✅ Added column {table}.yandex_assistant_id")
                except Exception as e:
                    trans.rollback()
                    logger.error(f"❌ Failed to add {table}.yandex_assistant_id: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_yandex_agent_columns error: {e}")


def ensure_agent_connectors_table():
    """
    Идемпотентно создаёт таблицу agent_connectors (внешние коннекторы агента
    через Composio: Google Calendar, Gmail).

    Миграции не трогаем — создаём таблицу через ORM-метаданные при старте, как и
    остальные agent-таблицы. Если таблица уже есть — no-op.
    """
    try:
        from sqlalchemy import inspect
        from backend.models.agent_connector import AgentConnector

        inspector = inspect(engine)
        if inspector.has_table('agent_connectors'):
            return

        AgentConnector.__table__.create(bind=engine, checkfirst=True)
        logger.info("✅ Created table agent_connectors")
    except Exception as e:
        logger.error(f"❌ ensure_agent_connectors_table error: {e}")


def ensure_agent_telegram_account_tables():
    """
    Идемпотентно создаёт таблицы личного Telegram-аккаунта агента (MTProto):
    agent_telegram_accounts, agent_telegram_dialogs, agent_telegram_messages.
    Как и остальные agent-таблицы — через ORM-метаданные при старте.
    """
    try:
        from sqlalchemy import inspect
        from backend.models.agent_telegram_account import (
            AgentTelegramAccount, AgentTelegramDialog, AgentTelegramMessage,
        )

        inspector = inspect(engine)
        for model in (AgentTelegramAccount, AgentTelegramDialog, AgentTelegramMessage):
            if not inspector.has_table(model.__tablename__):
                model.__table__.create(bind=engine, checkfirst=True)
                logger.info(f"✅ Created table {model.__tablename__}")
    except Exception as e:
        logger.error(f"❌ ensure_agent_telegram_account_tables error: {e}")


def ensure_connectors_agent_identity_migration():
    """
    Однократный сброс старых (пользовательских) подключений коннекторов в pending.

    После перехода на агентную identity Composio (вариант A) подключения,
    сделанные под общим user.id, больше не действуют — их нужно переподключить
    на каждом агенте. Идемпотентно: трогает только строки, чей composio_user_id
    НЕ агентного формата (новые имеют вид 'agent_<id>').
    """
    try:
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        if not inspector.has_table('agent_connectors'):
            return

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                result = conn.execute(text(
                    "UPDATE agent_connectors "
                    "SET status='pending', connected_account_id=NULL, state_token=NULL "
                    "WHERE status='connected' "
                    "AND (composio_user_id IS NULL OR composio_user_id NOT LIKE 'agent%')"
                ))
                trans.commit()
                if result.rowcount:
                    logger.info(f"✅ Reset {result.rowcount} pre-agent-identity connectors to pending (reconnect needed)")
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ connectors identity migration failed: {e}")
    except Exception as e:
        logger.error(f"❌ ensure_connectors_agent_identity_migration error: {e}")


@app.on_event("startup")
async def startup_event():
    """Application startup event"""
    try:
        logger.info("🚀 Starting WellcomeAI application v3.0...")
        
        # Простая проверка блокировки для Render
        lock_file_path = "/tmp/wellcome_migrations.lock"
        migration_completed = False
        
        try:
            # Для Render используем более простую блокировку
            if not os.path.exists(lock_file_path):
                # Создаем файл блокировки
                with open(lock_file_path, 'w') as lock_file:
                    lock_file.write(str(os.getpid()))
                
                logger.info("🔒 Running migrations and schema fixes...")
                
                # Шаг 1: Запускаем миграции
                run_migrations()
                
                # Шаг 2: Создаем базовые таблицы
                create_tables(engine)
                
                # Шаг 3: Комплексная проверка и исправление схемы
                check_and_fix_all_missing_columns()
                
                # Шаг 4: Создаем таблицы ElevenLabs и проверяем колонки
                create_elevenlabs_tables()
                
                # Шаг 5: Создаем таблицу email_verifications
                create_email_verification_table()
                
                # Шаг 6: Создаем таблицу embed_configs
                create_embed_configs_table()
                
                # Шаг 7: Создаем таблицы Gemini
                create_gemini_tables()
                
                # Шаг 8: Создаем таблицы CRM (Contacts)
                create_crm_tables()
                
                # 🆕 Шаг 9: Создаем таблицы Grok
                create_grok_tables()

                # 🆕 Шаг 10: Создаем таблицы Cartesia
                create_cartesia_tables()

                # 🆕 Шаг 10.1: Создаем таблицы Yandex (SpeechKit Realtime)
                create_yandex_tables()

                # 🐟 Создаем таблицы Fish
                create_fish_tables()

                # 🆕 Шаг 11: Сидинг данных системы кредитов (план agent + пакеты)
                seed_credits_data()

                # 🆕 Шаг 11.1: Разовый грант тестовых кредитов каскада всем юзерам
                backfill_cascade_trial_credits()

                # 🆕 Шаг 11.2: Гарантированный сид пакетов докупки каскада
                ensure_cascade_credit_packages()

                # 🆕 Шаг 11.3: Устаревшие слаги моделей оркестратора → актуальные
                ensure_agent_orchestrator_model_migration()

                # 🆕 Шаг 12: Нормализация стадий воронки (calling → active)
                normalize_agent_contact_stages()

                # 🆕 Шаг 13: Колонка voice_additional_instructions в agent_configs
                ensure_agent_voice_instructions_column()

                # 🆕 Шаг 13.1: FK-колонки каскад-голоса (agent_configs + tasks)
                ensure_agent_cascade_voice_columns()

                # 🆕 Шаг 13.2: FK-колонки fish-голоса (agent_configs + tasks)
                ensure_agent_fish_voice_columns()

                # 🆕 Шаг 13.25: Недостающие колонки tasks из модели Task
                #    (cartesia_assistant_id + агентно-оркестраторные поля)
                ensure_task_model_columns()

                # 🆕 Шаг 13.3: FK задач на ассистентов → ON DELETE SET NULL
                ensure_task_assistant_fk_on_delete()

                # 🆕 Шаг 14: Колонки базы знаний (Pinecone) в agent_configs
                ensure_agent_knowledge_base_columns()

                # 🆕 Шаг 15: Колонки публичного HTTP-канала в agent_configs
                ensure_agent_public_access_columns()

                # 🆕 Шаг 16: Колонка вебхука оркестратора в agent_configs
                ensure_agent_webhook_columns()

                # 🆕 Шаг 17: Колонка первой фразы для входящих в agent_configs
                ensure_agent_inbound_first_phrase_column()

                # 🆕 Шаг 18: Таблица внешних коннекторов агента (Composio)
                ensure_agent_connectors_table()

                # 🆕 Шаг 19: Сброс старых (пользовательских) коннекторов в pending
                #            после перехода на агентную identity Composio (вариант A)
                ensure_connectors_agent_identity_migration()

                # 🆕 Шаг 20: Правила ON DELETE для FK голосовых ассистентов → SET NULL
                #            (чтобы удаление ассистента не сносило/не блокировало агента)
                ensure_voice_assistant_fk_rules()

                # 🆕 Шаг 21: Таблицы личного Telegram-аккаунта агента (MTProto)
                ensure_agent_telegram_account_tables()

                # 🆕 Шаг 22: FK-колонки yandex_assistant_id (агент + задачи)
                ensure_yandex_agent_columns()

                # 🆕 Шаг 22.5: Колонки Finik в payment_transactions
                #    (finik_transaction_id + уникальный индекс, payment_url)
                ensure_payment_finik_columns()

                # 🆕 Шаг 23: Универсальный доводчик — все недостающие колонки
                #    всех моделей (запускается ПОСЛЕ специализированных шагов,
                #    чтобы не перехватывать их FK-колонки)
                ensure_all_model_columns()

                migration_completed = True
                logger.info("✅ All migrations and schema fixes completed")
                
            else:
                logger.info("⏳ Waiting for migrations to complete...")
                # Ждем завершения миграций
                max_wait = 60
                waited = 0
                while os.path.exists(lock_file_path) and waited < max_wait:
                    await asyncio.sleep(1)
                    waited += 1
                
                if waited >= max_wait:
                    logger.warning("⚠️ Migration timeout, proceeding anyway")
                
        except Exception as e:
            logger.error(f"❌ Migration error: {str(e)}")
            if not settings.PRODUCTION:
                raise
        finally:
            # Удаляем файл блокировки
            if migration_completed:
                try:
                    if os.path.exists(lock_file_path):
                        os.remove(lock_file_path)
                except Exception as e:
                    logger.error(f"Error removing lock file: {e}")
        
        # ✅ УПРОЩЁННАЯ ЛОГИКА: Всегда запускаем scheduler в Render
        try:
            logger.info("🔄 Starting background schedulers...")
            
            # Запуск Subscription Checker
            asyncio.create_task(start_subscription_checker())
            logger.info("✅ Subscription checker started")
            
            # ✅ Запуск Task Scheduler
            asyncio.create_task(start_task_scheduler(check_interval=30))
            logger.info("✅ Task Scheduler started (check every 30s)")

            # ✅ Запуск блокировщика истёкших подписок agent (каждые 5 мин)
            asyncio.create_task(start_subscription_blocker())
            logger.info("✅ Agent subscription blocker started (check every 5 min)")

            # ✅ Поллер личного Telegram агента (каждые 60 сек; no-op без
            #    TELEGRAM_API_ID/HASH/SESSION_KEY; мультиворкер — claim по БД)
            asyncio.create_task(start_telegram_user_poller(check_interval=60))
            logger.info("✅ Telegram user poller started (check every 60s)")

        except Exception as e:
            logger.error(f"❌ Error starting schedulers: {str(e)}")
        
        # Логирование инициализации Email Verification
        try:
            logger.info("📧 Email Verification API initialized")
            logger.info(f"   Send code: {settings.HOST_URL}/api/email-verification/send")
            logger.info(f"   Resend code: {settings.HOST_URL}/api/email-verification/resend")
            logger.info(f"   Verify code: {settings.HOST_URL}/api/email-verification/verify")
            logger.info(f"   Status: {settings.HOST_URL}/api/email-verification/status/{{email}}")
        except Exception as e:
            logger.error(f"❌ Error initializing Email Verification: {str(e)}")
        
        # Логирование инициализации Voximplant интеграции
        try:
            logger.info("📞 Voximplant integration initialized")
            logger.info(f"   WebSocket endpoint: {settings.HOST_URL}/api/voximplant/ws/{{assistant_id}}")
            logger.info(f"   Demo endpoint: {settings.HOST_URL}/api/voximplant/ws/demo")
            logger.info(f"   Test endpoint: {settings.HOST_URL}/api/voximplant/test")
        except Exception as e:
            logger.error(f"❌ Error initializing Voximplant integration: {str(e)}")
        
        # Логирование инициализации ElevenLabs интеграции
        try:
            logger.info("🎙️ ElevenLabs integration initialized")
            logger.info(f"   API endpoints: {settings.HOST_URL}/api/elevenlabs/")
            logger.info(f"   WebSocket endpoint: {settings.HOST_URL}/api/elevenlabs/ws/{{agent_id}}")
            logger.info(f"   Voice generation endpoint: {settings.HOST_URL}/api/elevenlabs/generate")
        except Exception as e:
            logger.error(f"❌ Error initializing ElevenLabs integration: {str(e)}")
        
        # Логирование инициализации Conversations API
        try:
            logger.info("💬 Conversations API initialized")
            logger.info(f"   List endpoint: {settings.HOST_URL}/api/conversations")
            logger.info(f"   Sessions endpoint: {settings.HOST_URL}/api/conversations/sessions")
            logger.info(f"   Detail endpoint: {settings.HOST_URL}/api/conversations/{{id}}")
            logger.info(f"   Stats endpoint: {settings.HOST_URL}/api/conversations/stats")
            logger.info(f"   By caller endpoint: {settings.HOST_URL}/api/conversations/by-caller/{{phone}}")
        except Exception as e:
            logger.error(f"❌ Error initializing Conversations API: {str(e)}")
        
        # Логирование инициализации CRM API
        try:
            logger.info("📇 CRM API initialized")
            logger.info(f"   List contacts: GET {settings.HOST_URL}/api/contacts")
            logger.info(f"   Get contact: GET {settings.HOST_URL}/api/contacts/{{id}}")
            logger.info(f"   Create/Update: POST {settings.HOST_URL}/api/contacts")
            logger.info(f"   Update: PUT {settings.HOST_URL}/api/contacts/{{id}}")
            logger.info(f"   Update status: PATCH {settings.HOST_URL}/api/contacts/{{id}}/status")
            logger.info(f"   Delete: DELETE {settings.HOST_URL}/api/contacts/{{id}}")
            logger.info("   Features:")
            logger.info("     - Auto-create contacts from phone calls")
            logger.info("     - Link all conversations to contacts")
            logger.info("     - Contact statuses: new, active, client, archived")
            logger.info("     - Search and filtering")
        except Exception as e:
            logger.error(f"❌ Error initializing CRM API: {str(e)}")
        
        # ✅ Логирование инициализации Tasks API
        try:
            logger.info("📅 Tasks API initialized")
            logger.info(f"   List tasks: GET {settings.HOST_URL}/api/contacts/{{contact_id}}/tasks")
            logger.info(f"   Create task: POST {settings.HOST_URL}/api/contacts/{{contact_id}}/tasks")
            logger.info(f"   Delete task: DELETE {settings.HOST_URL}/api/contacts/tasks/{{task_id}}")
            logger.info("   Features:")
            logger.info("     - Schedule automated calls to contacts")
            logger.info("     - Support for OpenAI and Gemini assistants")
            logger.info("     - Automatic execution via Task Scheduler")
            logger.info("     - Natural language time parsing (e.g., 'tomorrow at 3pm')")
            logger.info("     - Task statuses: scheduled, pending, completed, failed, cancelled")
            logger.info(f"   Task Scheduler runs every 30 seconds")
        except Exception as e:
            logger.error(f"❌ Error initializing Tasks API: {str(e)}")
        
        # Логирование инициализации Embeds API
        try:
            logger.info("🎨 Embeds API initialized")
            logger.info(f"   Create embed: POST {settings.HOST_URL}/api/embeds")
            logger.info(f"   List user embeds: GET {settings.HOST_URL}/api/embeds/user/me")
            logger.info(f"   Public embed page: GET {settings.HOST_URL}/embed/{{embed_code}}")
            logger.info(f"   Example: {settings.HOST_URL}/embed/w_abc123def456")
            logger.info("   Usage: <iframe src='https://voicyfy.ru/embed/w_YOUR_CODE' width='100%' height='800px'></iframe>")
        except Exception as e:
            logger.error(f"❌ Error initializing Embeds API: {str(e)}")
        
        # Логирование инициализации Gemini Live API
        try:
            logger.info("🤖 Google Gemini Live API initialized")
            logger.info(f"   WebSocket endpoint: {settings.HOST_URL}/ws/gemini/{{assistant_id}}")
            logger.info(f"   Model: gemini-2.5-flash-native-audio-preview-09-2025")
            logger.info(f"   Health check: {settings.HOST_URL}/gemini/health")
            logger.info(f"   Info: {settings.HOST_URL}/gemini/info")
            logger.info("   Features:")
            logger.info("     - Real-time audio (16kHz in, 24kHz out)")
            logger.info("     - Automatic VAD (voice activity detection)")
            logger.info("     - Manual function calling")
            logger.info("     - Thinking mode (configurable)")
            logger.info("     - Screen context support")
            logger.info("     - 30 HD voices, 24 languages")
        except Exception as e:
            logger.error(f"❌ Error initializing Gemini Live API: {str(e)}")
        
        # Логирование инициализации Gemini Assistants API
        try:
            logger.info("🤖 Gemini Assistants CRUD API initialized")
            logger.info(f"   List: GET {settings.HOST_URL}/api/gemini-assistants")
            logger.info(f"   Get: GET {settings.HOST_URL}/api/gemini-assistants/{{id}}")
            logger.info(f"   Create: POST {settings.HOST_URL}/api/gemini-assistants")
            logger.info(f"   Update: PUT {settings.HOST_URL}/api/gemini-assistants/{{id}}")
            logger.info(f"   Delete: DELETE {settings.HOST_URL}/api/gemini-assistants/{{id}}")
            logger.info(f"   Embed code: GET {settings.HOST_URL}/api/gemini-assistants/{{id}}/embed-code")
            logger.info(f"   Verify Sheet: POST {settings.HOST_URL}/api/gemini-assistants/{{id}}/verify-sheet")
        except Exception as e:
            logger.error(f"❌ Error initializing Gemini Assistants API: {str(e)}")
        
        # 🆕 v3.0: Логирование инициализации Grok Voice API
        try:
            logger.info("🤖 xAI Grok Voice Agent API initialized")
            logger.info(f"   WebSocket (web): {settings.HOST_URL}/ws/grok/{{assistant_id}}")
            logger.info(f"   WebSocket (telephony): {settings.HOST_URL}/ws/grok/voximplant/{{assistant_id}}")
            logger.info(f"   WebSocket (custom): {settings.HOST_URL}/ws/grok/custom/{{assistant_id}}?sample_rate=X")
            logger.info(f"   API endpoint: wss://api.x.ai/v1/realtime")
            logger.info(f"   Health check: {settings.HOST_URL}/grok/health")
            logger.info(f"   Info: {settings.HOST_URL}/grok/info")
            logger.info("   Features:")
            logger.info("     - Native G.711 μ-law telephony (no codec conversion)")
            logger.info("     - 5 voices: Ara, Rex, Sal, Eve, Leo")
            logger.info("     - Native web_search tool")
            logger.info("     - Native x_search (Twitter) tool")
            logger.info("     - Native file_search (vector store) tool")
            logger.info("     - PCM 8-48kHz for web")
            logger.info("     - Server-side VAD")
        except Exception as e:
            logger.error(f"❌ Error initializing Grok Voice API: {str(e)}")
        
        # 🆕 v3.0: Логирование инициализации Grok Assistants API
        try:
            logger.info("🤖 Grok Assistants CRUD API initialized")
            logger.info(f"   List: GET {settings.HOST_URL}/api/grok-assistants")
            logger.info(f"   Get: GET {settings.HOST_URL}/api/grok-assistants/{{id}}")
            logger.info(f"   Create: POST {settings.HOST_URL}/api/grok-assistants")
            logger.info(f"   Update: PUT {settings.HOST_URL}/api/grok-assistants/{{id}}")
            logger.info(f"   Delete: DELETE {settings.HOST_URL}/api/grok-assistants/{{id}}")
            logger.info(f"   Conversations: GET {settings.HOST_URL}/api/grok-assistants/{{id}}/conversations")
            logger.info(f"   Embed code: GET {settings.HOST_URL}/api/grok-assistants/{{id}}/embed-code")
            logger.info(f"   Voices: GET {settings.HOST_URL}/api/grok-assistants/voices/list")
        except Exception as e:
            logger.error(f"❌ Error initializing Grok Assistants API: {str(e)}")
        
        # Логирование инициализации Partners API
        try:
            logger.info("🤝 Partners API initialized")
            logger.info(f"   Dashboard: GET {settings.HOST_URL}/api/partners/dashboard")
            logger.info(f"   Referrals: GET {settings.HOST_URL}/api/partners/referrals")
            logger.info(f"   Generate link: GET {settings.HOST_URL}/api/partners/generate-link")
            logger.info(f"   Commission rate: 30%")
            logger.info("   Features:")
            logger.info("     - Auto-activation for all users")
            logger.info("     - UTM tracking (utm_source=partner)")
            logger.info("     - Referral code format: XX123456")
            logger.info("     - Commission on paid subscriptions")
        except Exception as e:
            logger.error(f"❌ Error initializing Partners API: {str(e)}")
        
        logger.info("✅ Application started successfully (v3.0 with Grok Voice API)")
        
    except Exception as e:
        logger.error(f"❌ Startup error: {str(e)}", exc_info=True)
        if not settings.PRODUCTION:
            raise

# ============================================================================
# ROOT ROUTES
# ============================================================================

@app.get("/")
async def serve_landing():
    """
    Serve React landing page.
    UTM parameters are handled client-side by the React app.
    """
    return FileResponse("backend/static/landing/index.html")


@app.get("/health")
async def health_check():
    """Health check for deployment platforms"""
    return {
        "status": "healthy",
        "service": "wellcome-ai",
        "version": "3.0.0",  # 🆕 Обновлена версия
        "features": {
            "openai_realtime": True,
            "gemini_live": True,
            "gemini_assistants_crud": True,
            "grok_voice": True,  # 🆕 v3.0
            "grok_assistants_crud": True,  # 🆕 v3.0
            "elevenlabs": True,
            "voximplant": True,
            "embeds": True,
            "email_verification": True,
            "crm": True,
            "tasks": True,
            "task_scheduler": True,
            "partners": True,
            "utm_tracking": True
        }
    }


@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown event"""
    logger.info("🛑 Application stopped")
