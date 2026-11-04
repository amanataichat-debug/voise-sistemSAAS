/* ============================================================================
 * agent/inbox-overlay.js — Инбокс на странице агента (Profi), ветка 0411-inbox.
 *
 * Кнопка «Инбокс» в шапке (#inbox-btn) открывает /static/inbox.html?embed=1
 * панелью поверх agent.html. Панель не перезагружается: iframe создаётся один
 * раз (заранее, когда страница агента загрузилась и простаивает), дальше только
 * показывается и прячется — открыть и закрыть можно мгновенно (кнопка, Esc,
 * ✕ в панели, клавиша «I» вне полей ввода).
 *
 * Сообщения от инбокса (postMessage, только свой origin):
 *   inbox:close     — закрыть панель;
 *   inbox:unread    — {n} непрочитанных сообщений → бейдж на кнопке;
 *   inbox:open-crm  — {contact, stage} → закрыть панель и открыть клиента в CRM агента.
 * ========================================================================== */
(function () {
  'use strict';
  var wrap = null, frame = null, open = false;

  var CSS =
    '.inbox-btn{position:relative;display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 12px;border-radius:9px;border:1px solid var(--border,#e2e8f0);background:var(--surface,#fff);color:inherit;font:600 13px/1 inherit;cursor:pointer}' +
    '.inbox-btn:hover{border-color:#2a5ce8;color:#2a5ce8}' +
    '.inbox-btn .inbox-badge{min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:#dc2626;color:#fff;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}' +
    '.inbox-btn .inbox-badge[hidden]{display:none}' +
    '.inbox-overlay{position:fixed;inset:0;z-index:2000;background:rgba(15,23,42,.35);display:flex;opacity:0;pointer-events:none;transition:opacity .14s ease}' +
    '.inbox-overlay.open{opacity:1;pointer-events:auto}' +
    '.inbox-overlay .inbox-sheet{position:absolute;inset:10px;border-radius:14px;overflow:hidden;box-shadow:0 24px 64px rgba(15,23,42,.35);background:#fff;transform:translateY(8px) scale(.995);transition:transform .14s ease}' +
    '.inbox-overlay.open .inbox-sheet{transform:none}' +
    '.inbox-overlay iframe{width:100%;height:100%;border:0;display:block}' +
    'html[data-theme="dark"] .inbox-overlay .inbox-sheet{background:#0a0f1b}' +
    '@media (max-width:860px){.inbox-overlay .inbox-sheet{inset:0;border-radius:0}.inbox-btn .inbox-lbl{display:none}}';

  var cssDone = false;
  function injectCss() {
    if (cssDone) return;
    cssDone = true;
    var st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function ensure() {
    if (wrap) return;
    injectCss();
    wrap = document.createElement('div');
    wrap.className = 'inbox-overlay';
    wrap.setAttribute('aria-hidden', 'true');
    wrap.innerHTML = '<div class="inbox-sheet"><iframe title="Инбокс" src="/static/inbox.html?embed=1" allow="microphone; autoplay; clipboard-write"></iframe></div>';
    document.body.appendChild(wrap);
    frame = wrap.querySelector('iframe');
    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) hide(); });
  }

  function post(msg) { try { frame.contentWindow.postMessage(msg, location.origin); } catch (e) { /* ignore */ } }

  function show(contactId) {
    ensure();
    open = true;
    wrap.classList.add('open');
    wrap.setAttribute('aria-hidden', 'false');
    document.documentElement.style.overflow = 'hidden';
    post({ type: 'inbox:shown' });
    if (contactId) post({ type: 'inbox:open', contact: contactId });
    try { frame.focus(); } catch (e) { /* ignore */ }
  }
  function hide() {
    if (!wrap || !open) return;
    open = false;
    wrap.classList.remove('open');
    wrap.setAttribute('aria-hidden', 'true');
    document.documentElement.style.overflow = '';
    post({ type: 'inbox:hidden' });
  }
  window.VoksyInbox = { open: show, close: hide, toggle: function () { open ? hide() : show(); } };

  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || !e.data || !frame || e.source !== frame.contentWindow) return;
    var d = e.data;
    if (d.type === 'inbox:close') hide();
    if (d.type === 'inbox:unread') {
      var b = document.getElementById('inbox-badge');
      if (b) { b.textContent = d.n > 99 ? '99+' : String(d.n || ''); b.hidden = !d.n; }
    }
    if (d.type === 'inbox:open-crm') {
      hide();
      try {
        if (d.contact) {
          if (typeof window.switchTab === 'function') window.switchTab('clients');
          setTimeout(function () { if (typeof window.openContactDetailsModal === 'function') window.openContactDetailsModal(d.contact); }, 150);
        } else if (typeof window.switchTab === 'function') {
          window.switchTab(d.stage ? 'pipeline' : 'clients');
        }
      } catch (err) { /* ignore */ }
    }
  });

  document.addEventListener('keydown', function (e) {
    if (open && e.key === 'Escape') { hide(); return; }
    var t = e.target, typing = t && (t.isContentEditable || /^(input|textarea|select)$/i.test(t.tagName));
    if (!open && !typing && !e.ctrlKey && !e.metaKey && !e.altKey && (e.key === 'i' || e.key === 'ш')) {
      if (document.querySelector('.modal-overlay.active, .modal-overlay.show, .vf-backdrop')) return;
      e.preventDefault(); show();
    }
  });

  function init() {
    injectCss();
    var btn = document.getElementById('inbox-btn');
    if (btn) btn.addEventListener('click', function () { show(); });
    // Заранее, когда страница агента загрузилась: первое открытие — без ожидания,
    // а бейдж непрочитанных виден сразу
    var warm = function () { ensure(); };
    if ('requestIdleCallback' in window) requestIdleCallback(warm, { timeout: 4000 });
    else setTimeout(warm, 2500);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
