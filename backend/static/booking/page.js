/**
 * Страница салона (Voksy Beauty, шаг 1.5): экран «Страница салона» в /static/booking.html.
 *
 * Слева — что видит клиент на /beauty/<slug>: название, короткая фраза, «о салоне», обложка, логотип,
 * галерея (порядок, подписи), адрес и ссылка на карту, телефон, WhatsApp, Instagram, Telegram, сайт,
 * удобства, акции, фирменный цвет, показывать ли цены. Сверху — «Страница готова на N%» с тем, чего не
 * хватает, ссылка (копировать, открыть, QR), онлайн-запись вкл/выкл. Справа на компьютере — живой
 * предпросмотр страницы в рамке телефона (обновляется после сохранения). Внизу — код кнопки на сайт.
 * Тексты сохраняются одной кнопкой, фото — сразу при загрузке.
 * API: GET/PUT /api/booking/page, POST /api/booking/page/media?kind=, DELETE /api/booking/page/media/{id},
 * PUT /api/booking/settings (адрес ссылки, вкл/выкл). Общее — window.BookingPage.
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var ctx = null;          // {root, back, canEdit, onChange}
  var st = { data: null, form: null, dirty: false, saving: false };
  var FIELDS = ['title', 'tagline', 'about', 'address', 'map_url', 'phone', 'whatsapp', 'instagram', 'telegram', 'website', 'hours_note'];
  // поля чата на сайте (1.6в) — в том же st.form и той же кнопке «Сохранить», поля ввода — field()

  function load() {
    return S.api('/api/booking/page').then(function (r) {
      if (!r.ok) { ctx.root.innerHTML = '<div class="bk-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      take(r.data);
      draw();
    });
  }
  function take(d) {
    st.data = d;
    var p = d.profile || {};
    st.form = {};
    FIELDS.forEach(function (k) { st.form[k] = p[k] || ''; });
    if (st.form.instagram) st.form.instagram = '@' + st.form.instagram;
    if (st.form.telegram) st.form.telegram = '@' + st.form.telegram;
    st.form.amenities = (p.amenities || []).slice();
    st.form.promos = (p.promos || []).map(function (x) { return { title: x.title, text: x.text || '', until: x.until || '' }; });
    st.form.accent = p.accent || '#d6336c';
    st.form.show_prices = p.show_prices !== false;
    var ch = d.chat || {};
    st.form.chat_enabled = ch.enabled !== false;
    st.form.chat_title = ch.title || '';
    st.form.chat_greeting = ch.greeting || '';
    st.dirty = false;
  }
  function open(c) {
    ctx = c;
    if (!st.data) {
      ctx.root.innerHTML = '<div class="ms"><div class="skeleton" style="height:56px;border-radius:14px"></div><div class="skeleton" style="height:420px;border-radius:16px"></div></div>';
      load();
    } else { draw(); load(); }
  }

  // ── экран ──
  function field(key, label, opts) {
    opts = opts || {};
    var v = st.form[key] || '';
    var input = opts.area
      ? '<textarea class="textarea" data-f="' + key + '" rows="' + (opts.rows || 3) + '" maxlength="' + (opts.max || 500) + '" placeholder="' + esc(opts.ph || '') + '">' + esc(v) + '</textarea>'
      : '<input class="input" data-f="' + key + '" maxlength="' + (opts.max || 200) + '" placeholder="' + esc(opts.ph || '') + '" value="' + esc(v) + '"' + (opts.type ? ' type="' + opts.type + '" inputmode="' + (opts.type === 'tel' ? 'tel' : 'url') + '"' : '') + '>';
    return '<div class="field"><label class="label">' + esc(label) + (opts.max && opts.counter ? ' <span class="muted pg-cnt" data-cnt="' + key + '">' + v.length + '/' + opts.max + '</span>' : '') + '</label>' + input +
      (opts.hint ? '<div class="hint">' + opts.hint + '</div>' : '') + '</div>';
  }
  function card(id, title, sub, inner) {
    return '<section class="pg-card" id="pg-' + id + '"><div class="pg-ch"><h3>' + esc(title) + '</h3>' + (sub ? '<span>' + esc(sub) + '</span>' : '') + '</div>' + inner + '</section>';
  }
  function draw() {
    var d = st.data, p = d.profile || {}, f = st.form, page = d.page || {};
    var rd = d.readiness || { percent: 0, todo: [] };
    var head = '<div class="ms-head"><button class="btn btn-sm" type="button" data-pg-back>' + icon('arrow-left') + '<span>К журналу</span></button>' +
      '<div class="ms-title"><h2>Страница салона</h2><div class="muted small">Витрина и онлайн-запись для клиентов</div></div><div class="bk-spacer"></div>' +
      (page.page_url ? '<a class="btn btn-sm" target="_blank" rel="noopener" href="' + esc(page.page_url) + '">' + icon('external-link') + '<span>Открыть</span></a>' : '') +
      (ctx.canEdit ? '<button class="btn btn-primary btn-sm" type="button" data-pg-save' + (st.dirty ? '' : ' disabled') + '>' + icon('check') + '<span>' + (st.saving ? 'Сохраняем…' : 'Сохранить') + '</span></button>' : '') + '</div>';
    var ready = '<div class="pg-ready"><div class="pg-ring" style="--p:' + rd.percent + '"><b>' + rd.percent + '%</b></div><div class="pg-rt"><b>' +
      (rd.percent >= 100 ? 'Страница готова — делитесь ссылкой' : 'Страница готова на ' + rd.percent + '%') + '</b>' +
      (rd.todo.length ? '<div class="pg-todo">' + rd.todo.map(function (t) { return '<button type="button" class="pg-tg" data-todo="' + esc(t.key) + '">' + icon('plus') + esc(t.title) + '</button>'; }).join('') + '</div>'
        : '<div class="muted small">Поставьте ссылку в шапку Instagram, 2ГИС и автоответ WhatsApp.</div>') + '</div></div>';
    var link = '<div class="pg-link"><div class="pg-url">' + icon('link-2') + '<span id="pg-url">' + esc(page.page_url || '') + '</span></div>' +
      '<button class="btn btn-sm" type="button" data-pg-copy>' + icon('copy') + '<span>Скопировать</span></button>' +
      '<button class="btn btn-sm" type="button" data-pg-qr>' + icon('download') + '<span>QR-код</span></button>' +
      '<label class="pg-sw"><span>Онлайн-запись</span><span class="switch"><input type="checkbox" id="pg-enabled"' + (page.enabled ? ' checked' : '') + (ctx.canEdit ? '' : ' disabled') + '><span class="track"></span></span></label></div>';

    var photos = card('photos', 'Фото', 'Первое, что видит клиент',
      '<div class="pg-photos"><div class="pg-cover">' + (p.cover ? '<img src="' + esc(p.cover.url) + '" alt="">' : '<div class="pg-ph">' + icon('image') + '<span>Обложка — фото зала или лучших работ<br><small>горизонтальное, от 1200 px</small></span></div>') +
      (ctx.canEdit ? '<div class="pg-pa"><button class="btn btn-sm" type="button" data-up="cover">' + icon('upload') + '<span>' + (p.cover ? 'Заменить' : 'Загрузить') + '</span></button>' +
        (p.cover ? '<button class="btn btn-sm btn-icon" type="button" data-del="' + esc(p.cover.id) + '" aria-label="Удалить обложку">' + icon('trash-2') + '</button>' : '') + '</div>' : '') + '</div>' +
      '<div class="pg-logo"><div class="pg-lg">' + (p.logo ? '<img src="' + esc(p.logo.url) + '" alt="">' : '<span>' + esc((f.title || d.default_title || '?').trim().charAt(0).toUpperCase()) + '</span>') + '</div>' +
      '<div><b>Логотип</b><div class="muted small">Квадратный, хорошо смотрится в круге</div>' +
      (ctx.canEdit ? '<div class="pg-pa2"><button class="btn btn-sm" type="button" data-up="logo">' + icon('upload') + '<span>' + (p.logo ? 'Заменить' : 'Загрузить') + '</span></button>' +
        (p.logo ? '<button class="btn btn-sm btn-icon" type="button" data-del="' + esc(p.logo.id) + '" aria-label="Удалить логотип">' + icon('trash-2') + '</button>' : '') + '</div>' : '') + '</div></div></div>' +
      '<div class="pg-gh"><b>Галерея</b><span class="muted small">' + (p.gallery || []).length + ' из ' + ((d.limits || {}).gallery || 20) + ' · зал, интерьер, атмосфера. Работы мастеров добавляются в карточках мастеров</span></div>' +
      '<div class="pg-gal">' + (p.gallery || []).map(function (g, i) {
        return '<div class="pg-gi" data-gi="' + esc(g.id) + '"><img src="' + esc(g.url) + '" alt="" loading="lazy">' +
          (g.caption ? '<span class="pg-cap">' + esc(g.caption) + '</span>' : '') +
          (ctx.canEdit ? '<div class="pg-ga"><button type="button" data-gcap="' + esc(g.id) + '" title="Подпись" aria-label="Подпись">' + icon('pencil') + '</button>' +
            (i ? '<button type="button" data-gleft="' + esc(g.id) + '" title="Раньше" aria-label="Раньше">' + icon('chevron-left') + '</button>' : '') +
            '<button type="button" data-del="' + esc(g.id) + '" title="Удалить" aria-label="Удалить">' + icon('x') + '</button></div>' : '') + '</div>';
      }).join('') + (ctx.canEdit && (p.gallery || []).length < ((d.limits || {}).gallery || 20) ? '<button class="pg-add" type="button" data-up="gallery">' + icon('plus') + '<span>Добавить фото</span></button>' : '') + '</div>');

    var main = card('main', 'О салоне', '',
      field('title', 'Название', { ph: d.default_title || 'Салон «Айдана»', max: 120 }) +
      field('tagline', 'Короткая фраза', { ph: 'Маникюр и брови в центре Бишкека', max: 140, counter: true, hint: 'Под названием и в превью ссылки в WhatsApp и Instagram.' }) +
      field('about', 'Подробнее', { area: true, rows: 4, max: 2000, counter: true, ph: 'С какого года работаете, на каких материалах, чем гордитесь. 2–4 предложения.' }));

    var contacts = card('contacts', 'Где вы и как связаться', '',
      field('address', 'Адрес', { ph: 'г. Бишкек, ул. Киевская 95, 2 этаж', max: 300, hint: 'Уходит клиенту в подтверждении записи и напоминании.' }) +
      field('map_url', 'Ссылка на карту', { ph: 'https://2gis.kg/…', max: 500, type: 'url', hint: 'Откройте салон в 2ГИС или Google Картах → «Поделиться» → вставьте ссылку. Пусто — поищем по адресу.' }) +
      '<div class="st-grid2">' + field('phone', 'Телефон', { ph: '+996 700 123 456', max: 40, type: 'tel' }) + field('whatsapp', 'WhatsApp', { ph: '+996 700 123 456', max: 40, type: 'tel' }) + '</div>' +
      '<div class="st-grid2">' + field('instagram', 'Instagram', { ph: '@salon.aidana', max: 120 }) + field('telegram', 'Telegram', { ph: '@salon_aidana', max: 120 }) + '</div>' +
      field('website', 'Сайт', { ph: 'https://…', max: 200, type: 'url' }));

    var amen = card('amen', 'Удобства', 'Отметьте, что есть', '<div class="sv-chips pg-amen">' + (d.amenities || []).map(function (a) {
      return '<button type="button" data-amen="' + esc(a.key) + '" class="' + (f.amenities.indexOf(a.key) >= 0 ? 'on' : '') + '"' + (ctx.canEdit ? '' : ' disabled') + '>' + esc(a.title) + '</button>';
    }).join('') + '</div>');

    var promos = card('promos', 'Акции', 'Показываются наверху страницы, пока не кончились',
      '<div class="pg-promos">' + f.promos.map(function (pr, i) {
        return '<div class="pg-promo" data-pi="' + i + '"><div class="st-grid2"><div class="field"><label class="label">Акция</label><input class="input" data-pf="title" maxlength="80" value="' + esc(pr.title) + '" placeholder="−20% на первый визит"></div>' +
          '<div class="field"><label class="label">До какого числа <span class="muted">(необязательно)</span></label><input class="input" type="date" data-pf="until" value="' + esc(pr.until) + '"></div></div>' +
          '<div class="field"><label class="label">Условия</label><input class="input" data-pf="text" maxlength="300" value="' + esc(pr.text) + '" placeholder="Для новых гостей при онлайн-записи"></div>' +
          (ctx.canEdit ? '<button class="btn btn-ghost btn-sm" type="button" data-pdel="' + i + '" style="color:var(--vf-danger)">' + icon('trash-2') + '<span>Убрать акцию</span></button>' : '') + '</div>';
      }).join('') + '</div>' +
      (ctx.canEdit && f.promos.length < ((d.limits || {}).promos || 6) ? '<button class="btn btn-sm" type="button" data-padd>' + icon('plus') + '<span>Добавить акцию</span></button>' : ''));

    var hours = (d.hours || []).filter(function (h) { return h.open; });
    var look = card('look', 'Оформление', '',
      '<div class="field"><label class="label">Фирменный цвет</label><div class="pg-sws">' + (d.accents || []).map(function (c) {
        return '<button type="button" class="pg-sw-c' + (f.accent === c ? ' on' : '') + '" data-accent="' + esc(c) + '" style="--c:' + esc(c) + '" aria-label="Цвет ' + esc(c) + '"' + (ctx.canEdit ? '' : ' disabled') + '></button>';
      }).join('') + '</div></div>' +
      '<div class="st-switch-row"><div class="t"><b>Показывать цены</b><div>Выключите, если цены зависят от мастера и длины волос и вы называете их после консультации.</div></div>' +
      '<label class="switch"><input type="checkbox" id="pg-prices"' + (f.show_prices ? ' checked' : '') + (ctx.canEdit ? '' : ' disabled') + '><span class="track"></span></label></div>' +
      '<div class="field" style="margin-top:12px"><label class="label">Часы работы</label><div class="pg-hours">' +
      (hours.length ? (d.hours || []).map(function (h) { return '<span' + (h.open ? '' : ' class="off"') + '><b>' + esc(h.title) + '</b>' + esc(h.open ? h.open + '–' + h.close : 'выходной') + '</span>'; }).join('') :
        '<span class="muted">Пока нет графика мастеров</span>') + '</div>' +
      '<div class="hint">Считаются по графику мастеров (экран «Мастера»). Отдельно вводить не нужно.</div></div>' +
      field('hours_note', 'Примечание к часам', { ph: 'В праздники — по записи', max: 160 }));

    // Чат на сайте (Voksy Beauty 1.6в): окно «Спросить» на странице салона и кнопка на сайт владельца
    var ch = d.chat || {};
    var chatState = !f.chat_enabled ? '<span class="pg-chat-st off">Выключен</span>'
      : ch.live ? '<span class="pg-chat-st on">Работает — отвечает бот</span>'
      : '<span class="pg-chat-st warn">Не виден клиентам: ' + esc(ch.reason || '') + '</span>';
    var chat = card('chat', 'Чат с администратором', 'Кнопка «Спросить» на странице — отвечает ваш бот',
      '<div class="st-switch-row"><div class="t"><b>Чат на странице салона</b><div>Клиент спрашивает про цены, мастеров и свободное время прямо на странице, бот отвечает и записывает. ' +
      'Переписка — в Инбоксе (канал «Сайт»), ответить можно и самой.</div><div style="margin-top:6px">' + chatState + '</div></div>' +
      '<label class="switch"><input type="checkbox" id="pg-chat"' + (f.chat_enabled ? ' checked' : '') + (ctx.canEdit ? '' : ' disabled') + '><span class="track"></span></label></div>' +
      field('chat_title', 'Как подписан чат', { ph: 'Администратор', max: 40, hint: 'Например: «Администратор Алия». Так клиент видит шапку окна.' }) +
      field('chat_greeting', 'Первое сообщение', { area: true, rows: 2, max: 300, counter: true,
        ph: 'Здравствуйте! Подскажу по услугам, ценам и свободному времени и запишу вас. Что вас интересует?' }) +
      (d.chat_widget_code ? '<div class="field" style="margin-top:12px"><label class="label">Кнопка чата на ваш сайт</label>' +
        '<textarea class="input" id="pg-ccode" readonly rows="3" style="font:12.5px/1.4 ui-monospace,Menlo,monospace;height:auto;padding:10px">' + esc(d.chat_widget_code) + '</textarea>' +
        '<div class="hint">Вставьте перед &lt;/body&gt;. Можно вместе с кнопкой записи — чат встанет над ней.</div>' +
        '<button class="btn btn-sm" type="button" data-ccopy style="margin-top:8px">' + icon('copy') + '<span>Скопировать код</span></button></div>' : ''));

    var kit = card('kit', 'Ссылка, QR и кнопка на сайт', '',
      '<div class="pg-kit">' + (d.qr ? '<div class="pg-qr"><img src="' + esc(d.qr) + '" alt="QR-код страницы салона"><a class="btn btn-sm" download="salon-qr.svg" href="' + esc(d.qr) + '">' + icon('download') + '<span>QR страницы</span></a></div>' : '') +
      '<div class="pg-kt"><b>Где разместить</b><ul><li>Шапка профиля Instagram и кнопка «Записаться»</li><li>Карточка в 2ГИС и Google Картах</li><li>Автоответ и статус WhatsApp</li><li>QR — на ресепшн, кассу, визитки</li></ul>' +
      '<div class="muted small">Прямая запись без витрины (для бота и рассылок): <a href="' + esc(page.url || '#') + '" target="_blank" rel="noopener">' + esc(page.url || '') + '</a></div></div></div>' +
      (d.widget_code ? '<div class="field" style="margin-top:12px"><label class="label">Кнопка «Записаться онлайн» на ваш сайт</label>' +
        '<textarea class="input" id="pg-wcode" readonly rows="3" style="font:12.5px/1.4 ui-monospace,Menlo,monospace;height:auto;padding:10px">' + esc(d.widget_code) + '</textarea>' +
        '<div class="hint">Вставьте перед &lt;/body&gt; (Tilda: блок «HTML-код»; WordPress: виджет «HTML»). Запись откроется окном прямо на сайте.</div>' +
        '<button class="btn btn-sm" type="button" data-wcopy style="margin-top:8px">' + icon('copy') + '<span>Скопировать код</span></button></div>' : '') +
      '<div class="field" style="margin-top:12px"><label class="label">Адрес ссылки</label><div class="pg-slug"><span class="muted">' + esc((page.page_url || '').replace(/[^/]*$/, '')) + '</span>' +
      '<input class="input" id="pg-slug" value="' + esc(page.slug || '') + '"' + (ctx.canEdit ? '' : ' disabled') + '>' +
      (ctx.canEdit ? '<button class="btn btn-sm" type="button" data-slug>Сохранить</button>' : '') + '</div><div class="hint">Латиница, цифры и дефис, например salon-aidana. Старая ссылка перестанет работать.</div></div>');

    var preview = page.page_url ? '<aside class="pg-prev"><div class="pg-phone"><div class="pg-notch"></div><iframe id="pg-frame" title="Предпросмотр страницы" src="' + esc(previewSrc()) + '" loading="lazy"></iframe></div>' +
      '<div class="muted small" style="text-align:center;margin-top:8px">Так видит клиент на телефоне</div></aside>' : '';
    ctx.root.innerHTML = '<div class="ms pg">' + head + '<div class="pg-cols"><div class="pg-ed">' + ready + link + photos + main + contacts + promos + amen + look + chat + kit + '</div>' + preview + '</div>' +
      (ctx.canEdit ? '<div class="pg-savebar' + (st.dirty ? ' on' : '') + '" id="pg-savebar"><span>Есть несохранённые изменения</span><button class="btn btn-primary" type="button" data-pg-save>' + (st.saving ? 'Сохраняем…' : 'Сохранить') + '</button></div>' : '') + '</div>';
    bind();
  }
  function previewSrc() { var u = (st.data.page || {}).page_url || ''; try { u = new URL(u).pathname; } catch (e) { /* уже путь */ } return u + '?preview=' + Date.now(); }
  function refreshPreview() { var fr = document.getElementById('pg-frame'); if (fr) fr.src = previewSrc(); }

  function markDirty() {
    st.dirty = true;
    S.$$('[data-pg-save]', ctx.root).forEach(function (b) { b.disabled = false; });
    var bar = document.getElementById('pg-savebar'); if (bar) bar.classList.add('on');
  }
  function collect() {
    S.$$('[data-f]', ctx.root).forEach(function (el) { st.form[el.getAttribute('data-f')] = el.value; });
    S.$$('[data-pi]', ctx.root).forEach(function (row) {
      var i = +row.getAttribute('data-pi'), pr = st.form.promos[i];
      if (!pr) return;
      S.$$('[data-pf]', row).forEach(function (el) { pr[el.getAttribute('data-pf')] = el.value; });
    });
  }
  function save() {
    if (st.saving) return;
    collect();
    var f = st.form, body = {};
    FIELDS.forEach(function (k) { body[k] = f[k]; });
    body.amenities = f.amenities;
    body.promos = f.promos.filter(function (p) { return (p.title || '').trim(); }).map(function (p) { return { title: p.title, text: p.text, until: p.until || null }; });
    body.accent = f.accent;
    body.show_prices = f.show_prices;
    body.chat_enabled = f.chat_enabled;
    body.chat_title = f.chat_title;
    body.chat_greeting = f.chat_greeting;
    st.saving = true;
    S.$$('[data-pg-save]', ctx.root).forEach(function (b) { b.disabled = true; });
    return S.api('/api/booking/page', { method: 'PUT', body: body }).then(function (r) {
      st.saving = false;
      if (!r.ok) { S.toast(S.apiError(r), 'error'); S.$$('[data-pg-save]', ctx.root).forEach(function (b) { b.disabled = false; }); return; }
      take(r.data);
      S.toast('Страница сохранена', 'success');
      draw();
      if (ctx.onChange) ctx.onChange();
    });
  }
  function upload(kind) {
    var inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = kind === 'gallery';
    inp.addEventListener('change', function () {
      var files = Array.prototype.slice.call(inp.files || []);
      if (!files.length) return;
      if (st.dirty) collect();
      var err = null;
      S.toast(files.length > 1 ? 'Загружаем ' + files.length + ' фото…' : 'Загружаем фото…');
      (function next(i) {
        if (i >= files.length) {
          if (err) S.toast(err, 'error'); else S.toast(files.length > 1 ? 'Фото добавлены' : 'Фото загружено', 'success');
          draw(); refreshPreview();
          return;
        }
        S.upload('/api/booking/page/media?kind=' + kind, files[i]).then(function (r) {
          if (!r.ok) err = S.apiError(r);
          else keepForm(r.data);
          next(i + 1);
        });
      })(0);
    });
    inp.click();
  }
  // Фото сохраняются сразу; несохранённые тексты на экране не теряются
  function keepForm(d) {
    var form = st.form, dirty = st.dirty;
    st.data = d;
    if (dirty) { st.form = form; st.dirty = true; } else take(d);
  }
  function mediaDelete(id) {
    S.confirm({ title: 'Удалить фото?', message: 'Оно пропадёт со страницы салона.', confirmText: 'Удалить', danger: true }).then(function (yes) {
      if (!yes) return;
      if (st.dirty) collect();
      S.api('/api/booking/page/media/' + id, { method: 'DELETE' }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        keepForm(r.data); draw(); refreshPreview();
      });
    });
  }
  function galleryPut(list) {
    if (st.dirty) collect();
    return S.api('/api/booking/page', { method: 'PUT', body: { gallery: list } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      keepForm(r.data); draw(); refreshPreview();
    });
  }
  function gallery() { return ((st.data.profile || {}).gallery || []).map(function (g) { return { id: g.id, caption: g.caption || '' }; }); }

  function bind() {
    var root = ctx.root;
    S.$$('[data-pg-back]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        if (!st.dirty) return ctx.back();
        S.confirm({ title: 'Сохранить изменения?', message: 'Вы поменяли страницу салона.', confirmText: 'Сохранить', cancelText: 'Не сохранять' }).then(function (yes) {
          if (yes) save().then(function () { ctx.back(); }); else { st.dirty = false; take(st.data); ctx.back(); }
        });
      });
    });
    S.$$('[data-pg-save]', root).forEach(function (b) { b.addEventListener('click', save); });
    S.$$('[data-f]', root).forEach(function (el) {
      el.addEventListener('input', function () {
        markDirty();
        var c = S.$('[data-cnt="' + el.getAttribute('data-f') + '"]', root);
        if (c) c.textContent = el.value.length + '/' + el.maxLength;
      });
    });
    S.$$('[data-pf]', root).forEach(function (el) { el.addEventListener('input', markDirty); el.addEventListener('change', markDirty); });
    S.$$('[data-amen]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        var k = b.getAttribute('data-amen'), a = st.form.amenities, i = a.indexOf(k);
        if (i >= 0) a.splice(i, 1); else a.push(k);
        b.classList.toggle('on', i < 0); markDirty();
      });
    });
    S.$$('[data-accent]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        st.form.accent = b.getAttribute('data-accent');
        S.$$('[data-accent]', root).forEach(function (x) { x.classList.toggle('on', x === b); }); markDirty();
      });
    });
    var pr = S.$('#pg-prices', root);
    if (pr) pr.addEventListener('change', function () { st.form.show_prices = pr.checked; markDirty(); });
    var pc = S.$('#pg-chat', root);
    if (pc) pc.addEventListener('change', function () { st.form.chat_enabled = pc.checked; markDirty(); });
    var cc = S.$('[data-ccopy]', root);
    if (cc) cc.addEventListener('click', function () {
      var t = S.$('#pg-ccode', root);
      (navigator.clipboard ? navigator.clipboard.writeText(t.value) : Promise.reject()).then(function () { S.toast('Код скопирован', 'success'); })
        .catch(function () { t.select(); document.execCommand('copy'); S.toast('Код скопирован', 'success'); });
    });
    var padd = S.$('[data-padd]', root);
    if (padd) padd.addEventListener('click', function () { collect(); st.form.promos.push({ title: '', text: '', until: '' }); markDirty(); draw(); var t = S.$$('[data-pi] [data-pf="title"]', root).pop(); if (t) t.focus(); });
    S.$$('[data-pdel]', root).forEach(function (b) { b.addEventListener('click', function () { collect(); st.form.promos.splice(+b.getAttribute('data-pdel'), 1); markDirty(); draw(); }); });
    S.$$('[data-up]', root).forEach(function (b) { b.addEventListener('click', function () { upload(b.getAttribute('data-up')); }); });
    S.$$('[data-del]', root).forEach(function (b) { b.addEventListener('click', function () { mediaDelete(b.getAttribute('data-del')); }); });
    S.$$('[data-gleft]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        var g = gallery(), id = b.getAttribute('data-gleft'), i = g.map(function (x) { return x.id; }).indexOf(id);
        if (i > 0) { var t = g[i - 1]; g[i - 1] = g[i]; g[i] = t; galleryPut(g); }
      });
    });
    S.$$('[data-gcap]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        var g = gallery(), id = b.getAttribute('data-gcap'), it = g.filter(function (x) { return x.id === id; })[0];
        if (!it) return;
        var d = S.dialog({ title: 'Подпись к фото', size: 'sm',
          body: '<input class="input" id="pg-capv" maxlength="120" placeholder="Например: зал маникюра" value="' + esc(it.caption) + '">',
          foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>Готово</button>' });
        var inp = S.$('#pg-capv', d.el);
        function ok() { it.caption = inp.value.trim(); d.close(); galleryPut(g); }
        S.$('[data-ok]', d.el).addEventListener('click', ok);
        S.$('[data-c]', d.el).addEventListener('click', d.close);
        inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') ok(); });
        setTimeout(function () { inp.focus(); }, 60);
      });
    });
    S.$$('[data-todo]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        var k = b.getAttribute('data-todo');
        if (k === 'cover' || k === 'logo') return upload(k);
        if (k === 'gallery') return upload('gallery');
        if (k === 'master_photos') { ctx.openMasters && ctx.openMasters(); return; }
        var map = { title: 'title', tagline: 'tagline', about: 'about', address: 'address', contacts: 'phone' };
        var el = S.$('[data-f="' + (map[k] || k) + '"]', root);
        if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); setTimeout(function () { el.focus(); }, 350); }
      });
    });
    var copy = S.$('[data-pg-copy]', root);
    if (copy) copy.addEventListener('click', function () {
      var u = (st.data.page || {}).page_url || '';
      (navigator.clipboard ? navigator.clipboard.writeText(u) : Promise.reject()).then(function () { S.toast('Ссылка скопирована'); }).catch(function () { window.prompt('Скопируйте ссылку', u); });
    });
    var qr = S.$('[data-pg-qr]', root);
    if (qr) qr.addEventListener('click', function () {
      var k = S.$('#pg-kit', root); if (k) k.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    var wc = S.$('[data-wcopy]', root);
    if (wc) wc.addEventListener('click', function () {
      var t = S.$('#pg-wcode', root).value;
      (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(function () { S.toast('Код скопирован'); }).catch(function () { S.$('#pg-wcode', root).select(); });
    });
    var en = S.$('#pg-enabled', root);
    if (en) en.addEventListener('change', function () {
      S.api('/api/booking/settings', { method: 'PUT', body: { enabled: en.checked } }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); en.checked = !en.checked; return; }
        st.data.page = Object.assign({}, st.data.page, { enabled: en.checked });
        S.toast(en.checked ? 'Онлайн-запись включена' : 'Онлайн-запись выключена — страница скрыта от клиентов');
        refreshPreview();
      });
    });
    var sl = S.$('[data-slug]', root);
    if (sl) sl.addEventListener('click', function () {
      S.api('/api/booking/settings', { method: 'PUT', body: { slug: S.$('#pg-slug', root).value } }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast('Адрес ссылки сохранён'); load();
      });
    });
  }

  window.BookingPage = { open: open, isDirty: function () { return st.dirty; } };
})();
