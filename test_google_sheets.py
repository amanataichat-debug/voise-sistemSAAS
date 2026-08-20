# test_google_sheets.py
"""
Диагностика Google Sheets на Render
Запуск: python test_google_sheets.py
"""

import os
import json
import sys
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

print("=" * 60)
print("🔍 GOOGLE SHEETS DIAGNOSTICS")
print("=" * 60)

# 1. Проверка переменной окружения
print("\n1️⃣ Checking GOOGLE_SERVICE_ACCOUNT_JSON...")
service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

if not service_account_json:
    print("❌ GOOGLE_SERVICE_ACCOUNT_JSON NOT FOUND")
    sys.exit(1)

print(f"✅ Variable exists, length: {len(service_account_json)} chars")

# 2. Парсинг JSON
print("\n2️⃣ Parsing JSON...")
try:
    service_account_info = json.loads(service_account_json)
    print("✅ JSON valid")
except json.JSONDecodeError as e:
    print(f"❌ JSON INVALID: {e}")
    sys.exit(1)

# 3. Проверка обязательных полей
print("\n3️⃣ Checking required fields...")
required_fields = ["type", "project_id", "private_key_id", "private_key", "client_email"]
missing = [f for f in required_fields if f not in service_account_info]

if missing:
    print(f"❌ Missing fields: {missing}")
    sys.exit(1)

print(f"✅ All required fields present")
print(f"   Client email: {service_account_info['client_email']}")
print(f"   Project ID: {service_account_info['project_id']}")

# 4. Исправление формата private_key
print("\n4️⃣ Fixing private_key format...")
if "\\n" in service_account_info["private_key"]:
    service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")
    print("✅ Private key newlines fixed")
else:
    print("✅ Private key format OK")

# 5. Создание credentials
print("\n5️⃣ Creating credentials...")
try:
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    print(f"✅ Credentials created")
    print(f"   Service account: {credentials.service_account_email}")
except Exception as e:
    print(f"❌ Credentials creation failed: {e}")
    sys.exit(1)

# 6. Получение токена
print("\n6️⃣ Getting access token...")
try:
    import google.auth.transport.requests
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    print("✅ Token obtained successfully")
except Exception as e:
    print(f"❌ Token refresh failed: {e}")
    sys.exit(1)

# 7. Создание Sheets API service
print("\n7️⃣ Building Sheets API service...")
try:
    service = build('sheets', 'v4', credentials=credentials, cache_discovery=False)
    print("✅ Sheets API service created")
except Exception as e:
    print(f"❌ Service creation failed: {e}")
    sys.exit(1)

# 8. Тест на конкретной таблице (если есть)
print("\n8️⃣ Testing with actual spreadsheet...")
print("Enter your Google Sheet ID (or press Enter to skip):")
sheet_id = input().strip()

if sheet_id:
    try:
        # Проверяем доступ
        sheet = service.spreadsheets().get(
            spreadsheetId=sheet_id,
            fields='properties.title'
        ).execute()
        
        title = sheet.get('properties', {}).get('title', 'Untitled')
        print(f"✅ Sheet found: {title}")
        
        # Пробуем записать тестовую строку
        print("   Testing write access...")
        test_values = [["TEST - Diagnostic check"]]
        
        result = service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range='Z:Z',
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': test_values}
        ).execute()
        
        print(f"✅ Write successful!")
        print(f"   Updated: {result.get('updates', {}).get('updatedRange')}")
        
        # Удаляем тестовую запись
        update_range = result.get('updates', {}).get('updatedRange', 'Z1')
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=update_range,
            body={}
        ).execute()
        print("✅ Test data cleaned up")
        
    except HttpError as e:
        print(f"❌ HTTP Error: {e}")
        if e.resp.status == 403:
            print("   → Sheet not shared with service account")
            print(f"   → Share with: {service_account_info['client_email']}")
        elif e.resp.status == 404:
            print("   → Sheet ID not found")
    except Exception as e:
        print(f"❌ Error: {e}")
else:
    print("⏭️  Skipped sheet test")

print("\n" + "=" * 60)
print("✅ DIAGNOSTICS COMPLETE")
print("=" * 60)
