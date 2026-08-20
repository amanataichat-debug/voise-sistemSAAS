import React, { useState, useEffect } from 'react';

function ScrollProgress() {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let ticking = false;
    const update = () => {
      const h = document.documentElement;
      const pct = (h.scrollTop / (h.scrollHeight - h.clientHeight)) * 100;
      setProgress(Math.min(pct, 100));
      ticking = false;
    };
    const onScroll = () => {
      if (!ticking) {
        requestAnimationFrame(update);
        ticking = true;
      }
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0,
      height: '3px', zIndex: 201,
      width: `${progress}%`,
      background: 'var(--vs-accent)',
      transition: 'width 0.1s linear',
      borderRadius: '0 2px 2px 0',
    }}/>
  );
}

export default ScrollProgress;
