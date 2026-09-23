import React from 'react';
import { useT } from '../i18n';
import SectionHead from './SectionHead';
import { Stagger, Item } from './Reveal';

// Не аккордеон: все ответы видны сразу. Тот же массив вопросов идёт в FAQPage JSON-LD
// (entry-server.jsx), поэтому текст в разметке и в разметке для поисковиков совпадает.
function Faq() {
  const { t } = useT();
  return (
    <section className="sec" id="faq">
      <div className="lp-container">
        <SectionHead index="07" title={t('faq.title')} />
        <Stagger as="dl" className="faq" stagger={0.06}>
          {t('faq.items').map(([q, a]) => (
            <Item as="div" key={q} y={14}>
              <dt>{q}</dt>
              <dd>{a}</dd>
            </Item>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

export default Faq;
