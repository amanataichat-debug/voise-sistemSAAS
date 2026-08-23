import React, { useState, useEffect } from 'react';
import { useT } from '../i18n';
import LangSwitch from './LangSwitch';

function Navbar({ onOpenModal }) {
  const [scrolled, setScrolled] = useState(false);
  const { t } = useT();

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 8);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <nav className={`nav${scrolled ? ' nav-scrolled' : ''}`}>
      <a href="#" className="nav-logo">
        <img src="/static/images/IMG_2820.PNG" alt="Voksy AI" className="nav-logo-img" />
        <span className="nav-logo-text">Voksy AI</span>
      </a>

      <div className="nav-center">
        <a href="#features" className="nav-link">{t('nav.features')}</a>
        <a href="#telephony" className="nav-link">{t('nav.telephony')}</a>
        <a href="#agent" className="nav-link">{t('nav.agent')}</a>
        <a href="#showcase" className="nav-link">{t('nav.platform')}</a>
        <a href="#providers" className="nav-link">{t('nav.tech')}</a>
        <a href="#pricing" className="nav-link">{t('nav.pricing')}</a>
        <a href="/static/prompts-wiki.html" className="nav-link" target="_blank" rel="noopener noreferrer">{t('nav.kb')}</a>
        <a href="https://t.me/Aibotconnect" className="nav-link" target="_blank" rel="noopener noreferrer">{t('nav.support')}</a>
        <a href="/static/api-docs.html" className="nav-link">{t('nav.api')}</a>
      </div>

      <div className="nav-right">
        <LangSwitch />
        <button className="nav-login" onClick={() => onOpenModal('login')}>{t('nav.login')}</button>
        <button className="nav-cta" onClick={() => onOpenModal('register')}>{t('nav.cta')}</button>
      </div>
    </nav>
  );
}

export default Navbar;
