/* ============================================================================
 * agent/credits.js — Кредиты и подписка оркестратора → /api/credits/*
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ════════════════ КРЕДИТЫ / ПОДПИСКА ════════════════
async function loadCredits(){
  try{
    const r = await apiFetch(CREDITS_API + '/balance');
    if(!r || r.status!==200) return;
    creditsState = await r.json();
    renderCreditsBadge();
  }catch(e){}
}

function renderCreditsBadge(){
  if(!creditsState) return;
  const badge = document.getElementById('sub-badge');
  const statusEl = document.getElementById('sub-status');
  const numEl = document.getElementById('sub-credits-num');
  const actionEl = document.getElementById('sub-action');
  const banner = document.getElementById('sub-expired-banner');
  badge.style.display = 'flex';

  const st = creditsState.subscription_status;
  statusEl.className = 'sub-status ' + st;
  if(st==='active') statusEl.textContent = '● Активен';
  else if(st==='trial') statusEl.textContent = `● Trial — ${creditsState.days_remaining} д.`;
  else if(st==='expired') statusEl.textContent = '● Истёк';
  else statusEl.textContent = '○ Не активен';

  numEl.textContent = (creditsState.credits_balance||0).toLocaleString('ru');

  // Кнопка действия
  if(st==='expired' || st==='none'){
    actionEl.style.display='inline-block';
    actionEl.className='sub-action danger';
    actionEl.textContent='Продлить';
  } else if((st==='active'||st==='trial') && (creditsState.credits_balance||0) < 500){
    actionEl.style.display='inline-block';
    actionEl.className='sub-action';
    actionEl.textContent='Пополнить';
  } else {
    actionEl.style.display='none';
  }

  // Баннер блокировки
  banner.style.display = creditsState.is_blocked ? 'flex' : 'none';

  // Карточка «Кредиты оркестратора»
  const expCredits = document.getElementById('exp-credits');
  const expStatus = document.getElementById('exp-sub-status');
  const expUntil = document.getElementById('exp-sub-until');
  if(expCredits) expCredits.textContent = (creditsState.credits_balance||0).toLocaleString('ru') + ' кр.';
  if(expStatus) expStatus.textContent = statusEl.textContent.replace(/^[●○]\s*/,'');
  if(expUntil) expUntil.textContent = creditsState.subscription_end_date
    ? new Date(creditsState.subscription_end_date).toLocaleDateString('ru') : '—';
}

function onSubAction(){
  if(!creditsState) return;
  const st = creditsState.subscription_status;
  if(st==='expired' || st==='none') subscribeAgent();
  else openCreditsModal();
}

async function openCreditsModal(){
  document.getElementById('credits-modal-overlay').classList.remove('hidden');
  const list = document.getElementById('credits-pkg-list');
  list.innerHTML = '<div class="empty">Загрузка…</div>';
  try{
    const r = await apiFetch(CREDITS_API + '/packages');
    const data = await r.json();
    const pkgs = data.packages || [];
    if(!pkgs.length){ list.innerHTML = '<div class="empty">Нет доступных пакетов</div>'; return; }
    list.innerHTML = pkgs.map(p => `
      <div class="credits-pkg">
        <div class="pkg-name">${p.name}</div>
        <div class="pkg-credits">${(p.credits).toLocaleString('ru')} кр.</div>
        <div class="pkg-price">${p.price_formatted}</div>
        <button onclick="purchasePackage('${p.code}')">Купить</button>
      </div>`).join('');
  }catch(e){ list.innerHTML = '<div class="empty">Ошибка загрузки</div>'; }
}

async function purchasePackage(code){
  try{
    const r = await apiFetch(CREDITS_API + '/purchase', { method:'POST', body: JSON.stringify({ package_code: code }) });
    if(!r || r.status!==200){ alert('Не удалось создать платёж'); return; }
    const data = await r.json();
    submitRobokassaForm(data);
  }catch(e){ alert('Ошибка: '+e.message); }
}

async function subscribeAgent(){
  try{
    const r = await apiFetch(CREDITS_API + '/subscribe', { method:'POST' });
    if(!r || r.status!==200){ alert('Не удалось оформить подписку'); return; }
    const data = await r.json();
    if(data.trial_activated){ alert('Trial активирован! Вам доступно 1 500 кредитов на 3 дня.'); loadCredits(); return; }
    submitRobokassaForm(data);
  }catch(e){ alert('Ошибка: '+e.message); }
}

// Сабмит формы Robokassa (редирект на оплату)
function submitRobokassaForm(data){
  if(!data || !data.payment_url || !data.form_params){ alert('Некорректный ответ платёжной системы'); return; }
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = data.payment_url;
  Object.entries(data.form_params).forEach(([k,v]) => {
    const inp = document.createElement('input');
    inp.type='hidden'; inp.name=k; inp.value=v;
    form.appendChild(inp);
  });
  document.body.appendChild(form);
  form.submit();
}

async function openBillingModal(){
  document.getElementById('billing-modal-overlay').classList.remove('hidden');
  const sum = document.getElementById('billing-summary');
  const list = document.getElementById('billing-tx-list');
  list.innerHTML = '<div class="empty">Загрузка…</div>';
  if(creditsState){
    sum.innerHTML = `Баланс: <b>${(creditsState.credits_balance||0).toLocaleString('ru')} кр.</b> · Статус: ${creditsState.subscription_status}` +
      (creditsState.subscription_end_date ? ` · до ${new Date(creditsState.subscription_end_date).toLocaleDateString('ru')}` : '') +
      ` <button class="sub-action" style="margin-left:8px" onclick="subscribeAgent()">Продлить</button>`;
  }
  try{
    const r = await apiFetch(CREDITS_API + '/transactions?limit=50');
    const data = await r.json();
    const txs = data.transactions || [];
    if(!txs.length){ list.innerHTML = '<div class="empty">Нет транзакций</div>'; return; }
    const labels = { trial_grant:'Trial', subscription_grant:'Подписка', purchase:'Покупка', spend:'Списание', refund:'Возврат', manual_adjust:'Система' };
    list.innerHTML = txs.map(t => {
      const cls = t.amount>0 ? 'pos' : (t.amount<0 ? 'neg' : 'zero');
      const amt = t.amount>0 ? `+${t.amount}` : `${t.amount}`;
      const when = t.created_at ? new Date(t.created_at).toLocaleString('ru',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
      return `<div class="credits-tx-row">
        <span>${when} · ${labels[t.type]||t.type}${t.ref_type?` <span style="color:var(--hint)">(${t.ref_type})</span>`:''}</span>
        <span class="credits-tx-amt ${cls}">${amt} кр · <span style="color:var(--hint)">${(t.balance_after||0).toLocaleString('ru')}</span></span>
      </div>`;
    }).join('');
  }catch(e){ list.innerHTML = '<div class="empty">Ошибка загрузки</div>'; }
}


