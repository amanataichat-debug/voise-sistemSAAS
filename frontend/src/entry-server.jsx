// Пререндер лендинга: HTML страницы и SEO-разметка <head> для каждого языка.
// Статический HTML читают роботы Google, Яндекса и ИИ-поиска, не выполняющие JS.
// Вызывается из scripts/prerender.mjs после `vite build`.
import React from 'react';
import { renderToString } from 'react-dom/server';
import App from './App';
import { LangProvider, translate, LANG_PATH } from './i18n';
import { SITE_URL, PHONE, TELEGRAM_URL } from './components/contacts';

export function render(lang) {
  return renderToString(
    <React.StrictMode>
      <LangProvider initialLang={lang}>
        <App />
      </LangProvider>
    </React.StrictMode>
  );
}

const esc = (s) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// JSON внутри <script>: экранируем «<», чтобы строка не закрыла тег
const json = (obj) => JSON.stringify(obj).replace(/</g, '\\u003c');

export function faqJsonLd(lang) {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    inLanguage: lang,
    mainEntity: translate(lang, 'faq.items').map(([q, a]) => ({
      '@type': 'Question',
      name: q,
      acceptedAnswer: { '@type': 'Answer', text: a },
    })),
  };
}

export function head(lang) {
  const t = (key) => translate(lang, key);
  const url = SITE_URL + LANG_PATH[lang];
  const title = t('seo.title');
  const description = t('seo.description');
  const image = `${SITE_URL}/static/android-chrome-512x512.png`;
  const other = lang === 'ru' ? 'ky' : 'ru';

  const graph = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Organization',
        '@id': `${SITE_URL}/#org`,
        name: 'VoksiAI',
        url: `${SITE_URL}/`,
        logo: image,
        sameAs: [TELEGRAM_URL],
        contactPoint: { '@type': 'ContactPoint', telephone: PHONE, contactType: 'customer support', areaServed: 'KG' },
      },
      {
        '@type': 'WebSite',
        '@id': `${SITE_URL}/#site`,
        url: `${SITE_URL}/`,
        name: 'VoksiAI',
        inLanguage: ['ky', 'ru'],
        publisher: { '@id': `${SITE_URL}/#org` },
      },
      {
        '@type': 'SoftwareApplication',
        name: 'VoksiAI',
        url,
        applicationCategory: 'BusinessApplication',
        operatingSystem: 'Web',
        inLanguage: lang,
        description,
        featureList: t('tour.rows').map((r) => r.title),
        offers: { '@type': 'Offer', price: '0', priceCurrency: 'KGS', description: t('seo.offer_description') },
        publisher: { '@id': `${SITE_URL}/#org` },
      },
    ],
  };

  return [
    `<title>${esc(title)}</title>`,
    `<meta name="description" content="${esc(description)}">`,
    `<meta name="robots" content="index, follow, max-image-preview:large">`,
    `<link rel="canonical" href="${url}">`,
    `<link rel="alternate" hreflang="ky" href="${SITE_URL}${LANG_PATH.ky}">`,
    `<link rel="alternate" hreflang="ru" href="${SITE_URL}${LANG_PATH.ru}">`,
    `<link rel="alternate" hreflang="x-default" href="${SITE_URL}${LANG_PATH.ky}">`,
    `<meta property="og:type" content="website">`,
    `<meta property="og:site_name" content="VoksiAI">`,
    `<meta property="og:locale" content="${t('seo.locale')}">`,
    `<meta property="og:locale:alternate" content="${translate(other, 'seo.locale')}">`,
    `<meta property="og:url" content="${url}">`,
    `<meta property="og:title" content="${esc(title)}">`,
    `<meta property="og:description" content="${esc(description)}">`,
    `<meta property="og:image" content="${image}">`,
    `<meta property="og:image:alt" content="${esc(t('seo.og_image_alt'))}">`,
    `<meta name="twitter:card" content="summary">`,
    `<meta name="twitter:title" content="${esc(title)}">`,
    `<meta name="twitter:description" content="${esc(description)}">`,
    `<meta name="twitter:image" content="${image}">`,
    `<script type="application/ld+json">${json(graph)}</script>`,
    `<script type="application/ld+json">${json(faqJsonLd(lang))}</script>`,
  ].join('\n  ');
}
