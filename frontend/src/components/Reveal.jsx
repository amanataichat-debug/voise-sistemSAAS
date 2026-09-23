import React, { createContext, useContext, useRef, useState } from 'react';
import { motion, useReducedMotion, useScroll, useTransform } from 'motion/react';

// Единая обёртка над Motion: появление при входе в экран (повторяется при
// каждом входе), каскад детей и параллакс. Блок возвращается с той стороны,
// куда ушёл: сторону запоминаем в onViewportLeave и передаём детям через контекст.
const EASE = [0.2, 0.7, 0.2, 1];
const VIEWPORT = { once: false, amount: 0.15, margin: '-32px 0px -32px 0px' };

const SideContext = createContext(1);

const tagCache = {};
function motionTag(as) {
  if (!tagCache[as]) tagCache[as] = motion[as] || motion.create(as);
  return tagCache[as];
}

function useSide() {
  const [side, setSide] = useState(1);
  const onViewportLeave = (entry) => {
    if (entry && entry.boundingClientRect) setSide(entry.boundingClientRect.top < 0 ? -1 : 1);
  };
  return [side, onViewportLeave];
}

export function Reveal({ as = 'div', y = 24, x = 0, delay = 0, children, ...rest }) {
  const reduce = useReducedMotion();
  const [side, onViewportLeave] = useSide();
  const Tag = motionTag(as);
  const variants = {
    hidden: (s) => ({ opacity: 0, y: (s || 1) * y, x }),
    show: { opacity: 1, y: 0, x: 0, transition: { duration: 0.6, ease: EASE, delay } },
  };
  return (
    <Tag
      data-reveal=""
      variants={variants}
      custom={side}
      initial={reduce ? false : 'hidden'}
      {...(reduce ? { animate: 'show' } : { whileInView: 'show', viewport: VIEWPORT, onViewportLeave })}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export function Stagger({ as = 'div', stagger = 0.08, delay = 0, amount = 0.15, children, ...rest }) {
  const reduce = useReducedMotion();
  const [side, onViewportLeave] = useSide();
  const Tag = motionTag(as);
  const variants = {
    hidden: {},
    show: { transition: { staggerChildren: stagger, delayChildren: delay } },
  };
  return (
    <SideContext.Provider value={side}>
      <Tag
        variants={variants}
        initial={reduce ? false : 'hidden'}
        {...(reduce
          ? { animate: 'show' }
          : { whileInView: 'show', viewport: { ...VIEWPORT, amount }, onViewportLeave })}
        {...rest}
      >
        {children}
      </Tag>
    </SideContext.Provider>
  );
}

export function Item({ as = 'div', y = 26, x = 0, scale = 1, rotate = 0, duration = 0.6, children, ...rest }) {
  const side = useContext(SideContext);
  const Tag = motionTag(as);
  const variants = {
    hidden: (s) => ({ opacity: 0, y: (s || 1) * y, x, scale, rotate }),
    show: { opacity: 1, y: 0, x: 0, scale: 1, rotate: 0, transition: { duration, ease: EASE } },
  };
  return (
    <Tag data-reveal="" variants={variants} custom={side} {...rest}>
      {children}
    </Tag>
  );
}

export function Parallax({ amount = 40, className, children }) {
  const ref = useRef(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start end', 'end start'] });
  const y = useTransform(scrollYProgress, [0, 1], [amount, -amount]);
  return (
    <motion.div ref={ref} className={className} style={reduce ? undefined : { y }}>
      {children}
    </motion.div>
  );
}
