// Проверка почты на формах входа и регистрации (23.11.2026).
// Клиенты ошибаются в домене: «gmail.comc», «gmial.com», «mail.ry», пишут русской раскладкой
// или без «@» — и потом не могут войти. Здесь — подсказка «возможно, вы имели в виду …»,
// без запросов к серверу. Решает человек: «Исправить» или «Нет, всё верно».

export const POPULAR_DOMAINS = [
  'gmail.com', 'mail.ru', 'inbox.ru', 'list.ru', 'bk.ru', 'internet.ru',
  'yandex.ru', 'ya.ru', 'yandex.com', 'rambler.ru',
  'icloud.com', 'outlook.com', 'hotmail.com', 'yahoo.com',
];

// Настоящие домены, похожие на популярные, — их не «исправляем».
const KNOWN_OK = new Set([
  'mail.kg', 'mail.com', 'email.com', 'gmail.ru', 'inbox.lv', 'inbox.kg', 'ymail.com',
  'me.com', 'mac.com', 'gmx.com', 'gmx.de', 'yandex.kz', 'yandex.by', 'yandex.ua',
  'live.com', 'msn.com', 'proton.me', 'protonmail.com', 'list.kg', 'bk.kg', 'ro.ru',
]);

function distance(a, b) {
  const m = a.length, n = b.length;
  const d = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
  for (let j = 1; j <= n; j++) d[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
      // перестановка соседних букв («gamil») — одна ошибка
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
      }
    }
  }
  return d[m][n];
}

// Нормализация при вводе: без пробелов, строчными. Запятую вместо точки в домене — точкой.
export function normalizeEmail(value) {
  let v = String(value || '').replace(/\s+/g, '').toLowerCase();
  const at = v.lastIndexOf('@');
  if (at >= 0) v = v.slice(0, at + 1) + v.slice(at + 1).replace(/,/g, '.');
  return v;
}

function suggestDomain(domain) {
  if (!domain || POPULAR_DOMAINS.includes(domain) || KNOWN_OK.has(domain)) return null;
  const clean = domain.replace(/^\.+|\.+$/g, '');
  if (clean !== domain && POPULAR_DOMAINS.includes(clean)) return clean;
  // «gmail.comc», «gmail.com.kg», «mail.ru1» — лишнее в конце
  for (const p of POPULAR_DOMAINS) {
    if (clean.startsWith(p) && clean.length > p.length) return p;
  }
  // «gmail», «gmail.» , «gmail.co», «mail.r» — оборвано
  for (const p of POPULAR_DOMAINS) {
    const name = p.split('.')[0];
    if (name.length >= 4 && (clean === name || (p.startsWith(clean) && clean.length >= name.length + 1))) return p;
  }
  let best = null, bestD = 99;
  for (const p of POPULAR_DOMAINS) {
    const limit = p.length <= 6 ? 1 : 2; // bk.ru, ya.ru — только одна ошибка
    const dd = distance(clean, p);
    if (dd > 0 && dd <= limit && dd < bestD) { best = p; bestD = dd; }
  }
  return best;
}

// → { problem: null | 'empty' | 'cyrillic' | 'no_at' | 'many_at' | 'no_domain' | 'bad', suggestion: string | null }
export function checkEmail(value) {
  const v = normalizeEmail(value);
  if (!v) return { problem: 'empty', suggestion: null };
  if (/[а-яёңөү]/i.test(v)) return { problem: 'cyrillic', suggestion: null };
  const parts = v.split('@');
  if (parts.length === 1) return { problem: 'no_at', suggestion: null };
  if (parts.length > 2) return { problem: 'many_at', suggestion: null };
  const [local, domain] = parts;
  if (!local || local.startsWith('.') || local.endsWith('.') || local.includes('..')) {
    return { problem: 'bad', suggestion: null };
  }
  const suggested = suggestDomain(domain);
  if (suggested) return { problem: null, suggestion: `${local}@${suggested}` };
  if (!domain || !/^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,24}$/.test(domain)) {
    return { problem: 'no_domain', suggestion: null };
  }
  return { problem: null, suggestion: null };
}
