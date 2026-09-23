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

const { render, head } = await import(pathToFileURL(path.join(ssrDir, 'entry-server.mjs')).href);
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

fs.rmSync(ssrDir, { recursive: true, force: true });
