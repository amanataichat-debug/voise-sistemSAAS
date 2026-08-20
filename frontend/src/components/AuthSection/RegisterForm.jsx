import React, { useState } from 'react';
import api from '../../utils/api';
import { useReferralTracker } from '../../hooks/useReferralTracker';
import InlineNotification from '../InlineNotification';
import EmailVerificationSection from './EmailVerificationSection';
import { useT } from '../../i18n';

function RegisterForm({ onSwitchToLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [firstName, setFirstName] = useState('');
  const [companyName, setCompanyName] = useState('');
  const [notification, setNotification] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showVerification, setShowVerification] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState(null);
  const { t } = useT();

  const { getReferralData, clearReferralData } = useReferralTracker();

  const handleSubmit = async (e) => {
    e.preventDefault();

    setNotification({ type: 'loading', message: t('auth.sendingCode') });
    setIsLoading(true);

    try {
      const referralData = getReferralData();

      const userData = {
        email: email,
        password: password,
        first_name: firstName || null,
        last_name: null,
        company_name: companyName || null,
        referral_code: referralData?.referral_code || null,
        utm_data: referralData?.utm_data || null
      };

      const data = await api.register(userData);

      if (data.message && data.message.includes('exists but not verified')) {
        setNotification({ type: 'success', message: t('auth.codeSent') });
        setVerificationMessage(t('auth.accountExists'));
        setShowVerification(true);
        return;
      }

      if (data.verification_required && data.verification_sent) {
        setNotification({ type: 'success', message: t('auth.codeSent') });
        setShowVerification(true);
        clearReferralData();
      } else if (data.token) {
        localStorage.setItem('auth_token', data.token);
        window.location.href = '/static/dashboard.html';
      } else {
        // Аккаунт создан, но письмо с кодом не отправилось (verification_sent: false)
        setIsLoading(false);
        setNotification({
          type: 'error',
          message: t('auth.emailFailed')
        });
      }

    } catch (error) {
      setIsLoading(false);

      if (error.message.includes('already registered')) {
        setNotification({ type: 'error', message: t('auth.alreadyRegistered') });
        setTimeout(() => onSwitchToLogin(), 2000);
      } else {
        // Переводим типовые ошибки валидации пароля
        let message = error.message || t('auth.regError');
        if (message.includes('at least one digit')) {
          message = t('auth.pwdDigit');
        } else if (message.includes('at least one letter')) {
          message = t('auth.pwdLetter');
        } else if (message.includes('at least 8 characters')) {
          message = t('auth.pwdLength');
        }
        setNotification({ type: 'error', message });
      }
    }
  };

  if (showVerification) {
    return (
      <EmailVerificationSection
        email={email}
        message={verificationMessage}
        onVerified={() => {
          window.location.href = '/static/dashboard.html';
        }}
      />
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      <InlineNotification notification={notification} />

      <div className="fg">
        <label htmlFor="register-name">{t('auth.name')}</label>
        <input
          type="text"
          id="register-name"
          className="fi"
          placeholder={t('auth.namePh')}
          value={firstName}
          onChange={(e) => setFirstName(e.target.value)}
        />
      </div>

      <div className="fg">
        <label htmlFor="register-email">{t('auth.email')}</label>
        <input
          type="email"
          id="register-email"
          className="fi"
          placeholder={t('auth.emailPh')}
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>

      <div className="fg">
        <label htmlFor="register-password">{t('auth.password')}</label>
        <input
          type="password"
          id="register-password"
          className="fi"
          placeholder={t('auth.passwordHint')}
          required
          minLength="8"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>

      <div className="fg">
        <label htmlFor="register-company">{t('auth.company')} <span>{t('auth.companyOptional')}</span></label>
        <input
          type="text"
          id="register-company"
          className="fi"
          placeholder={t('auth.companyPh')}
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
        />
      </div>

      <button
        type="submit"
        className="btn-submit"
        disabled={isLoading}
      >
        {isLoading ? t('auth.registering') : t('auth.register')}
      </button>

      <p className="auth-hint">
        {t('auth.haveAccount')}{' '}
        <a
          onClick={(e) => {
            e.preventDefault();
            onSwitchToLogin();
          }}
        >
          {t('auth.loginLink')}
        </a>
      </p>
    </form>
  );
}

export default RegisterForm;
