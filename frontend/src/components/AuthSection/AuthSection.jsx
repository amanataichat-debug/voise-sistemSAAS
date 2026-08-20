import React from 'react';
import LoginForm from './LoginForm';
import RegisterForm from './RegisterForm';
import { useT } from '../../i18n';

function AuthSection({ activeTab, setActiveTab }) {
  const { t } = useT();

  const switchToLogin = () => {
    setActiveTab('login');
  };

  const switchToRegister = () => {
    setActiveTab('register');
  };

  return (
    <section className="auth-section" id="auth">
      <div className="auth-header rev">
        <h2 className="s-title">{t('auth.startFree')}</h2>
        <p className="s-desc" style={{ margin: '0 auto' }}>{t('auth.trial3days')}</p>
      </div>

      <div className="auth-box rev d1">
        <div className="auth-tabs">
          <button
            className={`auth-tab${activeTab === 'login' ? ' active' : ''}`}
            onClick={switchToLogin}
          >
            {t('auth.tabLogin')}
          </button>
          <button
            className={`auth-tab${activeTab === 'register' ? ' active' : ''}`}
            onClick={switchToRegister}
          >
            {t('auth.tabSignup')}
          </button>
        </div>

        {activeTab === 'login' && (
          <LoginForm onSwitchToRegister={switchToRegister} />
        )}
        {activeTab === 'register' && (
          <RegisterForm onSwitchToLogin={switchToLogin} />
        )}
      </div>
    </section>
  );
}

export default AuthSection;
