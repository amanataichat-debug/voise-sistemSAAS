import React from 'react';
import { useT } from '../i18n';

function UseCasesSection() {
  const { t } = useT();

  return (
    <section className="use-cases-section">
      <div className="use-cases-container">
        <div className="use-cases-header">
          <h2 className="use-cases-title">{t('uc.title')}</h2>
          <p className="use-cases-subtitle">{t('uc.subtitle')}</p>
        </div>

        <div className="use-cases-grid">
          <div className="use-case-card">
            <div className="use-case-icon">
              <i className="fas fa-globe"></i>
            </div>
            <h3 className="use-case-title">{t('uc.site.title')}</h3>
            <p className="use-case-description">{t('uc.site.desc')}</p>
            <ul className="use-case-features">
              <li><i className="fas fa-check"></i> {t('uc.site.f1')}</li>
              <li><i className="fas fa-check"></i> {t('uc.site.f2')}</li>
              <li><i className="fas fa-check"></i> {t('uc.site.f3')}</li>
              <li><i className="fas fa-check"></i> {t('uc.site.f4')}</li>
            </ul>
          </div>

          <div className="use-case-card">
            <div className="use-case-icon">
              <i className="fas fa-phone"></i>
            </div>
            <h3 className="use-case-title">{t('uc.tel.title')}</h3>
            <p className="use-case-description">{t('uc.tel.desc')}</p>
            <ul className="use-case-features">
              <li><i className="fas fa-check"></i> {t('uc.tel.f1')}</li>
              <li><i className="fas fa-check"></i> {t('uc.tel.f2')}</li>
              <li><i className="fas fa-check"></i> {t('uc.tel.f3')}</li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

export default UseCasesSection;
