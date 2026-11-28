/**
 * Отзывы (Voksy Beauty, шаг 2.3): /static/reviews.html.
 *
 * Оценки 4–5 появляются на странице салона сразу, 1–3 ждут решения владельца («Ждут проверки»): он связывается с
 * клиентом, отвечает от салона (ответ виден на странице под отзывом; галочкой — ещё и клиенту в мессенджер) и
 * решает, показывать ли отзыв. Сверху — рейтинг салона и мастеров (только по опубликованным), просят ли клиентов
 * оценить визит, переключатель «показывать на странице салона».
 * API: /api/beauty/reviews* (api/beauty.py → services/reviews_service.py).
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var root, esc, icon;
  var st = { ov: null, items: [], next: null, total: 0, status: '', master: '', score: '', busy: false };
  var TABS = [['pending', 'Ждут проверки'], ['published', 'На странице'], ['hidden', 'Скрытые'], ['', 'Все']];
  var STATUS_CLS = { published: 'ok', pending: 'warn', hidden: '' };
  var STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"/></svg>';

  function plural(n, a, b, c) {
    var n100 = Math.abs(n) % 100, n10 = n100 % 10;
    if (n100 > 10 && n100 < 20) return c;
    if (n10 > 1 && n10 < 5) return b;
    if (n10 === 1) return a;
    return c;
  }
  function rate(v) { return v == null ? '—' : String(v).replace('.', ','); }
  function stars(v, cls) {
    var n = Math.round(+v || 0), o = '';
    for (var i = 1; i <= 5; i++) o += '<i class="' + (i <= n ? 'on' : '') + '">' + STAR + '</i>';
    return '<span class="rw-stars ' + (cls || '') + '" role="img" aria-label="' + rate(v) + ' из 5">' + o + '</span>';
  }
  function dateText(k) {
    if (!k) return '';
    var p = String(k).slice(0, 10).split('-');
    var MON = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
    return (+p[2]) + ' ' + MON[+p[1] - 1] + ' ' + p[0];
  }

  // ── URL ────────────────────────────────────────────────────────────────
  function readUrl() {
    var q = new URLSearchParams(location.search);
    st.status = q.has('status') ? (q.get('status') || '') : null;
    st.master = q.get('master') || '';
    st.score = q.get('score') || '';
  }
  function writeUrl() {
    var q = new URLSearchParams();
    if (st.status !== null) q.set('status', st.status);
    if (st.master) q.set('master', st.master);
    if (st.score) q.set('score', st.score);
    try { history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '')); } catch (e) { /* ignore */ }
  }

  // ── данные ─────────────────────────────────────────────────────────────
  function query(offset, head) {
    var q = new URLSearchParams({ offset: offset || 0, limit: 30, head: head ? 1 : 0 });
    if (st.status) q.set('status', st.status);
    if (st.master) q.set('master', st.master);
    if (st.score) q.set('score', st.score);
    return S.api('/api/beauty/reviews?' + q);
  }
  function load(head) {
    st.busy = true;
    return query(0, head).then(function (r) {
      st.busy = false;
      if (!r.ok) {
        root.innerHTML = '<div class="st-empty">' + esc(r.status === 403 ? 'Отзывы открыты владельцу салона и управляющему.' : S.apiError(r)) + '</div>';
        S.ready();
        return false;
      }
      if (r.data.overview) st.ov = r.data.overview;
      st.items = r.data.items || []; st.next = r.data.next_offset; st.total = r.data.total;
      return true;
    });
  }
  function more(btn) {
    btn.disabled = true;
    query(st.next, false).then(function (r) {
      btn.disabled = false;
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      st.items = st.items.concat(r.data.items || []); st.next = r.data.next_offset;
      drawList();
    });
  }

  // ── шапка: рейтинг, мастера, настройки ─────────────────────────────────
  function drawHead() {
    var ov = st.ov, rt = ov.rating, max = 1;
    ['5', '4', '3', '2', '1'].forEach(function (k) { max = Math.max(max, rt.dist[k] || 0); });
    var masters = (ov.masters || []).filter(function (m) { return m.count > 0; });
    return '<div class="rw-head"><div><h2>Отзывы клиентов</h2><p class="muted">Оценки 4–5 сразу на странице салона. 1–3 сначала видите только вы: ' +
      'свяжитесь с клиентом, ответьте и решите, показывать ли отзыв.</p></div>' +
      '<label class="rw-show"><span><b>На странице салона</b><span class="muted small">' + (ov.show_on_page ? 'отзывы видны клиентам' : 'отзывы скрыты') + '</span></span>' +
      '<span class="switch"><input type="checkbox" id="rw-show"' + (ov.show_on_page ? ' checked' : '') + '><span class="track"></span></span></label></div>' +
      (ov.counts.pending && st.status === 'pending' ? '<button type="button" class="rw-alert" id="rw-jump">' + icon('circle-alert') + '<span>' +
        ov.counts.pending + ' ' + plural(ov.counts.pending, 'отзыв ждёт', 'отзыва ждут', 'отзывов ждут') + ' проверки</span>' + icon('chevron-down') + '</button>' : '') +
      (!ov.asking ? '<div class="rw-warn">' + icon('info') + '<span>Клиентов сейчас не просят оценить визит. Включите «Просить оценку визита» в правиле «Спасибо после визита» — ' +
        'клиент получит сообщение со ссылкой на звёзды. <a href="/static/automations.html">Открыть автоматизации</a></span></div>' : '') +
      '<div class="rw-top">' +
      '<div class="card rw-sum"><div class="rw-big"><b>' + rate(rt.avg) + '</b>' + stars(rt.avg) + '<span class="muted small">' +
      (rt.count ? rt.count + ' ' + plural(rt.count, 'оценка', 'оценки', 'оценок') + ' на странице' : 'пока нет оценок на странице') + '</span></div>' +
      '<div class="rw-dist">' + ['5', '4', '3', '2', '1'].map(function (k) {
        var c = rt.dist[k] || 0;
        return '<button type="button" class="rw-bar" data-score="' + k + '" title="Показать оценки ' + k + '"><span>' + k + '</span><i><em style="width:' + Math.round(c * 100 / max) + '%"></em></i><small>' + c + '</small></button>';
      }).join('') + '</div></div>' +
      '<div class="card rw-ms"><div class="rw-ms-h">Мастера</div>' + (masters.length ? masters.map(function (m) {
        return '<button type="button" class="rw-m" data-master="' + esc(m.id) + '"><span class="rw-ava"' + (m.color ? ' style="--mc:' + esc(m.color) + '"' : '') + '>' +
          (m.photo_url ? '<img src="' + esc(m.photo_url) + '" alt="">' : esc(m.initials || '?')) + '</span><span class="rw-m-n">' + esc(m.name) + '</span>' +
          '<span class="rw-m-r">' + STAR + '<b>' + rate(m.avg) + '</b><small>' + m.count + '</small></span></button>';
      }).join('') : '<p class="muted small" style="margin:6px 0 0">Рейтинг мастера появится с первым опубликованным отзывом о нём.</p>') + '</div>' +
      '</div>';
  }

  function drawFilters() {
    var c = st.ov.counts;
    var opts = '<option value="">Все мастера</option>' + (st.ov.masters || []).map(function (m) {
      return '<option value="' + esc(m.id) + '"' + (st.master === m.id ? ' selected' : '') + '>' + esc(m.name) + '</option>';
    }).join('');
    var sc = [['', 'Любая оценка'], ['5', '5 звёзд'], ['4', '4 звезды'], ['-1', '1–3 звезды']];
    return '<div class="rw-filters"><div class="st-colbar rw-tabs">' + TABS.map(function (t) {
      var n = t[0] ? c[t[0]] : c.all;
      return '<button type="button" class="st-colchip' + (st.status === t[0] ? ' on' : '') + '" data-tab="' + t[0] + '">' + esc(t[1]) +
        ' <span class="rw-n' + (t[0] === 'pending' && n ? ' hot' : '') + '">' + n + '</span></button>';
    }).join('') + '</div>' +
      '<div class="rw-sel"><select class="select-native" id="rw-master">' + opts + '</select>' +
      '<select class="select-native" id="rw-score">' + sc.map(function (x) { return '<option value="' + x[0] + '"' + (st.score === x[0] ? ' selected' : '') + '>' + x[1] + '</option>'; }).join('') + '</select></div></div>';
  }

  function item(r) {
    var who = r.client_name || r.author || 'Клиент';
    var meta = [dateText(r.date), r.master ? 'мастер ' + r.master : '', r.service].filter(Boolean).join(' · ');
    var acts = '';
    if (r.status !== 'published') acts += '<button class="btn btn-sm' + (r.status === 'pending' ? ' btn-primary' : '') + '" type="button" data-pub="' + esc(r.id) + '">' + icon('eye') + '<span>Показать на странице</span></button>';
    if (r.status !== 'hidden') acts += '<button class="btn btn-sm" type="button" data-hide="' + esc(r.id) + '">' + icon('eye-off') + '<span>' + (r.status === 'pending' ? 'Не показывать' : 'Скрыть') + '</span></button>';
    acts += '<button class="btn btn-sm" type="button" data-reply="' + esc(r.id) + '">' + icon('message-square') + '<span>' + (r.reply ? 'Изменить ответ' : 'Ответить') + '</span></button>';
    if (r.contact_id) acts += '<a class="btn btn-sm btn-ghost" href="/static/start-crm.html?contact=' + encodeURIComponent(r.contact_id) + '">' + icon('user') + '<span>Клиент</span></a>';
    return '<article class="card rw-it' + (r.status === 'pending' ? ' pend' : '') + '" data-id="' + esc(r.id) + '">' +
      '<div class="rw-it-h">' + stars(r.score, 'sm') + '<span class="st-badge ' + (STATUS_CLS[r.status] || '') + '">' + esc(r.status_title) + '</span>' +
      '<span class="muted small rw-src">' + esc(r.source_title) + '</span></div>' +
      '<div class="rw-who"><b>' + esc(who) + '</b>' + (r.client_phone ? ' <span class="muted small">' + esc(S.fmtPhone(r.client_phone)) + '</span>' : '') +
      (r.author && r.author !== who ? ' <span class="muted small">· на странице «' + esc(r.author) + '»</span>' : (!r.author ? ' <span class="muted small">· на странице без имени</span>' : '')) + '</div>' +
      '<div class="muted small">' + esc(meta) + '</div>' +
      (r.text ? '<p class="rw-text">' + esc(r.text) + '</p>' : '<p class="rw-text muted">Только оценка, без комментария</p>') +
      (r.reply ? '<div class="rw-reply"><b>Ответ салона' + (r.reply_by ? ' · ' + esc(r.reply_by) : '') + '</b><p>' + esc(r.reply) + '</p></div>' : '') +
      '<div class="rw-acts">' + acts + '</div></article>';
  }

  function drawList() {
    var box = document.getElementById('rw-list');
    if (!box) return;
    if (!st.items.length) {
      var e = st.status === 'pending' ? 'Ничего не ждёт проверки — все низкие оценки разобраны.'
        : st.ov.counts.all ? 'По этим условиям отзывов нет.' : 'Отзывов пока нет. Они появятся, когда клиенты оценят визит — по ссылке из сообщения или ответом в переписке.';
      box.innerHTML = '<div class="st-empty">' + esc(e) + '</div>';
      return;
    }
    box.innerHTML = st.items.map(item).join('') +
      (st.next != null ? '<button type="button" class="btn rw-more" id="rw-more">Показать ещё</button>' : '');
  }

  function draw() {
    root.innerHTML = drawHead() + drawFilters() + '<div class="rw-list" id="rw-list"></div>';
    drawList();
  }

  function refresh(head) {
    writeUrl();
    var list = document.getElementById('rw-list');
    if (list) list.style.opacity = '.5';
    load(head).then(function (ok) { if (ok) draw(); });
  }

  // ── действия ───────────────────────────────────────────────────────────
  function byId(id) { return st.items.filter(function (x) { return x.id === id; })[0]; }
  function act(id, what, btn) {
    btn.disabled = true;
    S.api('/api/beauty/reviews/' + id + '/' + what, { method: 'POST' }).then(function (r) {
      btn.disabled = false;
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      S.toast(what === 'publish' ? 'Отзыв на странице салона' : 'Отзыв скрыт со страницы', 'success');
      refresh(true);
    });
  }
  function replyDialog(id) {
    var r = byId(id);
    if (!r) return;
    var d = S.dialog({
      title: r.reply ? 'Ответ на отзыв' : 'Ответить на отзыв',
      body: '<div class="rw-quote">' + stars(r.score, 'sm') + (r.text ? '<p>' + esc(r.text) + '</p>' : '<p class="muted">Только оценка</p>') + '</div>' +
        '<label class="field"><span class="label">Ответ салона</span><textarea class="textarea" id="rw-rtext" rows="5" maxlength="1000" placeholder="' +
        (r.score <= 3 ? 'Извинитесь и расскажите, что исправили. Ответ увидят на странице под отзывом, если вы его покажете' : 'Поблагодарите клиента — ответ увидят на странице под отзывом') + '">' +
        esc(r.reply || '') + '</textarea></label>' +
        (r.contact_id ? '<label class="au-check rw-ck"><input type="checkbox" id="rw-send"' + (r.score <= 3 && !r.reply ? ' checked' : '') + '><span><b>Отправить ответ клиенту в мессенджер</b><br>' +
          '<span class="muted small">Уйдёт туда, где клиент писал последним</span></span></label>' : '') +
        (r.status === 'pending' ? '<label class="au-check rw-ck"><input type="checkbox" id="rw-pub"><span><b>Показать отзыв на странице салона</b><br>' +
          '<span class="muted small">Иначе отзыв останется только у вас</span></span></label>' : ''),
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>Сохранить</button>'
    });
    var $ = function (s) { return S.$(s, d.el); };
    $('[data-c]').addEventListener('click', function () { d.close(); });
    $('[data-ok]').addEventListener('click', function () {
      var btn = $('[data-ok]'); btn.disabled = true;
      var body = { text: $('#rw-rtext').value, send: !!($('#rw-send') && $('#rw-send').checked) };
      if ($('#rw-pub') && $('#rw-pub').checked) body.publish = true;
      S.api('/api/beauty/reviews/' + id + '/reply', { method: 'POST', body: body }).then(function (res) {
        btn.disabled = false;
        if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
        S.toast(res.data.queued ? 'Ответ сохранён и отправляется клиенту' : 'Ответ сохранён', 'success');
        d.close();
        refresh(true);
      });
    });
  }
  function setShow(on, input) {
    input.disabled = true;
    S.api('/api/beauty/reviews/settings', { method: 'PUT', body: { show_on_page: on } }).then(function (r) {
      input.disabled = false;
      if (!r.ok) { input.checked = !on; S.toast(S.apiError(r), 'error'); return; }
      st.ov.show_on_page = r.data.show_on_page;
      S.toast(on ? 'Отзывы видны на странице салона' : 'Отзывы скрыты со страницы салона', 'success');
      draw();
    });
  }

  function wire() {
    root.addEventListener('click', function (e) {
      var t = e.target, x;
      if ((x = t.closest('[data-tab]'))) { st.status = x.dataset.tab; return refresh(false); }
      if ((x = t.closest('[data-score]'))) { st.score = x.dataset.score; st.status = ''; return refresh(false); }
      if ((x = t.closest('.rw-m[data-master]'))) { st.master = x.dataset.master; st.status = ''; return refresh(false); }
      if ((x = t.closest('[data-pub]'))) return act(x.dataset.pub, 'publish', x);
      if ((x = t.closest('[data-hide]'))) return act(x.dataset.hide, 'hide', x);
      if ((x = t.closest('[data-reply]'))) return replyDialog(x.dataset.reply);
      if (t.closest('#rw-more')) return more(t.closest('#rw-more'));
      if (t.closest('#rw-jump')) { var f = S.$('.rw-filters', root); if (f) window.scrollTo({ top: f.getBoundingClientRect().top + window.scrollY - 70, behavior: 'smooth' }); return; }
    });
    root.addEventListener('change', function (e) {
      if (e.target.id === 'rw-master') { st.master = e.target.value; return refresh(false); }
      if (e.target.id === 'rw-score') { st.score = e.target.value; return refresh(false); }
      if (e.target.id === 'rw-show') return setShow(e.target.checked, e.target);
    });
  }

  function start() {
    root = document.getElementById('rw');
    esc = S.esc; icon = S.icon;
    if (S.initMobileMenu) S.initMobileMenu();
    readUrl();
    load(true).then(function (ok) {
      if (!ok) return;
      if (st.status === null) {      // первый заход: есть что проверить — сразу туда, иначе все
        st.status = st.ov.counts.pending ? 'pending' : '';
        if (st.status) return load(false).then(function () { draw(); wire(); S.ready(); });
      }
      draw(); wire(); S.ready();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
