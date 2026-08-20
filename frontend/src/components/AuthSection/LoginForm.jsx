import React, { useState } from 'react';
import api from '../../utils/api';
import InlineNotification from '../InlineNotification';
import { useT } from '../../i18n';

function LoginForm({ onSwitchToRegister }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [notification, setNotification] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const { t } = useT();

  const handleSubmit = async (e) => {
    e.preventDefault();

    setNotification({ type: 'loading', message: t('auth.loggingInMsg') });
    setIsLoading(true);

    try {
      const data = await api.login({ email, password });

      localStorage.setItem('auth_token', data.token);

      setNotification({ type: 'success', message: t('auth.loginSuccess') });

      setTimeout(() => {
        window.location.href = '/static/dashboard.html';
      }, 500);

    } catch (error) {
      setIsLoading(false);

      if (error.message.includes('not verified') || error.message.includes('не подтвержден')) {
        setNotification({ type: 'warning', message: t('auth.emailNotVerified') });
      } else if (error.message.includes('Invalid') || error.message.includes('password')) {
        setNotification({ type: 'error', message: t('auth.invalidCreds') });
      } else {
        setNotification({ type: 'error', message: error.message || t('auth.loginError') });
      }
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <InlineNotification notification={notification} />

      <div className="fg">
        <label htmlFor="login-email">{t('auth.email')}</label>
        <input
          type="email"
          id="login-email"
          className="fi"
          placeholder={t('auth.emailPh')}
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>

      <div className="fg">
        <label htmlFor="login-password">{t('auth.password')}</label>
        <input
          type="password"
          id="login-password"
          className="fi"
          placeholder="••••••••"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>

      <button
        type="submit"
        className="btn-submit"
        disabled={isLoading}
      >
        {isLoading ? t('auth.loggingIn') : t('auth.loginBtn')}
      </button>

      <p className="auth-hint">
        {t('auth.noAccount')}{' '}
        <a
          onClick={(e) => {
            e.preventDefault();
            onSwitchToRegister();
          }}
        >
          {t('auth.signupLink')}
        </a>
      </p>
    </form>
  );
}

export default LoginForm;
