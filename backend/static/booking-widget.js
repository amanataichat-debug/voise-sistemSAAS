/*
 * Виджет онлайн-записи Voksy AI для сайта клиента (ветка 1811-niche-admin-speed).
 *
 * Владелец копирует код из «Записи → Ссылка для клиентов → Кнопка на сайт» и вставляет на свой сайт:
 *   <script src="https://voksyai.online/static/booking-widget.js" data-slug="salon-aidana" async></script>
 *
 * Атрибуты (все, кроме data-slug, необязательны):
 *   data-slug      — адрес страницы записи (/b/<slug>)
 *   data-text      — текст кнопки (по умолчанию «Записаться онлайн»)
 *   data-color     — цвет кнопки (#2a5ce8)
 *   data-position  — right | left (угол экрана)
 *   data-button    — "none": плавающей кнопки нет, окно открывают свои кнопки сайта
 *   data-chat      — "1": не запись, а ЧАТ с ИИ-администратором салона (Voksy Beauty 1.6в): кнопка
 *                    «Написать администратору», окно /b/<slug>?embed=1&chat=1. Можно поставить оба
 *                    скрипта — кнопка чата встанет над кнопкой записи.
 * Свои кнопки: любой элемент с атрибутом data-voksy-booking (data-voksy-chat — для чата) или ссылка на
 * /b/<slug> открывает окно. Из кода сайта: window.VoksyBooking.open() / window.VoksyChat.open().
 *
 * Окно — iframe со страницей /b/<slug>?embed=1. Никаких cookie и данных сайта виджет не читает.
 */
(function () {
  'use strict';
  var script = document.currentScript || (function () {
    var all = document.getElementsByTagName('script');
    for (var i = all.length - 1; i >= 0; i--) if (/booking-widget\.js/.test(all[i].src)) return all[i];
    return null;
  })();
  if (!script) return;
  var isChat = script.getAttribute('data-chat') === '1';
  var NS = isChat ? 'VoksyChat' : 'VoksyBooking';
  var P = isChat ? 'vxc' : 'vxb';
  var ATTR = isChat ? 'data-voksy-chat' : 'data-voksy-booking';
  if (window[NS]) return;
  var slug = (script.getAttribute('data-slug') || '').toLowerCase();
  if (!/^[a-z0-9-]{3,64}$/.test(slug)) { if (window.console) console.warn('[Voksy] booking-widget: неверный data-slug'); return; }
  var origin;
  try { origin = new URL(script.src).origin; } catch (e) { origin = 'https://voksyai.online'; }
  var text = script.getAttribute('data-text') || (isChat ? 'Написать администратору' : 'Записаться онлайн');
  var color = script.getAttribute('data-color') || '#2a5ce8';
  if (!/^#[0-9a-fA-F]{3,8}$/.test(color)) color = '#2a5ce8';
  var side = script.getAttribute('data-position') === 'left' ? 'left' : 'right';
  var noButton = script.getAttribute('data-button') === 'none';
  var pageUrl = origin + '/b/' + slug;

  var css = '' +
    '.' + P + '-btn{position:fixed;bottom:20px;' + side + ':20px;z-index:2147483000;display:flex;align-items:center;gap:8px;' +
    'height:52px;padding:0 22px 0 18px;border:0;border-radius:26px;background:' + color + ';color:#fff;cursor:pointer;' +
    'font:600 15px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,sans-serif;' +
    'box-shadow:0 12px 28px -10px rgba(14,23,41,.45);transition:transform .15s}' +
    '.' + P + '-btn:hover{transform:translateY(-2px)}.' + P + '-btn:active{transform:scale(.97)}' +
    '.' + P + '-btn svg{width:20px;height:20px;flex:none}' +
    '.' + P + '-ov{position:fixed;inset:0;z-index:2147483001;background:rgba(14,23,41,.5);display:flex;align-items:center;' +
    'justify-content:center;opacity:0;transition:opacity .2s}.' + P + '-ov.on{opacity:1}' +
    '.' + P + '-box{position:relative;width:440px;max-width:calc(100vw - 24px);height:min(760px,calc(100vh - 40px));' +
    'background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 30px 80px -20px rgba(0,0,0,.5);' +
    'transform:translateY(16px);transition:transform .2s}.' + P + '-ov.on .' + P + '-box{transform:none}' +
    '.' + P + '-box iframe{width:100%;height:100%;border:0;display:block}' +
    '.' + P + '-x{position:absolute;top:10px;right:10px;width:36px;height:36px;border-radius:50%;border:0;' +
    'background:rgba(14,23,41,.07);color:#0e1729;font:22px/36px sans-serif;cursor:pointer;z-index:2}' +
    '@media (max-width:560px){.' + P + '-box{width:100vw;max-width:100vw;height:100%;border-radius:0}' +
    '.' + P + '-btn{bottom:14px;' + side + ':14px}}';
  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  var overlay = null, lastFocus = null, prevOverflow = '';
  function close() {
    if (!overlay) return;
    var ov = overlay;
    overlay = null;
    ov.classList.remove('on');
    document.documentElement.style.overflow = prevOverflow;
    setTimeout(function () { if (ov.parentNode) ov.parentNode.removeChild(ov); }, 200);
    document.removeEventListener('keydown', onKey);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function onKey(e) { if (e.key === 'Escape') close(); }
  function open() {
    if (overlay) return;
    lastFocus = document.activeElement;
    overlay = document.createElement('div');
    overlay.className = P + '-ov';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', text);
    var box = document.createElement('div');
    box.className = P + '-box';
    var x = document.createElement('button');
    x.className = P + '-x';
    x.type = 'button';
    x.setAttribute('aria-label', 'Закрыть');
    x.innerHTML = '&times;';
    x.addEventListener('click', close);
    var frame = document.createElement('iframe');
    frame.src = pageUrl + '?embed=1' + (isChat ? '&chat=1' : '') + '&from=' + encodeURIComponent(location.hostname || '');
    frame.title = text;
    frame.setAttribute('allow', 'clipboard-write');
    if (!isChat) box.appendChild(x);   // у чата своя кнопка «закрыть» в шапке окна
    box.appendChild(frame);
    overlay.appendChild(box);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    document.body.appendChild(overlay);
    prevOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    document.addEventListener('keydown', onKey);
    requestAnimationFrame(function () { if (overlay) overlay.classList.add('on'); if (!isChat) x.focus(); else frame.focus(); });
  }
  window.addEventListener('message', function (e) {
    if (e.origin !== origin || !e.data || e.data.source !== 'voksy-booking') return;
    if (e.data.type === 'close') close();
  });

  function mount() {
    if (!noButton) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = P + '-btn';
      b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
        'stroke-linejoin="round" aria-hidden="true">' + (isChat ? '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>'
          : '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>') + '</svg>';
      // обе кнопки на одном сайте: чат встаёт над кнопкой записи (тот же угол)
      if (isChat && document.querySelector('.vxb-btn')) b.style.bottom = '84px';
      if (!isChat) { var cb = document.querySelector('.vxc-btn'); if (cb) cb.style.bottom = '84px'; }
      b.appendChild(document.createTextNode(text));
      b.addEventListener('click', open);
      document.body.appendChild(b);
    }
    document.addEventListener('click', function (e) {
      var el = e.target && e.target.closest ? e.target.closest('[' + ATTR + '], a[href]') : null;
      if (!el) return;
      if (el.hasAttribute(ATTR) || (!isChat && el.tagName === 'A' && el.href.indexOf(pageUrl) === 0)) {
        e.preventDefault();
        open();
      }
    });
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
  window[NS] = { open: open, close: close, url: pageUrl + (isChat ? '?chat=1' : '') };
})();
