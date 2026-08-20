/* ============================================================================
 * agent/motion.js — UI Фаза 2: микроинтерактивы (count-up, скелетоны, «пробуждение»)
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Подключается из agent.html ПЕРЕД init.js. Документация: backend/static/agent/CLAUDE.md
 *
 * Принцип: слой полностью аддитивный. Не правит loadStats/loadRecentCalls/
 * loadTasks/toggleActive — навешивается поверх через MutationObserver и
 * дополнительный 'change'-листенер. Уважает prefers-reduced-motion.
 * ========================================================================== */

const MOTION_REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ── Анимированный счётчик чисел статистики ──
// Наблюдает за #stat-calls/#stat-contacts. Когда loadStats() подставляет туда
// число — плавно «докручивает» от прошлого значения к новому.
function _animateCount(el){
  const raw = (el.textContent || '').trim();
  const target = parseInt(raw.replace(/[^\d]/g, ''), 10);
  if(isNaN(target)) return;             // «—» или не-число — ничего не делаем
  if(el._cnAnimating) return;           // игнорируем свои же промежуточные записи
  if(el._cnDone === target){ el.classList.remove('sk-num'); return; }

  el.classList.remove('sk-num');        // снять скелетон, как только пришли данные

  if(MOTION_REDUCED){ el.textContent = String(target); el._cnDone = target; return; }

  const from = Number.isFinite(el._cnDone) ? el._cnDone : 0;
  el._cnAnimating = true;
  const dur = 650, t0 = performance.now();
  const ease = t => 1 - Math.pow(1 - t, 3);   // easeOutCubic
  function step(now){
    const p = Math.min(1, (now - t0) / dur);
    el.textContent = String(Math.round(from + (target - from) * ease(p)));
    if(p < 1){ requestAnimationFrame(step); }
    else { el.textContent = String(target); el._cnAnimating = false; el._cnDone = target; }
  }
  requestAnimationFrame(step);
}

function _initCountUp(){
  ['stat-calls', 'stat-contacts'].forEach(id => {
    const el = document.getElementById(id);
    if(!el) return;
    el.classList.add('sk-num');         // показать скелетон до прихода данных
    const obs = new MutationObserver(() => _animateCount(el));
    obs.observe(el, { childList:true, characterData:true, subtree:true });
  });
}

// ── Скелетоны-заглушки в списках (до загрузки данных) ──
// loadRecentCalls()/loadTasks() затрут их своим innerHTML.
function _skeletonRows(n){
  let html = '';
  for(let i = 0; i < n; i++){
    html += `<div class="sk-row"><div class="sk-dot"></div><div class="sk-lines"><div class="sk-line"></div><div class="sk-line short"></div></div></div>`;
  }
  return html;
}
function _initSkeletons(){
  const calls = document.getElementById('recent-calls');
  const tasks = document.getElementById('tasks-list');
  if(calls) calls.innerHTML = _skeletonRows(3);
  if(tasks) tasks.innerHTML = _skeletonRows(3);
}

// ── «Пробуждение» агента при включении тумблера ──
// Дополнительный листенер (не заменяет toggleActive из init.js): даёт
// тактильный визуальный отклик — свечение тумблера + пульс статуса в шапке.
function _initAwaken(){
  const toggle = document.getElementById('active-toggle');
  if(!toggle) return;
  toggle.addEventListener('change', () => {
    if(!toggle.checked || MOTION_REDUCED) return;
    const wrap = toggle.closest('.toggle-wrap');
    if(wrap){
      wrap.classList.remove('agent-awaken');
      void wrap.offsetWidth;            // рестарт анимации
      wrap.classList.add('agent-awaken');
      setTimeout(() => wrap.classList.remove('agent-awaken'), 1000);
    }
    const dot = document.querySelector('#nav-agent-status i');
    if(dot){
      dot.classList.remove('status-pulse');
      void dot.offsetWidth;
      dot.classList.add('status-pulse');
      setTimeout(() => dot.classList.remove('status-pulse'), 1400);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  _initSkeletons();
  _initCountUp();
  _initAwaken();
});
