# frontend/src — исходники лендинга Voksy AI

## Точки входа
- `main.jsx` — если `#root` пререндерен: язык из `<html lang>`, `hydrateRoot`; иначе (dev)
  язык из localStorage `vs_lang`, `createRoot`. С корня `/` при сохранённом `vs_lang=ru`
  делает redirect на `/ru/`. Подключает `landing.css`.
- `entry-server.jsx` — `render(lang)` и `head(lang)` для `scripts/prerender.mjs`.
- `App.jsx` — секции (`.lp`), модалка авторизации, Lenis, якорный скролл с отступом шапки,
  `useAuth()` (редирект залогиненного в кабинет), `useReferralTracker()`.
- `landing.css` — копия `design-system/css/landing.css` + блок «Дополнения Voksy AI»
  (переключатель языка, иконки моделей без логотипа, KY-фолбэк шрифта: в Unbounded нет Ң Ө Ү,
  поэтому на `html[lang=ky]` заголовки набираются Inter 800).

## i18n (`i18n/`)
`index.jsx` — `LangProvider` (`initialLang`, `navigate`), `useT()` → `{lang, setLang, t}`.
`t('hero.title')` — строка; массивы/объекты (факты, реплики, тарифы) возвращаются целиком,
`{currency}` → «сом». `ru.js` / `ky.js` — словари с одинаковыми ключами. Переключение языка
сохраняет `vs_lang` (общий ключ с кабинетом) и переходит между `/` и `/ru/`.

## components/
Секции по порядку (ноябрь 2026, текст для владельца бизнеса, не для программиста): `Navbar` (+`LangSwitch`),
`Hero` (+`CallCard`: сцены звонок/исходящий/первым в WhatsApp, лента каналов вместо «моделей»),
`Abilities` (01, bento из шести умений), `AgentShowcase` (02, #agent: телефон со сценами звонок /
первым в мессенджер / голосовое / КП под клиента, автопереключение по CSS-анимации полоски),
`CrmSection` (03, #crm: живая воронка с `layoutId`-переездом карточки + карточка клиента),
`AgentSection` (04, #day: день агента), `ProductTour` (05, #platform, `Mockups`), `Start` (06),
`Scenarios` (07), `Pricing` (08), `Faq` (09), `FinalCta`, `Footer`.
Общие: `Reveal.jsx` (Reveal/Stagger/Item/Parallax на motion), `SectionHead`, `Icon` (спрайт),
`Logo`, `contacts.js`
(номер демо-ассистента, Telegram, домен). Авторизация: `AuthModal` + `AuthSection/`
(`LoginForm`, `RegisterForm`, `PasswordField`, `EmailVerificationSection`, `ForgotPasswordForm`),
`InlineNotification`.

## hooks/, utils/
`useAuth`, `useEmailVerification`, `useReferralTracker`, `utils/api.js` — бизнес-логика
без изменений. `utils/rememberedName.js` — имя из регистрации (localStorage `vs_first_name`)
для приветствия в модалке входа.

## Правила текста лендинга
- Названия провайдеров (ElevenLabs, GPT-Live, OpenAI, Gemini, Grok, Fish) на лендинге не
  упоминаем — только наши движки Voksy Voice / Voksy Live / Voksy Live Go (white-label). Нет слов «промпт», «ключи», «оркестратор», «SIP-шлюз».
- Новые секции подключают ключи обоих словарей сразу: `ru.js` и `ky.js` должны совпадать по
  структуре (проверка: сравнить ключи и длины массивов).
- Блоки с `useReducedMotion` не должны менять разметку между сервером и клиентом (иначе ошибка
  гидрации #418): отключать анимацию через `@media (prefers-reduced-motion)` в CSS.
