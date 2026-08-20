import React, { useEffect } from 'react';
import LoginForm from './AuthSection/LoginForm';
import RegisterForm from './AuthSection/RegisterForm';
import { useT } from '../i18n';

function AuthModal({ isOpen, onClose, activeTab, setActiveTab }) {
  const { t } = useT();

  useEffect(() => {
    const handler = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  useEffect(() => {
    document.body.style.overflow = isOpen ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>
          <svg width="18" height="18" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
        <div className="modal-logo">
          <img src="/static/images/IMG_2820.PNG" alt="VoiceSystem" />
          <span>VoiceSystem</span>
        </div>
        <div className="modal-title">
          {activeTab === 'login' ? t('auth.welcome') : t('auth.startFree')}
        </div>
        <div className="modal-sub">
          {activeTab === 'register' ? t('auth.trial3days') : ''}
        </div>
        <div className="auth-tabs">
          <button className={`auth-tab ${activeTab === 'login' ? 'active' : ''}`}
            onClick={() => setActiveTab('login')}>{t('auth.tabLogin')}</button>
          <button className={`auth-tab ${activeTab === 'register' ? 'active' : ''}`}
            onClick={() => setActiveTab('register')}>{t('auth.tabSignup')}</button>
        </div>
        {activeTab === 'login'
          ? <LoginForm onSwitchToRegister={() => setActiveTab('register')} />
          : <RegisterForm onSwitchToLogin={() => setActiveTab('login')} />
        }
      </div>
    </div>
  );
}

export default AuthModal;
