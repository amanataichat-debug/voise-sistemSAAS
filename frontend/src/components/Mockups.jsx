import React from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import ModelLogo, { MODELS } from './ModelLogo';
import { Stagger, Item } from './Reveal';
import { PHONE_DISPLAY } from './contacts';

// «Экраны кабинета» на лендинге — не скриншоты, а разметка из .frame и .m-*.
// Нечитаемый текст заменён серыми полосками .m-bar.
const NAV = [
  ['house', 'dashboard'],
  ['headset', 'agent'],
  ['audio-lines', 'assistants'],
  ['messages-square', 'conversations'],
  ['phone', 'telephony'],
  ['contact-round', 'crm'],
];

export function Frame({ active, title, children }) {
  const { t } = useT();
  return (
    <div className="frame">
      <div className="frame-side">
        <div className="frame-logo">
          <img src="/static/brand/logo.svg" alt="" width="20" height="20" />
          <span>{t('frame.brand')}</span>
        </div>
        <div className="frame-nav">
          {NAV.map(([icon, key]) => (
            <span key={key} className={`frame-nav-item${active === key ? ' on' : ''}`}>
              <Icon name={icon} className="ic-sm" />
              {t(`frame.nav.${key}`)}
            </span>
          ))}
        </div>
        <div className="frame-wallet">
          <span>{t('frame.plan_label')}</span>
          <b>{t('frame.plan_value')}</b>
        </div>
      </div>
      <div className="frame-main">
        <div className="frame-top">
          <b>{title}</b>
          <span className="frame-ava">{t('frame.avatar')}</span>
        </div>
        <div className="frame-body">{children}</div>
      </div>
    </div>
  );
}

export function MockAssistant() {
  const { t } = useT();
  const models = [
    ['gemini', 'key_own', true],
    ['openai', 'key_own', false],
    ['fish', 'key_platform', false],
    ['grok', 'key_own', false],
  ];
  return (
    <Frame active="assistants" title={t('frame.title_assistants')}>
      <Stagger className="m-editor" stagger={0.07} amount={0.3}>
        <Item className="m-editor-head" y={10}>
          <ModelLogo code="gemini" size={14} />
          <b>{t('mock.assistant_name')}</b>
          <span className="chip chip-success">
            <span className="dot dot-success" />
            {t('mock.assistant_status')}
          </span>
        </Item>
        <Item className="m-tabs" y={10}>
          {t('mock.tabs').map((tab, i) => (
            <span key={tab} className={i === 0 ? 'on' : undefined}>
              {tab}
            </span>
          ))}
        </Item>
        <Item className="m-field" y={10}>
          <label>{t('mock.greeting_label')}</label>
          <div className="m-input">{t('mock.greeting_value')}</div>
        </Item>
        <Item className="m-field" y={10}>
          <label>{t('mock.prompt_label')}</label>
          <div className="m-input m-textarea">
            {['92%', '78%', '85%', '40%'].map((w) => (
              <span key={w} className="m-bar" style={{ width: w }} />
            ))}
          </div>
        </Item>
        <Item className="m-field" y={10}>
          <label>{t('mock.model_label')}</label>
          <div className="m-models">
            {models.map(([code, key, on]) => (
              <div key={code} className={`m-model${on ? ' on' : ''}`}>
                <ModelLogo code={code} size={14} />
                <div>
                  <b>{MODELS[code].name}</b>
                  <span>{t(`mock.${key}`)}</span>
                </div>
                {on && <Icon name="circle-check" className="ic-sm" />}
              </div>
            ))}
          </div>
        </Item>
      </Stagger>
    </Frame>
  );
}

export function MockTelephony() {
  const { t } = useT();
  return (
    <Frame active="telephony" title={t('frame.title_telephony')}>
      <Stagger className="m-dialogs" stagger={0.08} amount={0.3}>
        <Item className="m-card" y={10}>
          <div className="m-card-head">
            <b>{t('mock.tel_line_title')}</b>
            <span className="chip chip-success">
              <span className="dot dot-success" />
              {t('mock.tel_line_status')}
            </span>
          </div>
          <div className="m-test-body">
            <div>
              <span className="m-label">{t('mock.tel_call_label')}</span>
              <span className="m-phone">{PHONE_DISPLAY}</span>
              <span className="m-sub">{t('mock.tel_answers')}</span>
            </div>
            <div className="m-timer">
              <b>{t('mock.tel_timer')}</b>
              <span>{t('mock.tel_timer_sub')}</span>
            </div>
          </div>
        </Item>
        <Item className="m-card" y={10}>
          <div className="m-card-head">
            <b>{t('mock.tel_my_numbers')}</b>
            <span className="btn btn-sm btn-primary">
              <Icon name="plus" className="ic-sm" />
              {t('mock.tel_buy')}
            </span>
          </div>
          <table className="m-table">
            <tbody>
              {t('mock.tel_rows').map(([num, city, chip, bound]) => (
                <tr key={num}>
                  <td className="mono">{num}</td>
                  <td>{city}</td>
                  <td>
                    <span className={`chip${bound ? ' chip-accent' : ''}`}>{chip}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Item>
        <Item className="m-prices" y={10}>
          {t('mock.tel_prices').map(([label, value]) => (
            <div key={label}>
              <span className="m-sub">{label}</span>
              <b>{value}</b>
            </div>
          ))}
        </Item>
      </Stagger>
    </Frame>
  );
}

export function MockDialogs() {
  const { t } = useT();
  const lines = t('mock.transcript');
  return (
    <Frame active="conversations" title={t('frame.title_dialogs')}>
      <Stagger className="m-dialogs" stagger={0.08} amount={0.3}>
        <Item className="m-card m-list" y={10}>
          {t('mock.dialogs').map(([time, src, dur, chip, tone]) => (
            <div key={time} className="m-row">
              <span className="m-sub">{time}</span>
              <b>{src}</b>
              <span className="mono muted">{dur}</span>
              <span className={`chip${tone ? ` chip-${tone}` : ''}`}>{chip}</span>
            </div>
          ))}
        </Item>
        <Item className="m-card" y={10}>
          <div className="m-card-head">
            <b>{t('mock.transcript_title')}</b>
            <span className="chip">
              <Icon name="play" className="ic-sm" />
              {t('mock.record_chip')}
            </span>
          </div>
          {lines.map((line, i) => (
            <div key={line} className={`m-line${i % 2 ? ' bot' : ''}`}>
              <span className={`m-ava${i % 2 ? ' bot' : ''}`}>
                {i % 2 ? <Icon name="bot" className="ic-sm" /> : t('mock.you_short')}
              </span>
              <span>{line}</span>
            </div>
          ))}
          <div className="m-result">
            <Icon name="circle-check" className="ic-sm" />
            {t('mock.dialogs_result')}
          </div>
        </Item>
      </Stagger>
    </Frame>
  );
}

export function MockCrm() {
  const { t } = useT();
  const names = t('mock.crm_names');
  const cols = t('mock.crm_cols');
  const groups = [
    [0, 1],
    [2, 3, 4],
    [5, 6],
    [7],
  ];
  return (
    <Frame active="crm" title={t('frame.title_crm')}>
      <Stagger className="m-kanban" stagger={0.07} amount={0.3}>
        {groups.map((idx, c) => (
          <Item key={cols[c]} className="m-col" y={10}>
            <div className="m-col-head">
              <b>{cols[c]}</b>
              <span>{idx.length}</span>
            </div>
            {idx.map((n) => (
              <div key={n} className={`m-contact${n === 2 ? ' on' : ''}`}>
                <b>{names[n]}</b>
                <span className="mono muted">{t('mock.crm_phone')}</span>
                {n === 2 && (
                  <div className="m-facts">
                    {t('mock.crm_facts').map((f) => (
                      <span key={f} className="chip chip-outline">
                        {f}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </Item>
        ))}
      </Stagger>
    </Frame>
  );
}

export function MockKnowledge() {
  const { t } = useT();
  return (
    <Frame active="assistants" title={t('frame.title_knowledge')}>
      <Stagger className="m-kb" stagger={0.08} amount={0.3}>
        <Item className="m-card m-list" y={10}>
          {t('mock.kb_files').map(([name, meta]) => (
            <div key={name} className="m-row">
              <Icon name="file-text" className="ic-sm" />
              <b>{name}</b>
              <span className="m-sub">{meta}</span>
              <span className="chip chip-success">{t('mock.kb_status')}</span>
            </div>
          ))}
          <div className="m-row m-drop">
            <Icon name="upload" className="ic-sm" />
            <span className="muted">{t('mock.kb_drop')}</span>
          </div>
        </Item>
        <Item className="m-card" y={10}>
          <div className="m-card-head">
            <b>{t('mock.kb_check_title')}</b>
          </div>
          <div className="m-line">
            <span className="m-ava">?</span>
            <span>{t('mock.kb_check_q')}</span>
          </div>
          <div className="m-line bot">
            <span className="m-ava bot">
              <Icon name="bot" className="ic-sm" />
            </span>
            <span>{t('mock.kb_check_a')}</span>
          </div>
        </Item>
      </Stagger>
    </Frame>
  );
}

export function MockAgent() {
  const { t } = useT();
  const chat = t('mock.agent_chat');
  return (
    <Frame active="agent" title={t('frame.title_agent')}>
      <Stagger className="m-agent" stagger={0.1} amount={0.25}>
        <Item className="m-card m-chat" y={10}>
          <div className="m-card-head">
            <b>{t('mock.agent_chat_title')}</b>
            <span className="chip">
              <Icon name="messages-square" className="ic-sm" />
              {t('mock.agent_chat_chip')}
            </span>
          </div>
          {chat.map((line, i) => (
            <div key={line} className={`m-line${i % 2 ? ' bot' : ''}`}>
              <span className={`m-ava${i % 2 ? ' bot' : ''}`}>
                {i % 2 ? <Icon name="headset" className="ic-sm" /> : t('mock.agent_you').slice(0, 2)}
              </span>
              <span>{line}</span>
            </div>
          ))}
        </Item>
        <div className="m-agent-side">
          <Item className="m-card" y={10}>
            <div className="m-card-head">
              <b>{t('mock.agent_tasks_title')}</b>
              <span className="chip chip-accent">{t('mock.agent_tasks_chip')}</span>
            </div>
            {t('mock.agent_tasks').map(([time, task]) => (
              <div key={time} className="m-task">
                <span className="mono">{time}</span>
                <span>{task}</span>
              </div>
            ))}
          </Item>
          <Item className="m-card m-memory" y={10}>
            <div className="m-card-head">
              <b>{t('mock.agent_card_title')}</b>
              <span className="chip chip-accent">{t('mock.agent_card_chip')}</span>
            </div>
            {t('mock.agent_card').map(([label, text]) => (
              <p key={label}>
                <b>{label}</b> {text}
              </p>
            ))}
          </Item>
        </div>
      </Stagger>
    </Frame>
  );
}
