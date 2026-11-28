/* ============================================================================
 * agent/onboarding.js — Обучающая карусель перед мастером создания агента.
 * Вариант A: 5 обязательных слайдов про суть автономного агента, который
 * заменяет первую линию продаж (что это · Pre-Call · живой разговор ·
 * Post-Call + память · управление словами). Показывается ВСЕГДА при создании
 * (с кнопкой «Пропустить»), перед wizard.showWizard() → renderWizard().
 *
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

let obStep = 0;
let obOnDone = null;
const OB_TOTAL = 5;

// Иллюстрации — стилизованные мокапы реальных экранов Voksy AI (без картинок).
const OB_SLIDES = [
  {
    badge: 'Автономный сотрудник',
    title: 'Это не виджет и не автоответчик',
    text: 'Агент Voksy AI сам звонит по вашей базе, ведёт живой разговор, квалифицирует контакт и решает что делать дальше. Он заменяет первую линию продаж — а вы только ставите цели.',
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

// Салон (Voksy Beauty Pro = Profi под салон, 27.11.2026): тот же агент и те же 5 шагов, но о записи,
// напоминаниях и возврате клиентов, а не об обзвоне базы продаж. Выбор — в момент показа слайда:
// salon-mode.js может понять, что это салон, уже после старта карусели.
const OB_SLIDES_SALON = [
  {
    badge: 'ИИ-администратор салона',
    title: 'Ведёт клиентов сам — в переписке и по телефону',
    text: 'Агент отвечает клиентам салона в WhatsApp, Instagram и Telegram, записывает к мастеру, переносит и напоминает о визите. И сам звонит и пишет первым — подтвердить запись, вернуть тех, кто давно не был, заполнить пустые окна.',
    art: () => `<div class="ob-art ob-art-intro">
      <div class="ob-orb"><i class="fas fa-headset"></i><span class="ob-pulse"></span></div>
      <div class="ob-chips">
        <span class="ob-chip"><i class="fas fa-calendar"></i> Записывает</span>
        <span class="ob-chip"><i class="fas fa-comments"></i> Отвечает</span>
        <span class="ob-chip"><i class="fas fa-phone-volume"></i> Звонит сам</span>
      </div>
    </div>`,
  },
  {
    badge: 'Запись',
    title: 'Записывает по вашему журналу',
    text: 'Агент видит свободное время мастеров, услуги и цены из вашего прайса. Перед записью присылает клиенту итог и записывает только после его «да» — запись сразу появляется в журнале.',
    art: () => `<div class="ob-mock ob-talk">
      <div class="ob-bubble client">Можно на маникюр в субботу?</div>
      <div class="ob-bubble agent">В субботу у Айгуль свободно 11:00 и 15:30. Записать вас?</div>
      <div class="ob-bubble client">Давайте 15:30</div>
      <div class="ob-bubble agent">Записала ✅ Суббота, 15:30, маникюр у Айгуль</div>
    </div>`,
  },
  {
    badge: 'Звонки',
    title: 'Звонит как живой администратор',
    text: 'По вашей инструкции агент сам звонит клиентам: подтвердить завтрашнюю запись, перенести, пригласить на повтор услуги. Говорит по-русски и по-кыргызски, звонок разбирает и записывает итог в карточку клиента.',
    art: () => `<div class="ob-mock ob-talk">
      <div class="ob-bubble agent">Айжан, здравствуйте! Это салон «Айдана». Напоминаю: завтра в 15:30 у вас маникюр. Всё в силе?</div>
      <div class="ob-bubble client">Да, приду</div>
      <div class="ob-bubble agent">Отлично, ждём вас! <span class="ob-kb"><i class="fas fa-check"></i> запись подтверждена</span></div>
    </div>`,
  },
  {
    badge: 'Память',
    title: 'Помнит каждого клиента',
    text: 'После каждой переписки и звонка агент обновляет карточку клиента: любимый мастер, когда был последний раз, что не понравилось. Поэтому пишет и звонит к месту, а не по шаблону.',
    art: () => `<div class="ob-mock">
      <div class="ob-mock-head"><i class="fas fa-user"></i> Карточка клиента · автоматически</div>
      <div class="ob-row"><span class="ob-k">Любимый мастер</span><span class="ob-v">Айгуль · маникюр</span></div>
      <div class="ob-row"><span class="ob-k">Последний визит</span><span class="ob-v">45 дней назад</span></div>
      <div class="ob-row"><span class="ob-k">Помнит</span><span class="ob-v">удобно после 18:00, по субботам</span></div>
      <div class="ob-row"><span class="ob-k">Следующий шаг</span><span class="ob-v">пригласить на повтор</span></div>
    </div>`,
  },
  {
    badge: 'Управление словами',
    title: 'Командуете обычными словами',
    text: 'В чате с агентом вы пишете как администратору: «кто завтра не подтвердил запись — позвони им», «кого давно не было». Отчёты, записи и клиенты — в одном месте, здесь же настройки агента: инструкции, голос, номер, мессенджеры.',
    art: () => `<div class="ob-mock ob-talk">
      <div class="ob-bubble client">Кто завтра не подтвердил запись?</div>
      <div class="ob-bubble agent">Трое: Айжан, Нурлан и Мээрим. Позвонить им утром?</div>
      <div class="ob-bubble client">Да, в 10:00</div>
      <div class="ob-bubble agent">Поставила 3 звонка на 10:00 ✅</div>
    </div>`,
  },
];

function obSalon(){
  if(window.AGENT_SALON) return true;
  try{ const b = JSON.parse(localStorage.getItem('vf_business') || 'null'); return !!(b && b.beauty && (!b.modules || b.modules.booking !== false)); }
  catch(e){ return false; }
}
function obSlides(){ return obSalon() ? OB_SLIDES_SALON : OB_SLIDES; }

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
  const slides = obSlides();
  const s = slides[obStep];

  const dots = slides.map((_, i) =>
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
