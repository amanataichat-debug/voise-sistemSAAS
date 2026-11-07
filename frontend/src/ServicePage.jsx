import React from 'react';
import Icon from './components/Icon';
import Logo from './components/Logo';
import { SERVICES, SERVICE_SLUGS, SERVICE_UI } from './seo/services';
import { LANG_PATH } from './i18n';
import { PHONE, PHONE_DISPLAY, TELEGRAM_URL } from './components/contacts';

// Страница услуги (SEO). Чистый статический HTML: на этих страницах нет React-приложения
// (prerender.mjs убирает скрипт бандла), поэтому только ссылки, без обработчиков.
// Кнопка «Попробовать» ведёт на главную с ?signup=1 — там открывается окно регистрации.
export function servicePath(lang, slug) {
  return `${LANG_PATH[lang]}${slug}/`;
}

function ServicePage({ lang, slug }) {
  const s = SERVICES[slug][lang];
  const ui = SERVICE_UI[lang];
  const home = LANG_PATH[lang];
  const otherLang = lang === 'ru' ? 'ky' : 'ru';
  const signup = `${home}?signup=1`;
  return (
    <div className="lp sp">
      <header className="lp-nav scrolled">
        <div className="lp-container lp-nav-inner">
          <Logo href={home} ariaLabel="Voksy AI" />
          <nav className="sp-nav" aria-label={ui.services}>
            {SERVICE_SLUGS.map((k) => (
              <a key={k} href={servicePath(lang, k)} className={k === slug ? 'active' : undefined}>
                {SERVICES[k][lang].nav}
              </a>
            ))}
          </nav>
          <div className="sp-nav-actions">
            <a className="sp-lang" href={servicePath(otherLang, slug)} hrefLang={otherLang}>
              {ui.lang_switch}
            </a>
            <a className="btn btn-ghost" href="/static/login.html">{ui.login}</a>
            <a className="btn btn-primary" href={signup}>{ui.cta}</a>
          </div>
        </div>
      </header>

      <main>
        <section className="sp-hero">
          <div className="lp-container">
            <nav className="sp-crumbs" aria-label="breadcrumb">
              <a href={home}>{ui.home}</a> <span>/</span> <span>{s.nav}</span>
            </nav>
            <div className="sp-hero-ic"><Icon name={SERVICES[slug].icon} /></div>
            <h1>{s.h1}</h1>
            <p className="sp-lead">{s.lead}</p>
            <div className="sp-cta">
              <a className="btn btn-primary btn-lg btn-glow" href={signup}>{ui.cta}</a>
              <a className="btn btn-secondary btn-lg" href={`tel:${PHONE}`}>
                <Icon name="phone" /> {PHONE_DISPLAY}
              </a>
            </div>
            <div className="sp-note">{ui.cta_note}</div>
          </div>
        </section>

        <section className="sp-sec">
          <div className="lp-container">
            <h2>{ui.features_title}</h2>
            <div className="sp-grid">
              {s.features.map(([title, text]) => (
                <div className="sp-card" key={title}>
                  <span className="sp-check"><Icon name="check" /></span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="sp-sec sp-alt">
          <div className="lp-container sp-two">
            <div>
              <h2>{ui.steps_title}</h2>
              <ol className="sp-steps">
                {s.steps.map((step, i) => (
                  <li key={step}><span>{i + 1}</span>{step}</li>
                ))}
              </ol>
            </div>
            <div>
              <h2>{ui.why_title}</h2>
              <ul className="sp-why">
                {s.why.map((w) => (
                  <li key={w}><Icon name="check" />{w}</li>
                ))}
              </ul>
              <a className="sp-link" href={`${home}#pricing`}>{ui.pricing_link} →</a>
            </div>
          </div>
        </section>

        <section className="sp-sec">
          <div className="lp-container">
            <h2>{ui.faq_title}</h2>
            <dl className="faq">
              {s.faq.map(([q, a]) => (
                <div key={q}>
                  <dt>{q}</dt>
                  <dd>{a}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <section className="sp-sec sp-alt">
          <div className="lp-container">
            <h2>{ui.related_title}</h2>
            <div className="sp-related">
              {SERVICE_SLUGS.filter((k) => k !== slug).map((k) => (
                <a key={k} href={servicePath(lang, k)}>
                  <Icon name={SERVICES[k].icon} />
                  <span>{SERVICES[k][lang].h1}</span>
                </a>
              ))}
            </div>
          </div>
        </section>

        <section className="sp-final">
          <div className="lp-container">
            <h2>{ui.final_title}</h2>
            <p>{ui.final_text}</p>
            <a className="btn btn-lg sp-final-btn" href={signup}>{ui.cta}</a>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-container lp-footer-bottom">
          <span>{ui.footer}</span>
          <span>
            <a href={home}>{ui.home}</a> · <a href={TELEGRAM_URL} rel="noopener">Telegram</a> ·{' '}
            <a href="/static/privacy-policy.html">Privacy</a>
          </span>
        </div>
      </footer>
    </div>
  );
}

export default ServicePage;
