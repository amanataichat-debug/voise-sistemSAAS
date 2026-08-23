# frontend — React + Vite лендинг (исходники)

## Назначение
Исходный код публичного лендинга voksyai.online на React 18 + Vite. Это **единственная** часть фронтенда на React — все внутренние страницы приложения (дашборд, агенты, CRM и т.д.) сделаны на vanilla HTML/JS в `backend/static/`. При сборке Vite кладёт результат в `backend/static/landing/`, откуда FastAPI отдаёт его на корневом маршруте `/`.

## Состав
- `index.html` — HTML-шаблон Vite (точка монтирования `#root`).
- `package.json` — зависимости (react, react-dom) и скрипты `dev`/`build`/`preview`. Имя пакета `voicyfy-landing`.
- `vite.config.js` — конфиг сборки: `outDir = ../backend/static/landing`, `emptyOutDir`, `base = /static/landing/`, dev-прокси `/api` и `/static` на `localhost:8000`.
- `package-lock.json` — лок зависимостей.
- `src/` — исходники приложения (компоненты, хуки, утилиты, стили) — см. дочернюю доку.

## Ключевые сущности / точки входа
- **Сборка:** `npm run build` → `backend/static/landing/`. **Не редактируйте** `backend/static/landing/` руками — это артефакт сборки, перезатрётся.
- **Dev:** `npm run dev` поднимает Vite с прокси на backend (порт 8000); API-запросы идут на реальный backend.
- **`base: '/static/landing/'`** — все ассеты в проде грузятся из-под этого пути; на `/` FastAPI отдаёт `backend/static/landing/index.html` (см. `app.py`).

## Связи с другими частями проекта
- Используется: `app.py` — маршрут `/` отдаёт собранный `index.html`; статика монтируется через `StaticFiles`.
- Использует: backend API (`/api/...`) — через `src/utils/api.js` и хуки (`src/hooks/`). UTM/рефералы обрабатываются клиентски (`useReferralTracker`).

## На что обратить внимание
- **Vite dev-прокси указывает на порт 8000**, тогда как локальный backend по умолчанию стартует на 5050 (`main.py`). При локальной разработке лендинга либо запускайте backend на 8000, либо поправьте прокси.
- Зависимости минимальны (только React) — никакого роутера/стейт-менеджера; всё в одном `App.jsx` со скроллом по секциям.
- Граница «React-лендинг vs vanilla-страницы» — частый источник путаницы: правки внутренних страниц приложения делаются в `backend/static/`, а не здесь.

## Связанные файлы документации
- `../claude-index.md` — корневой индекс
- `./src/claude-frontend-src.md` — структура исходников (компоненты/хуки/утилиты)
- `../backend/static/claude-static.md` — собранный лендинг и vanilla-страницы приложения
- `../backend/api/claude-api.md` — API, который дёргает лендинг
