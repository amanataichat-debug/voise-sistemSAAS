import React, { useState, useRef } from 'react';
import { useEmailVerification } from '../../hooks/useEmailVerification';
import InlineNotification from '../InlineNotification';
import Icon from '../Icon';
import { useT } from '../../i18n';

// Хук useEmailVerification отдаёт сообщения по-русски (логику хука не трогаем) —
// здесь переводим их на язык страницы по известным шаблонам.
function localize(notification, t) {
  if (!notification) return notification;
  const msg = notification.message || '';
  const map = [
    [/^Введите 6-значный код$/, () => t('auth.verify_error_format')],
    [/^Email подтвержден/, () => t('auth.verify_success')],
    [/^Исчерпаны попытки/, () => t('auth.verify_error_exhausted')],
    [/^Неверный код\. Осталось попыток: (\d+)/, (m) => t('auth.verify_error_wrong', { n: m[1] })],
    [/^Новый код отправлен/, () => t('auth.verify_resent')],
    [/^Подождите перед повторной/, () => t('auth.verify_wait')],
    [/^Ошибка отправки кода/, () => t('auth.verify_resend_error')],
  ];
  for (const [re, fn] of map) {
    const m = msg.match(re);
    if (m) return { ...notification, message: fn(m) };
  }
  return notification;
}

function EmailVerificationSection({ email, message, onVerified }) {
  const [code, setCode] = useState('');
  const codeInputRef = useRef(null);
  const { t } = useT();

  const {
    attempts,
    secondsLeft,
    isTimerActive,
    notification,
    isVerifying,
    isResending,
    codeDisabled,
    verifyCode,
    resendCode,
  } = useEmailVerification(email, onVerified);

  const handleVerify = () => {
    verifyCode(code);
    if (attempts > 1) {
      setCode('');
      if (codeInputRef.current) {
        codeInputRef.current.focus();
      }
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleVerify();
    }
  };

  const handleResend = () => {
    resendCode();
    setCode('');
    if (codeInputRef.current) {
      codeInputRef.current.focus();
    }
  };

  const attemptsChip = attempts === 2 ? ' chip-warning' : attempts === 1 ? ' chip-danger' : '';

  return (
    <form
      className="lp-form lp-verify"
      onSubmit={(e) => {
        e.preventDefault();
        handleVerify();
      }}
    >
      <div className="lp-auth-head">
        <div className={`lp-verify-icon${message ? ' warning' : ''}`}>
          <Icon name={message ? 'info' : 'mail'} />
        </div>
        <h2 className="lp-auth-title">{t('auth.verify_title')}</h2>
        <p className="lp-auth-sub">
          {message || (
            <>
              {t('auth.verify_subtitle_before')}
              <b>{email}</b>
              {t('auth.verify_subtitle_after')}
            </>
          )}
        </p>
      </div>

      <InlineNotification notification={localize(notification, t)} />

      <div className="field">
        <label className="label" htmlFor="verification-code">
          {t('auth.verify_code_label')}
        </label>
        <input
          type="text"
          id="verification-code"
          ref={codeInputRef}
          className="input lp-code-input"
          placeholder={t('auth.verify_code_placeholder')}
          maxLength="6"
          pattern="[0-9]{6}"
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
          onKeyDown={handleKeyDown}
          disabled={codeDisabled}
          autoFocus
        />
      </div>

      <div className="lp-verify-info">
        <span className={`chip${attemptsChip}`}>{t('auth.verify_attempts', { n: attempts })}</span>
        {isTimerActive && (
          <span className="muted">
            {t('auth.verify_resend_in_before')}
            <b>{secondsLeft}</b>
            {t('auth.verify_resend_in_after')}
          </span>
        )}
      </div>

      <button type="submit" className="btn btn-primary btn-lg lp-form-submit" disabled={isVerifying || codeDisabled}>
        {isVerifying ? (
          <>
            <span className="spin" /> {t('auth.verify_submitting')}
          </>
        ) : (
          <>
            {t('auth.verify_submit')}
            <Icon name="arrow-right" />
          </>
        )}
      </button>

      {!isTimerActive && (
        <button type="button" className="btn btn-lg lp-form-submit" onClick={handleResend} disabled={isResending}>
          {isResending ? (
            <>
              <span className="spin" /> {t('auth.verify_resending')}
            </>
          ) : (
            <>
              <Icon name="refresh-cw" />
              {t('auth.verify_resend')}
            </>
          )}
        </button>
      )}
    </form>
  );
}

export default EmailVerificationSection;
