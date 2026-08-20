// VoiceSystem — scroll-reveal для страниц ЛК (чисто презентационный, без API).
// Элементы с классом .vs-reveal плавно появляются при входе во вьюпорт (один раз).
(function () {
  'use strict';
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var els = document.querySelectorAll('.vs-reveal');
  if (!els.length || !('IntersectionObserver' in window)) {
    els.forEach && els.forEach(function (el) { el.classList.add('is-visible'); });
    return;
  }
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target); // once: true
      }
    });
  }, { threshold: 0.15 });
  els.forEach(function (el) { observer.observe(el); });
})();
