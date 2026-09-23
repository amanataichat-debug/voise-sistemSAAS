import React, { useState } from 'react';
import api from '../../utils/api';
import InlineNotification from '../InlineNotification';
import Icon from '../Icon';
import PasswordField from './PasswordField';
import ForgotPasswordForm from './ForgotPasswordForm';
import { useT } from '../../i18n';

// Логика входа не менялась: api.login → auth_token в localStorage → /static/dashboard.html
function LoginForm({ onSwitchToRegister }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [notification, setNotification] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showForgot, setShowForgot] = useState(false);
  const { t } = useT();

  if (showForgot) {
    return (
      <ForgotPasswordForm
        initialEmail={email}
        onBackToLogin={(resetEmail) => {
          if (resetEmail) setEmail(resetEmail);
          setPassword('');
          setNotification(null);
          setShowForgot(false);
        }}
      />
    );
  }

  const handleSubmit = async (e) => {
    e.preventDefault();

    setNotification({ type: 'loading', message: t('auth.login_status_loading') });
    setIsLoading(true);

    try {
      const data = await api.login({ email, password });

      localStorage.setItem('auth_token', data.token);

      setNotification({ type: 'success', message: t('auth.login_status_success') });

      setTimeout(() => {
        window.location.href = '/static/dashboard.html';
      }, 500);
    } catch (error) {
      setIsLoading(false);

      if (error.message.includes('not verified') || error.message.includes('не подтвержден')) {
        setNotification({ type: 'warning', message: t('auth.login_error_not_verified') });
      } else if (error.message.includes('Invalid') || error.message.includes('password')) {
        setNotification({ type: 'error', message: t('auth.login_error_invalid') });
      } else {
        setNotification({ type: 'error', message: error.message || t('auth.login_error_default') });
      }
    }
  };

  return (
    <form className="lp-form" onSubmit={handleSubmit}>
      <div className="lp-auth-head">
        <h2 className="lp-auth-title">{t('auth.login_title')}</h2>
        <p className="lp-auth-sub">{t('auth.login_subtitle')}</p>
      </div>

      <InlineNotification notification={notification} />

      <div className="lp-auth-fields">
        <div className="field">
          <label className="label" htmlFor="login-email">
            {t('auth.email_label')}
          </label>
          <div className="input-wrap lp-auth-input">
            <Icon name="mail" className="ic-sm" />
            <input
              type="email"
              id="login-email"
              className="input"
              placeholder={t('auth.email_placeholder')}
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <label className="label" htmlFor="login-password">
            {t('auth.password_label')}
            <a
              href="#"
              className="lp-forgot"
              onClick={(e) => {
                e.preventDefault();
                setNotification(null);
                setShowForgot(true);
              }}
            >
              {t('auth.forgot_link')}
            </a>
          </label>
          <PasswordField
            id="login-password"
            placeholder={t('auth.password_placeholder')}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
      </div>

      <button type="submit" className="btn btn-primary btn-lg lp-form-submit" disabled={isLoading}>
        {isLoading ? (
          <>
            <span className="spin" /> {t('auth.login_submitting')}
          </>
        ) : (
          <>
            {t('auth.login_submit')}
            <Icon name="arrow-right" />
          </>
        )}
      </button>

      <p className="lp-form-hint">
        {t('auth.login_hint')}{' '}
        <a
          href="#"
          onClick={(e) => {
            e.preventDefault();
            onSwitchToRegister();
          }}
        >
          {t('auth.login_hint_link')}
        </a>
      </p>
    </form>
  );
}

export default LoginForm;
