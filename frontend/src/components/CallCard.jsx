import React, { useEffect, useState } from 'react';
import { useReducedMotion } from 'motion/react';
import { useT } from '../i18n';
import Icon from './Icon';
import ModelLogo, { MODELS } from './ModelLogo';

// Анимированный «экран звонка» в hero. Четыре сцены проигрываются по кругу,
// реплики уже лежат в DOM (opacity: 0) и только проявляются — раскладка не прыгает.
// Порядок сцен перемешивается после монтирования, чтобы пререндер и гидрация совпали.
const SCENES = [
  { key: 'salon', dir: 'in', model: 'gemini', who: ['user', 'bot', 'user', 'bot'], tags: [[], [], [], ['crm', 'sheets', 'telegram']] },
  { key: 'clinic', dir: 'in', model: 'fish', who: ['user', 'bot', 'user', 'bot'], tags: [[], [], [], ['crm', 'task']] },
  { key: 'showroom', dir: 'out', model: 'openai', who: ['bot', 'user', 'bot', 'user'], tags: [[], [], [], ['crm', 'task']] },
  { key: 'service', dir: 'out', model: 'grok', who: ['bot', 'user', 'bot'], tags: [[], [], ['crm', 'sheets', 'task']] },
];

const TAG_ICONS = { crm: 'square-check', sheets: 'file-text', telegram: 'send', task: 'calendar' };

const STEP = 1500;
const FIRST = 700;
const HOLD = 4500;

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;

function CallCard() {
  const { t } = useT();
  const reduce = useReducedMotion();
  const [order, setOrder] = useState(SCENES.map((s) => s.key));
  const [sceneKey, setSceneKey] = useState(SCENES[0].key);
  const [run, setRun] = useState(0);
  const [shown, setShown] = useState(0);
  const [secs, setSecs] = useState(0);

  const scene = SCENES.find((s) => s.key === sceneKey);
  const msgs = t(`call_card.scenes.${sceneKey}.msgs`);

  // Перемешиваем порядок только на клиенте
  useEffect(() => {
    const shuffled = shuffle(SCENES.map((s) => s.key));
    setOrder(shuffled);
    setSceneKey(shuffled[0]);
  }, []);

  // Проигрывание сцены
  useEffect(() => {
    if (reduce) {
      setShown(msgs.length);
      return undefined;
    }
    setShown(0);
    setSecs(0);
    const timers = msgs.map((_, i) => setTimeout(() => setShown(i + 1), FIRST + i * STEP));
    const next = setTimeout(() => {
      const idx = order.indexOf(sceneKey);
      setSceneKey(order[(idx + 1) % order.length]);
    }, FIRST + msgs.length * STEP + HOLD);
    const tick = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => {
      timers.forEach(clearTimeout);
      clearTimeout(next);
      clearInterval(tick);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneKey, run, reduce, order]);

  const activeTags = new Set();
  scene.tags.forEach((list, i) => {
    if (i < shown) list.forEach((tag) => activeTags.add(tag));
  });
  const allTags = [...new Set(scene.tags.flat())];

  const pick = (key) => {
    if (key === sceneKey) setRun((r) => r + 1);
    else setSceneKey(key);
  };

  return (
    <>
      <div className="cc-scenes" role="tablist" aria-label={t('call_card.scenes_aria')}>
        {SCENES.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={s.key === sceneKey}
            className={`cc-scene${s.key === sceneKey ? ' on' : ''}`}
            onClick={() => pick(s.key)}
          >
            <Icon name={s.dir === 'in' ? 'phone-incoming' : 'phone-outgoing'} className="ic-sm" />
            {t(`call_card.scenes.${s.key}.label`)}
          </button>
        ))}
      </div>
      <div className="cc" aria-hidden="true">
        <div className="cc-head">
          <div className="cc-avatar">
            <span className="vf-wave">
              <i />
              <i />
              <i />
              <i />
              <i />
            </span>
          </div>
          <div className="cc-who">
            <div className="cc-name">{t(`call_card.scenes.${sceneKey}.name`)}</div>
            <div className="cc-sub">
              <span className="cc-live" />
              {scene.dir === 'in' ? t('call_card.status_incoming') : t('call_card.status_outgoing')} · {fmt(secs)}
            </div>
          </div>
          <span className="chip chip-success">
            <Icon name="phone-call" />
            {t('call_card.on_line')}
          </span>
        </div>
        <div className="cc-msgs">
          {msgs.map((text, i) => (
            <div key={`${sceneKey}-${run}-${i}`} className={`cc-msg cc-msg-${scene.who[i]}${i < shown ? ' on' : ''}`}>
              <span>{text}</span>
            </div>
          ))}
        </div>
        <div className="cc-foot">
          <span className="cc-model">
            <ModelLogo code={scene.model} size={14} wrap={false} />
            {MODELS[scene.model].name}
          </span>
          <div className="cc-tags">
            {allTags.map((tag) => (
              <span key={tag} className={`cc-tag${activeTags.has(tag) ? ' on' : ''}`}>
                <Icon name={TAG_ICONS[tag]} className="ic-sm" />
                {t(`call_card.tags.${tag}`)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

export default CallCard;
