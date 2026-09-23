import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { LangProvider, readStoredLang, storeLang } from './i18n';
import './landing.css';

const rootEl = document.getElementById('root');
// Пререндеренная страница (`/` — ky, `/ru/` — ru) уже содержит разметку: язык берём
// из <html lang>, чтобы гидрация совпала. В dev-сервере разметки нет — язык из localStorage.
const prerendered = rootEl.hasChildNodes();
const pageLang = document.documentElement.lang === 'ru' ? 'ru' : 'ky';
const stored = readStoredLang();

// Корень `/` — точка входа по умолчанию: если пользователь раньше выбрал русский
// (на лендинге или в кабинете), отправляем его на `/ru/`. Явный `/ru/` не трогаем.
if (prerendered && pageLang === 'ky' && stored === 'ru' && window.location.pathname === '/') {
  window.location.replace('/ru/' + window.location.search + window.location.hash);
} else {
  if (prerendered && !stored) storeLang(pageLang);
  const lang = prerendered ? pageLang : stored || pageLang;
  if (!prerendered) document.documentElement.lang = lang;

  const app = (
    <React.StrictMode>
      <LangProvider initialLang={lang} navigate={prerendered}>
        <App />
      </LangProvider>
    </React.StrictMode>
  );

  if (prerendered) ReactDOM.hydrateRoot(rootEl, app);
  else ReactDOM.createRoot(rootEl).render(app);
}
