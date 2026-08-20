import React from 'react';
import { useT } from '../i18n';

const CheckIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <circle cx="8" cy="8" r="8" fill="var(--vs-accent-soft)"/>
    <path d="M5 8l2 2 4-4"
      stroke="var(--vs-accent)" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

const ChipIcon = () => (
  <svg width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
    <rect x="6" y="6" width="12" height="12" rx="2" />
    <rect x="10" y="10" width="4" height="4" />
    <path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" />
  </svg>
);

const PhoneIcon = () => (
  <svg width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
    <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 014.69 15a19.79 19.79 0 01-3.07-8.67A2 2 0 013.6 4.22h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L7.91 11.8a16 16 0 006.29 6.29l1.87-1.87a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 18.92z" />
  </svg>
);

function AgentSection({ onOpenModal }) {
  const { t } = useT();

  return (
    <section className="section agent-section" id="agent">
      <div className="section-inner">
        <div className="s-head rev">
          <span className="s-label">{t('agent.label')}</span>
          <h2 className="s-title">{t('agent.title1')}<span className="gt">{t('agent.title2')}</span></h2>
          <p className="s-desc">{t('agent.desc')}</p>
        </div>

        {/* Два мозга */}
        <div className="agent-brains">
          <div className="agent-brain rev">
            <div className="icon-box ib-blue"><ChipIcon /></div>
            <h3>{t('agent.orch.title')}</h3>
            <p className="agent-brain-role">{t('agent.orch.role')}</p>
            <ul className="show-feats">
              <li><CheckIcon /> <span>{t('agent.orch.f1')}</span></li>
              <li><CheckIcon /> <span>{t('agent.orch.f2')}</span></li>
              <li><CheckIcon /> <span>{t('agent.orch.f3')}</span></li>
              <li><CheckIcon /> <span>{t('agent.orch.f4')}</span></li>
            </ul>
          </div>
          <div className="agent-brain rev d1">
            <div className="icon-box ib-green"><PhoneIcon /></div>
            <h3>{t('agent.voice.title')}</h3>
            <p className="agent-brain-role">{t('agent.voice.role')}</p>
            <ul className="show-feats">
              <li><CheckIcon /> <span>{t('agent.voice.f1')}</span></li>
              <li><CheckIcon /> <span>{t('agent.voice.f2')}</span></li>
              <li><CheckIcon /> <span>{t('agent.voice.f3')}</span></li>
              <li><CheckIcon /> <span>{t('agent.voice.f4')}</span></li>
            </ul>
          </div>
        </div>
        <p className="agent-note rev">{t('agent.note')}</p>

        {/* Три фазы звонка */}
        <div className="agent-subhead rev">
          <h3>{t('agent.phases.title')}</h3>
          <p>{t('agent.phases.desc')}</p>
        </div>
        <div className="agent-phases rev">
          <div className="agent-phase">
            <span className="agent-phase-num">1</span>
            <h4>{t('agent.p1.title')}</h4>
            <ul>
              <li>{t('agent.p1.i1')}</li>
              <li>{t('agent.p1.i2')}</li>
              <li>{t('agent.p1.i3')}</li>
              <li>{t('agent.p1.i4')}</li>
            </ul>
          </div>
          <div className="agent-phase-arrow">→</div>
          <div className="agent-phase">
            <span className="agent-phase-num">2</span>
            <h4>{t('agent.p2.title')}</h4>
            <ul>
              <li>{t('agent.p2.i1')}</li>
              <li>{t('agent.p2.i2')}</li>
              <li>{t('agent.p2.i3')}</li>
              <li>{t('agent.p2.i4')}</li>
            </ul>
          </div>
          <div className="agent-phase-arrow">→</div>
          <div className="agent-phase">
            <span className="agent-phase-num">3</span>
            <h4>{t('agent.p3.title')}</h4>
            <ul>
              <li>{t('agent.p3.i1')}</li>
              <li>{t('agent.p3.i2')}</li>
              <li>{t('agent.p3.i3')}</li>
              <li>{t('agent.p3.i4')}</li>
            </ul>
          </div>
        </div>

        {/* Исходящие и входящие */}
        <div className="agent-directions">
          <div className="agent-dir rev">
            <div className="agent-dir-head">
              <span className="agent-dir-arrow out">↑</span>
              <h4>{t('agent.out.title')}</h4>
            </div>
            <ul className="show-feats">
              <li><CheckIcon /> <span>{t('agent.out.f1')}</span></li>
              <li><CheckIcon /> <span>{t('agent.out.f2')}</span></li>
              <li><CheckIcon /> <span>{t('agent.out.f3')}</span></li>
              <li><CheckIcon /> <span>{t('agent.out.f4')}</span></li>
              <li><CheckIcon /> <span>{t('agent.out.f5')}</span></li>
            </ul>
          </div>
          <div className="agent-dir rev d1">
            <div className="agent-dir-head">
              <span className="agent-dir-arrow in">↓</span>
              <h4>{t('agent.in.title')}</h4>
            </div>
            <ul className="show-feats">
              <li><CheckIcon /> <span>{t('agent.in.f1')}</span></li>
              <li><CheckIcon /> <span>{t('agent.in.f2')}</span></li>
              <li><CheckIcon /> <span>{t('agent.in.f3')}</span></li>
              <li><CheckIcon /> <span>{t('agent.in.f4')}</span></li>
              <li><CheckIcon /> <span>{t('agent.in.f5')}</span></li>
            </ul>
          </div>
        </div>

        {/* Чипы про память */}
        <div className="agent-chips rev">
          <span className="agent-chip">{t('agent.chip1')}</span>
          <span className="agent-chip">{t('agent.chip2')}</span>
          <span className="agent-chip">{t('agent.chip3')}</span>
        </div>

        {/* CTA */}
        <div className="agent-cta rev">
          <button className="btn-primary-hero" onClick={() => onOpenModal('register')}>{t('agent.cta')}</button>
          <a
            className="btn-secondary-hero agent-video-btn"
            href="https://www.youtube.com/watch?v=NI_UMGrWt9E"
            target="_blank"
            rel="noopener noreferrer"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
            {t('agent.video')}
          </a>
        </div>
      </div>
    </section>
  );
}

export default AgentSection;
