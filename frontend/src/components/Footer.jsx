import React from 'react';
import { useT } from '../i18n';
import LangSwitch from './LangSwitch';

function Footer() {
  const { t } = useT();

  return (
    <footer className="footer">
      <div className="footer-inner">
        <div className="foot-top">
          <div className="foot-brand">
            <span className="foot-logo">Voksy AI</span>
            <p>{t('foot.desc')}</p>
            <div className="foot-lang">
              <LangSwitch compact />
            </div>
          </div>
          <div className="foot-col">
            <h5>{t('foot.product')}</h5>
            <a href="#features">{t('foot.features')}</a>
            <a href="#pricing">{t('foot.pricing')}</a>
            <a href="/static/api-docs.html">{t('foot.api')}</a>
          </div>
          <div className="foot-col">
            <h5>{t('foot.docs')}</h5>
            <a href="/static/privacy-policy.html">{t('foot.privacy')}</a>
            <a href="/static/terms-of-service.html">{t('foot.terms')}</a>
            <a href="/static/public-offer.html">{t('foot.offer')}</a>
          </div>
          <div className="foot-col">
            <h5>{t('foot.contacts')}</h5>
            <a href="https://t.me/voicyfy" target="_blank" rel="noopener noreferrer">{t('foot.tg')}</a>
            <a href="https://t.me/voicyfy_support">{t('foot.support')}</a>
          </div>
        </div>
        <div className="foot-bottom">
          <span>{t('foot.rights')}</span>
          <span>ИП Торобеков Нагызбек Чыныбекович</span>
        </div>
      </div>
    </footer>
  );
}

export default Footer;
