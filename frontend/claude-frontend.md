# frontend — React + Vite лендинг VoksiAI (исходники)

## Назначение
Публичный лендинг voksyai.online на React 18 + Vite по дизайн-системе VoksiAI
(`design-system/05-landing.md`). Единственная часть фронтенда на React — страницы кабинета
сделаны на vanilla HTML/JS в `backend/static/`.

## Состав
- `index.html` — шаблон: шрифты Inter + Unbounded, `<link href="/static/css/voksiai.css">`
  (токены и компоненты ДС, отдаются бэкендом, в бандл не входят), плейсхолдер `<!--app-head-->`
  для SEO, `<body class="vf">`, `#root`.
- `package.json` — react, react-dom, `motion` (v13, импорт из `motion/react`), `lenis`.
  `npm run build` = `vite build && node scripts/prerender.mjs`.
- `vite.config.js` — `outDir = ../backend/static/landing`, `base = /static/landing/`, dev-прокси.
- `scripts/prerender.mjs` — после сборки рендерит `src/entry-server.jsx` для двух языков:
  `backend/static/landing/index.html` (кыргызский, отдаётся на `/`) и
  `backend/static/landing/ru/index.html` (русский, `/ru/`, маршрут в `app.py`). В `<head>`:
  title, description, canonical, hreflang (ky/ru/x-default), OG, JSON-LD (Organization, WebSite,
  SoftwareApplication, FAQPage).
- `src/` — см. `src/claude-frontend-src.md`.

## На что обратить внимание
- Render не собирает фронтенд: после правок в `frontend/` обязательно `npm ci && npm run build`
  и коммит `backend/static/landing/` (обе страницы + хешированные ассеты).
- Иконки — спрайт `/static/icons/ui.svg`, логотипы моделей — `/static/icons/models/*.svg`.
- Dev-прокси указывает на порт 8000 (backend по умолчанию на 5050).
