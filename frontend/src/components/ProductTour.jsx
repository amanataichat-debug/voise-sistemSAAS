import React from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import SectionHead from './SectionHead';
import { Reveal, Parallax } from './Reveal';
import { MockAssistant, MockTelephony, MockDialogs, MockCrm, MockKnowledge } from './Mockups';

const MOCKS = [MockAssistant, MockTelephony, MockDialogs, MockCrm, MockKnowledge];

function ProductTour() {
  const { t } = useT();
  const rows = t('tour.rows');
  return (
    <section className="sec sec-grid" id="platform">
      <div className="lp-container">
        <SectionHead index="01" title={t('tour.title')} lead={t('tour.lead')} />
        <div className="tour">
          {rows.map((row, i) => {
            const Mock = MOCKS[i];
            return (
              <div key={row.title} className="tour-row">
                <Reveal className="tour-text" x={-16} y={0}>
                  <span className="tour-n">{String(i + 1).padStart(2, '0')}</span>
                  <h3>{row.title}</h3>
                  <p>{row.text}</p>
                  <dl className="facts">
                    {row.facts.map(([dt, dd]) => (
                      <div key={dt}>
                        <dt>{dt}</dt>
                        <dd>{dd}</dd>
                      </div>
                    ))}
                  </dl>
                </Reveal>
                <Parallax className="tour-mock" amount={36}>
                  <Reveal className="stack" y={24} delay={0.1}>
                    <Mock />
                  </Reveal>
                </Parallax>
              </div>
            );
          })}
        </div>
        <Reveal className="tour-foot" y={12}>
          <p>{t('tour.foot_text')}</p>
          <a href="#pricing" className="lp-link">
            {t('tour.foot_link')}
            <Icon name="arrow-right" className="ic-sm" />
          </a>
        </Reveal>
      </div>
    </section>
  );
}

export default ProductTour;
