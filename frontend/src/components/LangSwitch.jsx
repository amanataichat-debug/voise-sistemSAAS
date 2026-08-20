import React from 'react';
import { useT } from '../i18n';

// Капсула KY | RU: «пилюля» активного языка перемещается transform-слайдом
function LangSwitch({ compact }) {
  const { lang, setLang } = useT();

  return (
    <div
      className={`lang-switch${compact ? ' lang-switch-compact' : ''}`}
      data-lang={lang}
      role="group"
      aria-label="Тил / Язык"
    >
      <span className="lang-pill" aria-hidden="true"></span>
      <button
        type="button"
        className={`lang-opt${lang === 'ky' ? ' active' : ''}`}
        onClick={() => setLang('ky')}
        aria-pressed={lang === 'ky'}
      >
        KY
      </button>
      <button
        type="button"
        className={`lang-opt${lang === 'ru' ? ' active' : ''}`}
        onClick={() => setLang('ru')}
        aria-pressed={lang === 'ru'}
      >
        RU
      </button>
    </div>
  );
}

export default LangSwitch;
