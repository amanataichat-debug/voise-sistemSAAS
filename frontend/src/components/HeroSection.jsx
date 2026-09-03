import React, { useState } from 'react';
import SphereAnimation from './SphereAnimation';
import { useT } from '../i18n';

const PHONE = '+996554128222';
const PHONE_DISPLAY = '+996 554 128 222';

const isMobile = () =>
  window.innerWidth < 768 ||
  /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

function HeroSection({ onOpenModal }) {
  const [showPhonePopover, setShowPhonePopover] = useState(false);
  const [copied, setCopied] = useState(false);
  const { t } = useT();

  const handleCallClick = (e) => {
    if (isMobile()) return;
    e.preventDefault();
    setShowPhonePopover(true);
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(PHONE_DISPLAY);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section style={{ position: 'relative', zIndex: 1 }}>
      <div className="hero-bg vs-grid-bg vs-grid-fade" aria-hidden="true"></div>
      <div className="hero-blobs" aria-hidden="true">
        <div className="hero-blob hb-warm"></div>
        <div className="hero-blob hb-ink1"></div>
        <div className="hero-blob hb-ink2"></div>
      </div>
      <div className="hero">
        <div>
          <div className="hero-badge hero-cascade hc-0">
            <span className="badge-dot"></span>
            {t('hero.badge')}
          </div>

          <h1 className="hero-cascade hc-1">{t('hero.title')}</h1>

          <p className="hero-sub hero-cascade hc-2">{t('hero.desc')}</p>

          <div className="stats-strip hero-cascade hc-3">
            {[1, 2, 3, 4].map((i) => (
              <div className="stat-item" key={i}>
                <span className="stat-num">{t(`hero.stat${i}num`)}</span>
                <span className="stat-label">{t(`hero.stat${i}`)}</span>
              </div>
            ))}
          </div>

          <div className="hero-btns hero-cascade hc-3">
            <button className="btn-primary-hero" onClick={() => onOpenModal('register')}>
              <svg width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path d="M12 2a3 3 0 013 3v6a3 3 0 01-6 0V5a3 3 0 013-3z" />
                <path d="M19 10v1a7 7 0 01-14 0v-1" />
              </svg>
              {t('hero.cta')}
            </button>
            <div style={{ position: 'relative', display: 'inline-block' }}>
              <a href={`tel:${PHONE}`}
                className="btn-secondary-hero"
                onClick={handleCallClick}>
                <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 014.69 15a19.79 19.79 0 01-3.07-8.67A2 2 0 013.6 4.22h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L7.91 11.8a16 16 0 006.29 6.29l1.87-1.87a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 18.92z" />
                </svg>
                {t('hero.call')}
              </a>

              {showPhonePopover && (
                <>
                  <div className="popover-backdrop"
                    onClick={() => setShowPhonePopover(false)}/>
                  <div className="phone-popover">
                    <div className="pp-num">{PHONE_DISPLAY}</div>
                    <div className="pp-hint">{t('hero.pp.hint')}</div>
                    <button className="pp-copy" onClick={handleCopy}>
                      {copied ? t('hero.pp.copied') : t('hero.pp.copy')}
                    </button>
                    <div className="pp-or">{t('hero.pp.or')}</div>
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="hero-trust hero-cascade hc-4">
            <span className="ti"><i className="fas fa-circle-check"></i> {t('hero.trust1')}</span>
            <span className="ti"><i className="fas fa-circle-check"></i> {t('hero.trust2')}</span>
            <span className="ti"><i className="fas fa-circle-check"></i> {t('hero.trust3')}</span>
            <span className="ti"><i className="fas fa-circle-check"></i> {t('hero.trust4')}</span>
          </div>
        </div>

        <SphereAnimation />
      </div>
    </section>
  );
}

export default HeroSection;
