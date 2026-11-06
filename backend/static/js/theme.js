/* ============================================================================
 * /static/js/theme.js — кабинет Voksy AI только в светлой теме.
 *
 * Тёмная тема убрана 06.11.2026 (решение владельца: смотрелась плохо). Файл
 * оставлен, потому что его подключают страницы кабинета: он снимает
 * data-theme="dark" (если остался от старого выбора), стирает сохранённый
 * выбор и прячет старые кнопки-переключатели. CSS-правила [data-theme="dark"]
 * в стилях без этого атрибута не срабатывают.
 * ========================================================================== */
(function(){
  'use strict';
  try { localStorage.removeItem('vs_theme'); } catch(e){}
  var root = document.documentElement;
  root.removeAttribute('data-theme');
  root.style.removeProperty('background-color');
  root.style.removeProperty('color-scheme');
  function hideToggles(){
    document.querySelectorAll('.theme-toggle, #theme-toggle').forEach(function(b){ b.remove(); });
  }
  window.setTheme = function(){};
  window.toggleTheme = function(){};
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', hideToggles);
  else hideToggles();
})();
