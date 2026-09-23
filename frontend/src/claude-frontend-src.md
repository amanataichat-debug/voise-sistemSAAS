# frontend/src — исходники лендинга VoksiAI

## Точки входа
- `main.jsx` — если `#root` пререндерен: язык из `<html lang>`, `hydrateRoot`; иначе (dev)
  язык из localStorage `vs_lang`, `createRoot`. С корня `/` при сохранённом `vs_lang=ru`
  делает redirect на `/ru/`. Подключает `landing.css`.
- `entry-server.jsx` — `render(lang)` и `head(lang)` для `scripts/prerender.mjs`.
- `App.jsx` — секции (`.lp`), модалка авторизации, Lenis, якорный скролл с отступом шапки,
  `useAuth()` (редирект залогиненного в кабинет), `useReferralTracker()`.
- `landing.css` — копия `design-system/css/landing.css` + блок «Дополнения VoksiAI»
  (переключатель языка, иконки моделей без логотипа, KY-фолбэк шрифта: в Unbounded нет Ң Ө Ү,
  поэтому на `html[lang=ky]` заголовки набираются Inter 800).

## i18n (`i18n/`)
`index.jsx` — `LangProvider` (`initialLang`, `navigate`), `useT()` → `{lang, setLang, t}`.
`t('hero.title')` — строка; массивы/объекты (факты, реплики, тарифы) возвращаются целиком,
`{currency}` → «сом». `ru.js` / `ky.js` — словари с одинаковыми ключами. Переключение языка
сохраняет `vs_lang` (общий ключ с кабинетом) и переходит между `/` и `/ru/`.

## components/
Секции: `Navbar` (+`LangSwitch`), `Hero` (+`CallCard`), `ProductTour` (+`Mockups`),
`AgentSection`, `Start`, `Integration`, `Scenarios`, `Pricing`, `Faq`, `FinalCta`, `Footer`.
Общие: `Reveal.jsx` (Reveal/Stagger/Item/Parallax на motion), `SectionHead`, `Icon` (спрайт),
`ModelLogo` (OpenAI, Gemini, Grok, Fish Audio, ElevenLabs), `Logo`, `contacts.js`
(номер демо-ассистента, Telegram, домен). Авторизация: `AuthModal` + `AuthSection/`
(`LoginForm`, `RegisterForm`, `PasswordField`, `EmailVerificationSection`, `ForgotPasswordForm`),
`InlineNotification`.

## hooks/, utils/
`useAuth`, `useEmailVerification`, `useReferralTracker`, `utils/api.js` — бизнес-логика
без изменений. `utils/rememberedName.js` — имя из регистрации (localStorage `vs_first_name`)
для приветствия в модалке входа.
