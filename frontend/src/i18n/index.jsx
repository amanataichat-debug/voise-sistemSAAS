// VoiceSystem — i18n-контекст лендинга (KY/RU, без обращений к серверу)
import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import ky from './ky';
import ru from './ru';

const DICTS = { ky, ru };
const STORAGE_KEY = 'vs_lang';
const DEFAULT_LANG = 'ky';

const LangContext = createContext({
  lang: DEFAULT_LANG,
  setLang: () => {},
  t: (key) => key,
});

function readStoredLang() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'ky' || stored === 'ru') return stored;
  } catch (e) {
    /* localStorage недоступен — остаёмся на языке по умолчанию */
  }
  return DEFAULT_LANG;
}

export function LangProvider({ children }) {
  const [lang, setLangState] = useState(readStoredLang);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = DICTS[lang]['meta.title'] || document.title;
  }, [lang]);

  const setLang = useCallback((next) => {
    if (next !== 'ky' && next !== 'ru') return;
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (e) {
      /* приватный режим — выбор просто не переживёт перезагрузку */
    }
  }, []);

  const t = useCallback(
    (key) => DICTS[lang][key] ?? DICTS.ru[key] ?? key,
    [lang]
  );

  return (
    <LangContext.Provider value={{ lang, setLang, t }}>
      {children}
    </LangContext.Provider>
  );
}

export function useT() {
  return useContext(LangContext);
}
