import React, { useState, useEffect, useRef } from 'react';
import api from '../../utils/api';
import InlineNotification from '../InlineNotification';
import Icon from '../Icon';
import PasswordField from './PasswordField';
import { useT } from '../../i18n';

const RESEND_COOLDOWN = 60;

// Восстановление пароля: код на почту → новый пароль. Логика прежняя
// (api.resetPasswordRequest / api.resetPasswordConfirm), изменена только разметка.
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
    if (pwd.length < 8) return t('auth.pwd_length');
    if (!/\d/.test(pwd)) return t('auth.pwd_digit');
    if (!/[a-zA-Zа-яА-Я]/.test(pwd)) return t('auth.pwd_letter');
    return null;
  };

  const handleSendCode = async (e) => {
    if (e) e.preventDefault();
    setNotification({ type: 'loading', message: t('auth.forgot_status_sending') });
    setIsLoading(true);

    try {
      await api.resetPasswordRequest({ email });
      setNotification({ type: 'success', message: t('auth.forgot_code_sent') });
      setStep('code');
      setSecondsLeft(RESEND_COOLDOWN);
      setTimeout(() => codeInputRef.current && codeInputRef.current.focus(), 100);
    } catch (error) {
      // 429 — cooldown повторной отправки ещё не истёк
      const waitMatch = error.message.match(/(\d+)\s*seconds/);
      if (waitMatch) {
        setSecondsLeft(parseInt(waitMatch[1], 10));
        setNotification({ type: 'warning', message: t('auth.forgot_cooldown') });
        setStep('code');
      } else {
        setNotification({ type: 'error', message: error.message || t('auth.forgot_send_error') });
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
      setNotification({ type: 'error', message: t('auth.forgot_code_invalid') });
      return;
    }

    setNotification({ type: 'loading', message: t('auth.forgot_status_resetting') });
    setIsLoading(true);

    try {
      await api.resetPasswordConfirm({ email, code, new_password: newPassword });
      setNotification({ type: 'success', message: t('auth.forgot_success') });
      setTimeout(() => onBackToLogin(email), 1500);
    } catch (error) {
      setIsLoading(false);
      setNotification({ type: 'error', message: error.message || t('auth.forgot_reset_error') });
    }
  };

  const backLink = (
    <p className="lp-form-hint">
      <a
        href="#"
        onClick={(e) => {
          e.preventDefault();
          onBackToLogin();
        }}
      >
        <Icon name="arrow-left" className="ic-sm" /> {t('auth.forgot_back')}
      </a>
    </p>
  );

  if (step === 'email') {
    return (
      <form className="lp-form" onSubmit={handleSendCode}>
        <div className="lp-auth-head">
          <h2 className="lp-auth-title">{t('auth.forgot_title')}</h2>
          <p className="lp-auth-sub">{t('auth.forgot_intro')}</p>
        </div>

        <InlineNotification notification={notification} />

        <div className="field">
          <label className="label" htmlFor="forgot-email">
            {t('auth.email_label')}
          </label>
          <div className="input-wrap lp-auth-input">
            <Icon name="mail" className="ic-sm" />
            <input
              type="email"
              id="forgot-email"
              className="input"
              placeholder={t('auth.email_placeholder')}
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
            />
          </div>
        </div>

        <button type="submit" className="btn btn-primary btn-lg lp-form-submit" disabled={isLoading}>
          {isLoading ? (
            <>
              <span className="spin" /> {t('auth.forgot_sending')}
            </>
          ) : (
            <>
              {t('auth.forgot_send')}
              <Icon name="arrow-right" />
            </>
          )}
        </button>

        {backLink}
      </form>
    );
  }

  return (
    <form className="lp-form" onSubmit={handleConfirm}>
      <div className="lp-auth-head">
        <h2 className="lp-auth-title">{t('auth.forgot_title')}</h2>
        <p className="lp-auth-sub">
          {t('auth.verify_subtitle_before')}
          <b>{email}</b>
          {t('auth.verify_subtitle_after')}
        </p>
      </div>

      <InlineNotification notification={notification} />

      <div className="field">
        <label className="label" htmlFor="forgot-code">
          {t('auth.verify_code_label')}
        </label>
        <input
          type="text"
          id="forgot-code"
          ref={codeInputRef}
          className="input lp-code-input"
          placeholder={t('auth.verify_code_placeholder')}
          maxLength="6"
          pattern="[0-9]{6}"
          inputMode="numeric"
          autoComplete="one-time-code"
          required
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
        />
      </div>

      <div className="field">
        <label className="label" htmlFor="forgot-new-password">
          {t('auth.forgot_new_password')} <span className="muted">{t('auth.password_hint')}</span>
        </label>
        <PasswordField
          id="forgot-new-password"
          placeholder={t('auth.password_new_placeholder')}
          autoComplete="new-password"
          minLength={8}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
        />
      </div>

      <button type="submit" className="btn btn-primary btn-lg lp-form-submit" disabled={isLoading}>
        {isLoading ? (
          <>
            <span className="spin" /> {t('auth.forgot_resetting')}
          </>
        ) : (
          <>
            {t('auth.forgot_reset')}
            <Icon name="arrow-right" />
          </>
        )}
      </button>

      <p className="lp-form-hint lp-form-hint-tight">
        {secondsLeft > 0 ? (
          <span className="muted">
            {t('auth.verify_resend_in_before')}
            <b>{secondsLeft}</b>
            {t('auth.verify_resend_in_after')}
          </span>
        ) : (
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              if (!isLoading) handleSendCode();
            }}
          >
            {t('auth.verify_resend')}
          </a>
        )}
      </p>

      {backLink}
    </form>
  );
}

export default ForgotPasswordForm;
