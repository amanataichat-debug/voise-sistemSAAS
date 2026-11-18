import React, { useState } from 'react';
import api from '../../utils/api';
import { useReferralTracker } from '../../hooks/useReferralTracker';
import { rememberFirstName } from '../../utils/rememberedName';
import InlineNotification from '../InlineNotification';
import Icon from '../Icon';
import PasswordField from './PasswordField';
import EmailVerificationSection from './EmailVerificationSection';
import { useT } from '../../i18n';

// Телефон: цифры без кода страны (9 — Кыргызстан) получают +996; «+…» — любая страна.
// Сервер приводит номер к E.164 сам (backend/utils/phone.py), здесь — только быстрая проверка.
const phoneDigits = (v) => String(v || '').replace(/\D/g, '');
function phoneLooksValid(v) {
  const raw = String(v || '').trim();
  const d = phoneDigits(raw);
  if (raw.startsWith('+')) return d.length >= 9 && d.length <= 15;
  return d.length >= 9 && d.length <= 13;
}

// Регистрация: имя, телефон, почта, пароль (18.11.2026 — телефон и имя обязательны, чтобы
// владелец платформы мог связаться с новым клиентом). Реферальный код и UTM — из
// useReferralTracker; дальше экран подтверждения почты и переход в кабинет, где клиента
// встречает выбор сферы бизнеса.
function RegisterForm({ onSwitchToLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [firstName, setFirstName] = useState('');
  const [companyName, setCompanyName] = useState('');
  const [phone, setPhone] = useState('');
  const [notification, setNotification] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showVerification, setShowVerification] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState(null);
  const { t } = useT();

  const { getReferralData, clearReferralData } = useReferralTracker();

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!firstName.trim()) {
      setNotification({ type: 'error', message: t('auth.name_required') });
      return;
    }
    if (!phone.trim() || phone.trim() === '+996') {
      setNotification({ type: 'error', message: t('auth.phone_required') });
      return;
    }
    if (!phoneLooksValid(phone)) {
      setNotification({ type: 'error', message: t('auth.phone_invalid') });
      return;
    }

    setNotification({ type: 'loading', message: t('auth.register_status_loading') });
    setIsLoading(true);

    try {
      const referralData = getReferralData();

      const userData = {
        email: email.trim(),
        password: password,
        first_name: firstName.trim() || null,
        last_name: null,
        company_name: companyName.trim() || null,
        phone: phone.trim(),
        referral_code: referralData?.referral_code || null,
        utm_data: referralData?.utm_data || null,
      };

      const data = await api.register(userData);
      rememberFirstName(firstName);

      if (data.message && data.message.includes('exists but not verified')) {
        setNotification({ type: 'success', message: t('auth.register_status_success') });
        setVerificationMessage(t('auth.register_exists_not_verified'));
        setShowVerification(true);
        return;
      }

      if (data.verification_required && data.verification_sent) {
        setNotification({ type: 'success', message: t('auth.register_status_success') });
        setShowVerification(true);
        clearReferralData();
      } else if (data.token) {
        localStorage.setItem('auth_token', data.token);
        window.location.href = '/static/dashboard.html';
      } else {
        // Аккаунт создан, но письмо с кодом не отправилось (verification_sent: false)
        setIsLoading(false);
        setNotification({ type: 'error', message: t('auth.register_email_failed') });
      }
    } catch (error) {
      setIsLoading(false);

      if (error.message.includes('already registered')) {
        setNotification({ type: 'error', message: t('auth.register_error_already') });
        setTimeout(() => onSwitchToLogin(), 2000);
      } else {
        // Переводим типовые ошибки валидации пароля
        let message = error.message || t('auth.register_error_default');
        if (message.includes('at least one digit')) {
          message = t('auth.pwd_digit');
        } else if (message.includes('at least one letter')) {
          message = t('auth.pwd_letter');
        } else if (message.includes('at least 8 characters')) {
          message = t('auth.pwd_length');
        } else if (message.includes('Invalid phone')) {
          message = t('auth.phone_invalid');
        } else if (message.includes('Phone is required')) {
          message = t('auth.phone_required');
        } else if (message.includes('Name is required')) {
          message = t('auth.name_required');
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
    <form className="lp-form" onSubmit={handleSubmit}>
      <div className="lp-auth-head">
        <h2 className="lp-auth-title">{t('auth.register_form_title')}</h2>
        <p className="lp-auth-sub">{t('auth.register_form_subtitle')}</p>
      </div>

      <InlineNotification notification={notification} />

      <div className="lp-auth-fields">
        <div className="lp-auth-row">
          <div className="field">
            <label className="label" htmlFor="register-name">
              {t('auth.name_label')}
            </label>
            <input
              type="text"
              id="register-name"
              className="input"
              placeholder={t('auth.name_placeholder')}
              autoComplete="given-name"
              required
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="label" htmlFor="register-company">
              {t('auth.company_label')} <span className="muted">{t('auth.company_optional')}</span>
            </label>
            <input
              type="text"
              id="register-company"
              className="input"
              placeholder={t('auth.company_placeholder')}
              autoComplete="organization"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <label className="label" htmlFor="register-phone">
            {t('auth.phone_label')} <span className="muted">{t('auth.phone_hint')}</span>
          </label>
          <div className="input-wrap lp-auth-input">
            <Icon name="phone" className="ic-sm" />
            <input
              type="tel"
              id="register-phone"
              className="input"
              inputMode="tel"
              placeholder={t('auth.phone_placeholder')}
              autoComplete="tel"
              required
              value={phone}
              onFocus={() => { if (!phone) setPhone('+996 '); }}
              onBlur={() => { if (phone.trim() === '+996') setPhone(''); }}
              onChange={(e) => setPhone(e.target.value.replace(/[^\d+\s()-]/g, '').slice(0, 24))}
            />
          </div>
        </div>

        <div className="field">
          <label className="label" htmlFor="register-email">
            {t('auth.email_label')}
          </label>
          <div className="input-wrap lp-auth-input">
            <Icon name="mail" className="ic-sm" />
            <input
              type="email"
              id="register-email"
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
          <label className="label" htmlFor="register-password">
            {t('auth.password_label')} <span className="muted">{t('auth.password_hint')}</span>
          </label>
          <PasswordField
            id="register-password"
            placeholder={t('auth.password_new_placeholder')}
            autoComplete="new-password"
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
      </div>

      <button type="submit" className="btn btn-primary btn-lg lp-form-submit" disabled={isLoading}>
        {isLoading ? (
          <>
            <span className="spin" /> {t('auth.register_submitting')}
          </>
        ) : (
          <>
            {t('auth.register_submit')}
            <Icon name="arrow-right" />
          </>
        )}
      </button>

      <p className="lp-form-legal">
        {t('auth.register_legal_before')}
        <a href="/static/terms-of-service.html" target="_blank" rel="noopener">
          {t('auth.register_legal_terms')}
        </a>
        {t('auth.register_legal_middle')}
        <a href="/static/privacy-policy.html" target="_blank" rel="noopener">
          {t('auth.register_legal_privacy')}
        </a>
        {t('auth.register_legal_after')}
      </p>

      <p className="lp-form-hint">
        {t('auth.register_hint')}{' '}
        <a
          href="#"
          onClick={(e) => {
            e.preventDefault();
            onSwitchToLogin();
          }}
        >
          {t('auth.register_hint_link')}
        </a>
      </p>
    </form>
  );
}

export default RegisterForm;
