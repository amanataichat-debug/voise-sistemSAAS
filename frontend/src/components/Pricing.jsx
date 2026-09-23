import React from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import ModelLogo, { MODELS, MODEL_ORDER, PLATFORM_KEY_MODELS } from './ModelLogo';
import SectionHead from './SectionHead';
import { Reveal } from './Reveal';

// Сравнительная таблица тарифов. Значения — с текущего лендинга (карточки тарифов):
// true — есть, false — нет, null — не указано (ячейка пустая), строка — ключ pricing.values.*
const PLANS = [
  { key: 'trial', chip: 'chip-success', primary: true },
  { key: 'voice', chip: 'chip-ghost' },
  { key: 'start', chip: 'chip-accent', primary: true, col: 'hot' },
  { key: 'profi', chip: 'chip-ghost' },
  { key: 'agent', chip: 'chip-violet', col: 'agent' },
];

const ROWS = [
  ['assistants', ['a1', 'a3', 'a5', 'a10', null]],
  ['agents', [false, false, false, false, 'ag3']],
  ['widget', [true, true, true, true, null]],
  ['telephony', [true, false, true, true, true]],
  ['crm', [true, false, true, true, true]],
  ['knowledge', [true, true, true, true, true]],
  ['jarvis', [true, true, true, true, null]],
  ['selfcall', [false, false, false, false, true]],
  ['credits', [false, false, false, false, 'credits']],
  ['support', [null, null, 'priority', 'vip', null]],
];

function Cell({ value }) {
  const { t } = useT();
  if (value === true) {
    return (
      <span className="cell-yes">
        <Icon name="check" className="ic-sm" />
      </span>
    );
  }
  if (value === false) return <span className="cell-no">—</span>;
  if (value === null) return null;
  return t(`pricing.values.${value}`);
}

function Pricing({ onOpenModal }) {
  const { t } = useT();
  return (
    <section className="sec sec-tint tint-blue" id="pricing">
      <div className="lp-container">
        <SectionHead index="06" title={t('pricing.title')} lead={t('pricing.lead')} />
        <Reveal className="plans-card" y={20}>
          <div className="plans-wrap table-wrap">
            <table className="plans">
              <thead>
                <tr>
                  <th className="plans-feature" />
                  {PLANS.map((p) => {
                    const plan = t(`pricing.plans.${p.key}`);
                    return (
                      <th key={p.key} className={p.col}>
                        <div className="plan-head">
                          <span className={`chip ${p.chip}`}>{plan.badge || '·'}</span>
                          <b>{plan.name}</b>
                          <span className="plan-desc">{plan.desc}</span>
                          <span className="plans-price">
                            {plan.price}
                            <small>{plan.period}</small>
                          </span>
                          <button
                            type="button"
                            className={`btn btn-sm${p.primary ? ' btn-primary' : ''}`}
                            onClick={() => onOpenModal('register')}
                          >
                            {plan.cta}
                          </button>
                        </div>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {ROWS.map(([feature, values]) => (
                  <tr key={feature}>
                    <td className="plans-feature">{t(`pricing.features.${feature}`)}</td>
                    {values.map((v, i) => (
                      <td key={PLANS[i].key} className={PLANS[i].col}>
                        <Cell value={v} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Reveal>

        <div className="extra">
          <Reveal className="extra-col" y={16}>
            <h3>{t('pricing.extra_models_title')}</h3>
            <p>{t('pricing.extra_models_text')}</p>
            <ul className="rate-list">
              {MODEL_ORDER.map((code) => (
                <li key={code}>
                  <ModelLogo code={code} size={16} wrap={false} />
                  <span>{MODELS[code].name}</span>
                  <b>{PLATFORM_KEY_MODELS.includes(code) ? t('pricing.key_platform') : t('pricing.key_own')}</b>
                </li>
              ))}
            </ul>
          </Reveal>
          <Reveal className="extra-col" y={16} delay={0.08}>
            <h3>{t('pricing.extra_comm_title')}</h3>
            <p>{t('pricing.extra_comm_text')}</p>
            <ul className="rate-list">
              {t('pricing.extra_comm').map(([icon, label, value]) => (
                <li key={label}>
                  <Icon name={icon} className="ic-sm" />
                  <span>{label}</span>
                  <b>{value}</b>
                </li>
              ))}
            </ul>
          </Reveal>
          <Reveal className="extra-col" y={16} delay={0.16}>
            <h3>{t('pricing.extra_credits_title')}</h3>
            <p>{t('pricing.extra_credits_text')}</p>
            <ul className="rate-list">
              {t('pricing.extra_credits').map(([icon, label, value]) => (
                <li key={label}>
                  <Icon name={icon} className="ic-sm" />
                  <span>{label}</span>
                  <b>{value}</b>
                </li>
              ))}
            </ul>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

export default Pricing;
