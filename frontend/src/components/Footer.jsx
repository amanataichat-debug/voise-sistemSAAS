import React from 'react';
import { useT } from '../i18n';
import Logo from './Logo';
import LangSwitch from './LangSwitch';
import { TELEGRAM_URL, SUPPORT_URL } from './contacts';

function Footer() {
  const { t } = useT();
  return (
    <footer className="lp-footer">
      <div className="lp-container">
        <div className="lp-footer-inner">
          <div className="lp-footer-brand">
            <Logo ariaLabel={t('nav.logo_aria')} />
            <p>{t('footer.brand_text')}</p>
            <LangSwitch className="lp-footer-lang" />
          </div>
          <div className="lp-footer-col">
            <h4>{t('footer.product_title')}</h4>
            <a href="#platform">{t('footer.product_features')}</a>
            <a href="#agent">{t('footer.product_agent')}</a>
            <a href="#pricing">{t('footer.product_pricing')}</a>
            <a href="/static/api-docs.html">{t('footer.product_api')}</a>
            <a href="/static/prompts-wiki.html">{t('footer.product_knowledge')}</a>
          </div>
          <div className="lp-footer-col">
            <h4>{t('footer.docs_title')}</h4>
            <a href="/static/privacy-policy.html">{t('footer.docs_privacy')}</a>
            <a href="/static/terms-of-service.html">{t('footer.docs_terms')}</a>
            <a href="/static/public-offer.html">{t('footer.docs_offer')}</a>
          </div>
          <div className="lp-footer-col">
            <h4>{t('footer.contacts_title')}</h4>
            <a href={TELEGRAM_URL} target="_blank" rel="noopener">
              {t('footer.contacts_telegram')}
            </a>
            <a href={SUPPORT_URL} target="_blank" rel="noopener">
              {t('footer.contacts_support')}
            </a>
          </div>
        </div>
        <div className="lp-footer-bottom">
          <span>{t('footer.copyright')}</span>
          <span>{t('footer.company')}</span>
        </div>
      </div>
    </footer>
  );
}

export default Footer;
