import React from 'react';

// Логотип VoksiAI: знак (/static/brand/logo.svg) + wordmark с градиентом.
function Logo({ className = '', href = '#top', ariaLabel, plain = false }) {
  return (
    <a href={href} className={`vf-logo lp-logo ${className}`.trim()} aria-label={ariaLabel}>
      <img className="mark-img" src="/static/brand/logo.svg" alt="" width="30" height="30" />
      <span className={plain ? undefined : 'wordmark'}>VoksiAI</span>
    </a>
  );
}

export default Logo;
