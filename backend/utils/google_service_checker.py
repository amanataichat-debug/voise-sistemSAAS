# backend/utils/google_service_checker.py

import os
import json
import logging
from typing import Dict, Any, Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

def check_google_service_account() -> Dict[str, Any]:
    """
    Проверить настройку сервисного аккаунта Google
    
    Returns:
        Dict с результатами проверки
    """
    results = {
        "success": False,
        "env_var_exists": False,
        "json_valid": False,
        "required_fields": False,
        "private_key_valid": False,
        "details": {},
        "recommendations": []
    }
    
    # Проверка наличия переменной окружения
    env_var = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    results["env_var_exists"] = env_var is not None
    
    if not env_var:
        results["details"]["error"] = "Переменная окружения GOOGLE_SERVICE_ACCOUNT_JSON не найдена"
        results["recommendations"].append("Установите переменную окружения GOOGLE_SERVICE_ACCOUNT_JSON со значением JSON сервисного аккаунта")
        return results
    
    # Проверка на валидность JSON
    try:
        service_account_info = json.loads(env_var)
        results["json_valid"] = True
        results["details"]["parsed_json"] = True
    except json.JSONDecodeError as e:
        results["details"]["error"] = f"Невозможно декодировать JSON: {str(e)}"
        results["details"]["sample"] = env_var[:50] + "..." if len(env_var) > 50 else env_var
        results["recommendations"].append("Убедитесь, что значение переменной GOOGLE_SERVICE_ACCOUNT_JSON является валидным JSON")
        return results
    
    # Проверка наличия необходимых полей
    required_fields = ["type", "project_id", "private_key_id", "private_key", "client_email", "client_id"]
    missing_fields = [field for field in required_fields if field not in service_account_info]
    
    results["required_fields"] = len(missing_fields) == 0
    
    if missing_fields:
        results["details"]["missing_fields"] = missing_fields
        results["recommendations"].append(f"JSON сервисного аккаунта должен содержать поля: {', '.join(missing_fields)}")
    
    # Проверка формата private_key
    if "private_key" in service_account_info:
        private_key = service_account_info["private_key"]
        
        # Проверяем, что ключ начинается и заканчивается правильно
        if private_key.startswith("-----BEGIN PRIVATE KEY-----") and private_key.endswith("-----END PRIVATE KEY-----"):
            results["private_key_valid"] = True
        else:
            results["details"]["private_key_issue"] = "Некорректный формат private_key"
            results["recommendations"].append("Формат private_key должен начинаться с '-----BEGIN PRIVATE KEY-----' и заканчиваться '-----END PRIVATE KEY-----'")
        
        # Проверяем наличие переносов строк в ключе
        if "\\n" in private_key and "\n" not in private_key:
            results["details"]["private_key_issue"] = "В private_key обнаружены экранированные переносы строк (\\n), которые должны быть заменены на реальные переносы"
            results["recommendations"].append("Замените экранированные переносы строк '\\n' на реальные переносы строк в private_key")
    
    # Итоговый результат
    results["success"] = (
        results["env_var_exists"] and 
        results["json_valid"] and 
        results["required_fields"] and 
        results["private_key_valid"]
    )
    
    if results["success"]:
        results["recommendations"].append("Сервисный аккаунт настроен корректно")
    
    # Дополнительная информация для отладки
    if "client_email" in service_account_info:
        results["details"]["client_email"] = service_account_info["client_email"]
    
    if "project_id" in service_account_info:
        results["details"]["project_id"] = service_account_info["project_id"]
    
    return results
