import React from 'react';
import Icon from './Icon';

// Логотип VoksiAI: знак (квадрат акцента с волной) + wordmark с градиентом.
function Logo({ className = '', href = '#top', ariaLabel, plain = false }) {
  return (
    <a href={href} className={`vf-logo lp-logo ${className}`.trim()} aria-label={ariaLabel}>
      <span className="mark" aria-hidden="true">
        <Icon name="audio-lines" />
      </span>
      <span className={plain ? undefined : 'wordmark'}>VoksiAI</span>
    </a>
  );
}

export default Logo;
