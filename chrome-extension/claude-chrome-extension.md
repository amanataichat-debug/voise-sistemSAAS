# chrome-extension — браузерное расширение (голосовой ассистент в side panel)

## Назначение
Chrome-расширение (Manifest V3), дающее доступ к голосовому AI-ассистенту Voksy AI прямо из браузера. Popup служит для логина и выбора ассистента, side panel — рабочая панель с WebSocket-подключением к голосовому движку (аудио + захват экрана). Расширение — самостоятельный клиент backend'а, общается с ним по HTTPS/WSS; собственной серверной логики не содержит.

## Состав
- `manifest.json` — манифест MV3: `name` "WellcomeAI Assistant", permissions (`storage`, `activeTab`, `sidePanel`), `host_permissions` на backend, регистрация `background.js`, popup и side_panel.
- `background.js` — service worker (фоновые задачи; сейчас минимальный).
- `popup/` — `popup.html` / `popup.css` / `popup.js`: логин по email/паролю, получение токена, загрузка списка ассистентов пользователя, сохранение выбранного ассистента в `chrome.storage.local`.
- `sidepanel/` — `sidepanel.html` / `sidepanel.js`: основная панель — поднимает WebSocket к голосовому движку по выбранному `assistantId`, обрабатывает аудио и (по комментариям) захват экрана.

## Ключевые сущности / точки входа
- **`manifest.json`** — корень расширения; меняет права и точки входа (popup/sidepanel/worker).
- **`popup.js`** — `API_URL` указывает на backend (`/api`); поток: проверка токена → логин → `loadAssistants` → выбор → запись `selectedAssistant` в storage.
- **`sidepanel.js`** — `WS_URL` (`wss://.../ws`); читает `selectedAssistant` из `chrome.storage.local`, `connectWebSocket()` подключается к `${WS_URL}/${assistantId}` (тот же протокол, что web-виджет/`backend/websockets/handler_realtime_new`).

## Связи с другими частями проекта
- Используется: конечными пользователями как отдельный клиент; в репозитории ни на что не ссылается.
- Использует: backend API (`/api/auth`, `/api/assistants`) и голосовой WebSocket (`/ws/{assistant_id}`) — фактически тот же контракт, что у web-виджета (`backend/static/widget.js`).

## На что обратить внимание
- **Хардкод хоста.** `popup.js` и `sidepanel.js` содержат прод-URL `https://realtime-saas.onrender.com` (старый домен Render), а `manifest.json` `host_permissions` — тот же. Это НЕ `voksyai.online`; при смене бэкенда нужно править во всех трёх местах. Возможно легаси-домен.
- **Протокол WS** должен совпадать с серверным голосовым хендлером (`/ws/{assistant_id}`) — при изменениях в `backend/websockets/` проверяйте совместимость расширения.
- Логика side panel помечена комментарием «копируем из widget.js» — источник правды по аудио/WS — web-виджет `backend/static/widget.js`.
- Токен хранится в `chrome.storage.local` — отдельно от localStorage веб-приложения.

## Связанные файлы документации
- `../claude-index.md` — корневой индекс
- `../backend/api/claude-api.md` — API логина и ассистентов
- `../backend/websockets/claude-websockets.md` — голосовой WS-протокол
- `../backend/static/claude-static.md` — web-виджет (`widget.js`), на котором основана панель
