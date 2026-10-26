/* ============================================================================
 * /static/js/theme.js — Переключатель тёмной темы (кабинет VoksiAI)
 *
 * CSS-токены темной темы (--vf-*) УЖЕ определены в voksiai.css под селектором
 * [data-theme="dark"] на <html>. Этот модуль:
 *   - Применяет сохранённую тему (localStorage.vs_theme) при загрузке страницы.
 *     Чтобы избежать «моргания» (flash of wrong theme), ставим data-theme до
 *     первой отрисовки — для этого в <head> каждой страницы кабинета есть
 *     inline-снипет (ищи "theme-bootstrap"). Этот файл только для кнопки.
 *   - Выставляет кнопку-переключатель (создаёт элемент .theme-toggle или
 *     берёт существующий #theme-toggle) и переключает тему по клику.
 *   - Поддерживает три состояния: 'light' | 'dark' | 'system'. По клику
 *     циклично переключает light → dark → light (чистая обратимая UX).
 *     "system" применяется автоматически при первом заходе и реагирует на
 *     изменение системной темы.
 *
 * Хранилище: localStorage.vs_theme = 'light' | 'dark' | 'system'.
 * ========================================================================== */

(function(){
  'use strict';

  const KEY = 'vs_theme';
  const mql = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function readPref(){
    try { return localStorage.getItem(KEY) || 'system'; } catch(e){ return 'system'; }
  }
  function writePref(v){
    try { localStorage.setItem(KEY, v); } catch(e){}
  }
  function resolved(pref){
    if(pref === 'dark') return 'dark';
    if(pref === 'light') return 'light';
    return (mql && mql.matches) ? 'dark' : 'light';
  }
  function apply(pref){
    const r = resolved(pref);
    if(r === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    else document.documentElement.removeAttribute('data-theme');
    updateButtons(r);
    try { document.dispatchEvent(new CustomEvent('vs:themechange', { detail: { pref: pref, resolved: r } })); } catch(e){}
  }
  function updateButtons(r){
    document.querySelectorAll('.theme-toggle, #theme-toggle').forEach(btn => {
      btn.setAttribute('aria-pressed', r === 'dark' ? 'true' : 'false');
      btn.title = r === 'dark' ? 'Светлая тема' : 'Тёмная тема';
      // Иконка — одна FA, меняется классом fa-moon/fa-sun
      const i = btn.querySelector('i');
      if(i){
        i.classList.remove('fa-moon','fa-sun');
        i.classList.add(r === 'dark' ? 'fa-sun' : 'fa-moon');
      }
    });
  }

  function toggle(){
    // Циклично light ↔ dark (без "system" — проще для пользователя)
    const current = resolved(readPref());
    const next = current === 'dark' ? 'light' : 'dark';
    writePref(next);
    apply(next);
  }
  window.setTheme = function(v){ writePref(v); apply(v); };
  window.toggleTheme = toggle;

  function wireButtons(){
    document.querySelectorAll('.theme-toggle, #theme-toggle').forEach(btn => {
      if(btn.dataset.vsBound) return;
      btn.dataset.vsBound = '1';
      btn.addEventListener('click', toggle);
    });
    // Обновим внешний вид (если кнопки появились после начальной отрисовки)
    updateButtons(resolved(readPref()));
  }

  // Реагируем на смену системной темы, только если пользователь не задал явно
  if(mql && mql.addEventListener){
    mql.addEventListener('change', () => {
      if(readPref() === 'system') apply('system');
    });
  }

  // Первое применение + подключение кнопок
  apply(readPref());
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', wireButtons);
  } else {
    wireButtons();
  }
})();
