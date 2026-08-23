# backend/functions/write_google_doc.py
"""
Функция для добавления текста в конец Google Документа.
Использует тот же Service Account что и Google Таблицы.
"""
import re
import asyncio
from typing import Dict, Any, Optional

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function
from backend.services.google_sheets_service import GoogleSheetsService

logger = get_logger(__name__)


def extract_document_id(url: str) -> Optional[str]:
    """Извлекает ID документа из URL Google Docs."""
    patterns = [
        r'docs\.google\.com/document/d/([a-zA-Z0-9-_]+)',
        r'drive\.google\.com/file/d/([a-zA-Z0-9-_]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


@register_function
class WriteGoogleDocFunction(FunctionBase):
    """Функция для добавления текста в конец Google Документа через Service Account"""

    @classmethod
    def get_name(cls) -> str:
        return "write_google_doc"

    @classmethod
    def get_display_name(cls) -> str:
        return "Запись в Google Документ"

    @classmethod
    def get_description(cls) -> str:
        return "Добавляет текст в конец Google Документа. Документ должен быть расшарен на сервисный аккаунт с правами Редактор."

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL Google Документа"
                },
                "text": {
                    "type": "string",
                    "description": "Текст для добавления в конец документа"
                },
                "heading": {
                    "type": "string",
                    "description": "Заголовок перед текстом (опционально). Например: 'Звонок 10.05.2025 — Иван Петров'"
                }
            },
            "required": ["url", "text"]
        }

    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p>Ты можешь использовать функцию <code>write_google_doc</code> для записи итогов разговора в Google Документ.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>Нужно зафиксировать итоги звонка или встречи</li>
    <li>Ведётся лог заявок или обращений в свободном формате</li>
    <li>Требуется добавить заметку с заголовком и описанием</li>
</ul>

<p><strong>Параметры функции:</strong></p>
<ul>
    <li><code>url</code> — ссылка на Google Документ (сервисный аккаунт должен иметь права Редактор)</li>
    <li><code>text</code> — текст для добавления в конец документа</li>
    <li><code>heading</code> — заголовок перед текстом (опционально)</li>
</ul>

<p><strong>Пример вызова:</strong></p>
<pre>{
  "url": "https://docs.google.com/document/d/1ABC123XYZ456/edit",
  "heading": "Звонок 10.05.2025 — Иван Петров",
  "text": "Телефон: +79991234567\\nИнтерес: Тариф Про\\nРезультат: Отправить КП"
}</pre>

<p><strong>⚙️ Настройка документа:</strong></p>
<ol>
    <li>Откройте Google Документ</li>
    <li>Нажмите «Настроить доступ» → добавьте email сервисного аккаунта с правами «Редактор»</li>
    <li>Email сервисного аккаунта указан в настройках интеграций Voksy AI</li>
</ol>

<p><strong>Обработка ошибок:</strong></p>
<ul>
    <li><strong>403</strong> → документ не расшарен на сервисный аккаунт с правами Редактор</li>
    <li><strong>404</strong> → документ не найден, проверьте URL</li>
</ul>
"""

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Добавляет текст в конец Google Документа через Service Account."""
        try:
            url = arguments.get("url")
            text = arguments.get("text", "")
            heading = arguments.get("heading", "")

            if not url:
                return {"error": "URL документа обязателен"}

            if not text:
                return {"error": "Текст для добавления обязателен"}

            doc_id = extract_document_id(url)
            if not doc_id:
                return {"error": "Некорректный URL Google Документа"}

            logger.info(f"[WRITE_GOOGLE_DOC] ━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[WRITE_GOOGLE_DOC] 📄 Document ID: {doc_id}")
            logger.info(f"[WRITE_GOOGLE_DOC] 📝 Heading: {heading or '(none)'}")
            logger.info(f"[WRITE_GOOGLE_DOC] 📝 Text length: {len(text)} chars")

            loop = asyncio.get_event_loop()

            def write_to_doc_sync():
                try:
                    logger.info("[WRITE_GOOGLE_DOC] 🔧 Getting Docs service...")
                    service = GoogleSheetsService._get_docs_service()
                    logger.info("[WRITE_GOOGLE_DOC] ✅ Docs service obtained")

                    # Get current document to find the end index
                    doc = service.documents().get(documentId=doc_id).execute()
                    content = doc.get('body', {}).get('content', [])
                    end_index = content[-1]['endIndex'] - 1 if content else 1

                    logger.info(f"[WRITE_GOOGLE_DOC] 📍 Inserting at index: {end_index}")

                    # Build the full text to insert
                    if heading:
                        full_text = f"{heading}\n{text}\n"
                    else:
                        full_text = f"{text}\n"

                    requests = []

                    # Step 1: insert the full text at end_index
                    requests.append({
                        'insertText': {
                            'location': {'index': end_index},
                            'text': full_text
                        }
                    })

                    # Step 2: apply HEADING_2 style to heading line (if provided)
                    if heading:
                        requests.append({
                            'updateParagraphStyle': {
                                'range': {
                                    'startIndex': end_index,
                                    'endIndex': end_index + len(heading) + 1  # +1 for \n
                                },
                                'paragraphStyle': {'namedStyleType': 'HEADING_2'},
                                'fields': 'namedStyleType'
                            }
                        })

                    logger.info(f"[WRITE_GOOGLE_DOC] 🚀 Sending batchUpdate ({len(requests)} request(s))...")

                    service.documents().batchUpdate(
                        documentId=doc_id,
                        body={'requests': requests}
                    ).execute()

                    logger.info("[WRITE_GOOGLE_DOC] ✅ batchUpdate successful")
                    logger.info(f"[WRITE_GOOGLE_DOC] ✅ Added {len(full_text)} chars to document")
                    logger.info("[WRITE_GOOGLE_DOC] ━━━━━━━━━━━━━━━━━━━━━━━━━")

                    return {
                        "success": True,
                        "message": "Текст успешно добавлен в документ",
                        "document_id": doc_id,
                        "heading": heading or None,
                        "chars_added": len(full_text)
                    }

                except Exception as e:
                    error_str = str(e)
                    logger.error(f"[WRITE_GOOGLE_DOC] ❌ Ошибка: {error_str}")

                    if "403" in error_str or "PERMISSION_DENIED" in error_str:
                        return {
                            "success": False,
                            "error": "Доступ запрещен",
                            "details": "Документ должен быть расшарен на сервисный аккаунт с правами Редактор",
                            "instruction": "Откройте документ → «Настроить доступ» → добавьте email сервисного аккаунта с правами «Редактор»"
                        }
                    elif "404" in error_str or "NOT_FOUND" in error_str:
                        return {
                            "success": False,
                            "error": "Документ не найден",
                            "details": "Проверьте правильность URL документа"
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"Ошибка при записи в документ: {error_str}"
                        }

            logger.info("[WRITE_GOOGLE_DOC] ⏳ Running in thread pool...")
            result = await loop.run_in_executor(None, write_to_doc_sync)
            return result

        except Exception as e:
            logger.error(f"[WRITE_GOOGLE_DOC] ❌ Критическая ошибка: {str(e)}")
            return {
                "success": False,
                "error": f"Критическая ошибка: {str(e)}"
            }
