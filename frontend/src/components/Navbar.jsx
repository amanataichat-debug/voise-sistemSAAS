import React, { useState, useEffect } from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import Logo from './Logo';
import LangSwitch from './LangSwitch';

import { SUPPORT_URL } from './contacts';

const LINKS = [
  { href: '#platform', key: 'nav.platform' },
  { href: '#agent', key: 'nav.agent' },
  { href: '#how', key: 'nav.how' },
  { href: '#integration', key: 'nav.integration' },
  { href: '#pricing', key: 'nav.pricing' },
  { href: '/static/prompts-wiki.html', key: 'nav.knowledge' },
  { href: '/static/api-docs.html', key: 'nav.api' },
  { href: SUPPORT_URL, key: 'nav.support', external: true },
];

function Navbar({ onOpenModal }) {
  const { t } = useT();
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState('');

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  // Подсветка пункта меню по секции в центре экрана
  useEffect(() => {
    const ids = LINKS.filter((l) => l.href.startsWith('#')).map((l) => l.href.slice(1));
    const sections = ids.map((id) => document.getElementById(id)).filter(Boolean);
    if (!sections.length || !('IntersectionObserver' in window)) return;
    const ratios = {};
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          ratios[e.target.id] = e.isIntersecting ? e.intersectionRatio : 0;
        });
        let best = '';
        let max = 0;
        Object.keys(ratios).forEach((id) => {
          if (ratios[id] > max) {
            max = ratios[id];
            best = id;
          }
        });
        setActive(best);
      },
      { rootMargin: '-40% 0px -50% 0px', threshold: [0, 0.1, 0.5] }
    );
    sections.forEach((s) => io.observe(s));
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  const renderLinks = (onClick) =>
    LINKS.map((l) => (
      <a
        key={l.key}
        href={l.href}
        className={`lp-nav-link${active && l.href === `#${active}` ? ' on' : ''}`}
        onClick={onClick}
        {...(l.external ? { target: '_blank', rel: 'noopener' } : {})}
      >
        {t(l.key)}
      </a>
    ));

  const openModal = (tab) => {
    setOpen(false);
    onOpenModal(tab);
  };

  return (
    <header className={`lp-nav${scrolled ? ' scrolled' : ''}`}>
      <div className="lp-container lp-nav-inner">
        <Logo ariaLabel={t('nav.logo_aria')} />
        <nav className="lp-nav-links">{renderLinks()}</nav>
        <div className="lp-nav-actions">
          <LangSwitch />
          <button type="button" className="btn btn-ghost" onClick={() => openModal('login')}>
            {t('nav.login')}
          </button>
          <button type="button" className="btn btn-primary" onClick={() => openModal('register')}>
            {t('nav.start_free')}
          </button>
          <button
            type="button"
            className="btn btn-icon lp-burger"
            aria-expanded={open}
            aria-label={open ? t('nav.close_menu') : t('nav.open_menu')}
            onClick={() => setOpen((v) => !v)}
          >
            <Icon name={open ? 'x' : 'menu'} />
          </button>
        </div>
      </div>
      {open && (
        <div className="lp-nav-mobile">
          <LangSwitch />
          {renderLinks(() => setOpen(false))}
          <div className="lp-nav-mobile-actions">
            <button type="button" className="btn btn-lg" onClick={() => openModal('login')}>
              {t('nav.login')}
            </button>
            <button type="button" className="btn btn-primary btn-lg" onClick={() => openModal('register')}>
              {t('nav.start_free')}
            </button>
          </div>
        </div>
      )}
    </header>
  );
}

export default Navbar;
