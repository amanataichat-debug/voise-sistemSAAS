// Телефон на регистрации (23.11.2026): по умолчанию Кыргызстан, +996 и 9 цифр
// (мобильные O!, Beeline, MegaCom: 5xx, 7xx, 2xx, 9xx; городские 312…, 3xx).
// Частые ошибки: «0700 123 456» (ноль после кода), «+996 0700…», лишняя или
// недостающая цифра. Другие страны вводятся с «+» и кодом страны.

const digits = (v) => String(v || '').replace(/\D/g, '');

// 9 «национальных» цифр Кыргызстана из того, что ввёл человек, или null, если номер не кыргызский.
export function kgNational(value) {
  const raw = String(value || '').trim();
  let d = digits(raw);
  if (raw.startsWith('+') || raw.startsWith('00')) {
    if (raw.startsWith('00')) d = d.slice(2);
    if (!d.startsWith('996')) return null; // другая страна
    d = d.slice(3);
  } else if (d.startsWith('996') && d.length >= 12) {
    d = d.slice(3);
  }
  if (d.startsWith('0')) d = d.slice(1); // «0700…» и «+996 0700…»
  return d;
}

// Красивый вид при вводе: «+996 700 123 456». Номер другой страны не трогаем.
export function formatPhoneInput(value) {
  const raw = String(value || '');
  const trimmed = raw.trim();
  const dd = digits(trimmed);
  const kgCode = dd.startsWith('996');
  if (trimmed.startsWith('+') && !kgCode) {
    return raw.replace(/[^\d+\s()-]/g, '').slice(0, 24);
  }
  if (!digits(trimmed)) return trimmed.startsWith('+') ? '+' : '';
  const nat = (kgNational(trimmed) || '').slice(0, 9);
  const parts = [nat.slice(0, 3), nat.slice(3, 6), nat.slice(6, 9)].filter(Boolean);
  return ['+996', ...parts].join(' ');
}

// → { ok, problem: null | 'empty' | 'short' | 'long' | 'kg_prefix' | 'foreign_bad', e164 }
export function checkPhone(value) {
  const raw = String(value || '').trim();
  const d = digits(raw);
  if (!d || d === '996') return { ok: false, problem: 'empty', e164: null };
  const nat = kgNational(raw);
  if (nat === null) {
    // другая страна: код + номер, 10–15 цифр всего
    const ok = d.length >= 10 && d.length <= 15;
    return { ok, problem: ok ? null : 'foreign_bad', e164: ok ? '+' + d : null };
  }
  if (nat.length < 9) return { ok: false, problem: 'short', e164: null };
  if (nat.length > 9) return { ok: false, problem: 'long', e164: null };
  if (!/^[2-9]/.test(nat)) return { ok: false, problem: 'kg_prefix', e164: null };
  return { ok: true, problem: null, e164: '+996' + nat };
}
