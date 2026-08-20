import React from 'react';
import { useT } from '../i18n';

const CheckIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <circle cx="8" cy="8" r="8" fill="var(--vs-accent-soft)"/>
    <path d="M5 8l2 2 4-4"
      stroke="var(--vs-accent)" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

function ShowcaseSection() {
  const { t } = useT();

  return (
    <section className="section showcase-section" id="showcase">
      <div className="section-inner">
        <div className="s-head rev">
          <span className="s-label">{t('show.label')}</span>
          <h2 className="s-title">{t('show.title1')}<br /><span className="gt">{t('show.title2')}</span></h2>
          <p className="s-desc">{t('show.desc')}</p>
        </div>

        <div className="showcase-grid">
          {/* 1. TELEPHONY */}
          <div className="showcase-item rev" id="telephony">
            <div className="show-text">
              <div className="icon-box ib-blue">
                <svg width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 014.69 15a19.79 19.79 0 01-3.07-8.67A2 2 0 013.6 4.22h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L7.91 11.8a16 16 0 006.29 6.29l1.87-1.87a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 18.92z" />
                </svg>
              </div>
              <h3>{t('show.tel.title')}</h3>
              <p>{t('show.tel.desc')}</p>

              <div className="telephony-prices">
                <div className="tp-header">
                  <svg width="16" height="16" viewBox="0 0 24 24"
                    fill="none" stroke="currentColor" strokeWidth="2.5"
                    strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
                  </svg>
                  {t('show.tel.badge')}
                </div>
                <div className="tp-rates">
                  <div className="tp-rate">
                    <span className="tp-arrow tp-in">↓</span>
                    <div>
                      <div className="tp-dir">{t('show.tel.in')}</div>
                      <div className="tp-price">1.7 <span>{t('show.tel.perMin')}</span></div>
                    </div>
                  </div>
                  <div className="tp-divider"/>
                  <div className="tp-rate">
                    <span className="tp-arrow tp-out">↑</span>
                    <div>
                      <div className="tp-dir">{t('show.tel.out')}</div>
                      <div className="tp-price">2.7 <span>{t('show.tel.perMin')}</span></div>
                    </div>
                  </div>
                </div>
              </div>

              <ul className="show-feats">
                <li><CheckIcon /> <span>{t('show.tel.f1')}</span></li>
                <li><CheckIcon /> <span>{t('show.tel.f2')}</span></li>
                <li><CheckIcon /> <span>{t('show.tel.f3')}</span></li>
                <li><CheckIcon /> <span>{t('show.tel.f4')}</span></li>
                <li><CheckIcon /> <span>{t('show.tel.f5')}</span></li>
                <li><CheckIcon /> <span>{t('show.tel.f6')}</span></li>
              </ul>
            </div>
            <div className="mockup-wrap rev">
              <div className="mockup-frame">
                <div className="mock-topbar">
                  <div className="d r"></div><div className="d y"></div><div className="d g"></div>
                  <div className="url"><span>/dashboard/telephony</span></div>
                </div>
                <div className="mock-img-wrap">
                  <img src="https://pub-b1e3de631e544c69b0ad6587f740e140.r2.dev/image_2026-03-02_07-35-43.png" alt={t('show.tel.title')} loading="lazy" />
                </div>
              </div>
            </div>
          </div>

          {/* 2. DIALOGS */}
          <div className="showcase-item">
            <div className="show-text">
              <div className="icon-box ib-green">
                <svg width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
                </svg>
              </div>
              <h3>{t('show.dlg.title')}</h3>
              <p>{t('show.dlg.desc')}</p>
              <ul className="show-feats">
                <li><CheckIcon /> <span>{t('show.dlg.f1')}</span></li>
                <li><CheckIcon /> <span>{t('show.dlg.f2')}</span></li>
                <li><CheckIcon /> <span>{t('show.dlg.f3')}</span></li>
                <li><CheckIcon /> <span>{t('show.dlg.f4')}</span></li>
                <li><CheckIcon /> <span>{t('show.dlg.f5')}</span></li>
              </ul>
            </div>
            <div className="mockup-wrap">
              <div className="mockup-frame">
                <div className="mock-topbar">
                  <div className="d r"></div><div className="d y"></div><div className="d g"></div>
                  <div className="url"><span>/dashboard/dialogs</span></div>
                </div>
                <div className="mock-img-wrap">
                  <img src="https://pub-b1e3de631e544c69b0ad6587f740e140.r2.dev/image_2026-03-02_07-36-33.png" alt={t('show.dlg.title')} loading="lazy" />
                </div>
              </div>
            </div>
          </div>

          {/* 3. CRM */}
          <div className="showcase-item rev">
            <div className="show-text">
              <div className="icon-box ib-amber">
                <svg width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                  <path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75" />
                </svg>
              </div>
              <h3>{t('show.crm.title')}</h3>
              <p>{t('show.crm.desc')}</p>
              <ul className="show-feats">
                <li><CheckIcon /> <span>{t('show.crm.f1')}</span></li>
                <li><CheckIcon /> <span>{t('show.crm.f2')}</span></li>
                <li><CheckIcon /> <span>{t('show.crm.f3')}</span></li>
                <li><CheckIcon /> <span>{t('show.crm.f4')}</span></li>
                <li><CheckIcon /> <span>{t('show.crm.f5')}</span></li>
              </ul>
            </div>
            <div className="mockup-wrap rev">
              <div className="mockup-frame">
                <div className="mock-topbar">
                  <div className="d r"></div><div className="d y"></div><div className="d g"></div>
                  <div className="url"><span>/dashboard/crm</span></div>
                </div>
                <div className="mock-img-wrap">
                  <img src="https://pub-b1e3de631e544c69b0ad6587f740e140.r2.dev/image_2026-03-02_07-37-12.png" alt={t('show.crm.title')} loading="lazy" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default ShowcaseSection;
