import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from './hooks/useAuth';
import MeshBackground from './components/MeshBackground';
import Navbar from './components/Navbar';
import HeroSection from './components/HeroSection';
import CodeSection from './components/CodeSection';
import ShowcaseSection from './components/ShowcaseSection';
import PhoneCTASection from './components/PhoneCTASection';
import AgentSection from './components/AgentSection';
import ProvidersSection from './components/ProvidersSection';
import PricingSection from './components/PricingSection';
import AuthModal from './components/AuthModal';
import ScrollProgress from './components/ScrollProgress';
import Footer from './components/Footer';

function App() {
  const [activeTab, setActiveTab] = useState('register');
  const [isModalOpen, setIsModalOpen] = useState(false);

  useAuth();

  const openModal = useCallback((tab) => {
    setActiveTab(tab);
    setIsModalOpen(true);
  }, []);

  const closeModal = useCallback(() => {
    setIsModalOpen(false);
  }, []);

  // IntersectionObserver for .rev elements
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('on');
          }
        });
      },
      { threshold: 0.08 }
    );

    document.querySelectorAll('.rev').forEach((el) => observer.observe(el));

    return () => observer.disconnect();
  }, []);

  // Smooth scroll for anchor links
  useEffect(() => {
    const handleClick = (e) => {
      const href = e.currentTarget.getAttribute('href');
      if (href && href.startsWith('#')) {
        const target = document.querySelector(href);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
    };

    const links = document.querySelectorAll('a[href^="#"]');
    links.forEach((link) => link.addEventListener('click', handleClick));

    return () => {
      links.forEach((link) => link.removeEventListener('click', handleClick));
    };
  }, []);

  return (
    <>
      <ScrollProgress />
      <MeshBackground />
      <Navbar onOpenModal={openModal} />
      <HeroSection onOpenModal={openModal} />
      <CodeSection />
      <ShowcaseSection />
      <PhoneCTASection />
      <AgentSection onOpenModal={openModal} />
      <ProvidersSection />
      <PricingSection onOpenModal={openModal} />
      <AuthModal
        isOpen={isModalOpen}
        onClose={closeModal}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
      />
      <Footer />
    </>
  );
}

export default App;
