# backend/functions/add_google_sheet_row.py
"""
Функция для добавления строки в Google Таблицу.
Использует Service Account для записи в публичные/доступные таблицы.
"""
import re
import asyncio
from typing import Dict, Any, Optional

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function
from backend.services.google_sheets_service import GoogleSheetsService

logger = get_logger(__name__)

def extract_spreadsheet_id(url: str) -> Optional[str]:
    """Извлекает ID таблицы из URL"""
    match = re.search(r'docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None

@register_function
class AddGoogleSheetRowFunction(FunctionBase):
    """Функция для добавления строки в Google Таблицу через Service Account"""
    
    @classmethod
    def get_name(cls) -> str:
        return "add_google_sheet_row"
    
    @classmethod
    def get_display_name(cls) -> str:
        return "Добавление строки в Google Таблицу"
    
    @classmethod
    def get_description(cls) -> str:
        return "Добавляет новую строку в Google Таблицу. Таблица должна быть доступна для редактирования."
    
    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL Google Таблицы"
                },
                "row_to_append": {
                    "type": "string",
                    "description": "Данные для добавления, разделенные ;; (например: 'Иван Петров;;ivan@mail.com;;+79991234567')"
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Название листа (опционально, по умолчанию первый лист)"
                }
            },
            "required": ["url", "row_to_append"]
        }
    
    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p>Ты можешь использовать функцию <code>add_google_sheet_row</code> для записи данных в Google Таблицы.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>Пользователь просит записать/сохранить информацию в таблицу</li>
    <li>Нужно добавить контактные данные, заявку или бронирование</li>
    <li>Требуется логировать действия или события</li>
    <li>Пользователь хочет оставить feedback или заполнить форму</li>
    <li>Сбор лидов, опросов, регистраций</li>
</ul>

<p><strong>Параметры функции:</strong></p>
<ul>
    <li><code>url</code> — ссылка на Google Таблицу</li>
    <li><code>row_to_append</code> — данные через разделитель <code>;;</code></li>
    <li><code>sheet_name</code> — название листа (опционально)</li>
</ul>

<p><strong>Пример вызова:</strong></p>
<pre>{
  "url": "https://docs.google.com/spreadsheets/d/1ABC123XYZ456/edit",
  "row_to_append": "Иван Петров;;ivan@mail.com;;+79991234567;;Консультация;;15:00",
  "sheet_name": "Заявки"
}</pre>

<p><strong>Результат:</strong></p>
<pre>{
  "success": true,
  "message": "Строка успешно добавлена в таблицу (строка №42)",
  "spreadsheet_id": "1ABC123XYZ456",
  "sheet_name": "Заявки",
  "row_number": 42,
  "values_added": ["Иван Петров", "ivan@mail.com", ...],
  "cells_updated": 5
}</pre>

<p><strong>⚠️ ВАЖНО - Формат данных:</strong></p>
<ul>
    <li>Разделитель: <strong>двойная точка с запятой</strong> <code>;;</code></li>
    <li>Пример: <code>"Имя;;Email;;Телефон;;Услуга;;Время"</code></li>
    <li>Количество значений должно соответствовать количеству колонок</li>
    <li>Порядок важен — данные пойдут в колонки A, B, C, D...</li>
</ul>

<p><strong>Примеры данных:</strong></p>
<pre>Контакты:
"Иван Петров;;+79991234567;;ivan@mail.com"

Бронирование:
"Петр Сидоров;;2024-05-10;;15:00;;Массаж;;60 минут"

Заявка:
"ООО 'Компания';;Консультация;;contact@company.ru;;Срочно"</pre>

<p><strong>⚙️ Настройка таблицы:</strong></p>
<ol>
    <li>Создай Google Таблицу</li>
    <li>Настрой доступ: <strong>"Все, у кого есть ссылка → Редактор"</strong></li>
    <li>Или расшарь на сервисный аккаунт с правами редактора</li>
    <li>Первая строка (опционально) — заголовки колонок</li>
    <li>Данные будут добавляться в следующую пустую строку</li>
</ol>

<p><strong>💡 Примеры использования:</strong></p>
<ul>
    <li>"Запиши мои контакты: Иван, ivan@mail.com, +79991234567"</li>
    <li>"Забронируй на 15:00, услуга массаж, клиент Петр Сидоров"</li>
    <li>"Добавь заявку от компании ABC, нужна консультация"</li>
</ul>

<p><strong>Обработка ошибок:</strong></p>
<ul>
    <li><strong>403 PERMISSION_DENIED</strong> → таблица не расшарена, открой доступ</li>
    <li><strong>404 NOT_FOUND</strong> → неверная ссылка на таблицу</li>
    <li><strong>INVALID_ARGUMENT</strong> → неверное название листа</li>
</ul>
"""
    
    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Добавляет строку в Google Таблицу через ваш Service Account.
        """
        try:
            url = arguments.get("url")
            row_to_append = arguments.get("row_to_append", "")
            sheet_name = arguments.get("sheet_name", "")
            
            # Валидация
            if not url:
                return {"error": "URL таблицы обязателен"}
            
            if not row_to_append:
                return {"error": "Данные для добавления обязательны"}
            
            # Парсим данные (разделитель ;;)
            values = [v.strip() for v in row_to_append.split(";;")]
            
            if not values:
                return {"error": "Нет данных для добавления"}
            
            # Извлекаем ID таблицы
            spreadsheet_id = extract_spreadsheet_id(url)
            
            if not spreadsheet_id:
                return {"error": "Некорректный URL Google Таблицы"}
            
            logger.info(f"[ADD_GOOGLE_SHEET_ROW] ━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[ADD_GOOGLE_SHEET_ROW] 📊 Spreadsheet ID: {spreadsheet_id}")
            logger.info(f"[ADD_GOOGLE_SHEET_ROW] 📝 Values: {values}")
            logger.info(f"[ADD_GOOGLE_SHEET_ROW] 📄 Sheet: {sheet_name or 'default'}")
            
            # Используем существующий GoogleSheetsService с вашим Service Account
            loop = asyncio.get_event_loop()
            
            def append_row_sync():
                """Синхронная функция для выполнения в thread pool"""
                try:
                    logger.info("[ADD_GOOGLE_SHEET_ROW] 🔧 Получаем Sheets service...")
                    
                    # Используем ваш GoogleSheetsService (он сам знает про Service Account)
                    service = GoogleSheetsService._get_sheets_service()
                    
                    logger.info("[ADD_GOOGLE_SHEET_ROW] ✅ Service получен")
                    
                    # ✅ ИСПРАВЛЕНО: Определяем диапазон с кавычками для кириллицы
                    if sheet_name:
                        # Одинарные кавычки для поддержки кириллицы и спецсимволов
                        range_notation = f"'{sheet_name}'!A:Z"
                    else:
                        range_notation = "A:Z"  # Первый лист, все колонки
                    
                    logger.info(f"[ADD_GOOGLE_SHEET_ROW] 📍 Range: {range_notation}")
                    
                    # Подготавливаем тело запроса
                    body = {'values': [values]}
                    
                    logger.info("[ADD_GOOGLE_SHEET_ROW] 🚀 Отправляем запрос к Google Sheets API...")
                    
                    # Добавляем строку через API
                    result = service.spreadsheets().values().append(
                        spreadsheetId=spreadsheet_id,
                        range=range_notation,
                        valueInputOption='RAW',
                        insertDataOption='INSERT_ROWS',
                        body=body
                    ).execute()
                    
                    logger.info("[ADD_GOOGLE_SHEET_ROW] ✅ Запрос выполнен успешно!")
                    
                    # Получаем информацию о добавленной строке
                    updates = result.get('updates', {})
                    updated_range = updates.get('updatedRange', '')
                    updated_rows = updates.get('updatedRows', 0)
                    updated_cells = updates.get('updatedCells', 0)
                    
                    logger.info(f"[ADD_GOOGLE_SHEET_ROW] 📊 Updated range: {updated_range}")
                    logger.info(f"[ADD_GOOGLE_SHEET_ROW] 📊 Updated rows: {updated_rows}")
                    logger.info(f"[ADD_GOOGLE_SHEET_ROW] 📊 Updated cells: {updated_cells}")
                    
                    # Извлекаем номер строки из updated_range
                    # Формат: "Sheet1!A10:C10" → номер строки = 10
                    row_number = None
                    if updated_range:
                        match = re.search(r'!A(\d+):', updated_range)
                        if match:
                            row_number = int(match.group(1))
                    
                    logger.info("[ADD_GOOGLE_SHEET_ROW] ━━━━━━━━━━━━━━━━━━━━━━━━━")
                    logger.info(f"[ADD_GOOGLE_SHEET_ROW] ✅ УСПЕХ! Строка {row_number} добавлена")
                    logger.info("[ADD_GOOGLE_SHEET_ROW] ━━━━━━━━━━━━━━━━━━━━━━━━━")
                    
                    return {
                        "success": True,
                        "message": f"Строка успешно добавлена в таблицу{f' (строка №{row_number})' if row_number else ''}",
                        "spreadsheet_id": spreadsheet_id,
                        "sheet_name": sheet_name or "default",
                        "row_number": row_number,
                        "values_added": values,
                        "cells_updated": updated_cells
                    }
                    
                except Exception as e:
                    error_str = str(e)
                    logger.error(f"[ADD_GOOGLE_SHEET_ROW] ❌ Ошибка: {error_str}")
                    
                    # Понятные сообщения об ошибках
                    if "403" in error_str or "PERMISSION_DENIED" in error_str:
                        return {
                            "success": False,
                            "error": "Доступ запрещен",
                            "details": "Таблица должна быть расшарена на сервисный аккаунт с правами редактора",
                            "instruction": "Откройте таблицу → кнопка 'Настроить доступ' → добавьте email сервисного аккаунта с правами 'Редактор'"
                        }
                    elif "404" in error_str or "NOT_FOUND" in error_str:
                        return {
                            "success": False,
                            "error": "Таблица не найдена",
                            "details": "Проверьте правильность URL таблицы"
                        }
                    elif "INVALID_ARGUMENT" in error_str:
                        return {
                            "success": False,
                            "error": "Некорректные параметры",
                            "details": f"Проверьте название листа: '{sheet_name}'"
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"Ошибка при добавлении строки: {error_str}"
                        }
            
            # Выполняем синхронную функцию в thread pool
            logger.info("[ADD_GOOGLE_SHEET_ROW] ⏳ Запускаем выполнение...")
            result = await loop.run_in_executor(None, append_row_sync)
            
            return result
            
        except Exception as e:
            logger.error(f"[ADD_GOOGLE_SHEET_ROW] ❌ Критическая ошибка: {str(e)}")
            return {
                "success": False,
                "error": f"Критическая ошибка: {str(e)}"
            }
