/* ============================================================================
 * start/catalog-page.js — каталог отдельной страницей /static/catalog.html (ветка 1811-niche-admin-speed).
 * Нужна сотрудникам с разделом «Каталог»: страница бота (start-bot.html) и «Знания агента» — владельческие,
 * сотруднику туда нельзя. Код каталога тот же — общий модуль start/catalog.js (StartCatalog), данные —
 * /api/agent/knowledge?kind=product и файлы библиотеки. Владелец тоже может открыть страницу по ссылке.
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp, CAT = window.StartCatalog;
  var kb = { entries: [], collections: [], storage: true, files: [] };

  function staff() { return S.staff(); }

  function load() {
    var box = document.getElementById('cat-box');
    return Promise.all([S.api('/knowledge?kind=product'), S.api('/files?source=library')]).then(function (res) {
      if (window.VF && VF.ready) VF.ready();
      if (!res[0].ok) { box.innerHTML = '<div class="st-empty">' + S.esc(S.apiError(res[0])) + '</div>'; return; }
      kb.entries = res[0].data.entries || [];
      kb.collections = res[0].data.collections || [];
      kb.files = res[1].ok ? (res[1].data.files || []) : [];
      kb.storage = res[1].ok ? res[1].data.storage_configured !== false : true;
      CAT.draw(box);
    });
  }

  function start() {
    var box = document.getElementById('cat-box');
    try {
      var biz = JSON.parse(localStorage.getItem('vf_business') || 'null');
      var title = biz && biz.catalog && biz.catalog.title;
      if (title) { document.getElementById('cat-title').textContent = title; document.title = title + ' | Voksy AI'; }
    } catch (e) { /* ignore */ }
    S.boot({ allowFull: true }).then(function (ok) {
      if (!ok) return;
      if (!S.agent) {
        if (window.VF && VF.ready) VF.ready();
        box.innerHTML = '<div class="st-empty">' + (staff() ? 'Владелец ещё не создал бота — каталог появится вместе с ним.'
          : 'Сначала создайте бота — каталог принадлежит ему.') + '</div>';
        return;
      }
      var href = staff() ? false : (S.mode === 'full' ? '/static/agent.html?settings=sheet' : '/static/start-bot.html#s-sheet');
      CAT.init({ kb: kb, reload: load, sheetHref: href });
      load();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
