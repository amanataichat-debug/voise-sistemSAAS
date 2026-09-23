import React, { useEffect, useState } from 'react';
import LoginForm from './AuthSection/LoginForm';
import RegisterForm from './AuthSection/RegisterForm';
import Icon from './Icon';
import Logo from './Logo';
import ModelLogo, { MODELS, MODEL_ORDER } from './ModelLogo';
import { readFirstName } from '../utils/rememberedName';
import { useT } from '../i18n';

// Двухпанельная модалка: слева брендовая панель (задаёт высоту, поэтому вход и
// регистрация одного размера), справа табы и форма.
function BrandRegister({ t }) {
  return (
    <>
      <h3 className="lp-auth-brand-title">
        {t('auth.register_title')} <span>{t('auth.register_title_highlight')}</span>
      </h3>
      <ol className="lp-auth-steps">
        {t('auth.register_steps').map((step, i) => (
          <li key={step}>
            <span className="lp-auth-step-n">{i + 1}</span>
            {step}
          </li>
        ))}
      </ol>
      <span className="lp-auth-ready">
        <Icon name="phone-call" className="ic-sm" />
        {t('auth.register_ready')}
      </span>
    </>
  );
}

function BrandLogin({ t, name }) {
  return (
    <>
      <h3 className="lp-auth-brand-title">
        {t('auth.login_brand_title')}
        {name && (
          <>
            , <span>{name}</span>
          </>
        )}
      </h3>
      <p className="lp-auth-brand-text">{t('auth.login_brand_text')}</p>
      <ul className="lp-auth-steps lp-auth-points">
        {t('auth.login_points').map(([icon, text]) => (
          <li key={text}>
            <span className="lp-auth-step-n">
              <Icon name={icon} />
            </span>
            {text}
          </li>
        ))}
      </ul>
    </>
  );
}

function AuthModal({ isOpen, onClose, activeTab, setActiveTab }) {
  const { t } = useT();
  const [name, setName] = useState('');

  useEffect(() => {
    const handler = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  useEffect(() => {
    document.body.style.overflow = isOpen ? 'hidden' : '';
    if (isOpen) setName(readFirstName());
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!isOpen) return null;
  const isLogin = activeTab === 'login';

  return (
    <div className="lp-auth-backdrop" onClick={onClose}>
      <div
        className={`lp-auth lp-auth-${isLogin ? 'login' : 'register'}`}
        role="dialog"
        aria-modal="true"
        aria-label={isLogin ? t('auth.aria_login') : t('auth.aria_register')}
        onClick={(e) => e.stopPropagation()}
      >
        <aside className="lp-auth-brand">
          <div className="lp-auth-brand-top">
            <Logo className="lp-auth-logo" plain href="#top" ariaLabel={t('nav.logo_aria')} />
            <span className="lp-auth-badge">
              <span className="lp-auth-dot" />
              {isLogin ? t('auth.badge_login') : t('auth.badge_register')}
            </span>
          </div>
          {isLogin && name && (
            <div className="lp-auth-brand-greet">
              {t('auth.greet')} <span>{name}</span>
            </div>
          )}
          <div className="lp-auth-brand-switch" key={activeTab}>
            {isLogin ? <BrandLogin t={t} name={name} /> : <BrandRegister t={t} />}
          </div>
          <div className="lp-auth-models">
            <span className="lp-auth-models-label">{t('auth.models_label')}</span>
            <div className="lp-auth-models-row">
              {MODEL_ORDER.map((code) => (
                <span key={code} className={`lp-auth-model lp-auth-model-${code}`}>
                  <ModelLogo code={code} size={16} wrap={false} />
                  {MODELS[code].name}
                </span>
              ))}
            </div>
          </div>
        </aside>

        <section className="lp-auth-pane">
          <button type="button" className="btn btn-icon lp-auth-close" aria-label={t('auth.close')} onClick={onClose}>
            <Icon name="x" />
          </button>
          <div className="lp-auth-switch" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={isLogin}
              className={`lp-auth-switch-btn${isLogin ? ' active' : ''}`}
              onClick={() => setActiveTab('login')}
            >
              {t('auth.tab_login')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={!isLogin}
              className={`lp-auth-switch-btn${!isLogin ? ' active' : ''}`}
              onClick={() => setActiveTab('register')}
            >
              {t('auth.tab_register')}
            </button>
          </div>
          <div className="lp-auth-body" key={activeTab}>
            {isLogin ? (
              <LoginForm onSwitchToRegister={() => setActiveTab('register')} />
            ) : (
              <RegisterForm onSwitchToLogin={() => setActiveTab('login')} />
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

export default AuthModal;
