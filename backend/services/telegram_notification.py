# backend/services/telegram_notification.py
"""
🔔 Telegram Notification Service v1.0
=====================================

Сервис для отправки уведомлений о новых звонках в Telegram.
Отправляет информацию о звонке, диалог и ссылку на запись.

ИСПОЛЬЗОВАНИЕ:
    from backend.services.telegram_notification import TelegramNotificationService
    
    await TelegramNotificationService.send_call_notification(
        bot_token="123456:ABC...",
        chat_id="-1001234567890",  # Группа/канал (отрицательный) или личный чат
        assistant_name="Продажи",
        caller_number="+7 999 123-45-67",
        duration_seconds=225,
        call_cost=2.50,
        dialog=[
            {"role": "assistant", "text": "Здравствуйте!", "ts": 1737267554000},
            {"role": "user", "text": "Привет", "ts": 1737267558000}
        ],
        record_url="https://r2.voicyfy.ru/recordings/..."
    )

ЛИМИТЫ TELEGRAM:
- Максимальная длина сообщения: 4096 символов
- Если диалог длинный, он обрезается с сохранением начала и конца
"""

import asyncio
import aiohttp
from typing import Optional, List, Dict, Any
from datetime import datetime

from backend.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

class TelegramConfig:
    """Конфигурация Telegram Notification Service"""
    
    # Лимит сообщения Telegram
    MAX_MESSAGE_LENGTH = 4096
    
    # Резерв для метаданных (заголовок, стоимость, ссылка и т.д.)
    METADATA_RESERVE = 500
    
    # Максимальная длина диалога (с учётом резерва)
    MAX_DIALOG_LENGTH = MAX_MESSAGE_LENGTH - METADATA_RESERVE
    
    # Таймаут запросов к Telegram API
    REQUEST_TIMEOUT = 30.0
    
    # Telegram Bot API URL
    API_URL = "https://api.telegram.org/bot{token}/sendMessage"
    
    # Эмодзи для ролей
    EMOJI_ASSISTANT = "🤖"
    EMOJI_USER = "👤"
    EMOJI_PHONE = "📞"
    EMOJI_TIME = "⏱"
    EMOJI_COST = "💰"
    EMOJI_RECORD = "🎧"
    EMOJI_DIALOG = "💬"


# =============================================================================
# TELEGRAM NOTIFICATION SERVICE
# =============================================================================

class TelegramNotificationService:
    """
    Сервис для отправки уведомлений о звонках в Telegram.
    """
    
    @staticmethod
    def format_duration(seconds: Optional[float]) -> str:
        """
        Форматирует длительность в читаемый формат.
        
        Args:
            seconds: Длительность в секундах
            
        Returns:
            Строка вида "3:45" или "0:00"
        """
        if not seconds:
            return "0:00"
        
        try:
            total_seconds = int(seconds)
            minutes = total_seconds // 60
            secs = total_seconds % 60
            return f"{minutes}:{secs:02d}"
        except (ValueError, TypeError):
            return "0:00"
    
    @staticmethod
    def format_cost(cost: Optional[float]) -> str:
        """
        Форматирует стоимость в рублях.
        
        Args:
            cost: Стоимость звонка
            
        Returns:
            Строка вида "2.50 ₽" или "—"
        """
        if cost is None or cost == 0:
            return "—"
        
        try:
            return f"{float(cost):.2f} ₽"
        except (ValueError, TypeError):
            return "—"
    
    @staticmethod
    def format_phone(phone: Optional[str]) -> str:
        """
        Форматирует номер телефона для отображения.
        Убирает префиксы INBOUND:/OUTBOUND: если есть.
        
        Args:
            phone: Номер телефона
            
        Returns:
            Очищенный номер телефона
        """
        if not phone:
            return "Неизвестный"
        
        # Убираем префиксы направления
        cleaned = phone
        if cleaned.startswith("INBOUND:"):
            cleaned = cleaned[8:]
        elif cleaned.startswith("OUTBOUND:"):
            cleaned = cleaned[9:]
        
        return cleaned.strip() or "Неизвестный"
    
    @staticmethod
    def format_dialog(
        dialog: Optional[List[Dict[str, Any]]], 
        max_length: int = TelegramConfig.MAX_DIALOG_LENGTH
    ) -> str:
        """
        Форматирует структурированный диалог для Telegram.
        Обрезает если превышает лимит, сохраняя начало и конец.
        
        Args:
            dialog: Список реплик [{"role": "user/assistant", "text": "...", "ts": ...}]
            max_length: Максимальная длина результата
            
        Returns:
            Отформатированный диалог
        """
        if not dialog or not isinstance(dialog, list) or len(dialog) == 0:
            return "Нет данных о диалоге"
        
        # Форматируем все реплики
        formatted_lines = []
        for turn in dialog:
            role = turn.get("role", "unknown")
            text = turn.get("text", "").strip()
            
            if not text:
                continue
            
            # Выбираем эмодзи
            emoji = TelegramConfig.EMOJI_ASSISTANT if role == "assistant" else TelegramConfig.EMOJI_USER
            
            # Ограничиваем длину отдельной реплики (макс 500 символов)
            if len(text) > 500:
                text = text[:497] + "..."
            
            formatted_lines.append(f"{emoji}: {text}")
        
        if not formatted_lines:
            return "Нет данных о диалоге"
        
        # Собираем полный диалог
        full_dialog = "\n".join(formatted_lines)
        
        # Если укладываемся в лимит — возвращаем как есть
        if len(full_dialog) <= max_length:
            return full_dialog
        
        # Иначе обрезаем, сохраняя начало и конец
        logger.info(f"[TELEGRAM] Dialog too long ({len(full_dialog)} chars), truncating...")
        
        # Стратегия: показываем первые N и последние M реплик
        total_turns = len(formatted_lines)
        
        if total_turns <= 4:
            # Мало реплик — просто обрезаем текст
            truncated = full_dialog[:max_length - 50]
            last_newline = truncated.rfind("\n")
            if last_newline > max_length // 2:
                truncated = truncated[:last_newline]
            return truncated + "\n\n[...сообщение обрезано...]"
        
        # Берём первые 2 и последние 2 реплики
        first_part = "\n".join(formatted_lines[:2])
        last_part = "\n".join(formatted_lines[-2:])
        skipped = total_turns - 4
        
        middle_marker = f"\n\n[...ещё {skipped} реплик...]\n\n"
        
        result = first_part + middle_marker + last_part
        
        # Если всё ещё не влезает — обрезаем агрессивнее
        if len(result) > max_length:
            # Только первая и последняя реплика
            first_line = formatted_lines[0]
            last_line = formatted_lines[-1]
            skipped = total_turns - 2
            
            # Обрезаем каждую реплику до 300 символов
            if len(first_line) > 300:
                first_line = first_line[:297] + "..."
            if len(last_line) > 300:
                last_line = last_line[:297] + "..."
            
            result = f"{first_line}\n\n[...ещё {skipped} реплик...]\n\n{last_line}"
        
        return result
    
    @staticmethod
    def build_message(
        assistant_name: str,
        caller_number: Optional[str],
        duration_seconds: Optional[float],
        call_cost: Optional[float],
        dialog: Optional[List[Dict[str, Any]]],
        record_url: Optional[str],
        call_direction: Optional[str] = None
    ) -> str:
        """
        Собирает полное сообщение для отправки в Telegram.
        
        Args:
            assistant_name: Имя ассистента
            caller_number: Номер телефона
            duration_seconds: Длительность в секундах
            call_cost: Стоимость звонка
            dialog: Структурированный диалог
            record_url: Ссылка на запись
            call_direction: Направление звонка (inbound/outbound)
            
        Returns:
            Готовое сообщение для Telegram
        """
        # Заголовок с направлением
        direction_emoji = "📥" if call_direction == "inbound" else "📤" if call_direction == "outbound" else "📞"
        direction_text = "Входящий" if call_direction == "inbound" else "Исходящий" if call_direction == "outbound" else "Звонок"
        
        header = f"{direction_emoji} <b>Новый {direction_text.lower()} звонок</b>\n\n"
        
        # Метаданные
        metadata_lines = [
            f"{TelegramConfig.EMOJI_ASSISTANT} <b>Ассистент:</b> {assistant_name}",
            f"{TelegramConfig.EMOJI_USER} <b>Номер:</b> {TelegramNotificationService.format_phone(caller_number)}",
            f"{TelegramConfig.EMOJI_TIME} <b>Длительность:</b> {TelegramNotificationService.format_duration(duration_seconds)}",
        ]
        
        # Стоимость (только если есть)
        cost_formatted = TelegramNotificationService.format_cost(call_cost)
        if cost_formatted != "—":
            metadata_lines.append(f"{TelegramConfig.EMOJI_COST} <b>Стоимость:</b> {cost_formatted}")
        
        metadata = "\n".join(metadata_lines)
        
        # Рассчитываем доступное место для диалога
        # Формат: header + metadata + "\n\n💬 Диалог:\n" + dialog + "\n\n🎧 Запись: url"
        record_section = ""
        if record_url:
            record_section = f"\n\n{TelegramConfig.EMOJI_RECORD} <b>Запись:</b> {record_url}"
        
        dialog_header = f"\n\n{TelegramConfig.EMOJI_DIALOG} <b>Диалог:</b>\n"
        
        fixed_parts_length = len(header) + len(metadata) + len(dialog_header) + len(record_section)
        available_for_dialog = TelegramConfig.MAX_MESSAGE_LENGTH - fixed_parts_length - 50  # 50 символов запас
        
        # Форматируем диалог с учётом лимита
        dialog_text = TelegramNotificationService.format_dialog(dialog, max_length=available_for_dialog)
        
        # Собираем сообщение
        message = header + metadata + dialog_header + dialog_text + record_section
        
        # Финальная проверка длины
        if len(message) > TelegramConfig.MAX_MESSAGE_LENGTH:
            logger.warning(f"[TELEGRAM] Message still too long ({len(message)}), hard truncating...")
            message = message[:TelegramConfig.MAX_MESSAGE_LENGTH - 20] + "\n\n[...обрезано]"
        
        return message
    
    @staticmethod
    async def send_message(
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML"
    ) -> Dict[str, Any]:
        """
        Отправляет сообщение через Telegram Bot API.
        
        Args:
            bot_token: Токен бота
            chat_id: ID чата/группы/канала
            text: Текст сообщения
            parse_mode: Режим парсинга (HTML/Markdown)
            
        Returns:
            Dict с результатом: {"success": bool, "error": str|None, "message_id": int|None}
        """
        try:
            url = TelegramConfig.API_URL.format(token=bot_token)
            
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": False  # Показываем превью ссылки на запись
            }
            
            logger.info(f"[TELEGRAM] Sending message to chat {chat_id}")
            logger.info(f"[TELEGRAM]    Message length: {len(text)} chars")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=TelegramConfig.REQUEST_TIMEOUT)
                ) as response:
                    result = await response.json()
                    
                    if response.status == 200 and result.get("ok"):
                        message_id = result.get("result", {}).get("message_id")
                        logger.info(f"[TELEGRAM] ✅ Message sent successfully, message_id: {message_id}")
                        return {
                            "success": True,
                            "error": None,
                            "message_id": message_id
                        }
                    else:
                        error_description = result.get("description", "Unknown error")
                        error_code = result.get("error_code", response.status)
                        logger.error(f"[TELEGRAM] ❌ API error: {error_code} - {error_description}")
                        return {
                            "success": False,
                            "error": f"Telegram API error: {error_description}",
                            "message_id": None
                        }
                        
        except aiohttp.ClientError as e:
            logger.error(f"[TELEGRAM] ❌ Connection error: {e}")
            return {
                "success": False,
                "error": f"Connection error: {str(e)}",
                "message_id": None
            }
        except asyncio.TimeoutError:
            logger.error(f"[TELEGRAM] ❌ Request timeout")
            return {
                "success": False,
                "error": "Request timeout",
                "message_id": None
            }
        except Exception as e:
            logger.error(f"[TELEGRAM] ❌ Unexpected error: {e}")
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "message_id": None
            }
    
    @staticmethod
    async def send_call_notification(
        bot_token: str,
        chat_id: str,
        assistant_name: str,
        caller_number: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        call_cost: Optional[float] = None,
        dialog: Optional[List[Dict[str, Any]]] = None,
        record_url: Optional[str] = None,
        call_direction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Главный метод — отправляет уведомление о звонке в Telegram.
        
        Args:
            bot_token: Токен Telegram бота
            chat_id: ID чата/группы/канала для отправки
            assistant_name: Имя ассистента
            caller_number: Номер телефона звонящего
            duration_seconds: Длительность звонка
            call_cost: Стоимость звонка
            dialog: Структурированный диалог из client_info
            record_url: Ссылка на запись звонка
            call_direction: Направление (inbound/outbound)
            
        Returns:
            Dict с результатом отправки
        """
        logger.info(f"[TELEGRAM] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"[TELEGRAM] 📤 Sending call notification")
        logger.info(f"[TELEGRAM]    Assistant: {assistant_name}")
        logger.info(f"[TELEGRAM]    Phone: {caller_number}")
        logger.info(f"[TELEGRAM]    Duration: {duration_seconds}s")
        logger.info(f"[TELEGRAM]    Cost: {call_cost}")
        logger.info(f"[TELEGRAM]    Dialog turns: {len(dialog) if dialog else 0}")
        logger.info(f"[TELEGRAM]    Record URL: {'✅' if record_url else '❌'}")
        logger.info(f"[TELEGRAM] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # Валидация
        if not bot_token:
            logger.error("[TELEGRAM] ❌ Missing bot_token")
            return {"success": False, "error": "Missing bot_token", "message_id": None}
        
        if not chat_id:
            logger.error("[TELEGRAM] ❌ Missing chat_id")
            return {"success": False, "error": "Missing chat_id", "message_id": None}
        
        # Собираем сообщение
        message = TelegramNotificationService.build_message(
            assistant_name=assistant_name,
            caller_number=caller_number,
            duration_seconds=duration_seconds,
            call_cost=call_cost,
            dialog=dialog,
            record_url=record_url,
            call_direction=call_direction
        )
        
        logger.info(f"[TELEGRAM] 📝 Built message ({len(message)} chars)")
        
        # Отправляем
        result = await TelegramNotificationService.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=message
        )
        
        if result["success"]:
            logger.info(f"[TELEGRAM] ✅ Notification sent successfully")
        else:
            logger.error(f"[TELEGRAM] ❌ Failed to send notification: {result['error']}")
        
        return result
    
    @staticmethod
    async def test_connection(bot_token: str, chat_id: str) -> Dict[str, Any]:
        """
        Тестирует подключение к Telegram боту.
        Отправляет тестовое сообщение.
        
        Args:
            bot_token: Токен бота
            chat_id: ID чата
            
        Returns:
            Dict с результатом теста
        """
        logger.info(f"[TELEGRAM] 🧪 Testing connection...")
        
        test_message = (
            "✅ <b>Тест подключения успешен!</b>\n\n"
            "Voicyfy будет отправлять уведомления о новых звонках в этот чат.\n\n"
            f"📅 Время теста: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        result = await TelegramNotificationService.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=test_message
        )
        
        if result["success"]:
            logger.info(f"[TELEGRAM] ✅ Test successful")
        else:
            logger.error(f"[TELEGRAM] ❌ Test failed: {result['error']}")
        
        return result
    
    @staticmethod
    def validate_bot_token(token: str) -> bool:
        """
        Базовая валидация формата токена бота.
        Формат: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
        
        Args:
            token: Токен для проверки
            
        Returns:
            True если формат валидный
        """
        if not token:
            return False
        
        parts = token.split(":")
        if len(parts) != 2:
            return False
        
        # Первая часть — числовой ID бота
        try:
            int(parts[0])
        except ValueError:
            return False
        
        # Вторая часть — токен (буквы, цифры, _, -)
        if len(parts[1]) < 20:
            return False
        
        return True
    
    @staticmethod
    def validate_chat_id(chat_id: str) -> bool:
        """
        Базовая валидация формата chat_id.
        Может быть положительным (личный чат) или отрицательным (группа/канал).
        
        Args:
            chat_id: ID чата для проверки
            
        Returns:
            True если формат валидный
        """
        if not chat_id:
            return False
        
        # Пробуем преобразовать в число
        try:
            int(chat_id)
            return True
        except ValueError:
            # Может быть username канала (@channel_name)
            return chat_id.startswith("@") and len(chat_id) > 1


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

async def send_call_notification_safe(
    bot_token: Optional[str],
    chat_id: Optional[str],
    assistant_name: str,
    caller_number: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    call_cost: Optional[float] = None,
    dialog: Optional[List[Dict[str, Any]]] = None,
    record_url: Optional[str] = None,
    call_direction: Optional[str] = None
) -> bool:
    """
    Безопасная обёртка для отправки уведомления.
    Не выбрасывает исключения, только логирует ошибки.
    
    Returns:
        True если отправка успешна, False иначе
    """
    try:
        # Проверяем что настройки есть
        if not bot_token or not chat_id:
            logger.debug("[TELEGRAM] Skipping notification - no Telegram settings configured")
            return False
        
        result = await TelegramNotificationService.send_call_notification(
            bot_token=bot_token,
            chat_id=chat_id,
            assistant_name=assistant_name,
            caller_number=caller_number,
            duration_seconds=duration_seconds,
            call_cost=call_cost,
            dialog=dialog,
            record_url=record_url,
            call_direction=call_direction
        )
        
        return result.get("success", False)
        
    except Exception as e:
        logger.error(f"[TELEGRAM] ❌ Safe notification failed: {e}")
        return False
