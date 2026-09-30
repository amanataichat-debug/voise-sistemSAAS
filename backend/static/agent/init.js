/* ============================================================================
 * agent/init.js — Точки входа: DOMContentLoaded, focus, Escape. Грузится ПОСЛЕДНИМ.
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

window.addEventListener('focus', () => { if(creditsState) loadCredits(); });

// ── INIT ──
document.addEventListener('DOMContentLoaded', async () => {
  if(!getToken()){ location.href='/static/login.html'; return; }
  initPanels();
  await initAgents();

  // ── Кастомный dropdown переключения агентов ──
  document.getElementById('agent-trigger')?.addEventListener('click', (e)=>{
    if(document.getElementById('agent-switch').classList.contains('no-dropdown')) return;
    e.stopPropagation();
    toggleAgentDropdown();
  });
  document.addEventListener('click', (e)=>{
    const sw = document.getElementById('agent-switch');
    if(sw && !sw.contains(e.target)) closeAgentDropdown();
  });
  document.addEventListener('keydown', (e)=>{
    if(e.key==='Escape'){ closeAgentDropdown(); closeDrawer(); }
  });

  // ── Мобильное бургер-меню ──
  document.getElementById('burger-btn')?.addEventListener('click', openDrawer);
  document.getElementById('drawer-close')?.addEventListener('click', closeDrawer);
  document.getElementById('drawer-overlay')?.addEventListener('click', function(e){ if(e.target===this) closeDrawer(); });
  MOBILE_MQ.addEventListener('change', e => applyLayout(e.matches));
  applyLayout(MOBILE_MQ.matches);

  document.getElementById('chat-send').addEventListener('click', sendMessage);
  const inp = document.getElementById('chat-input');
  inp.addEventListener('keydown', e => { if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendMessage(); } });
  inp.addEventListener('input', () => { inp.style.height='auto'; inp.style.height=Math.min(inp.scrollHeight,120)+'px'; });

  document.getElementById('active-toggle').addEventListener('change', toggleActive);
  document.querySelectorAll('.doc-tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));
  document.querySelectorAll('#settings-nav .settings-nav-item').forEach(b => b.addEventListener('click', () => openSettingsSection(b.dataset.section)));
  // Клик по фону закрывает модалку (кроме импорта в процессе — там своя логика кнопок)
  document.querySelectorAll('.modal-overlay').forEach(ov => {
    ov.addEventListener('click', function(e){ if(e.target===this) this.classList.add('hidden'); });
  });
});

// Esc закрывает верхнюю открытую модалку (карточка контакта лежит поверх остальных)
document.addEventListener('keydown', e => {
  if(e.key !== 'Escape') return;
  const open = Array.from(document.querySelectorAll('.modal-overlay:not(.hidden)'));
  if(!open.length) return;
  const top = open.find(o => o.id === 'contact-details-modal-overlay') || open[open.length - 1];
  top.classList.add('hidden');
});

