// Контакты и номер демо-ассистента (факты с текущего лендинга).
export const PHONE = '+996554128222';
export const PHONE_DISPLAY = '+996 554 128 222';
export const TELEGRAM_URL = 'https://t.me/Aibotconnect';
export const SUPPORT_URL = 'https://t.me/Aibotconnect';
export const SITE_URL = 'https://voksyai.online';

export function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(() => {});
    }
  } catch (e) {
    /* буфер обмена недоступен */
  }
  return Promise.resolve();
}
