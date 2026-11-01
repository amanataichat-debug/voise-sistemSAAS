import React, { useState } from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import SectionHead from './SectionHead';
import { Reveal, Stagger, Item, Parallax } from './Reveal';

// Телефон с четырьмя сценами: звонок, первое сообщение, голосовое, КП под клиента.
// Сцены переключаются сами по окончании CSS-анимации полоски прогресса (без таймеров),
// на наведении полоска ставится на паузу. Реплики «приезжают» по очереди (--i).
const AUTO_MS = 7000;
const WAVE = [30, 56, 42, 78, 52, 90, 46, 68, 36, 82, 54, 64, 32, 74, 44, 58, 40, 70, 34, 60];

function Bubble({ who = 'bot', i = 0, children }) {
  return (
    <div className={`ph-b ph-b-${who}`} style={{ '--i': i }}>
      <span>{children}</span>
    </div>
  );
}

function VoiceBubble({ who, i, time, hint }) {
  return (
    <div className={`ph-b ph-b-${who}`} style={{ '--i': i }}>
      <span className="ph-voice">
        <span className="ph-voice-play">
          <Icon name="play" className="ic-sm" />
        </span>
        <span className="ph-voice-wave">
          {WAVE.map((h, k) => (
            <i key={k} style={{ height: `${h}%`, animationDelay: `${(k % 7) * 0.1}s` }} />
          ))}
        </span>
        <span className="ph-voice-time">{time}</span>
      </span>
      {hint ? <em className="ph-hint">{hint}</em> : null}
    </div>
  );
}

function Phone({ name, sub, channel, wave, children }) {
  return (
    <div className="ph" aria-hidden="true">
      <div className="ph-notch" />
      <div className={`ph-head${wave ? ' ph-head-call' : ''}`}>
        <span className="ph-ava">
          {wave ? (
            <span className="vf-wave">
              <i />
              <i />
              <i />
              <i />
              <i />
            </span>
          ) : (
            <Icon name="user-round" />
          )}
        </span>
        <span className="ph-who">
          <b>{name}</b>
          <span>
            <span className="cc-live" />
            {sub}
          </span>
        </span>
        <span className="ph-channel">
          <Icon name={wave ? 'phone-call' : 'message-circle'} className="ic-sm" />
          {channel}
        </span>
      </div>
      <div className={`ph-body${wave ? ' ph-body-call' : ''}`}>{children}</div>
      {!wave && (
        <div className="ph-input">
          <span />
          <Icon name="mic" className="ic-sm" />
        </div>
      )}
    </div>
  );
}

function SceneCall({ t }) {
  const lines = t('showcase.call_lines');
  const who = ['bot', 'user', 'bot', 'user'];
  return (
    <Phone name={t('showcase.call_name')} sub={t('showcase.call_sub')} channel={t('call_card.channel_phone')} wave>
      {lines.map((line, i) => (
        <Bubble key={line} who={who[i]} i={i}>
          {line}
        </Bubble>
      ))}
      <div className="ph-result" style={{ '--i': lines.length }}>
        <Icon name="circle-check" className="ic-sm" />
        {t('showcase.call_result')}
      </div>
    </Phone>
  );
}

function SceneChat({ t }) {
  const lines = t('showcase.chat_lines');
  const who = ['bot', 'user', 'bot', 'user'];
  return (
    <Phone name={t('showcase.chat_name')} sub={t('call_card.in_chat')} channel={t('showcase.chat_channel')}>
      <div className="ph-note" style={{ '--i': 0 }}>
        <Icon name="sparkles" className="ic-sm" />
        {t('showcase.chat_note')}
      </div>
      {lines.map((line, i) => (
        <Bubble key={line} who={who[i]} i={i + 1}>
          {line}
        </Bubble>
      ))}
    </Phone>
  );
}

function SceneVoice({ t }) {
  return (
    <Phone name={t('showcase.chat_name')} sub={t('call_card.in_chat')} channel={t('showcase.chat_channel')}>
      <VoiceBubble who="user" i={0} time={t('showcase.voice_user')} />
      <div className="ph-transcript" style={{ '--i': 1 }}>
        {t('showcase.voice_client_text')}
      </div>
      <div className="ph-note" style={{ '--i': 2 }}>
        <Icon name="sparkles" className="ic-sm" />
        {t('showcase.voice_hint')}
      </div>
      <VoiceBubble who="bot" i={3} time={t('showcase.voice_bot')} hint={t('showcase.voice_label')} />
      <div className="ph-transcript ph-transcript-bot" style={{ '--i': 4 }}>
        {t('showcase.voice_transcript')}
      </div>
    </Phone>
  );
}

function SceneDoc({ t }) {
  const rows = t('showcase.doc_rows');
  return (
    <Phone name={t('showcase.chat_name')} sub={t('call_card.in_chat')} channel={t('showcase.chat_channel')}>
      <Bubble who="bot" i={0}>
        {t('showcase.doc_msg')}
      </Bubble>
      <div className="ph-b ph-b-bot" style={{ '--i': 1 }}>
        <span className="ph-file">
          <span className="ph-file-ic">PDF</span>
          <span className="ph-file-name">
            <b>{t('showcase.doc_file')}</b>
            <span>{t('showcase.doc_meta')}</span>
          </span>
        </span>
      </div>
      <div className="ph-sheet" style={{ '--i': 2 }}>
        <div className="ph-sheet-top">
          <span>{t('showcase.doc_brand')}</span>
          <b>{t('showcase.doc_title')}</b>
          <em>{t('showcase.doc_for')}</em>
        </div>
        <div className="ph-sheet-rows">
          {rows.map(([name, price]) => (
            <div key={name}>
              <span>{name}</span>
              <b>{price}</b>
            </div>
          ))}
        </div>
        <div className="ph-sheet-total">{t('showcase.doc_total')}</div>
        <div className="ph-sheet-next">
          <Icon name="calendar" className="ic-sm" />
          {t('showcase.doc_next')}
        </div>
      </div>
    </Phone>
  );
}

const SCENES = [SceneCall, SceneChat, SceneVoice, SceneDoc];

function AgentShowcase({ onOpenModal }) {
  const { t } = useT();
  const [tab, setTab] = useState(0);
  const tabs = t('showcase.tabs');
  const safe = t('showcase.safe');
  const Scene = SCENES[tab];

  const next = () => setTab((v) => (v + 1) % tabs.length);

  return (
    <section className="sec sec-tint tint-lavender" id="agent">
      <div className="lp-container">
        <SectionHead index="02" title={t('showcase.title')} lead={t('showcase.lead')} />

        <div className="sc">
          <Reveal className="sc-tabs" x={-16} y={0} role="tablist" aria-label={t('showcase.tabs_aria')}>
            {tabs.map(([icon, title, text], i) => (
              <button
                key={title}
                type="button"
                role="tab"
                aria-selected={tab === i}
                className={`sc-tab${tab === i ? ' on' : ''}`}
                onClick={() => setTab(i)}
              >
                <span className="sc-tab-ic">
                  <Icon name={icon} />
                </span>
                <span className="sc-tab-text">
                  <b>{title}</b>
                  <span>{text}</span>
                </span>
                {tab === i ? (
                  <i
                    key={`p-${tab}`}
                    className="sc-progress"
                    style={{ animationDuration: `${AUTO_MS}ms` }}
                    onAnimationEnd={next}
                  />
                ) : null}
              </button>
            ))}
          </Reveal>

          <Parallax className="sc-stage" amount={22}>
            <Reveal className="sc-phone" y={26} delay={0.1}>
              <div className="sc-glow" aria-hidden="true" />
              <div className="sc-scene" key={tab}>
                <Scene t={t} />
              </div>
            </Reveal>
          </Parallax>
        </div>

        <Reveal className="safe" y={14}>
          <h3>{t('showcase.safe_title')}</h3>
          <Stagger as="ul" className="safe-list" stagger={0.07}>
            {safe.map((s) => (
              <Item as="li" key={s} y={12}>
                <Icon name="shield-check" className="ic-sm" />
                {s}
              </Item>
            ))}
          </Stagger>
        </Reveal>

        <Reveal className="sec-actions" y={12}>
          <button type="button" className="btn btn-primary btn-lg" onClick={() => onOpenModal('register')}>
            {t('showcase.cta')}
            <Icon name="arrow-right" />
          </button>
          <span className="muted">{t('showcase.cta_note')}</span>
        </Reveal>
      </div>
    </section>
  );
}

export default AgentShowcase;
