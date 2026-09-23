import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from './hooks/useAuth';
import { useReferralTracker } from './hooks/useReferralTracker';
import Navbar from './components/Navbar';
import Hero from './components/Hero';
import ProductTour from './components/ProductTour';
import AgentSection from './components/AgentSection';
import Start from './components/Start';
import Integration from './components/Integration';
import Scenarios from './components/Scenarios';
import Pricing from './components/Pricing';
import Faq from './components/Faq';
import FinalCta from './components/FinalCta';
import Footer from './components/Footer';
import AuthModal from './components/AuthModal';

const NAV_OFFSET = -72; // высота шапки 64px + запас

function App() {
  const [activeTab, setActiveTab] = useState('register');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const lenisRef = useRef(null);

  // Залогиненного пользователя сразу отправляем в кабинет
  useAuth();
  // UTM/реферальный код из URL сохраняем при заходе на страницу, а не только при
  // открытии формы регистрации
  useReferralTracker();

  const openModal = useCallback((tab) => {
    setActiveTab(tab);
    setIsModalOpen(true);
  }, []);

  const closeModal = useCallback(() => setIsModalOpen(false), []);

  // Плавная прокрутка Lenis (отключена при prefers-reduced-motion)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;
    let raf = 0;
    let lenis = null;
    let cancelled = false;
    import('lenis').then(({ default: Lenis }) => {
      if (cancelled) return;
      lenis = new Lenis({ lerp: 0.1, smoothWheel: true });
      lenisRef.current = lenis;
      const loop = (time) => {
        lenis.raf(time);
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      if (lenis) lenis.destroy();
      lenisRef.current = null;
    };
  }, []);

  // При открытой модалке прокрутку страницы останавливаем
  useEffect(() => {
    const lenis = lenisRef.current;
    if (!lenis) return;
    if (isModalOpen) lenis.stop();
    else lenis.start();
  }, [isModalOpen]);

  // Якорные ссылки: плавный скролл с поправкой на шапку
  useEffect(() => {
    const onClick = (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
      const link = e.target.closest && e.target.closest('a[href^="#"]');
      if (!link) return;
      const id = link.getAttribute('href').slice(1);
      const target = id ? document.getElementById(id) : null;
      if (!target) return;
      e.preventDefault();
      const lenis = lenisRef.current;
      if (lenis) {
        lenis.scrollTo(target, { offset: NAV_OFFSET, duration: 1.1 });
      } else {
        const top = target.getBoundingClientRect().top + window.scrollY + NAV_OFFSET;
        window.scrollTo({ top, behavior: 'smooth' });
      }
      history.replaceState(null, '', `#${id}`);
    };
    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, []);

  return (
    <div className="lp">
      <Navbar onOpenModal={openModal} />
      <main>
        <Hero onOpenModal={openModal} />
        <ProductTour />
        <AgentSection onOpenModal={openModal} />
        <Start onOpenModal={openModal} />
        <Integration />
        <Scenarios />
        <Pricing onOpenModal={openModal} />
        <Faq />
        <FinalCta onOpenModal={openModal} />
      </main>
      <Footer />
      <AuthModal isOpen={isModalOpen} onClose={closeModal} activeTab={activeTab} setActiveTab={setActiveTab} />
    </div>
  );
}

export default App;
