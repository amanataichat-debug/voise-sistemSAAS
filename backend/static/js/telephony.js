/**
 * VoksiAI — страница «Телефония» (/static/telephony.html), собственный SIP-шлюз.
 *
 *   GET   /api/sip/numbers              номера пользователя с привязкой
 *   PATCH /api/sip/numbers/{id}         привязка: {assistant_type, assistant_id} | {agent_config_id}
 *                                       | отвязка {assistant_type: null, assistant_id: null}
 *   POST  /api/sip/calls                исходящий звонок {to, caller_id, assistant_type, assistant_id}
 *   GET   /api/sip/calls[?limit], /{id} журнал и статус звонка; POST /{id}/hangup — отбой
 *   GET   /api/eleven-assistants, /api/agent/list — кого можно привязать
 *   GET   /api/sip/requests/my          заявки на подключение номера; POST /api/sip/requests — новая,
 *   POST  /api/sip/requests/check-number проверка своего номера O!; POST /{id}/cancel — отозвать
 *
 * Пока номеров нет, первым идёт шаг «Подключение номера»: ФИО, контактный телефон и
 * «новый номер» или «свой номер O!» (номер проверяется сразу: формат, префикс O!,
 * не подключён ли уже). Номер выдаёт администратор (вкладка «Заявки на номера» в админке).
 *
 * В кабинете остались только ассистенты ElevenLabs; номер, уже привязанный к
 * ассистенту другого провайдера, продолжает работать и показывается как есть.
 * Строки — раздел sip словарей /static/i18n/{ru,ky}.json.
 */
(function () {
  'use strict';

  var STR = {
    page_title: 'Телефония', refresh: 'Обновить',
    numbers_title: 'Мои номера', numbers_sub: 'Входящий звонок на номер отвечает привязанный ассистент или агент обзвона.',
    th_number: 'Номер', th_answers: 'Отвечает на входящие', th_outbound: 'Исходящие', th_status: 'Статус',
    no_numbers: 'Номеров пока нет', no_numbers_text: 'Номер оператора выдаёт администратор VoksiAI. Напишите в поддержку, и он появится здесь.',
    numbers_failed: 'Не удалось загрузить номера',
    agent_label: 'Агент обзвона', agent_deleted: 'Агент удалён', agent_voice_missing: 'голосовой ассистент агента не найден',
    assistant_missing: 'Ассистент не найден', rebind: 'Привяжите заново', inactive: 'неактивен', not_bound: 'Не привязан',
    allowed: 'разрешены', disabled: 'выключены', active: 'активен', off: 'отключён',
    bind: 'Привязать', call: 'Позвонить', unbind: 'Отвязать',
    call_title: 'Исходящий звонок', call_sub: 'Ассистент позвонит абоненту с вашего номера. Первая фраза и промпт берутся из настроек ассистента.',
    call_from: 'С номера', call_to: 'Номер абонента O!', call_who: 'Кто звонит', call_who_hint: 'По умолчанию тот, кто привязан к номеру',
    call_btn: 'Позвонить', no_outbound_number: 'Нет номера с исходящими', no_assistants: 'Нет ассистентов',
    to_hint: 'Только номера O!: префиксы 050, 070, 099', to_not_o: 'Это не номер O!. Оператор пропускает только префиксы 050, 070, 099',
    to_ok: 'Позвоним на {phone}', need_from: 'Нет номера для исходящих', need_to: 'Введите номер абонента',
    need_o: 'Исходящие возможны только на номера O! (050, 070, 099)', need_who: 'Выберите ассистента',
    call_queued: 'Звонок на {phone} поставлен в очередь', call_failed: 'Не удалось запустить звонок',
    hangup: 'Положить трубку', hangup_sent: 'Команда отбоя отправлена', hangup_failed: 'Не удалось положить трубку',
    going: 'идёт {time}', duration: 'длительность {time}', waiting_gateway: 'ждём шлюз',
    journal_title: 'Журнал звонков', journal_sub: 'Входящие и исходящие через шлюз. Расшифровки — на странице «Диалоги».', to_dialogs: 'Диалоги',
    th_time: 'Время', th_direction: 'Направление', th_peer: 'Абонент', th_our: 'Наш номер', th_assistant: 'Ассистент', th_call_status: 'Статус',
    th_duration: 'Длит.', th_result: 'Итог', inbound: 'Входящий', outbound: 'Исходящий',
    no_calls: 'Звонков ещё не было', calls_failed: 'Не удалось загрузить журнал',
    bind_title: 'Привязать к номеру {phone}', group_agents: 'Агенты обзвона', group_assistants: 'Ассистенты ElevenLabs',
    agent_unsupported: 'тип «{type}» не поддерживается телефонией',
    bind_empty: 'Нет ассистентов', bind_empty_text: 'Создайте ассистента на странице «Голосовые ассистенты» или агента обзвона.',
    bind_saved: 'Привязка сохранена', bind_failed: 'Не удалось привязать', unbound: 'Номер отвязан', unbind_failed: 'Не удалось отвязать',
    current_binding: 'текущая привязка',
    st_queued: 'В очереди', st_dialing: 'Набор номера', st_ringing: 'Вызов', st_answered: 'Разговор', st_completed: 'Завершён', st_failed: 'Не состоялся',
    er_completed: 'завершён', er_hangup: 'абонент положил трубку', er_assistant_hangup: 'ассистент завершил', er_busy: 'занято',
    er_no_answer: 'нет ответа', er_cancelled: 'отменён', er_channel_limit: 'все каналы заняты', er_congestion: 'сеть перегружена',
    er_rejected: 'отклонён', er_failed: 'ошибка', er_user_request: 'отбой из интерфейса', er_unknown_number: 'номер не найден',
    er_assistant_not_found: 'ассистент не найден', er_ami_unavailable: 'шлюз недоступен', er_trunk_unavailable: 'транк недоступен',
    request_error: 'Ошибка запроса',
    req_title: 'Подключение номера', req_sub: 'Оставьте заявку: мы подключим номер к вашему кабинету и сообщим, когда он появится в списке.',
    req_more: 'Подключить ещё номер', req_close: 'Отмена', no_numbers_req: 'Номер появится здесь после одобрения заявки.',
    req_kind_new: 'Новый номер', req_kind_new_text: 'Выдадим номер O! на нашей линии',
    req_kind_existing: 'У меня есть номер O!', req_kind_existing_text: 'Подключим ваш действующий номер',
    req_full_name: 'ФИО', req_full_name_ph: 'Иванов Иван Иванович', req_contact: 'Контактный телефон',
    req_contact_hint: 'По нему свяжемся для подключения', req_existing: 'Ваш номер O!',
    req_existing_hint: 'Номер O!: префиксы 050, 070, 099', req_checking: 'Проверяем номер…', req_number_ok: 'Номер {phone} можно подключить',
    req_comment: 'Комментарий', req_comment_ph: 'Необязательно: удобное время звонка, сколько нужно номеров и т. п.',
    req_submit: 'Отправить заявку', req_sent: 'Заявка отправлена', req_failed: 'Не удалось отправить заявку',
    req_need_name: 'Укажите ФИО полностью', req_need_contact: 'Укажите контактный телефон', req_need_existing: 'Укажите ваш номер O!',
    req_pending_title: 'Заявка на рассмотрении', req_pending_text: 'Мы свяжемся с вами по контактному телефону. Номер появится в списке «Мои номера».',
    req_rejected_title: 'Заявка отклонена', req_rejected_text: 'Можно отправить новую заявку ниже.', req_reason: 'Причина',
    req_dt_name: 'ФИО', req_dt_contact: 'Контакт', req_dt_kind: 'Что подключаем', req_dt_date: 'Отправлена',
    req_cancel: 'Отозвать заявку', req_cancelled: 'Заявка отозвана', req_cancel_failed: 'Не удалось отозвать заявку',
    req_steps_1: 'Проверим данные и свяжемся с вами', req_steps_2: 'Подключим номер к вашему кабинету',
    req_steps_3: 'Привяжите номер к ассистенту или агенту обзвона — и он начнёт принимать звонки'
  };
  function format(s, p) { return p ? String(s).replace(/\{(\w+)\}/g, function (m, k) { return p[k] != null ? p[k] : m; }) : s; }
  function t(key, params) { return window.I18N ? window.I18N.t('sip.' + key, params, STR[key]) : format(STR[key] || key, params); }
  var esc = VF.esc;

  // Оператор пропускает исходящие только на номера O!. Тот же список на бэкенде:
  // O_MOBILE_PREFIXES в backend/models/sip_gateway.py.
  var O_PREFIXES = ['050', '070', '099'];
  var TYPE_LABELS = { openai: 'OpenAI', gemini: 'Gemini', fish: 'Fish Audio', eleven: 'ElevenLabs' };
  var SIP_TYPES = ['openai', 'gemini', 'fish', 'eleven'];   // SIP_SUPPORTED_ASSISTANT_TYPES
  var ACTIVE = ['queued', 'dialing', 'ringing', 'answered'];

  // ------------------------------------------------------------------
  // API
  // ------------------------------------------------------------------
  function token() { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } }
  function api(path, opts) {
    opts = opts || {};
    var headers = { 'Authorization': 'Bearer ' + token() };
    var body = opts.body;
    if (body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
    return fetch('/api' + path, { method: opts.method || 'GET', headers: headers, body: body }).then(function (r) {
      if (r.status === 401) { location.href = '/static/login.html?reason=session_expired'; throw new Error('unauthorized'); }
      return r.text().then(function (txt) {
        var data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        if (!r.ok) {
          var d = data && data.detail;
          throw new Error(typeof d === 'string' ? d : (d && d.message) || (Array.isArray(d) && d[0] && d[0].msg) || t('request_error'));
        }
        return data;
      });
    });
  }
  if (!token()) { location.href = '/static/login.html'; return; }

  // ------------------------------------------------------------------
  // Форматирование
  // ------------------------------------------------------------------
  var $ = function (id) { return document.getElementById(id); };
  // 996705701707 → +996 705 701 707
  function fmtPhone(digits) {
    if (!digits) return '—';
    var d = String(digits).replace(/\D/g, '');
    if (d.length === 12 && d.indexOf('996') === 0) return '+996 ' + d.slice(3, 6) + ' ' + d.slice(6, 9) + ' ' + d.slice(9);
    return '+' + d;
  }
  // Любой ввод → 996XXXXXXXXX (как normalize_sip_number на бэкенде)
  function normalizePhone(v) {
    var d = String(v || '').replace(/\D/g, '');
    if (d.length === 10 && d.charAt(0) === '0') d = '996' + d.slice(1);
    else if (d.length === 9) d = '996' + d;
    return d;
  }
  function isO(d) {
    if (d.length !== 12 || d.indexOf('996') !== 0) return false;
    var national = '0' + d.slice(3);
    return O_PREFIXES.some(function (p) { return national.indexOf(p) === 0; });
  }
  function fmtDate(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bishkek' });
  }
  function fmtDur(sec) {
    if (sec === null || sec === undefined) return '—';
    var s = Math.round(sec);
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function statusChip(s) { return '<span class="st ' + esc(s) + '">' + esc(STR['st_' + s] ? t('st_' + s) : s) + '</span>'; }
  function endReason(c) {
    if (c.error) return c.error;
    if (!c.end_reason) return '';
    return STR['er_' + c.end_reason] ? t('er_' + c.end_reason) : c.end_reason;
  }
  function emptyBox(icon, title, text) {
    return '<div class="empty">' + VF.icon(icon, 'ic-lg') + '<div class="empty-title">' + esc(title) + '</div>' + (text ? '<div class="empty-text">' + esc(text) + '</div>' : '') + '</div>';
  }

  // ------------------------------------------------------------------
  // Состояние
  // ------------------------------------------------------------------
  var numbers = [];
  var targets = null;          // {eleven: [], agents: []}
  var selected = null;         // {kind, type, id} в модалке привязки
  var bindNumber = null;
  var currentCall = null, pollTimer = null, journalTimer = null;
  var bindModal = VF.modal('bind-modal');
  var requests = null;         // GET /sip/requests/my, новые сверху; null — ещё не загружены
  var numbersLoaded = false;
  var reqKind = 'new';         // new | existing_o
  var reqFormOpen = false;     // у клиента с номерами форма раскрывается кнопкой
  var reqCheckTimer = null, reqCheckSeq = 0, reqCheckOk = false;

  // ------------------------------------------------------------------
  // Номера
  // ------------------------------------------------------------------
  function bindingCell(n) {
    if (n.agent_config_id) {
      var sub = n.assistant_name ? (TYPE_LABELS[n.assistant_type] || n.assistant_type) + ' · ' + n.assistant_name : t('agent_voice_missing');
      return '<span class="bind">' + VF.icon('headset') + esc(n.agent_name || t('agent_deleted')) + '</span><div class="sub">' + esc(t('agent_label') + ' · ' + sub) + '</div>';
    }
    if (n.assistant_id) {
      if (!n.assistant_name) return '<span class="bind" style="color:var(--vf-danger)">' + VF.icon('triangle-alert') + esc(t('assistant_missing')) + '</span><div class="sub">' + esc(t('rebind')) + '</div>';
      return '<span class="bind">' + VF.icon('audio-lines') + esc(n.assistant_name) + '</span><div class="sub">' +
        esc((TYPE_LABELS[n.assistant_type] || n.assistant_type) + (n.assistant_active === false ? ' · ' + t('inactive') : '')) + '</div>';
    }
    return '<span class="chip chip-warning">' + VF.icon('unlink') + esc(t('not_bound')) + '</span>';
  }
  function renderNumbers() {
    var box = $('numbers');
    if (!numbers.length) { box.innerHTML = emptyBox('phone', t('no_numbers'), t('no_numbers_req')); return; }
    box.innerHTML = '<div class="table-wrap"><table class="table"><thead><tr><th>' + esc(t('th_number')) + '</th><th>' + esc(t('th_answers')) + '</th><th>' +
      esc(t('th_outbound')) + '</th><th>' + esc(t('th_status')) + '</th><th></th></tr></thead><tbody>' + numbers.map(function (n) {
        return '<tr><td><span class="phone">' + esc(fmtPhone(n.phone_number)) + '</span>' + (n.label ? '<div class="sub">' + esc(n.label) + '</div>' : '') + '</td>' +
          '<td>' + bindingCell(n) + '</td>' +
          '<td><span class="st' + (n.allow_outbound ? ' on' : '') + '">' + esc(n.allow_outbound ? t('allowed') : t('disabled')) + '</span></td>' +
          '<td><span class="st' + (n.is_active ? ' on' : '') + '">' + esc(n.is_active ? t('active') : t('off')) + '</span></td>' +
          '<td><div class="row"><button class="btn btn-sm" type="button" data-bind="' + esc(n.id) + '">' + VF.icon('link', 'ic-sm') + esc(t('bind')) + '</button>' +
          (n.allow_outbound && n.is_active ? '<button class="btn btn-sm btn-primary" type="button" data-call="' + esc(n.phone_number) + '">' + VF.icon('phone', 'ic-sm') + esc(t('call')) + '</button>' : '') +
          '</div></td></tr>';
      }).join('') + '</tbody></table></div>';
    Array.prototype.forEach.call(box.querySelectorAll('[data-bind]'), function (b) {
      b.addEventListener('click', function () { openBind(b.getAttribute('data-bind')); });
    });
    Array.prototype.forEach.call(box.querySelectorAll('[data-call]'), function (b) {
      b.addEventListener('click', function () {
        $('call-from').value = b.getAttribute('data-call');
        fillWho();
        $('outbound-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
        $('call-to').focus();
      });
    });
  }
  function loadNumbers() {
    return api('/sip/numbers').then(function (r) {
      numbers = (r && r.numbers) || [];
      numbersLoaded = true;
      renderNumbers();
      fillFrom();
      renderRequest();
    }).catch(function (e) {
      if (e.message === 'unauthorized') return;
      $('numbers').innerHTML = emptyBox('circle-alert', t('numbers_failed'), e.message);
    });
  }

  // ------------------------------------------------------------------
  // Заявка на подключение номера
  // ------------------------------------------------------------------
  function openRequest() { return (requests || []).filter(function (r) { return r.status === 'pending'; })[0] || null; }
  function loadRequests() {
    return api('/sip/requests/my').then(function (r) {
      requests = (r && r.requests) || [];
      renderRequest();
    }).catch(function () { requests = []; renderRequest(); });
  }
  function requestSummary(r) {
    var kind = r.kind === 'existing_o' ? t('req_kind_existing') + ' · ' + fmtPhone(r.existing_number) : t('req_kind_new');
    return '<dl><dt>' + esc(t('req_dt_name')) + '</dt><dd>' + esc(r.full_name) + '</dd>' +
      '<dt>' + esc(t('req_dt_contact')) + '</dt><dd class="phone">' + esc(fmtPhone(r.contact_phone)) + '</dd>' +
      '<dt>' + esc(t('req_dt_kind')) + '</dt><dd>' + esc(kind) + '</dd>' +
      '<dt>' + esc(t('req_dt_date')) + '</dt><dd>' + esc(fmtDate(r.created_at)) + '</dd>' +
      (r.admin_comment ? '<dt>' + esc(t('req_reason')) + '</dt><dd>' + esc(r.admin_comment) + '</dd>' : '') + '</dl>';
  }
  function renderRequest() {
    if (requests === null || !numbersLoaded) return;
    var card = $('request-card'), body = $('request-body');
    var open = openRequest();
    var hasNumbers = numbers.length > 0;
    // Без номеров заявка — первый шаг; с номерами форма раскрывается кнопкой «Подключить ещё номер»
    $('req-toggle').classList.toggle('hidden', !hasNumbers || !!open || reqFormOpen);
    card.classList.toggle('hidden', hasNumbers && !open && !reqFormOpen);
    if (open) {
      body.innerHTML = '<div class="req-status">' + VF.icon('clock', 'ic-lg') + '<div class="grow"><div class="title">' + esc(t('req_pending_title')) + '</div>' +
        '<div class="muted small">' + esc(t('req_pending_text')) + '</div>' + requestSummary(open) +
        '<ol class="req-steps"><li>' + esc(t('req_steps_1')) + '</li><li>' + esc(t('req_steps_2')) + '</li><li>' + esc(t('req_steps_3')) + '</li></ol>' +
        '<div style="margin-top:12px"><button class="btn btn-sm" type="button" id="req-cancel">' + VF.icon('x', 'ic-sm') + esc(t('req_cancel')) + '</button></div></div></div>';
      $('req-cancel').addEventListener('click', function () { cancelRequest(open.id); });
      return;
    }
    if (card.classList.contains('hidden')) { body.innerHTML = ''; return; }
    var last = requests[0];
    var rejected = last && last.status === 'rejected'
      ? '<div class="req-status rejected" style="margin-bottom:16px">' + VF.icon('circle-alert', 'ic-lg') + '<div class="grow"><div class="title">' + esc(t('req_rejected_title')) + '</div>' +
        '<div class="muted small">' + esc(t('req_rejected_text')) + '</div>' + requestSummary(last) + '</div></div>'
      : '';
    var kindOpt = function (kind, icon, title, text) {
      return '<div class="bind-opt' + (reqKind === kind ? ' selected' : '') + '" data-kind="' + kind + '"><span class="logo-wrap">' + VF.icon(icon) + '</span>' +
        '<div><div class="name">' + esc(title) + '</div><div class="sub">' + esc(text) + '</div></div></div>';
    };
    body.innerHTML = rejected +
      '<div class="req-kinds">' + kindOpt('new', 'sparkles', t('req_kind_new'), t('req_kind_new_text')) +
      kindOpt('existing_o', 'phone', t('req_kind_existing'), t('req_kind_existing_text')) + '</div>' +
      '<div class="req-form">' +
      '<div class="field span-2"><label class="label" for="req-name">' + esc(t('req_full_name')) + '</label>' +
      '<input class="input" id="req-name" maxlength="150" autocomplete="name" placeholder="' + esc(t('req_full_name_ph')) + '"></div>' +
      '<div class="field"><label class="label" for="req-contact">' + esc(t('req_contact')) + '</label>' +
      '<input class="input" type="tel" id="req-contact" autocomplete="tel" placeholder="0700 123 456"><span class="hint">' + esc(t('req_contact_hint')) + '</span></div>' +
      '<div class="field' + (reqKind === 'existing_o' ? '' : ' hidden') + '" id="req-existing-field"><label class="label" for="req-existing">' + esc(t('req_existing')) + '</label>' +
      '<input class="input" type="tel" id="req-existing" autocomplete="off" placeholder="0700 123 456"><span class="hint" id="req-existing-hint">' + esc(t('req_existing_hint')) + '</span></div>' +
      '<div class="field span-2"><label class="label" for="req-comment">' + esc(t('req_comment')) + '</label>' +
      '<textarea class="textarea" id="req-comment" rows="2" maxlength="1000" placeholder="' + esc(t('req_comment_ph')) + '"></textarea></div>' +
      '<div class="span-2 row"><button class="btn btn-primary" type="button" id="req-submit">' + VF.icon('send') + '<span>' + esc(t('req_submit')) + '</span></button>' +
      (numbers.length ? '<button class="btn" type="button" id="req-close">' + esc(t('req_close')) + '</button>' : '') + '</div>' +
      '</div>';
    reqCheckOk = false;
    Array.prototype.forEach.call(body.querySelectorAll('.req-kinds .bind-opt'), function (el) {
      el.addEventListener('click', function () {
        reqKind = el.getAttribute('data-kind');
        body.querySelectorAll('.req-kinds .bind-opt').forEach(function (o) { o.classList.toggle('selected', o === el); });
        $('req-existing-field').classList.toggle('hidden', reqKind !== 'existing_o');
        if (reqKind === 'existing_o') $('req-existing').focus();
      });
    });
    $('req-existing').addEventListener('input', scheduleNumberCheck);
    $('req-submit').addEventListener('click', submitRequest);
    if ($('req-close')) $('req-close').addEventListener('click', function () { reqFormOpen = false; renderRequest(); });
  }
  function setExistingHint(text, cls) {
    var h = $('req-existing-hint');
    if (!h) return;
    h.textContent = text;
    h.classList.toggle('error', cls === 'error');
    h.classList.toggle('ok', cls === 'ok');
  }
  // Проверка своего номера O!: формат и префикс — сразу, «не подключён ли уже» — запросом
  function scheduleNumberCheck() {
    clearTimeout(reqCheckTimer);
    reqCheckOk = false;
    var d = normalizePhone($('req-existing').value);
    if (!d) return setExistingHint(t('req_existing_hint'));
    if (d.length < 12) return setExistingHint(t('req_existing_hint'));
    if (!isO(d)) return setExistingHint(t('to_not_o'), 'error');
    setExistingHint(t('req_checking'));
    var seq = ++reqCheckSeq;
    reqCheckTimer = setTimeout(function () {
      api('/sip/requests/check-number', { method: 'POST', body: { phone_number: d } }).then(function (r) {
        if (seq !== reqCheckSeq) return;
        reqCheckOk = !!(r && r.ok);
        setExistingHint(reqCheckOk ? t('req_number_ok', { phone: fmtPhone(r.phone_number) }) : (r && r.message) || t('to_not_o'), reqCheckOk ? 'ok' : 'error');
      }).catch(function (e) { if (seq === reqCheckSeq) setExistingHint(e.message, 'error'); });
    }, 350);
  }
  function submitRequest() {
    var name = $('req-name').value.replace(/\s+/g, ' ').trim();
    var contact = normalizePhone($('req-contact').value);
    var existing = reqKind === 'existing_o' ? normalizePhone($('req-existing').value) : null;
    if (name.length < 3) return VF.toast(t('req_need_name'), { type: 'warning' });
    if (contact.length < 9) return VF.toast(t('req_need_contact'), { type: 'warning' });
    if (reqKind === 'existing_o') {
      if (!existing) return VF.toast(t('req_need_existing'), { type: 'warning' });
      if (!isO(existing)) return VF.toast(t('to_not_o'), { type: 'warning' });
    }
    var btn = $('req-submit'); btn.disabled = true;
    api('/sip/requests', { method: 'POST', body: {
      kind: reqKind, full_name: name, contact_phone: contact, existing_number: existing,
      comment: $('req-comment').value.trim() || null
    } }).then(function (r) {
      VF.toast(t('req_sent'), { type: 'success' });
      requests.unshift(r);
      reqFormOpen = false;
      renderRequest();
    }).catch(function (e) {
      VF.toast(e.message || t('req_failed'), { type: 'error', duration: 7000 });
      btn.disabled = false;
    });
  }
  function cancelRequest(id) {
    var b = $('req-cancel'); if (b) b.disabled = true;
    api('/sip/requests/' + encodeURIComponent(id) + '/cancel', { method: 'POST', body: {} }).then(function () {
      VF.toast(t('req_cancelled'), { type: 'info' });
      return loadRequests();
    }).catch(function (e) { VF.toast(e.message || t('req_cancel_failed'), { type: 'error' }); if (b) b.disabled = false; });
  }

  // ------------------------------------------------------------------
  // Кого можно привязать: ассистенты ElevenLabs и агенты обзвона
  // ------------------------------------------------------------------
  function loadTargets(force) {
    if (targets && !force) return Promise.resolve(targets);
    var safe = function (p, pick) { return p.then(pick, function () { return []; }); };
    return Promise.all([
      safe(api('/eleven-assistants?include_agent_voices=false'), function (d) { return (d && d.assistants) || []; }),
      safe(api('/agent/list'), function (d) { return (d && d.agents) || []; })
    ]).then(function (r) { targets = { eleven: r[0], agents: r[1] }; return targets; });
  }
  function agentOk(a) { return SIP_TYPES.indexOf(a.assistant_type) !== -1 && a.assistant_id; }

  function openBind(numberId) {
    bindNumber = numbers.filter(function (n) { return String(n.id) === String(numberId); })[0];
    if (!bindNumber) return;
    selected = null;
    $('bind-btn').disabled = true;
    $('bind-title').textContent = t('bind_title', { phone: fmtPhone(bindNumber.phone_number) });
    $('unbind-btn').classList.toggle('hidden', !bindNumber.assistant_id && !bindNumber.agent_config_id);
    var list = $('bind-list');
    list.innerHTML = '<div class="skeleton" style="height:48px"></div><div class="skeleton" style="height:48px"></div>';
    bindModal.open();
    loadTargets(true).then(function (c) {
      var n = bindNumber, parts = [];
      var opt = function (kind, type, id, name, sub, icon, disabled) {
        var cur = kind === 'agent' ? n.agent_config_id === id : (!n.agent_config_id && n.assistant_id === id);
        return '<div class="bind-opt' + (disabled ? ' disabled' : '') + (cur ? ' selected' : '') + '" data-kind="' + kind + '" data-type="' + esc(type) + '" data-id="' + esc(id) + '"' +
          (disabled ? ' data-disabled="1"' : '') + '><span class="logo-wrap">' + VF.icon(icon) + '</span><div><div class="name">' + esc(name) + '</div><div class="sub">' + esc(sub) + '</div></div></div>';
      };
      if (c.agents.length) {
        parts.push('<div class="bind-group">' + esc(t('group_agents')) + '</div>');
        c.agents.forEach(function (a) {
          var ok = agentOk(a);
          parts.push(opt('agent', a.assistant_type, a.id, a.name, ok ? (TYPE_LABELS[a.assistant_type] + ' · ' + (a.voice_assistant_name || '')) : t('agent_unsupported', { type: a.assistant_type || '—' }), 'headset', !ok));
        });
      }
      var eleven = c.eleven.slice();
      // Номер привязан к ассистенту другого провайдера — показываем его, чтобы привязку можно было оставить
      if (!n.agent_config_id && n.assistant_id && n.assistant_type !== 'eleven' && n.assistant_name) {
        parts.push('<div class="bind-group">' + esc(t('current_binding')) + '</div>');
        parts.push(opt('assistant', n.assistant_type, n.assistant_id, n.assistant_name, TYPE_LABELS[n.assistant_type] || n.assistant_type, 'audio-lines', false));
      }
      if (eleven.length) {
        parts.push('<div class="bind-group">' + esc(t('group_assistants')) + '</div>');
        eleven.forEach(function (a) { parts.push(opt('assistant', 'eleven', a.id, a.name, a.is_active === false ? t('inactive') : (a.voice_name || 'ElevenLabs'), 'audio-lines', false)); });
      }
      if (!parts.length) { list.innerHTML = emptyBox('audio-lines', t('bind_empty'), t('bind_empty_text')); return; }
      list.innerHTML = parts.join('');
      Array.prototype.forEach.call(list.querySelectorAll('.bind-opt'), function (el) {
        if (el.hasAttribute('data-disabled')) return;
        var pick = function () {
          list.querySelectorAll('.bind-opt').forEach(function (o) { o.classList.remove('selected'); });
          el.classList.add('selected');
          selected = { kind: el.getAttribute('data-kind'), type: el.getAttribute('data-type'), id: el.getAttribute('data-id') };
          $('bind-btn').disabled = false;
        };
        if (el.classList.contains('selected')) { selected = { kind: el.getAttribute('data-kind'), type: el.getAttribute('data-type'), id: el.getAttribute('data-id') }; $('bind-btn').disabled = false; }
        el.addEventListener('click', pick);
      });
    });
  }
  function patchNumber(body, okMsg, failMsg) {
    var b1 = $('bind-btn'), b2 = $('unbind-btn');
    b1.disabled = b2.disabled = true;
    return api('/sip/numbers/' + encodeURIComponent(bindNumber.id), { method: 'PATCH', body: body }).then(function () {
      bindModal.close();
      VF.toast(okMsg, { type: 'success' });
      return loadNumbers();
    }).catch(function (e) { VF.toast(e.message || failMsg, { type: 'error' }); }).then(function () { b1.disabled = !selected; b2.disabled = false; });
  }

  // ------------------------------------------------------------------
  // Исходящий звонок
  // ------------------------------------------------------------------
  function fillFrom() {
    var sel = $('call-from'), prev = sel.value;
    var usable = numbers.filter(function (n) { return n.is_active && n.allow_outbound; });
    sel.innerHTML = usable.length
      ? usable.map(function (n) { return '<option value="' + esc(n.phone_number) + '">' + esc(fmtPhone(n.phone_number) + (n.label ? ' · ' + n.label : '')) + '</option>'; }).join('')
      : '<option value="">' + esc(t('no_outbound_number')) + '</option>';
    if (prev && usable.some(function (n) { return n.phone_number === prev; })) sel.value = prev;
    $('call-btn').disabled = !usable.length;
    fillWho();
  }
  function fillWho() {
    var sel = $('call-who');
    var n = numbers.filter(function (x) { return x.phone_number === $('call-from').value; })[0];
    loadTargets().then(function (c) {
      var html = '';
      var isSel = function (kind, id) { return n && (kind === 'agent' ? n.agent_config_id === id : (!n.agent_config_id && n.assistant_id === id)); };
      var agents = c.agents.filter(agentOk);
      if (agents.length) {
        html += '<optgroup label="' + esc(t('group_agents')) + '">' + agents.map(function (a) {
          return '<option value="' + esc(a.assistant_type + ':' + a.assistant_id) + '"' + (isSel('agent', a.id) ? ' selected' : '') + '>' + esc(a.name + ' (' + TYPE_LABELS[a.assistant_type] + ')') + '</option>';
        }).join('') + '</optgroup>';
      }
      if (n && !n.agent_config_id && n.assistant_id && n.assistant_type !== 'eleven' && n.assistant_name) {
        html += '<optgroup label="' + esc(t('current_binding')) + '"><option value="' + esc(n.assistant_type + ':' + n.assistant_id) + '" selected>' +
          esc(n.assistant_name + ' (' + (TYPE_LABELS[n.assistant_type] || n.assistant_type) + ')') + '</option></optgroup>';
      }
      if (c.eleven.length) {
        html += '<optgroup label="' + esc(t('group_assistants')) + '">' + c.eleven.map(function (a) {
          return '<option value="eleven:' + esc(a.id) + '"' + (isSel('assistant', a.id) ? ' selected' : '') + '>' + esc(a.name) + '</option>';
        }).join('') + '</optgroup>';
      }
      sel.innerHTML = html || '<option value="">' + esc(t('no_assistants')) + '</option>';
    });
  }
  function updateToHint() {
    var h = $('call-to-hint'), d = normalizePhone($('call-to').value);
    h.classList.toggle('error', !!d && !isO(d));
    h.textContent = !d ? t('to_hint') : (isO(d) ? t('to_ok', { phone: fmtPhone(d) }) : t('to_not_o'));
  }
  function startCall() {
    var from = $('call-from').value, to = normalizePhone($('call-to').value), who = $('call-who').value;
    if (!from) return VF.toast(t('need_from'), { type: 'warning' });
    if (!to) return VF.toast(t('need_to'), { type: 'warning' });
    if (!isO(to)) return VF.toast(t('need_o'), { type: 'warning' });
    if (!who) return VF.toast(t('need_who'), { type: 'warning' });
    var i = who.indexOf(':');
    var btn = $('call-btn'); btn.disabled = true;
    api('/sip/calls', { method: 'POST', body: { to: to, caller_id: from, assistant_type: who.slice(0, i), assistant_id: who.slice(i + 1) } }).then(function (call) {
      VF.toast(t('call_queued', { phone: fmtPhone(to) }), { type: 'success' });
      track(call);
      loadCalls();
    }).catch(function (e) { VF.toast(e.message || t('call_failed'), { type: 'error', duration: 7000 }); }).then(function () { btn.disabled = false; });
  }
  function renderCallStatus(c) {
    var box = $('call-status');
    box.className = 'call-status ' + c.status;
    $('call-status-text').textContent = (STR['st_' + c.status] ? t('st_' + c.status) : c.status) + ' · ' + fmtPhone(c.to_number);
    var parts = [];
    if (c.status === 'answered' && c.answered_at) parts.push(t('going', { time: fmtDur((Date.now() - new Date(c.answered_at)) / 1000) }));
    if (c.status === 'completed') parts.push(t('duration', { time: fmtDur(c.duration_sec) }));
    var r = endReason(c);
    if (r && (c.status === 'completed' || c.status === 'failed')) parts.push(r);
    if (c.status === 'queued') parts.push(t('waiting_gateway'));
    $('call-status-detail').textContent = parts.join(' · ');
    $('hangup-btn').classList.toggle('hidden', ACTIVE.indexOf(c.status) === -1);
  }
  function track(call) {
    currentCall = call.id;
    renderCallStatus(call);
    clearInterval(pollTimer);
    pollTimer = setInterval(poll, 2000);
  }
  function poll() {
    if (!currentCall) { clearInterval(pollTimer); return; }
    api('/sip/calls/' + encodeURIComponent(currentCall)).then(function (c) {
      renderCallStatus(c);
      if (ACTIVE.indexOf(c.status) === -1) { clearInterval(pollTimer); pollTimer = null; currentCall = null; loadCalls(); }
    }).catch(function () {});
  }
  function hangup() {
    if (!currentCall) return;
    var b = $('hangup-btn'); b.disabled = true;
    api('/sip/calls/' + encodeURIComponent(currentCall) + '/hangup', { method: 'POST', body: {} }).then(function () {
      VF.toast(t('hangup_sent'), { type: 'info' });
    }).catch(function (e) { VF.toast(e.message || t('hangup_failed'), { type: 'error' }); }).then(function () { b.disabled = false; });
  }

  // ------------------------------------------------------------------
  // Журнал
  // ------------------------------------------------------------------
  function loadCalls() {
    var box = $('calls');
    clearTimeout(journalTimer);
    return api('/sip/calls?limit=50').then(function (r) {
      var calls = (r && r.calls) || [];
      if (!calls.length) { box.innerHTML = emptyBox('phone-call', t('no_calls')); return; }
      box.innerHTML = '<div class="table-wrap"><table class="table"><thead><tr><th>' + [t('th_time'), t('th_direction'), t('th_peer'), t('th_our'), t('th_assistant'), t('th_call_status'), t('th_duration'), t('th_result')].map(esc).join('</th><th>') +
        '</th></tr></thead><tbody>' + calls.map(function (c) {
          var inbound = c.direction === 'inbound';
          return '<tr><td class="muted" style="white-space:nowrap">' + esc(fmtDate(c.created_at)) + '</td>' +
            '<td><span class="dir ' + (inbound ? 'in' : 'out') + '">' + VF.icon(inbound ? 'phone-incoming' : 'phone-outgoing') + esc(inbound ? t('inbound') : t('outbound')) + '</span></td>' +
            '<td><span class="phone">' + esc(fmtPhone(inbound ? c.caller : c.to_number)) + '</span></td>' +
            '<td class="muted">' + esc(fmtPhone(c.did)) + '</td>' +
            '<td>' + esc(c.assistant_type ? (TYPE_LABELS[c.assistant_type] || c.assistant_type) : '—') + '</td>' +
            '<td>' + statusChip(c.status) + '</td><td>' + esc(fmtDur(c.duration_sec)) + '</td>' +
            '<td class="muted small">' + esc(endReason(c)) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
      // Пока идут звонки (в том числе входящие) — обновляем журнал сами
      if (calls.some(function (c) { return ACTIVE.indexOf(c.status) !== -1; }) && !pollTimer) journalTimer = setTimeout(loadCalls, 5000);
    }).catch(function (e) {
      if (e.message !== 'unauthorized') box.innerHTML = emptyBox('circle-alert', t('calls_failed'), e.message);
    });
  }

  // ------------------------------------------------------------------
  // События и старт
  // ------------------------------------------------------------------
  $('refresh-numbers').addEventListener('click', function () { loadNumbers(); loadRequests(); });
  $('req-toggle').addEventListener('click', function () { reqFormOpen = true; renderRequest(); $('request-card').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  $('refresh-calls').addEventListener('click', loadCalls);
  $('call-from').addEventListener('change', fillWho);
  $('call-to').addEventListener('input', updateToHint);
  $('call-btn').addEventListener('click', startCall);
  $('hangup-btn').addEventListener('click', hangup);
  $('bind-btn').addEventListener('click', function () {
    if (!selected) return;
    patchNumber(selected.kind === 'agent' ? { agent_config_id: selected.id } : { assistant_type: selected.type, assistant_id: selected.id }, t('bind_saved'), t('bind_failed'));
  });
  $('unbind-btn').addEventListener('click', function () {
    patchNumber({ assistant_type: null, assistant_id: null }, t('unbound'), t('unbind_failed'));
  });
  updateToHint();
  Promise.all([loadNumbers(), loadCalls(), loadRequests()]).catch(function () {}).then(function () { VF.ready(); });
})();
