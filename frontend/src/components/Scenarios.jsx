import React from 'react';
import { useT } from '../i18n';
import SectionHead from './SectionHead';
import { Stagger, Item } from './Reveal';

function Scenarios() {
  const { t } = useT();
  const chip = {
    assistant: ['chip', t('scenarios.chip_assistant')],
    agent: ['chip chip-accent', t('scenarios.chip_agent')],
    both: ['chip', t('scenarios.chip_both')],
  };
  return (
    <section className="sec sec-tint tint-sand" id="cases">
      <div className="lp-container">
        <SectionHead index="05" title={t('scenarios.title')} lead={t('scenarios.lead')} />
        <Stagger as="ul" className="cases" stagger={0.1}>
          {t('scenarios.rows').map(([who, title, text, kind]) => (
            <Item as="li" key={who}>
              <span className="cases-who">{who}</span>
              <div>
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
              <span className={chip[kind][0]}>{chip[kind][1]}</span>
            </Item>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

export default Scenarios;
