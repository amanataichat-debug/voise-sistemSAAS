import React, { useState, useRef } from 'react';
import { useEmailVerification } from '../../hooks/useEmailVerification';
import InlineNotification from '../InlineNotification';
import { useT } from '../../i18n';

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
    resendCode
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

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
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

  const attemptsClass = attempts === 2 ? 'warning' : attempts === 1 ? 'danger' : '';

  return (
    <div className="verification-section">
      <div className={`verification-notice${message ? ' warning' : ''}`}>
        <i className={message ? 'fas fa-info-circle' : 'fas fa-envelope'}></i>
        {message || (
          <>{t('verif.sentTo')} <strong>{email}</strong></>
        )}
      </div>

      <InlineNotification notification={notification} />

      <div className="code-input-container">
        <label htmlFor="verification-code">{t('verif.enterCode')}</label>
        <input
          type="text"
          id="verification-code"
          ref={codeInputRef}
          className="fi verification-code-input"
          placeholder="000000"
          maxLength="6"
          pattern="[0-9]{6}"
          inputMode="numeric"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
          onKeyPress={handleKeyPress}
          disabled={codeDisabled}
          autoFocus
        />
      </div>

      <div className="verification-info">
        <span className={`attempts-left ${attemptsClass}`}>
          {t('verif.attempts')} {attempts}
        </span>
        {isTimerActive && (
          <span className="resend-timer">
            {t('verif.resendIn')} <strong>{secondsLeft}</strong>{t('verif.seconds')}
          </span>
        )}
      </div>

      <button
        type="button"
        className="btn-submit"
        onClick={handleVerify}
        disabled={isVerifying || codeDisabled}
      >
        {isVerifying ? t('verif.verifying') : t('verif.confirm')}
      </button>

      {!isTimerActive && (
        <button
          type="button"
          className="btn-resend"
          onClick={handleResend}
          disabled={isResending}
        >
          {isResending ? (
            <><div className="spinner"></div> {t('verif.sending')}</>
          ) : (
            <><i className="fas fa-redo"></i> {t('verif.resend')}</>
          )}
        </button>
      )}
    </div>
  );
}

export default EmailVerificationSection;
