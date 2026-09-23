/**
 * VoksiAI — локализация личного кабинета (RU / KY), без зависимостей.
 *
 *   I18N.lang                     — текущий язык ('ru' | 'ky')
 *   I18N.t('section.key', {n: 3}, 'текст по умолчанию')
 *   I18N.setLang('ky')            — сохранить выбор и перезагрузить страницу
 *   I18N.apply(root)              — перевести разметку с data-i18n*
 *
 * Разметка:
 *   data-i18n="key"        → textContent
 *   data-i18n-html="key"   → innerHTML (строки с <b>)
 *   data-i18n-ph="key"     → placeholder
 *   data-i18n-title="key"  → title, aria-label и data-tip
 *
 * Язык: ?lang= → localStorage.vs_lang (общий с лендингом) → язык браузера
 * (ky* → ky) → ru. Словарь /static/i18n/<lang>.json грузится синхронно, чтобы
 * сайдбар и страницы сразу рисовались на нужном языке; в пределах вкладки он
 * кэшируется в sessionStorage. Подключать в <head> ДО ui.js и sidebar.js.
 */
(function (global) {
  'use strict';

  var LANGS = ['ru', 'ky'];
  var STORAGE_KEY = 'vs_lang';
  var DICT_VERSION = '2';
  // Подстановки по умолчанию для {currency} и {tz} в строках словаря
  var DEFAULTS = { currency: 'сом', tz: 'Бишкек' };

  function detect() {
    try {
      var q = new URLSearchParams(global.location.search).get('lang');
      if (LANGS.indexOf(q) !== -1) {
        try { localStorage.setItem(STORAGE_KEY, q); } catch (e) { /* ignore */ }
        return q;
      }
    } catch (e) { /* ignore */ }
    try {
      var s = localStorage.getItem(STORAGE_KEY);
      if (LANGS.indexOf(s) !== -1) return s;
    } catch (e) { /* ignore */ }
    var nav = (global.navigator && (global.navigator.language || '')).toLowerCase();
    return nav.indexOf('ky') === 0 ? 'ky' : 'ru';
  }

  function load(lang) {
    var cacheKey = 'vf_i18n_' + lang + '_' + DICT_VERSION;
    try {
      var cached = sessionStorage.getItem(cacheKey);
      if (cached) return JSON.parse(cached);
    } catch (e) { /* ignore */ }
    try {
      var xhr = new XMLHttpRequest();
      xhr.open('GET', '/static/i18n/' + lang + '.json?v=' + DICT_VERSION, false);
      xhr.send(null);
      if (xhr.status >= 200 && xhr.status < 300) {
        try { sessionStorage.setItem(cacheKey, xhr.responseText); } catch (e) { /* ignore */ }
        return JSON.parse(xhr.responseText);
      }
    } catch (e) { /* словарь недоступен — остаются тексты по умолчанию */ }
    return {};
  }

  var lang = detect();
  var dict = load(lang);

  function lookup(key) {
    var parts = String(key).split('.');
    var cur = dict;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== 'object') return null;
      cur = cur[parts[i]];
    }
    return typeof cur === 'string' && cur !== '' ? cur : null;
  }

  function format(str, params) {
    if (!params) return str;
    return str.replace(/\{(\w+)\}/g, function (m, k) {
      return params[k] != null ? String(params[k]) : m;
    });
  }

  function t(key, params, def) {
    var v = lookup(key);
    if (v == null) v = def != null ? def : key;
    return format(v, Object.assign({}, DEFAULTS, params || {}));
  }

  function apply(root) {
    root = root || document;
    if (!root.querySelectorAll) return;
    var i, el, v;
    var list = root.querySelectorAll('[data-i18n]');
    for (i = 0; i < list.length; i++) {
      el = list[i]; v = lookup(el.getAttribute('data-i18n'));
      if (v != null) el.textContent = format(v, DEFAULTS);
    }
    list = root.querySelectorAll('[data-i18n-html]');
    for (i = 0; i < list.length; i++) {
      el = list[i]; v = lookup(el.getAttribute('data-i18n-html'));
      if (v != null) el.innerHTML = format(v, DEFAULTS);
    }
    list = root.querySelectorAll('[data-i18n-ph]');
    for (i = 0; i < list.length; i++) {
      el = list[i]; v = lookup(el.getAttribute('data-i18n-ph'));
      if (v != null) el.setAttribute('placeholder', v);
    }
    list = root.querySelectorAll('[data-i18n-title]');
    for (i = 0; i < list.length; i++) {
      el = list[i]; v = lookup(el.getAttribute('data-i18n-title'));
      if (v == null) continue;
      el.setAttribute('title', v);
      el.setAttribute('aria-label', v);
      if (el.hasAttribute('data-tip')) el.setAttribute('data-tip', v);
    }
  }

  function setLang(next) {
    if (LANGS.indexOf(next) === -1 || next === lang) return;
    try { localStorage.setItem(STORAGE_KEY, next); } catch (e) { /* ignore */ }
    // ?lang= в адресе перебил бы выбор — убираем его
    try {
      var url = new URL(global.location.href);
      url.searchParams.delete('lang');
      global.location.replace(url.toString());
    } catch (e) { global.location.reload(); }
  }

  document.documentElement.setAttribute('lang', lang);

  global.I18N = { lang: lang, LANGS: LANGS, t: t, apply: apply, setLang: setLang };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(document); });
  else apply(document);
})(window);
