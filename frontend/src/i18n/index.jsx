// VoksiAI — i18n-контекст лендинга (KY/RU, без обращений к серверу).
// Язык страницы задаёт URL: `/` — кыргызская версия (по умолчанию), `/ru/` — русская.
// Пререндер кладёт язык в <html lang>, main.jsx передаёт его в initialLang, чтобы
// гидрация совпала с HTML. Выбор пользователя хранится в localStorage `vs_lang`
// (тот же ключ читает кабинет).
import React, { createContext, useContext, useState, useCallback, useMemo } from 'react';
import ky from './ky';
import ru from './ru';

export const DICTS = { ky, ru };
export const LANGS = ['ky', 'ru'];
export const STORAGE_KEY = 'vs_lang';
export const DEFAULT_LANG = 'ky';
// Адрес пререндеренной страницы для каждого языка
export const LANG_PATH = { ky: '/', ru: '/ru/' };
const CURRENCY = 'сом';

export function readStoredLang() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'ky' || stored === 'ru') return stored;
  } catch (e) {
    /* localStorage недоступен */
  }
  return null;
}

export function storeLang(lang) {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch (e) {
    /* приватный режим — выбор просто не переживёт перезагрузку */
  }
}

function lookup(dict, key) {
  return key.split('.').reduce((node, part) => (node == null ? undefined : node[part]), dict);
}

function interpolate(str, vars) {
  return str.replace(/\{(\w+)\}/g, (m, name) => (vars && vars[name] != null ? vars[name] : name === 'currency' ? CURRENCY : m));
}

// t('hero.title') → строка; массивы/объекты возвращаются как есть (строки внутри
// массивов тоже проходят подстановку {currency}).
export function translate(lang, key, vars) {
  let value = lookup(DICTS[lang], key);
  if (value === undefined) value = lookup(DICTS.ru, key);
  if (value === undefined) return key;
  const fill = (v) => {
    if (typeof v === 'string') return interpolate(v, vars);
    if (Array.isArray(v)) return v.map(fill);
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, fill(x)]));
    return v;
  };
  return fill(value);
}

const LangContext = createContext({
  lang: DEFAULT_LANG,
  setLang: () => {},
  t: (key) => key,
});

// navigate=true (прод): переход между `/` и `/ru/`; в dev-сервере язык меняется на месте.
export function LangProvider({ children, initialLang = DEFAULT_LANG, navigate = false }) {
  const [lang, setLangState] = useState(initialLang === 'ru' ? 'ru' : 'ky');

  const setLang = useCallback(
    (next) => {
      if (!LANGS.includes(next)) return;
      storeLang(next);
      if (next === lang) return;
      if (navigate && typeof window !== 'undefined') {
        const { search, hash } = window.location;
        window.location.assign(LANG_PATH[next] + search + hash);
        return;
      }
      setLangState(next);
      if (typeof document !== 'undefined') {
        document.documentElement.lang = next;
        document.title = translate(next, 'seo.title');
      }
    },
    [lang, navigate]
  );

  const t = useCallback((key, vars) => translate(lang, key, vars), [lang]);
  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useT() {
  return useContext(LangContext);
}
