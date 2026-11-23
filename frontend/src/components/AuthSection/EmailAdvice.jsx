import React from 'react';
import { checkEmail, normalizeEmail } from '../../utils/emailCheck';

// Подсказка под полем почты: «Возможно, вы имели в виду …@gmail.com?» с кнопкой «Исправить»,
// ошибка формата словами или короткое правило «латиницей, например name@gmail.com».
function EmailAdvice({ email, touched, dismissed, onFix, onDismiss, t }) {
  const res = checkEmail(email);
  if (res.suggestion && dismissed !== normalizeEmail(email)) {
    return (
      <div className="lp-field-suggest" role="alert">
        <span>{t('auth.email_suggest')} <b>{res.suggestion}</b>?</span>
        <span className="lp-field-suggest-actions">
          <button type="button" className="btn btn-primary btn-sm" onClick={() => onFix(res.suggestion)}>
            {t('auth.email_suggest_fix')}
          </button>
          {onDismiss && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={onDismiss}>
              {t('auth.email_suggest_keep')}
            </button>
          )}
        </span>
      </div>
    );
  }
  if (touched && res.problem && res.problem !== 'empty') {
    return <p className="lp-field-error">{t('auth.email_problem_' + res.problem)}</p>;
  }
  return <p className="lp-field-note">{t('auth.email_hint')}</p>;
}

export default EmailAdvice;
