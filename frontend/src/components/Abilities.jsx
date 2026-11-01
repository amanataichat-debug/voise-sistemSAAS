import React from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import SectionHead from './SectionHead';
import { Stagger, Item } from './Reveal';

// Шесть главных умений в виде «bento»-карточек. В каждой маленький рисунок, который
// без слов показывает, о чём карточка. Подсветка под курсором — через CSS-переменные.
const ICONS = ['phone-incoming', 'phone-outgoing', 'message-circle', 'mic', 'file-text', 'contact-round'];
const WAVE = [38, 62, 46, 80, 58, 92, 50, 70, 40, 84, 56, 66, 34, 76, 48, 60, 42, 72];

function Vis({ index, vis }) {
  if (index === 0) {
    return (
      <div className="vis vis-call">
        <span className="vf-wave">
          <i />
          <i />
          <i />
          <i />
          <i />
        </span>
        <b>{vis.call}</b>
        <span className="cc-live" />
      </div>
    );
  }
  if (index === 1) {
    return (
      <div className="vis vis-out">
        <span className="mono">{vis.out_time}</span>
        <span className="vis-out-text">{vis.out_text}</span>
        <Icon name="phone-outgoing" className="ic-sm" />
      </div>
    );
  }
  if (index === 2) {
    return (
      <div className="vis vis-msg">
        <span className="vis-bubble">{vis.msg}</span>
        <span className="vis-ticks">
          <Icon name="check" className="ic-sm" />
          <Icon name="check" className="ic-sm" />
        </span>
      </div>
    );
  }
  if (index === 3) {
    return (
      <div className="vis vis-voice">
        <span className="vis-play">
          <Icon name="play" className="ic-sm" />
        </span>
        <span className="vis-wave">
          {WAVE.map((h, i) => (
            <i key={i} style={{ height: `${h}%`, animationDelay: `${(i % 6) * 0.12}s` }} />
          ))}
        </span>
        <span className="mono">{vis.voice}</span>
      </div>
    );
  }
  if (index === 4) {
    return (
      <div className="vis vis-doc">
        <span className="vis-pdf">PDF</span>
        <span className="vis-doc-name">
          <b>{vis.doc}</b>
          <span>{vis.doc_price}</span>
        </span>
        <Icon name="send" className="ic-sm" />
      </div>
    );
  }
  return (
    <div className="vis vis-crm">
      {vis.crm_stages.map((stage, i) => (
        <div key={stage} className={`vis-crm-col${i === 1 ? ' on' : ''}`}>
          <span>{stage}</span>
          <i />
          {i === 1 && <i className="hot" />}
        </div>
      ))}
    </div>
  );
}

function onMove(e) {
  const r = e.currentTarget.getBoundingClientRect();
  e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`);
  e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`);
}

function Abilities() {
  const { t } = useT();
  const items = t('abilities.items');
  const vis = t('abilities.vis');
  return (
    <section className="sec" id="abilities">
      <div className="lp-container">
        <SectionHead index="01" title={t('abilities.title')} lead={t('abilities.lead')} />
        <Stagger as="ul" className="ab-grid" stagger={0.08}>
          {items.map(([title, text, tag], i) => (
            <Item as="li" key={title} className={`ab ab-${i + 1}`} y={22} onMouseMove={onMove}>
              <div className="ab-top">
                <span className="ab-ic">
                  <Icon name={ICONS[i]} />
                </span>
                <span className="chip chip-outline">{tag}</span>
              </div>
              <h3>{title}</h3>
              <p>{text}</p>
              <Vis index={i} vis={vis} />
            </Item>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

export default Abilities;
