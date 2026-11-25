/**
 * Админка «Тарифы» (ветка 2511-beauty): каталог тарифов Voksy Beauty.
 *
 * Beauty-тарифы живут только в базе: здесь суперадмин меняет цену, лимит сотрудников, режим ИИ,
 * кредиты бота, текст «что входит», метку, порядок, показ салонам и продажу; заводит новые общие и
 * индивидуальные (для одного клиента) тарифы. Прежние AI Voice / Старт / Profi — только просмотр
 * (их цены в коде вместе с лендингом).
 *
 * API: GET /api/admin/plans/catalog, PATCH /api/admin/plans/catalog/{code}, POST /api/admin/plans/catalog.
 * Каждая правка пишется в журнал (admin_audit_log, action="plan").
 */
(function () {
  'use strict';

  var root = null;
  var st = { data: null, loading: false };

  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function icon(n, cls) { return window.VF ? VF.icon(n, cls) : ''; }
  function tok() { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } }
  function toast(msg, type) { if (window.VF) VF.toast(msg, { type: type || 'info' }); else alert(msg); }
  function num(v) { return Math.round(Number(v) || 0).toLocaleString('ru-RU').replace(/\s/g, ' '); }
  function som(v) { return num(v) + ' сом'; }
  // после «до» и «из»: до 21 сотрудника, до 3 сотрудников (родительный падеж)
  function staffWord(n) {
    n = Math.abs(n);
    return (n % 10 === 1 && n % 100 !== 11) ? 'сотрудника' : 'сотрудников';
  }
  function staffLabel(n) { return n ? 'до ' + n + ' ' + staffWord(n) : 'без лимита сотрудников'; }

  function call(method, path, body) {
    var h = { Authorization: 'Bearer ' + tok() };
    if (body) h['Content-Type'] = 'application/json';
    return fetch('/api/admin/plans/catalog' + (path || ''), { method: method, headers: h, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, status: r.status, data: d }; }); })
      .catch(function () { return { ok: false, status: 0, data: { detail: 'Нет связи с сервером' } }; });
  }
  function errText(res) {
    var d = (res && res.data) || {};
    var x = d.detail || d.message;
    if (Array.isArray(x)) return x.map(function (e) { return e.msg || ''; }).join('; ') || 'Проверьте поля';
    return (x && typeof x === 'object') ? (x.message || 'Ошибка') : (x || 'Не удалось сохранить');
  }

  function periodPrice(base, months) {
    var disc = (st.data && st.data.discounts && st.data.discounts[months]) || 0;
    return Math.round(base * months * (1 - disc / 100));
  }

  // ───────────────────────── список ─────────────────────────
  function load() {
    if (st.loading) return;
    st.loading = true;
    if (!st.data) root.innerHTML = '<div class="ap-skel"></div><div class="ap-skel"></div>';
    call('GET').then(function (res) {
      st.loading = false;
      if (!res.ok) { root.innerHTML = '<div class="ap-empty">' + esc(errText(res)) + '</div>'; return; }
      st.data = res.data;
      draw();
    });
  }

  function chips(p) {
    var out = [];
    if (!p.is_active) out.push('<span class="ap-chip off">' + icon('lock') + 'Не продаётся</span>');
    else if (p.for_user_id) out.push('<span class="ap-chip ind">' + icon('user') + 'Только для ' + esc(p.for_user_email || 'клиента') + '</span>');
    else if (p.is_public) out.push('<span class="ap-chip on">' + icon('eye') + 'Видят салоны</span>');
    else out.push('<span class="ap-chip hid">' + icon('eye-off') + 'Скрыт (только продление)</span>');
    if (p.trial) out.push('<span class="ap-chip trial">' + icon('sparkles') + 'Лимиты пробного периода</span>');
    if (p.agent_mode === 'full') out.push('<span class="ap-chip full">' + icon('bot') + 'Автономный агент</span>');
    return out.join('');
  }

  function card(p) {
    var feats = (p.features || []).slice(0, 4);
    return '<article class="ap-card' + (p.is_active ? '' : ' off') + '">' +
      '<div class="ap-card-top"><div class="ap-name">' + esc(p.name) + (p.badge ? '<span class="ap-badge">' + esc(p.badge) + '</span>' : '') + '</div>' +
      '<button type="button" class="btn btn-sm" data-edit="' + esc(p.code) + '">' + icon('pencil') + '<span>Изменить</span></button></div>' +
      '<div class="ap-price">' + num(p.price) + '<small>сом/мес</small></div>' +
      '<div class="ap-facts"><span>' + icon('users') + esc(staffLabel(p.staff)) + '</span>' +
      '<span>' + icon('coins') + (p.credits_month ? esc(p.credits_month.toLocaleString('ru-RU')) + ' кредитов в месяц' : 'Кредиты — отдельно') + '</span>' +
      '<span>' + icon('star') + 'Платят сейчас: <b>' + (p.subscribers || 0) + '</b></span></div>' +
      '<div class="ap-chips">' + chips(p) + '</div>' +
      (feats.length ? '<ul class="ap-feats">' + feats.map(function (f) { return '<li>' + icon('check') + esc(f) + '</li>'; }).join('') +
        ((p.features || []).length > 4 ? '<li class="more">ещё ' + ((p.features || []).length - 4) + '…</li>' : '') + '</ul>' : '') +
      '<div class="ap-code">' + esc(p.code) + (p.admin_updated_at ? ' · изменён ' + esc(new Date(p.admin_updated_at).toLocaleDateString('ru-RU')) : '') + '</div>' +
      '</article>';
  }

  function draw() {
    var d = st.data || {};
    var list = d.beauty || [];
    var common = list.filter(function (p) { return !p.for_user_id; });
    var personal = list.filter(function (p) { return p.for_user_id; });
    var disc = d.discounts || {};
    var html = '<div class="ap-wrap">' +
      '<section class="ap-hero"><div class="ap-hero-ic">' + icon('crown') + '</div>' +
      '<div class="ap-hero-main"><h2>Тарифы Voksy Beauty</h2>' +
      '<p>Их видят только салоны: салон красоты, барбершоп, маникюр, массаж и СПА, косметология. Правка действует сразу; ' +
      'у тех, кто уже оплатил, новая цена — со следующей оплаты. Скидки за период одинаковые для всех: 6 мес −' + (disc[6] || 0) + '%, год −' + (disc[12] || 0) + '%.</p></div>' +
      '<button type="button" class="btn btn-primary" id="ap-new">' + icon('plus') + '<span>Новый тариф</span></button></section>' +
      '<div class="ap-grid">' + (common.length ? common.map(card).join('') : '<div class="ap-empty">Общих тарифов пока нет</div>') + '</div>';
    html += '<h3 class="ap-h">Индивидуальные тарифы <span>видит и оплачивает только один клиент</span></h3>' +
      (personal.length ? '<div class="ap-grid">' + personal.map(card).join('') + '</div>'
        : '<div class="ap-empty small">Нет. Заведите через «Новый тариф» → «Для одного клиента», если договорились о своей цене.</div>');
    html += '<h3 class="ap-h">Прежние тарифы <span>для остальных сфер · цены в коде вместе с лендингом</span></h3>' +
      '<div class="ap-table"><table><thead><tr><th>Тариф</th><th>Цена</th><th>Платят сейчас</th></tr></thead><tbody>' +
      (d.voksy || []).map(function (p) {
        return '<tr><td><b>' + esc(p.name) + '</b><div class="muted">' + esc(p.description || '') + '</div></td><td>' + som(p.price) + '/мес</td><td>' + (p.subscribers || 0) + '</td></tr>';
      }).join('') + '</tbody></table></div></div>';
    root.innerHTML = html;
    document.getElementById('ap-new').addEventListener('click', function () { editor(null); });
    Array.prototype.forEach.call(root.querySelectorAll('[data-edit]'), function (b) {
      b.addEventListener('click', function () {
        var code = b.getAttribute('data-edit');
        editor((st.data.beauty || []).filter(function (p) { return p.code === code; })[0]);
      });
    });
  }

  // ───────────────────────── окно тарифа ─────────────────────────
  function dialog(html) {
    var bd = document.createElement('div');
    bd.className = 'vf-backdrop';
    bd.innerHTML = '<div class="vf-dialog vf-dialog-lg ap-dlg" role="dialog" aria-modal="true">' + html + '</div>';
    document.body.appendChild(bd);
    var m = VF.modal(bd, { onClose: function () { setTimeout(function () { bd.remove(); }, 260); } });
    Array.prototype.forEach.call(bd.querySelectorAll('[data-close]'), function (b) { b.addEventListener('click', function () { m.close('cancel'); }); });
    m.open();
    return { el: bd, close: function () { m.close('done'); } };
  }

  function editor(p) {
    var isNew = !p;
    p = p || { name: '', price: '', staff: 5, max_assistants: 3, credits_month: 0, agent_mode: 'reactive', badge: '', description: '',
      features: [], sort_order: '', is_public: true, is_active: true, trial: false };
    var ind = !!p.for_user_id;
    var dlg = dialog(
      '<div class="vf-dialog-head"><div><h3>' + (isNew ? 'Новый тариф Voksy Beauty' : 'Тариф: ' + esc(p.name)) + '</h3>' +
      '<div class="muted" style="font-size:13px;margin-top:4px">' + (isNew ? 'Появится в окне оплаты у салонов (или у одного клиента)' :
        (ind ? 'Индивидуальный для ' + esc(p.for_user_email || 'клиента') : 'Платят сейчас: ' + (p.subscribers || 0)) + ' · ' + esc(p.code)) + '</div></div>' +
      '<button type="button" class="btn btn-ghost btn-icon btn-sm" data-close aria-label="Закрыть">' + icon('x') + '</button></div>' +
      '<div class="vf-dialog-body"><div class="ap-edit">' +
      '<form class="ap-form" id="ap-form" autocomplete="off">' +
      '<div class="grid2">' +
      field('ap-name', 'Название', '<input class="input" id="ap-name" maxlength="50" required value="' + esc(p.name) + '" placeholder="Beauty AI">') +
      field('ap-price', 'Цена, сом в месяц', '<input class="input" id="ap-price" type="number" min="0" max="1000000" step="10" required value="' + esc(p.price) + '">', '<span id="ap-periods"></span>') +
      field('ap-staff', 'Сотрудников в тарифе', '<input class="input" id="ap-staff" type="number" min="1" max="500" value="' + esc(p.staff == null ? '' : p.staff) + '" placeholder="без лимита">',
        'Входы сотрудников и мастера в журнале. Пусто — без лимита') +
      field('ap-assist', 'Голосовых ассистентов', '<input class="input" id="ap-assist" type="number" min="0" max="100" value="' + esc(p.max_assistants) + '">') +
      field('ap-credits', 'Кредиты бота в месяц', '<input class="input" id="ap-credits" type="number" min="0" max="10000000" step="500" value="' + esc(p.credits_month || 0) + '">',
        '0 — салон покупает кредиты сам (так решено по умолчанию)') +
      field('ap-mode', 'ИИ-агент', '<select class="input" id="ap-mode"><option value="reactive"' + (p.agent_mode !== 'full' ? ' selected' : '') + '>Отвечает и записывает (как Старт)</option>' +
        '<option value="full"' + (p.agent_mode === 'full' ? ' selected' : '') + '>Автономный: сам пишет и звонит (как Profi)</option></select>') +
      field('ap-badge', 'Метка на карточке', '<input class="input" id="ap-badge" maxlength="40" value="' + esc(p.badge || '') + '" placeholder="Хит">') +
      field('ap-sort', 'Порядок', '<input class="input" id="ap-sort" type="number" min="0" max="10000" value="' + esc(p.sort_order === '' ? '' : p.sort_order) + '" placeholder="по возрастанию">') +
      '</div>' +
      field('ap-desc', 'Коротко, для кого', '<input class="input" id="ap-desc" maxlength="300" value="' + esc(p.description || '') + '" placeholder="Для салона с командой мастеров">') +
      field('ap-feats', 'Что входит — по строке на пункт', '<textarea class="input" id="ap-feats" rows="7" maxlength="2000">' + esc((p.features || []).join('\n')) + '</textarea>',
        'До 15 строк. Пишите только то, что уже работает') +
      (isNew ? field('ap-for', 'Для одного клиента (необязательно)', '<input class="input" id="ap-for" type="email" maxlength="255" placeholder="почта клиента">',
        'Тариф увидит и оплатит только этот клиент') : '') +
      '<div class="ap-toggles">' +
      (ind ? '' : toggle('ap-public', 'Показывать салонам', 'Скрытый тариф могут только продлить те, кто уже на нём', p.is_public)) +
      toggle('ap-active', 'Продаётся', 'Выключенный нельзя оплатить; кто уже оплатил — работает до конца срока', p.is_active) +
      (ind ? '' : toggle('ap-trial', 'Лимиты пробного периода', 'Салон на пробном периоде получает лимиты этого тарифа', p.trial)) +
      '</div>' +
      '<div id="ap-err" class="ap-err" hidden></div>' +
      '</form>' +
      '<aside class="ap-preview"><div class="ap-prev-k">Так увидит салон</div><div id="ap-prev"></div></aside>' +
      '</div></div>' +
      '<div class="vf-dialog-foot"><button type="button" class="btn" data-close>Отмена</button>' +
      '<button type="button" class="btn btn-primary" id="ap-save">' + (isNew ? 'Создать тариф' : 'Сохранить') + '</button></div>');
    var el = dlg.el;
    function v(id) { var x = el.querySelector('#' + id); return x ? x.value : ''; }
    function chk(id) { var x = el.querySelector('#' + id); return x ? x.checked : undefined; }
    function feats() { return v('ap-feats').split('\n').map(function (s) { return s.trim(); }).filter(Boolean); }
    function preview() {
      var price = Number(v('ap-price')) || 0;
      var staff = parseInt(v('ap-staff'), 10) || null;
      el.querySelector('#ap-periods').textContent = price ? '6 мес: ' + som(periodPrice(price, 6)) + ' · год: ' + som(periodPrice(price, 12)) : '';
      el.querySelector('#ap-prev').innerHTML =
        '<div class="ap-pv"><div class="ap-pv-top"><b>' + esc(v('ap-name') || 'Название') + '</b>' + (v('ap-badge').trim() ? '<span class="ap-badge">' + esc(v('ap-badge').trim()) + '</span>' : '') + '</div>' +
        '<div class="ap-pv-price">' + num(price) + '<small>сом/мес</small></div>' +
        '<div class="ap-pv-staff">' + icon('users') + esc(staffLabel(staff)) + '</div>' +
        (v('ap-desc').trim() ? '<div class="ap-pv-desc">' + esc(v('ap-desc').trim()) + '</div>' : '') +
        '<ul>' + feats().slice(0, 15).map(function (f) { return '<li>' + icon('check') + esc(f) + '</li>'; }).join('') + '</ul></div>';
    }
    el.querySelector('#ap-form').addEventListener('input', preview);
    preview();
    function showErr(t) { var e = el.querySelector('#ap-err'); e.textContent = t || ''; e.hidden = !t; }
    el.querySelector('#ap-save').addEventListener('click', function () {
      var body = {
        name: v('ap-name').trim(), price: v('ap-price') === '' ? null : Number(v('ap-price')),
        staff: v('ap-staff') === '' ? null : parseInt(v('ap-staff'), 10), max_assistants: parseInt(v('ap-assist') || '0', 10),
        credits_month: parseInt(v('ap-credits') || '0', 10), agent_mode: v('ap-mode'), badge: v('ap-badge').trim(),
        description: v('ap-desc').trim(), features: feats(), is_active: chk('ap-active')
      };
      if (v('ap-sort') !== '') body.sort_order = parseInt(v('ap-sort'), 10);
      if (!ind) { body.is_public = chk('ap-public'); body.trial = chk('ap-trial'); }
      if (isNew && v('ap-for').trim()) body.for_user = v('ap-for').trim();
      if (body.name.length < 2) { showErr('Название — от 2 символов'); return; }
      if (body.price === null || isNaN(body.price) || body.price < 0) { showErr('Укажите цену в сомах'); return; }
      if (body.features.length > 15) { showErr('«Что входит» — не больше 15 строк'); return; }
      var btn = el.querySelector('#ap-save');
      btn.disabled = true; showErr('');
      call(isNew ? 'POST' : 'PATCH', isNew ? '' : '/' + encodeURIComponent(p.code), body).then(function (res) {
        btn.disabled = false;
        if (!res.ok) { showErr(errText(res)); return; }
        st.data = res.data;
        dlg.close();
        draw();
        toast(isNew ? 'Тариф создан' : 'Сохранено — салоны увидят изменения сразу', 'success');
      });
    });
  }

  function field(id, label, control, hint) {
    return '<div class="field"><label class="label" for="' + id + '">' + esc(label) + '</label>' + control +
      (hint ? '<div class="hint">' + (hint.indexOf('<') === 0 ? hint : esc(hint)) + '</div>' : '') + '</div>';
  }
  function toggle(id, label, hint, on) {
    return '<label class="ap-toggle"><input type="checkbox" id="' + id + '"' + (on ? ' checked' : '') + '><span class="sw"></span>' +
      '<span><span class="t">' + esc(label) + '</span><span class="d">' + esc(hint) + '</span></span></label>';
  }

  // ───────────────────────── запуск: по первому открытию вкладки ─────────────────────────
  function init() {
    root = document.getElementById('plans-app');
    if (!root) return;
    var tab = document.querySelector('.tab[data-tab="plans"]');
    var start = function () { if (!st.data && !st.loading) load(); };
    if (tab) tab.addEventListener('click', start);
    if (location.hash === '#plans' && tab) { setTimeout(function () { tab.click(); }, 0); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
