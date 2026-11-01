import React, { useEffect, useRef, useState } from 'react';
import { motion, useInView, useReducedMotion } from 'motion/react';
import { useT } from '../i18n';
import Icon from './Icon';
import SectionHead from './SectionHead';
import { Reveal, Stagger, Item } from './Reveal';

// Живая воронка: карточка Марины сама переезжает «Новые → В работе → Записаны → Купили»,
// над доской меняется событие, которое сделал агент. Анимация идёт, только пока блок виден,
// и отключена при prefers-reduced-motion (карточка стоит в «В работе»).
const STEP_MS = 3000;

function CrmSection() {
  const { t } = useT();
  const reduce = useReducedMotion();
  const ref = useRef(null);
  const inView = useInView(ref, { amount: 0.35 });
  const [step, setStep] = useState(1);

  const cols = t('crm.cols');
  const events = t('crm.events');
  const people = t('crm.people');
  const timeline = t('crm.timeline');
  const tags = t('crm.tags');

  useEffect(() => {
    if (reduce || !inView) return undefined;
    const id = setInterval(() => setStep((s) => (s + 1) % cols.length), STEP_MS);
    return () => clearInterval(id);
  }, [reduce, inView, cols.length]);

  return (
    <section className="sec" id="crm">
      <div className="lp-container">
        <SectionHead index="03" title={t('crm.title')} lead={t('crm.lead')} />

        <div className="crm" ref={ref}>
          <Reveal className="crm-board" y={24}>
            <div className="crm-board-head">
              <b>{t('crm.board_title')}</b>
              <span className="chip chip-success">
                <span className="dot dot-success" />
                {t('crm.live')}
              </span>
            </div>
            <div className="crm-event" aria-live="off">
              <Icon name="sparkles" className="ic-sm" />
              <span key={step}>{events[step]}</span>
            </div>
            <div className="crm-cols">
              {cols.map((col, c) => (
                <div key={col} className={`crm-col${c === step ? ' on' : ''}`}>
                  <div className="crm-col-head">
                    <b>{col}</b>
                    <span>{people[c].length + (c === step ? 1 : 0)}</span>
                  </div>
                  {c === step && (
                    <motion.div
                      layoutId="crm-hero"
                      className="crm-card crm-card-hero"
                      transition={{ type: 'spring', stiffness: 260, damping: 28 }}
                    >
                      <b>{t('crm.hero_name')}</b>
                      <span className="mono">{t('crm.phone')}</span>
                      <span className="crm-card-tags">
                        {tags.slice(0, 2).map((tag) => (
                          <span key={tag} className="chip chip-outline">
                            {tag}
                          </span>
                        ))}
                      </span>
                    </motion.div>
                  )}
                  {people[c].map((name) => (
                    <div key={name} className="crm-card">
                      <b>{name}</b>
                      <span className="mono">{t('crm.phone')}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </Reveal>

          <Reveal className="crm-client" y={24} delay={0.12}>
            <div className="crm-client-head">
              <span className="crm-client-ava">{t('crm.hero_name').slice(0, 1)}</span>
              <div>
                <b>{t('crm.hero_name')}</b>
                <span className="mono muted">{t('crm.phone')}</span>
              </div>
              <span className="chip chip-accent crm-stage" key={`stage-${step}`}>
                {cols[step]}
              </span>
            </div>
            <div className="crm-client-tags">
              {tags.map((tag) => (
                <span key={tag} className="chip chip-outline">
                  {tag}
                </span>
              ))}
            </div>
            <p className="crm-summary">
              <b>{t('crm.summary_label')}.</b> {t('crm.summary')}
            </p>
            <ul className="crm-timeline">
              {timeline.map(([icon, title, text], i) => (
                <li key={title} className={i === timeline.length - 1 ? 'next' : undefined}>
                  <span className="crm-timeline-ic">
                    <Icon name={icon} className="ic-sm" />
                  </span>
                  <span>
                    <b>{title}</b>
                    <span>{text}</span>
                  </span>
                </li>
              ))}
            </ul>
          </Reveal>
        </div>

        <Stagger as="ul" className="feat-grid" stagger={0.07}>
          {t('crm.features').map(([icon, title, text]) => (
            <Item as="li" key={title} className="feat" y={16}>
              <span className="feat-ic">
                <Icon name={icon} />
              </span>
              <div>
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
            </Item>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

export default CrmSection;
