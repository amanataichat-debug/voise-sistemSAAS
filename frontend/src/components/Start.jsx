import React, { useState } from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import ModelLogo from './ModelLogo';
import SectionHead from './SectionHead';
import { Reveal, Stagger, Item } from './Reveal';
import { PHONE, PHONE_DISPLAY, copyText } from './contacts';

function Start({ onOpenModal }) {
  const { t } = useT();
  const [copied, setCopied] = useState(false);

  const onCopy = () => {
    copyText(PHONE_DISPLAY);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="sec" id="how">
      <div className="lp-container">
        <SectionHead index="03" title={t('start.title')} lead={t('start.lead')} />
        <Stagger as="ol" className="steps" stagger={0.12}>
          {t('start.steps').map(([title, text], i) => (
            <Item as="li" key={title}>
              <span className="steps-n">{String(i + 1).padStart(2, '0')}</span>
              <h3>{title}</h3>
              <p>{text}</p>
            </Item>
          ))}
        </Stagger>

        <Reveal className="sec-actions" y={12}>
          <button type="button" className="btn btn-primary btn-lg" onClick={() => onOpenModal('register')}>
            {t('start.cta')}
            <Icon name="arrow-right" />
          </button>
          <span className="muted">{t('start.cta_note')}</span>
        </Reveal>

        <Reveal className="call" y={20}>
          <div className="call-copy">
            <span className="call-kicker">{t('start.call_kicker')}</span>
            <p>{t('start.call_text')}</p>
            <div className="call-tags">
              <span className="chip">
                <ModelLogo code="gemini" size={14} wrap={false} />
                {t('start.call_chip_model')}
              </span>
              <span className="chip">
                <Icon name="mic" />
                {t('start.call_chip_voice')}
              </span>
              <span className="chip">
                <Icon name="clock" />
                {t('start.call_chip_247')}
              </span>
            </div>
          </div>
          <div className="call-num">
            <a className="call-phone" href={`tel:${PHONE}`}>
              {PHONE_DISPLAY}
            </a>
            <div className="call-actions">
              <a className="btn btn-primary" href={`tel:${PHONE}`}>
                <Icon name="phone" />
                {t('start.call_btn')}
              </a>
              <button type="button" className="btn" onClick={onCopy}>
                <Icon name={copied ? 'check' : 'copy'} />
                {copied ? t('start.call_copied') : t('start.call_copy')}
              </button>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

export default Start;
