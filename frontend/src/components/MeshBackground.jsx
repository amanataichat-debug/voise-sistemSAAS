import React, { useEffect, useRef } from 'react';

function MeshBackground() {
  const morb1 = useRef(null);
  const morb2 = useRef(null);
  const morb3 = useRef(null);
  const morb4 = useRef(null);

  useEffect(() => {
    let ticking = false;

    const handleScroll = () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          const y = window.scrollY;
          if (morb1.current) morb1.current.style.transform = `translateY(${y * 0.14}px)`;
          if (morb2.current) morb2.current.style.transform = `translateY(${-y * 0.09}px)`;
          if (morb3.current) morb3.current.style.transform = `translateY(${y * 0.07}px) translateX(${y * 0.025}px)`;
          if (morb4.current) morb4.current.style.transform = `translateY(${-y * 0.06}px)`;
          ticking = false;
        });
        ticking = true;
      }
    };

    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <div className="mesh-bg">
      <div className="morb morb-1" ref={morb1}></div>
      <div className="morb morb-2" ref={morb2}></div>
      <div className="morb morb-3" ref={morb3}></div>
      <div className="morb morb-4" ref={morb4}></div>
    </div>
  );
}

export default MeshBackground;
