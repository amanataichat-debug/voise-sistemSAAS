import React from 'react';
import { useT } from '../i18n';

// Переключатель языка KY | RU. Ссылки ведут на пререндеренные версии (`/` и `/ru/`),
// клик сохраняет выбор в localStorage `vs_lang` (через setLang).
function LangSwitch({ className = '' }) {
  const { lang, setLang, t } = useT();
  const opts = [
    { code: 'ky', href: '/' },
    { code: 'ru', href: '/ru/' },
  ];

  return (
    <div className={`lp-lang ${className}`.trim()} role="group" aria-label={t('lang.group_aria')}>
      {opts.map((o) => (
        <a
          key={o.code}
          href={o.href}
          hrefLang={o.code}
          lang={o.code}
          title={t(`lang.${o.code}_title`)}
          className={`lp-lang-opt${lang === o.code ? ' on' : ''}`}
          aria-current={lang === o.code ? 'true' : undefined}
          onClick={(e) => {
            e.preventDefault();
            setLang(o.code);
          }}
        >
          {t(`lang.${o.code}`)}
        </a>
      ))}
    </div>
  );
}

export default LangSwitch;
