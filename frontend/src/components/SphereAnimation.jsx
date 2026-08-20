import React from 'react';
import { useT } from '../i18n';

function SphereAnimation() {
  const { t } = useT();

  return (
    <div className="hero-sphere-wrap">
      <div className="sphere-container">
        <div className="sphere-el">
          <div className="sphere-wave">
            <span></span><span></span><span></span><span></span>
            <span></span><span></span><span></span><span></span><span></span>
          </div>
        </div>
        <div className="wave-container">
          <div className="wave"></div>
          <div className="wave"></div>
          <div className="wave"></div>
          <div className="wave"></div>
        </div>
      </div>

      <div className="s-chip c1">
        <span className="chip-dot"></span>
        {t('sphere.calls')}
      </div>

      <div className="s-chip c3">
        <svg width="14" height="14" viewBox="0 0 24 24"
          fill="none" stroke="#ffffff" strokeWidth="2.5"
          strokeLinecap="round" strokeLinejoin="round">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
        </svg>
        {t('sphere.active')}
      </div>
    </div>
  );
}

export default SphereAnimation;
