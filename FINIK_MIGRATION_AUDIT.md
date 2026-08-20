# Аудит интеграции Robokassa и миграция на Finik

Дата: 2026-08-20. Статус: **миграция выполнена в этом же изменении**.
По решению владельца проекта код Robokassa **удалён полностью** (отступление от
этапа 4 ТЗ — переключатель `PAYMENT_PROVIDER` не нужен, откатываться некуда).
Исторические записи платежей Robokassa в БД сохранены (`payment_system='robokassa'`).

---

## Этап 1. Что было (аудит Robokassa до миграции)

### 1.1. Где создавался платёж

| Точка | Файл | Что делала |
|---|---|---|
| `POST /api/payments/create-payment` | `backend/api/payments.py` | Подписки ai_voice/start/profi. Формировала `form_params` (MerchantLogin, OutSum, InvId=unix-timestamp, MD5-подпись PASSWORD_1, `Shp_user_id/Shp_plan_code/Shp_duration`) для POST-формы на `auth.robokassa.ru/Merchant/Index.aspx` |
| `POST /api/credits/purchase` | `backend/api/credits.py` | Пакеты кредитов оркестратора (`Shp_credits_package`) через хелпер `_build_robokassa_payment` |
| `POST /api/credits/subscribe` | `backend/api/credits.py` | Тариф agent 5490 ₽/30 дней (`Shp_plan_code=agent`) либо бесплатный trial |
| `POST /api/grok-assistants/cascade/credits/purchase` | `backend/api/grok_assistants.py` | Пакеты кредитов каскада (`Shp_cascade_package`) |

### 1.2. Где принимался результат оплаты

- **ResultURL** `POST /api/payments/robokassa-result` — MD5-подпись по PASSWORD_2,
  ⚠️ **проверка подписи была ОТКЛЮЧЕНА** флагом `DISABLE_SIGNATURE_VERIFICATION=True`
  («временная мера»). Диспетчеризация по `Shp_*`-параметрам → продление подписки /
  зачисление кредитов. Ответ `OK{InvId}`.
- **SuccessURL** `/api/payments/success`, **FailURL** `/api/payments/cancel` — HTML-страницы.
- Идемпотентность — по `transaction.is_processed` (у пакетов/agent), у обычных
  подписок повторный callback продлевал подписку ещё раз (уязвимость).

### 1.3. Модели БД

- `payment_transactions` — `external_payment_id` (InvId), `payment_system`
  (default 'robokassa'), `amount NUMERIC(10,2)`, `currency` (default 'RUB'),
  `status`, `is_processed`, `payment_details` (free-text), `error_message`.
  Поле провайдера **уже было** (`payment_system`).
- `subscription_plans` — `code`, `price` (месячная цена), `max_assistants`.
- `credit_packages` — `code`, `product` (orchestrator/cascade), `credits`, `price_rub`.
- `subscription_logs` — журнал событий подписки.

### 1.4. Фронтенд

Все три точки строили **скрытую POST-форму** из `form_params` и сабмитили на URL Robokassa:
- `backend/static/dashboard.html` — модалка тарифов (`handlePayment`), цены захардкожены в ₽;
- `backend/static/agent/credits.js` — `submitRobokassaForm` (пакеты + подписка agent);
- `backend/static/cascade.html` — `submitRobokassaForm` (пакеты каскада);
- лендинг `frontend/src/components/PricingSection.jsx` — маркетинговые цены в ₽.

### 1.5. Бизнес-логика после оплаты

- Подписка: продление `users.subscription_end_date` (от конца текущей, если активна),
  `subscription_plan_id`, `is_trial=False`; для profi — ежемесячный грант кредитов.
- Agent: +30 дней, +20 000 кредитов, снятие `agent_subscription_blocked`.
- Пакеты: `CreditService.grant_purchase` / `CascadeCreditService.grant_purchase` (сверка суммы с ценой пакета).
- Партнёрская комиссия: `PartnerService.process_referral_payment` (подписки и agent).
- Журнал: `SubscriptionService.log_subscription_event`.

### 1.6. Валюта и суммы

Всё в RUB, захардкожено во множестве мест: `SUBSCRIPTION_PLANS_CONFIG` (payments.py),
`SUBSCRIPTION_PERIODS` (payment_service.py), `AGENT_PLAN_PRICE` (credits.py), сиды
в `app.py`, ценники в dashboard.html / agent/credits.js / лендинге. Конвертации не было.

---

## Этап 2+. Что стало (Finik)

### Архитектура

- **`backend/services/finik_service.py`** — клиент API Finik:
  - подпись запросов RSA-SHA256 (PKCS#1 v1.5, Base64) — формат канонической строки
    воспроизведён 1-в-1 из официального Node-пакета `@mancho.devs/authorizer`
    (официального Python-пакета в PyPI нет); корректность закреплена юнит-тестом
    `test_finik_signature.py` на фикстурах из исходников пакета (совпадает и
    каноническая строка, и итоговая RSA-подпись);
  - `create_payment()` — `POST {FINIK_API_URL}/v1/payment`, `follow_redirects=False`,
    успех = `302` → возвращается `Location`; иначе парсится `{"StatusCode","ErrorMessage"}`;
  - `verify_webhook_signature()` — проверка подписи входящего webhook по
    `FINIK_PUBLIC_KEY` (за флагом `FINIK_VERIFY_WEBHOOK_SIGNATURE`, default true;
    если ключ ещё не выдан — пропуск с предупреждением в логах).
- **`POST /api/payments/finik-webhook`** — единственный источник истины об оплате:
  подпись → 401 при невалидной; статус success/succeeded регистронезависимо;
  идемпотентность по `transactionId` (уникальный индекс `finik_transaction_id`);
  сопоставление заказа по `fields.paymentId` = `payment_transactions.external_payment_id`
  (UUID, генерируется при создании платежа); сверка суммы (расхождение → лог,
  без автозачисления); быстрый 200.
- **`backend/services/payment_service.py`** — `FinikPaymentService`: зачисление по
  типу платежа из `payment_details` (JSON: `subscription` / `agent_subscription` /
  `credits_package` / `cascade_package`), партнёрские комиссии сохранены.
- **Страница `/api/payments/success`** (RedirectUrl) — только UX, ничего не помечает
  оплаченным.
- **Фронтенд** — все три точки теперь делают `window.location = payment_url`.
- **БД (аддитивно, startup-миграция `ensure_payment_finik_columns` в app.py):**
  `payment_transactions.finik_transaction_id VARCHAR(100)` + уникальный частичный
  индекс, `payment_url VARCHAR(1000)`; default `payment_system='finik'`, `currency='KGS'`.
  Существующие записи не изменялись.

### Удалено

`RobokassaService`, эндпоинты `/robokassa-result`, `/cancel`, диагностика подписи
Robokassa, env-валидаторы `ROBOKASSA_*` (приложение больше не требует этих переменных),
формы Robokassa на фронте, упоминания в документации.

### Env-переменные (Render)

- `FINIK_API_KEY`, `FINIK_API_URL`, `FINIK_PRIVATE_PEM`, `FINIK_ACCOUNT_ID` — уже добавлены;
- `FINIK_PUBLIC_KEY` — добавить, когда Finik выдаст публичный ключ для webhook'ов;
- `FINIK_VERIFY_WEBHOOK_SIGNATURE` — default `True`; можно временно `False` на отладке.

Webhook URL для панели Finik: **`https://voicyfy.ru/api/payments/finik-webhook`**
(передаётся и в каждом платеже через `Data.webhookUrl`).

---

## 💰 ЦЕНЫ: где и как менять (сейчас стоят минимальные ТЕСТОВЫЕ)

Валюта всюду — **сомы (KGS)**. Для теста эквайринга выставлены минимальные цены:
ai_voice **10**, start **20**, profi **30**, agent **50** сом/мес.

### Тарифы подписок — источник истины: БД

Бэкенд берёт цену из `subscription_plans.price`; значения в коде — только fallback,
пока строки тарифа нет в БД (строка создаётся при первом платеже). Дашборд
подтягивает цены через `GET /api/payments/plans` автоматически.

```sql
-- Посмотреть текущие цены
SELECT code, name, price FROM subscription_plans ORDER BY code;

-- Установить боевые цены (пример)
UPDATE subscription_plans SET price = 1490 WHERE code = 'ai_voice';
UPDATE subscription_plans SET price = 2990 WHERE code = 'start';
UPDATE subscription_plans SET price = 5990 WHERE code = 'profi';
UPDATE subscription_plans SET price = 5490 WHERE code = 'agent';
```

⚠️ В существующей боевой БД строки тарифов могли остаться со СТАРЫМИ рублёвыми
значениями (например, start = 1490 из старых сидов) — они теперь трактуются как
сомы. **Перед тестом выполните:**

```sql
UPDATE subscription_plans SET price = 10 WHERE code = 'ai_voice';
UPDATE subscription_plans SET price = 20 WHERE code = 'start';
UPDATE subscription_plans SET price = 30 WHERE code = 'profi';
UPDATE subscription_plans SET price = 50 WHERE code = 'agent';
```

(Сид `app.py` больше НЕ перезатирает цену agent при рестарте — исправлено.)

### Пакеты кредитов — источник истины: БД

Колонка называется `price_rub` (историческое имя), значение — **в сомах**.

```sql
SELECT code, product, name, credits, price_rub FROM credit_packages ORDER BY product, sort_order;

-- Минимальные тестовые цены на все пакеты (пример):
UPDATE credit_packages SET price_rub = 5 WHERE product IN ('orchestrator', 'cascade');
```

### Скидки за период (6 мес −20%, 12 мес −30%)

Код: `PERIOD_DISCOUNTS` в `backend/api/payments.py` (и зеркало в `dashboard.html`).

### Маркетинговые цены на лендинге

Захардкожены: `frontend/src/components/PricingSection.jsx` (+ `src/i18n/ru.js`, `ky.js`).
После правки обязательно `cd frontend && npm ci && npm run build` и закоммитить
`backend/static/landing/` (см. CLAUDE.md).

---

## Этап 5. Тестирование

- [x] Юнит-тест канонизации/подписи — `python test_finik_signature.py`
      (фикстуры и эталонная подпись из `@mancho.devs/authorizer` 2.12.8 — совпадение побайтовое).
- [ ] Ручной тест минимального платежа на проде (цены уже минимальные) — после деплоя.
- [x] Повторная доставка webhook с тем же `transactionId` → уникальный индекс
      `finik_transaction_id` + `is_processed` → 200 без двойного зачисления
      (включая гонку параллельных доставок — ловится `IntegrityError`).
- [x] Закрытие платёжной страницы до редиректа: зачисление делает только webhook,
      страница `/success` ничего не помечает.
- [x] Ошибка создания платежа (не-302 / сетевые) → транзакция помечается `failed`
      с `error_message`, пользователь получает 502 с понятным сообщением, всё в логах.

## Чек-лист после деплоя

1. Прогнать SQL с тестовыми ценами (выше).
2. `GET https://voicyfy.ru/api/payments/config-check` → `configured: true`.
3. Тестовый платёж 10–50 сом по каждому типу (подписка / agent / пакет / каскад).
4. Запросить у Finik публичный ключ → добавить `FINIK_PUBLIC_KEY` в Render.
5. Выставить боевые цены в БД и синхронизировать лендинг.
