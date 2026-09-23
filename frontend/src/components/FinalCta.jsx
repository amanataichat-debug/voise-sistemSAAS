import React from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import { Reveal } from './Reveal';
import { SUPPORT_URL } from './contacts';

function FinalCta({ onOpenModal }) {
  const { t } = useT();
  return (
    <section className="sec final">
      <div className="lp-container">
        <Reveal className="final-inner" y={16}>
          <div>
            <h2>{t('final.title')}</h2>
            <p className="lp-lead">{t('final.lead')}</p>
          </div>
          <div className="final-actions">
            <button type="button" className="btn btn-primary btn-lg" onClick={() => onOpenModal('register')}>
              {t('final.cta_create')}
              <Icon name="arrow-right" />
            </button>
            <a className="btn btn-lg" href={SUPPORT_URL} target="_blank" rel="noopener">
              <Icon name="send" />
              {t('final.cta_ask')}
            </a>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

export default FinalCta;
