import React, { useState } from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import CallCard from './CallCard';
import { Stagger, Item } from './Reveal';
import { PHONE, PHONE_DISPLAY, copyText } from './contacts';

const isDesktop = () =>
  window.innerWidth >= 768 && !/Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

function Hero({ onOpenModal }) {
  const { t } = useT();
  const [pop, setPop] = useState(false);
  const [copied, setCopied] = useState(false);
  const channels = t('hero.channels');
  const floaters = t('hero.floaters');

  const onCall = (e) => {
    if (!isDesktop()) return;
    e.preventDefault();
    setPop(true);
  };

  const onCopy = () => {
    copyText(PHONE_DISPLAY);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="hero sec-grid" id="top">
      <div className="hero-aurora" aria-hidden="true">
        <i />
        <i />
      </div>
      <div className="lp-container">
        <div className="hero-grid">
          <Stagger className="hero-copy" stagger={0.09} amount={0.1}>
            <Item as="p" className="hero-kicker" y={10}>
              <span className="hero-kicker-dot" />
              {t('hero.kicker')}
            </Item>
            <Item as="h1" y={22}>
              {t('hero.title_a')} <span className="hl">{t('hero.title_hl')}</span>
            </Item>
            <Item as="p" className="hero-lead">
              {t('hero.lead')}
            </Item>
            <Item className="hero-actions">
              <button type="button" className="btn btn-primary btn-lg btn-glow" onClick={() => onOpenModal('register')}>
                {t('hero.cta_create')}
                <Icon name="arrow-right" />
              </button>
              <div className="lp-pop-wrap">
                <a className="btn btn-lg" href={`tel:${PHONE}`} onClick={onCall}>
                  <Icon name="phone" />
                  {t('hero.cta_call')}
                </a>
                {pop && (
                  <>
                    <div className="lp-pop-backdrop" onClick={() => setPop(false)} />
                    <div className="lp-pop card card-raised" role="dialog" aria-label={t('hero.popover_aria')}>
                      <div className="lp-pop-num">{PHONE_DISPLAY}</div>
                      <div className="lp-pop-hint">{t('hero.popover_hint')}</div>
                      <button type="button" className="btn" onClick={onCopy}>
                        <Icon name={copied ? 'check' : 'copy'} />
                        {copied ? t('hero.popover_copied') : t('hero.popover_copy')}
                      </button>
                      <div className="lp-pop-or">{t('hero.popover_or')}</div>
                    </div>
                  </>
                )}
              </div>
            </Item>
            <Item as="dl" className="hero-facts" y={12}>
              {t('hero.facts').map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </Item>
          </Stagger>

          <Stagger className="hero-visual" delay={0.3} amount={0.1}>
            <Item x={40} y={0} rotate={1.5} duration={0.8} className="cc-wrap">
              <CallCard />
              {floaters.map(([icon, text], i) => (
                <span key={text} className={`float-chip float-chip-${i + 1}`} aria-hidden="true">
                  <Icon name={icon} className="ic-sm" />
                  {text}
                </span>
              ))}
            </Item>
          </Stagger>
        </div>

        <div className="marquee" role="group" aria-label={t('hero.channels_label')}>
          <span className="marquee-label">{t('hero.channels_label')}</span>
          <div className="marquee-viewport">
            <div className="marquee-track">
              {[0, 1].map((copy) => (
                <ul key={copy} className="marquee-list" aria-hidden={copy === 1 ? 'true' : undefined}>
                  {channels.map(([icon, name]) => (
                    <li key={name} className="marquee-item">
                      <Icon name={icon} className="ic-sm" />
                      {name}
                    </li>
                  ))}
                </ul>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default Hero;
