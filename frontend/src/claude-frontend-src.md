# frontend/src — исходники React-лендинга (компоненты, хуки, утилиты, стили)

## Назначение
Весь код React-лендинга: точка входа, корневой компонент-страница, секционные компоненты, хуки работы с авторизацией/верификацией/рефералами, тонкий API-клиент и CSS. Приложение одностраничное — `App.jsx` собирает лендинг из секций и управляет модалкой входа/регистрации; роутера нет, навигация — якорный скролл.

## Состав
- `main.jsx` — точка входа: монтирует `<App/>` в `#root` (React 18 `createRoot`, StrictMode).
- `App.jsx` — корневой компонент: собирает секции (Navbar, Hero, Code, Showcase, PhoneCTA, Providers, Pricing, Footer), управляет `AuthModal`, IntersectionObserver для анимации появления (`.rev` → `.on`), плавный скролл по якорям. Вызывает `useAuth()`.
- `index.css` — корневые стили (импортирует/дополняет `styles/`).
- `components/` — секционные и UI-компоненты лендинга.
- `hooks/` — React-хуки бизнес-логики (auth, email-верификация, реферал-трекинг).
- `utils/` — `api.js` (клиент backend) и `notifications.js` (тосты/уведомления).
- `styles/` — CSS по областям (см. ниже).

## Состав подпапок (одной строкой)
**components/:** `Navbar.jsx`, `HeroSection.jsx`, `CodeSection.jsx`, `ShowcaseSection.jsx`, `PhoneCTASection.jsx`, `ProvidersSection.jsx`, `PricingSection.jsx`, `UseCasesSection.jsx`, `Footer.jsx` — секции лендинга; `AuthModal.jsx` + `AuthSection/` — модалка входа/регистрации; `InlineNotification.jsx` — инлайн-уведомление; `ScrollProgress.jsx`, `MeshBackground.jsx`, `SphereAnimation.jsx` — визуальные эффекты.

**hooks/:**
- `useAuth.js` — читает `auth_token` из localStorage; логика состояния авторизации (редирект/доступ к кабинету).
- `useEmailVerification.js` — таймер и счётчик попыток для подтверждения email, отправка/проверка кода, уведомления.
- `useReferralTracker.js` — парсит UTM/`ref` из URL и сохраняет реферальные данные для передачи при регистрации.

**utils/:**
- `api.js` — объект `api` с `baseUrl='/api'`: обёртка `fetch` (подставляет `Bearer`-токен из localStorage, JSON-сериализация, разбор ошибок) и методы (`register`, `login` и др.).
- `notifications.js` — показ уведомлений/тостов.

**styles/:** `globals.css`, `variables.css`, `layout.css`, `responsive.css`, `navbar.css`, `buttons.css`, `forms.css`, `auth.css`, `verification.css`, `notifications.css`, `pricing.css`, `use-cases.css`, `presentation.css`, `sphere.css`, `footer.css` — CSS по областям UI.

## Ключевые сущности / точки входа
- **`App.jsx`** — единственная «страница»; чтобы изменить структуру лендинга, правьте порядок/набор секций здесь.
- **`utils/api.js` (`api`)** — единственная точка общения с backend; токен берётся из `localStorage['auth_token']`. Все хуки/компоненты ходят в API через него.
- **`useAuth` / `useEmailVerification` / `useReferralTracker`** — вся клиентская бизнес-логика регистрации и входа.

## Связи с другими частями проекта
- Используется: собирается Vite в `backend/static/landing/` (см. `../claude-frontend.md`), отдаётся FastAPI на `/`.
- Использует: backend API `/api/auth/*`, `/api/email-verification/*`, `/api/partners/*` (через `utils/api.js` и хуки). Токен и реферальные данные — в `localStorage`.

## На что обратить внимание
- **Состояние авторизации — только localStorage** (`auth_token`), без httpOnly-cookie; XSS-чувствительно.
- **Нет роутера/стейт-менеджера** — намеренно простая SPA; не усложняйте без необходимости.
- **CSS-файлы по областям** — при правке стиля ищите соответствующий файл в `styles/`, не складывайте всё в `globals.css`.
- `AuthSection/` — единственная вложенная папка компонентов; остальные компоненты плоские.

## Связанные файлы документации
- `../claude-frontend.md` — родительская (сборка, конфиг Vite)
- `../../backend/api/claude-api.md` — эндпоинты, которые дёргает `utils/api.js`
- `../../claude-index.md` — корневой индекс
