// Пререндер лендинга: HTML страницы и SEO-разметка <head> для каждого языка.
// Статический HTML читают роботы Google, Яндекса и ИИ-поиска, не выполняющие JS.
// Вызывается из scripts/prerender.mjs после `vite build`.
import React from 'react';
import { renderToString } from 'react-dom/server';
import App from './App';
import { LangProvider, translate, LANG_PATH } from './i18n';
import { SITE_URL, PHONE, TELEGRAM_URL } from './components/contacts';
import ServicePage, { servicePath } from './ServicePage';
import { SERVICES, SERVICE_SLUGS, SERVICE_UI } from './seo/services';

export { SERVICES, SERVICE_SLUGS, servicePath };

export function renderService(lang, slug) {
  return renderToString(<ServicePage lang={lang} slug={slug} />);
}

// Организация: Бишкек, Кыргызстан — для локальной выдачи Google/Яндекса и ИИ-поиска
const ORG = {
  '@type': 'Organization',
  '@id': `${SITE_URL}/#org`,
  name: 'Voksy AI',
  alternateName: ['Voksy', 'VoksyAI', 'Voksy AI Кыргызстан'],
  url: `${SITE_URL}/`,
  logo: `${SITE_URL}/static/android-chrome-512x512.png`,
  sameAs: [TELEGRAM_URL],
  address: { '@type': 'PostalAddress', addressLocality: 'Бишкек', addressCountry: 'KG' },
  areaServed: { '@type': 'Country', name: 'Kyrgyzstan' },
  knowsLanguage: ['ky', 'ru'],
  contactPoint: {
    '@type': 'ContactPoint', telephone: PHONE, contactType: 'customer support',
    areaServed: 'KG', availableLanguage: ['Kyrgyz', 'Russian'],
  },
};

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
      ORG,
      {
        '@type': 'WebSite',
        '@id': `${SITE_URL}/#site`,
        url: `${SITE_URL}/`,
        name: 'Voksy AI',
        inLanguage: ['ky', 'ru'],
        publisher: { '@id': `${SITE_URL}/#org` },
      },
      {
        '@type': 'SoftwareApplication',
        name: 'Voksy AI',
        url,
        applicationCategory: 'BusinessApplication',
        operatingSystem: 'Web',
        inLanguage: lang,
        description,
        featureList: t('abilities.items').map((r) => r[0]),
        offers: { '@type': 'Offer', price: '0', priceCurrency: 'KGS', description: t('seo.offer_description') },
        areaServed: { '@type': 'Country', name: 'Kyrgyzstan' },
        publisher: { '@id': `${SITE_URL}/#org` },
      },
      {
        '@type': 'ItemList',
        name: lang === 'ru' ? 'Услуги Voksy AI' : 'Voksy AI кызматтары',
        itemListElement: SERVICE_SLUGS.map((slug, i) => ({
          '@type': 'ListItem', position: i + 1,
          name: SERVICES[slug][lang].h1, url: SITE_URL + servicePath(lang, slug),
        })),
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
    `<meta property="og:site_name" content="Voksy AI">`,
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


// <head> страницы услуги: свои title/description/canonical, hreflang на пару ky/ru,
// разметка Service + BreadcrumbList + FAQPage.
export function serviceHead(lang, slug) {
  const s = SERVICES[slug][lang];
  const ui = SERVICE_UI[lang];
  const url = SITE_URL + servicePath(lang, slug);
  const other = lang === 'ru' ? 'ky' : 'ru';
  const image = `${SITE_URL}/static/android-chrome-512x512.png`;
  const graph = {
    '@context': 'https://schema.org',
    '@graph': [
      ORG,
      {
        '@type': 'Service',
        name: s.h1,
        serviceType: s.nav,
        description: s.description,
        url,
        inLanguage: lang,
        provider: { '@id': `${SITE_URL}/#org` },
        areaServed: { '@type': 'Country', name: 'Kyrgyzstan' },
        availableChannel: { '@type': 'ServiceChannel', serviceUrl: url, servicePhone: PHONE },
      },
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: ui.home, item: SITE_URL + LANG_PATH[lang] },
          { '@type': 'ListItem', position: 2, name: s.nav, item: url },
        ],
      },
      {
        '@type': 'FAQPage',
        inLanguage: lang,
        mainEntity: s.faq.map(([q, a]) => ({ '@type': 'Question', name: q, acceptedAnswer: { '@type': 'Answer', text: a } })),
      },
    ],
  };
  return [
    `<title>${esc(s.title)}</title>`,
    `<meta name="description" content="${esc(s.description)}">`,
    `<meta name="keywords" content="${esc(s.keywords)}">`,
    `<meta name="robots" content="index, follow, max-image-preview:large">`,
    `<link rel="canonical" href="${url}">`,
    `<link rel="alternate" hreflang="ky" href="${SITE_URL}${servicePath('ky', slug)}">`,
    `<link rel="alternate" hreflang="ru" href="${SITE_URL}${servicePath('ru', slug)}">`,
    `<link rel="alternate" hreflang="x-default" href="${SITE_URL}${servicePath('ky', slug)}">`,
    `<meta property="og:type" content="website">`,
    `<meta property="og:site_name" content="Voksy AI">`,
    `<meta property="og:locale" content="${translate(lang, 'seo.locale')}">`,
    `<meta property="og:locale:alternate" content="${translate(other, 'seo.locale')}">`,
    `<meta property="og:url" content="${url}">`,
    `<meta property="og:title" content="${esc(s.title)}">`,
    `<meta property="og:description" content="${esc(s.description)}">`,
    `<meta property="og:image" content="${image}">`,
    `<meta name="twitter:card" content="summary">`,
    `<meta name="twitter:title" content="${esc(s.title)}">`,
    `<meta name="twitter:description" content="${esc(s.description)}">`,
    `<script type="application/ld+json">${json(graph)}</script>`,
  ].join('\n  ');
}
