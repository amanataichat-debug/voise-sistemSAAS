import React from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import SectionHead from './SectionHead';
import { Reveal, Stagger, Item, Parallax } from './Reveal';
import { MockAgent } from './Mockups';

function AgentSection({ onOpenModal }) {
  const { t } = useT();
  const flowWho = ['orch', 'voice', 'orch'];
  return (
    <section className="sec sec-tint tint-lavender" id="agent">
      <div className="lp-container">
        <SectionHead index="02" title={t('agent_section.title')} lead={t('agent_section.lead')} />

        <Parallax className="agent-mock" amount={30}>
          <Reveal className="stack" y={28}>
            <MockAgent />
          </Reveal>
        </Parallax>

        <div className="agent-grid">
          <Reveal className="agent-split" x={-16} y={0}>
            <div className="split-col">
              <h3>{t('agent_section.you_title')}</h3>
              <ul className="rule-list">
                {t('agent_section.you').map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </div>
            <div className="split-col">
              <h3>{t('agent_section.agent_title')}</h3>
              <ul className="rule-list">
                {t('agent_section.agent').map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </div>
          </Reveal>

          <div className="agent-day">
            <Reveal y={12}>
              <h3>{t('agent_section.day_title')}</h3>
              <p className="muted">{t('agent_section.day_sub')}</p>
            </Reveal>
            <Stagger as="ol" className="day" stagger={0.1}>
              {t('agent_section.day').map(([time, title, text]) => (
                <Item as="li" key={time} x={16} y={0}>
                  <span className="day-time">{time}</span>
                  <span>
                    <b>{title}</b> {text}
                  </span>
                </Item>
              ))}
            </Stagger>
          </div>
        </div>

        <Reveal className="brains" y={16}>
          <div className="brains-head">
            <h3>{t('agent_section.brains_title')}</h3>
            <p>{t('agent_section.brains_lead')}</p>
          </div>
          <div className="brains-flow">
            {t('agent_section.flow').map(([stage, text], i) => (
              <div key={stage} className="flow-step">
                <span className={`flow-who ${flowWho[i]}`}>
                  <Icon name={flowWho[i] === 'orch' ? 'brain' : 'audio-lines'} className="ic-sm" />
                  {flowWho[i] === 'orch' ? t('agent_section.role_orchestrator') : t('agent_section.role_voice')}
                </span>
                <b>{stage}</b>
                <p>{text}</p>
              </div>
            ))}
          </div>
        </Reveal>

        <Reveal className="sec-actions" y={12}>
          <button type="button" className="btn btn-primary btn-lg" onClick={() => onOpenModal('register')}>
            {t('agent_section.cta')}
            <Icon name="arrow-right" />
          </button>
          <span className="muted">{t('agent_section.cta_note')}</span>
        </Reveal>
      </div>
    </section>
  );
}

export default AgentSection;
