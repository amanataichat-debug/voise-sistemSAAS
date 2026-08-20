#!/usr/bin/env python3
"""
🔍 Диагностика Gemini API - берём ключ из БД как handler
Запуск: python test_gemini_db.py <assistant_id>

Пример: python test_gemini_db.py 6672d1cc-bd67-4200-b4f5-b29e4222046a
"""

import os
import sys
import json
import uuid

# Добавляем путь к проекту
sys.path.insert(0, '/opt/render/project/src')

def test_gemini_from_db(assistant_id: str):
    print("=" * 60)
    print("🔍 ДИАГНОСТИКА GEMINI API (из БД)")
    print("=" * 60)
    print(f"   Assistant ID: {assistant_id}")
    print()
    
    # 1. Подключаемся к БД
    print("📋 1. ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ:")
    print("-" * 40)
    
    db = None
    
    try:
        # Создаём сессию напрямую через SQLAlchemy
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        
        # Получаем URL базы данных
        database_url = os.environ.get('DATABASE_URL')
        
        if not database_url:
            # Пробуем из settings
            try:
                from backend.core.config import settings
                database_url = getattr(settings, 'DATABASE_URL', None)
            except:
                pass
        
        if not database_url:
            print("❌ DATABASE_URL не найден!")
            return
        
        # Исправляем postgres:// на postgresql://
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        
        print(f"   DATABASE_URL: {database_url[:40]}...")
        
        engine = create_engine(database_url)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        print("✅ Сессия БД создана")
            
    except Exception as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 2. Загружаем ассистента
    print("\n📋 2. ЗАГРУЗКА АССИСТЕНТА:")
    print("-" * 40)
    
    try:
        from backend.models.gemini_assistant import GeminiAssistantConfig
        
        try:
            uuid_obj = uuid.UUID(assistant_id)
            assistant = db.query(GeminiAssistantConfig).get(uuid_obj)
        except ValueError:
            assistant = db.query(GeminiAssistantConfig).filter(
                GeminiAssistantConfig.id.cast(str) == assistant_id
            ).first()
        
        if assistant:
            print(f"✅ Ассистент найден:")
            print(f"   ID: {assistant.id}")
            print(f"   Name: {assistant.name}")
            print(f"   User ID: {assistant.user_id}")
            print(f"   Voice: {assistant.voice}")
        else:
            print(f"❌ Ассистент не найден: {assistant_id}")
            db.close()
            return
            
    except Exception as e:
        print(f"❌ Ошибка загрузки ассистента: {e}")
        import traceback
        traceback.print_exc()
        db.close()
        return
    
    # 3. Загружаем пользователя и его API key
    print("\n📋 3. ЗАГРУЗКА ПОЛЬЗОВАТЕЛЯ И API KEY:")
    print("-" * 40)
    
    api_key = None
    
    try:
        from backend.models.user import User
        
        if assistant.user_id:
            user = db.query(User).get(assistant.user_id)
            
            if user:
                print(f"✅ Пользователь найден:")
                print(f"   ID: {user.id}")
                print(f"   Email: {user.email}")
                
                api_key = user.gemini_api_key
                
                if api_key:
                    print(f"✅ Gemini API Key:")
                    print(f"   Значение: {api_key[:15]}...{api_key[-5:]}")
                    print(f"   Длина: {len(api_key)} символов")
                    
                    # Проверяем формат ключа
                    if api_key.startswith("AIza"):
                        print(f"   Формат: ✅ Валидный Google API key")
                    else:
                        print(f"   Формат: ⚠️ Необычный (должен начинаться с 'AIza')")
                else:
                    print(f"❌ user.gemini_api_key = None!")
                    print(f"   Добавьте Gemini API key в настройках пользователя")
                    db.close()
                    return
            else:
                print(f"❌ Пользователь не найден: {assistant.user_id}")
                db.close()
                return
        else:
            print(f"❌ У ассистента нет user_id")
            db.close()
            return
            
    except Exception as e:
        print(f"❌ Ошибка загрузки пользователя: {e}")
        import traceback
        traceback.print_exc()
        db.close()
        return
    
    db.close()
    print("\n✅ Сессия БД закрыта")
    
    # 4. Тестируем API запрос
    print("\n📋 4. ТЕСТ HTTP ЗАПРОСА К GEMINI API:")
    print("-" * 40)
    
    import urllib.request
    import urllib.error
    
    model = "gemini-2.0-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": "Скажи одно слово: привет"}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 10}
    }
    
    print(f"   Модель: {model}")
    print(f"   API Key: {api_key[:10]}...{api_key[-5:]}")
    
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method='POST')
        
        print(f"\n   Отправляем запрос...")
        
        with urllib.request.urlopen(req, timeout=30) as response:
            response_text = response.read().decode('utf-8')
            response_json = json.loads(response_text)
            
            print(f"\n✅ ✅ ✅ УСПЕХ! API РАБОТАЕТ! ✅ ✅ ✅")
            print(f"   Status: {response.status}")
            
            candidates = response_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    print(f"   Ответ: '{parts[0].get('text', '')}'")
            
            print(f"\n   Ключ валидный! Ошибка 429 была временной.")
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        
        print(f"\n❌ HTTP ОШИБКА: {e.code}")
        print(f"   {error_body[:300]}")
        
        print(f"\n📋 ДИАГНОЗ:")
        if e.code == 429:
            print("   ⚠️ Rate Limit - подождите 1-2 минуты")
        elif e.code == 401:
            print("   ⚠️ Ключ недействителен - создайте новый")
        elif e.code == 403:
            print("   ⚠️ API не включён в Google Cloud")
            
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
    
    print("\n" + "=" * 60)
    print("🏁 ГОТОВО")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_gemini_db.py <assistant_id>")
        sys.exit(1)
    test_gemini_from_db(sys.argv[1])
