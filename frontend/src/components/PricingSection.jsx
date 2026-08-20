import React from 'react';
import { useT } from '../i18n';

function PricingSection({ onOpenModal }) {
  const { t } = useT();

  return (
    <section className="section pricing-section vs-grid-bg vs-grid-fade" id="pricing">
      <div className="section-inner">
        <div className="s-head rev">
          <span className="s-label">{t('price.label')}</span>
          <h2 className="s-title">{t('price.title1')}<span className="gt">{t('price.title2')}</span></h2>
          <p className="s-desc">{t('price.desc')}</p>
        </div>

        <div className="price-grid">
          {/* FREE */}
          <div className="price-card rev">
            <span className="pbadge pb-free">{t('price.free.badge')}</span>
            <h4>{t('price.free.name')}</h4>
            <p className="price-desc">{t('price.free.desc')}</p>
            <div className="price-amount"><span className="pamt">0 ₽</span> <small>{t('price.free.period')}</small></div>
            <ul className="pf-list">
              <li className="pf-item"><i className="fas fa-check"></i> <span className="hl">{t('price.f.a1')}</span></li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.widget')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.crm')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.tel')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.kb')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.jarvis')}</li>
            </ul>
            <button className="price-btn pb-ghost" onClick={() => onOpenModal('register')}>{t('price.btn.free')}</button>
          </div>

          {/* AI VOICE */}
          <div className="price-card rev d1">
            <span className="pbadge pb-basic">{t('price.voice.badge')}</span>
            <h4>{t('price.voice.name')}</h4>
            <p className="price-desc">{t('price.voice.desc')}</p>
            <div className="price-amount"><span className="pamt">1 490 ₽</span> <small>{t('price.month')}</small></div>
            <ul className="pf-list">
              <li className="pf-item"><i className="fas fa-check"></i> <span className="hl">{t('price.f.a3')}</span></li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.widget')}</li>
              <li className="pf-item disabled"><i className="fas fa-times"></i> {t('price.f.crm')}</li>
              <li className="pf-item disabled"><i className="fas fa-times"></i> {t('price.f.tel')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.kb')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.jarvis')}</li>
            </ul>
            <button className="price-btn pb-ghost" onClick={() => onOpenModal('register')}>{t('price.btn.choose')}</button>
          </div>

          {/* START (RECOMMENDED) */}
          <div className="price-card hot rev d2">
            <span className="pbadge pb-pop">{t('price.start.badge')}</span>
            <h4>{t('price.start.name')}</h4>
            <p className="price-desc">{t('price.start.desc')}</p>
            <div className="price-amount"><span className="pamt">2 990 ₽</span> <small>{t('price.month')}</small></div>
            <ul className="pf-list">
              <li className="pf-item"><i className="fas fa-check"></i> <span className="hl">{t('price.f.a5')}</span></li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.widget')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.crm')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.tel')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.kb')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.prio')}</li>
            </ul>
            <button className="price-btn pb-solid" onClick={() => onOpenModal('register')}>{t('price.btn.start')}</button>
          </div>

          {/* PROFI */}
          <div className="price-card rev d3">
            <span className="pbadge pb-prem">{t('price.profi.badge')}</span>
            <h4>{t('price.profi.name')}</h4>
            <p className="price-desc">{t('price.profi.desc')}</p>
            <div className="price-amount"><span className="pamt">5 990 ₽</span> <small>{t('price.month')}</small></div>
            <ul className="pf-list">
              <li className="pf-item"><i className="fas fa-check"></i> <span className="hl">{t('price.f.a10')}</span></li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.widget')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.crm')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.tel')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.kb')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.vip')}</li>
            </ul>
            <button className="price-btn pb-ghost" onClick={() => onOpenModal('register')}>{t('price.btn.choose')}</button>
          </div>

          {/* AGENT */}
          <div className="price-card rev d4">
            <span className="pbadge pb-agent">{t('price.agent.badge')}</span>
            <h4>{t('price.agent.name')}</h4>
            <p className="price-desc">{t('price.agent.desc')}</p>
            <div className="price-amount"><span className="pamt">5 490 ₽</span> <small>{t('price.month')}</small></div>
            <ul className="pf-list">
              <li className="pf-item"><i className="fas fa-check"></i> <span className="hl">{t('price.f.ag3')}</span></li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.selfcall')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.credits')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.funnel')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.tel')}</li>
              <li className="pf-item"><i className="fas fa-check"></i> {t('price.f.kb')}</li>
            </ul>
            <a href="#agent" className="agent-more-link">{t('price.agentLink')}</a>
            <button className="price-btn pb-ghost" onClick={() => onOpenModal('register')}>{t('price.btn.choose')}</button>
          </div>
        </div>
      </div>
    </section>
  );
}

export default PricingSection;
