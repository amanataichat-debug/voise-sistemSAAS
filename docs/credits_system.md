# Система кредитов оркестратора Voksy AI Agent

Реализация ТЗ v1.1. Тариф `agent` живёт параллельно с существующими тарифами
(`ai_voice`, `start`, `profi`) и не затрагивает их.

> **В платформе два независимых кошелька кредитов.** Этот документ — про
> кредиты **оркестратора** (`users.credits_balance`, транзакции с
> `product='orchestrator'`). Второй кошелёк — кредиты **каскад-ассистентов**
> (`users.cascade_credits_balance`, `product='cascade'`,
> `services/cascade_credit_service.py`): свой стартовый грант, свои пакеты
> докупки, доступен на всех тарифах, включая free, и не блокируется
> истечением подписки `agent`. Гейт по балансу каскада стоит на старте
> звонка (`api/telephony.py`), а не при создании ассистента. Общая у них
> только таблица `credit_transactions`, разделённая колонкой `product`.

## Обзор

- **Кредиты привязаны к юзеру** (`users.credits_balance`), а не к агенту.
  При удалении агента баланс, история транзакций, `agent_trial_used` и
  `subscription_end_date` сохраняются.
- **Trial** выдаётся один раз за всю жизнь юзера: 3 дня + 1 500 кредитов при
  первом создании агента.
- **Тариф agent**: 4 990 ₽/мес, +20 000 кредитов разово при покупке/продлении.
- **Пакеты докупки** (`credit_packages`) никогда не сгорают, доступны только при
  активной подписке agent (включая trial).
- При истечении подписки — жёсткая блокировка: оркестратор, звонки, Telegram-бот
  и веб-чат не работают. Кредиты не сгорают, но потратить их нельзя до оплаты.

## Тарификация моделей

Ставки **не задаются вручную** — они выводятся из реальной цены модели на
OpenRouter в `backend/services/agent_models.py`. Единственный источник правды
по каждой модели — её себестоимость в долларах за 1000 токенов, переданная в
`_rates(...)`; ставки считает формула:

```
кредитов за 1k токенов = ($ за 1k токенов) × ORCHESTRATOR_MARGIN × USD_RUB / CREDIT_PRICE_RUB
                       = ($ за 1k токенов) × 1.2 × 95 / 0.05998
                       = ($ за 1k токенов) × 1900.63
```

Три константы, задающие экономику:

| Константа | Значение | Смысл |
|-----------|----------|-------|
| `CREDIT_PRICE_RUB` | 0.05998 | Цена кредита по **самому выгодному** пакету (Enterprise: 500 000 кр. за 29 990 ₽). Считая маржу по нему, гарантируем +20% на всех пакетах — на мелких кредит дороже, значит маржа выше (на Mini ≈ +96%). |
| `USD_RUB` | 95.0 | Курс **с запасом** к споту (ЦБ 01.08.2026 — 79.46). Себестоимость в долларах, кредит продаётся за рубли: при марже 1.2 движение курса на 20% съело бы её целиком. |
| `ORCHESTRATOR_MARGIN` | 1.2 | Наценка к себестоимости OpenRouter. |

При изменении прайса OpenRouter правится только цена конкретной модели;
при изменении курса или маржи — одна константа, и пересчитываются все модели.

Формула списания (`CreditService.calculate_cost`) не изменилась:

```
credits_spent = ceil(
    (prompt_tokens / 1000) * input_credits_per_1k +
    (completion_tokens / 1000) * output_credits_per_1k
)
```

Минимум — 1 кредит за любой вызов. Ставки дробные (напр. 0.2661), арифметика
идёт во float, `ceil` применяется только к итогу вызова.

### Цена модели в интерфейсе

`_rates` дополнительно отдаёт `credits_per_call` — оценку стоимости одного
вызова по эталону `REFERENCE_CALL_TOKENS = (4000, 500)`. Фронт
(`agent/core.js` → `modelOptionsHtml`, `modelHintHtml`) показывает её в
селекте модели: `GPT-5.6 Luna (рекомендуем) · ~1.3 кр/звонок`, а под селектом —
точные ставки за 1k токенов.

Цветовой градации моделей нет намеренно: красные маркеры у дорогих моделей
отталкивали от вполне рабочих вариантов. Цену называет само число. Подсказку
«что выбрать» даёт флаг `is_recommended` у модели (сейчас — GPT-5.6 Luna);
на модель по умолчанию он не влияет, за неё отвечает отдельный `is_default`.

⚠️ `REFERENCE_CALL_TOKENS` — **оценка, а не замер**. Реальный расход зависит от
длины инструкций и истории диалога. Уточнять по факту:

```sql
SELECT ROUND(AVG(prompt_tokens)), ROUND(AVG(completion_tokens))
FROM credit_transactions
WHERE product = 'orchestrator' AND type = 'spend'
  AND prompt_tokens IS NOT NULL AND created_at > NOW() - INTERVAL '30 days';
```

### Устаревшие слаги моделей

`LEGACY_MODEL_ALIASES` — карта «снятый с OpenRouter слаг → актуальный».
Применяется в `is_valid_model` / `get_model_rates` / `resolve_slug`, чтобы у
существующих агентов не ломалось сохранение настроек, и разово мигрирует
`agent_configs.orchestrator_model` при старте
(`ensure_agent_orchestrator_model_migration` в `app.py`).

## Архитектура

| Слой | Файл |
|------|------|
| Модели | `backend/models/credit_transaction.py`, `backend/models/credit_package.py`, поля в `backend/models/user.py` |
| Сервис | `backend/services/credit_service.py` (`CreditService`, `activate_agent_trial`) |
| API | `backend/api/credits.py` (`/api/credits/*`) |
| Платежи | `backend/services/payment_service.py` (ветви пакетов и тарифа agent в `process_payment_result`) |
| Оркестратор | `backend/services/agent_orchestrator.py` (precheck + charge в 4 v3-методах) |
| Блокировка | `backend/services/subscription_blocker.py` (каждые 5 мин) + guard в `backend/core/task_scheduler.py` |
| Frontend | `backend/static/agent.html` (бейдж, баннер, модалки, обработка 402) |

### Списание кредитов

Списывается только v3-флоу на ключах Voksy AI (OpenRouter): precall, postcall,
веб-чат, Telegram-чат. НЕ списываются: v2 legacy-флоу (ключи юзера) и сами
голосовые звонки (Realtime/Live — ключи юзера). Токены накапливаются по всем
итерациям tool calls и списываются одним `charge` в конце.

## API

| Метод | Назначение |
|-------|------------|
| `GET /api/credits/balance` | Баланс + статус подписки |
| `GET /api/credits/packages` | Активные пакеты докупки |
| `GET /api/credits/transactions` | История транзакций (limit/offset/type_filter) |
| `POST /api/credits/purchase` | Платёж за пакет (тело `{package_code}`) |
| `POST /api/credits/subscribe` | Trial без оплаты ИЛИ платёж за тариф agent |

Коды ошибки 402 (`detail`): `subscription_expired`, `subscription_required`,
`{error: insufficient_credits, required, available}`.

## Платёжный коллбэк (Finik)

`process_payment_result` различает три ветви по Shp-параметрам:

- `Shp_credits_package=<code>` → начисление пакета (`grant_purchase`).
- `Shp_plan_code=agent` → продление 30 дней + 20 000 кредитов (`grant_subscription`),
  снятие `agent_subscription_blocked`.
- иначе → существующая логика других тарифов.

Идемпотентность через `transaction.is_processed`. Сумма сверяется с
`credit_packages.price_rub` / `subscription_plans.price` (защита от подмены Shp).

---

## Admin Runbook — ручная корректировка баланса

> На случай спора/компенсации. Все ручные операции пишутся в `credit_transactions`
> с `type=manual_adjust` и обязательной заметкой.

### Начислить/списать кредиты вручную (через Python shell на сервере)

```python
from backend.db.session import SessionLocal
from backend.services.credit_service import CreditService

db = SessionLocal()
# Начислить 5000 кредитов (компенсация по тикету #123)
CreditService.manual_adjust(db, user_id="<UUID>", amount=5000,
                            notes="support: ticket #123, goodwill credit by admin@voicyfy")
# Списать 2000 кредитов
CreditService.manual_adjust(db, user_id="<UUID>", amount=-2000,
                            notes="support: refund clawback, ticket #124")
db.close()
```

`manual_adjust` использует `SELECT FOR UPDATE`, не уходит в минус (обрезает до 0),
требует непустую заметку.

### Проверить баланс и историю

```python
from backend.db.session import SessionLocal
from backend.services.credit_service import CreditService
db = SessionLocal()
print(CreditService.get_balance(db, "<UUID>"))
rows, total = CreditService.get_transactions(db, "<UUID>", limit=20)
for t in rows: print(t.created_at, t.type, t.amount, t.balance_after, t.notes)
db.close()
```

### Разблокировать подписку вручную

Блокировка снимается автоматически при успешной оплате. Для ручного снятия:

```sql
UPDATE users SET agent_subscription_blocked = FALSE WHERE id = '<UUID>';
```

### Где смотреть при споре «куда делись кредиты после удаления агента»

Удаление агента создаёт системную запись `type=manual_adjust, amount=0,
ref_type=agent_deleted` с текущим балансом и числом отменённых задач — она
остаётся в `credit_transactions` даже после удаления агента.
