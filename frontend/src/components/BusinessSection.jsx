import React, { useState } from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import SectionHead from './SectionHead';
import { Stagger, Item } from './Reveal';

// «Кабинет под ваш бизнес» (ветка 1811-niche-admin-speed): после регистрации клиент выбирает сферу,
// и кабинет сам включает нужное — онлайн-запись, заказы, воронку, каталог с нужными полями.
// Сверху — переключатель сфер (что именно включится), ниже — модули карточками.
const ICONS = ['calendar', 'shopping-cart', 'chart-column', 'users', 'smartphone', 'sparkles'];

function onMove(e) {
  const r = e.currentTarget.getBoundingClientRect();
  e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`);
  e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`);
}

function BusinessSection({ onOpenModal }) {
  const { t } = useT();
  const niches = t('business.niches');
  const items = t('business.items');
  const [cur, setCur] = useState(0);
  const n = niches[cur] || niches[0];
  return (
    <section className="sec" id="business">
      <div className="lp-container">
        <SectionHead index="08" title={t('business.title')} lead={t('business.lead')} />
        <div className="bz-pick" role="tablist" aria-label={t('business.pick_label')}>
          {niches.map((x, i) => (
            <button
              key={x[0]}
              type="button"
              role="tab"
              aria-selected={i === cur}
              className={`bz-chip${i === cur ? ' on' : ''}`}
              onClick={() => setCur(i)}
            >
              {x[0]}
            </button>
          ))}
        </div>
        <div className="bz-result" aria-live="polite">
          <span className="bz-result-k">{t('business.result_label')}</span>
          <ul>
            {n[1].map((line) => (
              <li key={line}>
                <Icon name="check" className="ic-sm" />
                {line}
              </li>
            ))}
          </ul>
        </div>
        <Stagger as="ul" className="ab-grid bz-grid" stagger={0.08}>
          {items.map(([title, text, tag, href, linkText], i) => (
            <Item as="li" key={title} className={`ab bz bz-${i + 1}`} y={22} onMouseMove={onMove}>
              <div className="ab-top">
                <span className="ab-ic">
                  <Icon name={ICONS[i]} />
                </span>
                <span className="chip chip-outline">{tag}</span>
              </div>
              <h3>{title}</h3>
              <p>{text}</p>
              {href && <a className="bz-link" href={href}>{linkText} →</a>}
            </Item>
          ))}
        </Stagger>
        <div className="bz-cta">
          <button type="button" className="btn btn-primary btn-lg" onClick={() => onOpenModal && onOpenModal('register')}>
            {t('business.cta')}
          </button>
          <span className="bz-cta-note">{t('business.cta_note')}</span>
        </div>
      </div>
    </section>
  );
}

export default BusinessSection;
