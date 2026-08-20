# backend/utils — вспомогательные утилиты: аудио, ошибки, валидация, файлы, проверка Google

## Назначение
Набор stateless-хелперов без бизнес-логики, переиспользуемых по всему backend. Конвертация и анализ аудио (`audio_utils.py`), обработка и форматирование исключений (`error_handling.py`), валидаторы входных данных (`validators.py`), работа с локальной файловой системой загрузок (`storage.py`), общие утилиты (`helpers.py`) и диагностика сервисного аккаунта Google (`google_service_checker.py`).

## Состав
- `audio_utils.py` — base64↔bytes, `create_wav_from_pcm` (ручная сборка WAV-заголовка), `float32_to_int16`/`int16_to_float32` (numpy), `resample_audio` (scipy, опционально), `detect_silence`.
- `error_handling.py` — `handle_exception` (→ `HTTPException`), `log_exception` (traceback), `format_exception_for_client`, `get_exception_details`, `get_error_code` (маппинг типов исключений в коды).
- `validators.py` — `validate_email`, `validate_password`, `validate_api_key` (формат OpenAI `sk-`), `validate_uuid`, `validate_voice`, `validate_url`, `validate_file_type`, `validate_domain`. Все возвращают `(bool, Optional[str])`.
- `storage.py` — локальное файловое хранилище: каталоги загрузок, `get_file_path`, копирование/перемещение/удаление, `get_mime_type`, `is_allowed_file`, `list_files`. Список `ALLOWED_EXTENSIONS`.
- `helpers.py` — `generate_unique_id`, формат/парсинг datetime, `truncate_string`, `parse_client_info` (разбор User-Agent/UTM), `safe_json_loads`, `chunks`, `retry`.
- `google_service_checker.py` — `check_google_service_account`: валидирует env `GOOGLE_SERVICE_ACCOUNT_JSON` (JSON, обязательные поля, формат private_key) и возвращает диагностику с рекомендациями.

## Ключевые сущности / точки входа
- Аудио: `audio_buffer_to_base64` / `base64_to_audio_buffer` / `create_wav_from_pcm` (sample_rate по умолчанию 24000 Гц, 16-bit mono) — используются голосовым WS-слоем.
- Ошибки: `handle_exception(exc, log_message, status_code, detail)` — стандартный способ превратить исключение в HTTP-ответ; `get_error_code` для машинно-читаемых кодов.
- Валидаторы: все функции `validate_*` — единый контракт `(is_valid, error_message)`; `validate_api_key` допускает пустой ключ.
- Файлы: `get_user_upload_dir`, `get_file_path`, `is_allowed_file`, `ALLOWED_EXTENSIONS`.
- `check_google_service_account()` — единая проверка готовности Google-интеграции.

## Связи с другими частями проекта
- Используется: `backend/websockets/*` (аудио-конвертация), `backend/api/*` и `backend/services/*` (валидаторы, обработка ошибок, файловое хранилище), интеграции Google (`google_service_checker`).
- Использует: `backend/core/logging.py` (`get_logger`), `backend/core/config.py` (`settings.STATIC_DIR` в `storage.py`); внешние `numpy`, опционально `scipy` (`resample_audio`), стандартные `base64/struct/re/mimetypes/shutil`.

## На что обратить внимание
- `storage.py` работает с локальным диском (`settings.STATIC_DIR/uploads`) — это эфемерное хранилище на Render; постоянные артефакты (записи звонков) идут в Cloudflare R2 (см. `services/r2_storage.py`), а не сюда.
- `resample_audio` требует `scipy`; при отсутствии — `ImportError` (явно логируется warning).
- `create_wav_from_pcm` собирает заголовок вручную через `struct` — при смене формата (каналы/битность) проверяй расчёт `ByteRate`/`BlockAlign`.
- `validators.validate_api_key` проверяет только формат OpenAI-ключа (`sk-`, длина ≥30), не валидность ключа на сервере.
- `helpers.retry` — это функция-обёртка (вызывается как `retry(func)(...)`), синхронная, использует `time.sleep`; для async-кода не подходит.
- `google_service_checker` ожидает реальные переносы строк в `private_key`; экранированные `\n` отмечаются как проблема.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../websockets/claude-websockets.md` — основной потребитель аудио-утилит
- `../services/claude-services.md` — R2-хранилище, Google-сервисы, потребители валидаторов
- `../core/claude-core.md` — `settings`, `get_logger`
- `../api/claude-api.md` — обработка ошибок и валидация в роутерах
