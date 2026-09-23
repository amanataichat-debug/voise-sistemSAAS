// Имя из регистрации запоминаем локально, чтобы поздороваться при следующем входе.
const KEY = 'vs_first_name';

export function rememberFirstName(name) {
  try {
    const value = (name || '').trim().slice(0, 40);
    if (value) localStorage.setItem(KEY, value);
  } catch (e) {
    /* localStorage недоступен */
  }
}

export function readFirstName() {
  try {
    return localStorage.getItem(KEY) || '';
  } catch (e) {
    return '';
  }
}
