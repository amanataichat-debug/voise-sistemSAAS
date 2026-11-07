// Пререндер лендинга после `vite build`.
// 1) Собирает SSR-бандл src/entry-server.jsx во временную папку node_modules/.voksi-ssr.
// 2) Для каждого языка рендерит <App/> в строку и вставляет её в собранный index.html
//    вместе с SEO-разметкой <head> (title, description, canonical, hreflang, OG, JSON-LD).
// 3) Пишет backend/static/landing/index.html (ky, отдаётся на `/`) и
//    backend/static/landing/ru/index.html (ru, отдаётся на `/ru/`). Временную папку удаляет.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'vite';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outDir = path.resolve(root, '../backend/static/landing');
const ssrDir = path.resolve(root, 'node_modules/.voksi-ssr');

const PAGES = [
  { lang: 'ky', file: path.join(outDir, 'index.html') },
  { lang: 'ru', file: path.join(outDir, 'ru', 'index.html') },
];

await build({
  root,
  logLevel: 'warn',
  build: {
    ssr: 'src/entry-server.jsx',
    outDir: ssrDir,
    emptyOutDir: true,
    rollupOptions: { output: { entryFileNames: 'entry-server.mjs' } },
  },
});

const { render, head, renderService, serviceHead, SERVICES, SERVICE_SLUGS, servicePath } =
  await import(pathToFileURL(path.join(ssrDir, 'entry-server.mjs')).href);
const template = fs.readFileSync(path.join(outDir, 'index.html'), 'utf8');

if (!template.includes('<div id="root"></div>') || !template.includes('<!--app-head-->')) {
  throw new Error('prerender: в собранном index.html нет <div id="root"></div> или <!--app-head-->');
}

for (const { lang, file } of PAGES) {
  const html = template
    .replace(/<html lang="[^"]*">/, `<html lang="${lang}">`)
    .replace(/<title>[^<]*<\/title>\s*<!--app-head-->/, head(lang))
    .replace('<div id="root"></div>', `<div id="root">${render(lang)}</div>`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, html);
  console.log(`prerender: ${path.relative(root, file)} (${lang}, ${(html.length / 1024).toFixed(1)} КБ)`);
}

// --- Страницы услуг (SEO): статичный HTML без React-приложения -----------------------
// Скрипт бандла убираем: иначе App смонтировался бы поверх и показал главную.
const staticTemplate = template
  .replace(/\s*<script type="module"[^>]*><\/script>/g, '')
  .replace(/\s*<link rel="modulepreload"[^>]*>/g, '');
const SITE = 'https://voksyai.online';
const urls = [
  { loc: '/', alt: { ky: '/', ru: '/ru/' }, priority: '1.0' },
  { loc: '/ru/', alt: { ky: '/', ru: '/ru/' }, priority: '1.0' },
];
for (const slug of SERVICE_SLUGS) {
  for (const lang of ['ky', 'ru']) {
    const rel = servicePath(lang, slug);
    const file = path.join(outDir, rel, 'index.html');
    const html = staticTemplate
      .replace(/<html lang="[^"]*">/, `<html lang="${lang}">`)
      .replace(/<title>[^<]*<\/title>\s*<!--app-head-->/, serviceHead(lang, slug))
      .replace('<div id="root"></div>', `<div id="root">${renderService(lang, slug)}</div>`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, html);
    urls.push({ loc: rel, alt: { ky: servicePath('ky', slug), ru: servicePath('ru', slug) }, priority: '0.8' });
  }
}
console.log(`prerender: ${SERVICE_SLUGS.length * 2} страниц услуг`);

// --- sitemap.xml (с hreflang-парами) --------------------------------------------------
const today = new Date().toISOString().slice(0, 10);
const legal = ['/static/privacy-policy.html', '/static/terms-of-service.html', '/static/public-offer.html',
  '/static/payment-terms.html', '/static/prompts-wiki.html', '/static/api-docs.html'];
const xml = ['<?xml version="1.0" encoding="UTF-8"?>',
  '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'];
for (const u of urls) {
  xml.push('  <url>', `    <loc>${SITE}${u.loc}</loc>`, `    <lastmod>${today}</lastmod>`,
    `    <changefreq>weekly</changefreq>`, `    <priority>${u.priority}</priority>`);
  for (const [l, href] of Object.entries(u.alt)) xml.push(`    <xhtml:link rel="alternate" hreflang="${l}" href="${SITE}${href}"/>`);
  xml.push(`    <xhtml:link rel="alternate" hreflang="x-default" href="${SITE}${u.alt.ky}"/>`, '  </url>');
}
for (const p of legal) xml.push('  <url>', `    <loc>${SITE}${p}</loc>`, `    <priority>0.3</priority>`, '  </url>');
xml.push('</urlset>', '');
fs.writeFileSync(path.join(outDir, 'sitemap.xml'), xml.join('\n'));

// --- llms.txt: краткая справка о компании для ИИ-поисковиков (ChatGPT, Gemini, Perplexity) ---
const ru = (slug) => SERVICES[slug].ru;
const llms = [
  '# Voksy AI',
  '',
  '> Voksy AI (voksyai.online) — платформа ИИ-агентов для бизнеса в Кыргызстане (Бишкек). Голосовой ИИ-агент',
  '> отвечает на звонки и звонит клиентам на кыргызском и русском, чат-бот отвечает в WhatsApp, Telegram и',
  '> Instagram, CRM и аналитика заполняются ИИ автоматически. Номера +996, оплата в сомах.',
  '',
  '## Ключевые факты',
  '- Страна: Кыргызстан, город: Бишкек. Языки агентов: кыргызский и русский.',
  '- Каналы: телефон (номера +996, исходящие на номера O!), WhatsApp, Telegram, Instagram Direct, виджет на сайт.',
  '- Тарифы в месяц: AI Voice — 1 990 сом (голосовой ассистент), Старт — 7 500 сом (чат-бот в мессенджерах, CRM, аналитика, входящие звонки), Profi — 12 500 сом (автономный ИИ-агент по продажам: сам звонит и пишет первым). Скидки: 6 месяцев −20%, год −30%.',
  '- Минута разговора: 5,47 сом (входящий), 6,47 сом (исходящий на O!). Пробный период: 3 дня с возможностями тарифа Старт.',
  '- Запуск без программистов примерно за 10 минут. Демо-номер ИИ-ассистента: +996 705 701 707. Поддержка: Telegram @Aibotconnect.',
  '',
  '## Услуги',
  ...SERVICE_SLUGS.map((slug) => `- [${ru(slug).h1}](${SITE}${servicePath('ru', slug)}): ${ru(slug).description}`),
  '',
  '## Страницы',
  `- [Главная (кыргызча)](${SITE}/)`,
  `- [Главная (русский)](${SITE}/ru/)`,
  `- [Тарифы](${SITE}/ru/#pricing)`,
  `- [Политика конфиденциальности](${SITE}/static/privacy-policy.html)`,
  `- [Публичная оферта](${SITE}/static/public-offer.html)`,
  '',
  '## Частые вопросы',
  ...SERVICE_SLUGS.flatMap((slug) => ru(slug).faq.slice(0, 2).map(([q, a]) => `- ${q} ${a}`)),
  '',
];
fs.writeFileSync(path.join(outDir, 'llms.txt'), llms.join('\n'));
console.log('prerender: sitemap.xml, llms.txt');

fs.rmSync(ssrDir, { recursive: true, force: true });
