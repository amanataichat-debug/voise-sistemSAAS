/* ============================================================================
 * agent/onboarding.js — Обучающая карусель перед мастером создания агента.
 * Вариант A: 5 обязательных слайдов про суть автономного агента, который
 * заменяет первую линию продаж (что это · Pre-Call · живой разговор ·
 * Post-Call + память · управление словами). Показывается ВСЕГДА при создании
 * (с кнопкой «Пропустить»), перед wizard.showWizard() → renderWizard().
 *
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

let obStep = 0;
let obOnDone = null;
const OB_TOTAL = 5;

// Иллюстрации — стилизованные мокапы реальных экранов Voicyfy (без картинок).
const OB_SLIDES = [
  {
    badge: 'Автономный сотрудник',
    title: 'Это не виджет и не автоответчик',
    text: 'Агент Voicyfy сам звонит по вашей базе, ведёт живой разговор, квалифицирует контакт и решает что делать дальше. Он заменяет первую линию продаж — а вы только ставите цели.',
    art: () => `<div class="ob-art ob-art-intro">
      <div class="ob-orb"><i class="fas fa-headset"></i><span class="ob-pulse"></span></div>
      <div class="ob-chips">
        <span class="ob-chip"><i class="fas fa-phone-volume"></i> Звонит сам</span>
        <span class="ob-chip"><i class="fas fa-comments"></i> Ведёт диалог</span>
        <span class="ob-chip"><i class="fas fa-filter"></i> Квалифицирует</span>
      </div>
    </div>`,
  },
  {
    badge: 'Pre-Call',
    title: 'Думает перед каждым звонком',
    text: 'Перед звонком оркестратор готовит стратегию: первую фразу, тон и тактику — опираясь на память о контакте и прошлые разговоры. Каждый звонок персональный, а не скрипт по бумажке.',
    art: () => `<div class="ob-mock">
      <div class="ob-mock-head"><i class="fas fa-brain"></i> Стратегия звонка · Pre-Call</div>
      <div class="ob-row"><span class="ob-k">Первая фраза</span><span class="ob-v">«Иван, добрый день! Это Алина из…»</span></div>
      <div class="ob-row"><span class="ob-k">Тон</span><span class="ob-v">деловой, без давления</span></div>
      <div class="ob-row"><span class="ob-k">Тактика</span><span class="ob-v">напомнить о прошлом интересе к CRM</span></div>
      <div class="ob-row"><span class="ob-k">Помнит</span><span class="ob-v">2 звонка · просил перезвонить в среду</span></div>
    </div>`,
  },
  {
    badge: 'Живой разговор',
    title: 'Говорит как человек',
    text: 'Голосовой агент ведёт настоящий телефонный диалог в реальном времени, при необходимости находит ответы в вашей Базе знаний и сам завершает звонок.',
    art: () => `<div class="ob-mock ob-talk">
      <div class="ob-bubble agent">Иван, удобно сейчас пару минут?</div>
      <div class="ob-bubble client">Да, слушаю</div>
      <div class="ob-bubble agent">Подскажу по тарифам <span class="ob-kb"><i class="fas fa-database"></i> ищу в Базе знаний</span></div>
    </div>`,
  },
  {
    badge: 'Post-Call + Память',
    title: 'Сам учится после разговора',
    text: 'После звонка агент разбирает диалог: обновляет память о контакте, двигает его по воронке и планирует перезвон. Каждый контакт со временем известен агенту всё лучше — без вашего участия.',
    art: () => `<div class="ob-mock">
      <div class="ob-mock-head"><i class="fas fa-rotate"></i> После звонка · автоматически</div>
      <div class="ob-row"><span class="ob-k">Итог</span><span class="ob-v">заинтересован, ждёт КП</span></div>
      <div class="ob-row"><span class="ob-k">Память</span><span class="ob-v">+ ЛПР, бюджет до 50к, звонить после 15:00</span></div>
      <div class="ob-row"><span class="ob-k">Перезвон</span><span class="ob-v">запланирован на пятницу 16:00</span></div>
      <div class="ob-funnel">
        <span class="ob-stage">new</span><i class="fas fa-arrow-right-long"></i><span class="ob-stage on">active</span><i class="fas fa-arrow-right-long"></i><span class="ob-stage">success</span>
      </div>
    </div>`,
  },
  {
    badge: 'Управление словами',
    title: 'Командуете на простом языке',
    text: 'Через чат вы управляете всей базой обычными словами: «обзвони всех новых завтра с 10», «кому не дозвонились». Контакты, задачи и воронка ведутся автоматически и принадлежат только этому агенту.',
    art: () => `<div class="ob-mock ob-talk">
      <div class="ob-bubble client">Обзвони всех новых завтра с 10:00</div>
      <div class="ob-bubble agent">Готово — запланировал 12 звонков, интервал 15 мин ✅</div>
      <div class="ob-bubble client">Кому не дозвонились на этой неделе?</div>
      <div class="ob-bubble agent">7 контактов в очереди на перезвон</div>
    </div>`,
  },
];

/**
 * Запустить обучающий модуль. onDone вызывается после прохождения/пропуска —
 * туда передаётся открытие реального мастера (renderWizard через openWizardSteps).
 */
function startOnboarding(onDone){
  obOnDone = (typeof onDone === 'function') ? onDone : null;
  obStep = 0;
  const ls = document.getElementById('loading-screen');
  if(ls) ls.classList.add('hidden');
  document.getElementById('onboarding-overlay').classList.remove('hidden');
  renderOnboarding();
}

function renderOnboarding(){
  const s = OB_SLIDES[obStep];

  const dots = OB_SLIDES.map((_, i) =>
    `<span class="ob-dot ${i===obStep?'on':''} ${i<obStep?'done':''}"></span>`
  ).join('');

  const isLast = obStep === OB_TOTAL - 1;
  const backBtn = obStep > 0
    ? `<button class="btn btn-secondary" onclick="obBack()"><i class="fas fa-arrow-left"></i> Назад</button>`
    : `<span></span>`;
  const nextBtn = isLast
    ? `<button class="btn btn-primary" onclick="obNext()"><i class="fas fa-rocket"></i> Создать агента</button>`
    : `<button class="btn btn-primary" onclick="obNext()">Далее <i class="fas fa-arrow-right"></i></button>`;

  document.getElementById('ob-content').innerHTML = `
    <div class="ob-stage-art">${s.art()}</div>
    <div class="ob-badge">${s.badge}</div>
    <h2 class="ob-title">${s.title}</h2>
    <p class="ob-text">${s.text}</p>`;
  document.getElementById('ob-dots').innerHTML = dots;
  document.getElementById('ob-actions').innerHTML = backBtn + nextBtn;
}

function obNext(){
  if(obStep < OB_TOTAL - 1){ obStep++; renderOnboarding(); }
  else { finishOnboarding(); }
}
function obBack(){ if(obStep > 0){ obStep--; renderOnboarding(); } }
function obSkip(){ finishOnboarding(); }

function finishOnboarding(){
  document.getElementById('onboarding-overlay').classList.add('hidden');
  const cb = obOnDone; obOnDone = null;
  if(cb) cb();
}
