/* «Оцените визит» (Voksy Beauty, шаг 2.3) — /r/<токен>.
   Звёзды 1–5, текст, подпись (имя или «без имени»). 4–5 — сразу на странице салона, 1–3 — сначала читает владелец.
   Отзыв можно изменить, пока салон на него не ответил. */
(function () {
  'use strict';
  var token = (location.pathname.split('/').filter(Boolean)[1] || '').toLowerCase();
  var API = '/api/public/review/' + encodeURIComponent(token);
  var app = document.getElementById('app');
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var LABELS = ['', 'Очень плохо', 'Плохо', 'Нормально', 'Хорошо', 'Отлично'];
  var S = { data: null, score: 0, text: '', name: '', anon: false, sending: false, editing: false };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function dateText(k, t) { var p = String(k || '').split('-'); return p.length === 3 ? (+p[2]) + ' ' + MON[+p[1] - 1] + (t ? ', ' + t : '') : ''; }
  var STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"/></svg>';

  function head() {
    var s = S.data.salon, v = S.data.visit, m = v.master;
    var ini = String(s.name || '?').trim().charAt(0).toUpperCase();
    return '<header class="rv-head"><div class="rv-logo">' + (s.logo_url ? '<img src="' + esc(s.logo_url) + '" alt="">' : esc(ini)) + '</div>' +
      '<div><div class="rv-salon">' + esc(s.name) + '</div><h1>Как прошёл визит?</h1></div></header>' +
      '<div class="rv-visit">' + (m ? '<div class="rv-ava"' + (m.color ? ' style="--mc:' + esc(m.color) + '"' : '') + '>' + (m.photo_url ? '<img src="' + esc(m.photo_url) + '" alt="">' : esc(m.initials || '?')) + '</div>' : '') +
      '<div><b>' + esc(v.service || 'Визит') + '</b><span>' + esc([dateText(v.date, v.time), m ? 'мастер ' + m.name : ''].filter(Boolean).join(' · ')) + '</span></div></div>';
  }

  function form() {
    var low = S.score && S.score <= 3;
    var hint = !S.score ? 'Нажмите на звезду' : LABELS[S.score];
    return '<div class="rv-card">' + head() +
      '<div class="rv-stars" role="radiogroup" aria-label="Оценка от 1 до 5">' + [1, 2, 3, 4, 5].map(function (n) {
        return '<button type="button" role="radio" aria-checked="' + (S.score === n) + '" aria-label="' + n + ' из 5 — ' + LABELS[n] + '" class="' + (n <= S.score ? 'on' : '') + '" data-score="' + n + '">' + STAR + '</button>';
      }).join('') + '</div><div class="rv-label' + (S.score ? ' set' : '') + '">' + esc(hint) + '</div>' +
      '<div class="rv-rest' + (S.score ? ' on' : '') + '">' +
      '<label class="rv-f"><span>' + (low ? 'Что было не так?' : 'Что понравилось?') + ' <i>по желанию</i></span>' +
      '<textarea id="rv-text" rows="4" maxlength="1000" placeholder="' + (low ? 'Расскажите честно — администратор прочитает и свяжется с вами' : 'Мастер, результат, атмосфера — пара слов поможет другим клиентам') + '">' + esc(S.text) + '</textarea></label>' +
      '<label class="rv-f"><span>Как подписать отзыв</span><input id="rv-name" maxlength="40" autocomplete="given-name" value="' + esc(S.name) + '"' + (S.anon ? ' disabled' : '') + ' placeholder="Ваше имя"></label>' +
      '<label class="rv-check"><input type="checkbox" id="rv-anon"' + (S.anon ? ' checked' : '') + '><span>Без имени</span></label>' +
      '<p class="rv-note">' + (low ? 'Оценку от 1 до 3 сначала прочитает салон — он свяжется с вами и решит, показывать ли её на странице.'
        : 'Отзыв появится на странице салона — его увидят другие клиенты.') + '</p>' +
      '<button type="button" class="rv-btn" data-send' + (S.sending ? ' disabled' : '') + '>' + (S.sending ? 'Отправляем…' : (S.data.review ? 'Сохранить отзыв' : 'Отправить отзыв')) + '</button>' +
      '</div></div>';
  }

  function done(status) {
    var s = S.data.salon, r = S.data.review, ok = status === 'published';
    var stars = [1, 2, 3, 4, 5].map(function (n) { return '<i class="' + (n <= r.score ? 'on' : '') + '">' + STAR + '</i>'; }).join('');
    return '<div class="rv-card">' + head() +
      '<div class="rv-done"><div class="rv-mini">' + stars + '</div>' +
      '<h2>' + (ok ? 'Спасибо за отзыв!' : 'Спасибо, что рассказали') + '</h2>' +
      '<p>' + (ok ? 'Он уже на странице салона. Будем рады видеть вас снова.'
        : status === 'hidden' ? 'Салон прочитал ваш отзыв.' : 'Салон прочитает ваш отзыв и свяжется с вами.') + '</p>' +
      (r.text ? '<blockquote>' + esc(r.text) + '</blockquote>' : '') +
      (r.reply ? '<div class="rv-reply"><b>Ответ салона</b><p>' + esc(r.reply) + '</p></div>' : '') +
      '<div class="rv-acts">' + (s.book_url ? '<a class="rv-btn" href="' + esc(s.book_url) + '">Записаться снова</a>' : '') +
      (ok && s.page_url ? '<a class="rv-btn ghost" href="' + esc(s.page_url) + '#reviews">Открыть страницу салона</a>' : '') +
      (S.data.can_edit ? '<button type="button" class="rv-link" data-edit>Изменить отзыв</button>' : '') + '</div></div></div>';
  }

  function draw() {
    if (S.data.review && !S.editing) app.innerHTML = done(S.data.review.status);
    else app.innerHTML = form();
  }

  function fail(msg) {
    app.innerHTML = '<div class="rv-card rv-err"><h1>Не получилось открыть</h1><p>' + esc(msg || 'Попробуйте позже.') + '</p>' +
      '<div class="rv-pw">Работает на <a href="https://voksyai.online">Voksy AI</a></div></div>';
  }

  function send() {
    if (!S.score || S.sending) return;
    S.sending = true; draw();
    fetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ score: S.score, text: S.text, name: S.name, anonymous: S.anon }) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) { var m = d.message || d.detail; throw new Error(typeof m === 'string' ? m : 'Не получилось отправить'); } return d; }); })
      .then(function (d) {
        S.sending = false; S.editing = false;
        S.data.review = { score: S.score, text: S.text.trim(), author_name: S.anon ? '' : S.name, status: d.status, reply: '' };
        draw();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      })
      .catch(function (e) {
        S.sending = false; draw();
        var n = document.querySelector('.rv-note'); if (n) { n.textContent = e.message; n.classList.add('bad'); }
      });
  }

  app.addEventListener('click', function (e) {
    var st = e.target.closest('[data-score]');
    if (st) { S.score = +st.getAttribute('data-score'); draw(); var t = document.getElementById('rv-text'); if (t && window.matchMedia('(min-width: 761px)').matches) t.focus(); return; }
    if (e.target.closest('[data-send]')) return send();
    if (e.target.closest('[data-edit]')) {
      var r = S.data.review; S.editing = true; S.score = r.score; S.text = r.text || ''; S.anon = !r.author_name; S.name = r.author_name || S.data.suggest_name || '';
      return draw();
    }
  });
  app.addEventListener('input', function (e) {
    if (e.target.id === 'rv-text') S.text = e.target.value;
    if (e.target.id === 'rv-name') S.name = e.target.value;
  });
  app.addEventListener('change', function (e) {
    if (e.target.id === 'rv-anon') { S.anon = e.target.checked; var n = document.getElementById('rv-name'); if (n) n.disabled = S.anon; }
  });

  fetch(API, { cache: 'no-store' }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) { var m = d.message || d.detail; throw new Error(typeof m === 'string' ? m : ''); } return d; });
  }).then(function (d) {
    S.data = d; S.name = d.suggest_name || '';
    document.documentElement.style.setProperty('--a', d.salon.accent || '#d6336c');
    draw();
  }).catch(function (e) { fail(e.message); });
})();
