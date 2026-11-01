import React, { useEffect, useState } from 'react';
import { useReducedMotion } from 'motion/react';
import { useT } from '../i18n';
import Icon from './Icon';

// Анимированный «экран звонка» в hero. Четыре сцены проигрываются по кругу,
// реплики уже лежат в DOM (opacity: 0) и только проявляются — раскладка не прыгает.
// Порядок сцен перемешивается после монтирования, чтобы пререндер и гидрация совпали.
const SCENES = [
  { key: 'whatsapp', kind: 'chat', who: ['bot', 'user', 'bot', 'user'], tags: [[], [], ['doc'], ['crm', 'task']] },
  { key: 'salon', kind: 'in', who: ['user', 'bot', 'user', 'bot'], tags: [[], [], [], ['crm', 'sheets', 'telegram']] },
  { key: 'showroom', kind: 'out', who: ['bot', 'user', 'bot', 'user'], tags: [[], [], [], ['crm', 'task']] },
  { key: 'service', kind: 'out', who: ['bot', 'user', 'bot'], tags: [[], [], ['crm', 'sheets', 'task']] },
];

const SCENE_ICON = { in: 'phone-incoming', out: 'phone-outgoing', chat: 'message-circle' };
const STATUS_KEY = { in: 'status_incoming', out: 'status_outgoing', chat: 'status_message' };

const TAG_ICONS = { crm: 'square-check', sheets: 'layout-grid', telegram: 'send', task: 'calendar', doc: 'file-text', voice: 'mic' };

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
            <Icon name={SCENE_ICON[s.kind]} className="ic-sm" />
            {t(`call_card.scenes.${s.key}.label`)}
          </button>
        ))}
      </div>
      <div className="cc" aria-hidden="true">
        <div className="cc-head">
          <div className={`cc-avatar${scene.kind === 'chat' ? ' cc-avatar-chat' : ''}`}>
            {scene.kind === 'chat' ? (
              <Icon name="message-circle" />
            ) : (
              <span className="vf-wave">
                <i />
                <i />
                <i />
                <i />
                <i />
              </span>
            )}
          </div>
          <div className="cc-who">
            <div className="cc-name">{t(`call_card.scenes.${sceneKey}.name`)}</div>
            <div className="cc-sub">
              <span className="cc-live" />
              {t(`call_card.${STATUS_KEY[scene.kind]}`)} · {fmt(secs)}
            </div>
          </div>
          <span className="chip chip-success">
            <Icon name={scene.kind === 'chat' ? 'message-circle' : 'phone-call'} />
            {scene.kind === 'chat' ? t('call_card.in_chat') : t('call_card.on_line')}
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
            <Icon name={scene.kind === 'chat' ? 'message-circle' : 'phone'} className="ic-sm" />
            {scene.kind === 'chat' ? t('call_card.channel_whatsapp') : t('call_card.channel_phone')}
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
