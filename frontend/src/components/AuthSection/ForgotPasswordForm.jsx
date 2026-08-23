import React, { useState, useEffect, useRef } from 'react';
import api from '../../utils/api';
import InlineNotification from '../InlineNotification';
import { useT } from '../../i18n';

const RESEND_COOLDOWN = 60;

function ForgotPasswordForm({ initialEmail, onBackToLogin }) {
  const [step, setStep] = useState('email'); // 'email' | 'code'
  const [email, setEmail] = useState(initialEmail || '');
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [notification, setNotification] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const codeInputRef = useRef(null);
  const { t } = useT();

  useEffect(() => {
    if (secondsLeft <= 0) return;
    const timer = setTimeout(() => setSecondsLeft(secondsLeft - 1), 1000);
    return () => clearTimeout(timer);
  }, [secondsLeft]);

  const validatePassword = (pwd) => {
    if (pwd.length < 8) return t('auth.pwdLength');
    if (!/\d/.test(pwd)) return t('auth.pwdDigit');
    if (!/[a-zA-Zа-яА-Я]/.test(pwd)) return t('auth.pwdLetter');
    return null;
  };

  const handleSendCode = async (e) => {
    if (e) e.preventDefault();
    setNotification({ type: 'loading', message: t('forgot.sendingCode') });
    setIsLoading(true);

    try {
      await api.resetPasswordRequest({ email });
      setNotification({ type: 'success', message: t('forgot.codeSent') });
      setStep('code');
      setSecondsLeft(RESEND_COOLDOWN);
      setTimeout(() => codeInputRef.current && codeInputRef.current.focus(), 100);
    } catch (error) {
      // 429 — cooldown повторной отправки ещё не истёк
      const waitMatch = error.message.match(/(\d+)\s*seconds/);
      if (waitMatch) {
        setSecondsLeft(parseInt(waitMatch[1], 10));
        setNotification({ type: 'warning', message: t('forgot.cooldown') });
        setStep('code');
      } else {
        setNotification({ type: 'error', message: error.message || t('forgot.sendError') });
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleConfirm = async (e) => {
    e.preventDefault();

    const pwdError = validatePassword(newPassword);
    if (pwdError) {
      setNotification({ type: 'error', message: pwdError });
      return;
    }
    if (code.length !== 6) {
      setNotification({ type: 'error', message: t('forgot.codeInvalid') });
      return;
    }

    setNotification({ type: 'loading', message: t('forgot.resetting') });
    setIsLoading(true);

    try {
      await api.resetPasswordConfirm({ email, code, new_password: newPassword });
      setNotification({ type: 'success', message: t('forgot.success') });
      setTimeout(() => onBackToLogin(email), 1500);
    } catch (error) {
      setIsLoading(false);
      setNotification({ type: 'error', message: error.message || t('forgot.resetError') });
    }
  };

  if (step === 'email') {
    return (
      <form onSubmit={handleSendCode}>
        <InlineNotification notification={notification} />

        <p className="auth-hint" style={{ textAlign: 'left', marginTop: 0 }}>
          {t('forgot.intro')}
        </p>

        <div className="fg">
          <label htmlFor="forgot-email">{t('auth.email')}</label>
          <input
            type="email"
            id="forgot-email"
            className="fi"
            placeholder={t('auth.emailPh')}
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
          />
        </div>

        <button type="submit" className="btn-submit" disabled={isLoading}>
          {isLoading ? t('forgot.sending') : t('forgot.sendCodeBtn')}
        </button>

        <p className="auth-hint">
          <a onClick={(e) => { e.preventDefault(); onBackToLogin(); }}>
            {t('forgot.backToLogin')}
          </a>
        </p>
      </form>
    );
  }

  return (
    <form onSubmit={handleConfirm}>
      <div className="verification-notice">
        <i className="fas fa-envelope"></i>
        {t('verif.sentTo')} <strong>{email}</strong>
      </div>

      <InlineNotification notification={notification} />

      <div className="fg">
        <label htmlFor="forgot-code">{t('verif.enterCode')}</label>
        <input
          type="text"
          id="forgot-code"
          ref={codeInputRef}
          className="fi verification-code-input"
          placeholder="000000"
          maxLength="6"
          pattern="[0-9]{6}"
          inputMode="numeric"
          required
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
        />
      </div>

      <div className="fg">
        <label htmlFor="forgot-new-password">{t('forgot.newPassword')}</label>
        <input
          type="password"
          id="forgot-new-password"
          className="fi"
          placeholder={t('auth.passwordHint')}
          required
          minLength="8"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
        />
      </div>

      <button type="submit" className="btn-submit" disabled={isLoading}>
        {isLoading ? t('forgot.resettingBtn') : t('forgot.resetBtn')}
      </button>

      {secondsLeft > 0 ? (
        <p className="auth-hint">
          {t('verif.resendIn')} <strong>{secondsLeft}</strong>{t('verif.seconds')}
        </p>
      ) : (
        <p className="auth-hint">
          <a onClick={(e) => { e.preventDefault(); if (!isLoading) handleSendCode(); }}>
            {t('verif.resend')}
          </a>
        </p>
      )}

      <p className="auth-hint">
        <a onClick={(e) => { e.preventDefault(); onBackToLogin(); }}>
          {t('forgot.backToLogin')}
        </a>
      </p>
    </form>
  );
}

export default ForgotPasswordForm;
