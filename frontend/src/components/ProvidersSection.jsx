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

function ProvidersSection() {
  const { t } = useT();

  return (
    <section className="section providers-section" id="providers">
      <div className="section-inner">
        <div className="s-head rev">
          <span className="s-label">{t('prov.label')}</span>
          <h2 className="s-title">{t('prov.title1')}<span className="gt">{t('prov.title2')}</span></h2>
          <p className="s-desc">{t('prov.desc')}</p>
        </div>

        <div className="prov-grid">
          <div className="prov-card rev">
            <div className="prov-icon pi-g"><i className="fab fa-google"></i></div>
            <h4>Google Gemini Live</h4>
            <p>{t('prov.g.desc')}</p>
            <ul className="prov-feats">
              <li><CheckIcon /> {t('prov.g.f1')}</li>
              <li><CheckIcon /> {t('prov.g.f2')}</li>
              <li><CheckIcon /> {t('prov.g.f3')}</li>
              <li><CheckIcon /> {t('prov.g.f4')}</li>
              <li><CheckIcon /> {t('prov.g.f5')}</li>
            </ul>
          </div>

          <div className="prov-card rev d1">
            <div className="prov-icon pi-o"><i className="fas fa-robot"></i></div>
            <h4>OpenAI Realtime</h4>
            <p>{t('prov.o.desc')}</p>
            <ul className="prov-feats">
              <li><CheckIcon /> {t('prov.o.f1')}</li>
              <li><CheckIcon /> {t('prov.o.f2')}</li>
              <li><CheckIcon /> {t('prov.o.f3')}</li>
              <li><CheckIcon /> {t('prov.o.f4')}</li>
              <li><CheckIcon /> {t('prov.o.f5')}</li>
            </ul>
          </div>

          <div className="prov-card rev d2">
            <div className="prov-icon pi-c"><i className="fas fa-wave-square"></i></div>
            <h4>ChatGPT + Cartesia TTS</h4>
            <p>{t('prov.c.desc')}</p>
            <ul className="prov-feats">
              <li><CheckIcon /> {t('prov.c.f1')}</li>
              <li><CheckIcon /> {t('prov.c.f2')}</li>
              <li><CheckIcon /> {t('prov.c.f3')}</li>
              <li><CheckIcon /> {t('prov.c.f4')}</li>
              <li><CheckIcon /> {t('prov.c.f5')}</li>
            </ul>
          </div>

          <div className="prov-card rev d3">
            <div className="prov-icon pi-y"><i className="fab fa-yandex"></i></div>
            <h4>Яндекс SpeechKit Realtime</h4>
            <p>{t('prov.y.desc')}</p>
            <ul className="prov-feats">
              <li><CheckIcon /> {t('prov.y.f1')}</li>
              <li><CheckIcon /> {t('prov.y.f2')}</li>
              <li><CheckIcon /> {t('prov.y.f3')}</li>
              <li><CheckIcon /> {t('prov.y.f4')}</li>
              <li><CheckIcon /> {t('prov.y.f5')}</li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

export default ProvidersSection;
