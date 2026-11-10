/*
 * «Проверить базу знаний» — окно для кабинетов Старта (start/bot.js) и Profi (agent-knowledge.html), 10.11.2026.
 *
 * Сервер: GET /api/agent/knowledge/health → {status, counts, checks:[{level, area, text, items}], searches, seconds}
 * (backend/services/knowledge_health.py). Проверка находит ли агент каждую запись по её вопросу тем же путём, что
 * на звонке, что не влезло в «наизусть», файлы без текста, каталог без цены и фото, поиск по смыслу, инструкции.
 *
 * VoksyKnowledgeHealth.open({ request: function () → Promise<{ok, data}> })
 * Без зависимостей: свои стили с префиксом .vkh-.
 */
(function () {
  var css = '' +
    '.vkh-back{position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:10050;display:flex;align-items:center;justify-content:center;padding:16px}' +
    '.vkh{background:#fff;color:#0f172a;border-radius:16px;box-shadow:0 20px 60px rgba(15,23,42,.25);width:min(760px,100%);max-height:calc(100vh - 32px);display:flex;flex-direction:column;font:14px/1.5 Inter,system-ui,sans-serif}' +
    '.vkh-h{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 20px 8px}' +
    '.vkh-h h3{margin:0;font-size:17px;font-weight:650}' +
    '.vkh-x{border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer;color:#64748b;padding:4px 8px;border-radius:8px}' +
    '.vkh-x:hover{background:#f1f5f9}' +
    '.vkh-b{padding:4px 20px 18px;overflow:auto}' +
    '.vkh-sum{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:12px;margin:6px 0 12px;font-weight:600}' +
    '.vkh-sum.ok{background:#ecfdf5;color:#065f46}.vkh-sum.warn{background:#fffbeb;color:#92400e}.vkh-sum.fail{background:#fef2f2;color:#991b1b}' +
    '.vkh-row{display:flex;gap:10px;padding:10px 0;border-top:1px solid #eef2f7}' +
    '.vkh-dot{flex:0 0 22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#fff;margin-top:1px}' +
    '.vkh-dot.ok{background:#10b981}.vkh-dot.warn{background:#f59e0b}.vkh-dot.fail{background:#ef4444}' +
    '.vkh-area{font-size:12px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.02em}' +
    '.vkh-items{margin:6px 0 0;padding-left:18px;color:#475569;font-size:13px}' +
    '.vkh-sub{margin:16px 0 6px;font-weight:650}' +
    '.vkh-s{display:flex;justify-content:space-between;gap:10px;font-size:13px;padding:5px 0;border-top:1px solid #f1f5f9}' +
    '.vkh-s .q{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
    '.vkh-s .m{flex:0 0 auto;color:#64748b}.vkh-s.no .q{color:#991b1b}' +
    '.vkh-load{display:flex;align-items:center;gap:10px;color:#475569;margin:14px 0}' +
    '.vkh-spin{width:18px;height:18px;border:2px solid #cbd5e1;border-top-color:#2a5ce8;border-radius:50%;animation:vkhspin .8s linear infinite}' +
    '@keyframes vkhspin{to{transform:rotate(360deg)}}' +
    '.vkh-f{display:flex;justify-content:flex-end;gap:8px;padding:12px 20px 18px;border-top:1px solid #e2e8f0}' +
    '.vkh-btn{border:1px solid #cbd5e1;background:#fff;color:#0f172a;border-radius:10px;padding:9px 14px;font:inherit;font-weight:550;cursor:pointer}' +
    '.vkh-btn.pri{background:#2a5ce8;border-color:#2a5ce8;color:#fff}.vkh-btn[disabled]{opacity:.55;cursor:default}';
  var KIND = { qa: 'вопрос', section: 'о компании', product: 'каталог' };
  var MARK = { ok: '✓', warn: '!', fail: '×' };
  var SUMMARY = {
    ok: 'Всё в порядке: агент находит ответы из базы знаний',
    warn: 'Работает, но есть что улучшить',
    fail: 'Есть проблемы: часть знаний агент не найдёт'
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function ensureCss() {
    if (document.getElementById('vkh-css')) return;
    var st = document.createElement('style');
    st.id = 'vkh-css';
    st.textContent = css;
    document.head.appendChild(st);
  }
  function errText(data) {
    var d = data && (data.detail || data.message);
    return typeof d === 'string' ? d : 'Проверка не удалась — попробуйте ещё раз';
  }

  function render(data) {
    var status = data.status || (data.ok ? 'ok' : 'fail');
    var html = '<div class="vkh-sum ' + status + '">' + esc(SUMMARY[status] || '') +
      (data.seconds != null ? '<span style="margin-left:auto;font-weight:500;opacity:.8">' + esc(data.seconds) + ' с</span>' : '') + '</div>';
    var order = { fail: 0, warn: 1, ok: 2 };
    (data.checks || []).slice().sort(function (a, b) { return order[a.level] - order[b.level]; }).forEach(function (c) {
      html += '<div class="vkh-row"><div class="vkh-dot ' + c.level + '">' + MARK[c.level] + '</div><div style="min-width:0">' +
        '<div class="vkh-area">' + esc(c.area) + '</div><div>' + esc(c.text) + '</div>' +
        (c.items && c.items.length ? '<ul class="vkh-items">' + c.items.map(function (i) { return '<li>' + esc(i) + '</li>'; }).join('') + '</ul>' : '') +
        '</div></div>';
    });
    var s = data.searches || [];
    if (s.length) {
      html += '<div class="vkh-sub">Как агент находит записи по их вопросам (' + s.length + ')</div>';
      html += s.map(function (x) {
        return '<div class="vkh-s' + (x.found ? '' : ' no') + '"><span class="q">' + (x.found ? '✓ ' : '× ') + esc(x.q) +
          ' <span style="color:#94a3b8">· ' + esc(KIND[x.kind] || x.kind) + '</span></span><span class="m">' +
          (x.found ? esc(x.ms) + ' мс' : 'не нашёл') + '</span></div>';
      }).join('');
    }
    return html;
  }

  function open(opts) {
    ensureCss();
    var back = document.createElement('div');
    back.className = 'vkh-back';
    back.innerHTML = '<div class="vkh" role="dialog" aria-modal="true"><div class="vkh-h"><h3>Проверка базы знаний</h3>' +
      '<button class="vkh-x" type="button" aria-label="Закрыть">×</button></div><div class="vkh-b"></div>' +
      '<div class="vkh-f"><button class="vkh-btn" type="button" data-a="again">Проверить ещё раз</button>' +
      '<button class="vkh-btn pri" type="button" data-a="close">Готово</button></div></div>';
    document.body.appendChild(back);
    var body = back.querySelector('.vkh-b');
    var again = back.querySelector('[data-a="again"]');
    function close() { document.removeEventListener('keydown', onKey); back.remove(); }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    back.querySelector('.vkh-x').addEventListener('click', close);
    back.querySelector('[data-a="close"]').addEventListener('click', close);
    back.addEventListener('mousedown', function (e) { if (e.target === back) close(); });
    function run() {
      again.disabled = true;
      body.innerHTML = '<div class="vkh-load"><span class="vkh-spin"></span><span>Проверяю: ищу каждую запись так же, ' +
        'как агент на звонке… до 10 секунд</span></div>';
      Promise.resolve(opts.request()).then(function (r) {
        again.disabled = false;
        body.innerHTML = r && r.ok ? render(r.data || {}) :
          '<div class="vkh-sum fail">' + esc(errText(r && r.data)) + '</div>';
      }, function () {
        again.disabled = false;
        body.innerHTML = '<div class="vkh-sum fail">Нет связи с сервером</div>';
      });
    }
    again.addEventListener('click', run);
    run();
  }

  window.VoksyKnowledgeHealth = { open: open, render: render };
})();
