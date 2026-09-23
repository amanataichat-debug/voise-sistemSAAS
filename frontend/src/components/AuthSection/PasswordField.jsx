import React, { useState } from 'react';
import Icon from '../Icon';
import { useT } from '../../i18n';

function PasswordField({ id, value, onChange, placeholder, autoComplete, minLength }) {
  const { t } = useT();
  const [visible, setVisible] = useState(false);
  return (
    <div className="input-wrap lp-auth-input">
      <Icon name="lock" className="ic-sm" />
      <input
        id={id}
        type={visible ? 'text' : 'password'}
        className="input lp-auth-input-action"
        placeholder={placeholder}
        autoComplete={autoComplete}
        minLength={minLength}
        required
        value={value}
        onChange={onChange}
      />
      <button
        type="button"
        className="lp-auth-eye"
        tabIndex={-1}
        aria-label={visible ? t('auth.password_hide') : t('auth.password_show')}
        onClick={() => setVisible((v) => !v)}
      >
        <Icon name={visible ? 'eye-off' : 'eye'} className="ic-sm" />
      </button>
    </div>
  );
}

export default PasswordField;
