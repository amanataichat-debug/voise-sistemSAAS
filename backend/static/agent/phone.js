/* ============================================================================
 * agent/phone.js — Номер агента в собственной телефонии VoksiAI (SIP-шлюз)
 * Часть страницы /static/agent.html. Классический скрипт: функции и состояние глобальные.
 *
 * Агента можно создать и без номера: чат, Telegram и Instagram работают и так.
 * Пока у пользователя нет номеров, карточка и настройки сообщают, что номер
 * подключается позже; как только номер появился в базе — выбор разблокируется.
 * Выбранный номер: исходящие агента звонят с него, входящие на него обрабатывает
 * агент (история + разбор разговора).
 * Backend: GET /api/agent/phone-numbers, PUT /api/agent/phone-number {number_id|null}.
 * ========================================================================== */

let phoneState = { phone_numbers: [], has_numbers: false, selected_number_id: null };

function formatPhone(digits){
  const d = String(digits || '').replace(/\D/g, '');
  if(d.length === 12 && d.startsWith('996')) return `+996 ${d.slice(3,6)} ${d.slice(6,9)} ${d.slice(9)}`;
  return d ? '+' + d : '';
}

function selectedPhone(){
  return (phoneState.phone_numbers || []).find(n => n.id === phoneState.selected_number_id) || null;
}

async function loadPhoneNumbers(){
  try{
    const r = await apiFetch(API + '/phone-numbers');
    if(r && r.status === 200){
      phoneState = await r.json();
      phoneNumbers = phoneState.phone_numbers || [];
    }
  }catch(e){}
  renderPhoneBlock();
}

function renderPhoneBlock(){
  const el = document.getElementById('phone-block');
  if(!el) return;
  const sel = selectedPhone();
  if(sel){
    const dir = sel.allow_outbound ? 'Исходящие и входящие' : 'Только входящие (исходящие с номера выключены)';
    el.innerHTML = `<div class="phone-row">
        <span class="phone-ic"><i class="fas fa-phone"></i></span>
        <div style="min-width:0">
          <div class="phone-num">${esc(formatPhone(sel.phone_number))}</div>
          <div class="phone-sub">${esc(sel.label ? sel.label + ' · ' : '')}${dir}</div>
        </div>
      </div>`;
    return;
  }
  if(phoneState.has_numbers){
    el.innerHTML = `<div class="phone-row">
        <span class="phone-ic off"><i class="fas fa-phone"></i></span>
        <div class="phone-note">Номер не выбран — агент пока не звонит. <a href="#" onclick="openInstructionsModal('calls');return false;">Выбрать номер</a></div>
      </div>`;
    return;
  }
  el.innerHTML = `<div class="phone-row">
      <span class="phone-ic off"><i class="fas fa-phone-slash"></i></span>
      <div class="phone-note">Номера пока нет. Агент работает в чате, Telegram и Instagram, а номер можно будет подключить в настройках агента.</div>
    </div>`;
}

// Селект номера в настройках (раздел «Звонки»).
function fillPhoneSelect(selId){
  const sel = document.getElementById(selId);
  const note = document.getElementById('i-phone-note');
  if(!sel) return;
  const nums = phoneState.phone_numbers || [];
  sel.innerHTML = '<option value="">Без номера</option>' + nums.map(n => {
    const other = n.agent_config_id && !n.bound_to_this_agent && n.agent_name ? ` · сейчас у агента «${n.agent_name}»` : '';
    const label = n.label ? ` (${n.label})` : '';
    const inOnly = n.allow_outbound ? '' : ' · только входящие';
    return `<option value="${esc(n.id)}">${esc(formatPhone(n.phone_number) + label + inOnly + other)}</option>`;
  }).join('');
  sel.value = phoneState.selected_number_id || '';
  sel.disabled = !nums.length;
  if(note){
    note.innerHTML = nums.length ? '' :
      `<div class="note"><i class="fas fa-circle-info"></i><span>Номеров в собственной телефонии пока нет. Как только номер подключат к вашему аккаунту, здесь можно будет выбрать его для агента. До этого агент работает в чате, Telegram и Instagram.</span></div>`;
  }
}

// Сохранить выбор номера, если он изменился. Возвращает true при успехе или без изменений.
async function savePhoneSelection(selId){
  const sel = document.getElementById(selId);
  if(!sel || sel.disabled) return true;
  const want = sel.value || null;
  if((want || null) === (phoneState.selected_number_id || null)) return true;
  try{
    const r = await apiFetch(API + '/phone-number', { method:'PUT', body: JSON.stringify({ number_id: want }) });
    if(r && r.status === 200){
      phoneState = await r.json();
      phoneNumbers = phoneState.phone_numbers || [];
      renderPhoneBlock();
      return true;
    }
    const err = await r?.json().catch(() => ({}));
    showToast(errText(err.detail ?? err.message), 'error');
  }catch(e){ showToast('Ошибка сети', 'error'); }
  return false;
}
