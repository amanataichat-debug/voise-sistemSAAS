import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Твои данные
EMAIL_USERNAME = "voicyfy@mail.ru"  # 👈 Твой Mail.ru
EMAIL_PASSWORD = "n7zy3sgtN4i1xfWkERPD"  # 👈 Пароль для внешнего приложения
TO_EMAIL = "well96well@gmail.com"  # 👈 Твоя личная почта для теста

def test_mailru_smtp():
    try:
        # Создаем сообщение
        msg = MIMEMultipart()
        msg['From'] = f"Voicyfy <{EMAIL_USERNAME}>"
        msg['To'] = TO_EMAIL
        msg['Subject'] = "🎉 Тест SMTP Mail.ru - Voicyfy"
        
        body = """
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto;">
                <h2 style="color: #2563eb;">Привет! 👋</h2>
                <p style="font-size: 16px;">Если ты видишь это письмо - <strong>Mail.ru SMTP работает отлично!</strong> ✅</p>
                <div style="background: #f0f4ff; padding: 15px; border-radius: 8px; margin: 20px 0;">
                    <p style="color: #64748b; margin: 0;">Это тестовое письмо от системы Voicyfy</p>
                </div>
                <p style="font-size: 14px; color: #94a3b8;">
                    Можно начинать разработку верификации email! 🚀
                </p>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'html', 'utf-8'))
        
        # Подключаемся к Mail.ru SMTP
        print("📡 Подключаемся к Mail.ru SMTP...")
        print(f"   Хост: smtp.mail.ru")
        print(f"   Порт: 465 (SSL)")
        
        # Используем SMTP_SSL для порта 465
        server = smtplib.SMTP_SSL('smtp.mail.ru', 465)
        
        print("🔐 Авторизуемся...")
        server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        
        print("📧 Отправляем письмо...")
        server.send_message(msg)
        server.quit()
        
        print("\n" + "="*50)
        print("✅ УСПЕХ! Письмо отправлено!")
        print("="*50)
        print(f"📬 Проверь входящие на: {TO_EMAIL}")
        print("💡 Также проверь папку 'Спам' на всякий случай")
        
    except smtplib.SMTPAuthenticationError as e:
        print(f"\n❌ ОШИБКА АВТОРИЗАЦИИ: {e}")
        print("\n🔍 Проверь:")
        print("1. ✓ Используешь 'Пароль для внешнего приложения', а НЕ обычный пароль")
        print("2. ✓ Пароль скопирован правильно (без пробелов)")
        print("3. ✓ Логин правильный: твой-email@mail.ru")
        
    except smtplib.SMTPException as e:
        print(f"\n❌ ОШИБКА SMTP: {e}")
        print("\n🔍 Проверь:")
        print("1. ✓ Интернет соединение")
        print("2. ✓ Порт 465 не заблокирован файрволом")
        
    except Exception as e:
        print(f"\n❌ НЕОЖИДАННАЯ ОШИБКА: {e}")
        print(f"   Тип ошибки: {type(e).__name__}")

if __name__ == "__main__":
    print("🧪 Тестируем отправку через Mail.ru SMTP\n")
    test_mailru_smtp()
