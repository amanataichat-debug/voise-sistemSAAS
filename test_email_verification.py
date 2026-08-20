#!/usr/bin/env python3
"""
Тестовый скрипт для диагностики Email Verification системы
Запуск: python test_email_verification.py
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Цвета для вывода
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
RESET = '\033[0m'
BOLD = '\033[1m'

def print_success(msg):
    print(f"{GREEN}✅ {msg}{RESET}")

def print_error(msg):
    print(f"{RED}❌ {msg}{RESET}")

def print_warning(msg):
    print(f"{YELLOW}⚠️  {msg}{RESET}")

def print_info(msg):
    print(f"{BLUE}ℹ️  {msg}{RESET}")

def print_header(msg):
    print(f"\n{CYAN}{BOLD}{'='*60}{RESET}")
    print(f"{CYAN}{BOLD}{msg}{RESET}")
    print(f"{CYAN}{BOLD}{'='*60}{RESET}")

def test_imports():
    """Тест 0: Проверка импортов"""
    print_header("ТЕСТ 0: Проверка импортов")
    
    try:
        from backend.core.config import settings
        print_success("backend.core.config импортирован")
        
        from backend.core.security import create_jwt_token
        print_success("backend.core.security импортирован")
        
        from backend.services.email_service import EmailService
        print_success("backend.services.email_service импортирован")
        
        from backend.models.user import User
        print_success("backend.models.user импортирован")
        
        from backend.models.email_verification import EmailVerification
        print_success("backend.models.email_verification импортирован")
        
        return True, settings
        
    except Exception as e:
        print_error(f"Ошибка импорта: {e}")
        import traceback
        print(traceback.format_exc())
        return False, None

def test_smtp_config(settings):
    """Тест 1: Проверка конфигурации SMTP"""
    print_header("ТЕСТ 1: Проверка конфигурации SMTP")
    
    config_items = {
        'EMAIL_HOST': settings.EMAIL_HOST,
        'EMAIL_PORT': settings.EMAIL_PORT,
        'EMAIL_USERNAME': settings.EMAIL_USERNAME,
        'EMAIL_PASSWORD': '***' if settings.EMAIL_PASSWORD else None,
        'EMAIL_FROM': settings.EMAIL_FROM,
        'EMAIL_USE_SSL': settings.EMAIL_USE_SSL,
        'EMAIL_USE_TLS': settings.EMAIL_USE_TLS,
    }
    
    all_ok = True
    for key, value in config_items.items():
        if value is None or value == '':
            print_error(f"{key}: НЕ НАСТРОЕН")
            all_ok = False
        else:
            print_success(f"{key}: {value}")
    
    # Дополнительные проверки
    if settings.EMAIL_USE_SSL and settings.EMAIL_USE_TLS:
        print_warning("SSL и TLS включены одновременно - используйте что-то одно!")
        all_ok = False
    
    if settings.EMAIL_PORT == 465 and not settings.EMAIL_USE_SSL:
        print_warning("Порт 465 обычно требует SSL=True")
    
    if settings.EMAIL_PORT == 587 and not settings.EMAIL_USE_TLS:
        print_warning("Порт 587 обычно требует TLS=True")
    
    return all_ok

def test_database_connection(settings):
    """Тест 2: Проверка подключения к БД"""
    print_header("ТЕСТ 2: Проверка подключения к БД")
    
    try:
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.orm import sessionmaker
        
        print_info(f"DATABASE_URL: {settings.DATABASE_URL[:50]}...")
        
        engine = create_engine(settings.DATABASE_URL)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        print_success(f"Подключение к БД успешно")
        print_info(f"Всего таблиц: {len(tables)}")
        
        required_tables = ['users', 'email_verifications']
        for table in required_tables:
            if table in tables:
                print_success(f"Таблица '{table}' найдена")
                
                # Показываем структуру таблицы
                columns = inspector.get_columns(table)
                print_info(f"  Колонки ({len(columns)}): {', '.join([c['name'] for c in columns[:5]])}...")
            else:
                print_error(f"Таблица '{table}' ОТСУТСТВУЕТ!")
                return False
        
        db.close()
        return True
        
    except Exception as e:
        print_error(f"Ошибка подключения к БД: {e}")
        import traceback
        print(traceback.format_exc())
        return False

def test_smtp_connection(settings):
    """Тест 3: Прямая проверка SMTP подключения"""
    print_header("ТЕСТ 3: Проверка SMTP подключения")
    
    try:
        print_info(f"Подключаемся к {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        print_info(f"SSL: {settings.EMAIL_USE_SSL}, TLS: {settings.EMAIL_USE_TLS}")
        
        if settings.EMAIL_USE_SSL:
            print_info("Используем SMTP_SSL (порт 465)")
            server = smtplib.SMTP_SSL(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=10)
        else:
            print_info("Используем обычный SMTP")
            server = smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=10)
            
            if settings.EMAIL_USE_TLS:
                print_info("Запускаем STARTTLS...")
                server.starttls()
        
        print_success("Соединение установлено")
        
        # Пробуем авторизацию
        print_info(f"Авторизуемся как {settings.EMAIL_USERNAME}...")
        server.login(settings.EMAIL_USERNAME, settings.EMAIL_PASSWORD)
        print_success("Авторизация успешна!")
        
        server.quit()
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        print_error(f"Ошибка аутентификации SMTP: {e}")
        print_warning("Проверьте EMAIL_USERNAME и EMAIL_PASSWORD в .env")
        return False
        
    except smtplib.SMTPException as e:
        print_error(f"SMTP ошибка: {e}")
        return False
        
    except Exception as e:
        print_error(f"Неожиданная ошибка: {e}")
        import traceback
        print(traceback.format_exc())
        return False

async def test_email_sending(settings):
    """Тест 4: Отправка тестового email"""
    print_header("ТЕСТ 4: Отправка тестового email")
    
    test_email = input(f"{CYAN}Введите email для тестовой отправки (Enter для пропуска): {RESET}").strip()
    
    if not test_email:
        print_warning("Тест отправки email пропущен")
        return None
    
    try:
        from backend.services.email_service import EmailService
        
        code = "123456"
        print_info(f"Отправляем код {code} на {test_email}...")
        
        html_content = EmailService._create_verification_email_html(code, test_email)
        
        result = EmailService._send_email_smtp(
            to_email=test_email,
            subject=f"Тестовый код Voicyfy: {code}",
            html_content=html_content
        )
        
        if result:
            print_success(f"Email успешно отправлен на {test_email}!")
            print_info("Проверьте почтовый ящик (в том числе спам)")
            return True
        else:
            print_error("Email не отправлен")
            return False
            
    except Exception as e:
        print_error(f"Ошибка отправки email: {e}")
        import traceback
        print(traceback.format_exc())
        return False

async def test_uuid_token_generation():
    """Тест 5: Проверка генерации JWT токена с UUID"""
    print_header("ТЕСТ 5: Генерация JWT токена с UUID")
    
    try:
        from backend.core.security import create_jwt_token
        import uuid
        
        # Тест 1: UUID объект
        test_uuid = uuid.uuid4()
        print_info(f"Тестовый UUID: {test_uuid}")
        
        token1 = create_jwt_token(test_uuid)
        print_success(f"Токен из UUID создан: {token1[:50]}...")
        
        # Тест 2: Строка UUID
        token2 = create_jwt_token(str(test_uuid))
        print_success(f"Токен из строки создан: {token2[:50]}...")
        
        # Тест 3: Декодирование токена
        from backend.core.security import decode_jwt_token
        decoded = decode_jwt_token(token1)
        print_success(f"Токен декодирован: user_id={decoded['sub'][:8]}...")
        
        return True
        
    except Exception as e:
        print_error(f"Ошибка генерации токена: {e}")
        import traceback
        print(traceback.format_exc())
        return False

async def test_user_registration_flow(settings):
    """Тест 6: Полный flow регистрации с верификацией"""
    print_header("ТЕСТ 6: Полный flow регистрации")
    
    test_email = input(f"{CYAN}Введите email существующего пользователя (Enter для пропуска): {RESET}").strip()
    
    if not test_email:
        print_warning("Тест регистрации пропущен")
        return None
    
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.models.user import User
        from backend.models.email_verification import EmailVerification
        from backend.services.email_service import EmailService
        
        engine = create_engine(settings.DATABASE_URL)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        # Проверяем пользователя
        user = db.query(User).filter(User.email == test_email).first()
        
        if not user:
            print_error(f"Пользователь {test_email} не найден в БД")
            print_info("Сначала создайте пользователя через API или интерфейс")
            return False
        
        print_success(f"Пользователь найден: {user.email}")
        print_info(f"User ID: {user.id}")
        print_info(f"Email verified: {user.email_verified}")
        
        # Отправляем код верификации
        print_info("\nОтправка кода верификации...")
        
        result = await EmailService.send_verification_code(
            db=db,
            user_id=str(user.id),
            user_email=test_email
        )
        
        print_success("Код верификации создан и отправлен!")
        print_info(f"Срок действия: {result.get('expires_in_minutes')} минут")
        print_info(f"Макс. попыток: {result.get('max_attempts')}")
        
        # Проверяем запись в БД
        verification = EmailVerification.get_active_code_for_user(db, user.id)
        if verification:
            print_success(f"Код в БД: {verification.code[:2]}****")
            print_info(f"Полный код (для теста): {verification.code}")
            print_info(f"Истекает: {verification.expires_at}")
            print_info(f"Попыток: {verification.attempts}/{result.get('max_attempts')}")
        
        db.close()
        return True
        
    except Exception as e:
        print_error(f"Ошибка теста регистрации: {e}")
        import traceback
        print(traceback.format_exc())
        return False

async def test_verification_code_validation(settings):
    """Тест 7: Проверка валидации кода"""
    print_header("ТЕСТ 7: Проверка валидации кода")
    
    test_email = input(f"{CYAN}Введите email для проверки кода (Enter для пропуска): {RESET}").strip()
    
    if not test_email:
        print_warning("Тест валидации пропущен")
        return None
    
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.models.user import User
        from backend.models.email_verification import EmailVerification
        from backend.services.email_service import EmailService
        
        engine = create_engine(settings.DATABASE_URL)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        user = db.query(User).filter(User.email == test_email).first()
        if not user:
            print_error(f"Пользователь {test_email} не найден")
            return False
        
        # Получаем активный код
        verification = EmailVerification.get_active_code_for_user(db, user.id)
        
        if not verification:
            print_warning("Нет активного кода верификации")
            print_info("Сначала запустите Тест 6 для отправки кода")
            return False
        
        print_info(f"Найден код: {verification.code[:2]}****")
        print_success(f"ПОЛНЫЙ КОД ДЛЯ ТЕСТА: {verification.code}")
        
        # Спрашиваем код у пользователя
        input_code = input(f"{CYAN}Введите код для проверки: {RESET}").strip()
        
        if not input_code:
            print_warning("Код не введён")
            return None
        
        # Проверяем код
        print_info("Проверяем код...")
        result = await EmailService.verify_code(
            db=db,
            user_id=str(user.id),
            code=input_code
        )
        
        if result.get('success'):
            print_success("✅ КОД ВЕРИФИЦИРОВАН УСПЕШНО!")
            print_success(f"JWT токен создан: {result.get('token', '')[:50]}...")
            print_info(f"Email verified: {result.get('user', {}).get('email_verified')}")
        else:
            print_error("Верификация не удалась")
        
        db.close()
        return result.get('success', False)
        
    except Exception as e:
        print_error(f"Ошибка валидации кода: {e}")
        import traceback
        print(traceback.format_exc())
        return False

async def main():
    """Главная функция запуска всех тестов"""
    print(f"\n{CYAN}{BOLD}{'🚀'*30}{RESET}")
    print(f"{CYAN}{BOLD}ТЕСТИРОВАНИЕ EMAIL VERIFICATION СИСТЕМЫ{RESET}")
    print(f"{CYAN}{BOLD}{'🚀'*30}{RESET}")
    
    results = {}
    
    # Тест 0: Импорты
    imports_ok, settings = test_imports()
    results['Импорты'] = imports_ok
    
    if not imports_ok or not settings:
        print_error("\n❌ Не удалось загрузить настройки. Остановка тестов.")
        return
    
    # Тест 1: SMTP Config
    results['SMTP Config'] = test_smtp_config(settings)
    
    # Тест 2: Database
    results['Database'] = test_database_connection(settings)
    
    # Тест 3: SMTP Connection
    results['SMTP Connection'] = test_smtp_connection(settings)
    
    # Тест 4: Email Sending
    results['Email Sending'] = await test_email_sending(settings)
    
    # Тест 5: UUID Token
    results['UUID Token'] = await test_uuid_token_generation()
    
    # Тест 6: Full Registration
    results['Full Registration'] = await test_user_registration_flow(settings)
    
    # Тест 7: Code Validation
    results['Code Validation'] = await test_verification_code_validation(settings)
    
    # Итоговый отчет
    print_header("ИТОГОВЫЙ ОТЧЁТ")
    
    for test_name, result in results.items():
        if result is True:
            print_success(f"{test_name}: ✅ PASSED")
        elif result is False:
            print_error(f"{test_name}: ❌ FAILED")
        elif result is None:
            print_warning(f"{test_name}: ⏭️  SKIPPED")
    
    passed = sum(1 for r in results.values() if r is True)
    failed = sum(1 for r in results.values() if r is False)
    skipped = sum(1 for r in results.values() if r is None)
    total = len(results)
    
    print(f"\n{CYAN}{BOLD}{'='*60}{RESET}")
    print(f"{GREEN}✅ Пройдено: {passed}{RESET}")
    print(f"{RED}❌ Провалено: {failed}{RESET}")
    print(f"{YELLOW}⏭️  Пропущено: {skipped}{RESET}")
    print(f"{BLUE}📊 Всего: {total}{RESET}")
    print(f"{CYAN}{BOLD}{'='*60}{RESET}")
    
    if failed > 0:
        print(f"\n{RED}⚠️  Обнаружены проблемы! Проверьте логи выше.{RESET}")
    elif passed == total:
        print(f"\n{GREEN}🎉 Все тесты пройдены успешно!{RESET}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}⚠️  Тестирование прервано пользователем{RESET}")
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        import traceback
        print(traceback.format_exc())
