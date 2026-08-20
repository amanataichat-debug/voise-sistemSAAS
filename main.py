import os
import sys
import importlib
import importlib.util
import importlib.abc
import types
import uvicorn
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add current directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Meta import configuration
class BackendImportFinder(importlib.abc.MetaPathFinder):
    """
    Finder for redirecting imports without prefix to backend package.
    E.g., 'core.config' -> 'backend.core.config'
    """
    def __init__(self):
        self.prefix = 'backend.'
        self.main_modules = ['core', 'db', 'models', 'schemas', 'services', 'utils', 'api']
        self.imported_modules = set()

    def find_spec(self, fullname, path, target=None):
        if fullname in sys.modules:
            return None
        if fullname == 'websockets' or fullname.startswith('websockets.'):
            return None
        parts = fullname.split('.')
        if parts[0] not in self.main_modules:
            return None
        backend_name = self.prefix + fullname
        try:
            spec = importlib.util.find_spec(backend_name)
            if spec:
                self.imported_modules.add(fullname)
                loader = BackendImportLoader(fullname, backend_name)
                return importlib.machinery.ModuleSpec(fullname, loader)
        except (ImportError, AttributeError):
            pass
        return None

class BackendImportLoader(importlib.abc.Loader):
    """Loader for modules redirected to backend."""
    def __init__(self, fullname, backend_name):
        self.fullname = fullname
        self.backend_name = backend_name

    def create_module(self, spec):
        if self.fullname in sys.modules:
            return sys.modules[self.fullname]
        try:
            backend_module = importlib.import_module(self.backend_name)
            module = types.ModuleType(self.fullname)
            for attr in dir(backend_module):
                if not attr.startswith('__'):
                    setattr(module, attr, getattr(backend_module, attr))
            module.__file__ = getattr(backend_module, '__file__', None)
            module.__name__ = self.fullname
            module.__package__ = self.fullname.rpartition('.')[0] or None
            if '.' in self.fullname:
                parent_name, _, child_name = self.fullname.rpartition('.')
                if parent_name in sys.modules:
                    setattr(sys.modules[parent_name], child_name, module)
            sys.modules[self.fullname] = module
            return module
        except Exception as e:
            print(f"Error loading module {self.fullname}: {e}")
            return None

    def exec_module(self, module):
        pass

# Insert our import finder
sys.meta_path.insert(0, BackendImportFinder())

# Settings for running
PORT = int(os.getenv('PORT', 5050))
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
LOG_LEVEL = os.getenv('LOG_LEVEL', 'info').lower()

# ✅ НОВОЕ: Автоприменение миграций Alembic при старте
def run_alembic_migrations():
    """
    🚀 Автоматически применяет миграции Alembic при старте приложения
    """
    try:
        print("🔄 Применяем миграции Alembic...")
        
        # Импортируем Alembic
        from alembic.config import Config
        from alembic import command
        
        # Путь к конфигурации Alembic
        alembic_cfg_path = os.path.join(os.path.dirname(__file__), "alembic.ini")
        
        if not os.path.exists(alembic_cfg_path):
            print(f"❌ Файл alembic.ini не найден: {alembic_cfg_path}")
            return False
        
        # Создаем конфигурацию Alembic
        alembic_cfg = Config(alembic_cfg_path)
        
        # Устанавливаем DATABASE_URL из переменных окружения
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            alembic_cfg.set_main_option("sqlalchemy.url", database_url)
            print(f"🔗 Используем DATABASE_URL для миграций")
        
        # Применяем миграции до последней версии
        print("📊 Применяем миграции до последней версии...")
        command.upgrade(alembic_cfg, "head")
        
        print("✅ Миграции Alembic успешно применены!")
        return True
        
    except ImportError as e:
        print(f"❌ Alembic не установлен: {str(e)}")
        print("   Устанавливаем alembic: pip install alembic")
        return False
    except Exception as e:
        print(f"❌ Ошибка при применении миграций Alembic: {str(e)}")
        print(f"   Тип ошибки: {type(e).__name__}")
        print(f"   Продолжаем запуск приложения...")
        return False

# ✅ УЛУЧШЕННАЯ: Функция исправления цен (как запасной вариант)
def run_startup_fixes():
    """
    Запускаем исправления цен при старте (как запасной вариант если миграции не сработали)
    """
    try:
        print("🔧 Проверяем и исправляем цены подписок...")
        
        # Импортируем модели
        from backend.db.session import SessionLocal
        from backend.models.subscription import SubscriptionPlan
        
        db = SessionLocal()
        
        # Проверяем все планы в БД
        all_plans = db.query(SubscriptionPlan).all()
        print(f"📋 Найдено планов в БД: {len(all_plans)}")
        
        for plan in all_plans:
            print(f"   - {plan.code}: {plan.name} = {plan.price} руб")
        
        # Исправляем цену плана 'start'
        start_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == "start").first()
        if start_plan:
            if float(start_plan.price) != 1490.0:
                old_price = start_plan.price
                start_plan.price = 1490.0
                db.commit()
                print(f"✅ ИСПРАВЛЕНО: Цена плана 'start': {old_price} → 1490 рублей")
            else:
                print(f"✅ Цена плана 'start' уже корректная: 1490 рублей")
        else:
            print("⚠️  План 'start' не найден в БД - будет создан при первом платеже")
            
            # Создаем план 'start' если его нет
            try:
                new_start_plan = SubscriptionPlan(
                    code="start",
                    name="Тариф Старт", 
                    price=1490.0,
                    max_assistants=3,
                    description="Стартовый план с расширенными возможностями",
                    is_active=True
                )
                db.add(new_start_plan)
                db.commit()
                print("✅ Создан план 'start' с ценой 1490 рублей")
            except Exception as e:
                print(f"❌ Ошибка создания плана 'start': {str(e)}")
        
        # Исправляем цену плана 'pro' 
        pro_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == "pro").first()
        if pro_plan:
            if float(pro_plan.price) != 4990.0:
                old_price = pro_plan.price
                pro_plan.price = 4990.0
                db.commit()
                print(f"✅ ИСПРАВЛЕНО: Цена плана 'pro': {old_price} → 4990 рублей")
        
        # Проверяем что изменения сохранились
        updated_start_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == "start").first()
        if updated_start_plan:
            print(f"🔍 Финальная проверка - цена плана 'start': {updated_start_plan.price} рублей")
        
        db.close()
        print("🎉 Проверка и исправление цен завершена!")
        
    except ImportError as e:
        print(f"❌ Ошибка импорта моделей: {str(e)}")
        print("   Возможно, база данных еще не готова или модели не загружены")
    except Exception as e:
        print(f"❌ Ошибка при исправлении цен: {str(e)}")
        print(f"   Тип ошибки: {type(e).__name__}")
        # Продолжаем запуск даже при ошибке

# ✅ НОВОЕ: Проверка базы данных
def check_database_connection():
    """Проверяем подключение к базе данных"""
    try:
        print("🔗 Проверяем подключение к базе данных...")
        
        from backend.db.session import SessionLocal
        
        db = SessionLocal()
        
        # Простой тестовый запрос
        result = db.execute("SELECT 1 as test").fetchone()
        
        if result and result[0] == 1:
            print("✅ Подключение к базе данных успешно!")
            db.close()
            return True
        else:
            print("❌ Тест подключения к БД не прошел")
            db.close()
            return False
            
    except Exception as e:
        print(f"❌ Ошибка подключения к базе данных: {str(e)}")
        return False

# Import FastAPI app
from app import app

# Expose application for Gunicorn
application = app

# Configure root logger
logger = logging.getLogger("wellcome-ai")

if __name__ == "__main__":
    logger.info(f"🚀 Starting server on port {PORT}, debug={DEBUG}")
    
    print("=" * 70)
    print("🔄 ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ И МИГРАЦИЙ")
    print("=" * 70)
    
    # ✅ ШАГ 1: Проверяем подключение к БД
    db_connected = check_database_connection()
    
    if db_connected:
        # ✅ ШАГ 2: Применяем миграции Alembic
        migrations_success = run_alembic_migrations()
        
        # ✅ ШАГ 3: Запасное исправление цен (всегда выполняем для контроля)
        print("\n" + "="*50)
        print("🔧 ПРОВЕРКА И ИСПРАВЛЕНИЕ ЦЕН")
        print("="*50)
        run_startup_fixes()
        
        if migrations_success:
            print("\n🎉 Инициализация БД завершена успешно (миграции + проверка цен)")
        else:
            print("\n⚠️  Инициализация БД завершена (только проверка цен)")
    else:
        print("\n❌ Не удалось подключиться к базе данных!")
        print("   Сервер запустится, но могут быть проблемы с БД")
    
    print("=" * 70)
    print("🚀 ЗАПУСК СЕРВЕРА")  
    print("=" * 70)
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=PORT,
        log_level=LOG_LEVEL,
        reload=DEBUG,
        workers=1 if DEBUG else None,
        timeout_keep_alive=120,
    )
