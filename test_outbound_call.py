"""
Тестовый скрипт для диагностики исходящих звонков с реальными данными
"""
import asyncio
import httpx
import json

# РЕАЛЬНЫЕ ДАННЫЕ VOXIMPLANT
REAL_DATA = {
    "account_id": "9758383",
    "api_key": "c87130a0-5f46-44f8-9401-154947094986",
    "rule_id": "7957687",
    "script_custom_data": json.dumps({
        "phone_number": "+79500968479",
        "assistant_id": "test-assistant-id",  # Замени на реальный если нужно
        "caller_id": "74951087163"
    })
}

async def test_real_call():
    """Тестируем с реальными данными"""
    print("\n" + "="*70)
    print("🚀 ТЕСТ С РЕАЛЬНЫМИ ДАННЫМИ VOXIMPLANT")
    print("="*70)
    
    BASE_URL = "https://voicyfy.ru"
    ENDPOINT = f"{BASE_URL}/api/voximplant/start-outbound-call"
    
    print(f"\n📍 URL: {ENDPOINT}")
    print(f"\n📦 Данные запроса:")
    print(f"   Account ID: {REAL_DATA['account_id']}")
    print(f"   API Key: {REAL_DATA['api_key'][:20]}...")
    print(f"   Rule ID: {REAL_DATA['rule_id']}")
    
    script_data = json.loads(REAL_DATA['script_custom_data'])
    print(f"   Phone: {script_data['phone_number']}")
    print(f"   Caller ID: {script_data['caller_id']}")
    print(f"   Assistant ID: {script_data['assistant_id']}")
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            print("\n🚀 Отправляем POST запрос...")
            
            response = await client.post(
                ENDPOINT,
                json=REAL_DATA,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }
            )
            
            print(f"\n📊 РЕЗУЛЬТАТ:")
            print(f"   Статус: {response.status_code}")
            print(f"   Headers: {dict(response.headers)}")
            
            try:
                response_json = response.json()
                print(f"\n📄 Ответ (JSON):")
                print(json.dumps(response_json, indent=2, ensure_ascii=False))
                
                # Анализируем ответ
                if response.status_code == 200:
                    if response_json.get("success"):
                        print("\n✅ УСПЕХ! Звонок инициирован!")
                        if "call_id" in response_json:
                            print(f"   Call ID: {response_json['call_id']}")
                    else:
                        print(f"\n❌ ОШИБКА: {response_json.get('message', 'Неизвестная ошибка')}")
                        
                        # Детальный анализ ошибки
                        if "Authorization failed" in response_json.get('message', ''):
                            print("\n🔍 ДИАГНОСТИКА ОШИБКИ АВТОРИЗАЦИИ:")
                            print("   1. Проверьте Account ID в Voximplant")
                            print("   2. Проверьте API Key в Voximplant")
                            print("   3. Проверьте права доступа API ключа")
                        elif "Rule" in response_json.get('message', ''):
                            print("\n🔍 ДИАГНОСТИКА ОШИБКИ RULE:")
                            print("   1. Проверьте существование Rule ID: 7957687")
                            print("   2. Проверьте активность правила")
                        elif "phone" in response_json.get('message', '').lower():
                            print("\n🔍 ДИАГНОСТИКА ОШИБКИ НОМЕРА:")
                            print("   1. Проверьте формат номера: +79500968479")
                            print("   2. Проверьте Caller ID: 74951087163")
                else:
                    print(f"\n❌ HTTP ОШИБКА: {response.status_code}")
                    
            except json.JSONDecodeError:
                print(f"\n📄 Ответ (TEXT):")
                print(response.text)
                
    except httpx.TimeoutException:
        print("\n⏱️ ТАЙМАУТ: Сервер не ответил вовремя")
    except httpx.ConnectError:
        print("\n🔌 ОШИБКА ПОДКЛЮЧЕНИЯ: Не удалось подключиться к серверу")
    except Exception as e:
        print(f"\n❌ НЕОЖИДАННАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

async def test_voximplant_api_directly():
    """Тестируем Voximplant API напрямую"""
    print("\n" + "="*70)
    print("🔬 ПРЯМОЙ ТЕСТ VOXIMPLANT API")
    print("="*70)
    
    # URL Voximplant API
    VOXIMPLANT_URL = "https://api.voximplant.com/platform_api/StartScenarios/"
    
    params = {
        "account_id": REAL_DATA["account_id"],
        "api_key": REAL_DATA["api_key"],
        "rule_id": REAL_DATA["rule_id"],
        "script_custom_data": REAL_DATA["script_custom_data"]
    }
    
    print(f"\n📍 URL: {VOXIMPLANT_URL}")
    print(f"📦 Параметры:")
    for key, value in params.items():
        if key == "api_key":
            print(f"   {key}: {value[:20]}...")
        elif key == "script_custom_data":
            print(f"   {key}: {value[:50]}...")
        else:
            print(f"   {key}: {value}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print("\n🚀 Отправляем запрос в Voximplant...")
            
            response = await client.post(
                VOXIMPLANT_URL,
                data=params
            )
            
            print(f"\n📊 РЕЗУЛЬТАТ:")
            print(f"   Статус: {response.status_code}")
            
            try:
                response_json = response.json()
                print(f"\n📄 Ответ от Voximplant:")
                print(json.dumps(response_json, indent=2, ensure_ascii=False))
                
                if response_json.get("result"):
                    print("\n✅ Voximplant API работает корректно!")
                else:
                    print(f"\n❌ Ошибка Voximplant: {response_json.get('error', 'Unknown')}")
                    
            except json.JSONDecodeError:
                print(f"\n📄 Ответ (TEXT): {response.text}")
                
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")

async def main():
    """Главная функция"""
    print("\n" + "🔧"*35)
    print("ПОЛНАЯ ДИАГНОСТИКА С РЕАЛЬНЫМИ ДАННЫМИ")
    print("🔧"*35)
    
    # Тест 1: Через наш эндпоинт
    await test_real_call()
    
    # Тест 2: Напрямую в Voximplant API
    await test_voximplant_api_directly()
    
    print("\n" + "✅"*35)
    print("ДИАГНОСТИКА ЗАВЕРШЕНА")
    print("✅"*35 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
