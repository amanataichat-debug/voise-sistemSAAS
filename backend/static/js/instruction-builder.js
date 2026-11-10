/*
 * Окно «Сгенерировать инструкцию» и «Добавить правку» — общее для кабинетов Старта (start/bot.js) и Profi
 * (agent.html), 10.11.2026.
 *
 * Владелец: «клиент пишет своими словами, система пишет готовый промпт под нашу платформу и его продажи;
 * клиент смотрит и жмёт «Готово». И отдельно «Добавить инструкцию»: что убрать или добавить — правка уходит
 * в основной промпт, чтобы не копаться в целом тексте».
 *
 * Сервер: POST /api/agent/instructions/generate {kind, brief} → {text, kb_facts};
 *         POST /api/agent/instructions/refine   {kind, change, current} → {text, changes}.
 * Сервер НИЧЕГО не сохраняет — сохраняет страница в onApply(text) обычным путём.
 *
 * VoksyInstructionBuilder.open({
 *   mode: 'generate' | 'refine', kind: 'chat' | 'voice',
 *   request: function (path, body) → Promise<{ok, data}>,   // своя обёртка страницы над API агента
 *   getCurrent: function () → строка из поля на странице,
 *   onApply: function (text) → Promise<boolean>              // вставить в поле и сохранить
 * })
 * Без зависимостей: свои стили с префиксом .vib-, работает в любом каркасе кабинета.
 */
(function () {
  var KIND_TITLE = { chat: 'переписки', voice: 'звонков' };
  var BRIEF_HINT = {
    chat: 'Расскажите своими словами, как новому сотруднику: чем занимаетесь, что продаёте, кто клиенты, ' +
      'с чего обычно начинается разговор, что нужно узнать у клиента, что значит «оформить» (запись, заказ, ' +
      'оплата), когда звать вас и чего агенту нельзя. Цены и адреса положите в базу знаний — агент возьмёт их сам.',
    voice: 'Расскажите своими словами: кто звонит и кому звоним, зачем звонок, какой первый вопрос задать, ' +
      'что предложить, что значит «оформить» (запись на время, заказ, выезд), какие сомнения бывают у клиентов, ' +
      'чего агенту нельзя говорить. Цены и адреса положите в базу знаний.'
  };
  var BRIEF_PLACEHOLDER = {
    chat: 'Например: Продаём автозапчасти для японских и корейских авто, Бишкек, склад на Джибек-Жолу. Клиенты ' +
      'пишут из Instagram, спрашивают цену на запчасть. Надо узнать марку, модель и год, VIN если есть, подобрать ' +
      'из каталога, скинуть фото и цену. Оформление: имя, телефон, самовывоз или доставка. Скидки обещать нельзя, ' +
      'оригинал или аналог всегда уточнять. Зовите меня, если запчасти нет в каталоге или заказ больше 50 000.',
    voice: 'Например: Нам звонят по объявлениям, спрашивают, есть ли запчасть и сколько стоит. Агент узнаёт марку, ' +
      'модель и год, говорит цену из каталога, предлагает оригинал или аналог и записывает заказ: имя, самовывоз ' +
      'или доставка. Если детали нет — обещает перезвонить сегодня. Скидки не обещать.'
  };
  var CHANGE_PLACEHOLDER = 'Например: «добавь этап Замер перед Успехом», «первым вопросом спрашивай марку и год авто», ' +
    '«не предлагай скидки», «убери про доставку — её больше нет»';

  var css = '' +
    '.vib-back{position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:10050;display:flex;align-items:center;justify-content:center;padding:16px}' +
    '.vib{background:#fff;color:#0f172a;border-radius:16px;box-shadow:0 20px 60px rgba(15,23,42,.25);width:min(860px,100%);max-height:calc(100vh - 32px);display:flex;flex-direction:column;font:14px/1.5 Inter,system-ui,sans-serif}' +
    '.vib-h{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 20px 10px}' +
    '.vib-h h3{margin:0;font-size:17px;font-weight:650}' +
    '.vib-x{border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer;color:#64748b;padding:4px 8px;border-radius:8px}' +
    '.vib-x:hover{background:#f1f5f9}' +
    '.vib-b{padding:0 20px 8px;overflow:auto}' +
    '.vib-p{margin:0 0 10px;color:#475569}' +
    '.vib-ta{width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:10px;padding:10px 12px;font:inherit;resize:vertical;min-height:120px;color:#0f172a;background:#fff}' +
    '.vib-ta:focus{outline:2px solid #2a5ce8;outline-offset:0;border-color:#2a5ce8}' +
    '.vib-ta.big{min-height:340px;font-size:13px}' +
    '.vib-f{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;padding:12px 20px 18px;border-top:1px solid #e2e8f0}' +
    '.vib-btn{border:1px solid #cbd5e1;background:#fff;color:#0f172a;border-radius:10px;padding:9px 14px;font:inherit;font-weight:550;cursor:pointer}' +
    '.vib-btn:hover{background:#f8fafc}' +
    '.vib-btn.pri{background:#2a5ce8;border-color:#2a5ce8;color:#fff}' +
    '.vib-btn.pri:hover{background:#2350d0}' +
    '.vib-btn[disabled]{opacity:.55;cursor:default}' +
    '.vib-note{background:#f1f5ff;border:1px solid #d6e0fb;border-radius:10px;padding:10px 12px;margin:10px 0;color:#1e3a8a}' +
    '.vib-note b{display:block;margin-bottom:4px}' +
    '.vib-note ul{margin:4px 0 0;padding-left:18px}' +
    '.vib-err{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;border-radius:10px;padding:10px 12px;margin:10px 0}' +
    '.vib-load{display:flex;align-items:center;gap:10px;color:#475569;margin:12px 0}' +
    '.vib-spin{width:18px;height:18px;border:2px solid #cbd5e1;border-top-color:#2a5ce8;border-radius:50%;animation:vibspin .8s linear infinite}' +
    '@keyframes vibspin{to{transform:rotate(360deg)}}' +
    '.vib-diff{border:1px solid #e2e8f0;border-radius:10px;padding:8px 10px;margin:10px 0;max-height:180px;overflow:auto;font-size:13px}' +
    '.vib-diff div{white-space:pre-wrap;padding:1px 4px;border-radius:4px}' +
    '.vib-add{background:#ecfdf5;color:#065f46}.vib-del{background:#fef2f2;color:#991b1b;text-decoration:line-through}' +
    '.vib-meta{color:#64748b;font-size:12px;margin-top:4px}';

  function ensureCss() {
    if (document.getElementById('vib-css')) return;
    var st = document.createElement('style');
    st.id = 'vib-css';
    st.textContent = css;
    document.head.appendChild(st);
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function errText(data) {
    var d = data && (data.detail || data.message);
    if (!d) return 'Не получилось — попробуйте ещё раз';
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return 'Проверьте текст: он слишком короткий или слишком длинный';
    return d.message || d.detail || 'Не получилось — попробуйте ещё раз';
  }

  // Какие строки добавлены и какие убраны (порядок не важен — владельцу нужно увидеть суть правки)
  function lineDiff(before, after) {
    var norm = function (s) { return s.trim(); };
    var a = before.split('\n').map(norm).filter(Boolean);
    var b = after.split('\n').map(norm).filter(Boolean);
    var inA = {}, inB = {};
    a.forEach(function (l) { inA[l] = true; });
    b.forEach(function (l) { inB[l] = true; });
    return {
      removed: a.filter(function (l) { return !inB[l]; }),
      added: b.filter(function (l) { return !inA[l]; })
    };
  }

  function open(opts) {
    ensureCss();
    var kind = opts.kind === 'voice' ? 'voice' : 'chat';
    var mode = opts.mode === 'refine' ? 'refine' : 'generate';
    var back = document.createElement('div');
    back.className = 'vib-back';
    back.innerHTML = '<div class="vib" role="dialog" aria-modal="true"><div class="vib-h"><h3></h3>' +
      '<button class="vib-x" type="button" aria-label="Закрыть">×</button></div><div class="vib-b"></div><div class="vib-f"></div></div>';
    document.body.appendChild(back);
    var title = back.querySelector('h3'), body = back.querySelector('.vib-b'), foot = back.querySelector('.vib-f');
    var busy = false;
    var before = (opts.getCurrent && opts.getCurrent()) || '';

    function close() {
      if (busy) return;
      document.removeEventListener('keydown', onKey);
      back.remove();
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    back.querySelector('.vib-x').addEventListener('click', close);
    back.addEventListener('mousedown', function (e) { if (e.target === back) close(); });

    function buttons(list) {
      foot.innerHTML = '';
      list.forEach(function (b) {
        var el = document.createElement('button');
        el.type = 'button';
        el.className = 'vib-btn' + (b.primary ? ' pri' : '');
        el.textContent = b.label;
        el.addEventListener('click', b.onClick);
        foot.appendChild(el);
      });
    }
    function loading(text) {
      busy = true;
      foot.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
      var l = document.createElement('div');
      l.className = 'vib-load';
      l.innerHTML = '<span class="vib-spin"></span><span>' + esc(text) + '</span>';
      body.appendChild(l);
      return function () { busy = false; l.remove(); foot.querySelectorAll('button').forEach(function (b) { b.disabled = false; }); };
    }
    function showErr(text) {
      var old = body.querySelector('.vib-err');
      if (old) old.remove();
      var e = document.createElement('div');
      e.className = 'vib-err';
      e.textContent = text;
      body.appendChild(e);
    }

    // ── шаг 1: ввод ──
    function askStep(prefill) {
      if (mode === 'generate') {
        title.textContent = 'Сгенерировать инструкцию для ' + KIND_TITLE[kind];
        body.innerHTML = '<p class="vib-p">' + esc(BRIEF_HINT[kind]) + '</p>' +
          '<textarea class="vib-ta" id="vib-in" rows="9" placeholder="' + esc(BRIEF_PLACEHOLDER[kind]) + '"></textarea>' +
          '<div class="vib-meta">Агент учтёт этапы вашей воронки и то, что уже лежит в базе знаний. Текущую инструкцию ' +
          'вы не потеряете, пока не нажмёте «Готово».</div>';
      } else {
        title.textContent = 'Добавить правку в инструкцию ' + KIND_TITLE[kind];
        body.innerHTML = '<p class="vib-p">Напишите, что поменять — добавить, убрать или изменить. Правка встанет в нужное ' +
          'место инструкции, остальной текст останется как был.</p>' +
          '<textarea class="vib-ta" id="vib-in" rows="5" placeholder="' + esc(CHANGE_PLACEHOLDER) + '"></textarea>';
      }
      var input = body.querySelector('#vib-in');
      if (prefill) input.value = prefill;
      setTimeout(function () { input.focus(); }, 30);
      buttons([
        { label: 'Отмена', onClick: close },
        { label: mode === 'generate' ? 'Сгенерировать' : 'Внести правку', primary: true, onClick: function () { run(input.value); } }
      ]);
    }

    function run(text) {
      text = (text || '').trim();
      if (mode === 'generate' && text.length < 20) { showErr('Опишите подробнее — хотя бы пару предложений о бизнесе и продажах'); return; }
      if (mode === 'refine' && text.length < 3) { showErr('Напишите, что поменять'); return; }
      var done = loading(mode === 'generate' ? 'Пишу инструкцию под ваш бизнес… до минуты' : 'Вношу правку…');
      var path = mode === 'generate' ? '/instructions/generate' : '/instructions/refine';
      var payload = mode === 'generate' ? { kind: kind, brief: text } : { kind: kind, change: text, current: before };
      Promise.resolve(opts.request(path, payload)).then(function (r) {
        done();
        if (!r || !r.ok) { showErr(errText(r && r.data)); return; }
        resultStep(r.data, text);
      }, function () { done(); showErr('Нет связи с сервером'); });
    }

    // ── шаг 2: результат ──
    function resultStep(data, asked) {
      title.textContent = mode === 'generate' ? 'Готовая инструкция для ' + KIND_TITLE[kind] : 'Инструкция с правкой';
      var html = '<p class="vib-p">Проверьте и при желании поправьте текст прямо здесь. «Готово» заменит инструкцию и сохранит её — ' +
        'агент начнёт работать по ней со следующего сообщения или звонка.</p>';
      if (mode === 'refine') {
        var d = lineDiff(before, data.text || '');
        if (data.changes && data.changes.length) {
          html += '<div class="vib-note"><b>Что изменено</b><ul>' + data.changes.map(function (c) { return '<li>' + esc(c) + '</li>'; }).join('') + '</ul></div>';
        }
        if (!data.changed || (!d.added.length && !d.removed.length)) {
          html += '<div class="vib-note">Текст не изменился. Возможно, это факт для базы знаний или правка уже есть в инструкции.</div>';
        } else {
          html += '<div class="vib-diff">' + d.removed.map(function (l) { return '<div class="vib-del">' + esc(l) + '</div>'; }).join('') +
            d.added.map(function (l) { return '<div class="vib-add">' + esc(l) + '</div>'; }).join('') + '</div>';
        }
      }
      html += '<textarea class="vib-ta big" id="vib-out"></textarea><div class="vib-meta" id="vib-len"></div>';
      if (data.kb_facts && data.kb_facts.length) {
        html += '<div class="vib-note"><b>Это лучше добавить в базу знаний (вопрос — ответ), а не в инструкцию:</b><ul>' +
          data.kb_facts.map(function (f) { return '<li>' + esc(f) + '</li>'; }).join('') + '</ul></div>';
      }
      body.innerHTML = html;
      var out = body.querySelector('#vib-out'), len = body.querySelector('#vib-len');
      out.value = data.text || '';
      var upd = function () { len.textContent = out.value.length + ' символов'; };
      out.addEventListener('input', upd);
      upd();
      buttons([
        { label: 'Отмена', onClick: close },
        { label: mode === 'generate' ? 'Сгенерировать заново' : 'Другая правка', onClick: function () { askStep(asked); } },
        { label: 'Готово, поставить', primary: true, onClick: function () { apply(out.value); } }
      ]);
    }

    function apply(text) {
      text = (text || '').trim();
      if (!text) { showErr('Инструкция пустая'); return; }
      var done = loading('Сохраняю…');
      Promise.resolve(opts.onApply(text)).then(function (ok) {
        done();
        if (ok === false) { showErr('Не сохранилось — попробуйте ещё раз'); return; }
        busy = false;
        close();
      }, function () { done(); showErr('Не сохранилось — попробуйте ещё раз'); });
    }

    askStep('');
  }

  window.VoksyInstructionBuilder = { open: open, lineDiff: lineDiff };
})();
